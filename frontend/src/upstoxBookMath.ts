import { indexUnderlying } from "./ledgerMath.ts";

// The Upstox live lane's book arithmetic (owner, 19 Nov 2026: one set of metrics, the exact closed-trade count, charges
// included, who initiated each trade, an index / stock filter). Every figure is after Upstox's actual charges: the
// lane reads each day's F&O charges from Upstox's P&L report and splits them over that day's executions
// (upstox_live/lane.py, "charges"). JSX-free so `node --test` can import it (tests/upstoxBookMath.test.ts).

export type Charges = { total: number; brokerage: number; stt: number; gst: number; stamp_duty: number; exchange: number };
export type BookTrade = {
  symbol: string; quantity: number; bought?: number; average_price: number; exit_price?: number;
  opened_at?: string | null; closed_at?: string; realized?: number; net_realized?: number;
  charges?: Charges; charges_pending?: boolean; initiated_by?: "lane" | "owner"; closed_by?: "lane" | "owner";
};
export type Instrument = "ALL" | "INDEX" | "STOCK";
export type Who = "ALL" | "LANE" | "OWNER";

export const NO_CHARGES: Charges = { total: 0, brokerage: 0, stt: 0, gst: 0, stamp_duty: 0, exchange: 0 };

export const instrumentOf = (symbol: string): Exclude<Instrument, "ALL"> => indexUnderlying(symbol) ? "INDEX" : "STOCK";
export const openedBy = (row: BookTrade): Exclude<Who, "ALL"> => row.initiated_by === "owner" ? "OWNER" : "LANE";

export function filterBook<T extends BookTrade>(rows: readonly T[], instrument: Instrument, who: Who): T[] {
  return rows.filter((row) => (instrument === "ALL" || instrumentOf(row.symbol) === instrument) && (who === "ALL" || openedBy(row) === who));
}

export const grossOf = (row: BookTrade) => row.realized ?? 0;
export const chargesOf = (row: BookTrade) => row.charges?.total ?? 0;
export const netOf = (row: BookTrade) => row.net_realized ?? grossOf(row) - chargesOf(row);
/** Net P&L on the money paid for the position (quantity bought × average price). */
export function netReturnPct(row: BookTrade): number | null {
  const cost = (row.bought || row.quantity) * row.average_price;
  return cost > 0 ? netOf(row) / cost * 100 : null;
}

export type BookStats = {
  count: number; wins: number; losses: number; winRate: number | null;
  gross: number; charges: Charges; net: number; netWin: number; netLoss: number; profitFactor: number | null;
  avgWin: number | null; avgLoss: number | null; sizeRatio: number | null; best: number | null; worst: number | null;
  avgReturnPct: number | null; pending: number;
  openedByLane: number; openedByOwner: number; closedByLane: number; closedByOwner: number;
};

/** Closed-trade figures, all after charges: a trade is a win when its net P&L is above zero. */
export function bookStats(rows: readonly BookTrade[]): BookStats {
  const nets = rows.map(netOf);
  const winners = nets.filter((value) => value > 0);
  const losers = nets.filter((value) => value < 0);
  const netWin = winners.reduce((sum, value) => sum + value, 0);
  const netLoss = -losers.reduce((sum, value) => sum + value, 0);
  const charges = rows.reduce<Charges>((sum, row) => {
    const c = row.charges ?? NO_CHARGES;
    return { total: sum.total + c.total, brokerage: sum.brokerage + c.brokerage, stt: sum.stt + c.stt, gst: sum.gst + c.gst,
      stamp_duty: sum.stamp_duty + c.stamp_duty, exchange: sum.exchange + c.exchange };
  }, { ...NO_CHARGES });
  const returns = rows.map(netReturnPct).filter((value): value is number => value != null);
  const avgWin = winners.length ? netWin / winners.length : null;
  const avgLoss = losers.length ? -netLoss / losers.length : null;
  return {
    count: rows.length, wins: winners.length, losses: losers.length, winRate: rows.length ? winners.length / rows.length * 100 : null,
    gross: rows.reduce((sum, row) => sum + grossOf(row), 0), charges, net: nets.reduce((sum, value) => sum + value, 0),
    netWin, netLoss, profitFactor: netLoss ? netWin / netLoss : null, avgWin, avgLoss,
    sizeRatio: avgWin != null && avgLoss ? avgWin / -avgLoss : null,
    best: nets.length ? Math.max(...nets) : null, worst: nets.length ? Math.min(...nets) : null,
    avgReturnPct: returns.length ? returns.reduce((sum, value) => sum + value, 0) / returns.length : null,
    pending: rows.filter((row) => row.charges_pending).length,
    openedByLane: rows.filter((row) => openedBy(row) === "LANE").length, openedByOwner: rows.filter((row) => openedBy(row) === "OWNER").length,
    closedByLane: rows.filter((row) => row.closed_by !== "owner").length, closedByOwner: rows.filter((row) => row.closed_by === "owner").length,
  };
}

/** Equity after charges: the start, then each closed trade's net P&L in closing order, then the open P&L now. */
export function netCurve(rows: readonly BookTrade[], capital: number, start: string | undefined, now: string | undefined, openPnl = 0) {
  const sorted = [...rows].sort((a, b) => Date.parse(a.closed_at ?? "") - Date.parse(b.closed_at ?? ""));
  let running = capital;
  const first = start ?? sorted[0]?.opened_at ?? sorted[0]?.closed_at;
  return [...(first ? [{ at: first, equity: capital }] : []),
    ...sorted.filter((row) => row.closed_at).map((row) => ({ at: row.closed_at as string, equity: (running += netOf(row)) })),
    ...(now ? [{ at: now, equity: running + openPnl }] : [])];
}
