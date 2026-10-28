import { useCallback, useEffect, useState } from "react";
import { curvePath, rupees, tone } from "./agentMath";
import type { Tick } from "./types";
import { currentSmartBook } from "./laneMarks";
import { API_TOKEN, API_URL } from "./runtime";

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
};
type Health = { pending: number; cutoff: number; rolling_scores: number; error: string | null; last_batch_at: string | null;
  intelligence?:{policy:string;mode:string;qwen_required:boolean;validation:string;last_allocation?:{priority:number;observed_at:string}};
  rules: { entry_delay_seconds: number; skip_fraction: number; ticket_fraction: number; liquidity_share_pct: number;
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
const DECISIONS = ["", "TAKEN", "WAITING_PRICE", "NO_PRICE", "SESSION_CLOSED", "QWEN_REJECTED", "QWEN_UNAVAILABLE", "QWEN_STALE", "SKIPPED_LOW_SCORE", "MACD_FADED", "LOT_ABOVE_TICKET", "NO_CASH", "TOO_ILLIQUID", "ALREADY_HELD", "STALE", "NOT_TRADABLE", "ENTRY_REJECTED"];

function LaneCard({ summary, active, curve, onSelect }: { summary: Summary; active: boolean; curve: number[]; onSelect: () => void }) {
  const all = summary.all;
  const taken = summary.decisions.TAKEN || 0;
  const seen = Object.values(summary.decisions).reduce((a, b) => a + b, 0);
  return <button className={`panel lane-card ${active ? "active" : ""}`} onClick={onSelect}>
    <div className="lane-card-head"><b>{label(summary.lane)}</b><span className={tone(summary.return_pct)}>{percent(summary.return_pct)}</span><small>equity {money(summary.equity)} · cash {money(summary.cash)}</small></div>
    {curve.length > 1 && <div className="agent-curve"><svg viewBox="0 0 300 40" preserveAspectRatio="none"><path d={curvePath(curve, 300, 40)} /></svg></div>}
    <div className="agent-stats lane-stats">
      <div className="agent-stat"><span>Realized / open</span><b className={tone(summary.realized)}>{rupees(summary.realized)}</b><small className={tone(summary.unrealized)}>{rupees(summary.unrealized)} on {summary.open_positions} open</small></div>
      <div className="agent-stat"><span>Closed trades</span><b>{all.trades}</b><small>win {all.win_rate == null ? "—" : `${Math.round(all.win_rate * 100)}%`} · PF {all.profit_factor == null ? "—" : all.profit_factor.toFixed(2)}</small></div>
      <div className="agent-stat"><span>MACD source</span><b className={tone(summary.by_source.macd?.net)}>{rupees(summary.by_source.macd?.net ?? 0)}</b><small>{summary.by_source.macd?.trades ?? 0} trades</small></div>
      <div className="agent-stat"><span>Blast source</span><b className={tone(summary.by_source.blast?.net)}>{rupees(summary.by_source.blast?.net ?? 0)}</b><small>{summary.by_source.blast?.trades ?? 0} trades</small></div>
      <div className="agent-stat"><span>Max drawdown</span><b className={summary.max_drawdown > 0 ? "negative" : ""}>{rupees(-summary.max_drawdown)}</b><small>{percent(-summary.max_drawdown / summary.capital * 100)} of capital</small></div>
      <div className="agent-stat"><span>Decisions</span><b>{taken} taken</b><small>of {seen} journalled</small></div>
    </div>
  </button>;
}

export function LanesPage({ ticks, portfolios }: { ticks: Record<string, Tick>; portfolios: Record<string, any> }) {
  const [lanes, setLanes] = useState<Summary[]>([]);
  const [snapshots, setSnapshots] = useState<Record<string, Snapshot>>({});
  const [curves, setCurves] = useState<Record<string, number[]>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [journal, setJournal] = useState<JournalRow[]>([]);
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState("");
  const [decision, setDecision] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadIndex = useCallback(async () => {
    try {
      const index = await call<{ lanes: Summary[] }>("/api/lanes");
      setLanes(index.lanes);
      const requestedAt=Date.now();
      const books = await Promise.all(index.lanes.map(async lane => [lane.lane, {...await call<Snapshot>(`/api/lanes/${lane.lane}/snapshot`),requestedAt}] as const));
      setSnapshots(Object.fromEntries(books));
      setSelected((current) => current ?? index.lanes[0]?.lane ?? null);
      const pairs = await Promise.all(index.lanes.map(async (lane) =>
        [lane.lane, (await call<{ equity: { equity: number }[] }>(`/api/lanes/${lane.lane}/equity`)).equity.map((p) => p.equity)] as const));
      setCurves(Object.fromEntries(pairs));
      setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, []);

  const loadLane = useCallback(async () => {
    if (!selected) return;
    try {
      const query = new URLSearchParams({ limit: "400", ...(day ? { day } : {}), ...(decision ? { decision } : {}) });
      const requestedAt=Date.now();
      const [snap, closed, rows] = await Promise.all([
        call<Snapshot>(`/api/lanes/${selected}/snapshot`),
        call<{ trades: Trade[] }>(`/api/lanes/${selected}/trades?limit=300`),
        call<{ rows: JournalRow[]; days: string[] }>(`/api/lanes/${selected}/journal?${query}`),
      ]);
      setSnapshot({...snap,requestedAt}); setTrades(closed.trades); setJournal(rows.rows); setDays(rows.days);
      setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, [selected, day, decision]);

  useEffect(() => { void loadIndex(); const id = window.setInterval(loadIndex, 15_000); return () => window.clearInterval(id); }, [loadIndex]);
  useEffect(() => { void loadLane(); const id = window.setInterval(loadLane, 5_000); return () => window.clearInterval(id); }, [loadLane]);

  const health = snapshot?.health;
  const rules = health?.rules;
  const books=Object.fromEntries(lanes.map(lane=>[lane.lane,currentSmartBook(
    lane.lane===selected&&snapshot?.lane===selected?snapshot:snapshots[lane.lane],portfolios[lane.lane],ticks)]));
  const marked = selected ? books[selected] : null;
  const positions: Position[] = marked?.positions || [];
  return <div className="agent-page lanes-page">
    <div className="agent-head panel-row">
      <div><b>Smart lanes · local intelligence</b><span className="agent-sub">MACD signals · paper only · Qwen independent</span></div>
      {health && <div className="agent-actions">
        <span className="agent-chip">Local ranking · no score veto</span>
        <span className="agent-chip">{health.pending} waiting</span>
        {rules && <span className="agent-chip">MACD: stop {Math.round(rules.macd.hard * 100)}% · trail {Math.round(rules.macd.trail * 100)}% from +{Math.round(rules.macd.act * 100)}% · target +{Math.round((rules.macd.target || 0) * 100)}% · hold {rules.macd.hold} sessions</span>}
        {rules?.blast && <span className="agent-chip">Blast: stop {Math.round(rules.blast.hard * 100)}% · trail {Math.round(rules.blast.trail * 100)}% from +{Math.round(rules.blast.act * 100)}%</span>}
      </div>}
    </div>
    <section className="panel agent-panel" aria-label="Local allocation status"><div className="panel-title">{health?.intelligence?.policy??'Checking allocation policy…'}</div><div className="agent-actions"><span className="agent-chip">{marked?`${Math.max(0,(1-marked.cash/Math.max(1,marked.equity))*100).toFixed(1)}%`:'—'} capital deployed</span><span className="agent-chip">{health?.pending??0} queued</span><span className="agent-chip">Last batch {when(health?.last_batch_at)}</span></div><details><summary>ⓘ Allocation policy and limits</summary><p>Same MACD BUY signals, rechecked after {rules?rules.entry_delay_seconds/60:5} minutes. Local research rank, current premium MACD, side breadth, fresh spot alignment and existing exposure determine priority. Rank and breadth size whole-lot tickets within {rules?Math.round(rules.ticket_fraction*100):10}% of equity and available cash. Missing quotes retry within the original three-minute admission deadline. No Qwen call is required. Flow is recorded as context, without a directional veto. Existing exits are retained. Priority is a heuristic, not a calibrated profit probability; prospective improvement is unproven.</p></details></section>
    {(error || health?.error) && <div className="agent-error">{error || health?.error}</div>}
    <small>Retained book history · totals below include earlier policies and do not measure the new allocation policy.</small>
    <div className="lane-cards">{lanes.map((lane) => {
      const book=books[lane.lane];
      const summary=book?{...lane,equity:book.equity,cash:book.cash,unrealized:book.unrealized_pnl,
        realized:book.realized_pnl??lane.realized,open_positions:book.positions.length,return_pct:(book.equity/lane.capital-1)*100}:lane;
      return <LaneCard key={lane.lane} summary={summary} active={lane.lane===selected} curve={curves[lane.lane]||[]} onSelect={()=>setSelected(lane.lane)}/>;
    })}
      {lanes.length === 0 && !error && <div className="agent-empty">No smart lanes configured.</div>}</div>

    {selected && <>
      <section className="panel agent-panel">
        <div className="panel-title">{label(selected)} — open positions ({positions.length})</div>
        {positions.length === 0 ? <div className="agent-empty">Nothing open.</div> :
          <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr>
            <th>Contract</th><th>Source</th><th>Lots</th><th>Avg</th><th>LTP</th><th>P&amp;L</th><th>Stop</th><th>Trail</th><th>Target</th><th>Hold until</th><th>Score</th><th>Size×</th><th>Opened</th>
          </tr></thead><tbody>{positions.map((p) => {
            const tick = ticks[p.symbol];
            const ltp = tick && tick.ltp > 0 ? tick.ltp : p.last_price;
            const ret = (ltp / p.average_price - 1) * 100;
            return <tr key={p.symbol}>
              <td>{short(p.symbol)}<small> {p.expiry ? `exp ${p.expiry.slice(5)}` : ""}</small></td><td>{p.source}</td><td>{p.lots}</td>
              <td>{p.average_price.toFixed(2)}</td><td>{ltp.toFixed(2)}</td>
              <td className={tone(ret)}>{percent(ret)}<small> {rupees((ltp - p.average_price) * p.quantity)}</small></td>
              <td>{p.hard_stop.toFixed(2)}</td><td>{p.trailing_stop ? p.trailing_stop.toFixed(2) : "—"}</td><td>{p.target_price ? p.target_price.toFixed(2) : "—"}</td>
              <td>{when(p.hold_until)}</td><td>{p.score == null ? "—" : p.score.toFixed(3)}</td><td>{p.dispersion == null ? "—" : p.dispersion.toFixed(2)}</td><td>{when(p.opened_at)}</td>
            </tr>;
          })}</tbody></table></div>}
      </section>

      <div className="agent-bottom">
        <section className="panel agent-panel">
          <div className="panel-title">Closed trades ({trades.length})</div>
          {trades.length === 0 ? <div className="agent-empty">No closed trades yet.</div> :
            <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr>
              <th>Contract</th><th>Source</th><th>Entry</th><th>Exit</th><th>Lots</th><th>P&amp;L</th><th>Return</th><th>Peak</th><th>Exit reason</th>
            </tr></thead><tbody>{trades.map((t) => <tr key={t.position_id}>
              <td>{short(t.symbol)}</td><td>{t.source}</td><td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td><td>{t.lots}</td>
              <td className={tone(t.pnl)}>{rupees(t.pnl)}</td><td className={tone(t.return_pct)}>{percent(t.return_pct)}</td>
              <td>{percent(t.max_return_pct)}</td><td>{(t.exit_reason || "—").replace(/_/g, " ").toLowerCase()}{t.closed ? "" : " (part)"}</td>
            </tr>)}</tbody></table></div>}
        </section>
        <section className="panel agent-panel">
          <div className="panel-title">Journal — every decision and why
            <select value={day} onChange={(e) => setDay(e.target.value)}><option value="">all days</option>{days.map((d) => <option key={d} value={d}>{d}</option>)}</select>
            <select value={decision} onChange={(e) => setDecision(e.target.value)}>{DECISIONS.map((d) => <option key={d} value={d}>{d ? d.replace(/_/g, " ").toLowerCase() : "all decisions"}</option>)}</select>
          </div>
          {journal.length === 0 ? <div className="agent-empty">No decisions journalled{day ? ` on ${day}` : ""} yet.</div> :
            <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr>
              <th>Time</th><th>Source</th><th>Contract</th><th>Decision</th><th>Priority / research score</th><th>Size×</th><th>Ticket</th><th>Lots</th><th>Signal → check</th>
            </tr></thead><tbody>{journal.map((row) => <tr key={row.id}>
              <td>{when(row.at)}</td><td>{row.source}</td><td>{short(row.symbol)}</td>
              <td className={row.decision === "TAKEN" ? "positive" : ""}>{row.decision.replace(/_/g, " ").toLowerCase()}</td>
              <td>{typeof (row.detail.allocation as {priority?:number}|undefined)?.priority==='number'?(row.detail.allocation as {priority:number}).priority.toFixed(3):'—'}<small> / {row.score == null ? '—' : row.score.toFixed(3)}</small><details><summary>ⓘ Inputs</summary><pre>{JSON.stringify(row.detail,null,2)}</pre></details></td>
              <td>{row.dispersion == null ? "—" : row.dispersion.toFixed(2)}</td><td>{row.ticket == null ? "—" : money(row.ticket)}</td><td>{row.lots ?? "—"}</td>
              <td>{row.signal_price?.toFixed(2) ?? "—"}{row.check_price != null && <small> → {row.check_price.toFixed(2)}</small>}</td>
            </tr>)}</tbody></table></div>}
        </section>
      </div>
    </>}
  </div>;
}
