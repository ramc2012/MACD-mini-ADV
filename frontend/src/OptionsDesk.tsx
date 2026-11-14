import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";
import { asOf, bandPath, compact, extent, linePath, linear, nearestIndex, sessionDomain, sessionTicks, ticks, type Domain } from "./optionsDeskMath";

// Auction desk -> Options & flow. Eight tools from data the app already stores (no downloads, no second socket):
// NTM IV vs VIX, spot with the expected-move band and volume zones, gamma exposure by strike and through the day,
// call vs put volume, writers' P&L on OI added today, the IV range, MFLOW and smart candles, plus the option table.
// Every session chart shares one crosshair time; a click pins that moment for the table, gamma and writers.

type VolxPoint = { ts: number; spot: number; vix: number | null; ntm_iv: number | null; ce_iv: number | null; pe_iv: number | null };
type Leg = { ltp: number | null; bid: number | null; ask: number | null; volume: number | null; oi: number | null; oi_change: number | null;
  oi_change_today: number | null; iv: number | null; delta: number | null; gamma: number | null; theta: number | null; vega: number | null };
type TableRow = { strike: number; CE?: Leg; PE?: Leg };
type WriterLeg = { net_added: number; avg_premium: number | null; premium: number; pnl: number };
type Chain = {
  underlying: string; expiries: string[]; expiry?: string; day?: string; days?: string[]; note?: string;
  as_of?: number; as_of_ist?: string; spot?: number; futures?: number | null; vix?: number | null; step?: number; atm?: number;
  snapshots?: number; first_ts?: number; last_ts?: number; days_to_expiry?: number;
  volx?: VolxPoint[];
  expected_move?: { open: { ts: number; spot: number; ntm_iv: number } | null; band: { ts: number; spot: number; upper: number; lower: number }[];
    moves: Record<string, { days: number; points: number; pct: number }> };
  table?: TableRow[];
  gamma?: { spot: number; net: number; zero_gamma: number | null; call_wall: number | null; put_wall: number | null;
    strikes: { strike: number; call: number; put: number; net: number }[]; timeline: { ts: number; spot: number; net: number }[] };
  spectrum?: { points: { ts: number; spot: number; ce: number; pe: number }[]; zones: { strike: number; type: "CE" | "PE"; volume: number }[];
    pcr: number | null; ce: number; pe: number };
  writers?: { strikes: { strike: number; CE?: WriterLeg; PE?: WriterLeg }[]; timeline: { ts: number; ce: number; pe: number }[];
    ce_total: number; pe_total: number; basis: string };
  iv_range?: { series: { day: string; ts: number; iv: number | null; spot: number; vix: number | null; expiry: string; days_to_expiry: number }[];
    sessions: number; current?: number; high?: number; low?: number; mean?: number; high_20?: number; low_20?: number;
    percentile?: number; rank?: number | null; projected?: Record<string, { days: number; points: number }> };
};
type Cls = "long_buildup" | "short_buildup" | "short_covering" | "long_unwinding";
type Bucket = { ts: number; volume: number; new: number; exit: number; oi: number | null; close: number } & Record<Cls, number>;
type Bar = { ts: number; open: number; high: number; low: number; close: number; volume: number; delta: number; strength: number };
type Flow = {
  underlying: string; symbol?: string; day: string; tf?: number; note?: string;
  mflow?: { available: boolean; note?: string; series?: Bucket[]; classes?: Record<Cls, number>; unassigned?: number;
    opening_oi?: number | null; oi?: number | null; oi_change?: number | null; new?: number; exit?: number; shares?: Partial<Record<Cls, number>> };
  candles?: { bars: Bar[]; ib: { high: number; low: number; start: number; end: number } | null;
    clusters: { start: number; end: number; high: number; low: number; bars: number }[] };
};
type Instrument = { underlying: string; kind: "index" | "stock" };

// Colours: CE / buying green and PE / selling red as everywhere in the app; IV blue, VIX amber (dashed), spot neutral.
// The two "leaving" MFLOW classes are dashed so the four read without colour too.
const C = { ce: "#22c893", pe: "#f15b6c", iv: "#55a9ff", vix: "#f5b84b", spot: "#c5d1e1", cover: "#55a9ff", unwind: "#f5b84b", muted: "#8192a9", grid: "#1a2433", violet: "#9b8cff" };
const CLASSES: [Cls, string, string, string | undefined][] = [
  ["long_buildup", "Long build-up (OI ↑ price ↑)", C.ce, undefined], ["short_buildup", "Short build-up (OI ↑ price ↓)", C.pe, undefined],
  ["short_covering", "Short covering (OI ↓ price ↑)", C.cover, "5 3"], ["long_unwinding", "Long unwinding (OI ↓ price ↓)", C.unwind, "5 3"]];
const TFS: [number, string][] = [[3, "3m"], [5, "5m"], [15, "15m"]];
const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
const CLOCK = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const FULL = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const hhmm = (ts: number) => CLOCK.format(new Date(ts * 1000));
const full = (ts?: number | null) => ts ? `${FULL.format(new Date(ts * 1000)).replace(/(\d+)\/(\d+)\/(\d+),?/, "$3-$2-$1")} IST` : "—";
const num = (v: number | null | undefined, d = 2) => v == null || !Number.isFinite(v) ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: Math.abs(v) >= 1000 ? 0 : d });
const pct = (v: number | null | undefined, d = 2) => v == null ? "—" : `${v.toFixed(d)}%`;
const signed = (v: number | null | undefined, d = 2) => v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${num(Math.abs(v), d)}`;
const ordinal = (n: number) => { const r = Math.round(n), t = r % 100, u = r % 10; return `${r}${t >= 11 && t <= 13 ? "th" : u === 1 ? "st" : u === 2 ? "nd" : u === 3 ? "rd" : "th"}`; };
const rupees = (v: number | null | undefined) => v == null ? "—" : `₹${compact(v, true)}`;
const STORE = "macd.optionsDesk";
const recall = (): Record<string, string> => { try { return JSON.parse(window.localStorage.getItem(STORE) || "{}"); } catch { return {}; } };
const remember = (value: Record<string, string>) => { try { window.localStorage.setItem(STORE, JSON.stringify(value)); } catch { return; } };

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(560);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => setWidth(Math.max(260, el.clientWidth || 560));
    read();
    const observer = new ResizeObserver(read);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

// ------------------------------------------------------------------------------------------------ chart frame

type Line = { key: string; label: string; color: string; dash?: string; width?: number; points: [number, number | null][]; fmt?: (v: number) => string };
type HLine = { y: number; label: string; color: string; dash?: string };
type Scales = { sx: (x: number) => number; sy: (y: number) => number; left: number; right: number; top: number; bottom: number };
type ChartProps = {
  label: string; height?: number; xDomain: Domain; xTicks: number[]; xFmt: (x: number) => string; yFmt: (v: number) => string;
  lines?: Line[]; band?: { color: string; points: [number, number, number][] }; hlines?: HLine[]; yDomain?: Domain; zeroLine?: boolean;
  hover?: number | null; onHover?: (x: number | null) => void; onPick?: (x: number) => void; pinned?: number | null;
  children?: (s: Scales) => ReactNode;
};

const valueAt = (points: [number, number | null][], x: number) => {
  if (!points.length) return null;
  const i = nearestIndex(points.map(([ts]) => ({ ts })), x);
  return points[i]?.[1] ?? null;
};

function XYChart({ label, height = 190, xDomain, xTicks, xFmt, yFmt, lines = [], band, hlines = [], yDomain, zeroLine, hover, onHover, onPick, pinned, children }: ChartProps) {
  const [ref, width] = useWidth();
  const m = { l: 6, r: 62, t: 8, b: 20 };
  const left = m.l, right = width - m.r, top = m.t, bottom = height - m.b;
  const ys = yDomain ?? extent([...lines.flatMap((l) => l.points.map(([, v]) => v)), ...(band?.points.flatMap(([, u, d]) => [u, d]) ?? [])], 0.08, zeroLine ? 0 : undefined);
  const sx = linear(xDomain, [left, right]), sy = linear(ys, [bottom, top]);
  const scales: Scales = { sx, sy, left, right, top, bottom };
  const toX = (clientX: number, rect: DOMRect) => xDomain[0] + ((clientX - rect.left - left) / Math.max(1, right - left)) * (xDomain[1] - xDomain[0]);
  const inside = (x: number) => x >= xDomain[0] && x <= xDomain[1];
  return <div className="od-chart" ref={ref}>
    <svg width={width} height={height} role="img" aria-label={label}
      onPointerMove={onHover ? (e) => { const x = toX(e.clientX, e.currentTarget.getBoundingClientRect()); onHover(inside(x) ? x : null); } : undefined}
      onPointerLeave={onHover ? () => onHover(null) : undefined}
      onClick={onPick ? (e) => { const x = toX(e.clientX, e.currentTarget.getBoundingClientRect()); if (inside(x)) onPick(x); } : undefined}>
      <defs><clipPath id={`clip-${label.replace(/\W/g, "")}`}><rect x={left} y={top} width={Math.max(0, right - left)} height={Math.max(0, bottom - top)} /></clipPath></defs>
      {ticks(ys, 4).map((v) => <g key={`y${v}`}><line x1={left} x2={right} y1={sy(v)} y2={sy(v)} style={{ stroke: C.grid }} /><text x={right + 6} y={sy(v) + 3} className="od-axis">{yFmt(v)}</text></g>)}
      {xTicks.map((x) => <text key={`x${x}`} x={sx(x)} y={height - 5} textAnchor="middle" className="od-axis">{xFmt(x)}</text>)}
      <g clipPath={`url(#clip-${label.replace(/\W/g, "")})`}>
        {band && <path d={bandPath(band.points.map(([x, u, d]) => [sx(x), sy(u), sy(d)]))} style={{ fill: band.color, opacity: 0.14 }} />}
        {zeroLine && ys[0] < 0 && ys[1] > 0 && <line x1={left} x2={right} y1={sy(0)} y2={sy(0)} style={{ stroke: C.muted, strokeWidth: 1 }} />}
        {children?.(scales)}
        {hlines.filter((h) => h.y >= ys[0] && h.y <= ys[1]).map((h) => <g key={`h${h.label}`}>
          <line x1={left} x2={right} y1={sy(h.y)} y2={sy(h.y)} style={{ stroke: h.color, strokeDasharray: h.dash ?? "4 3", strokeWidth: 1 }} />
          <text x={left + 4} y={sy(h.y) - 3} className="od-tag" style={{ fill: h.color }}>{h.label}</text></g>)}
        {lines.map((l) => <path key={l.key} d={linePath(l.points.map(([x, y]) => [sx(x), y == null ? null : sy(y)]))} fill="none"
          style={{ stroke: l.color, strokeWidth: l.width ?? 2, strokeDasharray: l.dash, strokeLinejoin: "round", strokeLinecap: "round" }} />)}
        {pinned != null && inside(pinned) && <line x1={sx(pinned)} x2={sx(pinned)} y1={top} y2={bottom} style={{ stroke: C.violet, strokeWidth: 1.5 }} />}
        {hover != null && inside(hover) && <line x1={sx(hover)} x2={sx(hover)} y1={top} y2={bottom} style={{ stroke: C.muted, strokeDasharray: "3 3" }} />}
      </g>
    </svg>
  </div>;
}

function Legend({ lines, at, extra }: { lines: Line[]; at?: number | null; extra?: ReactNode }) {
  return <div className="od-legend">{lines.map((l) => {
    const v = at != null ? valueAt(l.points, at) : l.points.length ? l.points[l.points.length - 1][1] : null;
    return <span key={l.key}><i style={{ borderTopColor: l.color, borderTopStyle: l.dash ? "dashed" : "solid" }} />{l.label} <b>{v == null ? "—" : (l.fmt ?? ((x: number) => num(x)))(v)}</b></span>;
  })}{extra}</div>;
}

function Panel({ title, sub, children, foot, wide }: { title: string; sub?: ReactNode; children: ReactNode; foot?: ReactNode; wide?: boolean }) {
  return <section className={`panel od-panel${wide ? " wide" : ""}`}>
    <header><h2>{title}</h2>{sub && <span className="od-sub">{sub}</span>}</header>
    {children}
    {foot && <p className="od-foot">{foot}</p>}
  </section>;
}

function Empty({ text }: { text: string }) {
  return <div className="od-empty" role="status">{text}</div>;
}

// ------------------------------------------------------------------------------------------------ page

export function OptionsDeskPage() {
  const saved = useMemo(recall, []);
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [underlying, setUnderlying] = useState(saved.underlying || "NIFTY");
  const [expiry, setExpiry] = useState("");
  const [day, setDay] = useState("");
  const [tf, setTf] = useState(Number(saved.tf) || 5);
  const [at, setAt] = useState<number | null>(null);
  const [chain, setChain] = useState<Chain>();
  const [flow, setFlow] = useState<Flow>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [flowError, setFlowError] = useState("");
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    requestJson<{ instruments: Instrument[] }>(`${API_URL}/api/auction/straddle/instruments`, { headers, signal: controller.signal })
      .then((r) => setInstruments(r.instruments)).catch(() => undefined);
    return () => controller.abort();
  }, []);
  useEffect(() => remember({ underlying, tf: String(tf) }), [underlying, tf]);

  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams();
    if (expiry) query.set("expiry", expiry);
    if (day) query.set("day", day);
    if (at) query.set("at", String(Math.floor(at)));
    const load = (quiet = false) => {
      if (!quiet) { setLoading(true); setError(""); }
      requestJson<Chain>(`${API_URL}/api/auction/options/${encodeURIComponent(underlying)}?${query}`, { headers, signal: controller.signal })
        .then((r) => { setChain(r); setError(""); })
        .catch((reason) => { if (reason.name !== "AbortError") setError(reason instanceof Error ? reason.message : "Options request failed"); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    };
    load();
    // A past session is fixed; the latest one refreshes as new chains are stored (about once a minute for indices).
    const timer = day || at ? undefined : window.setInterval(() => load(true), 60_000);
    return () => { controller.abort(); if (timer) clearInterval(timer); };
  }, [underlying, expiry, day, at]);

  const flowDay = day || chain?.day || "";
  useEffect(() => {
    if (!flowDay) return;
    const controller = new AbortController();
    const load = () => requestJson<Flow>(`${API_URL}/api/auction/futures-flow/${encodeURIComponent(underlying)}?day=${flowDay}&tf=${tf}`, { headers, signal: controller.signal })
      .then((r) => { setFlow(r); setFlowError(""); })
      .catch((reason) => { if (reason.name !== "AbortError") setFlowError(reason instanceof Error ? reason.message : "Futures flow request failed"); });
    load();
    const timer = day ? undefined : window.setInterval(load, 60_000);
    return () => { controller.abort(); if (timer) clearInterval(timer); };
  }, [underlying, flowDay, tf, day]);

  const pickUnderlying = (value: string) => { setUnderlying(value); setExpiry(""); setDay(""); setAt(null); setChain(undefined); setFlow(undefined); };
  const domain = useMemo(() => sessionDomain(chain?.day || flowDay || new Date().toISOString().slice(0, 10)), [chain?.day, flowDay]);
  const xTicks = useMemo(() => sessionTicks(domain), [domain]);
  const shared = { xDomain: domain, xTicks, xFmt: hhmm, hover, onHover: setHover, onPick: (x: number) => setAt(x), pinned: at };
  const indices = instruments.filter((r) => r.kind === "index"), stocks = instruments.filter((r) => r.kind === "stock");
  const iv = chain?.volx?.length ? asOf(chain.volx, chain.as_of ?? Infinity) : undefined;
  const ivr = chain?.iv_range;
  const g = chain?.gamma;
  const flowNow = chain?.spectrum?.points.length ? asOf(chain.spectrum.points, chain.as_of ?? Infinity) : undefined;
  const writersNow = chain?.writers?.timeline.length ? asOf(chain.writers.timeline, chain.as_of ?? Infinity) : undefined;

  return <section className="ledger-page options-desk">
    <div className="page-heading">
      <div><h1>Options &amp; flow</h1><p>NTM IV vs VIX · gamma · expected move · call/put volume · writers' P&amp;L · IV range · MFLOW · smart candles · option table — from stored chains and futures flow, no downloads</p></div>
      <div className="straddle-controls">
        <select className="rrg-select" value={underlying} onChange={(e) => pickUnderlying(e.target.value)} aria-label="Instrument">
          {!instruments.length && <option value={underlying}>{underlying}</option>}
          {!!indices.length && <optgroup label="Indices">{indices.map((r) => <option key={r.underlying} value={r.underlying}>{r.underlying}</option>)}</optgroup>}
          {!!stocks.length && <optgroup label="F&O stocks">{stocks.map((r) => <option key={r.underlying} value={r.underlying}>{r.underlying}</option>)}</optgroup>}
        </select>
        <select className="rrg-select" value={expiry} onChange={(e) => { setExpiry(e.target.value); setDay(""); setAt(null); }} aria-label="Expiry">
          <option value="">Front expiry{chain?.expiry && !expiry ? ` · ${chain.expiry}` : ""}</option>
          {(chain?.expiries ?? []).map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
        <select className="rrg-select" value={day} onChange={(e) => { setDay(e.target.value); setAt(null); }} aria-label="Session">
          <option value="">Latest session{chain?.day && !day ? ` · ${chain.day}` : ""}</option>
          {[...(chain?.days ?? [])].reverse().map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <span className="radar-filter" role="group" aria-label="Candle and MFLOW bucket">{TFS.map(([value, text]) => <button key={value} aria-pressed={tf === value} className={tf === value ? "active" : ""} onClick={() => setTf(value)}>{text}</button>)}</span>
      </div>
    </div>

    <div className="od-moment" role="status">
      {chain?.as_of ? <>Moment <b>{full(chain.as_of)}</b>{at ? <> · pinned <button className="od-link" onClick={() => setAt(null)}>back to latest</button></> : <> · latest snapshot · click any session chart to pin a moment</>}</> : null}
      {loading && <span className="state-loading">Reading stored option chains…</span>}
      {error && <span className="negative" role="alert">{error}</span>}
      {chain?.note && <span className="muted">{chain.note}</span>}
    </div>

    <div className="straddle-tiles">
      <div><span>Spot · futures</span><b>{num(chain?.spot)}</b><small>{chain?.futures ? `fut ${num(chain.futures)} · basis ${signed(chain.futures - (chain.spot ?? 0))}` : "—"}</small></div>
      <div><span>NTM IV vs VIX</span><b>{pct(iv?.ntm_iv)}</b><small>VIX {num(iv?.vix)} · spread {iv?.ntm_iv != null && iv?.vix != null ? signed(iv.ntm_iv - iv.vix) : "—"} pts</small></div>
      <div><span>IV percentile</span><b>{ivr?.percentile != null ? ordinal(ivr.percentile) : "—"}</b><small>{ivr?.sessions ? `${ivr.sessions} sessions · range ${pct(ivr.low, 1)}–${pct(ivr.high, 1)}` : "—"}</small></div>
      <div><span>Expected move · 1 day</span><b>{chain?.expected_move?.moves["1 day"] ? `±${num(chain.expected_move.moves["1 day"].points, 0)}` : "—"}</b><small>{chain?.expected_move?.moves["to expiry"] ? `to expiry ±${num(chain.expected_move.moves["to expiry"].points, 0)} (${num(chain.days_to_expiry, 1)} d)` : "—"}</small></div>
      <div><span>Net GEX · 1% move</span><b className={g ? (g.net >= 0 ? "positive" : "negative") : ""}>{g ? rupees(g.net) : "—"}</b><small>{g?.zero_gamma ? `zero gamma ${num(g.zero_gamma, 0)} · walls ${g.put_wall ?? "—"} / ${g.call_wall ?? "—"}` : "zero gamma —"}</small></div>
      <div><span>PCR · volume</span><b>{flowNow?.ce ? (flowNow.pe / flowNow.ce).toFixed(2) : "—"}</b><small>CE {compact(flowNow?.ce)} · PE {compact(flowNow?.pe)}</small></div>
      <div><span>Writers' P&amp;L (added OI)</span><b>{writersNow ? rupees(writersNow.ce + writersNow.pe) : "—"}</b><small>CE {rupees(writersNow?.ce)} · PE {rupees(writersNow?.pe)}</small></div>
    </div>

    <div className="od-grid">
      <VolxPanel chain={chain} shared={shared} />
      <MovePanel chain={chain} shared={shared} />
      <GammaPanel chain={chain} shared={shared} />
      <SpectrumPanel chain={chain} shared={shared} />
      <WritersPanel chain={chain} shared={shared} />
      <IvRangePanel chain={chain} />
      <MflowPanel flow={flow} error={flowError} shared={shared} />
      <CandlePanel flow={flow} error={flowError} shared={shared} />
      <OptionTable chain={chain} />
    </div>
  </section>;
}

type Shared = { xDomain: Domain; xTicks: number[]; xFmt: (x: number) => string; hover: number | null; onHover: (x: number | null) => void; onPick: (x: number) => void; pinned: number | null };

// ------------------------------------------------------------------------------------------------ 3 NTM VolX

function VolxPanel({ chain, shared }: { chain?: Chain; shared: Shared }) {
  const v = chain?.volx ?? [];
  const lines: Line[] = [
    { key: "ntm", label: "NTM IV (ATM ±2)", color: C.iv, points: v.map((p) => [p.ts, p.ntm_iv]), fmt: (x) => pct(x) },
    { key: "ce", label: "CE IV", color: C.ce, width: 1.25, points: v.map((p) => [p.ts, p.ce_iv]), fmt: (x) => pct(x) },
    { key: "pe", label: "PE IV", color: C.pe, width: 1.25, points: v.map((p) => [p.ts, p.pe_iv]), fmt: (x) => pct(x) },
    { key: "vix", label: "India VIX", color: C.vix, dash: "6 4", points: v.map((p) => [p.ts, p.vix]), fmt: (x) => num(x) },
  ];
  return <Panel title="NTM VolX" sub="implied volatility of the 5 strikes nearest spot vs India VIX"
    foot="IV solved from each contract's mid (or last) price, Black-Scholes on spot, 6.5%, calendar days. VIX is the 30-day NIFTY index; the front weekly's NTM IV usually sits below it early in the week and swings near expiry.">
    <Legend lines={lines} at={shared.hover ?? shared.pinned} />
    {v.length ? <XYChart label="NTM IV vs VIX" lines={lines} yFmt={(x) => x.toFixed(1)} {...shared} /> : <Empty text="No chain snapshots for this session." />}
  </Panel>;
}

// ------------------------------------------------------------------------------------------------ 4 expected move + 5 zones

function MovePanel({ chain, shared }: { chain?: Chain; shared: Shared }) {
  const em = chain?.expected_move;
  const g = chain?.gamma;
  const band = em?.band ?? [];
  const lines: Line[] = [{ key: "spot", label: "Spot", color: C.spot, points: band.map((p) => [p.ts, p.spot]) }];
  const ys = extent([...band.flatMap((p) => [p.spot, p.upper, p.lower])], 0.12);
  const zones = (chain?.spectrum?.zones ?? []).filter((z) => z.strike >= ys[0] && z.strike <= ys[1]);
  const step = chain?.step || 0;
  const hlines: HLine[] = [];
  if (g?.call_wall) hlines.push({ y: g.call_wall, label: `call wall ${g.call_wall}`, color: C.ce });
  if (g?.put_wall) hlines.push({ y: g.put_wall, label: `put wall ${g.put_wall}`, color: C.pe });
  if (g?.zero_gamma) hlines.push({ y: g.zero_gamma, label: `zero gamma ${num(g.zero_gamma, 0)}`, color: C.violet, dash: "2 3" });
  const open = em?.open;
  return <Panel title="Spot & expected move" sub={open ? `open ${num(open.spot)} ± 1σ from the opening NTM IV ${pct(open.ntm_iv)}` : "open ± 1σ"}
    foot="Band = open × IV × √(1/365) × √(minutes elapsed / 375). Shaded strips: the day's 5 highest-volume CE (green) and PE (red) strikes; lines: gamma walls and the zero-gamma level (model).">
    <Legend lines={lines} at={shared.hover ?? shared.pinned} extra={<span className="muted">{Object.entries(em?.moves ?? {}).map(([k, m]) => `${k} ±${num(m.points, 0)} (${m.pct.toFixed(2)}%)`).join(" · ")}</span>} />
    {band.length ? <XYChart label="Spot and expected move" lines={lines} band={{ color: C.iv, points: band.map((p) => [p.ts, p.upper, p.lower]) }} hlines={hlines} yDomain={ys} yFmt={(x) => num(x, 0)} height={220} {...shared}>
      {({ sy, left, right }) => zones.map((z) => {
        const half = (step || 0) / 4;
        return <rect key={`${z.type}${z.strike}`} x={left} width={right - left} y={sy(z.strike + half)} height={Math.max(2, sy(z.strike - half) - sy(z.strike + half))}
          style={{ fill: z.type === "CE" ? C.ce : C.pe, opacity: 0.12 }}><title>{`${z.strike} ${z.type} · ${compact(z.volume)} traded`}</title></rect>;
      })}
    </XYChart> : <Empty text="No NTM IV yet for the band." />}
  </Panel>;
}

// ------------------------------------------------------------------------------------------------ 4 gamma

function StrikeBars({ label, rows, spot, marks = [], fmt, height = 200 }: { label: string; rows: { strike: number; up: number; down: number; tip: string }[]; spot?: number; marks?: { x: number; label: string; color: string }[]; fmt: (v: number) => string; height?: number }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  if (!rows.length) return <Empty text="No strikes." />;
  const strikes = rows.map((r) => r.strike);
  const step = Math.min(...strikes.slice(1).map((s, i) => s - strikes[i]).filter((d) => d > 0), Infinity);
  const gap = Number.isFinite(step) ? step : 1;
  const m = { l: 6, r: 62, t: 10, b: 20 };
  const xd: Domain = [strikes[0] - gap, strikes[strikes.length - 1] + gap];
  const yd = extent(rows.flatMap((r) => [r.up, r.down]), 0.08, 0);
  const sx = linear(xd, [m.l, width - m.r]), sy = linear(yd, [height - m.b, m.t]);
  const bw = Math.max(1.5, (sx(gap) - sx(0)) * 0.42);
  const labelEvery = Math.max(1, Math.ceil(rows.length / Math.max(4, Math.floor((width - 80) / 46))));
  const hovered = rows.find((r) => r.strike === hover);
  return <div className="od-chart" ref={ref}>
    {hovered && <div className="od-tip">{hovered.tip}</div>}
    <svg width={width} height={height} role="img" aria-label={label} onPointerLeave={() => setHover(null)}>
      {ticks(yd, 4).map((v) => <g key={v}><line x1={m.l} x2={width - m.r} y1={sy(v)} y2={sy(v)} style={{ stroke: C.grid }} /><text x={width - m.r + 6} y={sy(v) + 3} className="od-axis">{fmt(v)}</text></g>)}
      <line x1={m.l} x2={width - m.r} y1={sy(0)} y2={sy(0)} style={{ stroke: C.muted }} />
      {rows.map((r, i) => <g key={r.strike} onPointerEnter={() => setHover(r.strike)}>
        <rect x={sx(r.strike) - bw} y={m.t} width={bw * 2} height={height - m.b - m.t} style={{ fill: "transparent" }} />
        {r.up > 0 && <rect x={sx(r.strike) - bw / 2} y={sy(r.up)} width={bw} height={Math.max(1, sy(0) - sy(r.up))} rx={1.5} style={{ fill: C.ce, opacity: hover == null || hover === r.strike ? 0.9 : 0.45 }} />}
        {r.down < 0 && <rect x={sx(r.strike) - bw / 2} y={sy(0)} width={bw} height={Math.max(1, sy(r.down) - sy(0))} rx={1.5} style={{ fill: C.pe, opacity: hover == null || hover === r.strike ? 0.9 : 0.45 }} />}
        {i % labelEvery === 0 && <text x={sx(r.strike)} y={height - 5} textAnchor="middle" className="od-axis">{r.strike}</text>}
      </g>)}
      {spot != null && spot >= xd[0] && spot <= xd[1] && <g><line x1={sx(spot)} x2={sx(spot)} y1={m.t} y2={height - m.b} style={{ stroke: C.spot, strokeDasharray: "4 3" }} /><text x={sx(spot) + 3} y={m.t + 8} className="od-tag" style={{ fill: C.spot }}>spot</text></g>}
      {marks.filter((k) => k.x >= xd[0] && k.x <= xd[1]).map((k) => <g key={k.label}><line x1={sx(k.x)} x2={sx(k.x)} y1={m.t} y2={height - m.b} style={{ stroke: k.color, strokeDasharray: "2 3" }} /><text x={sx(k.x) + 3} y={m.t + 18} className="od-tag" style={{ fill: k.color }}>{k.label}</text></g>)}
    </svg>
  </div>;
}

function GammaPanel({ chain, shared }: { chain?: Chain; shared: Shared }) {
  const g = chain?.gamma;
  const rows = (g?.strikes ?? []).map((s) => ({ strike: s.strike, up: s.call, down: s.put, tip: `${s.strike}: calls ${rupees(s.call)} · puts ${rupees(s.put)} · net ${rupees(s.net)} per 1% move` }));
  const net: Line[] = [{ key: "net", label: "Net GEX", color: C.violet, points: (g?.timeline ?? []).map((p) => [p.ts, p.net]), fmt: (x) => rupees(x) }];
  return <Panel title="Gamma exposure" sub={g ? `net ${rupees(g.net)} per 1% · ${g.net >= 0 ? "positive: dealer hedging damps moves (model)" : "negative: dealer hedging adds to moves (model)"}` : undefined}
    foot="GEX = gamma × OI (shares) × spot² × 1%, calls +, puts − (dealers assumed long calls, short puts). The zero-gamma level re-prices every contract at other spots with its IV held. A positioning model, not observed dealer books.">
    <StrikeBars label="Gamma exposure by strike" rows={rows} spot={g?.spot} fmt={(v) => compact(v)}
      marks={g?.zero_gamma ? [{ x: g.zero_gamma, label: "zero γ", color: C.violet }] : []} />
    <Legend lines={net} at={shared.hover ?? shared.pinned} />
    {net[0].points.length ? <XYChart label="Net GEX through the day" lines={net} yFmt={(v) => compact(v)} zeroLine height={120} {...shared} /> : null}
  </Panel>;
}

// ------------------------------------------------------------------------------------------------ 5 spectrum

function SpectrumPanel({ chain, shared }: { chain?: Chain; shared: Shared }) {
  const sp = chain?.spectrum;
  const pts = sp?.points ?? [];
  const lines: Line[] = [
    { key: "ce", label: "Calls traded", color: C.ce, points: pts.map((p) => [p.ts, p.ce]), fmt: (x) => compact(x) },
    { key: "pe", label: "Puts traded", color: C.pe, dash: "6 3", points: pts.map((p) => [p.ts, p.pe]), fmt: (x) => compact(x) },
  ];
  const zones = sp?.zones ?? [];
  const top = Math.max(1, ...zones.map((z) => z.volume));
  return <Panel title="Spectrum · call vs put volume" sub={sp?.pcr != null ? `put/call volume ${sp.pcr.toFixed(2)}` : undefined}
    foot="Cumulative volume (shares) of the chain read (±8% of spot for indices): each contract adds what it traded since its previous snapshot.">
    <Legend lines={lines} at={shared.hover ?? shared.pinned} />
    {pts.length ? <XYChart label="Cumulative call and put volume" lines={lines} yFmt={(v) => compact(v)} zeroLine {...shared} /> : <Empty text="No volume yet." />}
    {!!zones.length && <div className="od-zones" aria-label="Highest-volume strikes">
      {(["CE", "PE"] as const).map((side) => <div key={side}><span className="od-zone-head">{side === "CE" ? "Call" : "Put"} activity zones</span>
        {zones.filter((z) => z.type === side).map((z) => <div key={z.strike} className="od-zone"><span>{z.strike}</span>
          <i style={{ width: `${(100 * z.volume) / top}%`, background: side === "CE" ? C.ce : C.pe }} /><b>{compact(z.volume)}</b></div>)}</div>)}
    </div>}
  </Panel>;
}

// ------------------------------------------------------------------------------------------------ 9 writers

function WritersPanel({ chain, shared }: { chain?: Chain; shared: Shared }) {
  const w = chain?.writers;
  const lines: Line[] = [
    { key: "ce", label: "Call writers", color: C.ce, points: (w?.timeline ?? []).map((p) => [p.ts, p.ce]), fmt: (x) => rupees(x) },
    { key: "pe", label: "Put writers", color: C.pe, dash: "6 3", points: (w?.timeline ?? []).map((p) => [p.ts, p.pe]), fmt: (x) => rupees(x) },
  ];
  const rows = (w?.strikes ?? []).filter((s) => (s.CE?.pnl ?? 0) !== 0 || (s.PE?.pnl ?? 0) !== 0).map((s) => {
    const ce = s.CE?.pnl ?? 0, pe = s.PE?.pnl ?? 0;
    return { strike: s.strike, up: Math.max(0, ce) + Math.max(0, pe), down: Math.min(0, ce) + Math.min(0, pe),
      tip: `${s.strike}: CE ${rupees(ce)} (${compact(s.CE?.net_added)} added @ ${num(s.CE?.avg_premium)} → ${num(s.CE?.premium)}) · PE ${rupees(pe)} (${compact(s.PE?.net_added)} @ ${num(s.PE?.avg_premium)} → ${num(s.PE?.premium)})` };
  });
  return <Panel title="Gamma derivatives · writers' P&L" sub="OI added today × (average premium while added − premium now)"
    foot="Positive = whoever sold the added contracts is ahead. It assumes added OI was written (sold to open); OI cannot say which side opened it. Bars: per strike, green ahead, red behind.">
    <Legend lines={lines} at={shared.hover ?? shared.pinned} />
    {lines[0].points.length ? <XYChart label="Writers P&L through the day" lines={lines} yFmt={(v) => compact(v)} zeroLine height={160} {...shared} /> : <Empty text="No OI added yet." />}
    <StrikeBars label="Writers P&L by strike" rows={rows} spot={chain?.spot} fmt={(v) => compact(v)} height={150} />
  </Panel>;
}

// ------------------------------------------------------------------------------------------------ 10 IV range

function IvRangePanel({ chain }: { chain?: Chain }) {
  const r = chain?.iv_range;
  const series = r?.series ?? [];
  const [hover, setHover] = useState<number | null>(null);
  const lines: Line[] = [
    { key: "iv", label: "NTM IV at the close", color: C.iv, points: series.map((p, i) => [i, p.iv]), fmt: (x) => pct(x) },
    { key: "vix", label: "India VIX", color: C.vix, dash: "6 4", points: series.map((p, i) => [i, p.vix]), fmt: (x) => num(x) },
  ];
  const hl: HLine[] = [];
  if (r?.high != null) hl.push({ y: r.high, label: `high ${pct(r.high, 1)}`, color: C.pe });
  if (r?.low != null) hl.push({ y: r.low, label: `low ${pct(r.low, 1)}`, color: C.ce });
  if (r?.mean != null) hl.push({ y: r.mean, label: `mean ${pct(r.mean, 1)}`, color: C.muted, dash: "2 3" });
  const every = Math.max(1, Math.ceil(series.length / 6));
  const xt = series.map((_, i) => i).filter((i) => i % every === 0);
  const hi = hover != null ? Math.round(hover) : null;
  const point = hi != null ? series[hi] : series[series.length - 1];
  return <Panel title="IV range" sub={r?.percentile != null ? `now ${pct(r.current)} · ${ordinal(r.percentile)} percentile · IV rank ${r.rank != null ? `${r.rank.toFixed(0)}%` : "—"} · ${r.sessions} sessions` : undefined}
    foot="Each stored session's NTM IV near the close (last chain by 15:25 IST) on the nearest expiry at least a day away; expiry days are left out (only the expiring series is stored then). Projected move = spot × IV × √(days / 365), one standard deviation.">
    <div className="od-legend">
      {lines.map((l) => <span key={l.key}><i style={{ borderTopColor: l.color, borderTopStyle: l.dash ? "dashed" : "solid" }} />{l.label}</span>)}
      {point && <span className="muted">{point.day} · IV <b>{pct(point.iv)}</b> · VIX <b>{num(point.vix)}</b> · {point.expiry} ({num(point.days_to_expiry, 0)} d)</span>}
    </div>
    {series.length ? <XYChart label="IV range by session" lines={lines} hlines={hl} xDomain={[0, Math.max(1, series.length - 1)]} xTicks={xt}
      xFmt={(i) => series[Math.round(i)]?.day.slice(5) ?? ""} yFmt={(v) => v.toFixed(0)} hover={hover} onHover={setHover} /> : <Empty text="No stored sessions." />}
    {r?.projected && <div className="od-chips">{Object.entries(r.projected).map(([k, p]) => <span key={k} className="od-chip">{k} <b>±{num(p.points, 0)}</b></span>)}
      {r.high_20 != null && <span className="od-chip">20-session range <b>{pct(r.low_20, 1)}–{pct(r.high_20, 1)}</b></span>}</div>}
  </Panel>;
}

// ------------------------------------------------------------------------------------------------ 6 MFLOW

function MflowPanel({ flow, error, shared }: { flow?: Flow; error: string; shared: Shared }) {
  const m = flow?.mflow;
  const series = m?.series ?? [];
  const running: Record<Cls, number> = { long_buildup: 0, short_buildup: 0, short_covering: 0, long_unwinding: 0 };
  const cum = series.map((b) => { for (const [k] of CLASSES) running[k] += b[k]; return { ts: b.ts, ...running }; });
  const lines: Line[] = CLASSES.map(([k, label, color, dash]) => ({ key: k, label, color, dash, points: cum.map((p) => [p.ts, p[k]]), fmt: (x) => compact(x) }));
  return <Panel title="MFLOW · futures volume by OI" sub={flow?.symbol ? `${flow.symbol} · OI ${compact(m?.oi)} (${compact(m?.oi_change, true)} today)` : undefined}
    foot={`OI rising = new business, falling = old business leaving; the volume traded since the last OI change goes to the next one. New ${compact(m?.new)} · leaving ${compact(m?.exit)}${m?.unassigned ? ` · ${compact(m.unassigned)} since the last OI change` : ""}. OI captured for NIFTY, BANKNIFTY and SENSEX futures.`}>
    {error ? <Empty text={error} /> : flow?.note ? <Empty text={flow.note} /> : m && !m.available ? <Empty text={m.note || "OI not captured for this contract."} /> : <>
      <Legend lines={lines} at={shared.hover ?? shared.pinned} extra={m?.shares ? <span className="muted">{CLASSES.map(([k]) => `${Math.round((m.shares?.[k] ?? 0) * 100)}%`).join(" / ")}</span> : null} />
      {series.length ? <XYChart label="MFLOW cumulative volume by class" lines={lines} yFmt={(v) => compact(v)} zeroLine {...shared} /> : <Empty text="Reading futures flow…" />}
    </>}
  </Panel>;
}

// ------------------------------------------------------------------------------------------------ 7 smart candles

function CandlePanel({ flow, error, shared }: { flow?: Flow; error: string; shared: Shared }) {
  const c = flow?.candles;
  const bars = c?.bars ?? [];
  const tf = (flow?.tf || 5) * 60;
  const ys = extent(bars.flatMap((b) => [b.high, b.low]), 0.06);
  const hb = shared.hover != null ? bars[nearestIndex(bars, shared.hover - tf / 2)] : undefined;
  return <Panel title="Smart candles" sub={flow?.symbol ? `${flow.symbol} · ${flow.tf}-minute · colour = delta / volume` : undefined}
    foot="Shaded box: initial balance (first hour). Outlined boxes: balance clusters (3+ overlapping bars inside 2.5× a typical bar's range). Stronger colour = more one-sided aggressor volume (quote-classified, not exchange tick data).">
    {error ? <Empty text={error} /> : flow?.note ? <Empty text={flow.note} /> : <>
      <div className="od-legend">{hb ? <span>{hhmm(hb.ts)} · O {num(hb.open)} H {num(hb.high)} L {num(hb.low)} C {num(hb.close)} · vol {compact(hb.volume)} · delta {compact(hb.delta, true)} · strength <b>{(hb.strength * 100).toFixed(0)}%</b></span>
        : c?.ib ? <span>IB {num(c.ib.low)} – {num(c.ib.high)} · {c.clusters.length} balance clusters</span> : null}</div>
      {bars.length ? <XYChart label="Smart candles" yDomain={ys} yFmt={(v) => num(v, 0)} height={240} {...shared}>
        {({ sx, sy }) => {
          const w = Math.max(1.5, (sx(tf) - sx(0)) * 0.62);
          return <>
            {c?.ib && <rect x={sx(c.ib.start)} width={Math.max(0, sx(c.ib.end) - sx(c.ib.start))} y={sy(c.ib.high)} height={Math.max(1, sy(c.ib.low) - sy(c.ib.high))} style={{ fill: C.iv, opacity: 0.08 }} />}
            {c?.ib && [c.ib.high, c.ib.low].map((y) => <line key={y} x1={sx(c.ib!.start)} x2={sx(shared.xDomain[1])} y1={sy(y)} y2={sy(y)} style={{ stroke: C.iv, strokeDasharray: "4 3", opacity: 0.7 }} />)}
            {c?.clusters.map((k) => <rect key={k.start} x={sx(k.start)} width={Math.max(2, sx(k.end + tf) - sx(k.start))} y={sy(k.high)} height={Math.max(1, sy(k.low) - sy(k.high))}
              style={{ fill: "none", stroke: C.vix, strokeWidth: 1, strokeDasharray: "3 2" }}><title>{`balance ${hhmm(k.start)}–${hhmm(k.end + tf)} · ${num(k.low)}–${num(k.high)} · ${k.bars} bars`}</title></rect>)}
            {bars.map((b) => {
              const x = sx(b.ts + tf / 2), up = b.close >= b.open;
              const colour = b.strength >= 0 ? C.ce : C.pe;
              const alpha = 0.3 + Math.min(0.7, Math.abs(b.strength) * 1.4);
              return <g key={b.ts}>
                <line x1={x} x2={x} y1={sy(b.high)} y2={sy(b.low)} style={{ stroke: colour, opacity: Math.max(0.5, alpha) }} />
                <rect x={x - w / 2} width={w} y={sy(Math.max(b.open, b.close))} height={Math.max(1, Math.abs(sy(b.open) - sy(b.close)))} rx={1}
                  style={{ fill: up ? colour : "#0d141e", stroke: colour, opacity: alpha }} />
              </g>;
            })}
          </>;
        }}
      </XYChart> : <Empty text="Reading futures flow…" />}
    </>}
  </Panel>;
}

// ------------------------------------------------------------------------------------------------ 8 option table

function OptionTable({ chain }: { chain?: Chain }) {
  const rows = chain?.table ?? [];
  const spot = chain?.spot ?? 0;
  const maxOi = Math.max(1, ...rows.flatMap((r) => [r.CE?.oi ?? 0, r.PE?.oi ?? 0]));
  const leg = (l: Leg | undefined, side: "CE" | "PE", itm: boolean) => {
    const k = (name = "") => `${name}${itm ? " itm" : ""}`.trim() || undefined;
    const cells = [
      <td key="oi" className={k("od-oi")}><i style={{ width: `${(100 * (l?.oi ?? 0)) / maxOi}%`, background: side === "CE" ? C.ce : C.pe }} /><span>{compact(l?.oi)}</span></td>,
      <td key="chg" className={k((l?.oi_change ?? 0) >= 0 ? "positive" : "negative")}>{compact(l?.oi_change, true)}</td>,
      <td key="vol" className={k()}>{compact(l?.volume)}</td>,
      <td key="iv" className={k()}>{l?.iv != null ? l.iv.toFixed(1) : "—"}</td>,
      <td key="d" className={k()}>{l?.delta != null ? l.delta.toFixed(2) : "—"}</td>,
      <td key="g" className={k()}>{l?.gamma != null ? (l.gamma * 1e4).toFixed(2) : "—"}</td>,
      <td key="t" className={k()}>{num(l?.theta)}</td>,
      <td key="v" className={k()}>{num(l?.vega)}</td>,
      <td key="ltp" className={k("od-ltp")}>{num(l?.ltp)}</td>,
    ];
    return side === "CE" ? cells : [...cells].reverse();
  };
  const heads = ["OI", "OI chg", "Volume", "IV %", "Delta", "Gamma ×10⁴", "Theta/day", "Vega/pt", "LTP"];
  return <Panel title="Options table" wide sub={chain?.as_of ? `${chain.underlying} ${chain.expiry} · ${full(chain.as_of)} · OI and volume in shares · OI change vs the previous session` : undefined}
    foot="Greeks from the solved IV (Black-Scholes on spot). Shaded cells are in the money. Hover a strike for bid/ask and today's OI change.">
    {rows.length ? <div className="overflow-auto od-table-wrap"><table className="od-table">
      <thead><tr><th colSpan={9} className="od-side ce">Calls</th><th className="od-strike">Strike</th><th colSpan={9} className="od-side pe">Puts</th></tr>
        <tr>{heads.map((h) => <th key={`c${h}`}>{h}</th>)}<th className="od-strike" />{[...heads].reverse().map((h) => <th key={`p${h}`}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r) => <tr key={r.strike} className={r.strike === chain?.atm ? "atm" : ""}
        title={`${r.strike} · CE bid/ask ${num(r.CE?.bid)} / ${num(r.CE?.ask)}, OI today ${compact(r.CE?.oi_change_today, true)} · PE bid/ask ${num(r.PE?.bid)} / ${num(r.PE?.ask)}, OI today ${compact(r.PE?.oi_change_today, true)}`}>
        {leg(r.CE, "CE", r.strike < spot)}
        <td className="od-strike">{r.strike}</td>
        {leg(r.PE, "PE", r.strike > spot)}
      </tr>)}</tbody></table></div> : <Empty text="No chain at this moment." />}
  </Panel>;
}
