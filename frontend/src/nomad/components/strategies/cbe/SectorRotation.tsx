"use client";

/**
 * Sector-rotation strip — the macro layer above the stock-level RRG.
 *
 * The alpha engine tags every sector with an RRG quadrant and a
 * relative-strength % vs Nifty50. We render the sector universe as a
 * sorted horizontal RS ladder: each row is a sector, the bar grows from a
 * centred zero line (→ right = outperforming, ← left = lagging), coloured
 * by the sector's RRG quadrant. A compact, dense read of "where the money
 * is rotating" that pairs with the stock RrgScatter. Pure inline SVG.
 */
import { CHART } from "../shared/chartTheme";
import { QUADRANT_COLOR } from "./RrgScatter";

export type SectorRow = {
  code: string;
  quadrant: string;
  rs: number;        // relative strength % vs Nifty50
  count: number;     // # stocks scored in the sector
  leaders: number;   // # stocks in the leading quadrant
};

export function SectorRotation({ sectors }: { sectors: SectorRow[] }) {
  if (!sectors.length) {
    return (
      <div className="flex h-[180px] items-center justify-center rounded-xl border border-dashed border-bg-border/60 text-sm text-text-muted">
        Awaiting sector rotation…
      </div>
    );
  }

  const rows = [...sectors].sort((a, b) => b.rs - a.rs);
  const maxAbs = Math.max(0.5, ...rows.map((s) => Math.abs(s.rs)));

  return (
    <div className="space-y-1">
      {rows.map((s) => {
        const col = QUADRANT_COLOR[s.quadrant] || CHART.muted;
        const pct = Math.min(100, (Math.abs(s.rs) / maxAbs) * 100);
        const positive = s.rs >= 0;
        return (
          <div key={s.code} className="flex items-center gap-2 text-[11.5px]">
            <div className="w-32 shrink-0 truncate font-medium text-text-secondary" title={s.code}>
              {s.code.replace(/_/g, " ")}
            </div>
            {/* centred diverging bar */}
            <div className="relative h-4 flex-1 overflow-hidden rounded bg-bg-primary/20">
              <div className="absolute inset-y-0 left-1/2 w-px bg-bg-border" />
              <div
                className="absolute inset-y-0.5 rounded"
                style={{
                  background: col,
                  opacity: 0.55,
                  width: `${pct / 2}%`,
                  left: positive ? "50%" : undefined,
                  right: positive ? undefined : "50%",
                }}
              />
            </div>
            <div className="w-14 shrink-0 text-right font-mono" style={{ color: col }}>
              {positive ? "+" : ""}
              {s.rs.toFixed(2)}%
            </div>
            <div className="w-16 shrink-0 text-right text-[10px] uppercase tracking-[0.1em]" style={{ color: col }}>
              {s.quadrant}
            </div>
            <div className="w-12 shrink-0 text-right font-mono text-text-muted" title="leaders / scored">
              {s.leaders}/{s.count}
            </div>
          </div>
        );
      })}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-text-muted">
        <Legend color={QUADRANT_COLOR.leading} label="leading" />
        <Legend color={QUADRANT_COLOR.improving} label="improving" />
        <Legend color={QUADRANT_COLOR.weakening} label="weakening" />
        <Legend color={QUADRANT_COLOR.lagging} label="lagging" />
        <span>· bar = RS vs Nifty50 · last col = leaders/scored</span>
      </div>
    </div>
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
