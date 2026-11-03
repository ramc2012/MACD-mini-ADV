import { useCallback, useEffect, useState } from "react";
import { curvePath, rupees, tone } from "./agentMath";
import type { Tick } from "./types";
import { currentSmartBook } from "./laneMarks";
import { API_TOKEN, API_URL } from "./runtime";

// The spot MACD lane (engine spot_lane.py): cash equities on the MACD lane's own stock signals, Rs 1 L tickets.
const LANE = "spot_macd";

type Stats = { trades: number; wins: number; win_rate: number | null; net: number; profit_factor: number | null; avg_win: number | null; avg_loss: number | null };
type Summary = { lane: string; capital: number; equity: number; cash: number; return_pct: number; realized: number; unrealized: number;
  open_positions: number; max_drawdown: number; all: Stats; by_exit_reason: Record<string, Stats>; decisions: Record<string, number> };
type Position = { symbol: string; position_id?: string; quantity: number; average_price: number; last_price: number; opened_at: string;
  hard_stop: number; trailing_stop: number | null; exit_stage?: number; entry_stage?: number; hold_until: string | null; max_return_pct?: number };
type Rules = { entry: string; ticket: number; sizing: string; hard_stop_pct: number; trail_activation_pct: number; trail_pct: number;
  staged_exits: number[]; invalidate_below_mfe_pct: number; max_hold_sessions: number };
type Health = { pending: number; error: string | null; last_batch_at: string | null; deployed: number; book_full: boolean; rules: Rules; validation: string };
type Snapshot = { requestedAt?: number; lane: string; capital: number; portfolio: { cash: number; equity: number; positions: Position[] }; health: Health };
type Trade = { position_id: string; symbol: string; entry_time: string; exit_time: string; entry_price: number; lots: number; pnl: number;
  return_pct: number; exit_reason: string | null; max_return_pct: number | null; closed: boolean };
type JournalRow = { id: string; at: string; symbol: string; decision: string; ticket: number | null; lots: number | null;
  signal_price: number | null; check_price: number | null; detail: Record<string, unknown> };

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
const short = (symbol: string) => symbol.replace(/^(NSE|BSE):/, "").replace(/-EQ$/, "");
const percent = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const pct = (fraction: number) => `${+(fraction * 100).toFixed(2)}%`;
const money = (value: number) => value >= 10_000_000 ? `₹${(value / 10_000_000).toFixed(2)} Cr` : `₹${(value / 100_000).toFixed(2)} L`;
const words = (text: string | null | undefined) => (text || "—").replace(/_/g, " ").toLowerCase();
const DECISIONS = ["", "TAKEN", "ALREADY_HELD", "WAITING_PRICE", "NO_PRICE", "SESSION_CLOSED", "LOT_ABOVE_TICKET", "NO_CASH", "NOT_TRADABLE", "STALE", "ENTRY_REJECTED"];

export function SpotLanePage({ ticks, portfolio }: { ticks: Record<string, Tick>; portfolio?: unknown }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [curve, setCurve] = useState<number[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [journal, setJournal] = useState<JournalRow[]>([]);
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState("");
  const [decision, setDecision] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadSlow = useCallback(async () => {
    try {
      const [sum, eq] = await Promise.all([call<Summary>(`/api/lanes/${LANE}/summary`),
        call<{ equity: { equity: number }[] }>(`/api/lanes/${LANE}/equity`)]);
      setSummary(sum); setCurve(eq.equity.map((p) => p.equity)); setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, []);
  const loadFast = useCallback(async () => {
    try {
      const query = new URLSearchParams({ limit: "400", ...(day ? { day } : {}), ...(decision ? { decision } : {}) });
      const requestedAt = Date.now();
      const [snap, closed, rows] = await Promise.all([
        call<Snapshot>(`/api/lanes/${LANE}/snapshot`),
        call<{ trades: Trade[] }>(`/api/lanes/${LANE}/trades?limit=300`),
        call<{ rows: JournalRow[]; days: string[] }>(`/api/lanes/${LANE}/journal?${query}`),
      ]);
      setSnapshot({ ...snap, requestedAt }); setTrades(closed.trades); setJournal(rows.rows); setDays(rows.days); setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, [day, decision]);
  useEffect(() => { void loadSlow(); const id = window.setInterval(loadSlow, 30_000); return () => window.clearInterval(id); }, [loadSlow]);
  useEffect(() => { void loadFast(); const id = window.setInterval(loadFast, 5_000); return () => window.clearInterval(id); }, [loadFast]);

  const health = snapshot?.health;
  const rules = health?.rules;
  const book = currentSmartBook(snapshot, portfolio, ticks);
  const positions: Position[] = book?.positions || [];
  const capital = snapshot?.capital ?? summary?.capital ?? 0;
  const equity = book?.equity ?? summary?.equity ?? 0;
  const all = summary?.all;
  const reasons = Object.entries(summary?.by_exit_reason || {}).sort((a, b) => b[1].trades - a[1].trades);
  return <div className="agent-page lanes-page">
    <div className="agent-head panel-row">
      <div><b>Spot MACD · positional cash equities</b><span className="agent-sub">MACD lane stock signals · long only · paper only</span></div>
      {rules && <div className="agent-actions">
        <span className="agent-chip">Ticket {money(rules.ticket)} · whole shares</span>
        <span className="agent-chip">Stop {pct(rules.hard_stop_pct)} · thirds at +{rules.staged_exits.map(pct).join(" / +")} · trail {pct(rules.trail_pct)} from +{pct(rules.trail_activation_pct)}</span>
        <span className="agent-chip">MACD &lt; 0 exit while best &lt; +{pct(rules.invalidate_below_mfe_pct)} · hold {rules.max_hold_sessions} sessions</span>
        {health?.book_full && <span className="agent-chip negative" title="Cash is below one ticket: new signals are journalled as no cash">Book full</span>}
      </div>}
    </div>
    <section className="panel agent-panel" aria-label="Spot lane rules">
      <div className="agent-actions"><span className="agent-chip">{health ? money(health.deployed) : "—"} deployed</span><span className="agent-chip">{health?.pending ?? 0} queued</span><span className="agent-chip">Last batch {when(health?.last_batch_at)}</span></div>
      <details><summary>ⓘ Rules and evidence</summary><p>{rules?.entry}. Bought at the next fresh quote in a {rules ? money(rules.ticket) : "₹1 L"} ticket of whole shares; one position per stock; no score cut-off. Exits are the MACD lane's option ladder scaled to spot by 1/10, checked on every tick; the MACD-below-zero exit is judged on closed 30-minute bars. Positions carry overnight. Paper fills include slippage but not delivery charges (about 0.25% round trip). {health?.validation}: entry skill against random entries was positive in 2025 and negative since, so this lane measures the rule forward.</p></details>
    </section>
    {(error || health?.error) && <div className="agent-error">{error || health?.error}</div>}
    <div className="lane-cards"><div className="panel lane-card active">
      <div className="lane-card-head"><b>SPOT MACD</b><span className={tone(equity - capital)}>{capital ? percent((equity / capital - 1) * 100) : "—"}</span><small>equity {money(equity)} · cash {money(book?.cash ?? summary?.cash ?? 0)} · capital {money(capital)}</small></div>
      {curve.length > 1 && <div className="agent-curve"><svg viewBox="0 0 300 40" preserveAspectRatio="none"><path d={curvePath(curve, 300, 40)} /></svg></div>}
      <div className="agent-stats lane-stats">
        <div className="agent-stat"><span>Realized / open</span><b className={tone(summary?.realized)}>{rupees(summary?.realized ?? 0)}</b><small className={tone(book?.unrealized_pnl)}>{rupees(book?.unrealized_pnl ?? 0)} on {positions.length} open</small></div>
        <div className="agent-stat"><span>Closed trades</span><b>{all?.trades ?? 0}</b><small>win {all?.win_rate == null ? "—" : `${Math.round(all.win_rate * 100)}%`} · PF {all?.profit_factor == null ? "—" : all.profit_factor.toFixed(2)}</small></div>
        <div className="agent-stat"><span>Avg win / loss</span><b className="positive">{all?.avg_win == null ? "—" : rupees(all.avg_win)}</b><small className="negative">{all?.avg_loss == null ? "—" : rupees(all.avg_loss)}</small></div>
        <div className="agent-stat"><span>Max drawdown</span><b className={(summary?.max_drawdown ?? 0) > 0 ? "negative" : ""}>{rupees(-(summary?.max_drawdown ?? 0))}</b><small>{capital ? percent(-(summary?.max_drawdown ?? 0) / capital * 100) : "—"} of capital</small></div>
        <div className="agent-stat"><span>Decisions</span><b>{summary?.decisions.TAKEN ?? 0} taken</b><small>of {Object.values(summary?.decisions || {}).reduce((a, b) => a + b, 0)} journalled</small></div>
      </div>
    </div></div>

    <section className="panel agent-panel">
      <div className="panel-title">Open positions ({positions.length})</div>
      {positions.length === 0 ? <div className="agent-empty">Nothing open.</div> :
        <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr>
          <th>Stock</th><th>Shares</th><th>Avg</th><th>LTP</th><th>P&amp;L</th><th>Best</th><th>Stop</th><th>Trail</th><th>Thirds out</th><th>Hold until</th><th>Opened</th>
        </tr></thead><tbody>{[...positions].sort((a, b) => (b.last_price / b.average_price) - (a.last_price / a.average_price)).map((p) => {
          const ret = (p.last_price / p.average_price - 1) * 100;
          return <tr key={p.symbol}>
            <td>{short(p.symbol)}</td><td>{p.quantity}</td><td>{p.average_price.toFixed(2)}</td><td>{p.last_price.toFixed(2)}</td>
            <td className={tone(ret)}>{percent(ret, 2)}<small> {rupees((p.last_price - p.average_price) * p.quantity)}</small></td>
            <td>{percent(p.max_return_pct, 2)}</td><td>{p.hard_stop.toFixed(2)}</td><td>{p.trailing_stop ? p.trailing_stop.toFixed(2) : "—"}</td>
            <td>{p.exit_stage ?? 0}</td><td>{when(p.hold_until)}</td><td>{when(p.opened_at)}</td>
          </tr>;
        })}</tbody></table></div>}
    </section>

    <div className="agent-bottom">
      <section className="panel agent-panel">
        <div className="panel-title">Exits by reason</div>
        {reasons.length === 0 ? <div className="agent-empty">No closed trades yet.</div> :
          <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Reason</th><th>Trades</th><th>Win</th><th>Net</th><th>PF</th></tr></thead>
            <tbody>{reasons.map(([reason, s]) => <tr key={reason}><td>{words(reason)}</td><td>{s.trades}</td><td>{s.win_rate == null ? "—" : `${Math.round(s.win_rate * 100)}%`}</td>
              <td className={tone(s.net)}>{rupees(s.net)}</td><td>{s.profit_factor == null ? "—" : s.profit_factor.toFixed(2)}</td></tr>)}</tbody></table></div>}
        <div className="panel-title">Closed trades ({trades.length})</div>
        {trades.length === 0 ? <div className="agent-empty">No closed trades yet.</div> :
          <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr>
            <th>Stock</th><th>Entry</th><th>Exit</th><th>Shares</th><th>P&amp;L</th><th>Return</th><th>Best</th><th>Exit reason</th>
          </tr></thead><tbody>{trades.map((t) => <tr key={t.position_id}>
            <td>{short(t.symbol)}</td><td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td><td>{t.lots}</td>
            <td className={tone(t.pnl)}>{rupees(t.pnl)}</td><td className={tone(t.return_pct)}>{percent(t.return_pct, 2)}</td>
            <td>{percent(t.max_return_pct, 2)}</td><td>{words(t.exit_reason)}{t.closed ? "" : " (part)"}</td>
          </tr>)}</tbody></table></div>}
      </section>
      <section className="panel agent-panel">
        <div className="panel-title">Journal — every signal and what was done
          <select value={day} onChange={(e) => setDay(e.target.value)}><option value="">all days</option>{days.map((d) => <option key={d} value={d}>{d}</option>)}</select>
          <select value={decision} onChange={(e) => setDecision(e.target.value)}>{DECISIONS.map((d) => <option key={d} value={d}>{d ? words(d) : "all decisions"}</option>)}</select>
        </div>
        {journal.length === 0 ? <div className="agent-empty">No decisions journalled{day ? ` on ${day}` : ""} yet.</div> :
          <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr>
            <th>Time</th><th>Stock</th><th>Decision</th><th>Signal</th><th>Shares</th><th>Signal → fill</th>
          </tr></thead><tbody>{journal.map((row) => <tr key={row.id}>
            <td>{when(row.at)}</td><td>{short(row.symbol)}</td>
            <td className={row.decision === "TAKEN" ? "positive" : ""}>{words(row.decision)}<details><summary>ⓘ</summary><pre>{JSON.stringify(row.detail, null, 2)}</pre></details></td>
            <td>{String(row.detail.kind ?? "").includes("INTRABAR") ? "intrabar" : row.detail.kind ? "bar close" : "—"}</td><td>{row.lots ?? "—"}</td>
            <td>{row.signal_price?.toFixed(2) ?? "—"}{row.check_price != null && <small> → {row.check_price.toFixed(2)}</small>}</td>
          </tr>)}</tbody></table></div>}
      </section>
    </div>
  </div>;
}
