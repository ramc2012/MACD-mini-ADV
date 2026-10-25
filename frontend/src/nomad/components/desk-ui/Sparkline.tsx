"use client";

/**
 * Tiny inline-SVG sparkline — no charting dependency, viewBox-scaled so it
 * stays crisp at any width and renders dozens per page cheaply.
 *
 * Merges the two prior near-copies (strategies/cbe and strategies/macro):
 *  - `color` omitted → auto tone by net move (green up / red down), the cbe
 *    alpha-table behaviour;
 *  - `color` given → fixed stroke, the macro-tape behaviour;
 *  - `endDot` marks the latest value;
 *  - `fill` draws a soft gradient under the line.
 */
import { useMemo } from "react";

const UP = "rgb(var(--accent-green))";
const DOWN = "rgb(var(--accent-red))";

export function Sparkline({
  values,
  width = 96,
  height = 26,
  color,
  fill = true,
  endDot = false,
}: {
  values?: number[] | null;
  width?: number;
  height?: number;
  /** Fixed stroke color; omit to auto-tone by net move (up=green, down=red). */
  color?: string;
  fill?: boolean;
  endDot?: boolean;
}) {
  const series = useMemo(
    () => (values || []).filter((v) => typeof v === "number" && Number.isFinite(v)),
    [values],
  );

  const geometry = useMemo(() => {
    if (series.length < 2) return null;
    const min = Math.min(...series);
    const max = Math.max(...series);
    const span = max - min || 1;
    const pad = 2;
    const innerW = width - pad * 2;
    const innerH = height - pad * 2;
    const step = innerW / (series.length - 1);
    const xy = series.map((v, i) => {
      const x = pad + i * step;
      const y = pad + (1 - (v - min) / span) * innerH;
      return [x, y] as const;
    });
    const line = xy.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    const area =
      `${line} L${xy[xy.length - 1][0].toFixed(1)},${(height - pad).toFixed(1)} L${xy[0][0].toFixed(1)},${(height - pad).toFixed(1)} Z`;
    return { line, area, last: xy[xy.length - 1] };
  }, [series, width, height]);

  if (!geometry) {
    return <div style={{ width, height }} className="rounded bg-bg-primary/20" />;
  }

  const stroke = color || (series[series.length - 1] >= series[0] ? UP : DOWN);
  const gid = `spark-${stroke.replace(/[^a-z0-9]/gi, "")}`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} preserveAspectRatio="none" className="overflow-visible">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
          <stop offset="100%" stopColor={stroke} stopOpacity={0} />
        </linearGradient>
      </defs>
      {fill ? <path d={geometry.area} fill={`url(#${gid})`} /> : null}
      <path d={geometry.line} fill="none" stroke={stroke} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" />
      {endDot ? <circle cx={geometry.last[0]} cy={geometry.last[1]} r={1.7} fill={stroke} /> : null}
    </svg>
  );
}
