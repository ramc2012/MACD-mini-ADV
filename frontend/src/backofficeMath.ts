// Back office (owner, 18 Nov 2026): every lane's history, day-wise, monthly and yearly.
// Pure helpers; the page is BackOfficePage.tsx and the engine side is backoffice.py.

export type Stats = { trades: number; wins: number; win_rate: number | null; net: number; profit_factor: number | null; avg_win: number | null; avg_loss: number | null };
export type DayRow = { day: string; pnl: number; realized: number; unrealized_change: number; equity: number; capital_added: number; trades: number; wins: number; gross_win: number; gross_loss: number; exits: number; fees: number };
export type PeriodRow = {
  period: string; pnl: number; realized: number; return_pct: number | null; opening_equity: number; closing_equity: number; capital_added: number;
  trades: number; wins: number; win_rate: number | null; profit_factor: number | null; best_day: string; best_day_pnl: number;
  worst_day: string; worst_day_pnl: number; max_drawdown: number; sessions: number;
};
export type SplitRow = { key: string; net: number; trades: number; win_rate: number | null; profit_factor: number | null };
export type LaneSummary = {
  lane: string; label: string; kind?: "paper" | "real"; error?: string; capital: number; starting_capital: number; capital_added: number;
  since: string | null; note: string | null; realized: number; equity: number; closed: Stats; months: PeriodRow[]; years: PeriodRow[]; sessions: number;
};
export type LaneStatement = LaneSummary & { days: DayRow[]; splits: Record<"underlying" | "kind" | "exit_reason" | "hold", SplitRow[]> };
export type Summary = { as_of: string; lanes: LaneSummary[]; cache_seconds: number };
export type CalendarLane = { lane: string; label: string; kind?: "paper" | "real"; cells: Record<string, number> };
export type Calendar = { period: "day" | "month" | "year"; periods: string[]; lanes: CalendarLane[] };
export type TradeRow = {
  lane: string; position_id?: string | number; symbol: string; side: string; quantity?: number; lots?: number; entry_time?: string; entry_price?: number;
  exit_time?: string; exit_price?: number; pnl: number; fees?: number; exit_reason?: string; partial?: boolean; return_pct?: number | null; day?: string;
};
export type SpreadBlock = { slices: number; legs: number; covered_legs: number; coverage_pct: number | null; net: number; charged: number; extra: number; net_at_quote: number; median_entry_spread_pct: number | null };
export type SpreadLane = SpreadBlock & { lane: string; label: string; kind?: "paper" | "real"; months: (SpreadBlock & { month: string })[] };
export type SpreadReport = { as_of: string; window_seconds: number; source: string; lanes: SpreadLane[] };

/** The haircut as a share of the recorded result's size (how much of the paper P&L the spread would take). */
export function haircutShare(block: Pick<SpreadBlock, "net" | "extra">): number | null {
  return block.net ? (block.extra / Math.abs(block.net)) * 100 : null;
}

export type Page<T> = { total: number; page: number; size: number; rows: T[]; net?: number };

export const VIEWS = [["OVERVIEW", "Overview"], ["DAILY", "Daily"], ["MONTHLY", "Monthly"], ["YEARLY", "Yearly"], ["LANE", "Lane statement"], ["TRADES", "Trade register"], ["ORDERS", "Order register"], ["SPREAD", "Spread check"]] as const;
export type View = typeof VIEWS[number][0];
/** Lanes whose orders are an engine repository (the order register pages them in SQL). */
export const ORDER_LANES = ["macd", "smart_1l", "smart_50l", "spot_macd", "blast", "agentic", "mcx"];

/** Paper lanes first, the real-money lane apart: it is never added into paper totals. */
export function splitKinds<T extends { kind?: string }>(rows: readonly T[]): { paper: T[]; real: T[] } {
  return { paper: rows.filter((row) => row.kind !== "real"), real: rows.filter((row) => row.kind === "real") };
}

/** Sum of the paper lanes per period (real money excluded). */
export function paperTotals(calendar: Calendar | null | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  for (const lane of splitKinds(calendar?.lanes ?? []).paper) {
    for (const [period, value] of Object.entries(lane.cells)) out[period] = (out[period] ?? 0) + value;
  }
  for (const key of Object.keys(out)) out[key] = Math.round(out[key] * 100) / 100;
  return out;
}

/** Totals over paper lanes for the overview strip. */
export function paperSummary(lanes: readonly LaneSummary[]) {
  const paper = splitKinds(lanes.filter((lane) => !lane.error)).paper;
  return {
    lanes: paper.length,
    capital: paper.reduce((sum, lane) => sum + (lane.capital || 0), 0),
    equity: paper.reduce((sum, lane) => sum + (lane.equity || 0), 0),
    realized: paper.reduce((sum, lane) => sum + (lane.realized || 0), 0),
    trades: paper.reduce((sum, lane) => sum + (lane.closed?.trades || 0), 0),
  };
}

/** "2026-11" -> "Nov 2026", "2026-11-18" -> "18 Nov", "2026" stays. */
export function periodLabel(period: string): string {
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  if (/^\d{4}-\d{2}-\d{2}$/.test(period)) return `${Number(period.slice(8))} ${months[Number(period.slice(5, 7)) - 1]}`;
  if (/^\d{4}-\d{2}$/.test(period)) return `${months[Number(period.slice(5, 7)) - 1]} ${period.slice(0, 4)}`;
  return period;
}

/** A heat level -3..3 for a P&L cell, scaled by the largest absolute cell in view. */
export function heat(value: number | undefined, scale: number): number {
  if (value == null || !Number.isFinite(value) || !scale || Math.abs(value) < 1) return 0;
  const level = Math.min(3, Math.ceil((Math.abs(value) / scale) * 3));
  return value > 0 ? level : -level;
}

/** IST calendar day `days` before today (for the default date range). */
export function istDaysAgo(days: number, now: Date = new Date()): string {
  return new Date(now.getTime() + 330 * 60_000 - days * 86_400_000).toISOString().slice(0, 10);
}

/** Query string from the set filters only. */
export function query(params: Record<string, string | number | null | undefined>): string {
  const parts = Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** Hold time between two ISO stamps, as "45m", "3h 10m", "2d 4h". */
export function holdText(entry?: string, exit?: string): string {
  const a = Date.parse(entry ?? ""), b = Date.parse(exit ?? "");
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return "—";
  const minutes = Math.round((b - a) / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
