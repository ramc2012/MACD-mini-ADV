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
export function completedRoundTrips(trades: Trade[]): RoundTrip[] {
  type Cycle = { quantity: number; bought: number; buyValue: number; sold: number; sellValue: number; fees: number; openedAt: number; lotSize: number; entries: number; exits: number };
  const inventory = new Map<string, Cycle>();
  const result: RoundTrip[] = [];
  const seen = new Set<string>();
  for (const trade of [...trades].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))) {
    if (seen.has(trade.trade_id)) continue;
    seen.add(trade.trade_id);
    if (trade.side === "BUY") {
      const cycle = inventory.get(trade.symbol) || { quantity: 0, bought: 0, buyValue: 0, sold: 0, sellValue: 0, fees: 0, openedAt: Date.parse(trade.timestamp), lotSize: trade.lot_size || 0, entries: 0, exits: 0 };
      cycle.quantity += trade.quantity;
      cycle.bought += trade.quantity;
      cycle.buyValue += trade.quantity * trade.price;
      cycle.fees += trade.fees || 0;
      cycle.entries += 1;
      inventory.set(trade.symbol, cycle);
      continue;
    }
    const cycle = inventory.get(trade.symbol);
    if (trade.side !== "SELL" || !cycle || cycle.quantity <= 0) continue;
    const closed = Math.min(cycle.quantity, trade.quantity);
    cycle.quantity -= closed;
    cycle.sold += closed;
    cycle.sellValue += closed * trade.price;
    cycle.fees += (trade.fees || 0) * closed / trade.quantity;
    cycle.exits += 1;
    if (cycle.quantity !== 0) continue;
    const pnl = cycle.sellValue - cycle.buyValue - cycle.fees;
    result.push({
      symbol: trade.symbol, quantity: cycle.bought,
      lots: cycle.lotSize > 0 ? Math.floor(cycle.bought / cycle.lotSize) : 0,
      lotSize: cycle.lotSize, entryPrice: cycle.buyValue / cycle.bought,
      exitPrice: cycle.sellValue / cycle.sold, entryTime: new Date(cycle.openedAt).toISOString(),
      exitTime: trade.timestamp, pnl, returnPct: cycle.buyValue ? 100 * pnl / cycle.buyValue : 0,
      heldMs: Math.max(0, Date.parse(trade.timestamp) - cycle.openedAt),
      entryCount: cycle.entries, exitCount: cycle.exits,
    });
    inventory.delete(trade.symbol);
  }
  return result;
}
