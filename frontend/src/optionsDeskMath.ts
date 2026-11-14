// Pure helpers for the Options & flow desk's SVG charts (scales, ticks, nearest point, compact numbers).

export type Domain = [number, number];

/** A linear scale from a domain onto a pixel range; a flat domain maps to the middle of the range. */
export function linear(domain: Domain, range: Domain): (v: number) => number {
  const [d0, d1] = domain, [r0, r1] = range;
  if (d1 === d0) return () => (r0 + r1) / 2;
  return (v) => r0 + ((v - d0) / (d1 - d0)) * (r1 - r0);
}

/** Domain covering every finite value, padded by ``pad`` of its span (and never flat). */
export function extent(values: Iterable<number | null | undefined>, pad = 0.06, include?: number): Domain {
  let lo = Infinity, hi = -Infinity;
  for (const v of values) if (v != null && Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (include != null) { lo = Math.min(lo, include); hi = Math.max(hi, include); }
  if (!Number.isFinite(lo)) return [0, 1];
  if (hi === lo) { const w = Math.abs(lo) * 0.01 || 1; return [lo - w, hi + w]; }
  const span = hi - lo;
  return [lo - span * pad, hi + span * pad];
}

/** About ``count`` round tick values inside the domain (1, 2, 2.5, 5 x 10^n steps). */
export function ticks(domain: Domain, count = 4): number[] {
  const [lo, hi] = domain;
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / Math.max(1, count);
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? 10 * power;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** The session's 09:15 and 15:30 IST for the IST date ``day`` (YYYY-MM-DD), as epoch seconds. */
export function sessionDomain(day: string): Domain {
  const start = Date.parse(`${day}T09:15:00+05:30`) / 1000;
  return [start, start + 375 * 60];
}

/** Whole-hour IST tick marks (10:00 … 15:00) across a session domain that starts at 09:15 IST. */
export function sessionTicks(domain: Domain): number[] {
  const out: number[] = [];
  for (let t = domain[0] + 45 * 60; t <= domain[1]; t += 3600) out.push(t);
  return out;
}

/** Index of the item whose ``ts`` is nearest ``at`` (items sorted by ts); -1 when empty. */
export function nearestIndex<T extends { ts: number }>(items: T[], at: number): number {
  if (!items.length) return -1;
  let lo = 0, hi = items.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (items[mid].ts <= at) lo = mid; else hi = mid;
  }
  return Math.abs(items[lo].ts - at) <= Math.abs(items[hi].ts - at) ? lo : hi;
}

/** The last item at or before ``at``, else the first one. */
export function asOf<T extends { ts: number }>(items: T[], at: number): T | undefined {
  let found: T | undefined;
  for (const item of items) { if (item.ts <= at) found = item; else break; }
  return found ?? items[0];
}

/** Compact Indian-style quantity: 1.23 Cr, 4.56 L, 7,890; signed when asked. */
export function compact(v: number | null | undefined, signed = false): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "−" : signed && v > 0 ? "+" : "";
  const a = Math.abs(v);
  const body = a >= 1e7 ? `${(a / 1e7).toFixed(a >= 1e9 ? 0 : 2)} Cr` : a >= 1e5 ? `${(a / 1e5).toFixed(2)} L` : Math.round(a).toLocaleString("en-IN");
  return sign + body;
}

/** SVG path for a line through (x, y) points, breaking at nulls. */
export function linePath(points: [number, number | null][]): string {
  let d = "", pen = false;
  for (const [x, y] of points) {
    if (y == null || !Number.isFinite(y)) { pen = false; continue; }
    d += `${pen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    pen = true;
  }
  return d;
}

/** SVG path for the area between an upper and a lower line. */
export function bandPath(points: [number, number, number][]): string {
  if (!points.length) return "";
  const top = points.map(([x, u]) => `${x.toFixed(1)},${u.toFixed(1)}`);
  const bottom = [...points].reverse().map(([x, , l]) => `${x.toFixed(1)},${l.toFixed(1)}`);
  return `M${top.join("L")}L${bottom.join("L")}Z`;
}
