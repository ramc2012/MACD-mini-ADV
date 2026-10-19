import { useEffect, useMemo, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import { requestJson } from "./requestJson";

type State = "up" | "down" | "fading";
type Kind = "CE" | "PE" | "SPOT";
type Point = { x: number; y: number; timestamp: number; state: State };
type Row = { symbol: string; label: string; group: string; spot_symbol: string; price: number; state: State;
  x: number; y: number; held: number; as_of: number; price_at: number; tail: Point[]; strike?: number; expiry?: string };
type Payload = { kind: Kind; generated_at: string; rows: Row[];
  unavailable: { symbol: string; reason: string }[]; evaluated: number };
const COLORS: Record<State, string> = { up: "#26cf99", down: "#fa7785", fading: "#b7a0d5" };
const LABELS: Record<State, string> = { up: "Trending up", down: "Trending down", fading: "Fading" };
const SIZE = 640, PAD = 45, MID = SIZE / 2;
const stamp = (seconds: number) => new Date(seconds * 1000).toLocaleString("en-IN", {
  timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });

export function TrendRotation() {
  const [kind, setKind] = useState<Kind>("CE");
  const [timeframe, setTimeframe] = useState(1800);
  const [data, setData] = useState<Payload>();
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const [filter, setFilter] = useState("all");
  const [tailLength, setTailLength] = useState(5);
  const [selected, setSelected] = useState("");
  const [hover, setHover] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    setData(undefined); setSelected(""); setFilter("all"); setError(""); setLoading(true);
    const load = () => void requestJson<Payload>(`${API_URL}/api/trend-rotation?kind=${kind}&timeframe_seconds=${timeframe}`,
      { headers, signal: controller.signal }).then((value) => {
        if (!controller.signal.aborted) { setData(value); setError(""); setLoading(false); }
      }).catch((reason) => { if (!controller.signal.aborted) { setError(reason.message || "Unable to load trend rotation"); setLoading(false); } });
    load();
    const timer = setInterval(load, 30_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [kind, timeframe]);
  const states: State[] = kind === "SPOT" ? ["up", "down", "fading"] : ["up", "fading"];
  const groups = useMemo(() => [...new Set((data?.rows || []).map((r) => r.group))].sort(), [data]);
  const all = useMemo(() => (data?.rows || []).filter((r) =>
    (!group || r.group === group) && `${r.label} ${r.symbol}`.toLowerCase().includes(query.toLowerCase())), [data, group, query]);
  const rows = useMemo(() => all.filter((r) => filter === "all" || r.state === filter)
    .sort((a,b) => Number(b.state !== "fading") - Number(a.state !== "fading") || Math.abs(b.x) - Math.abs(a.x)), [all, filter]);
  const focus = rows.find((r) => r.symbol === selected) || rows.find((r) => r.symbol === hover);
  // Normalize each axis independently and retain every point; labels are deliberately sparse.
  const spreadX = Math.max(2, ...rows.flatMap((r) => r.tail.slice(-tailLength).map((p) => Math.abs(p.x)))) * 1.15;
  const spreadY = Math.max(.5, ...rows.flatMap((r) => r.tail.slice(-tailLength).map((p) => Math.abs(p.y)))) * 1.15;
  const fx = (v: number) => MID + v / spreadX * (MID-PAD);
  const fy = (v: number) => MID - v / spreadY * (MID-PAD);
  const labels = new Map<string, { x: number; y: number }>();
  const boxes: {x: number; y: number; width: number}[] = [];
  const labelRows = [...rows].sort((a,b) => Number(b.symbol === focus?.symbol) - Number(a.symbol === focus?.symbol));
  for (const row of labelRows.slice(0, rows.length <= 16 ? 16 : 8)) {
    const width = Math.min(150, row.label.length * 6.5), cx = fx(row.x), cy = fy(row.y);
    const at = [{x:cx+8,y:cy-8}, {x:cx+8,y:cy+17}, {x:cx-width-8,y:cy-8}, {x:cx-width-8,y:cy+17}]
      .find((p) => p.x >= PAD && p.x+width < SIZE-PAD && p.y > PAD && p.y < SIZE-PAD &&
        boxes.every((b) => p.x+width < b.x || b.x+b.width < p.x || Math.abs(p.y-b.y)>14));
    if (at) { labels.set(row.symbol, at); boxes.push({...at,width}); }
  }
  const counts = (s: State) => all.filter((r) => r.state === s).length;
  return <section className="ledger-page trend-page">
    <div className="page-heading"><div><h1>Trend rotation</h1><p>{kind === "SPOT" ? "Price direction and fading trends" : "Follow the premium, on either side of the market"} · own price, completed candles</p></div></div>
    <div className="rrg-toolbar">
      <div className="radar-filter" aria-label="Instrument">{([["CE", "ATM calls"], ["PE", "ATM puts"], ["SPOT", "Spot"]] as const).map(([k, label]) =>
        <button key={k} className={kind === k ? "active" : ""} onClick={() => { setKind(k); setGroup(""); }}>{label}</button>)}</div>
      <div className="radar-filter" aria-label="Candle interval">{[[1800,"30m"], [3600,"1h"], [86400,"1D"]].map(([value,label]) =>
        <button key={value} className={timeframe === value ? "active" : ""} onClick={() => setTimeframe(Number(value))}>{label}</button>)}</div>
      <input aria-label="Find instrument" className="heatmap-search" placeholder="Find instrument or contract" value={query} onChange={(e) => setQuery(e.target.value)} />
      <select aria-label="Sector" className="rrg-select" value={group} onChange={(e) => setGroup(e.target.value)}><option value="">All sectors & indices</option>{groups.map((g) => <option key={g}>{g}</option>)}</select>
      <label className="rrg-tail">Tail {tailLength}<input aria-label="Trend tail length" type="range" min={2} max={8} value={tailLength} onChange={(e) => setTailLength(Number(e.target.value))} /></label>
    </div>
    <div className="trend-state-filters"><button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>All <b>{all.length}</b></button>{states.map((s) =>
      <button key={s} className={filter === s ? "active" : ""} style={{color:COLORS[s]}} onClick={() => setFilter(filter === s ? "all" : s)}><span className="swatch" style={{background:COLORS[s]}} />{LABELS[s]} <b>{counts(s)}</b></button>)}</div>
    {loading && <p role="status">Reading completed candles…</p>}
    {error && <p role="alert" className="error">{error}{data ? " · Showing the last successful response." : " · Retrying automatically."}</p>}
    {data && <p className="trend-asof">{rows.length} instruments shown · latest completed closes {rows.length ? stamp(Math.max(...rows.map((r) => r.as_of))) : "unavailable"} IST · {data.unavailable.length} unavailable across all names</p>}
    <div className="trend-layout">
      <div className="panel trend-plot"><svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label={`${kind} price trend rotation chart`}>
        <rect x={PAD} y={PAD} width={SIZE-2*PAD} height={SIZE-2*PAD} fill="#b7a0d508" />
        <rect x={MID} y={PAD} width={MID-PAD} height={MID-PAD} fill="#26cf9918" />
        {kind === "SPOT" && <rect x={PAD} y={MID} width={MID-PAD} height={MID-PAD} fill="#fa778518" />}
        <line x1={MID} y1={PAD} x2={MID} y2={SIZE-PAD} stroke="#44516a" /><line x1={PAD} y1={MID} x2={SIZE-PAD} y2={MID} stroke="#44516a" />
        <text x={SIZE-PAD-8} y={PAD+20} textAnchor="end" fill={COLORS.up} className="trend-zone">TRENDING UP</text>
        <text x={PAD+8} y={PAD+20} fill={COLORS.fading} className="trend-zone">FADING</text>
        <text x={SIZE-PAD-8} y={SIZE-PAD-10} textAnchor="end" fill={COLORS.fading} className="trend-zone">FADING</text>
        <text x={PAD+8} y={SIZE-PAD-10} fill={kind === "SPOT" ? COLORS.down : COLORS.fading} className="trend-zone">{kind === "SPOT" ? "TRENDING DOWN" : "FADING"}</text>
        <text x={MID} y={SIZE-10} textAnchor="middle" className="trend-axis">← Below longer trend · Above longer trend →</text>
        <text transform={`translate(16 ${MID}) rotate(-90)`} textAnchor="middle" className="trend-axis">← Falling momentum · Rising momentum →</text>
        {[-1,1].map((sign) => <g key={sign}><text x={fx(sign*spreadX*.7)} y={MID+15} className="trend-tick">{(sign*spreadX*.7).toFixed(1)}</text><text x={MID+5} y={fy(sign*spreadY*.7)} className="trend-tick">{(sign*spreadY*.7).toFixed(1)}</text></g>)}
        {rows.map((r) => {
          const active = r.symbol === focus?.symbol, dim = !!focus && !active;
          const path = r.tail.slice(-tailLength);
          return <g key={r.symbol} opacity={dim ? .12 : 1} onMouseEnter={() => setHover(r.symbol)} onMouseLeave={() => setHover("")}>
            <title>{r.label} · {LABELS[r.state]} · {r.symbol} · {stamp(r.as_of)} IST</title>
            <polyline points={path.map((p) => `${fx(p.x)},${fy(p.y)}`).join(" ")} fill="none" stroke={COLORS[r.state]} strokeWidth={active ? 2.5 : 1} opacity={active ? 1 : .4} />
            {path.slice(0,-1).map((p,i) => <circle key={i} cx={fx(p.x)} cy={fy(p.y)} r={1.8} fill={COLORS[p.state]} />)}
            <circle role="button" aria-label={`Focus ${r.label}`} tabIndex={0} cx={fx(r.x)} cy={fy(r.y)} r={active ? 7 : 4.5} fill={COLORS[r.state]} stroke="#101722" onClick={() => setSelected(selected === r.symbol ? "" : r.symbol)} onKeyDown={(e) => {if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(selected === r.symbol ? "" : r.symbol); }}} />
            {labels.has(r.symbol) && <text x={labels.get(r.symbol)!.x} y={labels.get(r.symbol)!.y} className="rrg-label" fill="#d2dcea">{r.label}</text>}
          </g>;
        })}
      </svg><p className="rrg-hint">Each dot is one exact instrument. Tails show its last {tailLength} completed bars; larger dot is the latest. Hover or select a row to isolate a name.</p>
      {!loading && !rows.length && <p className="chart-empty">No instruments match these filters with current, sufficient history.</p>}</div>
      <aside className="panel trend-list"><div className="trend-detail">{focus ? <><strong style={{color:COLORS[focus.state]}}>{focus.label} · {LABELS[focus.state]}</strong><small>{focus.symbol}</small><span>₹{focus.price.toLocaleString("en-IN")} · {focus.held} bars in state</span><small>Completed close {stamp(focus.as_of)} IST · last stored price {stamp(focus.price_at)} IST{focus.expiry ? ` · expiry ${focus.expiry}` : ""}</small><button className="rrg-clear" onClick={() => {setSelected("");setHover("");}}>Clear focus</button></> : <><strong>One price, one state</strong><small>{kind === "SPOT" ? "Up, down or fading." : "Both calls and puts trend up when their own premium rises. Flat and falling premiums are fading."}</small></>}</div>
        <div className="trend-list-scroll"><table className="wheel-table"><thead><tr><th>Instrument</th><th>State</th></tr></thead><tbody>{rows.map((r) => <tr key={r.symbol} className={selected === r.symbol ? "active" : ""}><td><button className="trend-name" onClick={() => setSelected(selected === r.symbol ? "" : r.symbol)} onMouseEnter={() => setHover(r.symbol)} onMouseLeave={() => setHover("")}>{r.label}<small>{kind === "SPOT" ? r.group : `${r.strike} ${kind} · ${r.expiry}`}</small></button></td><td style={{color:COLORS[r.state]}}>{LABELS[r.state]}<small>{r.held} bars · {stamp(r.as_of)}</small></td></tr>)}</tbody></table></div>
      </aside>
    </div>
    <details className="trend-rules"><summary>How the states are defined</summary><p>Trending up: the 5-bar exponential average is above the 20-bar average and the 5-bar average is rising over three bars. Spot is trending down when both conditions reverse. Every other valid pattern is fading, including flat or falling option premiums.</p><p>The horizontal axis measures the gap between the two averages; the vertical axis measures the short average’s slope. Both use the average absolute price change over 14 bars as scale. This is a descriptive view of past price movement, not a tested entry signal. ATM selection can change, but each tail stays on one exact contract.</p><p>Only completed candles from the instrument’s session are used. The latest completed bar must be present, with at least 21 bars of history. Closed-session values retain their date; missing or stale history is unavailable.</p></details>
    {!!data?.unavailable.length && <details className="trend-rules"><summary>Unavailable history ({data.unavailable.length})</summary><div className="trend-unavailable">{data.unavailable.map((r) => <div key={r.symbol}>{r.symbol} · {r.reason}</div>)}</div></details>}
  </section>;
}
