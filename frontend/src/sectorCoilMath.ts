// Geometry for the Sector coil (Market breadth → Sector coil), after Barron's "The Big Reversal" chart.
// A sector's line starts at 0 % on the base date and runs along one row to its return at the first checkpoint, then
// drops to the next row and runs on to the next checkpoint, and so on. Where the direction flips, the drop is a
// half-circle turn (the template's U-turn). Where it keeps going the same way, the drop is a small loop, so the line
// stays one continuous coil.

export type CoilRow = { symbol: string; label: string; kind: "benchmark" | "broad" | "sector"; values: (number | null)[]; price?: number };

/** Nulls (a missing close) take the previous value so the coil never breaks. */
export function filled(values: (number | null)[]): number[] {
  let last = 0;
  return values.map((v) => (v === null || v === undefined ? last : (last = v)));
}

/** A round tick step and padded domain that always include zero. */
export function axis(values: number[], maxTicks = 8): { lo: number; hi: number; step: number; ticks: number[] } {
  const lo0 = Math.min(0, ...values), hi0 = Math.max(0, ...values);
  const span = Math.max(1, hi0 - lo0);
  const step = [1, 2, 2.5, 5, 10, 20, 25, 50, 100].find((s) => span / s <= maxTicks) ?? 200;
  const lo = Math.floor(lo0 / step - 0.15) * step, hi = Math.ceil(hi0 / step + 0.15) * step;
  const ticks: number[] = [];
  for (let t = lo; t <= hi + 1e-9; t += step) ticks.push(Math.round(t * 100) / 100);
  return { lo, hi, step, ticks };
}

const sign = (v: number) => (v > 0 ? 1 : v < 0 ? -1 : 0);

/** Direction of travel on each row (1 right, -1 left); a flat row keeps the previous direction. */
export function directions(xs: number[]): number[] {
  const out: number[] = [];
  for (let k = 1; k < xs.length; k++) out.push(sign(xs[k] - xs[k - 1]) || out[out.length - 1] || 1);
  return out;
}

/** SVG path for one coil. xs[0] is the base (0 %), xs[k] the k-th checkpoint; row k-1 carries the move into xs[k]. */
export function coilPath(xs: number[], top: number, rowH: number): string {
  const dirs = directions(xs);
  const r = rowH / 2, c = rowH * 0.9;
  let d = `M${xs[0].toFixed(1)},${top.toFixed(1)}`;
  for (let k = 1; k < xs.length; k++) {
    const y = top + (k - 1) * rowH, x = xs[k];
    d += ` L${x.toFixed(1)},${y.toFixed(1)}`;
    if (k === xs.length - 1) break;
    const dir = dirs[k - 1], next = dirs[k], y2 = y + rowH;
    d += next !== dir
      ? ` A${r.toFixed(2)},${r.toFixed(2)} 0 0 ${dir > 0 ? 1 : 0} ${x.toFixed(1)},${y2.toFixed(1)}`
      : ` C${(x + dir * c).toFixed(1)},${y.toFixed(1)} ${(x - dir * c).toFixed(1)},${y2.toFixed(1)} ${x.toFixed(1)},${y2.toFixed(1)}`;
  }
  return d;
}

/** The arrowhead at the coil's end: tip, then the two back corners. */
export function arrowHead(xs: number[], top: number, rowH: number, size = 4.5): string {
  const dirs = directions(xs);
  const dir = dirs[dirs.length - 1] ?? 1;
  const x = xs[xs.length - 1], y = top + (xs.length - 2) * rowH;
  return `M${(x + dir * size).toFixed(1)},${y.toFixed(1)} L${(x - dir * size * 0.6).toFixed(1)},${(y - size * 0.75).toFixed(1)} L${(x - dir * size * 0.6).toFixed(1)},${(y + size * 0.75).toFixed(1)} Z`;
}

/** Row height that keeps a coil readable: tall for two turns, tight for weekly coils. */
export const rowHeight = (segments: number) => Math.max(2.6, Math.min(11, 52 / Math.max(1, segments)));

export type Story = { title: string; text: string; giver?: string; gainer?: string };

/** One-line reading of the chart: the biggest give-back from a positive peak and the biggest gain over the last step. */
export function story(rows: CoilRow[], dates: string[], fmtDay: (d: string) => string, unit = "%"): Story {
  // dates[0] is the base day; values[i] belongs to dates[i].
  const sectors = rows.filter((r) => r.kind !== "benchmark").map((r) => ({ r, v: filled(r.values) }));
  if (!sectors.length || dates.length < 3) return { title: "Sector progress", text: "" };
  const n = dates.length - 1;
  let giver: { label: string; peak: number; at: number; now: number; drop: number } | null = null;
  let gainer: { label: string; gain: number; now: number } | null = null;
  for (const { r, v } of sectors) {
    let at = 1;
    for (let i = 1; i < n; i++) if (v[i] > v[at]) at = i;
    const drop = v[at] - v[n];
    if (v[at] > 0 && (!giver || drop > giver.drop)) giver = { label: r.label, peak: v[at], at, now: v[n], drop };
    const gain = v[n] - v[n - 1];
    if (!gainer || gain > gainer.gain) gainer = { label: r.label, gain, now: v[n] };
  }
  const pct = (x: number) => `${x > 0 ? "+" : ""}${x.toFixed(1)}${unit}`;
  const parts: string[] = [];
  if (giver && giver.drop >= 1) parts.push(`On ${fmtDay(dates[giver.at])} ${giver.label} was ${pct(giver.peak)} since ${fmtDay(dates[0])}; it is ${pct(giver.now)} now.`);
  if (gainer && gainer.gain > 0) parts.push(`${gainer.label} made up the most ground since ${fmtDay(dates[n - 1])}: ${pct(gainer.gain).replace(unit, " pts")}, to ${pct(gainer.now)}.`);
  const title = giver && giver.drop >= 5 && gainer && gainer.gain >= 3 ? "The big reversal" : giver && giver.drop >= 5 ? "Leaders giving back" : "Sector progress";
  return { title, text: parts.join(" "), giver: giver?.label, gainer: gainer?.label };
}
