"use client";

/**
 * Relative-Rotation-Graph (RRG) scatter — THE shared quadrant-scatter
 * renderer. One implementation behind the two domain adapters that used
 * to be near-copies (strategies/sector/RrgChart and strategies/cbe/
 * RrgScatter):
 *
 *   x-axis = relative strength     y-axis = momentum
 *   origin = 0,0 crosshair → 4 quadrants:
 *     top-right    Leading    top-left     Improving
 *     bottom-left  Lagging    bottom-right Weakening
 *
 * Pure SVG (viewBox-scaled) so it stays crisp at any width without a
 * charting dependency that can't draw labelled quadrants.
 *
 * Detail affordances (2026-08-02): numeric axis ticks with a faint grid,
 * per-quadrant membership counts, rotation tails with a start marker so
 * direction reads at a glance, a hover crosshair with axis read-outs,
 * and a tooltip that adds rotation delta (Δ over the tail window) and
 * distance-from-origin intensity to the adapter's own lines.
 */
import { useMemo, useState } from "react";

import { CHART } from "./chartTheme";

export const QUADRANT_COLOR: Record<string, string> = {
  leading: CHART.green,
  improving: CHART.blue,
  weakening: CHART.amber,
  lagging: CHART.red,
};

export function quadrantColor(q?: string): string {
  return QUADRANT_COLOR[String(q || "").toLowerCase()] || CHART.muted;
}

export type RrgScatterPoint = {
  key: string;
  /** Bubble label. */
  label: string;
  /** Tooltip headline; defaults to `label`. */
  title?: string;
  x: number;
  y: number;
  /** Bubble magnitude (abs, sqrt-scaled). */
  size: number;
  quadrant: string;
  /** Emphasized stroke ring (e.g. on-watchlist names). */
  ring?: boolean;
  /** Rotation path history, oldest first. */
  tail?: Array<{ x: number; y: number }>;
  /** Extra tooltip lines under the default read-out. */
  hoverLines?: string[];
};

const VB_W = 1000;
const PAD = { t: 22, r: 26, b: 34, l: 46 };

/** A "nice" tick step so ~2-3 ticks land on each side of zero. */
function niceStep(absMax: number): number {
  const raw = absMax / 2.5;
  const mag = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-9)));
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (raw <= m * mag) return m * mag;
  }
  return 10 * mag;
}

function fmtTick(v: number, step: number): string {
  const digits = step >= 1 ? 0 : step >= 0.25 ? 1 : 2;
  return v.toFixed(digits);
}

function fmtSigned(v: number): string {
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}

export function RrgScatter({
  points,
  selected,
  onSelect,
  xCaption = "relative strength →",
  yCaption = "↑ momentum",
  quadrantSubs = false,
  labelPolicy = "all",
  sizeCaption = "bubble size = magnitude",
  vbHeight = 620,
  minDomain,
  emptyText = "Awaiting rotation data…",
}: {
  points: RrgScatterPoint[];
  selected?: string | null;
  onSelect?: (key: string) => void;
  xCaption?: string;
  yCaption?: string;
  /** Show the explanatory sub-line under each quadrant title. */
  quadrantSubs?: boolean;
  /** "all" labels every bubble; "highlight" labels only ring/hover/selected. */
  labelPolicy?: "all" | "highlight";
  sizeCaption?: string;
  vbHeight?: number;
  /** Floor for the symmetric axis domains (data units). */
  minDomain?: { x?: number; y?: number };
  emptyText?: string;
}) {
  const [hover, setHover] = useState<RrgScatterPoint | null>(null);
  const VB_H = vbHeight;

  const model = useMemo(() => {
    if (!points.length) return null;
    const xs = points.flatMap((p) => [p.x, ...(p.tail || []).map((t) => t.x)]);
    const ys = points.flatMap((p) => [p.y, ...(p.tail || []).map((t) => t.y)]);
    // Symmetric bounds around 0 so the crosshair sits at the true centre.
    // Domain fits the 95th percentile of |values| (×1.25), NOT the max — one
    // outlier used to flatten the whole board into a band around the axis.
    // Points beyond the domain clamp to the frame edge; tooltips keep the
    // true values.
    const absFit = (vals: number[], floor: number) => {
      const sorted = vals.map(Math.abs).sort((a, b) => a - b);
      const hardMax = sorted[sorted.length - 1] || 0;
      const p95 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] || 0;
      return Math.max(floor, Math.min(hardMax, p95 * 1.25)) * 1.18;
    };
    const xAbs = absFit(xs, minDomain?.x ?? 1e-6);
    const yAbs = absFit(ys, minDomain?.y ?? 1e-6);
    const clamp = (v: number, abs: number) => Math.max(-abs, Math.min(abs, v));
    const xOf = (v: number) => PAD.l + ((clamp(v, xAbs) + xAbs) / (2 * xAbs)) * (VB_W - PAD.l - PAD.r);
    const yOf = (v: number) => PAD.t + (1 - (clamp(v, yAbs) + yAbs) / (2 * yAbs)) * (VB_H - PAD.t - PAD.b);
    const sMax = Math.max(1e-6, ...points.map((p) => Math.abs(p.size) || 0));
    const rOf = (s: number) => 6 + (Math.sqrt(Math.abs(s) / sMax) || 0) * 16;
    // Nice tick positions per axis (excluding 0 — the crosshair marks it).
    const xStep = niceStep(xAbs);
    const yStep = niceStep(yAbs);
    const ticksOf = (abs: number, step: number) => {
      const out: number[] = [];
      for (let v = step; v <= abs; v += step) out.push(v, -v);
      return out;
    };
    return { xAbs, yAbs, xOf, yOf, rOf, xStep, yStep, xTicks: ticksOf(xAbs, xStep), yTicks: ticksOf(yAbs, yStep) };
  }, [points, minDomain?.x, minDomain?.y, VB_H]);

  const quadCounts = useMemo(() => {
    const counts: Record<string, number> = { leading: 0, improving: 0, weakening: 0, lagging: 0 };
    for (const p of points) {
      const q = String(p.quadrant || "").toLowerCase();
      if (q in counts) counts[q] += 1;
    }
    return counts;
  }, [points]);

  if (!model || !points.length) {
    return (
      <div className="flex h-[340px] items-center justify-center rounded-xl border border-dashed border-bg-border/60 text-sm text-text-muted">
        {emptyText}
      </div>
    );
  }

  const { xOf, yOf, rOf, xStep, yStep, xTicks, yTicks } = model;
  const cx = xOf(0);
  const cy = yOf(0);

  // Sort so big/selected bubbles draw last (on top).
  const drawOrder = [...points].sort((a, b) => {
    if (a.key === selected) return 1;
    if (b.key === selected) return -1;
    return Math.abs(a.size) - Math.abs(b.size);
  });

  const showLabel = (p: RrgScatterPoint) =>
    labelPolicy === "all" || p.ring || p.key === selected || hover?.key === p.key;

  const hoverDetail = (p: RrgScatterPoint): string[] => {
    const lines = p.hoverLines?.length ? [...p.hoverLines] : [`x ${p.x.toFixed(2)} · y ${p.y.toFixed(2)}`, `size ${p.size.toFixed(2)}`];
    // Rotation delta over the tail window — the direction of travel.
    if (p.tail?.length) {
      const start = p.tail[0];
      lines.push(`Δ tail ${fmtSigned(p.x - start.x)} RS · ${fmtSigned(p.y - start.y)} mom (${p.tail.length + 1} obs)`);
    }
    lines.push(`intensity ${Math.hypot(p.x, p.y).toFixed(2)} from origin`);
    return lines;
  };

  return (
    <div className="relative w-full">
      <svg viewBox={`0 0 ${VB_W} ${VB_H}`} className="w-full" style={{ height: "auto", aspectRatio: `${VB_W} / ${VB_H}` }}>
        {/* quadrant fills */}
        <rect x={cx} y={PAD.t} width={VB_W - PAD.r - cx} height={cy - PAD.t} fill={CHART.green} opacity={0.05} />
        <rect x={PAD.l} y={PAD.t} width={cx - PAD.l} height={cy - PAD.t} fill={CHART.blue} opacity={0.05} />
        <rect x={PAD.l} y={cy} width={cx - PAD.l} height={VB_H - PAD.b - cy} fill={CHART.red} opacity={0.05} />
        <rect x={cx} y={cy} width={VB_W - PAD.r - cx} height={VB_H - PAD.b - cy} fill={CHART.amber} opacity={0.05} />

        {/* tick grid — light, behind everything else */}
        {xTicks.map((v) => (
          <g key={`xt-${v}`}>
            <line x1={xOf(v)} y1={PAD.t} x2={xOf(v)} y2={VB_H - PAD.b} stroke={CHART.grid} strokeWidth={0.5} opacity={0.55} />
            <text x={xOf(v)} y={VB_H - PAD.b + 13} fill={CHART.axis} fontSize={8.5} textAnchor="middle">
              {fmtTick(v, xStep)}
            </text>
          </g>
        ))}
        {yTicks.map((v) => (
          <g key={`yt-${v}`}>
            <line x1={PAD.l} y1={yOf(v)} x2={VB_W - PAD.r} y2={yOf(v)} stroke={CHART.grid} strokeWidth={0.5} opacity={0.55} />
            <text x={PAD.l - 6} y={yOf(v) + 3} fill={CHART.axis} fontSize={8.5} textAnchor="end">
              {fmtTick(v, yStep)}
            </text>
          </g>
        ))}

        {/* crosshair axes */}
        <line x1={cx} y1={PAD.t} x2={cx} y2={VB_H - PAD.b} stroke={CHART.axis} strokeWidth={0.8} strokeDasharray="4 4" />
        <line x1={PAD.l} y1={cy} x2={VB_W - PAD.r} y2={cy} stroke={CHART.axis} strokeWidth={0.8} strokeDasharray="4 4" />

        {/* quadrant labels with membership counts */}
        <QuadLabel x={VB_W - PAD.r - 8} y={PAD.t + 16} anchor="end" color={CHART.green} title={`LEADING · ${quadCounts.leading}`} sub={quadrantSubs ? "strong · accelerating" : undefined} />
        <QuadLabel x={PAD.l + 8} y={PAD.t + 16} anchor="start" color={CHART.blue} title={`IMPROVING · ${quadCounts.improving}`} sub={quadrantSubs ? "weak · accelerating" : undefined} />
        <QuadLabel x={PAD.l + 8} y={VB_H - PAD.b - 18} anchor="start" color={CHART.red} title={`LAGGING · ${quadCounts.lagging}`} sub={quadrantSubs ? "weak · decelerating" : undefined} />
        <QuadLabel x={VB_W - PAD.r - 8} y={VB_H - PAD.b - 18} anchor="end" color={CHART.amber} title={`WEAKENING · ${quadCounts.weakening}`} sub={quadrantSubs ? "strong · decelerating" : undefined} />

        {/* axis captions */}
        <text x={VB_W - PAD.r} y={cy - 6} fill={CHART.muted} fontSize={10} textAnchor="end">{xCaption}</text>
        <text x={cx + 6} y={PAD.t + 10} fill={CHART.muted} fontSize={10} textAnchor="start">{yCaption}</text>

        {/* rotation tails — dashed path, hollow start marker for direction */}
        {drawOrder.map((p) => {
          if (!p.tail?.length) return null;
          const pts = [...p.tail, { x: p.x, y: p.y }];
          const d = pts.map((t, i) => `${i === 0 ? "M" : "L"}${xOf(t.x).toFixed(1)},${yOf(t.y).toFixed(1)}`).join(" ");
          const isFocus = hover?.key === p.key || p.key === selected;
          return (
            <g key={`tail-${p.key}`}>
              <path d={d} fill="none" stroke={quadrantColor(p.quadrant)} strokeWidth={isFocus ? 1.6 : 1} opacity={isFocus ? 0.7 : 0.35} strokeDasharray="2 3" />
              <circle
                cx={xOf(p.tail[0].x)}
                cy={yOf(p.tail[0].y)}
                r={2.2}
                fill="none"
                stroke={quadrantColor(p.quadrant)}
                strokeWidth={1}
                opacity={isFocus ? 0.8 : 0.45}
              />
            </g>
          );
        })}

        {/* hover crosshair — dashed drop-lines with axis read-outs */}
        {hover ? (
          <g pointerEvents="none">
            <line x1={xOf(hover.x)} y1={yOf(hover.y)} x2={xOf(hover.x)} y2={VB_H - PAD.b} stroke={CHART.muted} strokeWidth={0.7} strokeDasharray="2 3" opacity={0.7} />
            <line x1={PAD.l} y1={yOf(hover.y)} x2={xOf(hover.x)} y2={yOf(hover.y)} stroke={CHART.muted} strokeWidth={0.7} strokeDasharray="2 3" opacity={0.7} />
            <text x={xOf(hover.x)} y={VB_H - PAD.b - 4} fill="#e6edf3" fontSize={9} textAnchor="middle" fontWeight={600}>
              {hover.x.toFixed(2)}
            </text>
            <text x={PAD.l + 4} y={yOf(hover.y) - 4} fill="#e6edf3" fontSize={9} textAnchor="start" fontWeight={600}>
              {hover.y.toFixed(2)}
            </text>
          </g>
        ) : null}

        {/* bubbles */}
        {drawOrder.map((p) => {
          const x = xOf(p.x);
          const y = yOf(p.y);
          const r = rOf(p.size);
          const col = quadrantColor(p.quadrant);
          const isSel = p.key === selected;
          const isHover = hover?.key === p.key;
          return (
            <g
              key={p.key}
              onMouseEnter={() => setHover(p)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(p.key)}
              style={{ cursor: onSelect ? "pointer" : "default" }}
            >
              <circle
                cx={x}
                cy={y}
                r={r}
                fill={col}
                fillOpacity={isSel || isHover ? 0.4 : 0.22}
                stroke={col}
                strokeWidth={isSel || isHover ? 2.2 : p.ring ? 1.8 : 1.1}
              />
              <circle cx={x} cy={y} r={2} fill={col} />
              {showLabel(p) ? (
                <text
                  x={x}
                  y={y - r - 4}
                  fill={isSel || isHover ? "#e6edf3" : CHART.muted}
                  fontSize={isSel ? 11 : 9.5}
                  textAnchor="middle"
                  fontWeight={isSel || p.ring ? 700 : 500}
                >
                  {p.label}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>

      {hover ? (
        <div
          className="pointer-events-none absolute right-3 top-3 z-10 rounded-lg border px-3 py-2 text-[11px] font-mono"
          style={{ background: CHART.surface, borderColor: CHART.border }}
        >
          <div className="mb-1 font-sans text-[12px] font-semibold" style={{ color: quadrantColor(hover.quadrant) }}>
            {hover.title || hover.label}
          </div>
          <div className="text-text-muted">{hover.quadrant.toUpperCase()}</div>
          {hoverDetail(hover).map((line) => (
            <div key={line}>{line}</div>
          ))}
        </div>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-text-muted">
        <Legend color={CHART.green} label={`leading ${quadCounts.leading}`} />
        <Legend color={CHART.blue} label={`improving ${quadCounts.improving}`} />
        <Legend color={CHART.amber} label={`weakening ${quadCounts.weakening}`} />
        <Legend color={CHART.red} label={`lagging ${quadCounts.lagging}`} />
        <span className="text-text-muted/70">{sizeCaption}</span>
        <span className="text-text-muted/70">{points.length} plotted · ○ = tail start</span>
      </div>
    </div>
  );
}

function QuadLabel({ x, y, anchor, color, title, sub }: { x: number; y: number; anchor: "start" | "end"; color: string; title: string; sub?: string }) {
  return (
    <g>
      <text x={x} y={y} fill={color} fontSize={12} fontWeight={700} textAnchor={anchor} opacity={0.85}>{title}</text>
      {sub ? <text x={x} y={y + 12} fill={CHART.muted} fontSize={8.5} textAnchor={anchor}>{sub}</text> : null}
    </g>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-2 w-2 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}
