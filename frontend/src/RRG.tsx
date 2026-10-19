import { useEffect, useMemo, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import type { RRGData, RRGPoint } from "./types";

// Relative rotation against NIFTY 50. Three ways to read the same numbers, because a 200-name scatter is not
// readable on its own: the rotation chart (tails first, then quadrants), a quadrant board laid out in the
// usual clockwise order of rotation, and a table ranked by who is heading most directly toward Leading.
const QUADRANT_COLORS: Record<string, string> = { leading: "#22c893", weakening: "#e5c85a", lagging: "#f15b6c", improving: "#4da3ff" };
const QUADRANT_LABELS: Record<string, string> = { leading: "Leading", weakening: "Weakening", lagging: "Lagging", improving: "Improving" };
const BOARD_ORDER = ["improving", "leading", "weakening", "lagging"] as const;
const TIMEFRAMES = [{ label: "30m", value: 1800 }, { label: "1h", value: 3600 }, { label: "1D", value: 86400 }];
const INDEX_LABELS: Record<string, string> = {
  "NSE:NIFTYIT-INDEX": "IT", "NSE:NIFTYAUTO-INDEX": "Auto", "NSE:NIFTYPHARMA-INDEX": "Pharma", "NSE:NIFTYFMCG-INDEX": "FMCG",
  "NSE:NIFTYMETAL-INDEX": "Metal", "NSE:NIFTYENERGY-INDEX": "Energy", "NSE:NIFTYREALTY-INDEX": "Realty", "NSE:FINNIFTY-INDEX": "Fin Nifty",
  "NSE:NIFTYNXT50-INDEX": "Next 50", "NSE:NIFTYPSUBANK-INDEX": "PSU Bank", "NSE:NIFTYPVTBANK-INDEX": "Pvt Bank", "NSE:NIFTYMEDIA-INDEX": "Media",
  "NSE:NIFTYINFRA-INDEX": "Infra", "NSE:NIFTYBANK-INDEX": "Bank Nifty", "NSE:MIDCPNIFTY-INDEX": "Midcap Select", "BSE:SENSEX-INDEX": "Sensex",
  "NSE:INDIAVIX-INDEX": "India VIX",
};
const SIZE = 640;
type Universe = "INDICES" | "GROUPS" | "STOCKS";
type View = "CHART" | "BOARD" | "TABLE";
type Row = { key: string; label: string; tail: RRGPoint[]; x: number; y: number; quadrant: string; group?: string;
  heading: number; toward: number; speed: number; held: number };

const ARROWS = ["→", "↗", "↑", "↖", "←", "↙", "↓", "↘"];
const arrow = (angle: number) => ARROWS[Math.round(((angle % 360) + 360) % 360 / 45) % 8];
const quadrantOf = (p: RRGPoint) => (p.x >= 100 ? (p.y >= 100 ? "leading" : "weakening") : (p.y >= 100 ? "improving" : "lagging"));

function describe(key: string, label: string, full: RRGPoint[], tailLength: number, quadrant: string, group?: string): Row {
  const tail = full.slice(-Math.max(2, tailLength));
  const head = tail[tail.length - 1];
  const prev = tail[tail.length - 2] || head;
  const dx = head.x - prev.x, dy = head.y - prev.y;
  const speed = Math.hypot(dx, dy);
  // Toward Leading: the last step's component along the up-and-right diagonal, so +ve means rotating toward strength.
  const toward = (dx + dy) / Math.SQRT2;
  let held = 0;
  // Counted over the full fetched history, not just the visible tail.
  for (let i = full.length - 1; i >= 0 && quadrantOf(full[i]) === quadrant; i--) held++;
  return { key, label, tail, x: head.x, y: head.y, quadrant, group, heading: (Math.atan2(dy, dx) * 180) / Math.PI, toward, speed, held };
}

export function RRGPage() {
  const [data, setData] = useState<RRGData>();
  const [error, setError] = useState("");
  const [universe, setUniverse] = useState<Universe>("INDICES");
  const [view, setView] = useState<View>("CHART");
  const [group, setGroup] = useState("");
  const [timeframe, setTimeframe] = useState(86400);
  const [tailLength, setTailLength] = useState(5);
  const [hover, setHover] = useState("");
  const [pinned, setPinned] = useState<string[]>([]);

  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    let stopped = false;
    setData(undefined); setError("");
    const load = () => {
      void fetch(`${API_URL}/api/rrg?timeframe_seconds=${timeframe}&tail=15`, { headers })
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

  const rows = useMemo<Row[]>(() => {
    if (!data) return [];
    if (universe === "INDICES") {
      return data.symbols.filter((row) => row.sector === "Indices" && row.symbol !== "NSE:NIFTY50-INDEX")
        .map((row) => describe(row.symbol, INDEX_LABELS[row.symbol] || row.symbol.split(":")[1].replace("-INDEX", ""), row.tail, tailLength, row.quadrant));
    }
    if (universe === "GROUPS") {
      return data.sectors.map((row) => describe(row.sector, `${row.sector} (${row.members})`, row.tail, tailLength, row.quadrant));
    }
    return data.symbols.filter((row) => row.sector !== "Indices" && (!group || row.sector === group))
      .map((row) => describe(row.symbol, row.symbol.split(":")[1].replace("-EQ", ""), row.tail, tailLength, row.quadrant, row.sector));
  }, [data, universe, group, tailLength]);

  const visible = pinned.length ? rows.filter((row) => pinned.includes(row.key)) : rows;
  const togglePin = (key: string) => setPinned((old) => (old.includes(key) ? old.filter((k) => k !== key) : [...old, key]));
  const counts = useMemo(() => {
    const totals: Record<string, number> = { leading: 0, weakening: 0, lagging: 0, improving: 0 };
    rows.forEach((row) => { totals[row.quadrant] = (totals[row.quadrant] || 0) + 1; });
    return totals;
  }, [rows]);

  // Square, symmetric scale around 100 so distance from the centre reads the same on both axes.
  const spread = useMemo(() => {
    const deviations = visible.flatMap((row) => row.tail.flatMap((p) => [Math.abs(p.x - 100), Math.abs(p.y - 100)]));
    return Math.max(1.5, ...deviations) * 1.12;
  }, [visible]);
  const fx = (x: number) => ((x - (100 - spread)) / (2 * spread)) * SIZE;
  const fy = (y: number) => SIZE - ((y - (100 - spread)) / (2 * spread)) * SIZE;
  // Few names: label all. Many: label the hovered, the pinned and the eight furthest from the centre.
  const labelled = useMemo(() => {
    if (visible.length <= 20) return new Set(visible.map((row) => row.key));
    const far = [...visible].sort((a, b) => Math.hypot(b.x - 100, b.y - 100) - Math.hypot(a.x - 100, a.y - 100)).slice(0, 8);
    return new Set([...far.map((row) => row.key), ...pinned, hover]);
  }, [visible, pinned, hover]);

  // Greedy label placement: furthest-out names first; a label that would overlap one already placed tries below
  // its dot, else waits for hover. Keeps the crowded centre legible.
  const placed = useMemo(() => {
    const boxes: { x: number; y: number; w: number }[] = [];
    const out = new Map<string, { x: number; y: number }>();
    const order = [...visible].filter((row) => labelled.has(row.key) || row.key === hover)
      .sort((a, b) => Number(b.key === hover) - Number(a.key === hover) || Math.hypot(b.x - 100, b.y - 100) - Math.hypot(a.x - 100, a.y - 100));
    for (const row of order) {
      const w = row.label.length * 6.2 + 4;
      const cx = fx(row.x), cy = fy(row.y);
      const spot = [{ x: cx + 9, y: cy - 7 }, { x: cx + 9, y: cy + 15 }, { x: cx - 9 - w, y: cy - 7 }, { x: cx - 9 - w, y: cy + 15 }]
        .find((p) => boxes.every((b) => p.x + w < b.x || b.x + b.w < p.x || Math.abs(p.y - b.y) > 12));
      if (spot || row.key === hover || pinned.includes(row.key)) {
        const at = spot || { x: cx + 9, y: cy - 7 };
        boxes.push({ ...at, w }); out.set(row.key, at);
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, labelled, hover, pinned, spread]);

  const chip = (quadrant: string) => <span className="rrg-chip" style={{ background: `${QUADRANT_COLORS[quadrant]}26`, color: QUADRANT_COLORS[quadrant] }}>{QUADRANT_LABELS[quadrant]}</span>;

  return <section className="ledger-page rrg-page">
    <div className="page-heading">
      <div><h1>Relative Rotation</h1><p>Against NIFTY 50 · RS-Ratio (x: relative trend) and RS-Momentum (y: its rate of change) · clockwise rotation: Improving → Leading → Weakening → Lagging · updated {data ? new Date(data.generated_at).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false }) : "—"}</p></div>
    </div>
    <div className="rrg-controls rrg-toolbar">
      <div className="radar-filter">{([["INDICES", "Sector indices"], ["GROUPS", "Sector groups"], ["STOCKS", "Stocks"]] as const).map(([key, label]) =>
        <button key={key} className={universe === key ? "active" : ""} onClick={() => { setUniverse(key); setPinned([]); if (key !== "STOCKS") setGroup(""); }}>{label}</button>)}</div>
      {universe === "STOCKS" && <select className="rrg-select" value={group} onChange={(event) => setGroup(event.target.value)}>
        <option value="">All sectors</option>
        {(data?.sectors || []).map((row) => <option key={row.sector} value={row.sector}>{row.sector} ({row.members})</option>)}
      </select>}
      <div className="radar-filter">{([["CHART", "Rotation chart"], ["BOARD", "Quadrant board"], ["TABLE", "Ranking"]] as const).map(([key, label]) =>
        <button key={key} className={view === key ? "active" : ""} onClick={() => setView(key)}>{label}</button>)}</div>
      <div className="radar-filter">{TIMEFRAMES.map((row) => <button key={row.value} className={timeframe === row.value ? "active" : ""} onClick={() => setTimeframe(row.value)}>{row.label}</button>)}</div>
      <label className="rrg-tail">Tail {tailLength}<input type="range" min={2} max={15} value={tailLength} onChange={(event) => setTailLength(Number(event.target.value))} /></label>
      {pinned.length > 0 && <button className="rrg-clear" onClick={() => setPinned([])}>Show all ({pinned.length} pinned)</button>}
    </div>
    <div className="rrg-legend rrg-legend-inline">{Object.entries(QUADRANT_LABELS).map(([key, label]) => <div key={key}><span className="swatch" style={{ background: QUADRANT_COLORS[key] }} />{label}<b>{counts[key] || 0}</b></div>)}</div>
    {!data && !error && <div className="panel chart-empty">Computing rotation from stored candles…</div>}
    {error && <div className="panel chart-empty error">{error}</div>}
    {data && !rows.length && <div className="panel chart-empty">Not enough stored history for this view at this timeframe yet (the sector indices are backfilled after start-up). Try 1h, or another universe.</div>}

    {data && view === "CHART" && <div className="rrg-layout">
      <div className="panel rrg-plot rrg-square">
        <svg viewBox={`0 0 ${SIZE} ${SIZE}`}>
          <rect x={SIZE / 2} y={0} width={SIZE / 2} height={SIZE / 2} fill="#22c89312" />
          <rect x={SIZE / 2} y={SIZE / 2} width={SIZE / 2} height={SIZE / 2} fill="#e5c85a0f" />
          <rect x={0} y={SIZE / 2} width={SIZE / 2} height={SIZE / 2} fill="#f15b6c12" />
          <rect x={0} y={0} width={SIZE / 2} height={SIZE / 2} fill="#4da3ff12" />
          <line x1={SIZE / 2} y1={0} x2={SIZE / 2} y2={SIZE} stroke="#33445c" />
          <line x1={0} y1={SIZE / 2} x2={SIZE} y2={SIZE / 2} stroke="#33445c" />
          <text x={SIZE - 10} y={20} textAnchor="end" className="rrg-quadrant-label" fill="#22c893">LEADING</text>
          <text x={SIZE - 10} y={SIZE - 10} textAnchor="end" className="rrg-quadrant-label" fill="#e5c85a">WEAKENING</text>
          <text x={10} y={SIZE - 10} className="rrg-quadrant-label" fill="#f15b6c">LAGGING</text>
          <text x={10} y={20} className="rrg-quadrant-label" fill="#4da3ff">IMPROVING</text>
          {visible.map((row) => {
            const color = QUADRANT_COLORS[row.quadrant] || "#8796aa";
            const active = hover === row.key || pinned.includes(row.key);
            const dim = hover && hover !== row.key;
            return <g key={row.key} opacity={dim ? 0.18 : 1} onMouseEnter={() => setHover(row.key)} onMouseLeave={() => setHover("")}
              onClick={() => togglePin(row.key)} style={{ cursor: "pointer" }}>
              <polyline points={row.tail.map((p) => `${fx(p.x)},${fy(p.y)}`).join(" ")} fill="none" stroke={color} strokeWidth={active ? 2.4 : 1.4} strokeOpacity={0.75} />
              {row.tail.slice(0, -1).map((p, index) => <circle key={index} cx={fx(p.x)} cy={fy(p.y)} r={2.2} fill={color} fillOpacity={0.35 + 0.5 * (index / row.tail.length)} />)}
              <circle cx={fx(row.x)} cy={fy(row.y)} r={active ? 7.5 : 5.5} fill={color} stroke="#0b1018" strokeWidth={1.5} />
              {placed.has(row.key) && <text x={placed.get(row.key)!.x} y={placed.get(row.key)!.y} className="rrg-label" fill={active ? "#f2f6fb" : "#b8c4d4"}>{row.label}</text>}
            </g>;
          })}
        </svg>
        <p className="rrg-hint">Tails show the last {tailLength} {timeframe === 86400 ? "days" : "bars"}; the big dot is now. Click a name to pin it, hover to isolate it.</p>
      </div>
      <aside className="panel rrg-side">
        <div className="panel-title">Heading toward Leading</div>
        <div className="rrg-list">{[...rows].sort((a, b) => b.toward - a.toward).map((row) =>
          <button key={row.key} className={hover === row.key || pinned.includes(row.key) ? "rrg-row active" : "rrg-row"}
            onMouseEnter={() => setHover(row.key)} onMouseLeave={() => setHover("")} onClick={() => togglePin(row.key)}>
            <span className="swatch" style={{ background: QUADRANT_COLORS[row.quadrant] }} />
            <span className="rrg-row-label">{row.label}</span>
            <span className="rrg-arrow" style={{ color: row.toward >= 0 ? "#22c893" : "#f15b6c" }}>{arrow(row.heading)}</span>
          </button>)}</div>
      </aside>
    </div>}

    {data && view === "BOARD" && <div className="rrg-board">{BOARD_ORDER.map((quadrant) => {
      const members = rows.filter((row) => row.quadrant === quadrant).sort((a, b) => b.toward - a.toward);
      return <div key={quadrant} className="panel rrg-column" style={{ borderTopColor: QUADRANT_COLORS[quadrant] }}>
        <div className="rrg-column-head" style={{ color: QUADRANT_COLORS[quadrant] }}>{QUADRANT_LABELS[quadrant]} <b>{members.length}</b></div>
        {members.map((row) => <div key={row.key} className="rrg-card" title={`RS-Ratio ${row.x.toFixed(2)} · RS-Momentum ${row.y.toFixed(2)}`}>
          <span className="rrg-card-name">{row.label}</span>
          <span className="rrg-arrow" style={{ color: row.toward >= 0 ? "#22c893" : "#f15b6c" }}>{arrow(row.heading)}</span>
          <small>{row.held} {timeframe === 86400 ? "d" : "bars"} here</small>
        </div>)}
      </div>;
    })}</div>}

    {data && view === "TABLE" && <div className="panel rrg-table-wrap"><table className="ledger-table">
      <thead><tr><th>Name</th>{universe === "STOCKS" && <th>Sector</th>}<th>Quadrant</th><th>RS-Ratio</th><th>RS-Momentum</th><th>Heading</th><th>Toward Leading</th><th>Speed</th><th>Time in quadrant</th></tr></thead>
      <tbody>{[...rows].sort((a, b) => b.toward - a.toward).map((row) => <tr key={row.key}>
        <td><b>{row.label}</b></td>{universe === "STOCKS" && <td>{row.group}</td>}<td>{chip(row.quadrant)}</td>
        <td>{row.x.toFixed(2)}</td><td>{row.y.toFixed(2)}</td>
        <td className="rrg-arrow" style={{ color: row.toward >= 0 ? "#22c893" : "#f15b6c" }}>{arrow(row.heading)}</td>
        <td style={{ color: row.toward >= 0 ? "#22c893" : "#f15b6c" }}>{row.toward >= 0 ? "+" : ""}{row.toward.toFixed(2)}</td>
        <td>{row.speed.toFixed(2)}</td><td>{row.held} {timeframe === 86400 ? "days" : "bars"}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
