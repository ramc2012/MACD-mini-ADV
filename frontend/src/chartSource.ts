import type { ChartSource } from "./Chart";
import type { Candle } from "./types";

const clock = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false,
});
const day = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", day: "2-digit", month: "short",
});

/** Describe the sources actually feeding a chart, not just broker connectivity. */
export function chartSourceStatus({ bar, quote, now, marketOpen, streamConnected, timeframeSeconds, loading }: {
  bar?: Candle; quote?: { label: string; title: string; old: boolean; aged: boolean }; now: number; marketOpen?: boolean;
  streamConnected: boolean; timeframeSeconds?: number; loading?: boolean;
}): ChartSource {
  if (loading) return { label: "Loading chart history", title: "Historical candles are loading", tone: "muted" };
  if (!bar) return { label: "No chart bars", title: "No candles are available for this symbol", tone: "warn" };

  const at = new Date(bar.timestamp * 1000);
  const barLabel = `${day.format(at)} ${clock.format(at)} IST`;
  const title = `Last chart bar opens ${barLabel} · exchange quote ${quote?.title ?? "unavailable"}`;
  if (marketOpen === false) return { label: `Last bar ${barLabel} · closed`, title, tone: "muted" };
  if (!streamConnected) return { label: `Stream reconnecting · bar ${barLabel}`, title, tone: "warn" };
  if (!quote || quote.label === "No quote") return { label: `No live quote · bar ${barLabel}`, title, tone: "warn" };
  if (quote.aged) return { label: `${quote.old ? "Prior-session" : "Aged"} quote · bar ${barLabel}`, title, tone: "warn" };
  const secondsSinceBarOpen = now / 1000 - bar.timestamp;
  const maxAge = Math.max(60, timeframeSeconds || 1800) * 2 + 120;
  if (marketOpen === true && secondsSinceBarOpen > maxAge) {
    return { label: `Bar lag · quote ${quote.label}`, title, tone: "warn" };
  }
  return { label: `Quote ${quote.label} · bar ${clock.format(at)} IST`, title, tone: marketOpen === true ? "live" : "muted" };
}
