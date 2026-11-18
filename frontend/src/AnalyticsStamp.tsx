import { useCachedJson } from "./useCachedJson";

type Status = { runs: number; last_finished?: string; last_reason?: string; last_seconds?: number; next_slot?: string | null; every_minutes: number;
  views: Record<string, { at: string; seconds: number; error: string | null }> };
const CLOCK = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const at = (iso?: string | null) => (iso ? `${CLOCK.format(new Date(iso))} IST` : "—");

/** One line under the Market breadth tabs: when the stored views were last computed and when they are next due
 *  (analytics_schedule.py, owner 18 Nov 2026). An open page keeps polling its own view as before. */
export function AnalyticsStamp() {
  const { data } = useCachedJson<Status>("/api/analytics/status", 60_000);
  if (!data) return null;
  const failed = Object.entries(data.views || {}).filter(([, row]) => row.error);
  return <div className="lane-day-note analytics-stamp" role="note">
    Stored views · computed {data.last_finished ? at(data.last_finished) : "at start-up (running)"}{data.last_seconds ? ` in ${Math.round(data.last_seconds)} s` : ""}
    {" "}· every {data.every_minutes} min in session{data.next_slot ? ` · next ${at(data.next_slot)}` : ""} · an open page refreshes on its own
    {failed.length > 0 && <span className="negative" title={failed.map(([name, row]) => `${name}: ${row.error}`).join("\n")}> · {failed.length} view{failed.length === 1 ? "" : "s"} failed</span>}
  </div>;
}
