import { useEffect, useMemo, useState } from "react";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";

// Circular view of own vs NIFTY-relative returns. One wedge per name, strongest relative performer at 12 o'clock and
// clockwise from there. Bar length = own return over the chosen window (out from the zero ring when up, in when down);
// bar colour = return relative to NIFTY. The three thin outer rings are relative strength over 5, 20 and 60 sessions,
// so a sector green on all three has an undercurrent, and one green only on the inner ring is a fresh pop.
// Windows follow research/sector_persistence_20261017 (relative strength lasts ~2-4 weeks on daily bars).
type Changes = Record<string, number | null>;
type Row = { symbol: string; label: string; kind: "benchmark" | "broad" | "sector" | "stock"; group: string; price: number; as_of: string;
  own: Changes; relative: Changes; month_start?: { rank: number; of: number; relative: number } };
type Payload = { as_of: string; live: boolean; windows: string[]; benchmark_change: Changes; refreshing?: boolean;
  stale_symbols?: string[];
  month_start: { formed_on: string; sessions_since: number; typical_run: number; leaders: string[]; laggards: string[] } | null; rows: Row[] };

const SIZE = 660, C = SIZE / 2, R_ZERO = 150, R_OUT = 238, R_IN = 62;
const RINGS = [["5D", 248], ["20D", 260], ["60D", 272]] as const;
const RING_W = 10;
const REL_CAP: Record<string, number> = { "1D": 1.5, "5D": 4, "20D": 8, "60D": 14 };
const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);
const tint = (v: number | null | undefined, cap: number, alpha = 0.92) => {
  if (v === null || v === undefined) return "#283444";
  const k = 0.22 + 0.78 * Math.min(1, Math.abs(v) / cap);
  return v >= 0 ? `rgba(38, 190, 110, ${k * alpha})` : `rgba(235, 75, 85, ${k * alpha})`;
};
const point = (r: number, a: number) => [C + r * Math.sin(a), C - r * Math.cos(a)];
function wedge(r1: number, r2: number, a1: number, a2: number) {
  const [inner, outer] = r1 < r2 ? [r1, r2] : [r2, r1];
  const large = a2 - a1 > Math.PI ? 1 : 0;
  const [x1, y1] = point(outer, a1), [x2, y2] = point(outer, a2), [x3, y3] = point(inner, a2), [x4, y4] = point(inner, a1);
  return `M${x1},${y1} A${outer},${outer} 0 ${large} 1 ${x2},${y2} L${x3},${y3} A${inner},${inner} 0 ${large} 0 ${x4},${y4} Z`;
}

export function SectorWheel() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const [win, setWin] = useState("20D");
  const [universe, setUniverse] = useState<"SECTORS" | "STOCKS">("SECTORS");
  const [group, setGroup] = useState("");
  const [hover, setHover] = useState("");

  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const controller = new AbortController();
    const load = () => void requestJson<Payload>(`${API_URL}/api/sector-wheel`, { headers, signal: controller.signal })
      .then((payload) => { setData(payload); setError(""); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unavailable"); });
    load();
    const timer = setInterval(load, 60_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);

  const groups = useMemo(() => [...new Set((data?.rows || []).filter((r) => r.kind === "stock").map((r) => r.group))].sort(), [data]);
  const rows = useMemo(() => {
    const all = (data?.rows || []).filter((r) => r.relative[win] !== null && r.relative[win] !== undefined);
    const rel = (r: Row) => r.relative[win] as number;
    if (universe === "SECTORS") return all.filter((r) => r.kind === "sector" || r.kind === "broad").sort((a, b) => rel(b) - rel(a));
    const stocks = all.filter((r) => r.kind === "stock" && (!group || r.group === group)).sort((a, b) => rel(b) - rel(a));
    // A wheel stays legible to ~40 wedges: without a sector filter show the 18 leaders and 18 laggards.
    return group || stocks.length <= 40 ? stocks.slice(0, 48) : [...stocks.slice(0, 18), ...stocks.slice(-18)];
  }, [data, win, universe, group]);

  const scale = universe === "STOCKS" ? 2 : 1;
  // Scale to the 90th percentile of moves so one outlier (a -39% stock) does not flatten every other bar;
  // bars beyond it are clipped and get a white edge, and the table keeps the true value.
  const moves = rows.map((r) => Math.abs(r.own[win] ?? 0)).sort((a, b) => a - b);
  const ownMax = Math.max(0.5, moves[Math.floor((moves.length - 1) * 0.9)] || 0);
  const clipped = (v: number) => Math.abs(v) > ownMax;
  const radius = (raw: number) => {
    const v = Math.max(-ownMax, Math.min(ownMax, raw));
    return v >= 0 ? R_ZERO + (R_OUT - R_ZERO) * (v / ownMax) : R_ZERO - (R_ZERO - R_IN) * (-v / ownMax);
  };
  const step = rows.length ? (2 * Math.PI) / rows.length : 0;
  const gap = Math.min(0.02, step * 0.12);
  const focus = rows.find((r) => r.symbol === hover);
  const ms = data?.month_start;
  const msLabel = (symbol: string) => data?.rows.find((r) => r.symbol === symbol)?.label || symbol;

  return <section className="panel sector-wheel">
    <div className="heatmap-toolbar">
      <div className="risk-tabs" role="group" aria-label="Window">{(data?.windows || ["1D", "5D", "20D", "60D"]).map((key) =>
        <button key={key} aria-pressed={win === key} onClick={() => setWin(key)}>{key}</button>)}</div>
      <div className="risk-tabs" role="group" aria-label="Universe">{([["SECTORS", "Sectors & indices"], ["STOCKS", "Stocks"]] as const).map(([key, label]) =>
        <button key={key} aria-pressed={universe === key} onClick={() => setUniverse(key)}>{label}</button>)}</div>
      {universe === "STOCKS" && <select className="heatmap-search" value={group} onChange={(event) => setGroup(event.target.value)} aria-label="Sector group">
        <option value="">Leaders &amp; laggards</option>{groups.map((g) => <option key={g} value={g}>{g}</option>)}</select>}
    </div>
    <div className="heatmap-meta">{data
      ? <>Closes to {new Date(data.as_of).toLocaleDateString("en-IN", { dateStyle: "medium" })}{data.live ? " (live price for today)" : ""} · NIFTY 50 {win} {pct(data.benchmark_change[win], 2)} · bar = own return (white edge = clipped outlier), colour = vs NIFTY, outer rings = vs NIFTY over 5 / 20 / 60 sessions{data.refreshing ? " · refreshing daily closes…" : ""}</>
      : error || "Loading…"}</div>
    {!!data?.stale_symbols?.length && <div role="status">Daily closes missing for {data.stale_symbols.length} names; unavailable comparisons are omitted.</div>}
    {data && !rows.length && <div className="chart-empty">No daily closes stored yet{data.refreshing ? " — the first download is running (about a minute)" : ""}.</div>}
    {rows.length > 0 && <div className="wheel-layout">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="wheel-svg">
        {RINGS.map(([key, r]) => <text key={key} x={C + 3} y={C - r - 1} className="wheel-ring-label">{key}</text>)}
        {rows.map((row, i) => {
          const a1 = i * step + gap, a2 = (i + 1) * step - gap, mid = (a1 + a2) / 2;
          const own = row.own[win] ?? 0;
          const dim = hover && hover !== row.symbol;
          const [lx, ly] = point(RINGS[2][1] + RING_W + 8, mid);
          const deg = (mid * 180) / Math.PI;
          const flip = deg > 180;
          const star = ms && universe === "SECTORS" && ms.leaders.includes(row.symbol) ? "★ " : "";
          return <g key={row.symbol} opacity={dim ? 0.3 : 1} onMouseEnter={() => setHover(row.symbol)} onMouseLeave={() => setHover("")} style={{ cursor: "default" }}>
            <path d={wedge(R_ZERO, radius(own), a1, a2)} fill={tint(row.relative[win], REL_CAP[win] * scale)}
              stroke={clipped(own) ? "#f2f6fb" : "#0b1018"} strokeWidth={clipped(own) ? 1.4 : 0.6} />
            {RINGS.map(([key, r]) => <path key={key} d={wedge(r, r + RING_W, a1, a2)} fill={tint(row.relative[key], REL_CAP[key] * scale)} />)}
            <text x={lx} y={ly} className="wheel-label" textAnchor={flip ? "end" : "start"} dominantBaseline="middle"
              transform={`rotate(${flip ? deg + 90 : deg - 90} ${lx} ${ly})`}>{star}{row.label}</text>
          </g>;
        })}
        <circle cx={C} cy={C} r={R_ZERO} fill="none" stroke="#6b7c93" strokeDasharray="3 3" />
        <circle cx={C} cy={C} r={R_IN - 6} fill="#0d141f" stroke="#26334a" />
        {focus ? <>
          <text x={C} y={C - 22} className="wheel-center-name" textAnchor="middle">{focus.label}</text>
          <text x={C} y={C + 2} className="wheel-center-value" textAnchor="middle">{pct(focus.own[win])}</text>
          <text x={C} y={C + 22} className="wheel-center-sub" textAnchor="middle">vs NIFTY {pct(focus.relative[win])}</text>
        </> : <>
          <text x={C} y={C - 14} className="wheel-center-sub" textAnchor="middle">NIFTY 50 · {win}</text>
          <text x={C} y={C + 12} className="wheel-center-value" textAnchor="middle">{pct(data?.benchmark_change[win] ?? null, 2)}</text>
        </>}
      </svg>
      <aside className="wheel-side">
        {ms && universe === "SECTORS" && <div className="wheel-note">
          <b>Month-start leaders</b> (best vs NIFTY over this month's first 3 sessions, formed {new Date(ms.formed_on).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}):
          {" "}{ms.leaders.map(msLabel).join(", ")} · laggards {ms.laggards.map(msLabel).join(", ")}.
          {" "}Session {ms.sessions_since} of a typical ~{ms.typical_run}-session run; after that the edge has historically faded.
        </div>}
        <table className="ledger-table wheel-table"><thead><tr><th>Name</th><th>Own {win}</th><th>vs NIFTY</th><th>5D</th><th>20D</th><th>60D</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.symbol} className={hover === row.symbol ? "active" : ""} onMouseEnter={() => setHover(row.symbol)} onMouseLeave={() => setHover("")}>
            <td><b>{row.label}</b>{universe === "STOCKS" && <small> {row.group}</small>}</td>
            <td>{pct(row.own[win])}</td>
            <td style={{ color: (row.relative[win] ?? 0) >= 0 ? "#22c893" : "#f15b6c" }}>{pct(row.relative[win])}</td>
            {(["5D", "20D", "60D"] as const).map((key) => <td key={key}><span className="wheel-chip" style={{ background: tint(row.relative[key], REL_CAP[key] * scale) }}>{pct(row.relative[key], 1)}</span></td>)}
          </tr>)}</tbody></table>
      </aside>
    </div>}
  </section>;
}
