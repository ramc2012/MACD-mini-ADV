import type { HealthInfo } from "./types";

export function Readiness({ health, error, updated }: { health?: HealthInfo; error: string; updated?: string }) {
  const stamp = updated ? new Date(updated).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" }) : "not yet received";
  const warm = health?.warmup;
  return <details className="readiness">
    <summary>Readiness · {error ? "health unavailable" : health?.session_open ? health.feed_alive ? "receiving ticks" : "waiting for feed" : health ? "session closed" : "loading"}
      {warm && ` · ${warm.with_indicators}/${warm.required} indicators available`}
      {!!health?.history_errors && ` · ${health.history_errors} history errors`}
    </summary>
    <div className="readiness-body">
      <p>Last health update: {stamp} IST. {error && `${error}; previous readings are stale.`}</p>
      <p>Broker: {health?.status || "unknown"} · Last tick: {health?.last_tick_age_seconds == null ? "none" : `${Math.round(health.last_tick_age_seconds)}s ago at last update`} · Feed recoveries: {health?.feed_recoveries ?? "—"} · Pre-open refreshes: {health?.preopen_refreshes ?? "—"}</p>
      {health?.error && <p role="alert">{health.error}</p>}
      <p>Indicator availability measures warm-up coverage, not signal qualification. A closed session can legitimately have no new ticks.</p>
      {!!health?.history_errors && <><p>History failures (showing up to 20). A failed symbol may have only cached history.</p><ul>{Object.entries(health.history_error_details || {}).map(([symbol, reason]) => <li key={symbol}><b>{symbol}</b> — {reason}</li>)}</ul></>}
    </div>
  </details>;
}
