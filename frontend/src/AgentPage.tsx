import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BaselineSeries, ColorType, createChart, LineSeries, LineStyle, type IChartApi, type MouseEventParams, type Time } from "lightweight-charts";
import { driverText, featureLabel, indexPrediction, pct, rupees, sinceEntry, tone } from "./agentMath";
import { LaneChartModal, type LaneChartFocus } from "./LaneChart";
import type { Tick } from "./types";
import { API_TOKEN, API_URL } from "./runtime";

type Pick = {
  rank: number; symbol: string; underlying: string; strike: number; expiry: string; entry_price: number; score: number;
  drivers: [string, number][]; last_price: number | null; last_at: string | null; since_entry_pct: number | null;
  r1: number | null; r2: number | null; r3: number | null; y?: number | null; max_ret: number | null; min_ret: number | null;
  size?: { vol_daily: number | null; notional: number; lot_size: number | null; lot_value: number | null; lots: number | null };
};
type Picks = { run: { run_id: number; day: string; emitted_at: string; model_version: number | null; candidates: number; source: string } | null; ce: Pick[]; pe: Pick[]; index?: IndexTrade[] };
type SideStats = { r1: number | null; r2: number | null; r3: number | null; y: number | null; excess: number | null; top_quintile: number | null; max_ret: number | null; min_ret: number | null };
type RunSide = SideStats & { picks: number; labelled: number; universe_y: number | null };
type Performance = { runs: { day: string; run_id: number; model_version: number | null; source: string; ce: RunSide; pe: RunSide }[]; summary: { ce: SideStats; pe: SideStats }; basket: { day: string; level: number }[]; index?: { summary: IndexSummary; paths: Record<string, number>; trades: IndexOutcome[] } };
type Metric = { test_days: number; excess: number | null; ce_excess: number | null; ce_excess_t: number | null; pe_excess: number | null; pe_excess_t: number | null; ce_ic: number | null; pe_ic: number | null; ce_hit: number | null; pe_hit: number | null };
type Model = { version: number; trained_at: string; config: { name: string }; train_from: string; train_to: string; samples: number; status: string; note: string; metrics: Record<string, Metric>; weights: Record<string, number> & { rule?: string[] } };
type Models = { models: Model[]; index_models?: IndexModel[]; constants: { as_of: string; top_n: number; min_premium: number; min_turnover: number; max_moneyness: number; validation_days: number; promote_margin: number } };
type IndexTrade = {
  prediction_kind?: "scorecard" | "return" | "rule_score";
  underlying: string; side: number; symbol: string | null; strike: number | null; expiry: string | null; entry_price: number | null;
  predicted: number | null; conviction: number | null; spot: number | null; atr: number | null; stop_spot: number | null; target_spot: number | null;
  premium_stop: number | null; premium_target: number | null; iv: number | null; lot: number | null; drivers: [string, number][];
  alternative: { symbol: string; predicted: number } | null; reason: string; last_price?: number | null; since_entry_pct?: number | null;
  spot_now?: number | null; series?: string | null; front_expiry?: string | null; size?: { lots: number; lot_size: number | null; capital: number; risk_at_stop: number | null };
  r1: number | null; r2: number | null; r3: number | null; y: number | null; max_ret: number | null; min_ret: number | null; spot_path: string | null;
};
type IndexSummary = { opportunities: number; trades: number; no_trade: number; mean_return: number | null; win_rate: number | null; mean_excess: number | null; excess_t: number | null; calls: number; puts: number };
type IndexOutcome = { day: string; underlying: string; side: number; symbol: string | null; y: number | null; baseline: number | null; spot_path: string | null };
type IndexModel = { version: number; config: { name: string }; status: string; note: string; trained_at: string; metrics: Record<string, IndexSummary> };
type Status = { emitted_day: string | null; emit: Record<string, unknown> | null; learned_day: string | null; learn: Record<string, unknown> | null; backfill: Record<string, unknown> | null; error: string | null; busy: boolean; emit_at?: string };

const headers = (): HeadersInit => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});

async function call<T>(path: string, method = "GET"): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { method, headers: headers() });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${path} failed (HTTP ${response.status})`);
  }
  return response.json() as Promise<T>;
}

const num = (value: number | null | undefined, digits = 2) => value == null ? "—" : value.toFixed(digits);

type Ticks = Record<string, Tick>;
const INDEX_SPOT: Record<string, string> = { NIFTY: "NSE:NIFTY50-INDEX", BANKNIFTY: "NSE:NIFTYBANK-INDEX", SENSEX: "BSE:SENSEX-INDEX", MIDCPNIFTY: "NSE:MIDCPNIFTY-INDEX", FINNIFTY: "NSE:FINNIFTY-INDEX" };
const CLOCK = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

/** The live tick when the stream carries this contract, else the last stored price. */
function markOf(ticks: Ticks, symbol: string | null | undefined, fallback: number | null | undefined) {
  const tick = symbol ? ticks[symbol] : undefined;
  if (tick && tick.ltp > 0) return { ltp: tick.ltp, live: true, at: tick.timestamp };
  return { ltp: fallback ?? null, live: false, at: null as string | null };
}

/** A price that flashes green/red on each change. */
function LivePrice({ value, live, at }: { value: number | null; live: boolean; at: string | null }) {
  const previous = useRef<number | null>(null);
  const [flash, setFlash] = useState("");
  useEffect(() => {
    const before = previous.current;
    previous.current = value;
    if (value == null || before == null || value === before) return;
    setFlash(value > before ? "ix-flash-up" : "ix-flash-down");
    const timer = window.setTimeout(() => setFlash(""), 600);
    return () => window.clearTimeout(timer);
  }, [value]);
  return <span className={`agent-live ${flash}`} title={live ? `live · ${at ? CLOCK.format(new Date(at)) : ""} IST` : "last stored price (not streaming)"}>
    {live && <i className="agent-live-dot" aria-hidden="true" />}{num(value)}
  </span>;
}

type RunSummary = {
  run_id: number; day: string; emitted_at: string; model_version: number | null; candidates: number; source: string;
  hold_until: string; sessions_elapsed: number; open: boolean;
  ce: { picks: number; mean_1_3d: number | null; labelled: number; mean_d1: number | null };
  pe: { picks: number; mean_1_3d: number | null; labelled: number; mean_d1: number | null };
};
type TrackerList = { run: NonNullable<Picks["run"]>; hold_until: string; sessions_elapsed: number; open: boolean; ce: Pick[]; pe: Pick[]; index: IndexTrade[];
  no_trade: string[]; summary: { legs: number; final: number; mean_1_3d: number | null; winners: number; mean_since_entry_pct: number | null } };
type View = "latest" | number;
type Tab = "LIST" | "TRACKER" | "EQUITY" | "LEARNING";
const TABS: [Tab, string][] = [["LIST", "Today's list"], ["TRACKER", "Watchlist tracker"], ["EQUITY", "Equity"], ["LEARNING", "Learning"]];
const TAB_STORE = "macd.agentTab";
const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" });
const dayLabel = (iso: string) => DAY.format(new Date(`${iso}T12:00:00+05:30`));
const FULL = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const fullTime = (iso: string | null | undefined) => (iso ? `${FULL.format(new Date(iso))} IST` : "—");
const recallTab = (): Tab => { try { const v = window.localStorage.getItem(TAB_STORE); return TABS.some(([k]) => k === v) ? v as Tab : "LIST"; } catch { return "LIST"; } };

type ChartFocus = { list: LaneChartFocus[]; index: number };
/** A pick on the lane chart: the emission as the entry marker, the entry premium as a level. */
const pickFocus = (rows: { symbol: string | null; underlying: string; strike: number | null; expiry: string | null; entry_price: number | null }[], emittedAt: string | null | undefined, day: string): LaneChartFocus[] =>
  rows.filter((r) => r.symbol).map((r) => ({
    symbol: r.symbol!, title: `${r.underlying} ${r.strike ?? ""} ${r.symbol!.slice(-2)}`,
    subtitle: `listed ${dayLabel(day)}${r.expiry ? ` · exp ${r.expiry}` : ""} · entry ₹${num(r.entry_price)}`,
    markers: emittedAt && r.entry_price ? [{ timestamp: Math.floor(Date.parse(emittedAt) / 1000), price: r.entry_price, label: "LIST", tone: "entry" as const }] : [],
    levels: r.entry_price ? [{ price: r.entry_price, label: "Entry", tone: "avg" as const }] : [],
  }));

export function AgentPage({ ticks = {} }: { ticks?: Ticks }) {
  const [tab, setTabState] = useState<Tab>(recallTab);
  const [view, setView] = useState<View>("latest");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [historic, setHistoric] = useState<Picks>();
  const [picks, setPicks] = useState<Picks>();
  const [perf, setPerf] = useState<Performance>();
  const [models, setModels] = useState<Models>();
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [focus, setFocus] = useState<ChartFocus | null>(null);
  const setTab = (next: Tab) => { setTabState(next); try { window.localStorage.setItem(TAB_STORE, next); } catch { /* not kept */ } };

  const load = useCallback(async () => {
    try {
      const [p, f, m, s, r] = await Promise.all([
        call<Picks>("/api/agent/picks"), call<Performance>("/api/agent/performance?days=90"),
        call<Models>("/api/agent/models"), call<Status>("/api/agent/status"), call<RunSummary[]>("/api/agent/runs?days=60"),
      ]);
      setPicks(p); setPerf(f); setModels(m); setStatus(s); setRuns(r); setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "request failed");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  // A past list: fetched when chosen, refreshed with the rest.
  useEffect(() => {
    if (typeof view !== "number") { setHistoric(undefined); return; }
    let live = true;
    const fetchRun = () => call<Picks>(`/api/agent/picks?run_id=${view}`).then((value) => { if (live) setHistoric(value); })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : "request failed"); });
    void fetchRun();
    const timer = setInterval(fetchRun, 30_000);
    return () => { live = false; clearInterval(timer); };
  }, [view]);

  const act = async (label: string, path: string) => {
    setBusy(label);
    try { await call(path, "POST"); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "failed"); } finally { setBusy(""); }
  };

  const champion = models?.models.find((m) => m.status === "champion");
  const shown = typeof view === "number" ? historic : picks;
  const run = shown?.run;
  const openLists = runs.filter((r, i) => r.open && runs.findIndex((x) => x.day === r.day) === i).length;
  const openChart = (rows: Parameters<typeof pickFocus>[0], at: string | null | undefined, day: string, symbol: string) => {
    const list = pickFocus(rows, at, day);
    const index = list.findIndex((row) => row.symbol === symbol);
    if (index >= 0) setFocus({ list, index });
  };
  return <div className="ledger-page agent-page">
    <div className="mp-head agent-head">
      <div>
        <b>Option agent</b>
        <span className="agent-sub">Top {models?.constants.top_n ?? 10} calls and puts for the next 1–3 sessions, as of {models?.constants.as_of ?? "14:45"} · emitted {status?.emit_at ?? "14:47"} IST each session · index trades on NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY and FINNIFTY</span>
      </div>
      <div className="agent-actions">
        {champion && <span className="agent-chip">champion v{champion.version} {champion.config.name}</span>}
        {!champion&&models?.models.some(m=>m.status==='baseline')&&<span className="agent-chip">Local paper baseline · untrained</span>}
        {runs[0] && <span className="agent-chip" title="Last emission">last list {fullTime(runs[0].emitted_at)}</span>}
        <button disabled={!!busy} onClick={() => void act("emit", "/api/agent/run")}>{busy === "emit" ? "Emitting…" : "Emit now"}</button>
        <button disabled={!!busy} onClick={() => void act("train", "/api/agent/train")}>{busy === "train" ? "Training…" : "Retrain"}</button>
      </div>
    </div>
    <nav className="agent-tabs" role="tablist" aria-label="Option agent views">{TABS.map(([key, label]) =>
      <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
        {label}{key === "TRACKER" && openLists > 0 && <span title="Lists still inside their 1–3 session hold">{openLists} open</span>}
      </button>)}</nav>
    {(error || status?.error) && <div className="agent-error">{error || status?.error}</div>}
    {tab === "LIST" && <>
      <div className="agent-list-bar">
        <div className="agent-views" role="group" aria-label="Which list">
          <button className={view === "latest" ? "active" : ""} aria-pressed={view === "latest"} onClick={() => setView("latest")}>Latest list</button>
          <select aria-label="Past lists" value={typeof view === "number" ? String(view) : ""} onChange={(e) => setView(e.target.value ? Number(e.target.value) : "latest")}>
            <option value="">Past lists…</option>
            {runs.map((r) => <option key={r.run_id} value={r.run_id}>
              {dayLabel(r.day)} · {r.source}{r.open ? ` · open D+${r.sessions_elapsed}/3` : " · closed"}
              {r.ce.mean_1_3d != null ? ` · CE ${pct(r.ce.mean_1_3d)} PE ${pct(r.pe.mean_1_3d)}` : ""}
            </option>)}
          </select>
        </div>
        {run && <span className="agent-chip">List of {dayLabel(run.day)} · emitted {fullTime(run.emitted_at)} · run #{run.run_id} · {run.candidates.toLocaleString("en-IN")} candidates · {run.model_version != null ? `model v${run.model_version}` : "no stored model"} · {run.source}</span>}
        {typeof view === "number" && run && <span className="agent-chip">{(() => { const r = runs.find((x) => x.run_id === run.run_id); return r ? (r.open ? `in its hold: D+${r.sessions_elapsed} of 3, exits by ${dayLabel(r.hold_until)}` : `hold ended ${dayLabel(r.hold_until)}: D+1/D+2/D+3 are final`) : ""; })()}</span>}
        <small className="agent-hint">click a contract for its chart</small>
      </div>
      <IndexTrades trades={shown?.index || []} ticks={ticks} onOpen={run ? (symbol) => openChart(shown?.index || [], run.emitted_at, run.day, symbol) : undefined} />
      <div className="agent-picks">
        <PickTable title="Calls (CE)" rows={shown?.ce || []} ticks={ticks} emitAt={status?.emit_at} onOpen={run ? (symbol) => openChart(shown?.ce || [], run.emitted_at, run.day, symbol) : undefined} />
        <PickTable title="Puts (PE)" rows={shown?.pe || []} ticks={ticks} emitAt={status?.emit_at} onOpen={run ? (symbol) => openChart(shown?.pe || [], run.emitted_at, run.day, symbol) : undefined} />
      </div>
      <CompareTopPanel runId={run?.run_id} />
    </>}
    {tab === "TRACKER" && <WatchlistTracker ticks={ticks} onChart={(list, index) => setFocus({ list, index })} />}
    {tab === "EQUITY" && <EquityPanel />}
    {tab === "LEARNING" && <>
      <CompareSummaryPanel />
      <div className="agent-bottom">
        <PerformancePanel perf={perf} />
        <ModelsPanel models={models} status={status} />
      </div>
    </>}
    {focus && <LaneChartModal list={focus.list} index={focus.index} onIndex={(index) => setFocus({ ...focus, index })} onClose={() => setFocus(null)} />}
  </div>;
}

function PickTable({ title, rows, ticks, emitAt, onOpen }: { title: string; rows: Pick[]; ticks: Ticks; emitAt?: string; onOpen?: (symbol: string) => void }) {
  const liveCount = rows.filter((p) => ticks[p.symbol]?.ltp).length;
  return <section className="panel agent-panel">
    <div className="panel-title">{title}{rows.length > 0 && <span className="agent-live-count">{liveCount}/{rows.length} live</span>}</div>
    {!rows.length ? <div className="agent-empty">Awaiting fresh candidates at {emitAt ?? "14:47:00"}. A local paper baseline can collect observations before a trained model is available.</div> :
      <div className="live-table-wrap"><table className="live-table agent-table">
        <thead><tr><th>#</th><th>Contract</th><th>Entry</th><th>LTP</th><th>Chg</th><th>Since entry</th><th title="Profit or loss for one lot at the current price">₹ / lot</th><th>D+1</th><th>D+2</th><th>D+3</th><th>Best / worst</th><th title="Volatility-sized: a one-sigma day moves the position about ₹1,500">Size</th><th>Why</th></tr></thead>
        <tbody>{rows.map((p) => { const mark = markOf(ticks, p.symbol, p.last_price); const m = sinceEntry(p.entry_price, mark.ltp, p.size?.lot_size); return <tr key={p.symbol} className={onOpen ? "openable-row" : ""} onClick={onOpen ? () => onOpen(p.symbol) : undefined}>
          <td>{p.rank}</td>
          <td title={`${p.symbol}${onOpen ? " · click for its chart" : ""}`}><b>{p.underlying}</b> {p.strike} <small>{p.expiry.slice(5)}</small></td>
          <td>{num(p.entry_price)}</td>
          <td><LivePrice value={mark.ltp} live={mark.live} at={mark.at || p.last_at} /></td>
          <td className={tone(m.points)}>{m.points == null ? "—" : `${m.points > 0 ? "+" : ""}${m.points.toFixed(2)}`}</td>
          <td className={tone(m.fraction)}>{pct(m.fraction)}</td>
          <td className={tone(m.perLot)}>{m.perLot == null ? "—" : rupees(m.perLot)}</td>
          <td className={tone(p.r1)}>{pct(p.r1)}</td><td className={tone(p.r2)}>{pct(p.r2)}</td><td className={tone(p.r3)}>{pct(p.r3)}</td>
          <td><span className="positive">{pct(p.max_ret, 0)}</span> / <span className="negative">{pct(p.min_ret, 0)}</span></td>
          <td title={p.size?.vol_daily != null ? `premium moves ~${(p.size.vol_daily * 100).toFixed(0)}% a day (1σ)${p.size.lot_value ? ` · 1 lot = ₹${level(p.size.lot_value)}` : ""}` : "no implied vol: fallback sizing"}>{p.size ? `₹${level(p.size.notional)}` : "—"}{p.size?.lots != null ? <small> · {p.size.lots < 1 ? "<1 lot" : `${p.size.lots.toFixed(1)} lots`}</small> : null}</td>
          <td className="agent-why" title={(p.drivers || []).map(([n, v]) => `${featureLabel(n)} ${v.toFixed(3)}`).join("\n")}>{driverText(p.drivers)}</td>
        </tr>; })}</tbody>
      </table></div>}
  </section>;
}

function PerformancePanel({ perf }: { perf?: Performance }) {
  const side = (key: "ce" | "pe", label: string) => {
    const s = perf?.summary[key];
    return <div className="agent-stat">
      <span>{label}</span>
      <b className={tone(s?.y)}>{pct(s?.y)}</b>
      <small>vs all candidates <em className={tone(s?.excess)}>{pct(s?.excess)}</em> · top-quintile {s?.top_quintile == null ? "—" : `${(s.top_quintile * 100).toFixed(0)}%`}</small>
      <small>D+1 {pct(s?.r1)} · D+2 {pct(s?.r2)} · D+3 {pct(s?.r3)}</small>
    </div>;
  };
  return <section className="panel agent-panel">
    <div className="panel-title">Tracking — mean returns per emission (the equity curves above are the account view)</div>
    <div className="agent-stats">
      {side("ce", "Calls: mean 1–3 day return")}
      {side("pe", "Puts: mean 1–3 day return")}
    </div>
    <IndexSummaryLine perf={perf} />
    <div className="live-table-wrap agent-runs"><table className="live-table">
      <thead><tr><th>Day</th><th>Model</th><th>CE mean</th><th>CE excess</th><th>PE mean</th><th>PE excess</th><th>Labelled</th></tr></thead>
      <tbody>{[...(perf?.runs || [])].reverse().map((r) => <tr key={r.run_id}>
        <td>{r.day}</td><td>v{r.model_version ?? "—"} <small>{r.source}</small></td>
        <td className={tone(r.ce.y)}>{pct(r.ce.y)}</td><td className={tone(r.ce.excess)}>{pct(r.ce.excess)}</td>
        <td className={tone(r.pe.y)}>{pct(r.pe.y)}</td><td className={tone(r.pe.excess)}>{pct(r.pe.excess)}</td>
        <td>{r.ce.labelled + r.pe.labelled}/{r.ce.picks + r.pe.picks}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

function ModelsPanel({ models, status }: { models?: Models; status?: Status }) {
  const latest = models?.models[0];
  const configs = latest ? Object.entries(latest.metrics) : [];
  const champion = models?.models.find((m) => m.status === "champion");
  const weights = champion && !champion.weights.rule
    ? Object.entries(champion.weights).sort((a, b) => Math.abs(b[1] as number) - Math.abs(a[1] as number)).slice(0, 8) : [];
  return <section className="panel agent-panel">
    <div className="panel-title">Learning loop — walk-forward validation, champion / challenger</div>
    {latest && <p className="agent-note">{latest.status==='baseline'?'Untrained cold-start paper baseline. No held-out performance or calibrated profit forecast.':`Judged on ${configs[0]?.[1].test_days??0} held-out sessions (${latest.samples.toLocaleString('en-IN')} labelled contracts).`} A challenger replaces the champion only when it beats it by {((models?.constants.promote_margin ?? 0) * 100).toFixed(1)} pts of excess return. {latest.note}</p>}
    <div className="live-table-wrap"><table className="live-table">
      <thead><tr><th>Config</th><th>Excess</th><th>CE excess (t)</th><th>PE excess (t)</th><th>IC CE / PE</th><th>Top-quintile CE / PE</th></tr></thead>
      <tbody>{configs.map(([name, m]) => <tr key={name} className={champion?.config.name === name ? "agent-champion" : ""}>
        <td>{name}{champion?.config.name === name ? " ★" : ""}</td>
        <td className={tone(m.excess)}>{pct(m.excess, 2)}</td>
        <td className={tone(m.ce_excess)}>{pct(m.ce_excess, 2)} <small>({num(m.ce_excess_t, 1)})</small></td>
        <td className={tone(m.pe_excess)}>{pct(m.pe_excess, 2)} <small>({num(m.pe_excess_t, 1)})</small></td>
        <td>{num(m.ce_ic)} / {num(m.pe_ic)}</td>
        <td>{m.ce_hit == null ? "—" : `${(m.ce_hit * 100).toFixed(0)}%`} / {m.pe_hit == null ? "—" : `${(m.pe_hit * 100).toFixed(0)}%`}</td>
      </tr>)}</tbody>
    </table></div>
    <IndexModelsTable models={models?.index_models} />
    {weights.length > 0 && <div className="agent-weights">{weights.map(([name, w]) => <span key={name} className={tone(w as number)}>{featureLabel(name)} {(w as number) > 0 ? "+" : ""}{(w as number).toFixed(3)}</span>)}</div>}
    <p className="agent-note">
      {status?.learned_day ? `Last learned ${status.learned_day}` : "Learns after the nightly run (≥16:05)"}
      {status?.backfill ? ` · completed bars for ${String(status.backfill.symbols ?? 0)} contracts` : ""}
      {" · versions: "}{(models?.models || []).slice(0, 6).map((m) => `v${m.version} ${m.status}`).join(", ") || "none yet"}
    </p>
  </section>;
}

const INDEX_ORDER = ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY"];   // owner, 4 Nov 2026: index trades on these five only
const level = (value: number | null | undefined, digits = 0) => value == null ? "—" : value.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });

function IndexTrades({ trades, ticks, onOpen }: { trades: IndexTrade[]; ticks: Ticks; onOpen?: (symbol: string) => void }) {
  if (!trades.length) return null;
  // Lists from before 4 Nov 2026 also judged NIFTYNXT50, BANKEX, FOCIT and NIFTYFPI: their no-trade cards are not shown.
  const rank = (root: string) => (INDEX_ORDER.includes(root) ? INDEX_ORDER.indexOf(root) : INDEX_ORDER.length);
  const ordered = trades.filter((t) => t.side || INDEX_ORDER.includes(t.underlying)).sort((a, b) => rank(a.underlying) - rank(b.underlying));
  return <section className="panel agent-panel">
    <div className="panel-title">Index trades — side, contract and plan for the next 1–3 sessions</div>
    <div className="agent-index-grid">{ordered.map((t) => <IndexCard key={t.underlying} trade={t} ticks={ticks} onOpen={onOpen} />)}</div>
  </section>;
}

function IndexCard({ trade: t, ticks, onOpen }: { trade: IndexTrade; ticks: Ticks; onOpen?: (symbol: string) => void }) {
  const prediction = indexPrediction(t.prediction_kind, t.predicted, t.conviction);
  if (!t.side) {
    return <div className="agent-index-card agent-index-none">
      <div className="agent-index-head"><b>{t.underlying}</b><span className="agent-index-side">No trade</span></div>
      <p>{t.reason}</p>
      {t.predicted != null && <small>{prediction.label} {prediction.value} · {prediction.detail}</small>}
    </div>;
  }
  const call = t.side > 0;
  const capital = t.entry_price != null && t.lot ? t.entry_price * t.lot : null;
  const mark = markOf(ticks, t.symbol, t.last_price);
  const m = sinceEntry(t.entry_price, mark.ltp, t.lot);
  const spot = markOf(ticks, INDEX_SPOT[t.underlying], t.spot_now);
  const toStop = spot.ltp != null && t.stop_spot != null ? (spot.ltp - t.stop_spot) * t.side : null;   // > 0: not yet hit (a put's stop is above the index)
  const toTarget = spot.ltp != null && t.target_spot != null ? (t.target_spot - spot.ltp) * t.side : null;
  return <div className={`agent-index-card ${call ? "agent-index-call" : "agent-index-put"}`}>
    <div className="agent-index-head">
      <b>{t.underlying}</b>
      <span className={`agent-index-side ${call ? "positive" : "negative"}`}>Buy {call ? "CE" : "PE"}</span>
      {onOpen && t.symbol
        ? <button className="agent-index-contract agent-link" title={`${t.symbol} · open its chart`} onClick={() => onOpen(t.symbol!)}>{t.strike} {call ? "CE" : "PE"} · exp {t.expiry?.slice(5)} ↗</button>
        : <span className="agent-index-contract" title={t.symbol || ""}>{t.strike} {call ? "CE" : "PE"} · exp {t.expiry?.slice(5)}</span>}
      {t.series === "next" && <span className="agent-index-series" title={`The front series (${t.front_expiry}) expires inside the 3-session hold, so the next series is used`}>next series · front {t.front_expiry?.slice(5)} inside hold</span>}
    </div>
    <div className="agent-index-rows">
      <span>Entry</span><b>{level(t.entry_price, 2)}</b>
      <span>Lot / capital</span><b>{t.lot ? `${t.lot} / ₹${level(capital)}` : "—"}</b>
      <span>Sized</span><b>{t.size ? (t.size.lots ? `${t.size.lots} lot${t.size.lots > 1 ? "s" : ""} · ₹${level(t.size.capital)}` : "0 lots (1 lot risks more than ₹4k)") : "—"}{t.size?.risk_at_stop ? <small> risk ₹{level(t.size.risk_at_stop)}</small> : null}</b>
      <span>Spot at entry</span><b>{level(t.spot)} <small>ATR {level(t.atr)}</small></b>
      <span>Stop (index)</span><b className="negative">{level(t.stop_spot)} <small>≈ ₹{level(t.premium_stop, 1)}</small></b>
      <span>Target (index)</span><b className="positive">{level(t.target_spot)} <small>≈ ₹{level(t.premium_target, 1)}</small></b>
      <span>{prediction.label}</span><b>{prediction.value} <small>{prediction.detail}</small></b>
      <span>LTP</span><b><LivePrice value={mark.ltp} live={mark.live} at={mark.at} /> <small className={tone(m.points)}>{m.points == null ? "" : `${m.points > 0 ? "+" : ""}${m.points.toFixed(2)}`}</small></b>
      <span>Since entry</span><b className={tone(m.fraction)}>{pct(m.fraction)} <small className={tone(m.perLot)}>{m.perLot == null ? "" : `${rupees(m.perLot)} / lot`}</small></b>
      <span>Index now</span><b><LivePrice value={spot.ltp} live={spot.live} at={spot.at} /> <small>{toStop == null ? "" : `${Math.round(Math.abs(toStop))} pts ${toStop < 0 ? "past" : "to"} stop · ${Math.round(Math.abs(toTarget ?? 0))} to target`}</small></b>
      <span>D+1 / D+2 / D+3</span><b><em className={tone(t.r1)}>{pct(t.r1)}</em> / <em className={tone(t.r2)}>{pct(t.r2)}</em> / <em className={tone(t.r3)}>{pct(t.r3)}</em></b>
      <span>Plan status</span><b>{t.spot_path || "—"}</b>
    </div>
    <small className="agent-why" title={(t.drivers || []).map(([n, v]) => `${featureLabel(n)} ${v.toFixed(3)}`).join("\n")}>{driverText(t.drivers, 3)}</small>
  </div>;
}

function IndexSummaryLine({ perf }: { perf?: Performance }) {
  const s = perf?.index?.summary;
  if (!s || !s.opportunities) return null;
  const paths = perf?.index?.paths || {};
  return <p className="agent-note">
    <b>Index trades:</b> {s.trades} taken ({s.calls} CE, {s.puts} PE), {s.no_trade} no-trade · mean 1–3d <em className={tone(s.mean_return)}>{pct(s.mean_return)}</em>
    {" "}· win {s.win_rate == null ? "—" : `${(s.win_rate * 100).toFixed(0)}%`} · vs coin-flip ATM <em className={tone(s.mean_excess)}>{pct(s.mean_excess)}</em>
    {s.excess_t != null ? ` (t ${s.excess_t.toFixed(2)})` : ""} · plan: {Object.entries(paths).map(([k, v]) => `${k} ${v}`).join(", ") || "—"}
  </p>;
}

function IndexModelsTable({ models }: { models?: IndexModel[] }) {
  const latest = models?.[0];
  if (!latest) return null;
  const champion = models?.find((m) => m.status === "champion");
  return <>
    <p className="agent-note">Index model: champion {champion ? `v${champion.version} ${champion.config.name}` : "none"} · {latest.note}</p>
    <div className="live-table-wrap"><table className="live-table">
      <thead><tr><th>Index config</th><th>Trades</th><th>No-trade</th><th>Mean 1–3d</th><th>Win</th><th>vs coin flip (t)</th><th>CE / PE</th></tr></thead>
      <tbody>{Object.entries(latest.metrics).map(([name, m]) => <tr key={name} className={champion?.config.name === name ? "agent-champion" : ""}>
        <td>{name}{champion?.config.name === name ? " ★" : ""}</td><td>{m.trades}</td><td>{m.no_trade}</td>
        <td className={tone(m.mean_return)}>{pct(m.mean_return)}</td>
        <td>{m.win_rate == null ? "—" : `${(m.win_rate * 100).toFixed(0)}%`}</td>
        <td className={tone(m.mean_excess)}>{pct(m.mean_excess)} <small>({num(m.excess_t, 1)})</small></td>
        <td>{m.calls} / {m.puts}</td>
      </tr>)}</tbody>
    </table></div>
  </>;
}

type EquityPoint = { day: string; equity: number; realised: number; deployed: number };
type EquityStats = { trades: number; closed?: number; open?: number; final?: number; max_drawdown?: number; max_deployed?: number; return_on_deployed?: number; win_rate?: number | null; profit_factor?: number | null; costs?: number; daily_sharpe?: number | null; stale_exits?: number; exits?: Record<string, number> };
type EquityBook = { points: EquityPoint[]; stats: EquityStats };
type Equity = { since: string; as_of: string; notional: number; books: Record<string, EquityBook> };

// Book colours: the validated categorical slots of LaneEquityChart (blue, orange) and a muted benchmark grey; the
// main book's area turns green above zero and red below, so the sign reads without the axis.
const BOOK = { main: "#3987e5", alt: "#d95926", bench: "#8796aa" } as const;
type BookRole = keyof typeof BOOK;
const EQUITY_CHARTS: { title: string; note: string; lines: { book: string; label: string; role: BookRole }[] }[] = [
  { title: "Top-10 calls", note: "vol-sized, ₹3 L cap · held to the 3rd close", lines: [
    { book: "ce_sized", label: "Sized", role: "main" }, { book: "ce", label: "Flat ₹10k", role: "alt" },
    { book: "ce_all_sized", label: "All calls (same ₹)", role: "bench" }] },
  { title: "Top-10 puts", note: "vol-sized, ₹3 L cap · held to the 3rd close", lines: [
    { book: "pe_sized", label: "Sized", role: "main" }, { book: "pe", label: "Flat ₹10k", role: "alt" },
    { book: "pe_all_sized", label: "All puts (same ₹)", role: "bench" }] },
  { title: "Index trades", note: "₹4k risk at stop, ₹2 L cap · plan exit", lines: [
    { book: "index_sized", label: "Sized", role: "main" }, { book: "index_plan", label: "1 lot", role: "alt" },
    { book: "index_coin", label: "Coin-flip ATM (1 lot)", role: "bench" }] },
];
const compact = (value: number) => {
  const a = Math.abs(value), sign = value < 0 ? "−" : value > 0 ? "+" : "";
  return a >= 100_000 ? `${sign}₹${(a / 100_000).toFixed(2)} L` : a >= 1000 ? `${sign}₹${(a / 1000).toFixed(1)}k` : `${sign}₹${a.toFixed(0)}`;
};
const SESSION = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
const sessionLabel = (day: string) => SESSION.format(new Date(`${day}T12:00:00+05:30`));
const timeDay = (time: Time) => typeof time === "string" ? time
  : typeof time === "number" ? new Date(time * 1000).toISOString().slice(0, 10)
  : `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;

function EquityPanel() {
  const [equity, setEquity] = useState<Equity>();
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    const load = () => call<Equity>("/api/agent/equity?days=45").then((value) => { if (live) { setEquity(value); setError(""); } })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : "equity failed"); });
    void load();
    const timer = setInterval(load, 60_000);
    return () => { live = false; clearInterval(timer); };
  }, []);
  return <section className="panel agent-panel">
    <div className="panel-title">Equity curves <small>rupee P&amp;L marked at every session close, net of costs{equity ? ` · ${dayLabel(equity.since)} → ${dayLabel(equity.as_of)}` : ""} · hover for the session's values</small></div>
    {error && <div className="agent-empty">{error}</div>}
    <div className="agent-equity-grid">{EQUITY_CHARTS.map((spec) => <EquityChart key={spec.title} spec={spec} books={equity?.books} />)}</div>
  </section>;
}

function EquityChart({ spec, books }: { spec: typeof EQUITY_CHARTS[number]; books?: Record<string, EquityBook> }) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const byDay = useMemo(() => {
    const out = new Map<string, Record<string, number>>();
    for (const line of spec.lines) for (const point of books?.[line.book]?.points || []) {
      out.set(point.day, { ...(out.get(point.day) || {}), [line.book]: point.equity });
    }
    return out;
  }, [books, spec]);
  const lastDay = useMemo(() => [...byDay.keys()].sort().pop() ?? null, [byDay]);
  useEffect(() => {
    if (!element.current) return;
    const api = createChart(element.current, {
      width: element.current.clientWidth, height: element.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#090d14" }, textColor: "#7d8ea6", fontSize: 10, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: "#151d29" } },
      rightPriceScale: { borderVisible: false, minimumWidth: 58, scaleMargins: { top: 0.18, bottom: 0.08 } },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
      localization: { locale: "en-IN", priceFormatter: compact, timeFormatter: (time: Time) => sessionLabel(timeDay(time)) },
      crosshair: { vertLine: { color: "#4a5a72", width: 1, style: LineStyle.Solid, labelBackgroundColor: "#1d2838" },
        horzLine: { color: "#4a5a72", style: LineStyle.Dotted, labelBackgroundColor: "#1d2838" } },
      handleScroll: false, handleScale: false,
    });
    chart.current = api;
    const onMove = (param: MouseEventParams<Time>) => setHover(param.time === undefined ? null : timeDay(param.time));
    api.subscribeCrosshairMove(onMove);
    const observer = new ResizeObserver(() => api.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => { observer.disconnect(); api.unsubscribeCrosshairMove(onMove); chart.current = null; api.remove(); };
  }, []);
  useEffect(() => {
    const api = chart.current;
    if (!api || !books) return;
    // Benchmarks first so the main book draws on top.
    const series = [...spec.lines].reverse().map((line) => {
      const data = (books[line.book]?.points || []).map((p) => ({ time: p.day as Time, value: p.equity }));
      if (line.role === "main") {
        const s = api.addSeries(BaselineSeries, { baseValue: { type: "price", price: 0 }, lineWidth: 2,
          topLineColor: "#22c893", topFillColor1: "rgba(34,200,147,0.28)", topFillColor2: "rgba(34,200,147,0.02)",
          bottomLineColor: "#f15b6c", bottomFillColor1: "rgba(241,91,108,0.02)", bottomFillColor2: "rgba(241,91,108,0.28)",
          priceLineVisible: false, lastValueVisible: true, crosshairMarkerRadius: 4, crosshairMarkerBorderColor: "#090d14" });
        s.setData(data);
        s.createPriceLine({ price: 0, color: "#56657c", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
        return s;
      }
      const s = api.addSeries(LineSeries, { color: BOOK[line.role], lineWidth: line.role === "bench" ? 1 : 2,
        lineStyle: line.role === "bench" ? LineStyle.Dashed : LineStyle.Solid, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerRadius: 3, crosshairMarkerBorderColor: "#090d14" });
      s.setData(data);
      return s;
    });
    api.timeScale().fitContent();
    return () => {
      // The chart effect disposes the API first on unmount; updates still remove their live series.
      if (chart.current !== api) return;
      for (const s of series) api.removeSeries(s);
    };
  }, [books, spec]);
  const main = books?.[spec.lines[0].book]?.stats;
  const bench = books?.[spec.lines[spec.lines.length - 1].book]?.stats;
  const day = hover && byDay.has(hover) ? hover : lastDay;
  const values = day ? byDay.get(day) : undefined;
  const vsBench = main?.final != null && bench?.final != null ? main.final - bench.final : null;
  return <div className="agent-equity">
    <div className="agent-equity-head">
      <b>{spec.title}</b><small>{spec.note}</small>
      <span className={`agent-equity-final ${tone(main?.final)}`}>{main?.final == null ? "—" : compact(main.final)}</span>
    </div>
    <div className="agent-equity-legend" aria-live="off">
      <span className="agent-equity-day">{day ? sessionLabel(day) : "no sessions yet"}</span>
      {spec.lines.map((line) => {
        const value = values?.[line.book];
        return <span key={line.book}><s className={`swatch-${line.role}`} style={{ background: line.role === "main" ? undefined : BOOK[line.role] }} />{line.label}
          <i className={tone(value)}>{value == null ? "—" : compact(value)}</i></span>;
      })}
    </div>
    <div className="agent-equity-chart" ref={element} aria-label={`${spec.title}: P&L by session, ${spec.lines.map((l) => l.label).join(", ")}`} />
    <div className="agent-equity-stats">
      <span>vs benchmark <b className={tone(vsBench)}>{vsBench == null ? "—" : compact(vsBench)}</b></span>
      <span>Max drawdown <b className="negative">{main?.max_drawdown == null ? "—" : compact(main.max_drawdown)}</b></span>
      <span>Peak capital <b>{main?.max_deployed == null ? "—" : compact(main.max_deployed).replace("+", "")}</b></span>
      <span>Win rate <b>{main?.win_rate == null ? "—" : `${(main.win_rate * 100).toFixed(0)}%`}</b></span>
      <span>Profit factor <b>{main?.profit_factor == null ? "—" : main.profit_factor.toFixed(2)}</b></span>
      <span>Trades <b>{main ? `${main.closed ?? 0} closed · ${main.open ?? 0} open` : "—"}</b></span>
      <span>Costs <b>{main?.costs == null ? "—" : compact(main.costs).replace("+", "")}</b></span>
      <span>Daily Sharpe <b>{main?.daily_sharpe == null ? "—" : main.daily_sharpe.toFixed(2)}</b></span>
    </div>
    {main?.exits && <small className="agent-equity-exits">exits: {Object.entries(main.exits).map(([k, v]) => `${k} ${v}`).join(" · ")}{main.stale_exits ? " (last known price = contract expired, no later bars)" : ""}</small>}
  </div>;
}

type Leg = { kind: "CE" | "PE" | "INDEX"; rank: number | null; symbol: string; underlying: string; strike: number | null; expiry: string | null;
  entry_price: number | null; last_price: number | null | undefined; lot: number | null | undefined;
  r1: number | null; r2: number | null; r3: number | null; max_ret: number | null; min_ret: number | null; y?: number | null; plan?: string | null };
type Status3 = "OPEN" | "CLOSED" | "ALL";
type KindFilter = "ALL" | "CE" | "PE" | "INDEX";
const legsOf = (list: TrackerList): Leg[] => [
  ...list.index.map((t): Leg => ({ kind: "INDEX", rank: null, symbol: t.symbol!, underlying: t.underlying, strike: t.strike, expiry: t.expiry,
    entry_price: t.entry_price, last_price: t.last_price, lot: t.lot, r1: t.r1, r2: t.r2, r3: t.r3, max_ret: t.max_ret, min_ret: t.min_ret, y: t.y,
    plan: t.spot_path })),
  ...[...list.ce, ...list.pe].map((p): Leg => ({ kind: p.symbol.endsWith("CE") ? "CE" : "PE", rank: p.rank, symbol: p.symbol, underlying: p.underlying,
    strike: p.strike, expiry: p.expiry, entry_price: p.entry_price, last_price: p.last_price, lot: p.size?.lot_size,
    r1: p.r1, r2: p.r2, r3: p.r3, max_ret: p.max_ret, min_ret: p.min_ret, y: p.y })),
];
const mean = (values: number[]) => (values.length ? values.reduce((a, b) => a + b, 0) / values.length : null);

/** Every emitted list and its follow-up: each leg's entry, latest mark, D+1..D+3 closes and where the list stands. */
function WatchlistTracker({ ticks, onChart }: { ticks: Ticks; onChart: (list: LaneChartFocus[], index: number) => void }) {
  const [days, setDays] = useState(30);
  const [status, setStatus] = useState<Status3>("ALL");
  const [kind, setKind] = useState<KindFilter>("ALL");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<{ lists: TrackerList[] }>();
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  useEffect(() => {
    let live = true;
    const load = () => call<{ lists: TrackerList[] }>(`/api/agent/tracker?days=${days}`).then((value) => { if (live) { setData(value); setError(""); } })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : "tracker failed"); });
    void load();
    const timer = setInterval(load, 30_000);
    return () => { live = false; clearInterval(timer); };
  }, [days]);
  const lists = (data?.lists || []).filter((l) => status === "ALL" || (status === "OPEN") === l.open);
  const needle = query.trim().toUpperCase();
  const allOpen = (data?.lists || []).filter((l) => l.open);
  const openMarks = allOpen.flatMap(legsOf).map((leg) => sinceEntry(leg.entry_price, markOf(ticks, leg.symbol, leg.last_price).ltp).fraction).filter((v): v is number => v != null);
  const closed = (data?.lists || []).filter((l) => !l.open);
  const closedLegs = closed.flatMap(legsOf).filter((leg) => leg.y != null);
  return <>
    <section className="panel agent-panel agent-tracker-summary">
      <div className="agent-tracker-cards">
        <div><small>Open lists</small><b>{allOpen.length}</b><span>{allOpen.reduce((n, l) => n + l.summary.legs, 0)} legs in their 1–3 session hold</span></div>
        <div><small>Open legs since entry</small><b className={tone(mean(openMarks))}>{pct(mean(openMarks))}</b><span>{openMarks.filter((v) => v > 0).length} of {openMarks.length} up · live where streamed</span></div>
        <div><small>Closed lists ({closed.length})</small><b className={tone(mean(closedLegs.map((l) => l.y!)))}>{pct(mean(closedLegs.map((l) => l.y!)))}</b><span>mean of D+1..D+3 · {closedLegs.filter((l) => l.y! > 0).length} of {closedLegs.length} legs positive</span></div>
        <div><small>Closed: calls / puts / index</small><b>{(["CE", "PE", "INDEX"] as const).map((k) => pct(mean(closedLegs.filter((l) => l.kind === k).map((l) => l.y!)))).join(" / ")}</b><span>a short sample is not proven either way</span></div>
      </div>
    </section>
    <div className="agent-tracker-bar">
      <div className="agent-views" role="group" aria-label="Status">{(["ALL", "OPEN", "CLOSED"] as const).map((key) =>
        <button key={key} className={status === key ? "active" : ""} aria-pressed={status === key} onClick={() => setStatus(key)}>{key === "ALL" ? "All lists" : key === "OPEN" ? "Open" : "Closed"}</button>)}</div>
      <div className="agent-views" role="group" aria-label="Leg kind">{(["ALL", "CE", "PE", "INDEX"] as const).map((key) =>
        <button key={key} className={kind === key ? "active" : ""} aria-pressed={kind === key} onClick={() => setKind(key)}>{key === "ALL" ? "All legs" : key === "INDEX" ? "Index trades" : key}</button>)}</div>
      <div className="agent-views" role="group" aria-label="Window">{[10, 30, 60].map((value) =>
        <button key={value} className={days === value ? "active" : ""} aria-pressed={days === value} onClick={() => setDays(value)}>{value} days</button>)}</div>
      <input className="heatmap-search" placeholder="Filter underlying" value={query} onChange={(event) => setQuery(event.target.value)} />
      <small className="agent-hint">open lists expand by default · click a leg for its chart</small>
    </div>
    {error && <div className="agent-error">{error}</div>}
    {data && !lists.length && <section className="panel agent-panel"><div className="agent-empty">No {status === "ALL" ? "" : status.toLowerCase() + " "}lists in the last {days} days.</div></section>}
    {lists.map((list) => {
      const legs = legsOf(list).filter((leg) => (kind === "ALL" || leg.kind === kind) && (!needle || leg.underlying.includes(needle)));
      const marks = legsOf(list).map((leg) => sinceEntry(leg.entry_price, markOf(ticks, leg.symbol, leg.last_price).ltp).fraction).filter((v): v is number => v != null);
      const open = expanded[list.run.run_id] ?? list.open;
      const focus = pickFocus(legs, list.run.emitted_at, list.run.day);
      return <section key={list.run.run_id} className={`panel agent-panel agent-track ${list.open ? "is-open" : "is-closed"}`}>
        <button className="agent-track-head" aria-expanded={open} onClick={() => setExpanded({ ...expanded, [list.run.run_id]: !open })}>
          <span className="agent-track-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
          <b>List of {sessionLabel(list.run.day)}</b>
          <span className={`agent-track-status ${list.open ? "open" : "closed"}`}>{list.open ? (list.sessions_elapsed === 0 ? "Entered today" : `Open · D+${list.sessions_elapsed} of 3`) : "Closed"}</span>
          <small>emitted {fullTime(list.run.emitted_at)} · {list.open ? "exits by" : "hold ended"} {sessionLabel(list.hold_until)} · {list.run.source} · model v{list.run.model_version ?? "—"}</small>
          <span className="agent-track-figures">
            <span>{list.summary.legs} legs{list.no_trade.filter((root) => INDEX_ORDER.includes(root)).length ? ` · no index trade: ${list.no_trade.filter((root) => INDEX_ORDER.includes(root)).join(", ")}` : ""}</span>
            <span>since entry <em className={tone(mean(marks))}>{pct(mean(marks))}</em></span>
            <span>1–3d <em className={tone(list.summary.mean_1_3d)}>{pct(list.summary.mean_1_3d)}</em>{list.summary.final < list.summary.legs ? <small> ({list.summary.final}/{list.summary.legs} final)</small> : null}</span>
            {list.summary.final > 0 && <span>{list.summary.winners}/{list.summary.final} up</span>}
          </span>
        </button>
        {open && <div className="live-table-wrap"><table className="live-table agent-table agent-track-table">
          <thead><tr><th>Leg</th><th>Contract</th><th>Entry</th><th>LTP</th><th>Since entry</th><th title="Profit or loss for one lot at the current price">₹ / lot</th><th>D+1</th><th>D+2</th><th>D+3</th><th>Best / worst</th><th>Outcome</th></tr></thead>
          <tbody>{legs.map((leg) => {
            const mark = markOf(ticks, leg.symbol, leg.last_price);
            const m = sinceEntry(leg.entry_price, mark.ltp, leg.lot ?? undefined);
            const done = leg.y != null;
            return <tr key={leg.symbol} className="openable-row" onClick={() => onChart(focus, focus.findIndex((row) => row.symbol === leg.symbol))}>
              <td><span className={`agent-leg agent-leg-${leg.kind.toLowerCase()}`}>{leg.kind === "INDEX" ? "IDX" : leg.kind}</span>{leg.rank != null && <small> #{leg.rank}</small>}</td>
              <td title={leg.symbol}><b>{leg.underlying}</b> {leg.strike} {leg.symbol.slice(-2)} <small>{leg.expiry?.slice(5)}</small></td>
              <td>{num(leg.entry_price)}</td>
              <td><LivePrice value={mark.ltp} live={mark.live} at={mark.at} /></td>
              <td className={tone(m.fraction)}>{pct(m.fraction)}</td>
              <td className={tone(m.perLot)}>{m.perLot == null ? "—" : rupees(m.perLot)}</td>
              <td className={tone(leg.r1)}>{pct(leg.r1)}</td><td className={tone(leg.r2)}>{pct(leg.r2)}</td><td className={tone(leg.r3)}>{pct(leg.r3)}</td>
              <td><span className="positive">{pct(leg.max_ret, 0)}</span> / <span className="negative">{pct(leg.min_ret, 0)}</span></td>
              <td>{done ? <span className={`agent-outcome ${leg.y! > 0 ? "win" : "loss"}`}>{pct(leg.y)} {leg.y! > 0 ? "✓" : "✗"}</span>
                : <small>{list.open ? (list.sessions_elapsed === 0 ? "entered today" : `in hold · D+${list.sessions_elapsed} of 3`) : "awaiting closes"}</small>}{leg.plan && leg.plan !== "open" ? <small> · plan: {leg.plan}</small> : null}</td>
            </tr>;
          })}</tbody>
        </table>{!legs.length && <div className="agent-empty">No legs match the filter.</div>}</div>}
      </section>;
    })}
  </>;
}


type CompareRow = { symbol: string; underlying: string; strike: number; expiry: string; ret: number; rank: number; percentile: number; final: boolean; picked?: boolean };
type CompareSide = { universe: number; picks: CompareRow[]; actual_top: CompareRow[]; overlap: number; picks_mean: number | null; top_mean: number | null;
  universe_mean: number | null; universe_median: number | null; capture: number | null; picks_percentile: number | null; top_quintile_hits: number; final: boolean };
type CompareIndex = { underlying: string; side: number; symbol: string | null; ret: number | null; rank: number | null; of: number;
  best_ce: CompareRow | null; best_pe: CompareRow | null };
type Compare = { run: { run_id: number; day: string; source: string } | null; horizon: string; horizon_label: string; top: number; ce: CompareSide; pe: CompareSide; index: CompareIndex[] };
type CompareSummary = { horizon_label: string; rows: { run_id: number; day: string; source: string; ce: CompareSide; pe: CompareSide }[];
  summary: Record<string, { lists: number; overlap: number | null; picks_percentile: number | null; capture: number | null; picks_mean: number | null; top_mean: number | null; universe_mean: number | null }> };

const HORIZON_BUTTONS: [string, string][] = [["y", "1–3d avg"], ["r1", "D+1"], ["r2", "D+2"], ["r3", "D+3"], ["live", "Latest"]];
const short = (row: { underlying: string; strike: number; symbol: string }) => `${row.underlying} ${row.strike} ${row.symbol.slice(-2)}`;
const pctile = (v: number | null | undefined) => v == null ? "—" : `${Math.round(v * 100)}th`;

/** One list against the actual top performers among the same day's candidates. */
function CompareTopPanel({ runId }: { runId: number | undefined }) {
  const [horizon, setHorizon] = useState("y");
  const [data, setData] = useState<Compare>();
  const [error, setError] = useState("");
  useEffect(() => {
    if (runId == null) return;
    let live = true;
    const load = () => call<Compare>(`/api/agent/compare?run_id=${runId}&horizon=${horizon}`)
      .then((value) => { if (live) { setData(value); setError(""); } })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : "compare failed"); });
    void load();
    const timer = setInterval(load, 60_000);
    return () => { live = false; clearInterval(timer); };
  }, [runId, horizon]);
  const side = (key: "ce" | "pe", label: string) => {
    const s = data?.[key];
    if (!s) return null;
    return <div className="agent-compare-side">
      <div className="agent-compare-metrics">
        <b>{label}</b>
        <span>in actual top {data!.top}: <em className={s.overlap ? "positive" : ""}>{s.overlap}/{s.picks.length}</em></span>
        <span>picks' percentile <em>{pctile(s.picks_percentile)}</em></span>
        <span>capture <em className={tone(s.capture)}>{s.capture == null ? "—" : `${Math.round(s.capture * 100)}%`}</em></span>
        <span>picks <em className={tone(s.picks_mean)}>{pct(s.picks_mean)}</em> · actual top <em className="positive">{pct(s.top_mean)}</em> · all {s.universe} <em className={tone(s.universe_mean)}>{pct(s.universe_mean)}</em></span>
        {!s.final && <small>so far — not all outcomes final</small>}
      </div>
      <div className="agent-compare-tables">
        <table className="live-table"><thead><tr><th>Agent pick</th><th>Return</th><th>Actual rank</th></tr></thead>
          <tbody>{s.picks.map((r) => <tr key={r.symbol}><td title={r.symbol}>{short(r)}</td><td className={tone(r.ret)}>{pct(r.ret)}</td>
            <td className={r.rank <= data!.top ? "positive" : r.percentile < 0.5 ? "negative" : ""}>{r.rank} / {s.universe}</td></tr>)}</tbody></table>
        <table className="live-table"><thead><tr><th>Actual top {data!.top}</th><th>Return</th><th>Picked</th></tr></thead>
          <tbody>{s.actual_top.map((r) => <tr key={r.symbol}><td title={r.symbol}>{r.rank}. {short(r)}</td><td className="positive">{pct(r.ret)}</td>
            <td>{r.picked ? "✓" : ""}</td></tr>)}</tbody></table>
      </div>
    </div>;
  };
  return <section className="panel agent-panel">
    <div className="panel-title">Agent vs actual top {data?.top ?? 10} — same day's candidates, ranked by {data?.horizon_label ?? "outcome"}
      <span className="agent-views">{HORIZON_BUTTONS.map(([key, label]) => <button key={key} className={horizon === key ? "active" : ""} onClick={() => setHorizon(key)}>{label}</button>)}</span>
    </div>
    {error && <div className="agent-empty">{error}</div>}
    <div className="agent-compare">{side("ce", "Calls")}{side("pe", "Puts")}</div>
    {data?.index?.length ? <div className="agent-compare-index">{data.index.map((t) => <span key={t.underlying}>
      <b>{t.underlying}</b> {t.side ? <>{t.side > 0 ? "CE" : "PE"} <em className={tone(t.ret)}>{pct(t.ret)}</em> rank {t.rank ?? "—"}/{t.of}</> : "no trade"}
      <small> · best CE {t.best_ce ? `${short(t.best_ce)} ${pct(t.best_ce.ret)}` : "—"} · best PE {t.best_pe ? `${short(t.best_pe)} ${pct(t.best_pe.ret)}` : "—"}</small>
    </span>)}</div> : null}
  </section>;
}

/** Every list's overlap with the actual top 10, over time. */
function CompareSummaryPanel() {
  const [data, setData] = useState<CompareSummary>();
  useEffect(() => {
    let live = true;
    const load = () => call<CompareSummary>("/api/agent/compare/summary?days=60&horizon=y").then((v) => { if (live) setData(v); }).catch(() => undefined);
    void load();
    const timer = setInterval(load, 120_000);
    return () => { live = false; clearInterval(timer); };
  }, []);
  if (!data) return null;
  const cell = (s: CompareSide) => <>
    <td className={s.overlap ? "positive" : ""}>{s.overlap}/10</td><td>{pctile(s.picks_percentile)}</td>
    <td className={tone(s.capture)}>{s.capture == null ? "—" : `${Math.round(s.capture * 100)}%`}{s.final ? "" : "*"}</td></>;
  return <section className="panel agent-panel">
    <div className="panel-title">Every list vs the actual top 10 (1–3 day average) · * = outcome not final yet</div>
    <div className="agent-stats">{(["ce", "pe"] as const).map((key) => { const m = data.summary[key]; return <div key={key} className="agent-stat">
      <span>{key === "ce" ? "Calls" : "Puts"}: average over {m?.lists ?? 0} final lists</span>
      <b>{m?.overlap == null ? "—" : `${m.overlap.toFixed(1)}/10`} in top 10</b>
      <small>picks' percentile {pctile(m?.picks_percentile)} · capture {m?.capture == null ? "—" : `${Math.round(m.capture * 100)}%`} · picks {pct(m?.picks_mean)} vs top {pct(m?.top_mean)} vs all {pct(m?.universe_mean)}</small>
    </div>; })}</div>
    <div className="live-table-wrap agent-runs"><table className="live-table">
      <thead><tr><th>List</th><th>CE in top 10</th><th>CE pctile</th><th>CE capture</th><th>PE in top 10</th><th>PE pctile</th><th>PE capture</th></tr></thead>
      <tbody>{[...data.rows].reverse().map((r) => <tr key={r.run_id}><td>{dayLabel(r.day)} <small>{r.source}</small></td>{cell(r.ce)}{cell(r.pe)}</tr>)}</tbody>
    </table></div>
  </section>;
}
