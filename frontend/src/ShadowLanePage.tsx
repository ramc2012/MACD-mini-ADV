import { useEffect, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import "./shadowLanes.css";

type Row = { symbol?: string; instrument?: string; option_type?: string; horizon_sessions?: number; source_mark?: number;
  combined_score?: number; score?: number; rank?: number; side_rank?: number; source_mark_ts?: string; expiry?: string };
type Publication = { id: string; source_session: string; decision_at: string; policy: string; watchlist: Row[] };
type Snapshot = { lane: string; shadow_only: boolean; execution_enabled: boolean; worker_enabled: boolean; checked_at?: string;
  publication_window: string; target: string; method: string;
  readiness: { status: string; blockers?: string[]; source_session?: string; expected?: number; current_complete?: number;
    fresh_feature_symbols?: number; inputs?: string; direction_model?: string; contract_model?: string; policy?: string; feature_error?: string; diagnostic?: boolean };
  publications: Publication[]; journal: { at: string; kind: string; detail: Record<string, unknown> }[];
  outcomes: { instrument: string; horizon: string; observed_at: string; observed_return: number; baseline_price: number; observed_price: number }[] };
type Archive = { status: string; reason?: string; book?: { open_positions?: Record<string, unknown>[]; closed_positions?: Record<string, unknown>[]; initial_capital?: number; available_capital?: number }; runs?: Record<string, unknown>[] };
const TABS = ["Overview", "Selections", "Journal", "Outcomes", "Nomad history"] as const;
type Tab = typeof TABS[number];
const number = (v: number | undefined, digits = 2) => v == null ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: digits });
function stamp(value?: string) {
  if (!value) return "—";
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return value;
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(d).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} IST`;
}
async function read<T,>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { signal, headers: API_TOKEN ? { "X-Macd-Token": API_TOKEN } : {} });
  if (!response.ok) throw new Error(`Lane service returned ${response.status}`);
  return response.json() as Promise<T>;
}
const details = (value: unknown) => JSON.stringify(value, (_key, v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(v) ? stamp(v) : v, 2);
function ScoreChart({ rows }: { rows: Row[] }) {
  const selected = rows.slice(0, 20), height = Math.max(80, selected.length * 27 + 30);
  return selected.length ? <svg className="shadow-score" viewBox={`0 0 720 ${height}`} role="img" aria-label="Frozen ordinal candidate scores">
    {selected.map((r, i) => { const value = r.combined_score ?? (r.score || 0) / 100; return <g key={`${r.instrument}-${r.option_type}-${i}`}>
      <text x="0" y={i * 27 + 22}>{r.symbol || r.instrument} {r.option_type || "LONG"}</text>
      <rect x="210" y={i * 27 + 7} width={Math.max(0, Math.min(1, value)) * 440} height="17" fill={r.option_type === "PE" ? "#f3a85c" : "#55baae"} rx="3" />
      <text x="665" y={i * 27 + 22}>{number(value, 3)}</text></g>; })}
  </svg> : <div className="agent-empty">No prospective selections have been frozen. Historical candidates are not presented as new decisions.</div>;
}

export function ShadowLanePage({ lane }: { lane: "vanguard" | "cbe" }) {
  const [tab, setTab] = useState<Tab>("Overview");
  const [data, setData] = useState<Snapshot | null>(null);
  const [archive, setArchive] = useState<Archive | null>(null);
  const [error, setError] = useState("");
  const [publication, setPublication] = useState("");
  useEffect(() => {
    setData(null); setArchive(null); setError(""); setPublication(""); setTab("Overview");
    const controller = new AbortController();
    const load = async () => { try { const value = await read<Snapshot>(`/api/shadow-lanes/${lane}`, controller.signal); setData(value); setError(""); }
      catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); } };
    void load(); const timer = window.setInterval(load, 30_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [lane]);
  useEffect(() => {
    if (tab !== "Nomad history") return;
    const controller = new AbortController();
    void read<Archive>(`/api/shadow-lanes/${lane}/archive`, controller.signal).then(setArchive).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
    return () => controller.abort();
  }, [lane, tab]);
  const active = data?.publications.find(p => p.id === publication) || data?.publications[0];
  const rows = active?.watchlist || [], readiness = data?.readiness;
  return <section className="agent-page shadow-lane-page">
    <div className="agent-head panel-row"><div><b>{lane === "vanguard" ? "Vanguard" : "CBE"} · fresh shadow lane</b><span className="agent-sub">{data?.target || (lane === "vanguard" ? "Stock-option performers over 1–3 sessions and BTST" : "Daily long-only stock selection")}</span></div>
      <span className="agent-chip">Observation only · execution disabled</span></div>
    <nav className="shadow-tabs" aria-label="Shadow lane views">{TABS.map(t => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}</nav>
    {error && <div className="agent-error" role="alert">{error}</div>}
    {!data && !error && <div className="agent-empty" role="status">Loading shadow lane…</div>}
    {data && <>
      <div className="shadow-metrics">
        <div className="panel"><small>Input status</small><b>{readiness?.status.replace(/_/g, " ")}</b><span>checked {stamp(data.checked_at)}</span></div>
        <div className="panel"><small>Prospective cohorts</small><b>{data.publications.length}</b><span>{data.publication_window}</span></div>
        <div className="panel"><small>Current observed breadth</small><b>{number(readiness?.current_complete, 0)} / {number(readiness?.expected, 0)}</b><span>80% required; missing inputs block selection</span></div>
        <div className="panel"><small>Orders / funded positions</small><b>Disabled</b><span>{data.worker_enabled ? "Shadow scheduler enabled" : "Shadow scheduler not activated"}</span></div>
      </div>
      {tab === "Overview" && <>
        <div className="panel shadow-panel"><h2>Readiness</h2>{readiness?.diagnostic && <p>Last-session input diagnostic only. No fresh cohort has been published from this check.</p>}{readiness?.blockers?.map(b => <p key={b}>{b}</p>)}{readiness?.feature_error && <p className="agent-error">{readiness.feature_error}</p>}
          {readiness?.fresh_feature_symbols != null && <p>Fresh combined features: {number(readiness.fresh_feature_symbols, 0)} / {number(readiness.expected, 0)} stocks.</p>}
          <p>{readiness?.inputs || "The lane waits for completed broker bars and fresh market inputs. It does not replay missed publications."}</p>
          <p className="agent-sub">{data.method}</p>{readiness?.direction_model && <p>Direction model: {readiness.direction_model} · payoff model: {readiness.contract_model}</p>}</div>
        <div className="panel shadow-panel"><h2>Latest frozen ranking</h2><ScoreChart rows={rows} /><p className="agent-sub">Scores are ordinal rankings, not calibrated returns or probabilities.</p></div>
      </>}
      {tab === "Selections" && <div className="panel shadow-panel"><h2>Immutable prospective selections</h2>
        {data.publications.length > 0 && <select value={active?.id || ""} onChange={e => setPublication(e.target.value)} aria-label="Frozen cohort">{data.publications.map(p => <option value={p.id} key={p.id}>{p.source_session} · {stamp(p.decision_at)}</option>)}</select>}
        <ScoreChart rows={rows} />{active && <p>Frozen {stamp(active.decision_at)} · {active.policy}</p>}
        {rows.length > 0 && <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Rank</th><th>Underlying</th><th>Exact instrument</th><th>Side</th><th>Horizon</th><th>Observed baseline</th><th>Source time</th></tr></thead>
          <tbody>{rows.map((r, i) => <tr key={`${r.instrument}-${i}`}><td>{r.side_rank || r.rank || i + 1}</td><td>{r.symbol}</td><td>{r.instrument}</td><td>{r.option_type || "LONG"}</td><td>{r.horizon_sessions || 1} sessions</td><td>{number(r.source_mark)}</td><td>{stamp(r.source_mark_ts)}</td></tr>)}</tbody></table></div>}
      </div>}
      {tab === "Journal" && <div className="panel shadow-panel"><h2>Decision journal</h2>{!data.journal.length && <p>No events yet.</p>}{data.journal.map((r, i) => <details key={`${r.at}-${i}`}><summary>{stamp(r.at)} · {r.kind.replace(/_/g, " ")}</summary><pre>{details(r.detail)}</pre></details>)}</div>}
      {tab === "Outcomes" && <div className="panel shadow-panel"><h2>Observed follow-through</h2><p>{data.method}. BTST uses the next session’s completed 09:15–09:45 bar; it is not an opening-price fill.</p>
        {!data.outcomes.length ? <div className="agent-empty">No completed prospective outcomes yet.</div> : <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Exact instrument</th><th>Horizon</th><th>Baseline</th><th>Observed quote</th><th>Quote change</th><th>Recorded</th></tr></thead>
          <tbody>{data.outcomes.map((r, i) => <tr key={`${r.instrument}-${r.horizon}-${i}`}><td>{r.instrument}</td><td>{r.horizon}</td><td>{number(r.baseline_price)}</td><td>{number(r.observed_price)}</td><td className={r.observed_return >= 0 ? "positive" : "negative"}>{number(r.observed_return * 100)}%</td><td>{stamp(r.observed_at)}</td></tr>)}</tbody></table></div>}</div>}
      {tab === "Nomad history" && <div className="panel shadow-panel"><h2>Retained Nomad history · read only</h2><p>The former paper book and frozen lists remain separate from these fresh shadow observations. Their historical performance is not imported as this lane’s track record.</p>
        {!archive ? <p>Loading retained history…</p> : <><p>Status: {archive.status}{archive.reason ? ` · ${archive.reason}` : ""}</p>{archive.book && <p>{archive.book.open_positions?.length || 0} retained open records · {archive.book.closed_positions?.length || 0} retained closed records</p>}
          {archive.runs && <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Source session</th><th>Observations</th><th>Former status</th><th>Original decision</th></tr></thead><tbody>{archive.runs.map((r, i) => <tr key={i}><td>{String(r.source_session)}</td><td>{String(r.item_count)}</td><td>{String(r.status)}</td><td>{stamp(String(r.decision_at || ""))}</td></tr>)}</tbody></table></div>}
          {archive.book && <details><summary>Show original retained records</summary><pre>{details(archive.book)}</pre></details>}</>}
      </div>}
    </>}
  </section>;
}
