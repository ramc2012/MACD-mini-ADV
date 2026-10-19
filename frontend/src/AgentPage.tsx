import { useCallback, useEffect, useRef, useState } from "react";
import { ColorType, createChart, LineSeries, LineStyle, type IChartApi, type Time } from "lightweight-charts";
import { driverText, featureLabel, indexPrediction, pct, rupees, sinceEntry, tone } from "./agentMath";
import type { Tick } from "./types";
import { API_TOKEN, API_URL } from "./runtime";

type Pick = {
  rank: number; symbol: string; underlying: string; strike: number; expiry: string; entry_price: number; score: number;
  drivers: [string, number][]; last_price: number | null; last_at: string | null; since_entry_pct: number | null;
  r1: number | null; r2: number | null; r3: number | null; max_ret: number | null; min_ret: number | null;
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
  prediction_kind?: "scorecard" | "return";
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
const INDEX_SPOT: Record<string, string> = { NIFTY: "NSE:NIFTY50-INDEX", BANKNIFTY: "NSE:NIFTYBANK-INDEX", SENSEX: "BSE:SENSEX-INDEX", MIDCPNIFTY: "NSE:MIDCPNIFTY-INDEX" };
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
type ActiveList = { run: Picks["run"]; hold_until: string; sessions_elapsed: number; ce: Pick[]; pe: Pick[]; index: IndexTrade[] };
type View = "latest" | "active" | number;
const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" });
const dayLabel = (iso: string) => DAY.format(new Date(`${iso}T12:00:00+05:30`));

export function AgentPage({ ticks = {} }: { ticks?: Ticks }) {
  const [view, setView] = useState<View>("latest");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [active, setActive] = useState<ActiveList[]>([]);
  const [historic, setHistoric] = useState<Picks>();
  const [picks, setPicks] = useState<Picks>();
  const [perf, setPerf] = useState<Performance>();
  const [models, setModels] = useState<Models>();
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const [p, f, m, s, r, a] = await Promise.all([
        call<Picks>("/api/agent/picks"), call<Performance>("/api/agent/performance?days=90"),
        call<Models>("/api/agent/models"), call<Status>("/api/agent/status"),
        call<RunSummary[]>("/api/agent/runs?days=60"), call<{ lists: ActiveList[] }>("/api/agent/active"),
      ]);
      setPicks(p); setPerf(f); setModels(m); setStatus(s); setRuns(r); setActive(a.lists); setError("");
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
  return <div className="ledger-page agent-page">
    <div className="mp-head agent-head">
      <div>
        <b>Option agent</b>
        <span className="agent-sub">Top {models?.constants.top_n ?? 10} calls and puts for the next 1–3 sessions, as of {models?.constants.as_of ?? "14:45"} · emitted {status?.emit_at ?? "14:45:15"} each session</span>
      </div>
      <div className="agent-actions">
        <div className="agent-views" role="tablist" aria-label="Which list">
          <button role="tab" aria-selected={view === "latest"} className={view === "latest" ? "active" : ""} onClick={() => setView("latest")}>Latest list</button>
          <button role="tab" aria-selected={view === "active"} className={view === "active" ? "active" : ""} onClick={() => setView("active")}>Open lists <span>{active.length}</span></button>
          <select aria-label="Past lists" value={typeof view === "number" ? String(view) : ""} onChange={(e) => setView(e.target.value ? Number(e.target.value) : "latest")}>
            <option value="">History…</option>
            {runs.map((r) => <option key={r.run_id} value={r.run_id}>
              {dayLabel(r.day)} · {r.source}{r.open ? ` · open D+${r.sessions_elapsed}/3` : " · closed"}
              {r.ce.mean_1_3d != null ? ` · CE ${pct(r.ce.mean_1_3d)} PE ${pct(r.pe.mean_1_3d)}` : ""}
            </option>)}
          </select>
        </div>
        {run && <span className="agent-chip">{run.day} · run #{run.run_id} · {run.candidates.toLocaleString("en-IN")} candidates · {run.model_version != null ? `model v${run.model_version}` : "no stored model"} · {run.source}</span>}
        {champion && <span className="agent-chip">champion v{champion.version} {champion.config.name}</span>}
        <button disabled={!!busy} onClick={() => void act("emit", "/api/agent/run")}>{busy === "emit" ? "Emitting…" : "Emit now"}</button>
        <button disabled={!!busy} onClick={() => void act("train", "/api/agent/train")}>{busy === "train" ? "Training…" : "Retrain"}</button>
      </div>
    </div>
    {(error || status?.error) && <div className="agent-error">{error || status?.error}</div>}
    {view === "active" ? <OpenLists lists={active} ticks={ticks} /> : <>
      {typeof view === "number" && run && <div className="agent-list-banner">
        List of {dayLabel(run.day)} ({run.source}) · {(() => { const r = runs.find((x) => x.run_id === run.run_id); return r ? (r.open ? `in its hold: D+${r.sessions_elapsed} of 3, exits by ${dayLabel(r.hold_until)}` : `hold ended ${dayLabel(r.hold_until)} — D+1/D+2/D+3 are final`) : ""; })()}
        <button onClick={() => setView("latest")}>Back to latest</button>
      </div>}
      <IndexTrades trades={shown?.index || []} ticks={ticks} />
    <div className="agent-picks">
      <PickTable title="Calls (CE)" rows={shown?.ce || []} ticks={ticks} emitAt={status?.emit_at} />
      <PickTable title="Puts (PE)" rows={shown?.pe || []} ticks={ticks} emitAt={status?.emit_at} />
    </div>
    <CompareTopPanel runId={run?.run_id} />
    </>}
    <EquityPanel />
    <CompareSummaryPanel />
    <div className="agent-bottom">
      <PerformancePanel perf={perf} />
      <ModelsPanel models={models} status={status} />
    </div>
  </div>;
}

function PickTable({ title, rows, ticks, emitAt }: { title: string; rows: Pick[]; ticks: Ticks; emitAt?: string }) {
  const liveCount = rows.filter((p) => ticks[p.symbol]?.ltp).length;
  return <section className="panel agent-panel">
    <div className="panel-title">{title}{rows.length > 0 && <span className="agent-live-count">{liveCount}/{rows.length} live</span>}</div>
    {!rows.length ? <div className="agent-empty">No picks yet — the first emission comes at {emitAt ?? "14:45:15"} once a model is trained.</div> :
      <div className="live-table-wrap"><table className="live-table agent-table">
        <thead><tr><th>#</th><th>Contract</th><th>Entry</th><th>LTP</th><th>Chg</th><th>Since entry</th><th title="Profit or loss for one lot at the current price">₹ / lot</th><th>D+1</th><th>D+2</th><th>D+3</th><th>Best / worst</th><th title="Volatility-sized: a one-sigma day moves the position about ₹1,500">Size</th><th>Why</th></tr></thead>
        <tbody>{rows.map((p) => { const mark = markOf(ticks, p.symbol, p.last_price); const m = sinceEntry(p.entry_price, mark.ltp, p.size?.lot_size); return <tr key={p.symbol}>
          <td>{p.rank}</td>
          <td title={p.symbol}><b>{p.underlying}</b> {p.strike} <small>{p.expiry.slice(5)}</small></td>
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
    {latest && <p className="agent-note">Last judged {latest.trained_at.slice(0, 16).replace("T", " ")} UTC on {configs[0]?.[1].test_days ?? 0} held-out sessions (train {latest.train_from} → {latest.train_to}, {latest.samples.toLocaleString("en-IN")} labelled contracts). A challenger replaces the champion only when it beats it by {((models?.constants.promote_margin ?? 0) * 100).toFixed(1)} pts of excess return. {latest.note}</p>}
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

const INDEX_ORDER = ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"];
const level = (value: number | null | undefined, digits = 0) => value == null ? "—" : value.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });

function IndexTrades({ trades, ticks }: { trades: IndexTrade[]; ticks: Ticks }) {
  if (!trades.length) return null;
  const ordered = [...trades].sort((a, b) => INDEX_ORDER.indexOf(a.underlying) - INDEX_ORDER.indexOf(b.underlying));
  return <section className="panel agent-panel">
    <div className="panel-title">Index trades — side, contract and plan for the next 1–3 sessions</div>
    <div className="agent-index-grid">{ordered.map((t) => <IndexCard key={t.underlying} trade={t} ticks={ticks} />)}</div>
  </section>;
}

function IndexCard({ trade: t, ticks }: { trade: IndexTrade; ticks: Ticks }) {
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
      <span className="agent-index-contract" title={t.symbol || ""}>{t.strike} {call ? "CE" : "PE"} · exp {t.expiry?.slice(5)}</span>
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

const EQUITY_CHARTS: { title: string; note: string; lines: { book: string; label: string; color: string; dashed?: boolean }[] }[] = [
  { title: "Top-10 calls", note: "vol-sized, ₹3 L cap · held to the 3rd close", lines: [
    { book: "ce_sized", label: "sized", color: "#22c893" }, { book: "ce", label: "flat ₹10k", color: "#1b7f60", dashed: true },
    { book: "ce_all_sized", label: "all calls (same ₹)", color: "#8796aa", dashed: true }] },
  { title: "Top-10 puts", note: "vol-sized, ₹3 L cap · held to the 3rd close", lines: [
    { book: "pe_sized", label: "sized", color: "#f15b6c" }, { book: "pe", label: "flat ₹10k", color: "#8f3a45", dashed: true },
    { book: "pe_all_sized", label: "all puts (same ₹)", color: "#8796aa", dashed: true }] },
  { title: "Index trades", note: "₹4k risk at stop, ₹2 L cap · plan exit", lines: [
    { book: "index_sized", label: "sized", color: "#55a9ff" }, { book: "index_plan", label: "1 lot", color: "#2f5f93", dashed: true },
    { book: "index_coin", label: "coin flip ATM (1 lot)", color: "#8796aa", dashed: true }] },
];

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
    <div className="panel-title">Equity curves — rupee P&L marked at every session close, net of costs{equity ? ` · ${equity.since} → ${equity.as_of}` : ""}</div>
    {error && <div className="agent-empty">{error}</div>}
    <div className="agent-equity-grid">{EQUITY_CHARTS.map((spec) => <EquityChart key={spec.title} spec={spec} books={equity?.books} />)}</div>
  </section>;
}

function EquityChart({ spec, books }: { spec: typeof EQUITY_CHARTS[number]; books?: Record<string, EquityBook> }) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  useEffect(() => {
    if (!element.current) return;
    const api = createChart(element.current, {
      width: element.current.clientWidth, height: element.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#090e16" }, textColor: "#8796aa", attributionLogo: false },
      grid: { vertLines: { color: "#151e2b" }, horzLines: { color: "#151e2b" } },
      rightPriceScale: { borderColor: "#263144", minimumWidth: 64 },
      timeScale: { borderColor: "#263144" },
      localization: { priceFormatter: (value: number) => rupees(value) },
      crosshair: { vertLine: { color: "#61728a", labelBackgroundColor: "#243247" }, horzLine: { color: "#61728a", labelBackgroundColor: "#243247" } },
    });
    chart.current = api;
    const observer = new ResizeObserver(() => api.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => { observer.disconnect(); chart.current = null; api.remove(); };
  }, []);
  useEffect(() => {
    const api = chart.current;
    if (!api || !books) return;
    const series = spec.lines.map((line) => {
      const s = api.addSeries(LineSeries, { color: line.color, lineWidth: 2, lineStyle: line.dashed ? LineStyle.Dashed : LineStyle.Solid, title: line.label, priceLineVisible: false, lastValueVisible: true });
      s.setData((books[line.book]?.points || []).map((p) => ({ time: p.day as Time, value: p.equity })));
      return s;
    });
    series[0]?.createPriceLine({ price: 0, color: "#71819877", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    api.timeScale().fitContent();
    return () => {
      // The chart effect disposes the API first on unmount; updates still remove their live series.
      if (chart.current !== api) return;
      for (const s of series) api.removeSeries(s);
    };
  }, [books, spec]);
  const main = books?.[spec.lines[0].book]?.stats;
  const bench = books?.[spec.lines[spec.lines.length - 1].book]?.stats;
  return <div className="agent-equity">
    <div className="agent-equity-head"><b>{spec.title}</b><small>{spec.note}</small></div>
    <div className="agent-equity-chart" ref={element} />
    <div className="agent-equity-stats">
      <span>P&amp;L <b className={tone(main?.final)}>{rupees(main?.final)}</b></span>
      <span>vs benchmark <b className={tone(main?.final != null && bench?.final != null ? main.final - bench.final : null)}>{main?.final != null && bench?.final != null ? rupees(main.final - bench.final) : "—"}</b></span>
      <span>Max drawdown <b className="negative">{rupees(main?.max_drawdown)}</b></span>
      <span>Peak capital <b>{rupees(main?.max_deployed).replace("+", "")}</b></span>
      <span>Win rate <b>{main?.win_rate == null ? "—" : `${(main.win_rate * 100).toFixed(0)}%`}</b></span>
      <span>Profit factor <b>{main?.profit_factor == null ? "—" : main.profit_factor.toFixed(2)}</b></span>
      <span>Trades <b>{main ? `${main.closed ?? 0} closed · ${main.open ?? 0} open` : "—"}</b></span>
      <span>Costs <b>{rupees(main?.costs).replace("+", "")}</b></span>
    </div>
    {main?.exits && <small className="agent-equity-exits">exits: {Object.entries(main.exits).map(([k, v]) => `${k} ${v}`).join(" · ")}{main.stale_exits ? " (last known price = contract expired, no later bars)" : ""}</small>}
  </div>;
}


/** Every list still inside its hold, newest first: the day it was issued, where it is in its hold, live marks. */
function OpenLists({ lists, ticks }: { lists: ActiveList[]; ticks: Ticks }) {
  if (!lists.length) return <section className="panel agent-panel"><div className="agent-empty">No list is inside its 1–3 session hold right now.</div></section>;
  return <>{lists.map((list) => {
    const run = list.run!;
    const all = [...list.ce, ...list.pe];
    const marks = all.map((p) => sinceEntry(p.entry_price, markOf(ticks, p.symbol, p.last_price).ltp)).map((m) => m.fraction).filter((x): x is number => x != null);
    const mean = marks.length ? marks.reduce((a, b) => a + b, 0) / marks.length : null;
    return <section key={run.run_id} className="agent-open-list">
      <div className="agent-open-head">
        <b>List of {dayLabel(run.day)}</b>
        <span className="agent-chip">{list.sessions_elapsed === 0 ? "entered today" : `D+${list.sessions_elapsed} of 3`} · exits by {dayLabel(list.hold_until)}</span>
        <span className="agent-chip">{run.source} · model v{run.model_version ?? "—"} · {run.candidates.toLocaleString("en-IN")} candidates</span>
        <span className={`agent-open-mean ${tone(mean)}`}>{mean == null ? "" : `avg since entry ${pct(mean)}`}</span>
      </div>
      {list.index.length > 0 && <IndexTrades trades={list.index} ticks={ticks} />}
      <div className="agent-picks">
        <PickTable title="Calls (CE)" rows={list.ce} ticks={ticks} />
        <PickTable title="Puts (PE)" rows={list.pe} ticks={ticks} />
      </div>
    </section>;
  })}</>;
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
