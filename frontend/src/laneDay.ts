// The lane day (owner, 18 Nov 2026): lane pages show today's orders and trades only; the full
// history lives in the Back office. Mirror of engine market_calendar.lane_day_start: the day
// starts at 08:00 IST on the latest trading day whose 08:00 has passed, the same roll as
// closed positions' visible_until. MCX evening sessions stay in that day.

const IST_OFFSET_MS = 330 * 60_000;
const ROLL_MINUTES = 8 * 60;

function tradingDay(day: string, holidays: Set<string>) {
  const weekday = new Date(`${day}T00:00:00Z`).getUTCDay();
  return weekday !== 0 && weekday !== 6 && !holidays.has(day);
}

/** Start of the lane day as epoch milliseconds. */
export function laneDayStart(now: Date = new Date(), holidays: string[] = []): number {
  const set = new Set(holidays);
  const shifted = new Date(now.getTime() + IST_OFFSET_MS);
  let day = shifted.toISOString().slice(0, 10);
  const minutes = shifted.getUTCHours() * 60 + shifted.getUTCMinutes();
  const back = (d: string) => new Date(Date.parse(`${d}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
  if (minutes < ROLL_MINUTES || !tradingDay(day, set)) {
    day = back(day);
    for (let guard = 0; guard < 20 && !tradingDay(day, set); guard++) day = back(day);
  }
  return Date.parse(`${day}T00:00:00Z`) + ROLL_MINUTES * 60_000 - IST_OFFSET_MS;
}

/** The lane day as an IST date label, e.g. "Wed 18 Nov". */
export function laneDayLabel(now: Date = new Date(), holidays: string[] = []): string {
  const start = new Date(laneDayStart(now, holidays) + IST_OFFSET_MS);
  return start.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
}

/** True when an ISO timestamp falls in the current lane day (unparseable or missing: kept). */
export function inLaneDay(value: string | null | undefined, start: number): boolean {
  if (!value) return true;
  const at = Date.parse(value);
  return Number.isNaN(at) || at >= start;
}
