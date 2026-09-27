import type { AuctionContext, Candle, ChartLayers } from "./types";

/** Session arithmetic for the terminal chart.
 *
 * Pure functions, no React and no chart handle, so the rules the chart and
 * App both depend on — where a session starts, how many sessions a period
 * covers, what today's initial balance is — are stated once and can be
 * tested without a DOM.
 */
export const IST_OFFSET = 19_800;
// 09:15 IST, seconds after IST midnight. Bars are bucketed from this anchor
// (chart_history.py) and the stored IB is the first hour from it
// (profile_history.py), so a client IB must use the same window or it will
// disagree with the profile it sits next to.
export const SESSION_OPEN_SECONDS = 33_300;
export const IB_SECONDS = 3_600;

/** IST calendar-day key; the same rule that resets VWAP. */
export const sessionKey = (timestamp: number) => Math.floor((timestamp + IST_OFFSET) / 86_400);
/** Epoch seconds of 09:15 IST for a session key. */
export const sessionOpen = (key: number) => key * 86_400 - IST_OFFSET + SESSION_OPEN_SECONDS;

/** Timestamp of the first bar of every session after the first visible one. */
export function sessionStarts(rows: Candle[]): number[] {
  const out: number[] = [];
  let previous = Number.NaN;
  for (const row of rows) {
    const key = sessionKey(row.timestamp);
    if (key !== previous) { out.push(row.timestamp); previous = key; }
  }
  return out.slice(1);
}

/** 00:00 IST of the first of the last `sessions` sessions present in `rows` (0 = no cutoff).
 *
 * Periods count sessions, not wall-clock days: a 24-hour window at 09:20
 * showed yesterday from 09:20 onward plus a few bars of today, which is not
 * what "1D" means on a trading desk.
 */
export function sessionCutoff(rows: Candle[], sessions: number): number {
  if (!sessions || !rows.length) return 0;
  const keys = [...new Set(rows.map((row) => sessionKey(row.timestamp)))].sort((a, b) => a - b);
  const first = keys[Math.max(0, keys.length - sessions)];
  return first * 86_400 - IST_OFFSET;
}

export type InitialBalance = { session: number; high: number; low: number; complete: boolean };
/** Initial balance of the latest session in `rows`: the first hour from 09:15.
 *
 * `complete` is false while the hour is still forming, which the chart shows
 * as a lighter line so a half-built IB is not read as a broken one.
 */
export function initialBalance(rows: Candle[], timeframe: number): InitialBalance | undefined {
  if (!rows.length) return undefined;
  const last = rows[rows.length - 1];
  const session = sessionKey(last.timestamp);
  const open = sessionOpen(session);
  const end = open + IB_SECONDS;
  const window = rows.filter((row) => sessionKey(row.timestamp) === session && row.timestamp >= open && row.timestamp < end);
  if (!window.length) return undefined;
  const complete = last.timestamp >= end || (last.timestamp + timeframe >= end && last.closed);
  return { session, high: Math.max(...window.map((row) => row.high)), low: Math.min(...window.map((row) => row.low)), complete };
}

/** Stored history plus the bars that closed since it was fetched.
 *
 * Only newer timestamps append; a stored row is never replaced, the same
 * invariant the indicator merge keeps so the price and indicator panes
 * cannot drift into two time domains.
 */
export function mergeLiveCandles(stored: Candle[], live: Candle[]): Candle[] {
  if (!live.length) return stored;
  const lastStored = stored.length ? stored[stored.length - 1].timestamp : 0;
  const newer = live.filter((row) => row.timestamp > lastStored);
  if (!newer.length) return stored;
  const byTime = new Map(newer.map((row) => [row.timestamp, row]));
  return [...stored, ...[...byTime.values()].sort((a, b) => a.timestamp - b.timestamp)];
}

export type ReferenceKind = "pd_poc" | "pd_va" | "wk_poc" | "wk_va" | "naked" | "ib" | "ib_forming";
export type ReferenceLevel = { key: string; price: number; title: string; kind: ReferenceKind };
export type ReferenceLayers = Pick<ChartLayers, "priorDay" | "week" | "nakedPocs" | "ib">;
const NAKED_POC_LIMIT = 8;

/** The auction references a trader wants drawn, flattened to price lines. */
export function referenceLevels(context: AuctionContext | undefined, ib: InitialBalance | undefined, layers: ReferenceLayers): ReferenceLevel[] {
  const out: ReferenceLevel[] = [];
  const pd = context?.prior_day;
  if (layers.priorDay && pd) {
    // The date travels with the line: a page left open past a nightly rebuild
    // that has not run yet would otherwise show a two-session-old "prior day".
    const day = pd.day.slice(5).replace("-", "/");
    if (pd.poc != null) out.push({ key: "pd_poc", price: pd.poc, title: `PD POC ${day}`, kind: "pd_poc" });
    if (pd.vah != null) out.push({ key: "pd_vah", price: pd.vah, title: "PD VAH", kind: "pd_va" });
    if (pd.val != null) out.push({ key: "pd_val", price: pd.val, title: "PD VAL", kind: "pd_va" });
  }
  const week = context?.week;
  if (layers.week && week) {
    if (week.poc != null) out.push({ key: "wk_poc", price: week.poc, title: "WK POC", kind: "wk_poc" });
    if (week.vah != null) out.push({ key: "wk_vah", price: week.vah, title: "WK VAH", kind: "wk_va" });
    if (week.val != null) out.push({ key: "wk_val", price: week.val, title: "WK VAL", kind: "wk_va" });
  }
  if (layers.nakedPocs && context) {
    context.naked_pocs.slice(0, NAKED_POC_LIMIT).forEach((price, index) =>
      out.push({ key: `npoc_${index}`, price, title: index === 0 ? "NAKED POC" : "", kind: "naked" }));
  }
  if (layers.ib && ib) {
    const kind = ib.complete ? "ib" : "ib_forming";
    out.push({ key: "ib_high", price: ib.high, title: ib.complete ? "IB H" : "IB H (forming)", kind });
    out.push({ key: "ib_low", price: ib.low, title: ib.complete ? "IB L" : "IB L (forming)", kind });
  }
  return out;
}
