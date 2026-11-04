import type { ClosedPosition, Trade } from "./types";

// Pure book arithmetic for the Ledger page, kept JSX-free so `node --test`
// can import it directly (see tests/ledgerMath.test.ts).

// IST has no DST, so a fixed offset is exact here; do not reuse this for any other zone.
export const IST_OFFSET_MS = 5.5 * 3_600_000;
const DAY_MS = 86_400_000;
// Mirrors portfolio.CLOSED_VISIBLE_UNTIL on the backend. 08:00, not 06:00:
// the session roll fires on the first tick of the new day and Fyers
// republishes the prior close well before the bell, so a 07:0x roll used to
// prune the previous day's round trips before anyone had read them.
export const BOOK_DAY_ROLL_HOUR = 8;
const BOOK_DAY_ROLL_MS = BOOK_DAY_ROLL_HOUR * 3_600_000;
// The label the desk prints for that boundary, derived so it cannot drift
// from the rule the way the hardcoded "06:00 IST" string did.
export const BOOK_ROLL_LABEL = `${String(BOOK_DAY_ROLL_HOUR).padStart(2, "0")}:00 IST`;

// Epoch day 0 (1970-01-01) was a Thursday; getUTCDay() convention, 0 = Sunday.
const weekday = (istDayIndex: number) => (istDayIndex + 4) % 7;
const isWeekend = (istDayIndex: number) => { const day = weekday(istDayIndex); return day === 0 || day === 6; };
// Exchange holidays as YYYY-MM-DD, from the snapshot's config.market_holidays.
// An IST day index times DAY_MS is that IST date at UTC midnight, so its ISO
// date is the IST calendar date.
const NO_HOLIDAYS: ReadonlySet<string> = new Set();
const isClosedDay = (istDayIndex: number, holidays: ReadonlySet<string>) =>
  isWeekend(istDayIndex) || holidays.has(new Date(istDayIndex * DAY_MS).toISOString().slice(0, 10));

// The closed book rolls at 08:00 IST on weekdays, not midnight: a 15:20 exit
// reviewed at 23:00 the same evening is still "today", the next session's
// 09:15 open starts from a clean slate, and a Friday exit stays on the desk
// through the weekend. Mirrors portfolio.closed_visible_until on the backend
// (which is likewise not holiday-aware).
export function closedBookStart(now = Date.now(), holidays: ReadonlySet<string> = NO_HOLIDAYS): number {
  const ist = now + IST_OFFSET_MS;
  let day = Math.floor(ist / DAY_MS);
  if (day * DAY_MS + BOOK_DAY_ROLL_MS > ist) day -= 1;
  while (isClosedDay(day, holidays)) day -= 1;
  return day * DAY_MS + BOOK_DAY_ROLL_MS - IST_OFFSET_MS;
}

// 08:00 IST on the next weekday after the IST date of the exit — the same
// rule the backend stamps into ClosedPosition.visible_until.
export function closedVisibleUntil(exitTime: number, holidays: ReadonlySet<string> = NO_HOLIDAYS): number {
  let day = Math.floor((exitTime + IST_OFFSET_MS) / DAY_MS) + 1;
  while (isClosedDay(day, holidays)) day += 1;
  return day * DAY_MS + BOOK_DAY_ROLL_MS - IST_OFFSET_MS;
}

// The backend's stamp wins when present; a row without one (an older frame)
// gets the same rule applied client-side.
export function closedRowVisibleUntil(row: Pick<ClosedPosition, "exit_time" | "visible_until">, holidays: ReadonlySet<string> = NO_HOLIDAYS): number {
  const stamped = row.visible_until ? Date.parse(row.visible_until) : Number.NaN;
  return Number.isNaN(stamped) ? closedVisibleUntil(Date.parse(row.exit_time), holidays) : stamped;
}

export function visibleClosedRows<Row extends Pick<ClosedPosition, "exit_time" | "visible_until">>(rows: Row[], now = Date.now(), holidays: ReadonlySet<string> = NO_HOLIDAYS): Row[] {
  return rows.filter((row) => closedRowVisibleUntil(row, holidays) > now);
}

// A staged exit is several slices sharing one position_id; the badge counts
// positions, the table shows slices.
export function distinctPositions(rows: Pick<ClosedPosition, "position_id" | "symbol" | "entry_time">[]): number {
  return new Set(rows.map((row) => row.position_id || `${row.symbol}|${row.entry_time}`)).size;
}

// The backend defaults an unobserved path to 0.0 rather than null (a position
// rebuilt at boot has seen no tick), so a zero price means "not observed",
// never "never moved" — an option premium is never 0.
export function observedExcursion(price: number | null | undefined, returnPct: number | null | undefined): number | null {
  if (price === null || price === undefined || !(price > 0)) return null;
  return returnPct ?? null;
}

export type RoundTrip = {
  symbol: string;
  side: "LONG" | "SHORT";
  quantity: number;
  lots: number;
  lotSize: number;
  entryPrice: number;
  exitPrice: number;
  entryTime: string;
  exitTime: string;
  pnl: number;
  returnPct: number;
  heldMs: number;
  entryCount: number;
  exitCount: number;
};

// A trade ends only when inventory returns to flat. Partial exits accumulate
// in that cycle; their realized P&L is excluded from completed-trade metrics.
// Option books only buy to open, so a SELL with nothing held (a fill whose
// entry fell outside the loaded window) is skipped. A book that sells short
// (the spot lane's futures) passes `shorts`, and such a SELL opens a short.
export function completedRoundTrips(trades: Trade[], { shorts = false }: { shorts?: boolean } = {}): RoundTrip[] {
  type Cycle = { side: 1 | -1; quantity: number; opened: number; openValue: number; closed: number; closeValue: number; fees: number; openedAt: number; lotSize: number; entries: number; exits: number };
  const inventory = new Map<string, Cycle>();
  const result: RoundTrip[] = [];
  const seen = new Set<string>();
  for (const trade of [...trades].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))) {
    if (seen.has(trade.trade_id)) continue;
    seen.add(trade.trade_id);
    const direction = trade.side === "BUY" ? 1 : trade.side === "SELL" ? -1 : 0;
    if (!direction) continue;
    let cycle = inventory.get(trade.symbol);
    if (!cycle) {
      if (direction < 0 && !shorts) continue;
      cycle = { side: direction, quantity: 0, opened: 0, openValue: 0, closed: 0, closeValue: 0, fees: 0, openedAt: Date.parse(trade.timestamp), lotSize: trade.lot_size || 0, entries: 0, exits: 0 };
      inventory.set(trade.symbol, cycle);
    }
    if (direction === cycle.side) {
      cycle.quantity += trade.quantity;
      cycle.opened += trade.quantity;
      cycle.openValue += trade.quantity * trade.price;
      cycle.fees += trade.fees || 0;
      cycle.entries += 1;
      continue;
    }
    const closed = Math.min(cycle.quantity, trade.quantity);
    cycle.quantity -= closed;
    cycle.closed += closed;
    cycle.closeValue += closed * trade.price;
    cycle.fees += (trade.fees || 0) * closed / trade.quantity;
    cycle.exits += 1;
    if (cycle.quantity !== 0) continue;
    const pnl = cycle.side * (cycle.closeValue - cycle.openValue) - cycle.fees;
    result.push({
      symbol: trade.symbol, side: cycle.side > 0 ? "LONG" : "SHORT", quantity: cycle.opened,
      lots: cycle.lotSize > 0 ? Math.floor(cycle.opened / cycle.lotSize) : 0,
      lotSize: cycle.lotSize, entryPrice: cycle.openValue / cycle.opened,
      exitPrice: cycle.closeValue / cycle.closed, entryTime: new Date(cycle.openedAt).toISOString(),
      exitTime: trade.timestamp, pnl, returnPct: cycle.openValue ? 100 * pnl / cycle.openValue : 0,
      heldMs: Math.max(0, Date.parse(trade.timestamp) - cycle.openedAt),
      entryCount: cycle.entries, exitCount: cycle.exits,
    });
    inventory.delete(trade.symbol);
  }
  return result;
}

/** Same rolling book window as Closed today, including weekends and exchange holidays. */
export function bookFillCounts(trades: Trade[], now: number, holidays: ReadonlySet<string> = new Set()) {
  const start = closedBookStart(now, holidays);
  const fills = [...new Map(trades.map((r) => [r.trade_id, r])).values()].filter((r) => {
    const time = Date.parse(r.timestamp);
    return time >= start && time <= now && r.quantity > 0;
  });
  return { start, buys: fills.filter((r) => r.side === "BUY").length, sells: fills.filter((r) => r.side === "SELL").length };
}

/** Index option underlyings, longest first so NIFTYNXT50 is not read as NIFTY. */
export const INDEX_UNDERLYINGS = ["MIDCPNIFTY", "NIFTYNXT50", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "NIFTY"] as const;

/** The index an option or future is written on ("NSE:BANKNIFTY26NOV57000CE" → "BANKNIFTY"); null for stock options,
 * cash equities and the indices' own spot symbols. */
export function indexUnderlying(symbol: string): string | null {
  const [, body = ""] = symbol.toUpperCase().split(":");
  if (!/(CE|PE|FUT)$/.test(body)) return null;
  const root = INDEX_UNDERLYINGS.find((name) => body.startsWith(name) && /\d/.test(body.charAt(name.length)));
  return root ?? null;
}

export function optionSide(symbol: string): "CE" | "PE" | null {
  const upper = symbol.toUpperCase();
  return upper.endsWith("CE") ? "CE" : upper.endsWith("PE") ? "PE" : null;
}

export type RoundStats = {
  trades: number; wins: number; losses: number; winRate: number | null; net: number; grossProfit: number; grossLoss: number;
  profitFactor: number | null; avgWin: number | null; avgLoss: number | null; payoff: number | null; avgReturn: number | null;
  best: number | null; worst: number | null; medianHeldMs: number | null;
};

/** Completed-trade figures for any slice of round trips. payoff = average winner ÷ |average loser| (the asymmetry an
 * option book lives on); profit factor is ∞ when there are no losers. */
export function roundStats(rounds: readonly RoundTrip[]): RoundStats {
  const wins = rounds.filter((row) => row.pnl > 0), losses = rounds.filter((row) => row.pnl < 0);
  const grossProfit = wins.reduce((sum, row) => sum + row.pnl, 0);
  const grossLoss = Math.abs(losses.reduce((sum, row) => sum + row.pnl, 0));
  const mean = (rows: readonly RoundTrip[], pick: (row: RoundTrip) => number) => rows.length ? rows.reduce((sum, row) => sum + pick(row), 0) / rows.length : null;
  const avgWin = mean(wins, (row) => row.pnl), avgLoss = mean(losses, (row) => row.pnl);
  const held = rounds.map((row) => row.heldMs).sort((a, b) => a - b);
  const middle = held.length >> 1;
  return {
    trades: rounds.length, wins: wins.length, losses: losses.length,
    winRate: rounds.length ? wins.length / rounds.length : null,
    net: grossProfit - grossLoss, grossProfit, grossLoss,
    profitFactor: grossLoss ? grossProfit / grossLoss : grossProfit ? Number.POSITIVE_INFINITY : null,
    avgWin, avgLoss, payoff: avgWin !== null && avgLoss ? avgWin / Math.abs(avgLoss) : null,
    avgReturn: mean(rounds, (row) => row.returnPct),
    best: rounds.length ? Math.max(...rounds.map((row) => row.pnl)) : null,
    worst: rounds.length ? Math.min(...rounds.map((row) => row.pnl)) : null,
    medianHeldMs: held.length ? (held.length % 2 ? held[middle] : (held[middle - 1] + held[middle]) / 2) : null,
  };
}
