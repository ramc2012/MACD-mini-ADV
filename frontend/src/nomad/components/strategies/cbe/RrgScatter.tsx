"use client";

/**
 * CBE-desk RRG — thin domain adapter over the shared quadrant scatter
 * (strategies/shared/RrgScatter). Keeps the alpha-engine point type and
 * re-exports QUADRANT_COLOR for SectorRotation; all rendering lives in
 * the shared component.
 */
import { RrgScatter as SharedRrgScatter, type RrgScatterPoint } from "../shared/RrgScatter";

export { QUADRANT_COLOR } from "../shared/RrgScatter";

export type RrgPoint = {
  symbol: string;
  rs: number;        // x — relative strength %
  momentum: number;  // y — momentum proxy (MACD hist)
  score: number;     // bubble size — composite alpha 0..100
  quadrant: string;  // colour
  watchlist?: boolean;
};

export function RrgScatter({ points }: { points: RrgPoint[] }) {
  const mapped: RrgScatterPoint[] = points.map((p, i) => ({
    key: `${p.symbol}-${i}`,
    label: p.symbol,
    x: p.rs,
    y: p.momentum,
    size: p.score,
    quadrant: p.quadrant,
    ring: p.watchlist,
    hoverLines: [
      `RS ${p.rs.toFixed(2)}% · mom ${p.momentum.toFixed(3)}`,
      `alpha ${p.score.toFixed(1)}`,
    ],
  }));
  return (
    <SharedRrgScatter
      points={mapped}
      labelPolicy="highlight"
      vbHeight={520}
      minDomain={{ x: 4, y: 0.4 }}
      xCaption="Relative strength vs Nifty50 (%) →"
      yCaption="↑ Momentum (MACD histogram)"
      sizeCaption="bubble size = composite alpha · ring = on watchlist"
      emptyText="Awaiting RRG candidates…"
    />
  );
}
