"use client";

/**
 * FootprintGrid — the last few footprint bars as a price × (bid|ask) grid.
 *
 * One shared price axis (union of all shown bars' levels, descending) with a
 * sell@bid / buy@ask cell pair per bar. Cells shade with imbalance intensity;
 * a ratio ≥ ratioHighlight (default 3×) gets a hard ring so the trigger
 * condition is visible at a glance. Each bar's volume POC row is outlined
 * amber. Per-bar Δ / cumulative delta / volume run underneath along with a
 * stacked imbalance summary (buy- vs sell-imbalanced level counts), and the
 * header carries the source honesty badge + the last bar's timestamp.
 *
 * HONESTY (2026-07-19): the bid/ask split is INFERRED, not observed. The feed
 * carries no aggressor-tagged prints (`backend/analytics/orderflow.py`) and
 * `market_ticks` has no per-trade size or side, so `build_footprint` buckets
 * `ltp` and assigns sides heuristically. Every label here says "inferred".
 */
import { useMemo } from "react";

import { FreshnessBadge, ProvenanceChip, formatIST, formatISTTime, formatNumber } from "@/components/desk-ui";
import { describeImbalance, imbalanceOf, provenanceOf, type DataMode } from "@/lib/market-semantics";

import { OfSourceBadge } from "./OfSourceBadge";

export type FootprintLevel = {
  price: number;
  buy: number;
  sell: number;
  /**
   * Backend-reported side ratios. UNBOUNDED by construction (values in the
   * millions when the opposing side is empty) — never render these directly;
   * they are inputs to `imbalanceOf`, nothing more.
   */
  buy_ratio?: number | null;
  sell_ratio?: number | null;
};
export type FootprintBar = {
  time: string;
  delta?: number;
  volume?: number;
  cumulative_delta?: number;
  levels?: FootprintLevel[];
};

const compact = (v?: number | null): string => {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${(n / 1_000).toFixed(0)}k`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toFixed(0);
};

/**
 * Bounded imbalance for one side of one level.
 *
 * The old `ratioOf` returned the backend's raw `buy_ratio` / `sell_ratio`,
 * which are unbounded by construction (a level with zero opposing volume has
 * produced values up to 4,006,145). Those were rendered verbatim as
 * "4006145.0×", which is not a measurement — it is a divide-by-zero artefact.
 * The honest statements are a 0–100% share plus the raw volumes, and
 * "one-sided" when there is no opposing volume at all.
 *
 * `ratio` survives ONLY as the threshold input for the highlight predicate, so
 * the existing ≥3× ring semantics (and therefore the hot counts, the stacked
 * summary bar and the legend) are preserved exactly.
 */
const imbalanceAt = (level: FootprintLevel, side: "buy" | "sell") =>
  imbalanceOf(
    side === "buy" ? level.buy : level.sell,
    side === "buy" ? level.sell : level.buy,
    side === "buy" ? level.buy_ratio : level.sell_ratio,
  );

/** Ring predicate — mathematically equivalent to the previous `ratio >= n`. */
const isHot = (imb: ReturnType<typeof imbalanceOf>, threshold: number): boolean =>
  imb.oneSided || (imb.ratio != null && imb.ratio >= threshold);

export function FootprintGrid({
  bars,
  maxBars = 4,
  ratioHighlight = 3,
  source,
  timeframe = "3m",
  dataMode,
  digits = 1,
  hideHeader = false,
  maxHeight = 420,
}: {
  bars?: FootprintBar[] | null;
  maxBars?: number;
  ratioHighlight?: number;
  source?: string | null;
  /** Bar aggregation window, surfaced in the provenance caption. */
  timeframe?: string | null;
  /** Declared by the owning desk; without it the caption can only say "unknown". */
  dataMode?: DataMode;
  digits?: number;
  hideHeader?: boolean;
  maxHeight?: number;
}) {
  const shown = useMemo(() => (bars ?? []).slice(-maxBars), [bars, maxBars]);

  const { prices, byBar } = useMemo(() => {
    const priceSet = new Map<string, number>();
    const perBar: Array<Map<string, FootprintLevel>> = shown.map((bar) => {
      const m = new Map<string, FootprintLevel>();
      for (const level of bar.levels ?? []) {
        if (!Number.isFinite(Number(level.price))) continue;
        const key = Number(level.price).toFixed(6);
        priceSet.set(key, Number(level.price));
        m.set(key, level);
      }
      return m;
    });
    const sorted = Array.from(priceSet.entries()).sort((a, b) => b[1] - a[1]);
    return { prices: sorted, byBar: perBar };
  }, [shown]);

  const maxVol = useMemo(() => {
    let max = 1;
    for (const bar of shown) for (const level of bar.levels ?? []) max = Math.max(max, Number(level.buy) || 0, Number(level.sell) || 0);
    return max;
  }, [shown]);

  /** Per-bar volume POC (price key with max buy+sell) + imbalance level counts. */
  const barMeta = useMemo(() => {
    return shown.map((bar) => {
      let pocKey: string | null = null;
      let pocVol = -1;
      let buyHot = 0;
      let sellHot = 0;
      for (const level of bar.levels ?? []) {
        if (!Number.isFinite(Number(level.price))) continue;
        const key = Number(level.price).toFixed(6);
        const total = (Number(level.buy) || 0) + (Number(level.sell) || 0);
        if (total > pocVol) { pocVol = total; pocKey = key; }
        if (isHot(imbalanceAt(level, "buy"), ratioHighlight)) buyHot += 1;
        if (isHot(imbalanceAt(level, "sell"), ratioHighlight)) sellHot += 1;
      }
      return { pocKey, buyHot, sellHot, levelCount: (bar.levels ?? []).length };
    });
  }, [shown, ratioHighlight]);

  const lastTime = shown.at(-1)?.time ?? null;

  // Panel provenance (0d): where these bars came from, how they are aggregated,
  // how complete the window is and how old the newest bar is.
  const provenance = useMemo(
    () =>
      provenanceOf({
        source,
        feature: "flow_attribution",
        asOf: lastTime,
        timeframe,
        dataMode,
        have: shown.length,
        expect: maxBars,
        completenessLabel: `${shown.length}/${maxBars} bars · ${prices.length} levels`,
      }),
    [source, lastTime, timeframe, dataMode, shown.length, maxBars, prices.length],
  );

  if (!shown.length || !prices.length) {
    return (
      <div className="space-y-2">
        {!hideHeader ? <OfSourceBadge source={source} /> : null}
        <div className="flex h-32 items-center justify-center rounded-xl border border-dashed border-bg-border/60 text-xs text-text-muted">
          No footprint bars — no quote stream reconstructed for this window yet.
        </div>
      </div>
    );
  }

  return (
    <div className="w-full">
      {!hideHeader ? (
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <OfSourceBadge source={source} />
            <span className="text-[10px] uppercase tracking-[0.12em] text-text-muted" title="Sides are INFERRED from the quote stream — the feed carries no aggressor-tagged trade prints, so no cell here is a measured buy or sell.">
              sides inferred from quotes
            </span>
            <span className="text-[10px] uppercase tracking-[0.12em] text-text-muted" title={`A level is ringed when one side is ≥${ratioHighlight}× the other, or when the opposing side has no volume at all ("one-sided"). Cell tooltips state the bounded 0–100% share, never an unbounded ratio.`}>≥{ratioHighlight}× or one-sided ringed</span>
            <span className="text-[10px] uppercase tracking-[0.12em] text-text-muted"><span className="text-accent-red/80">sell @ bid</span> · <span className="text-accent-green/80">buy @ ask</span></span>
            <span className="rounded border border-accent-amber/60 px-1 text-[9px] uppercase tracking-[0.1em] text-accent-amber">bar POC outlined</span>
          </div>
          <FreshnessBadge asOf={lastTime} label="last bar" />
        </div>
      ) : null}
      {!hideHeader ? <ProvenanceChip provenance={provenance} density="caption" /> : null}

      <div className="overflow-x-auto">
        <div className="min-w-fit">
          {/* header row: bar times + bid/ask column labels */}
          <div className="flex items-end gap-1 pb-1">
            <div className="w-[70px] shrink-0" />
            {shown.map((bar, i) => (
              <div key={i} className="w-[128px] shrink-0 text-center font-mono text-[10px] text-text-muted" title={`${formatIST(bar.time)} IST`}>
                {formatISTTime(bar.time) || `bar ${i + 1}`}
                <div className="mt-0.5 flex justify-between px-1 text-[8.5px] uppercase tracking-[0.1em]">
                  <span className="text-accent-red/80" title="volume assigned to the bid side — inferred from quotes, not an aggressor-tagged print">bid ×</span>
                  <span className="text-accent-green/80" title="volume assigned to the ask side — inferred from quotes, not an aggressor-tagged print">× ask</span>
                </div>
              </div>
            ))}
          </div>

          {/* price × bar grid */}
          <div className="overflow-y-auto" style={{ maxHeight }}>
            {prices.map(([key, price]) => (
              <div key={key} className="flex items-stretch gap-1 border-b border-bg-border/30">
                <div className="flex w-[70px] shrink-0 items-center justify-end pr-1.5 font-mono text-[10.5px] text-text-secondary">
                  {formatNumber(price, digits)}
                </div>
                {shown.map((_, barIdx) => {
                  const level = byBar[barIdx]?.get(key);
                  if (!level) {
                    // MISSING, not zero: this price never printed in this bar.
                    return (
                      <div
                        key={barIdx}
                        className="w-[128px] shrink-0 bg-bg-secondary/10"
                        title="no volume bucketed at this price in this bar"
                      />
                    );
                  }
                  const isPoc = barMeta[barIdx]?.pocKey === key;
                  const sellImb = imbalanceAt(level, "sell");
                  const buyImb = imbalanceAt(level, "buy");
                  const sellHot = isHot(sellImb, ratioHighlight);
                  const buyHot = isHot(buyImb, ratioHighlight);
                  const sellAlpha = 0.08 + 0.5 * Math.min(1, (Number(level.sell) || 0) / maxVol);
                  const buyAlpha = 0.08 + 0.5 * Math.min(1, (Number(level.buy) || 0) / maxVol);
                  return (
                    <div
                      key={barIdx}
                      className={`flex w-[128px] shrink-0 ${isPoc ? "outline outline-1 -outline-offset-1 outline-accent-amber/80" : ""}`}
                      title={isPoc ? `bar POC · ${formatNumber(price, digits)}` : undefined}
                    >
                      <div
                        className={`flex h-[19px] w-1/2 items-center justify-end pr-1 font-mono text-[10px] ${sellHot ? "font-bold text-accent-red ring-1 ring-inset ring-accent-red/80" : "text-text-secondary"}`}
                        style={{ backgroundColor: `rgba(255,71,87,${sellAlpha.toFixed(3)})` }}
                        title={`sell @ bid ${formatNumber(Number(level.sell), 0)} · ${describeImbalance(sellImb, "sell", "buy")}`}
                      >
                        {compact(level.sell)}
                      </div>
                      <div
                        className={`flex h-[19px] w-1/2 items-center pl-1 font-mono text-[10px] ${buyHot ? "font-bold text-accent-green ring-1 ring-inset ring-accent-green/80" : "text-text-secondary"}`}
                        style={{ backgroundColor: `rgba(0,212,163,${buyAlpha.toFixed(3)})` }}
                        title={`buy @ ask ${formatNumber(Number(level.buy), 0)} · ${describeImbalance(buyImb, "buy", "sell")}`}
                      >
                        {compact(level.buy)}
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>

          {/* per-bar delta + cumulative delta + volume + imbalance summary footer */}
          <div className="mt-1 flex items-start gap-1 border-t border-bg-border/50 pt-1">
            <div className="flex w-[70px] shrink-0 flex-col items-end pr-1.5 font-mono text-[9.5px] text-text-muted">
              <span>Δ</span>
              <span>CVD</span>
              <span>vol</span>
              <span className="mt-0.5">imb</span>
            </div>
            {shown.map((bar, i) => {
              const delta = Number(bar.delta ?? 0);
              const cvd = Number(bar.cumulative_delta ?? 0);
              const meta = barMeta[i];
              const hotTotal = (meta?.buyHot ?? 0) + (meta?.sellHot ?? 0);
              return (
                <div key={i} className="flex w-[128px] shrink-0 flex-col items-center font-mono text-[10px]">
                  <span className={delta > 0 ? "text-accent-green" : delta < 0 ? "text-accent-red" : "text-text-muted"}>
                    {delta > 0 ? "+" : ""}{compact(delta)}
                  </span>
                  <span className={cvd > 0 ? "text-accent-green/80" : cvd < 0 ? "text-accent-red/80" : "text-text-muted"}>
                    {cvd > 0 ? "+" : ""}{compact(cvd)}
                  </span>
                  <span className="text-text-muted">{compact(bar.volume)}</span>
                  {/* stacked imbalance summary: sell-imbalanced vs buy-imbalanced level counts */}
                  <div className="mt-0.5 w-full px-1" title={`${meta?.sellHot ?? 0} sell-imbalanced · ${meta?.buyHot ?? 0} buy-imbalanced levels (of ${meta?.levelCount ?? 0})`}>
                    {hotTotal > 0 ? (
                      <div className="flex h-[7px] w-full overflow-hidden rounded-sm bg-bg-secondary/40">
                        <div className="h-full bg-accent-red/75" style={{ width: `${((meta.sellHot / hotTotal) * 100).toFixed(1)}%` }} />
                        <div className="h-full bg-accent-green/75" style={{ width: `${((meta.buyHot / hotTotal) * 100).toFixed(1)}%` }} />
                      </div>
                    ) : (
                      <div className="h-[7px] w-full rounded-sm bg-bg-secondary/30" />
                    )}
                    <div className="mt-px flex justify-between text-[8px] leading-none">
                      <span className={meta?.sellHot ? "text-accent-red/90" : "text-text-muted"}>{meta?.sellHot ?? 0}S</span>
                      <span className={meta?.buyHot ? "text-accent-green/90" : "text-text-muted"}>{meta?.buyHot ?? 0}B</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
