/**
 * market-hours — pure IST session clock.
 *
 * The "LIVE" badge in the old TopBar meant only "an API answered". Market
 * session state is a SEPARATE fact: whether the NSE / MCX trading window is
 * open right now. That is a deterministic function of the wall clock in IST —
 * no backend, no broker, no feed required. This helper computes it so the
 * truth strip can show "NSE closed" honestly even while every status endpoint
 * is up and answering.
 *
 * Sessions (regular trading, IST):
 *   NSE equity/F&O : Mon–Fri 09:15–15:30
 *   MCX commodity  : Mon–Fri 09:00–23:30 (evening session; single continuous
 *                    window is a deliberate simplification — it is a coarse
 *                    open/closed signal, not a microstructure calendar)
 *
 * Holidays are NOT modelled here (no client-side exchange holiday calendar);
 * the backend health payload's `next_market_open_ist` is the authority when a
 * holiday matters, and useSystemState cross-checks it.
 */

export type MarketSessions = {
  /** Minutes since IST midnight, for callers that want to render the clock. */
  istMinutes: number;
  /** IST day-of-week: 0=Sun … 6=Sat. */
  istWeekday: number;
  nseOpen: boolean;
  mcxOpen: boolean;
};

/** IST is a fixed UTC+05:30 offset (no DST) — safe to compute without tz libs. */
const IST_OFFSET_MINUTES = 5 * 60 + 30;

function istParts(now: Date): { weekday: number; minutes: number } {
  // Shift the UTC epoch into IST wall-clock, then read UTC fields off it.
  const shifted = new Date(now.getTime() + IST_OFFSET_MINUTES * 60_000);
  const weekday = shifted.getUTCDay();
  const minutes = shifted.getUTCHours() * 60 + shifted.getUTCMinutes();
  return { weekday, minutes };
}

const NSE_OPEN = 9 * 60 + 15; // 09:15
const NSE_CLOSE = 15 * 60 + 30; // 15:30
const MCX_OPEN = 9 * 60; // 09:00
const MCX_CLOSE = 23 * 60 + 30; // 23:30

/**
 * Compute NSE/MCX open-closed from the wall clock. `now` defaults to the live
 * clock; pass a fixed Date for deterministic tests. Weekends are always closed.
 */
export function marketSessions(now: Date = new Date()): MarketSessions {
  const { weekday, minutes } = istParts(now);
  const isWeekday = weekday >= 1 && weekday <= 5;
  return {
    istMinutes: minutes,
    istWeekday: weekday,
    nseOpen: isWeekday && minutes >= NSE_OPEN && minutes < NSE_CLOSE,
    mcxOpen: isWeekday && minutes >= MCX_OPEN && minutes < MCX_CLOSE,
  };
}
