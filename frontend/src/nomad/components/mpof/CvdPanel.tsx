"use client";

/**
 * CvdPanel — cumulative-volume-delta sparkline vs close overlay.
 *
 * CVD is INFERRED FROM QUOTES, never measured: there is no aggressor-tagged
 * trade tape on any wired broker (`backend/analytics/orderflow.py`), so each
 * bar's buy/sell split is a heuristic over ltp + L1 snapshots. The panel says
 * so on its face.
 *
 * Dual-axis recharts line (price left / CVD right) with:
 *   · a PROMINENT order-flow-source badge (TICK/BOOK QUOTES vs BAR PROXY —
 *     sides inferred either way) — the derivation is the headline, not a
 *     footnote
 *   · divergence markers drawn ON the chart: when the lane flags a
 *     divergence, the two swing points (price extreme vs CVD disagreement)
 *     are dotted and connected on both axes
 *   · a per-bar delta histogram subpanel (ΔCVD bar per bar, green/red)
 *   · a LastUpdated freshness badge on the last series point
 */
import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { LastUpdated } from "@/components/common/LastUpdated";
import { ProvenanceChip, StatusBadge, formatIST, formatISTTime, formatNumber } from "@/components/desk-ui";
import { provenanceOf, type DataMode } from "@/lib/market-semantics";

import { OfSourceBadge } from "./OfSourceBadge";

export type CvdPoint = { time: string; cvd: number; close: number };
export type CvdDivergence = { kind?: string | null; strength?: number | null } | null;

type DivergenceMarks = {
  bullish: boolean;
  a: CvdPoint; // earlier swing
  b: CvdPoint; // later swing
} | null;

/**
 * Locate the two swing points illustrating the lane-flagged divergence:
 * split the tail window in half and take the price extreme of each half.
 * (The backend owns the detection — this only places honest markers.)
 */
function findDivergenceMarks(rows: CvdPoint[], kind: string | null): DivergenceMarks {
  if (!kind || rows.length < 6) return null;
  const k = kind.toLowerCase();
  const bullish = k.includes("bull");
  const bearish = k.includes("bear");
  if (!bullish && !bearish) return null;
  const tail = rows.slice(-Math.min(rows.length, 16));
  const mid = Math.floor(tail.length / 2);
  const pick = (slice: CvdPoint[]): CvdPoint =>
    slice.reduce((best, r) => (bearish ? r.close >= best.close : r.close <= best.close) ? r : best, slice[0]);
  const a = pick(tail.slice(0, mid));
  const b = pick(tail.slice(mid));
  if (!a || !b || a.time === b.time) return null;
  return { bullish, a, b };
}

export function CvdPanel({
  series,
  source,
  divergence,
  asOf,
  timeframe = "3m",
  dataMode,
  height = 280,
  hideHeader = false,
  showDelta = true,
}: {
  series?: CvdPoint[] | null;
  source?: string | null;
  divergence?: CvdDivergence;
  /** Overrides the last series point as the freshness timestamp. */
  asOf?: string | null;
  /** Aggregation window, surfaced in the provenance caption. */
  timeframe?: string | null;
  /** Declared by the owning desk; without it the caption can only say "unknown". */
  dataMode?: DataMode;
  height?: number;
  hideHeader?: boolean;
  /** Render the per-bar delta histogram subpanel underneath. */
  showDelta?: boolean;
}) {
  const rows = useMemo(() => (series ?? []).filter((r) => r && Number.isFinite(Number(r.cvd)) && Number.isFinite(Number(r.close))), [series]);
  const lastTime = asOf ?? rows.at(-1)?.time ?? null;
  const divKind = divergence?.kind ? String(divergence.kind) : null;

  const marks = useMemo(() => findDivergenceMarks(rows, divKind), [rows, divKind]);
  const deltas = useMemo(
    () => rows.map((r, i) => ({ time: r.time, delta: i === 0 ? 0 : Number(r.cvd) - Number(rows[i - 1].cvd) })),
    [rows],
  );
  const hasDelta = showDelta && deltas.some((d) => d.delta !== 0);
  const deltaHeight = hasDelta ? Math.max(64, Math.round(height * 0.28)) : 0;
  const mainHeight = hasDelta ? height - deltaHeight - 6 : height;
  const markColor = marks ? (marks.bullish ? "#00d4a3" : "#ff4757") : "#a78bfa";

  return (
    <div className="w-full">
      {!hideHeader ? (
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <OfSourceBadge source={source} />
            <span className="text-[10px] uppercase tracking-[0.12em] text-text-muted" title="market_ticks carries quotes only (ltp / OHLC / cumulative volume / bid / ask) — no trade_id, no per-trade size, no broker aggressor flag. Each bar's buy/sell split is inferred.">
              CVD · inferred from quotes
            </span>
            {divKind ? (
              <StatusBadge
                label={`divergence · ${divKind}${divergence?.strength != null ? ` ${formatNumber(Number(divergence.strength) * 100, 0)}%` : ""}`}
                variant={divKind.toLowerCase().includes("bull") ? "success" : divKind.toLowerCase().includes("bear") ? "error" : "info"}
              />
            ) : (
              <span className="text-[10.5px] text-text-muted">no CVD divergence</span>
            )}
          </div>
          <LastUpdated timestamp={lastTime} label="last bar" />
        </div>
      ) : null}
      {!hideHeader ? (
        <ProvenanceChip
          density="caption"
          provenance={provenanceOf({
            source,
            feature: "flow_attribution",
            asOf: lastTime,
            timeframe,
            dataMode,
            have: rows.length,
            completenessLabel: `${rows.length} points`,
          })}
        />
      ) : null}

      {rows.length ? (
        <>
          <div style={{ height: mainHeight }}>
            <ResponsiveContainer>
              <LineChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} syncId="cvd-panel">
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="time" tickFormatter={(v) => formatISTTime(v)} minTickGap={30} tick={{ fontSize: 10, fill: "rgba(255,255,255,0.4)" }} stroke="rgba(255,255,255,0.12)" />
                <YAxis yAxisId="price" domain={["auto", "auto"]} width={58} tick={{ fontSize: 10, fill: "rgba(255,255,255,0.4)" }} stroke="rgba(255,255,255,0.12)" />
                <YAxis yAxisId="cvd" orientation="right" width={52} tick={{ fontSize: 10, fill: "#ffa502" }} stroke="rgba(255,255,255,0.12)" />
                <Tooltip
                  contentStyle={{ background: "#0d1117", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8, fontSize: 11 }}
                  labelFormatter={(v) => `${formatIST(v)} IST`}
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  formatter={(value: any, name: any) => [formatNumber(Number(value), name === "CVD" ? 0 : 2), name]}
                />
                <Line yAxisId="price" dataKey="close" name="Price" stroke="#3b82f6" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                <Line yAxisId="cvd" dataKey="cvd" name="CVD" stroke="#ffa502" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                {marks ? (
                  <>
                    {/* price-swing trendline + dots */}
                    <ReferenceLine yAxisId="price" segment={[{ x: marks.a.time, y: marks.a.close }, { x: marks.b.time, y: marks.b.close }]} stroke={markColor} strokeDasharray="4 3" strokeWidth={1.2} ifOverflow="extendDomain" />
                    <ReferenceDot yAxisId="price" x={marks.a.time} y={marks.a.close} r={4} fill={markColor} stroke="#0d1117" strokeWidth={1.5} ifOverflow="extendDomain" />
                    <ReferenceDot yAxisId="price" x={marks.b.time} y={marks.b.close} r={4} fill={markColor} stroke="#0d1117" strokeWidth={1.5} ifOverflow="extendDomain" />
                    {/* CVD disagreement trendline + dots */}
                    <ReferenceLine yAxisId="cvd" segment={[{ x: marks.a.time, y: marks.a.cvd }, { x: marks.b.time, y: marks.b.cvd }]} stroke={markColor} strokeDasharray="2 3" strokeWidth={1} ifOverflow="extendDomain" />
                    <ReferenceDot yAxisId="cvd" x={marks.a.time} y={marks.a.cvd} r={3} fill="#0d1117" stroke={markColor} strokeWidth={1.5} ifOverflow="extendDomain" />
                    <ReferenceDot yAxisId="cvd" x={marks.b.time} y={marks.b.cvd} r={3} fill="#0d1117" stroke={markColor} strokeWidth={1.5} ifOverflow="extendDomain" />
                  </>
                ) : null}
              </LineChart>
            </ResponsiveContainer>
          </div>

          {hasDelta ? (
            <div className="mt-1.5" style={{ height: deltaHeight }}>
              <ResponsiveContainer>
                <BarChart data={deltas} margin={{ top: 2, right: 4, bottom: 0, left: 0 }} syncId="cvd-panel">
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
                  <XAxis dataKey="time" tickFormatter={(v) => formatISTTime(v)} minTickGap={30} tick={{ fontSize: 9, fill: "rgba(255,255,255,0.3)" }} stroke="rgba(255,255,255,0.10)" height={14} />
                  <YAxis width={58} tick={{ fontSize: 9, fill: "rgba(255,255,255,0.35)" }} stroke="rgba(255,255,255,0.10)" tickFormatter={(v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v))} />
                  <Tooltip
                    contentStyle={{ background: "#0d1117", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8, fontSize: 11 }}
                    labelFormatter={(v) => `${formatIST(v)} IST`}
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    formatter={(value: any) => [formatNumber(Number(value), 0), "Δ delta (inferred)"]}
                  />
                  <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
                  <Bar dataKey="delta" isAnimationActive={false} maxBarSize={14}>
                    {deltas.map((d, i) => (
                      <Cell key={`${d.time}-${i}`} fill={d.delta >= 0 ? "#00d4a3" : "#ff4757"} fillOpacity={0.75} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : null}
        </>
      ) : (
        <div className="flex items-center justify-center rounded-xl border border-dashed border-bg-border/60 text-xs text-text-muted" style={{ height }}>
          No CVD series in this snapshot.
        </div>
      )}
    </div>
  );
}
