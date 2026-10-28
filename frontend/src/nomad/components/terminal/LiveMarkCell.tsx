"use client";

/**
 * LiveMarkCell — a terminal-grade live price for a single table cell.
 *
 * Subscribes to ONE tape symbol via `useQuote` and renders its live LTP with the
 * green/red flash + a live dot when a tick is present; otherwise it falls back to
 * the row's static snapshot value (so nothing regresses for symbols the backend
 * isn't streaming yet). This is the primitive that makes the watchlist/positions
 * tables themselves "terminal-like" — no separate Terminal tab needed.
 *
 * TIMESTAMPED MARKS (2026-08-03, owner-requested): a price with no time on it
 * is unreadable during a stall — Friday's close and a live print looked
 * identical. Every mark now states WHEN its rate was taken:
 *   • live tick   → age since the tick ("3s", "2m"), green while fresh,
 *                   amber past `staleAfterSeconds`, red past 5x that
 *   • snapshot    → the age of `fallbackAt`, always muted, never a live dot
 *   • unknown     → "no time" rather than an implied-fresh bare number
 * The tooltip carries the absolute IST timestamp and the source (live tape vs
 * stored snapshot). Set `showAge={false}` for dense cells that only want the
 * tooltip + tone.
 *
 * Memoized leaf: one symbol's tick re-renders only its own cell, not the table.
 */
import { memo, useSyncExternalStore } from "react";

import { formatIST, formatNumber } from "@/components/desk-ui";
import { useQuote } from "@/hooks/useQuoteStore";
import { usePriceFlash } from "@/hooks/usePriceFlash";
import {useLaneLiveValue} from '@/hooks/useLaneLive';

/**
 * ONE shared 1s ticker for every mark cell on the page. A per-cell
 * setInterval would mean ~45 timers on the positions table alone, each
 * re-rendering its own leaf on its own phase; this is a single timer that
 * only runs while at least one cell is mounted.
 */
const clockListeners = new Set<() => void>();
let clockTimer: ReturnType<typeof setInterval> | null = null;
let clockNow = Date.now();

function subscribeClock(cb: () => void): () => void {
  clockListeners.add(cb);
  if (clockTimer === null) {
    clockTimer = setInterval(() => {
      clockNow = Date.now();
      clockListeners.forEach((fn) => fn());
    }, 1000);
  }
  return () => {
    clockListeners.delete(cb);
    if (clockListeners.size === 0 && clockTimer !== null) {
      clearInterval(clockTimer);
      clockTimer = null;
    }
  };
}

const getClock = () => clockNow;
// SSR: a fixed value keeps hydration deterministic; the first client tick
// corrects it within a second.
const getClockServer = () => 0;

/** Compact age: 4s · 3m · 2h · 3d. Sub-second reads as 0s, never "now". */
export function formatAge(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

export const LiveMarkCell = memo(function LiveMarkCell({
  symbol,
  fallback,
  fallbackAt,
  decimals = 2,
  prefix = "",
  showAge = false,
  staleAfterSeconds = 30,
}: {
  /** Broker/tape symbol (namespaced) to stream; null/undefined → fallback only. */
  symbol?: string | null;
  /** Static snapshot value shown when there's no live tick. */
  fallback?: number | null;
  /** When the snapshot value was marked (ISO string or epoch ms). */
  fallbackAt?: string | number | null;
  decimals?: number;
  prefix?: string;
  /** Render the age chip next to the price (tooltip always carries the time). */
  showAge?: boolean;
  /** Seconds after which a LIVE tick is called stale. */
  staleAfterSeconds?: number;
}) {
  const q = useQuote(symbol || undefined);
  const sessionOpen=useLaneLiveValue(l=>symbol?.endsWith('-EQ')?l.cash_open:l.market_open);
  const live = q?.ltp != null;
  const flash = usePriceFlash(live ? q?.ltp : undefined);
  const value = live ? (q?.ltp as number) : fallback;
  const flashBg = flash === "up" ? "bg-accent-green/20" : flash === "down" ? "bg-accent-red/20" : "";

  // The instant this mark's rate was taken: the tick's own exchange timestamp
  // when live (falling back to received-at), else the snapshot's mark time.
  const markMs = live
    ? (q?.ts && q.ts > 0 ? q.ts : q?.rxAt) ?? null
    : fallbackAt == null
      ? null
      : typeof fallbackAt === "number"
        ? fallbackAt
        : Date.parse(fallbackAt) || null;

  // Re-tick the age once a second (shared clock) so a frozen tape visibly
  // ages instead of showing whatever it said when the row last rendered.
  const now = useSyncExternalStore(subscribeClock, getClock, getClockServer);
  const ageMs = markMs == null || !now ? null : Math.max(0, now - markMs);
  const ageTone =
    ageMs == null
      ? "text-text-muted"
      : !live
        ? "text-text-muted"
        : ageMs > staleAfterSeconds * 5000
          ? "text-accent-red"
          : ageMs > staleAfterSeconds * 1000
            ? "text-accent-amber"
            : "text-accent-green";

  const title = markMs == null
    ? live
      ? "live tape · tick carried no timestamp"
      : "stored snapshot · no mark time reported"
    : `${live ? "live tape" : "stored snapshot"} · marked ${formatIST(new Date(markMs).toISOString())} · ${formatAge(ageMs ?? 0)} ago`;

  // Absolute clock time of the mark (HH:MM), the second line's main content —
  // the age alone can't tell you WHICH 15:29 close you are looking at.
  const clock = markMs == null ? null : formatIST(new Date(markMs));
  const dayPart = '';

  return (
    <span className="inline-flex flex-col items-end leading-tight" title={title}>
      <span
        className={`inline-flex items-center justify-end gap-1 rounded px-1 font-mono tabular-nums transition-colors ${flashBg} ${!live||ageMs==null||ageMs>staleAfterSeconds*1000?ageTone:""}`}
      >
        {value != null ? `${prefix}${formatNumber(value, decimals)}` : "—"}
        {live ? <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${ageMs!=null&&ageMs<=staleAfterSeconds*1000&&sessionOpen?'bg-accent-green':'bg-text-muted'}`} /> : null}
      </span>
      {showAge && value != null ? (
        <span className={`px-1 font-mono text-[9.5px] ${ageTone}`}>
          {ageMs == null ? "no time" : `${clock}${dayPart} · ${formatAge(ageMs)}`}
        </span>
      ) : null}
    </span>
  );
});
