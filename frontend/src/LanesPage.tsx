import { useCallback, useEffect, useState } from "react";
import { curvePath, rupees, tone } from "./agentMath";
import { LaneChartModal, type LaneChartFocus } from "./LaneChart";
import { EquityChart, type EquityPoint } from "./LaneEquityChart";
import { fillMarkers, positionLevels } from "./laneChartMarks";
import type { Tick, Trade as Fill } from "./types";
import { currentSmartBook } from "./laneMarks";
import { API_TOKEN, API_URL } from "./runtime";
import { newestFirst } from "./positionOrder";

type Stats = { trades: number; wins: number; win_rate: number | null; net: number; gross_profit: number; gross_loss: number; profit_factor: number | null; avg_win: number | null; avg_loss: number | null };
type Summary = {
  lane: string; capital: number; equity: number; cash: number; return_pct: number; realized: number; unrealized: number;
  open_positions: number; max_drawdown: number; all: Stats; by_source: Record<string, Stats>; by_exit_reason: Record<string, Stats>;
  decisions: Record<string, number>;
};
type Position = {
  symbol: string; quantity: number; lots: number; lot_size: number; average_price: number; last_price: number; opened_at: string;
  hard_stop: number; trailing_stop: number | null; source: string; expiry: string | null; target_price: number | null;
  hold_until: string | null; score: number | null; dispersion: number | null; unrealized_pnl: number;
  stop_price?: number | null; grok?: { stop: number | null; target: number | null; conviction: string | null; reason: string | null };
};
type GrokHealth = { enabled: boolean; ticket?: string; slots?: number; index_only?: boolean; queued?: number; effort?: string; error?: string | null;
  entry_in_flight?: boolean; exit_in_flight?: boolean; safety_stop_pct?: number;
  today?: { calls: number; cost_usd: number; errors: number; avg_latency_ms: number | null; taken: number; candidates: number };
  last_entry?: { at: string; market_read: string; candidates: number; taken: number } | null;
  last_exit?: { at: string; market_read: string; actions: { symbol: string; action: string; reason: string }[] } | null };
type Health = { grok?: GrokHealth; pending: number; cutoff: number; rolling_scores: number; error: string | null; last_batch_at: string | null;
  intelligence?:{policy:string;mode:string;qwen_required:boolean;validation:string;last_allocation?:{priority:number;observed_at:string}};
  rules: { entry_delay_seconds: number; skip_fraction: number; ticket_fraction: number | null; slots?: number | null; sizing?: string; liquidity_share_pct: number;
    macd: { hard: number; act: number; trail: number; target: number | null; hold: number }; blast: { hard: number; act: number; trail: number } | null } };
type Snapshot = { requestedAt?:number; lane: string; capital: number; portfolio: { cash: number; equity: number; positions: Position[] }; health: Health };
type Trade = { position_id: string; symbol: string; source: string; entry_time: string; exit_time: string; entry_price: number; lots: number; pnl: number; return_pct: number; exit_reason: string | null; max_return_pct: number | null; closed: boolean };
type JournalRow = { id: string; at: string; source: string; symbol: string; option_type: string; decision: string; score: number | null; cutoff: number | null;
  dispersion: number | null; ticket: number | null; lots: number | null; signal_price: number | null; check_price: number | null; detail: Record<string, unknown> };

const headers = (): HeadersInit => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});
async function call<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { headers: headers() });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${path} failed (HTTP ${response.status})`);
  }
  return response.json() as Promise<T>;
}

const when = (iso: string | null | undefined) => {if(!iso)return '—';const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(iso)).map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} IST`;};
const short = (symbol: string) => symbol.replace(/^(NSE|BSE):/, "");
const percent = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const money = (value: number) => `₹${value >= 100_000 ? `${(value / 100_000).toFixed(2)} L` : `${(value / 1000).toFixed(1)}k`}`;
const label = (lane: string) => lane.replace("smart_", "₹").toUpperCase();
const SOURCE_LABEL: Record<string, string> = { grok: "Grok picks", macd: "MACD source", blast: "Blast source" };
const DECISIONS = ["", "TAKEN", "WAITING_PRICE", "NO_PRICE", "SESSION_CLOSED", "QWEN_REJECTED", "QWEN_UNAVAILABLE", "QWEN_STALE", "SKIPPED_LOW_SCORE", "MACD_FADED", "LOT_ABOVE_TICKET", "NO_CASH", "POSITION_LIMIT", "TOO_ILLIQUID", "ALREADY_HELD", "STALE", "NOT_TRADABLE", "ENTRY_REJECTED", "GROK_SKIP", "GROK_UNAVAILABLE", "GROK_PRICE_MOVED", "GROK_STALE"];

function LaneCard({ summary, active, curve, onSelect }: { summary: Summary; active: boolean; curve: number[]; onSelect: () => void }) {
  const all = summary.all;
  const taken = summary.decisions.TAKEN || 0;
  const seen = Object.values(summary.decisions).reduce((a, b) => a + b, 0);
  // The book's own sources (a Grok book trades as "grok"); two shown, MACD and Blast when it has none yet.
  const listed = Object.entries(summary.by_source || {}).sort((x, y) => y[1].trades - x[1].trades);
  const sources: [string, Stats | undefined][] = (listed.length ? listed : [["macd", undefined], ["blast", undefined]] as [string, Stats | undefined][]).slice(0, 2);
  return <button className={`panel lane-card ${active ? "active" : ""}`} onClick={onSelect}>
    <div className="lane-card-head"><b>{label(summary.lane)}</b><span className={tone(summary.return_pct)}>{percent(summary.return_pct)}</span><small>equity {money(summary.equity)} · cash {money(summary.cash)}</small></div>
    {curve.length > 1 && <div className="agent-curve"><svg viewBox="0 0 300 40" preserveAspectRatio="none"><path d={curvePath(curve, 300, 40)} /></svg></div>}
    <div className="agent-stats lane-stats">
      <div className="agent-stat"><span>Realized / open</span><b className={tone(summary.realized)}>{rupees(summary.realized)}</b><small className={tone(summary.unrealized)}>{rupees(summary.unrealized)} on {summary.open_positions} open</small></div>
      <div className="agent-stat"><span>Closed trades</span><b>{all.trades}</b><small>win {all.win_rate == null ? "—" : `${Math.round(all.win_rate * 100)}%`} · PF {all.profit_factor == null ? "—" : all.profit_factor.toFixed(2)}</small></div>
      {sources.map(([name, stats]) => <div className="agent-stat" key={name}><span>{SOURCE_LABEL[name] ?? `${name} source`}</span><b className={tone(stats?.net)}>{rupees(stats?.net ?? 0)}</b><small>{stats?.trades ?? 0} trades</small></div>)}
      <div className="agent-stat"><span>Max drawdown</span><b className={summary.max_drawdown > 0 ? "negative" : ""}>{rupees(-summary.max_drawdown)}</b><small>{percent(-summary.max_drawdown / summary.capital * 100)} of capital</small></div>
      <div className="agent-stat"><span>Decisions</span><b>{taken} taken</b><small>of {seen} journalled</small></div>
    </div>
  </button>;
}

// Inner views, as in the MACD lane (owner, 18 Nov 2026: "build UI for smart lanes as in MACD lane with separate tabs").
type View = "OVERVIEW" | "POSITIONS" | "ORDERS" | "TRADES" | "JOURNAL" | "GROK" | "PERFORMANCE" | "RULES";
const VIEWS: [View, string][] = [["OVERVIEW", "Overview"], ["POSITIONS", "Positions"], ["ORDERS", "Orders & fills"], ["TRADES", "Trades"],
  ["JOURNAL", "Decisions"], ["GROK", "Grok calls"], ["PERFORMANCE", "Performance"], ["RULES", "Rules"]];
const VIEW_STORE = "macd.smartView", BOOK_STORE = "macd.smartBook";
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const keep = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { /* not kept */ } };
type GrokCall = { id: string; at: string; kind: string; items: number | null; taken: number | null; latency_ms: number | null; model: string | null;
  prompt_tokens: number | null; completion_tokens: number | null; reasoning_tokens: number | null; cost_ticks: number | null; error: string | null;
  request: unknown; response: unknown };
const TICKS_PER_USD = 1e10;

export function LanesPage({ ticks, portfolios }: { ticks: Record<string, Tick>; portfolios: Record<string, any> }) {
  const [lanes, setLanes] = useState<Summary[]>([]);
  const [snapshots, setSnapshots] = useState<Record<string, Snapshot>>({});
  const [curves, setCurves] = useState<Record<string, EquityPoint[]>>({});
  const [fills, setFills] = useState<Fill[]>([]);
  const [focus, setFocus] = useState<{ list: LaneChartFocus[]; index: number }>();
  const [selected, setSelected] = useState<string | null>(() => recall(BOOK_STORE) || null);
  const [view, setView] = useState<View>(() => (VIEWS.some(([key]) => key === recall(VIEW_STORE)) ? recall(VIEW_STORE) as View : "OVERVIEW"));
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [journal, setJournal] = useState<JournalRow[]>([]);
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState("");
  const [decision, setDecision] = useState("");
  const [calls, setCalls] = useState<GrokCall[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pickView = (next: View) => { setView(next); keep(VIEW_STORE, next); };
  const pickBook = (next: string) => { setSelected(next); keep(BOOK_STORE, next); };

  const loadIndex = useCallback(async () => {
    try {
      const index = await call<{ lanes: Summary[] }>("/api/lanes");
      setLanes(index.lanes);
      const requestedAt=Date.now();
      const books = await Promise.all(index.lanes.map(async lane => [lane.lane, {...await call<Snapshot>(`/api/lanes/${lane.lane}/snapshot`),requestedAt}] as const));
      setSnapshots(Object.fromEntries(books));
      setSelected((current) => current && index.lanes.some((lane) => lane.lane === current) ? current : index.lanes[0]?.lane ?? null);
      const pairs = await Promise.all(index.lanes.map(async (lane) =>
        [lane.lane, (await call<{ equity: EquityPoint[] }>(`/api/lanes/${lane.lane}/equity?today=true`)).equity] as const));
      setCurves(Object.fromEntries(pairs));
      setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, []);

  const loadLane = useCallback(async () => {
    if (!selected) return;
    try {
      const requestedAt=Date.now();
      const [snap, closed] = await Promise.all([
        call<Snapshot>(`/api/lanes/${selected}/snapshot`),
        call<{ trades: Trade[] }>(`/api/lanes/${selected}/trades?limit=300&today=true`),
      ]);
      setSnapshot({...snap,requestedAt}); setTrades(closed.trades);
      setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, [selected]);

  // The decisions journal and the Grok calls are read only while their tab is open.
  const loadJournal = useCallback(async () => {
    if (!selected || view !== "JOURNAL") return;
    try {
      const query = new URLSearchParams({ limit: "400", ...(day ? { day } : {}), ...(decision ? { decision } : {}) });
      const rows = await call<{ rows: JournalRow[]; days: string[] }>(`/api/lanes/${selected}/journal?${query}`);
      setJournal(rows.rows); setDays(rows.days);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, [selected, view, day, decision]);
  const loadCalls = useCallback(async () => {
    if (!selected || view !== "GROK") return;
    try { setCalls((await call<{ calls: GrokCall[] }>(`/api/lanes/${selected}/grok?limit=100${day ? `&day=${day}` : ""}`)).calls); }
    catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, [selected, view, day]);

  // The lane's own fills: the Orders & fills tab and the position charts' buy / sell markers.
  const loadFills = useCallback(async () => {
    if (!selected) return;
    try { setFills((await call<{ trades: Fill[] }>(`/api/lanes/${selected}/book?limit=3000&today=true`)).trades); } catch { /* markers only */ }
  }, [selected]);
  useEffect(() => { setFills([]); void loadFills(); const id = window.setInterval(loadFills, 20_000); return () => window.clearInterval(id); }, [loadFills]);
  useEffect(() => { void loadIndex(); const id = window.setInterval(loadIndex, 15_000); return () => window.clearInterval(id); }, [loadIndex]);
  useEffect(() => { void loadLane(); const id = window.setInterval(loadLane, 5_000); return () => window.clearInterval(id); }, [loadLane]);
  useEffect(() => { void loadJournal(); const id = window.setInterval(loadJournal, 10_000); return () => window.clearInterval(id); }, [loadJournal]);
  useEffect(() => { setCalls([]); void loadCalls(); const id = window.setInterval(loadCalls, 30_000); return () => window.clearInterval(id); }, [loadCalls]);

  const current = snapshot?.lane === selected ? snapshot : selected ? snapshots[selected] ?? null : null;
  const health = current?.health;
  const rules = health?.rules;
  const grokOn = Boolean(health?.grok?.enabled);
  const books=Object.fromEntries(lanes.map(lane=>[lane.lane,currentSmartBook(
    lane.lane===selected&&snapshot?.lane===selected?snapshot:snapshots[lane.lane],portfolios[lane.lane],ticks)]));
  const marked = selected ? books[selected] : null;
  const positions: Position[] = newestFirst(marked?.positions || []);
  const summary = lanes.find((lane) => lane.lane === selected);
  const orderedFills = [...fills].sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  const openPosition = (index: number) => setFocus({ index, list: positions.map((p) => ({
    symbol: p.symbol, title: short(p.symbol), subtitle: `${p.lots} lots @ ₹${p.average_price.toFixed(2)} · ${p.source} · opened ${when(p.opened_at)}`,
    markers: fillMarkers(fills, p.symbol, p.opened_at), levels: positionLevels(p) })) });
  const openClosed = (index: number) => setFocus({ index, list: trades.map((t) => ({
    symbol: t.symbol, title: short(t.symbol), subtitle: `${t.lots} lots @ ₹${t.entry_price.toFixed(2)} · ${(t.exit_reason || "").replace(/_/g, " ").toLowerCase()} ${percent(t.return_pct)} · ${when(t.entry_time)} → ${when(t.exit_time)}`,
    markers: fillMarkers(fills, t.symbol, t.entry_time, t.exit_time) })) });
  const openFill = (index: number) => { const f = orderedFills[index]; const at = positions.findIndex((p) => p.symbol === f.symbol);
    if (at >= 0) openPosition(at); else { const t = trades.findIndex((row) => row.symbol === f.symbol); if (t >= 0) openClosed(t); } };
  const badges: Partial<Record<View, number | string>> = { POSITIONS: positions.length || "", ORDERS: fills.length || "", TRADES: trades.length || "",
    GROK: grokOn ? health?.grok?.today?.calls || "" : "" };
  const views = VIEWS.filter(([key]) => key !== "GROK" || grokOn);
  const shown = view === "GROK" && !grokOn ? "OVERVIEW" : view;

  return <div className="agent-page lanes-page">
    <div className="agent-head panel-row">
      <div><b>Smart lanes{grokOn ? " · Grok picks" : " · local intelligence"}</b><span className="agent-sub">MACD signals · paper only · {lanes.length} books</span></div>
      {health && <div className="agent-actions">
        <span className="agent-chip">{health.pending} waiting</span>
        {rules?.slots ? <span className="agent-chip" title="No new entry while every slot is used">{marked?.positions.length ?? 0} / {rules.slots} slots</span> : null}
        <span className="agent-chip">{marked ? `${Math.max(0, (1 - marked.cash / Math.max(1, marked.equity)) * 100).toFixed(1)}%` : "—"} deployed</span>
      </div>}
    </div>
    <div className="lane-cards">{lanes.map((lane) => {
      const book=books[lane.lane];
      const card=book?{...lane,equity:book.equity,cash:book.cash,unrealized:book.unrealized_pnl,
        realized:book.realized_pnl??lane.realized,open_positions:book.positions.length,return_pct:(book.equity/lane.capital-1)*100}:lane;
      return <LaneCard key={lane.lane} summary={card} active={lane.lane===selected} curve={(curves[lane.lane]||[]).map((p)=>p.equity)} onSelect={()=>pickBook(lane.lane)}/>;
    })}
      {lanes.length === 0 && !error && <div className="agent-empty">No smart lanes configured.</div>}</div>
    {selected && <nav className="sub-tabs lane-views smart-views" aria-label={`${label(selected)} views`}>{views.map(([key, text]) =>
      <button key={key} className={shown === key ? "active" : ""} aria-current={shown === key ? "page" : undefined} onClick={() => pickView(key)}>
        {text}{badges[key] !== undefined && badges[key] !== "" && <span>{badges[key]}</span>}</button>)}</nav>}
    {(error || health?.error) && <div className="agent-error">{error || health?.error}</div>}

    {selected && shown === "OVERVIEW" && <>
      {grokOn && health?.grok && <GrokPanel grok={health.grok} />}
      <section className="panel agent-panel lane-equity">
        <div className="panel-title">{label(selected)} — equity and drawdown today <small>hover for the IST time, equity, return and fall from the peak</small></div>
        <EquityChart points={curves[selected] || []} capital={summary?.capital ?? 0} height={260} />
      </section>
      <div className="agent-bottom">
        <section className="panel agent-panel"><div className="panel-title">Open now ({positions.length}) <small><a href="#" onClick={(e) => { e.preventDefault(); pickView("POSITIONS"); }}>all positions →</a></small></div>
          {positions.length === 0 ? <div className="agent-empty">Nothing open.</div> : <PositionsTable positions={positions.slice(0, 8)} ticks={ticks} onOpen={openPosition} compact />}</section>
        <section className="panel agent-panel"><div className="panel-title">Closed today ({trades.length}) <small><a href="#" onClick={(e) => { e.preventDefault(); pickView("TRADES"); }}>all trades →</a></small></div>
          {trades.length === 0 ? <div className="agent-empty">No closed trades yet.</div> : <TradesTable trades={trades.slice(0, 8)} onOpen={openClosed} />}</section>
      </div>
    </>}

    {selected && shown === "POSITIONS" && <section className="panel agent-panel">
      <div className="panel-title">{label(selected)} — open positions ({positions.length}) <small>newest first · click a row for its chart</small></div>
      {positions.length === 0 ? <div className="agent-empty">Nothing open.</div> : <PositionsTable positions={positions} ticks={ticks} onOpen={openPosition} />}
    </section>}

    {selected && shown === "ORDERS" && <section className="panel agent-panel">
      <div className="panel-title">{label(selected)} — orders &amp; fills today ({fills.length}) <small>paper fills, newest first · click a row for its chart</small></div>
      {orderedFills.length === 0 ? <div className="agent-empty">No fills today.</div> :
        <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Time</th><th>Side</th><th>Contract</th><th>Lots</th><th>Qty</th><th>Price</th><th>Value</th><th>Fees</th></tr></thead>
          <tbody>{orderedFills.map((f, index) => <tr key={f.trade_id} className="openable-row" onClick={() => openFill(index)}>
            <td>{when(f.timestamp)}</td><td className={f.side === "BUY" ? "positive" : "negative"}>{f.side}</td><td>{short(f.symbol)}</td><td>{f.lots}</td><td>{f.quantity}</td>
            <td>{f.price.toFixed(2)}</td><td>{money(f.price * f.quantity)}</td><td>{f.fees == null ? "—" : `₹${f.fees.toFixed(0)}`}</td></tr>)}</tbody></table></div>}
    </section>}

    {selected && shown === "TRADES" && <section className="panel agent-panel">
      <div className="panel-title">{label(selected)} — closed trades today ({trades.length}) <small>click a row for its chart · history in Back office</small></div>
      {trades.length === 0 ? <div className="agent-empty">No closed trades yet.</div> : <TradesTable trades={trades} onOpen={openClosed} />}
    </section>}

    {selected && shown === "JOURNAL" && <section className="panel agent-panel">
      <div className="panel-title">{label(selected)} — every decision and why
        <select value={day} onChange={(e) => setDay(e.target.value)}><option value="">all days</option>{days.map((d) => <option key={d} value={d}>{d}</option>)}</select>
        <select value={decision} onChange={(e) => setDecision(e.target.value)}>{DECISIONS.map((d) => <option key={d} value={d}>{d ? d.replace(/_/g, " ").toLowerCase() : "all decisions"}</option>)}</select>
      </div>
      {summary && <div className="agent-actions">{Object.entries(summary.decisions).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([name, n]) =>
        <button key={name} className={`agent-chip${decision === name ? " active" : ""}`} onClick={() => setDecision(decision === name ? "" : name)}>{name.replace(/_/g, " ").toLowerCase()} {n}</button>)}</div>}
      {journal.length === 0 ? <div className="agent-empty">No decisions journalled{day ? ` on ${day}` : ""} yet.</div> :
        <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr>
          <th>Time</th><th>Source</th><th>Contract</th><th>Decision</th><th>Priority / research score</th><th>Size×</th><th>Ticket</th><th>Lots</th><th>Signal → check</th>
        </tr></thead><tbody>{journal.map((row) => <tr key={row.id}>
          <td>{when(row.at)}</td><td>{row.source}</td><td>{short(row.symbol)}</td>
          <td className={row.decision === "TAKEN" ? "positive" : ""}>{row.decision.replace(/_/g, " ").toLowerCase()}{grokReason(row) && <small> · Grok: {grokReason(row)}</small>}</td>
          <td>{typeof (row.detail.allocation as {priority?:number}|undefined)?.priority==='number'?(row.detail.allocation as {priority:number}).priority.toFixed(3):'—'}<small> / {row.score == null ? '—' : row.score.toFixed(3)}</small><details><summary>ⓘ Inputs</summary><pre>{JSON.stringify(row.detail,null,2)}</pre></details></td>
          <td>{row.dispersion == null ? "—" : row.dispersion.toFixed(2)}</td><td>{row.ticket == null ? "—" : money(row.ticket)}</td><td>{row.lots ?? "—"}</td>
          <td>{row.signal_price?.toFixed(2) ?? "—"}{row.check_price != null && <small> → {row.check_price.toFixed(2)}</small>}</td>
        </tr>)}</tbody></table></div>}
    </section>}

    {selected && shown === "GROK" && <section className="panel agent-panel">
      <div className="panel-title">{label(selected)} — Grok calls ({calls.length}) <small>what was sent, what came back, time and cost · newest first</small>
        <select value={day} onChange={(e) => setDay(e.target.value)}><option value="">latest</option>{days.map((d) => <option key={d} value={d}>{d}</option>)}</select></div>
      {calls.length === 0 ? <div className="agent-empty">No Grok calls{day ? ` on ${day}` : " yet"}.</div> :
        <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Time</th><th>Kind</th><th>Shown → taken</th><th>Time taken</th><th>Tokens in / out</th><th>Cost</th><th>Result</th></tr></thead>
          <tbody>{calls.map((c) => <tr key={c.id}><td>{when(c.at)}</td><td>{c.kind === "entry" ? "pick" : "review"}</td>
            <td>{c.kind === "entry" ? `${c.items ?? 0} → ${c.taken ?? 0}` : `${c.items ?? 0} held`}</td><td>{c.latency_ms == null ? "—" : `${(c.latency_ms / 1000).toFixed(1)} s`}</td>
            <td>{c.prompt_tokens ?? "—"} / {c.completion_tokens ?? "—"}{c.reasoning_tokens ? <small> (+{c.reasoning_tokens} reasoning)</small> : null}</td>
            <td>{c.cost_ticks == null ? "—" : `$${(c.cost_ticks / TICKS_PER_USD).toFixed(3)}`}</td>
            <td className={c.error ? "negative" : ""}>{c.error ? c.error : "ok"}<details><summary>ⓘ Sent / received</summary><pre>{JSON.stringify({ request: c.request, response: c.response }, null, 2)}</pre></details></td></tr>)}</tbody></table></div>}
    </section>}

    {selected && shown === "PERFORMANCE" && summary && <>
      <section className="panel agent-panel"><div className="panel-title">{label(selected)} — book since its start <small>capital {money(summary.capital)} · not proven on a short sample</small></div>
        <div className="agent-stats lane-stats">
          <div className="agent-stat"><span>Equity</span><b>{money(marked?.equity ?? summary.equity)}</b><small className={tone(summary.return_pct)}>{percent(((marked?.equity ?? summary.equity) / summary.capital - 1) * 100)}</small></div>
          <div className="agent-stat"><span>Realized</span><b className={tone(summary.realized)}>{rupees(summary.realized)}</b><small>{summary.all.trades} closed</small></div>
          <div className="agent-stat"><span>Win rate · PF</span><b>{summary.all.win_rate == null ? "—" : `${Math.round(summary.all.win_rate * 100)}%`}</b><small>PF {summary.all.profit_factor == null ? "—" : summary.all.profit_factor.toFixed(2)}</small></div>
          <div className="agent-stat"><span>Avg win / loss</span><b className="positive">{rupees(summary.all.avg_win)}</b><small className="negative">{rupees(summary.all.avg_loss)}</small></div>
          <div className="agent-stat"><span>Payoff ratio</span><b>{summary.all.avg_win && summary.all.avg_loss ? Math.abs(summary.all.avg_win / summary.all.avg_loss).toFixed(2) : "—"}</b><small>avg win ÷ avg loss</small></div>
          <div className="agent-stat"><span>Max drawdown</span><b className={summary.max_drawdown > 0 ? "negative" : ""}>{rupees(-summary.max_drawdown)}</b><small>{percent(-summary.max_drawdown / summary.capital * 100)} of capital</small></div>
        </div></section>
      <div className="agent-bottom">
        <StatsTable title="By source" rows={summary.by_source} />
        <StatsTable title="By exit reason" rows={summary.by_exit_reason} />
      </div>
    </>}

    {selected && shown === "RULES" && <section className="panel agent-panel">
      <div className="panel-title">{label(selected)} — how this book trades</div>
      <div className="agent-actions">
        {grokOn && health?.grok && <><span className="agent-chip">{health.grok.ticket}</span><span className="agent-chip">up to {health.grok.slots} positions</span>
          {health.grok.index_only && <span className="agent-chip">index signals only</span>}<span className="agent-chip">Grok effort {health.grok.effort ?? "—"}</span>
          <span className="agent-chip">safety stop {Math.round((health.grok.safety_stop_pct ?? 0.5) * 100)}%</span></>}
        {rules && !grokOn && <span className="agent-chip">MACD: stop {Math.round(rules.macd.hard * 100)}% · trail {Math.round(rules.macd.trail * 100)}% from +{Math.round(rules.macd.act * 100)}% · target +{Math.round((rules.macd.target || 0) * 100)}% · hold {rules.macd.hold} sessions</span>}
        {rules?.blast && !grokOn && <span className="agent-chip">Blast: stop {Math.round(rules.blast.hard * 100)}% · trail {Math.round(rules.blast.trail * 100)}% from +{Math.round(rules.blast.act * 100)}%</span>}
        <span className="agent-chip">Last batch {when(health?.last_batch_at)}</span>
      </div>
      {grokOn ? <p>MACD BUY signals that pass the usual checks (not held, today's ATM leg, one per stock, in session, fresh quote, MACD still above zero after {rules ? rules.entry_delay_seconds / 60 : 5} minutes) are sent to Grok in batches. Grok takes only the ones it judges the best probable winners and may skip them all. Each take is a fixed ticket in whole lots. Grok sets a stop and a target premium and reviews open positions every 30 minutes (hold, close, halve, move the levels); the code also keeps the {Math.round((health?.grok?.safety_stop_pct ?? 0.5) * 100)}% safety stop and the expiry-day and stock-series exits. Every call, with what was sent and what came back, is in the Grok calls tab. Not proven.</p>
        : <p>{health?.intelligence?.policy ?? ""} Same MACD BUY signals, rechecked after {rules ? rules.entry_delay_seconds / 60 : 5} minutes. {rules?.slots ? `Up to ${rules.slots} open positions; each entry gets the available cash divided by the free slots, in whole lots.` : `Whole-lot tickets within ${rules?.ticket_fraction ? Math.round(rules.ticket_fraction * 100) : 10}% of equity and available cash.`} Missing quotes retry for up to ten minutes; a fill always needs a fresh quote.</p>}
    </section>}
    {focus && <LaneChartModal list={focus.list} index={focus.index} onIndex={(index) => setFocus((old) => old && { ...old, index })} onClose={() => setFocus(undefined)} />}
  </div>;
}

function PositionsTable({ positions, ticks, onOpen, compact = false }: { positions: Position[]; ticks: Record<string, Tick>; onOpen: (index: number) => void; compact?: boolean }) {
  return <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr>
    <th>Contract</th>{!compact && <th>Source</th>}<th>Lots</th><th>Avg</th><th>LTP</th><th>P&amp;L</th><th>Stop</th>{!compact && <><th>Trail</th><th>Target</th><th>Hold until</th><th>Score</th><th>Size×</th></>}<th>Opened</th>
  </tr></thead><tbody>{positions.map((p, index) => {
    const tick = ticks[p.symbol];
    const ltp = tick && tick.ltp > 0 ? tick.ltp : p.last_price;
    const ret = (ltp / p.average_price - 1) * 100;
    return <tr key={p.symbol} className="openable-row" onClick={() => onOpen(index)}>
      <td>{short(p.symbol)}<small> {p.expiry ? `exp ${p.expiry.slice(5)}` : ""}</small></td>{!compact && <td>{p.source}</td>}<td>{p.lots}</td>
      <td>{p.average_price.toFixed(2)}</td><td>{ltp.toFixed(2)}</td>
      <td className={tone(ret)}>{percent(ret)}<small> {rupees((ltp - p.average_price) * p.quantity)}</small></td>
      <td>{p.stop_price ? <>{p.stop_price.toFixed(2)}<small> Grok · safety {p.hard_stop.toFixed(2)}</small></> : p.hard_stop.toFixed(2)}</td>
      {!compact && <><td>{p.trailing_stop ? p.trailing_stop.toFixed(2) : "—"}</td><td>{p.target_price ? p.target_price.toFixed(2) : p.grok?.target ? p.grok.target.toFixed(2) : "—"}</td>
        <td>{when(p.hold_until)}</td><td>{p.score == null ? "—" : p.score.toFixed(3)}</td><td>{p.dispersion == null ? "—" : p.dispersion.toFixed(2)}</td></>}<td>{when(p.opened_at)}</td>
    </tr>;
  })}</tbody></table></div>;
}

function TradesTable({ trades, onOpen }: { trades: Trade[]; onOpen: (index: number) => void }) {
  return <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr>
    <th>Contract</th><th>Source</th><th>Entry</th><th>Exit</th><th>Lots</th><th>P&amp;L</th><th>Return</th><th>Peak</th><th>Exit reason</th>
  </tr></thead><tbody>{trades.map((t, index) => <tr key={`${t.position_id}:${t.exit_time}`} className="openable-row" onClick={() => onOpen(index)}>
    <td>{short(t.symbol)}</td><td>{t.source}</td><td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td><td>{t.lots}</td>
    <td className={tone(t.pnl)}>{rupees(t.pnl)}</td><td className={tone(t.return_pct)}>{percent(t.return_pct)}</td>
    <td>{percent(t.max_return_pct)}</td><td>{(t.exit_reason || "—").replace(/_/g, " ").toLowerCase()}{t.closed ? "" : " (part)"}</td>
  </tr>)}</tbody></table></div>;
}

function StatsTable({ title, rows }: { title: string; rows: Record<string, Stats> }) {
  const list = Object.entries(rows || {}).sort((a, b) => b[1].trades - a[1].trades);
  return <section className="panel agent-panel"><div className="panel-title">{title}</div>
    {list.length === 0 ? <div className="agent-empty">No closed trades yet.</div> :
      <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>{title.replace("By ", "")}</th><th>Trades</th><th>Win</th><th>Net</th><th>PF</th><th>Avg win</th><th>Avg loss</th></tr></thead>
        <tbody>{list.map(([name, s]) => <tr key={name}><td>{name.replace(/_/g, " ").toLowerCase()}</td><td>{s.trades}</td><td>{s.win_rate == null ? "—" : `${Math.round(s.win_rate * 100)}%`}</td>
          <td className={tone(s.net)}>{rupees(s.net)}</td><td>{s.profit_factor == null ? "—" : s.profit_factor.toFixed(2)}</td><td>{rupees(s.avg_win)}</td><td>{rupees(s.avg_loss)}</td></tr>)}</tbody></table></div>}
  </section>;
}

function grokReason(row: JournalRow): string | null {
  const grok = row.detail.grok as { reason?: string | null } | undefined;
  return grok?.reason || null;
}

function GrokPanel({ grok }: { grok: GrokHealth }) {
  const t = grok.today;
  return <section className="panel agent-panel" aria-label="Grok picks">
    <div className="panel-title">Grok picks and exits <small>{grok.ticket} · up to {grok.slots} positions{grok.index_only ? " · index signals only" : ""} · not proven</small></div>
    <div className="agent-actions">
      <span className="agent-chip">Today {t?.candidates ?? 0} shown → {t?.taken ?? 0} taken</span>
      <span className="agent-chip">{t?.calls ?? 0} calls · ${(t?.cost_usd ?? 0).toFixed(2)}{t?.avg_latency_ms ? ` · ${(t.avg_latency_ms / 1000).toFixed(0)} s avg` : ""}{t?.errors ? ` · ${t.errors} failed` : ""}</span>
      <span className="agent-chip">{grok.queued ?? 0} queued{grok.entry_in_flight ? " · picking…" : ""}{grok.exit_in_flight ? " · reviewing…" : ""}</span>
      <span className="agent-chip">Safety stop {Math.round((grok.safety_stop_pct ?? 0.5) * 100)}%</span>
    </div>
    {grok.last_entry && <p><b>Last pick</b> {when(grok.last_entry.at)} — {grok.last_entry.taken} of {grok.last_entry.candidates} taken. {grok.last_entry.market_read}</p>}
    {grok.last_exit && <p><b>Last review</b> {when(grok.last_exit.at)} — {grok.last_exit.market_read}{grok.last_exit.actions.filter((a) => a.action !== "hold").map((a) => ` · ${short(a.symbol)} ${a.action.replace("_", " ")}: ${a.reason}`).join("")}</p>}
    {grok.error && <small className="negative">{grok.error}</small>}
  </section>;
}
