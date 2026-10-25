"use client";

/**
 * Order-flow source honesty badge.
 *
 * Every OF visualization must state HOW its buy/sell split was obtained:
 *   TICK QUOTES · SIDES INFERRED (info)  — flow rebuilt from the L1 quote/tick
 *                                          stream (market_ticks,
 *                                          tick_reconstruction)
 *   BOOK QUOTES · SIDES INFERRED (info)  — rebuilt from L2 book snapshots
 *                                          (tick_reconstruction_book)
 *   BAR PROXY · SIDES INFERRED (amber, pulsing)
 *                                        — fabricated from bar OHLCV shape
 *   SOURCE UNKNOWN (neutral)             — payload didn't say; treat with
 *                                          suspicion.
 *
 * 2026-07-19 — this badge used to say "REAL TICKS". That claimed a trade-print
 * tape the feed has never carried: `market_ticks` stores only quotes
 * (ltp/OHLC/cumulative volume/oi/bid/ask/qty — no trade_id, no per-trade size,
 * no aggressor), and `backend/analytics/orderflow.py` says outright that
 * Indian retail brokers do not push public trade prints, so CVD/footprint
 * sides are approximated from OHLCV bars + L1 snapshots. The quote STREAM is
 * observed; the SIDES are not. Labels now say which.
 *
 * The pulse on the amber badge is deliberate — bar-fabricated flow must be
 * impossible to mistake for quote-derived flow.
 *
 * Classification is delegated to the shared semantic contract
 * (`@/lib/market-semantics` → `lib/flow-provenance`) so this badge, the
 * provenance chip and every desk's own honesty label can never disagree.
 */
import { classifyOfSource } from "@/lib/market-semantics";

export { classifyOfSource };
export type { OfSourceKind, OfSourceClass } from "@/lib/market-semantics";

const STYLE: Record<string, string> = {
  quote_derived: "border-accent-blue/50 bg-accent-blue/15 text-accent-blue",
  bar_inferred: "border-accent-amber/50 bg-accent-amber/15 text-accent-amber animate-pulse",
  unknown: "border-bg-border bg-bg-secondary/40 text-text-muted",
};

const DOT: Record<string, string> = {
  quote_derived: "bg-accent-blue",
  bar_inferred: "bg-accent-amber",
  unknown: "bg-text-muted",
};

export function OfSourceBadge({
  source,
  size = "md",
  className,
}: {
  source?: string | null;
  size?: "sm" | "md";
  className?: string;
}) {
  const { kind, label, note } = classifyOfSource(source);
  const pad = size === "sm" ? "px-1.5 py-[1px] text-[9px]" : "px-2.5 py-0.5 text-[10.5px]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-semibold uppercase tracking-[0.12em] ${pad} ${STYLE[kind]} ${className ?? ""}`}
      title={`order-flow source: ${source || "not reported"} — ${note}`}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[kind]}`} />
      {label}
    </span>
  );
}
