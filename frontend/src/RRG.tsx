import { useEffect, useMemo, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import type { RRGData, RRGPoint } from "./types";

const QUADRANT_COLORS: Record<string, string> = { leading: "#22c893", weakening: "#e5c85a", lagging: "#f15b6c", improving: "#4da3ff" };
const QUADRANT_LABELS: Record<string, string> = { leading: "Leading", weakening: "Weakening", lagging: "Lagging", improving: "Improving" };
const TIMEFRAMES = [{ label: "15m", value: 900 }, { label: "30m", value: 1800 }, { label: "1h", value: 3600 }, { label: "1D", value: 86400 }];
const WIDTH = 1000;
const HEIGHT = 620;

type PlotRow = { key: string; label: string; sector?: string; members?: number; tail: RRGPoint[]; x: number; y: number; quadrant: string };

export function RRGPage() {
  const [data, setData] = useState<RRGData>();
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"SECTORS" | "STOCKS">("SECTORS");
  const [sectorFilter, setSectorFilter] = useState("");
  const [timeframe, setTimeframe] = useState(1800);
  const [hover, setHover] = useState("");

  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    let stopped = false;
    setData(undefined); setError("");
    const load = () => {
      void fetch(`${API_URL}/api/rrg?timeframe_seconds=${timeframe}`, { headers })
        .then(async (response) => {
          if (!response.ok) throw new Error((await response.json()).detail || "RRG request failed");
          return response.json();
        })
        .then((value) => { if (!stopped) setData(value as RRGData); })
        .catch((reason) => { if (!stopped) setError(reason instanceof Error ? reason.message : "RRG request failed"); });
    };
    load();
    const timer = window.setInterval(load, 60_000);
    return () => { stopped = true; clearInterval(timer); };
  }, [timeframe]);

  const rows = useMemo<PlotRow[]>(() => {
    if (!data) return [];
    if (mode === "SECTORS") {
      return data.sectors.map((row) => ({ key: row.sector, label: row.sector, members: row.members, tail: row.tail, x: row.x, y: row.y, quadrant: row.quadrant }));
    }
    return data.symbols
      .filter((row) => row.sector !== "Indices")
      .filter((row) => !sectorFilter || row.sector === sectorFilter)
      .map((row) => ({ key: row.symbol, label: row.symbol.split(":")[1].replace("-EQ", ""), sector: row.sector, tail: row.tail, x: row.x, y: row.y, quadrant: row.quadrant }));
  }, [data, mode, sectorFilter]);

  const spread = useMemo(() => {
    const deviations = rows.flatMap((row) => row.tail.flatMap((point) => [Math.abs(point.x - 100), Math.abs(point.y - 100)]));
    return Math.max(2.5, ...deviations) * 1.15;
  }, [rows]);
  const fx = (x: number) => ((x - (100 - spread)) / (2 * spread)) * WIDTH;
  const fy = (y: number) => HEIGHT - ((y - (100 - spread)) / (2 * spread)) * HEIGHT;

  const counts = useMemo(() => {
    const totals: Record<string, number> = { leading: 0, weakening: 0, lagging: 0, improving: 0 };
    rows.forEach((row) => { totals[row.quadrant] = (totals[row.quadrant] || 0) + 1; });
    return totals;
  }, [rows]);

  return <section className="ledger-page rrg-page">
    <div className="page-heading">
      <div><h1>Relative Rotation Graph</h1><p>RS-Ratio vs RS-Momentum against {data?.benchmark.split(":")[1] || "NIFTY 50"} · {mode === "SECTORS" ? "sector averages" : sectorFilter || "all F&O stocks"} · updated {data ? new Date(data.generated_at).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false }) : "—"}</p></div>
      <div className="rrg-controls">
        <div className="radar-filter">{(["SECTORS", "STOCKS"] as const).map((key) => <button key={key} className={mode === key ? "active" : ""} onClick={() => { setMode(key); if (key === "SECTORS") setSectorFilter(""); }}>{key}</button>)}</div>
        {mode === "STOCKS" && <select className="rrg-select" value={sectorFilter} onChange={(event) => setSectorFilter(event.target.value)}>
          <option value="">All sectors</option>
          {(data?.sectors || []).map((row) => <option key={row.sector} value={row.sector}>{row.sector} ({row.members})</option>)}
        </select>}
        <div className="radar-filter">{TIMEFRAMES.map((row) => <button key={row.value} className={timeframe === row.value ? "active" : ""} onClick={() => setTimeframe(row.value)}>{row.label}</button>)}</div>
      </div>
    </div>
    <div className="rrg-layout">
      <div className="panel rrg-plot">
        {!data && !error && <div className="chart-empty">Computing rotation from stored candles…</div>}
        {error && <div className="chart-empty error">{error}</div>}
        {data && <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none">
          <rect x={WIDTH / 2} y={0} width={WIDTH / 2} height={HEIGHT / 2} fill="#22c89310" />
          <rect x={WIDTH / 2} y={HEIGHT / 2} width={WIDTH / 2} height={HEIGHT / 2} fill="#e5c85a0d" />
          <rect x={0} y={HEIGHT / 2} width={WIDTH / 2} height={HEIGHT / 2} fill="#f15b6c10" />
          <rect x={0} y={0} width={WIDTH / 2} height={HEIGHT / 2} fill="#4da3ff10" />
          <line x1={WIDTH / 2} y1={0} x2={WIDTH / 2} y2={HEIGHT} stroke="#263144" />
          <line x1={0} y1={HEIGHT / 2} x2={WIDTH} y2={HEIGHT / 2} stroke="#263144" />
          <text x={WIDTH - 12} y={20} textAnchor="end" className="rrg-quadrant-label" fill="#22c893">LEADING</text>
          <text x={WIDTH - 12} y={HEIGHT - 10} textAnchor="end" className="rrg-quadrant-label" fill="#e5c85a">WEAKENING</text>
          <text x={12} y={HEIGHT - 10} className="rrg-quadrant-label" fill="#f15b6c">LAGGING</text>
          <text x={12} y={20} className="rrg-quadrant-label" fill="#4da3ff">IMPROVING</text>
          {rows.map((row) => {
            const color = QUADRANT_COLORS[row.quadrant] || "#8796aa";
            const active = hover === row.key;
            const points = row.tail.map((point) => `${fx(point.x)},${fy(point.y)}`).join(" ");
            return <g key={row.key} opacity={hover && !active ? 0.25 : 1} onMouseEnter={() => setHover(row.key)} onMouseLeave={() => setHover("")} style={{ cursor: mode === "SECTORS" ? "pointer" : "default" }}
              onClick={() => { if (mode === "SECTORS") { setMode("STOCKS"); setSectorFilter(row.key); } }}>
              <polyline points={points} fill="none" stroke={color} strokeWidth={active ? 2 : 1.1} strokeOpacity={0.65} />
              {row.tail.slice(0, -1).map((point, index) => <circle key={index} cx={fx(point.x)} cy={fy(point.y)} r={2} fill={color} fillOpacity={0.5} />)}
              <circle cx={fx(row.x)} cy={fy(row.y)} r={active ? 7 : 5} fill={color} stroke="#0b1018" strokeWidth={1.5} />
              <text x={fx(row.x) + 8} y={fy(row.y) - 6} className="rrg-label" fill={active ? "#dce5f1" : "#8796aa"}>{row.label}{row.members ? ` (${row.members})` : ""}</text>
            </g>;
          })}
        </svg>}
      </div>
      <aside className="panel rrg-side">
        <div className="panel-title">Quadrants <span>{rows.length}</span></div>
        <div className="rrg-legend">{Object.entries(QUADRANT_LABELS).map(([key, label]) => <div key={key}><span className="swatch" style={{ background: QUADRANT_COLORS[key] }} />{label}<b>{counts[key] || 0}</b></div>)}</div>
        <div className="rrg-list">
          {[...rows].sort((a, b) => (b.x + b.y) - (a.x + a.y)).map((row) => <button key={row.key} className={hover === row.key ? "rrg-row active" : "rrg-row"}
            onMouseEnter={() => setHover(row.key)} onMouseLeave={() => setHover("")}
            onClick={() => { if (mode === "SECTORS") { setMode("STOCKS"); setSectorFilter(row.key); } }}>
            <span className="swatch" style={{ background: QUADRANT_COLORS[row.quadrant] }} />
            <span className="rrg-row-label">{row.label}{row.members ? ` · ${row.members}` : ""}</span>
            <small>{row.x.toFixed(1)} / {row.y.toFixed(1)}</small>
          </button>)}
        </div>
      </aside>
    </div>
  </section>;
}
