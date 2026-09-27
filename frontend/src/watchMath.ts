import type { OptionWatchRow, SpotWatchRow } from "./types";

export function matchesWatchSearch(row: SpotWatchRow | OptionWatchRow, query: string): boolean {
  const text = ("underlying" in row
    ? `${row.symbol} ${row.underlying} ${row.strike} ${row.option_type} ${row.expiry}`
    : row.symbol).toLowerCase();
  return query.trim().toLowerCase().split(/\s+/).every((word) => text.includes(word));
}

const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });
const clock = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
const date = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" });
export function quoteStamp(timestamp?: string, observedAt?: string | number) {
  const value = new Date(timestamp || "");
  if (!Number.isFinite(value.getTime())) return { label: "No quote", title: "unavailable", old: false, aged: false };
  const now = new Date(observedAt ?? Date.now());
  const old = day.format(value) !== day.format(now);
  const delayed = now.getTime() - value.getTime() > 120_000;
  return {
    old, aged: old || delayed,
    label: old ? date.format(value) : `${clock.format(value)}${delayed ? " · aged" : ""}`,
    title: `${date.format(value)} ${value.getFullYear()}, ${clock.format(value)} IST${old ? " · prior-day quote" : delayed ? " · over 2 minutes old" : ""}`,
  };
}
