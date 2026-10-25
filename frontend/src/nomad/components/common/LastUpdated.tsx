"use client";

/**
 * LastUpdated — the canonical data-freshness badge.
 *
 * Renders the absolute IST wall-clock time of a payload timestamp plus a
 * live relative age that ticks every second, colour-coded by staleness:
 *
 *   green  dot  age <  staleAfterSeconds     (default 120s)
 *   amber  dot  age <  criticalAfterSeconds  (default 600s)
 *   red    dot  age >= criticalAfterSeconds
 *   gray   dot  timestamp null / invalid     ("no data")
 *
 * Every lane page header (via DeskShell) and per-section header uses this
 * one component so "how fresh is this?" reads the same everywhere.
 *
 * Timestamp parsing goes through desk-ui's `toDate`, which treats the
 * backend's TZ-naive ISO strings as UTC (the repo-wide convention).
 */
import { clsx } from "clsx";
import { useEffect, useState } from "react";

import { formatTimestamp, toDate } from "@/components/desk-ui/formatters";

export type FreshnessTone = "fresh" | "stale" | "critical" | "none";

/**
 * Shared threshold logic — exported so other components can colour their
 * own ages with the exact same cutoffs.
 */
export function freshnessTone(
  ageSeconds: number | null | undefined,
  stale = 120,
  critical = 600,
): FreshnessTone {
  if (ageSeconds == null || Number.isNaN(ageSeconds)) return "none";
  if (ageSeconds < stale) return "fresh";
  if (ageSeconds < critical) return "stale";
  return "critical";
}

const DOT: Record<FreshnessTone, string> = {
  fresh: "bg-accent-green",
  stale: "bg-accent-amber",
  critical: "bg-accent-red",
  none: "bg-text-muted",
};

const AGE_TEXT: Record<FreshnessTone, string> = {
  fresh: "text-accent-green",
  stale: "text-accent-amber",
  critical: "text-accent-red",
  none: "text-text-muted",
};

/** Newest parseable timestamp out of a bag of optional ISO strings — for
 *  section headers that surface the freshest row of a table. */
export function newestTimestamp(
  values: Array<string | number | Date | null | undefined>,
): string | null {
  let best: Date | null = null;
  for (const v of values) {
    if (v == null || v === "") continue;
    const d = toDate(v);
    if (Number.isNaN(d.getTime())) continue;
    if (!best || d.getTime() > best.getTime()) best = d;
  }
  return best ? best.toISOString() : null;
}

function formatAge(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

const IST = "Asia/Kolkata";

function istClock(d: Date): string {
  return `${d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: IST,
  })} IST`;
}

function istDay(d: Date): string {
  return d.toLocaleDateString("en-CA", { timeZone: IST });
}

function istDatePrefix(d: Date): string {
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: IST });
}

export function LastUpdated({
  timestamp,
  label = "Updated",
  staleAfterSeconds = 120,
  criticalAfterSeconds = 600,
  className,
}: {
  /** ISO string (naive = UTC per backend convention), epoch ms, or Date. */
  timestamp?: string | number | Date | null;
  /** Badge prefix — "Updated" for payload times, "Fetched" for client fetch times. */
  label?: string;
  staleAfterSeconds?: number;
  criticalAfterSeconds?: number;
  className?: string;
}) {
  // Tick once a second. `nowMs` starts null so the server render and the
  // first client render agree (no hydration mismatch); the age appears on
  // the first tick after mount.
  const [nowMs, setNowMs] = useState<number | null>(null);
  useEffect(() => {
    setNowMs(Date.now());
    const id = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(id);
  }, []);

  const parsed = timestamp == null ? null : toDate(timestamp);
  const valid = parsed != null && !Number.isNaN(parsed.getTime());

  const ageSeconds = valid && nowMs != null ? Math.max(0, (nowMs - parsed.getTime()) / 1000) : null;
  const tone: FreshnessTone = valid
    ? freshnessTone(ageSeconds, staleAfterSeconds, criticalAfterSeconds)
    : "none";

  // Show a date prefix once the timestamp is no longer "today" in IST.
  const showDate = valid && nowMs != null && istDay(parsed) !== istDay(new Date(nowMs));

  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-bg-border bg-bg-primary/20 px-2.5 py-0.5 text-[11px] leading-4 text-text-muted",
        tone === "critical" && "border-accent-red/40",
        className,
      )}
      title={valid ? `${label} ${formatTimestamp(parsed)}` : "No timestamp in payload"}
    >
      <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", DOT[tone])} />
      <span className="text-[10px] font-semibold uppercase tracking-[0.12em]">{label}</span>
      {valid ? (
        <>
          <span className="font-mono text-text-secondary">
            {formatTimestamp(parsed)}
          </span>
          <span className={clsx("font-mono", AGE_TEXT[tone])}>
            {ageSeconds != null ? `${formatAge(ageSeconds)} ago` : "…"}
          </span>
        </>
      ) : (
        <span className="font-mono">no data</span>
      )}
    </span>
  );
}

export default LastUpdated;
