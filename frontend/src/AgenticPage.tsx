import { useCallback, useEffect, useMemo, useState } from "react";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";
import { LaneChartModal, type LaneChartFocus } from "./LaneChart";
import { EquityChart } from "./LaneEquityChart";
import { fillMarkers } from "./laneChartMarks";
import type { ClosedPosition, Trade as Fill } from "./types";
import { newestFirst } from "./positionOrder";

// The agentic lane: a Grok-run paper options book. Every cycle the model reads the market and the book and
// answers with actions and its reasoning; code validates and executes them. This page shows the book with
// each position's thesis, the decision journal, and lets the operator ask for a cycle now (with a note),
// pause the lane, or close a position by hand.
type Position = {
  symbol: string; quantity: number; lots: number; lot_size: number; average_price: number; last_price: number;
  unrealized_pnl: number; return_pct: number; max_return_pct: number; min_return_pct: number;
  thesis?: string | null; last_comment?: string | null; stop_premium?: number | null; target_premium?: number | null;
  expiry?: string | null; booked?: number | null; opened_at?: string | null;
  horizon?: string | null; setup?: string | null; grade?: string | null; invalidation_level?: number | null;
};
type SetupRow = { setup: string; horizon: string; trades: number; wins: number; net_pnl_rs: number; avg_return_pct: number | null };
type Health = {
  enabled: boolean; paused: boolean; brain: string; model: string; cycle_minutes: number; next_cycle_at: string | null;
  cycles_today: number; pending_triggers: string[]; error: string | null; open_positions: number;
  last_cycle: { at: string; trigger: string; mode: string; market_view: string; regime: string; error: string | null } | null;
  limits: Record<string, number | string>;
};
type Trigger = { id: string; contract: string; lots: number; when: string; valid_until: string; stop_premium: number | null;
  target_premium: number | null; horizon: string | null; armed_at: string; why: string };
type Snapshot = { capital: number; health: Health; triggers?: Trigger[]; setups?: SetupRow[];
  portfolio: { cash: number; equity: number; realized_pnl: number; unrealized_pnl: number; positions: Position[] } };
type Act = { action: string; symbol?: string; contract_id?: string | null; position_id?: string | null; lots?: number | null;
  reason?: string; note?: string | null; rejected?: string; stop_premium?: number | null; target_premium?: number | null };
type Exec = { action: string; symbol: string; lots?: number | null; status: string; price?: number | null; error?: string };
type Cycle = { cycle_id: string; at: string; trigger: string; mode: string; model: string | null; market_view: string; regime: string | null;
  confidence: number | null; notes: string | null; operator_note: string | null; accepted: Act[] | null; rejected: Act[] | null;
  executed: Exec[] | null; latency_ms: number | null; prompt_tokens: number | null; completion_tokens: number | null; reasoning_tokens?: number | null; cost_ticks?: number | null; error: string | null };

const headers = (json = false): HeadersInit => ({ ...(API_TOKEN ? { "x-macd-token": API_TOKEN } : {}), ...(json ? { "Content-Type": "application/json" } : {}) });
const rupees = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `₹${Math.round(v).toLocaleString("en-IN")}`);
const pct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const hhmm = (iso?: string | null) => (iso ? new Date(iso).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false }) : "—");
const tone = (v: number) => (v > 0 ? "agentic-up" : v < 0 ? "agentic-down" : "");
const ACTION_LABEL: Record<string, string> = { open: "OPEN", add: "ADD", reduce: "REDUCE", close: "CLOSE", set_levels: "LEVELS",
  hold: "HOLD", arm: "ARM", disarm: "DISARM" };

type BrainTest = { ok: boolean; models: string[]; model: string; model_listed: boolean | null; reply: string | null;
  latency_ms: number | null; error: string | null; at: string | null };
type BrainStatus = { configured: boolean; key_source: "panel" | "environment" | "none"; key_hint: string | null; model: string;
  api_base: string; reasoning_effort: string; efforts: string[]; default_model: string; last_test: BrainTest | null;
  universe: string; universes: string[]; cycle_minutes: number; cycle_choices: number[]; management_key_hint?: string | null; team_id?: string };

/** Connection panel: the xAI key (write-only; only its last 4 characters ever come back), model, effort, endpoint. */
function BrainPanel({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<BrainStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [base, setBase] = useState("");
  const [universe, setUniverse] = useState("index");
  const [cycle, setCycle] = useState(60);
  const [managementKey, setManagementKey] = useState("");
  const [team, setTeam] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const apply = (s: BrainStatus) => {
    setStatus(s); setModel(s.model); setEffort(s.reasoning_effort); setBase(s.api_base); setUniverse(s.universe); setCycle(s.cycle_minutes); setTeam(s.team_id || "");
  };
  useEffect(() => {
    void requestJson<BrainStatus>(`${API_URL}/api/agentic/brain`, { headers: headers() })
      .then((s) => { apply(s); if (!s.configured) setOpen(true); })
      .catch((reason) => setMessage(reason instanceof Error ? reason.message : "Unavailable"));
  }, []);

  const call = async (label: string, method: "PUT" | "POST", path: string, body?: unknown) => {
    setBusy(label); setMessage("");
    try {
      const response = await fetch(`${API_URL}${path}`, { method, headers: headers(true), body: body ? JSON.stringify(body) : undefined });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      apply(payload as BrainStatus);
      onChanged();
      return payload as BrainStatus;
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Request failed");
      return null;
    } finally {
      setBusy("");
    }
  };
  const save = async () => {
    const saved = await call("save", "PUT", "/api/agentic/brain", { api_key: key || null, model, api_base: base, reasoning_effort: effort, universe, cycle_minutes: cycle,
      management_key: managementKey || null, team_id: team });
    if (saved) { setKey(""); setManagementKey(""); setMessage("Saved."); }
  };
  const test = status?.last_test;

  return <div className="panel agentic-section agentic-brain">
    <button className="agentic-brain-head" onClick={() => setOpen(!open)}>
      <span>Brain connection (xAI API)</span>
      <span className={status?.configured ? "agentic-up" : "agentic-down"}>
        {status ? (status.configured ? `key ${status.key_hint} (${status.key_source}) · ${status.model} · ${status.universe === "index" ? "index options" : "index + stocks"} · every ${status.cycle_minutes} min` : "no API key — rules-only") : "…"}
        {test ? (test.ok ? " · test passed" : " · test failed") : ""}</span>
      <span>{open ? "▾" : "▸"}</span>
    </button>
    {open && <div className="agentic-brain-body">
      <p className="agentic-hint">Use an API key from <b>console.x.ai</b>. On this account the calls show as <b>Included</b> in the Grok plan's
        weekly allowance and draw on prepaid credits only once that is used up. The key is stored only in the app's runtime folder (owner-only file) and is never shown again.</p>
      <div className="agentic-form">
        <label>API key<input type="password" autoComplete="off" placeholder={status?.configured ? `saved ${status.key_hint} — leave blank to keep` : "xai-…"}
          value={key} onChange={(event) => setKey(event.target.value)} /></label>
        <label>Model<input list="agentic-models" value={model} placeholder={status?.default_model} onChange={(event) => setModel(event.target.value)} />
          <datalist id="agentic-models">{(test?.models || []).map((m) => <option key={m} value={m} />)}</datalist></label>
        <label>Reasoning effort<select value={effort} onChange={(event) => setEffort(event.target.value)}>
          <option value="">Default (medium)</option>{(status?.efforts || []).map((e) => <option key={e} value={e}>{e}</option>)}</select></label>
        <label>API base<input value={base} onChange={(event) => setBase(event.target.value)} /></label>
        <label>Universe<select value={universe} onChange={(event) => setUniverse(event.target.value)}>
          <option value="index">Index options only (NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY, FINNIFTY)</option>
          <option value="all">Index + F&amp;O stock options</option></select></label>
        <label>Cycle<select value={cycle} onChange={(event) => setCycle(Number(event.target.value))}>
          {(status?.cycle_choices || [15, 30, 60, 120]).map((m) => <option key={m} value={m}>Every {m} min</option>)}</select></label>
        <label title="Optional. Only to read the prepaid credit balance (console.x.ai › Settings › Management keys). It cannot make model calls.">Management key (credit balance)
          <input type="password" autoComplete="off" placeholder={status?.management_key_hint ? `saved ${status.management_key_hint} — leave blank to keep` : "optional"}
            value={managementKey} onChange={(event) => setManagementKey(event.target.value)} /></label>
        <label title="console.x.ai › Settings › Team: the team id">Team id<input value={team} placeholder="optional" onChange={(event) => setTeam(event.target.value)} /></label>
      </div>
      <div className="agentic-controls">
        <button className="agentic-btn primary" disabled={!!busy} onClick={() => void save()}>{busy === "save" ? "Saving…" : "Save"}</button>
        <button className="agentic-btn" disabled={!!busy || !status?.configured} onClick={() => void call("test", "POST", "/api/agentic/brain/test")}>
          {busy === "test" ? "Testing…" : "Test connection"}</button>
        {status?.key_source === "panel" && <button className="agentic-btn" disabled={!!busy}
          onClick={() => { if (window.confirm("Remove the saved API key? The lane falls back to rules-only.")) void call("clear", "PUT", "/api/agentic/brain", { clear_key: true }); }}>Remove key</button>}
        {status?.management_key_hint && <button className="agentic-btn" disabled={!!busy}
          onClick={() => { if (window.confirm("Remove the saved management key? The credit balance stops updating.")) void call("clearm", "PUT", "/api/agentic/brain", { clear_management_key: true }); }}>Remove management key</button>}
        {message && <span className="agentic-meta">{message}</span>}
      </div>
      {test && <div className={`agentic-test ${test.ok ? "ok" : "bad"}`}>
        {test.ok
          ? <>Connected. {test.models.length} models on this account; <b>{test.model}</b> {test.model_listed ? "is available" : "was NOT in the list — pick one below"}. Reply “{test.reply}” in {((test.latency_ms || 0) / 1000).toFixed(1)} s.</>
          : <>Test failed: {test.error}</>}
        {test.models.length > 0 && <div className="agentic-models">{test.models.map((m) => <button key={m} className="agentic-btn small" onClick={() => setModel(m)}>{m}</button>)}</div>}
      </div>}
    </div>}
  </div>;
}

type UsageTotals = { calls: number; prompt_tokens: number; cached_tokens: number; completion_tokens: number; reasoning_tokens: number; priced_calls: number; cost_usd: number };
type UsageDay = UsageTotals & { day: string; avg_latency_ms: number };
type Balance = { balance_usd?: number; changes?: { at: string | null; origin: string | null; amount_usd: number }[]; at: string; error: string | null };
type Usage = { as_of: string; model: string; periods: Record<"today" | "last_7_days" | "this_month" | "since_start", UsageTotals>; days: UsageDay[];
  cost_recorded_from: string | null; balance: Balance | null; balance_configured: boolean };
const PERIODS: [keyof Usage["periods"], string][] = [["today", "Today"], ["last_7_days", "Last 7 days"], ["this_month", "This month"], ["since_start", "Since start"]];
const tokens = (v: number) => (v >= 1_000_000 ? `${(v / 1_000_000).toFixed(2)} M` : v >= 1000 ? `${(v / 1000).toFixed(1)} k` : String(v));
const dollars = (v: number | null | undefined) => (v == null ? "—" : v < 1 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);
const istFull = (iso?: string | null) => (iso ? `${new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })} IST` : "—");
const shortDay = (day: string) => new Date(`${day}T12:00:00+05:30`).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

/** Grok usage from the lane's own journal (every response's usage block, no extra call to xAI) and, when a
 * management key is saved, the prepaid credit balance from xAI's Management API on request. */
function UsagePanel() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => requestJson<Usage>(`${API_URL}/api/agentic/usage`, { headers: headers() })
    .then((u) => { setUsage(u); setError(""); }).catch((reason) => setError(reason instanceof Error ? reason.message : "Unavailable")), []);
  useEffect(() => { void load(); const id = setInterval(() => void load(), 60_000); return () => clearInterval(id); }, [load]);
  const refresh = async () => {
    setBusy(true);
    try { await requestJson(`${API_URL}/api/agentic/usage/balance`, { method: "POST", headers: headers() }); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Balance failed"); } finally { setBusy(false); }
  };
  const days = usage?.days.slice(-21) || [];
  const peak = Math.max(1, ...days.map((d) => d.prompt_tokens + d.completion_tokens + d.reasoning_tokens));
  const balance = usage?.balance;
  return <div className="panel agentic-section agentic-usage">
    <div className="panel-title">Grok usage <small>{usage ? `${usage.model} · from each response's usage block · cost is xAI's own price per call (recorded since ${usage.cost_recorded_from ? istFull(usage.cost_recorded_from) : "the next cycle"}) · as of ${istFull(usage.as_of)}` : error || "Loading…"}</small></div>
    <div className="agentic-usage-grid">
      {PERIODS.map(([key, label]) => { const t = usage?.periods[key]; return <div key={key} className="agentic-usage-card">
        <small>{label}</small>
        <b>{t ? tokens(t.prompt_tokens + t.completion_tokens + t.reasoning_tokens) : "—"} <em>tokens</em></b>
        <span>{t ? `${t.calls} call${t.calls === 1 ? "" : "s"}` : ""}{t && t.priced_calls ? ` · ${dollars(t.cost_usd)}${t.priced_calls < t.calls ? ` (${t.priced_calls} priced)` : ""}` : ""}</span>
        {t && <span className="agentic-usage-split">in {tokens(t.prompt_tokens)}{t.cached_tokens ? ` (${tokens(t.cached_tokens)} cached)` : ""} · out {tokens(t.completion_tokens)}{t.reasoning_tokens ? ` · reasoning ${tokens(t.reasoning_tokens)}` : ""}</span>}
      </div>; })}
      <div className="agentic-usage-card agentic-usage-balance">
        <small>Prepaid credits</small>
        <b>{balance?.balance_usd != null ? `$${balance.balance_usd.toFixed(2)}` : "—"}</b>
        <span>{usage?.balance_configured
          ? balance ? (balance.error ? <span className="agentic-down">{balance.error}</span> : `checked ${istFull(balance.at)}`) : "not checked yet"
          : "add a management key and team id in Brain connection to read it"}</span>
        <span className="agentic-usage-split">Plan allowance (Included) is shown only on console.x.ai</span>
        {usage?.balance_configured && <button className="agentic-btn small" disabled={busy} onClick={() => void refresh()}>{busy ? "Checking…" : "Check balance"}</button>}
      </div>
    </div>
    {days.length > 0 && <div className="agentic-usage-days">
      <div className="agentic-usage-bars" role="img" aria-label="Tokens per IST day: input and output + reasoning">
        {days.map((d) => { const out = d.completion_tokens + d.reasoning_tokens;
          return <div key={d.day} className="agentic-usage-bar" title={`${shortDay(d.day)}: ${d.calls} calls · in ${tokens(d.prompt_tokens)} · out ${tokens(d.completion_tokens)} · reasoning ${tokens(d.reasoning_tokens)}${d.priced_calls ? ` · ${dollars(d.cost_usd)}` : ""} · avg ${(d.avg_latency_ms / 1000).toFixed(0)} s a call`}>
            <i style={{ height: `${(d.prompt_tokens / peak) * 100}%` }} className="in" /><i style={{ height: `${(out / peak) * 100}%` }} className="out" />
          </div>; })}
      </div>
      <div className="agentic-usage-axis"><span>{shortDay(days[0].day)}</span><span><s style={{ background: "#3987e5" }} />input <s style={{ background: "#d95926" }} />output + reasoning · hover a day</span><span>{shortDay(days[days.length - 1].day)}</span></div>
    </div>}
  </div>;
}

type TelegramStatus = { configured: boolean; token_hint: string | null; token_source: string; paired: boolean;
  chat_name: string | null; pairing_code: string | null; polling: boolean; last_error: string | null; last_sent_at: string | null };

/** Telegram: bot token (write-only), pairing by code, test message. Decisions go to the paired chat. */
function TelegramPanel() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const refresh = useCallback(() => requestJson<TelegramStatus>(`${API_URL}/api/agentic/telegram`, { headers: headers() })
    .then((s) => { setStatus(s); return s; }).catch(() => null), []);
  useEffect(() => { void refresh().then((s) => { if (s && !s.paired) setOpen(true); }); }, [refresh]);
  useEffect(() => {                       // while waiting for /pair, watch for the pairing to land
    if (!open || !status?.configured || status.paired) return;
    const timer = setInterval(() => void refresh(), 4000);
    return () => clearInterval(timer);
  }, [open, status?.configured, status?.paired, refresh]);

  const call = async (label: string, method: "PUT" | "POST", path: string, body?: unknown) => {
    setBusy(label); setMessage("");
    try {
      const response = await fetch(`${API_URL}${path}`, { method, headers: headers(true), body: body ? JSON.stringify(body) : undefined });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      setStatus(payload as TelegramStatus);
      return payload;
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Request failed");
      return null;
    } finally {
      setBusy("");
    }
  };

  return <div className="panel agentic-section agentic-brain">
    <button className="agentic-brain-head" onClick={() => setOpen(!open)}>
      <span>Telegram</span>
      <span className={status?.paired ? "agentic-up" : "agentic-down"}>
        {!status ? "…" : !status.configured ? "no bot token" : status.paired ? `paired with ${status.chat_name || "your chat"}${status.last_error ? " · last send failed" : ""}` : "waiting for /pair"}</span>
      <span>{open ? "▾" : "▸"}</span>
    </button>
    {open && <div className="agentic-brain-body">
      <p className="agentic-hint">Create a bot with <b>@BotFather</b> in Telegram (send <code>/newbot</code>, pick a name), paste its token here and Save.
        Then send the pairing code below to your bot. Only that chat can command the lane; decisions, exits and a day-end summary are sent there.
        Commands: /status /positions /journal /think [note] /pause /resume /close P01.</p>
      <div className="agentic-form">
        <label>Bot token<input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)}
          placeholder={status?.configured ? `saved ${status.token_hint} — leave blank to keep` : "123456789:AA…"} /></label>
      </div>
      <div className="agentic-controls">
        <button className="agentic-btn primary" disabled={!!busy || !token}
          onClick={() => { void call("save", "PUT", "/api/agentic/telegram", { bot_token: token }).then((s) => { if (s) setToken(""); }); }}>
          {busy === "save" ? "Saving…" : "Save"}</button>
        <button className="agentic-btn" disabled={!!busy || !status?.paired} onClick={() => void call("test", "POST", "/api/agentic/telegram/test")}>
          {busy === "test" ? "Sending…" : "Send test message"}</button>
        {status?.paired && <button className="agentic-btn" disabled={!!busy} onClick={() => void call("unpair", "PUT", "/api/agentic/telegram", { unpair: true })}>Unpair</button>}
        {status?.token_source === "panel" && <button className="agentic-btn" disabled={!!busy}
          onClick={() => { if (window.confirm("Remove the bot token?")) void call("clear", "PUT", "/api/agentic/telegram", { clear_token: true }); }}>Remove token</button>}
        {message && <span className="agentic-meta">{message}</span>}
      </div>
      {status?.configured && !status.paired && status.pairing_code && <div className="agentic-test ok">
        Send this to your bot in Telegram: <b className="agentic-code">/pair {status.pairing_code}</b> — this panel updates when it is paired.</div>}
      {status?.last_error && <div className="agentic-test bad">Telegram: {status.last_error}</div>}
    </div>}
  </div>;
}

type Graded = { grade: string; score: number; factors: [number, string][] };
type MatrixCell = Graded & { scorecard_agrees: boolean; swing?: Graded };
type MatrixRow = { index: string; CE: MatrixCell; PE: MatrixCell;
  inputs: { open_type?: string; open_dir?: number; ib_high?: number; ib_low?: number; ib_atr?: number; prev_range_atr?: number;
    prev_nr7?: boolean; prev_inside?: boolean; atr_points?: number } | null };
type Matrix = { at: string | null; lots_by_grade: Record<string, number>; indices: MatrixRow[] };

// The decision matrix (agentic_matrix.py): code grades every index x side from the tested factors and the grade caps
// the premium a new trade may put at risk. Refreshed every minute without a model call.
function MatrixPanel() {
  const [m, setM] = useState<Matrix | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const load = () => requestJson<Matrix>(`${API_URL}/api/agentic/matrix`, { headers: headers() }).then(setM).catch(() => undefined);
    void load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, []);
  if (!m) return null;
  const graded = (label: string, c: Graded) => <div><small>{label}</small> <b className={`agentic-grade grade-${c.grade}`}>{c.grade}</b> <small>score {c.score}</small>
    {open && <ul className="agentic-factors">{c.factors.map(([pts, why], i) => <li key={i}>{pts > 0 ? `+${pts}` : pts} {why}</li>)}</ul>}</div>;
  const cell = (c: MatrixCell) => <td>{graded("intraday", c)}{c.swing && graded("swing", c.swing)}{c.scorecard_agrees && <small>scorecard agrees</small>}</td>;
  return <div className="panel agentic-section">
    <div className="panel-title agentic-journal-head">Decision matrix — governs size{m.at ? ` · as of ${m.at}` : ""}
      <button className="agentic-btn small" onClick={() => setOpen(!open)}>{open ? "Hide factors" : "Show factors"}</button></div>
    <div className="agentic-empty">Most lots per position by the grade of its horizon (learning phase): {Object.entries(m.lots_by_grade || {}).map(([g, n]) => `${g} ${n}`).join(" · ")}.
      Intraday positions close at 15:15; swing positions run up to 5 sessions with an index invalidation level.</div>
    <table className="ledger-table agentic-table"><thead><tr><th>Index</th><th>Calls (CE)</th><th>Puts (PE)</th><th>Inputs</th></tr></thead>
      <tbody>{m.indices.map((r) => <tr key={r.index}><td><b>{r.index}</b></td>{cell(r.CE)}{cell(r.PE)}
        <td><small>{r.inputs?.open_type ? `open ${r.inputs.open_type}${r.inputs.open_dir ? (r.inputs.open_dir > 0 ? " up" : " down") : ""}` : "open type at 09:45"}
          {r.inputs?.ib_high ? ` · IB ${r.inputs.ib_low}–${r.inputs.ib_high} (${r.inputs.ib_atr} ATR)` : " · IB at 10:15"}
          {r.inputs?.prev_range_atr != null ? ` · prior day ${r.inputs.prev_range_atr} ATR${r.inputs.prev_nr7 ? " NR7" : ""}${r.inputs.prev_inside ? " inside" : ""}` : ""}</small></td></tr>)}</tbody></table>
  </div>;
}

export function AgenticPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState("");
  const [equity, setEquity] = useState<{ at: string; equity: number }[]>([]);
  const [fills, setFills] = useState<Fill[]>([]);
  const [closed, setClosed] = useState<ClosedPosition[]>([]);
  const [focus, setFocus] = useState<{ list: LaneChartFocus[]; index: number }>();
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, c, e] = await Promise.all([
        requestJson<Snapshot>(`${API_URL}/api/agentic`, { headers: headers() }),
        requestJson<{ cycles: Cycle[]; days: string[] }>(`${API_URL}/api/agentic/cycles?limit=60${day ? `&day=${day}` : ""}`, { headers: headers() }),
        requestJson<{ equity: { at: string; equity: number }[] }>(`${API_URL}/api/agentic/equity?today=true`, { headers: headers() }),
      ]);
      setSnap(s); setCycles(c.cycles); setDays(c.days); setEquity(e.equity); setError("");
      // Fills only feed the chart markers; an older backend without the route leaves them empty.
      requestJson<{ trades: Fill[]; closed_positions: ClosedPosition[] }>(`${API_URL}/api/agentic/book?today=true`, { headers: headers() })
        .then((book) => { setFills(book.trades); setClosed(book.closed_positions); }).catch(() => undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unavailable");
    }
  }, [day]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);

  const post = async (path: string, body?: unknown, label = path) => {
    setBusy(label);
    try {
      await requestJson(`${API_URL}${path}`, { method: "POST", headers: headers(true), body: body ? JSON.stringify(body) : undefined });
      setTimeout(() => void load(), 1500);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Request failed");
    } finally {
      setBusy("");
    }
  };

  const h = snap?.health;
  const p = snap?.portfolio;
  const dayPnl = useMemo(() => {
    if (!equity.length || !p) return null;
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
    const before = equity.filter((row) => row.at.slice(0, 10) < today);
    // Today's marks only (lane day from 08:00 IST): the first one is the opening level.
    return p.equity - (before.length ? before[before.length - 1].equity : equity[0]?.equity ?? snap!.capital);
  }, [equity, p, snap]);
  const spark = useMemo(() => {
    const pts = equity.slice(-400);
    if (pts.length < 2) return "";
    const lo = Math.min(...pts.map((r) => r.equity)), hi = Math.max(...pts.map((r) => r.equity));
    return pts.map((r, i) => `${(i / (pts.length - 1)) * 300},${hi > lo ? 40 - ((r.equity - lo) / (hi - lo)) * 40 : 20}`).join(" ");
  }, [equity]);

  return <section className="ledger-page agentic-page">
    <div className="page-heading">
      <div>
        <h1>Agentic lane</h1>
        <p>Paper options book run by {h?.brain === "grok" ? <b>Grok ({h.model})</b> : <b className="agentic-down">{h?.brain || "…"}</b>} · a cycle every {h?.cycle_minutes ?? "—"} min in the session and on triggers · code enforces the limits and the stops</p>
      </div>
      <div className="agentic-controls">
        <input className="heatmap-search agentic-note" placeholder="Note for the agent (optional), e.g. 'avoid stock puts today'" value={note} maxLength={1000}
          onChange={(event) => setNote(event.target.value)} />
        <button className="agentic-btn primary" disabled={!!busy} onClick={() => { void post("/api/agentic/think", { note: note || null }, "think"); setNote(""); }}>
          {busy === "think" ? "Asking…" : "Think now"}</button>
        {h?.paused
          ? <button className="agentic-btn" disabled={!!busy} onClick={() => void post("/api/agentic/resume")}>Resume</button>
          : <button className="agentic-btn" disabled={!!busy} onClick={() => void post("/api/agentic/pause")}>Pause</button>}
      </div>
    </div>
    {error && <div className="panel chart-empty error">{error}</div>}
    {h?.error && <div className="panel chart-empty error">Lane error: {h.error}</div>}
    <BrainPanel onChanged={() => void load()} />
    <TelegramPanel />

    <div className="agentic-cards">
      <div className="panel agentic-card"><small>Equity</small><b>{rupees(p?.equity)}</b><svg viewBox="0 0 300 40" preserveAspectRatio="none"><polyline points={spark} fill="none" stroke="#4da3ff" strokeWidth="1.5" /></svg></div>
      <div className="panel agentic-card"><small>Today</small><b className={tone(dayPnl ?? 0)}>{rupees(dayPnl)}</b><span>realized {rupees(p?.realized_pnl)} · open {rupees(p?.unrealized_pnl)}</span></div>
      <div className="panel agentic-card"><small>Cash / capital</small><b>{rupees(p?.cash)}</b><span>of {rupees(snap?.capital)}</span></div>
      <div className="panel agentic-card"><small>Brain</small><b>{h?.paused ? "Paused" : h?.brain === "grok" ? "Live" : "Rules only"}</b>
        <span>{h?.cycles_today ?? 0} cycles today · next {hhmm(h?.next_cycle_at)}{h?.pending_triggers?.length ? ` · trigger: ${h.pending_triggers.join("; ")}` : ""}</span></div>
    </div>

    <UsagePanel />

    <div className="panel agentic-section lane-equity">
      <div className="panel-title">Equity today <small>hover for the IST time, equity, return and fall from the peak · full history in Back office</small></div>
      <EquityChart points={equity} capital={snap?.capital ?? 0} height={230} />
    </div>

    <MatrixPanel />

    {(snap?.triggers || []).length > 0 && <div className="panel agentic-section">
      <div className="panel-title">Armed triggers ({snap!.triggers!.length}) — fire automatically between cycles</div>
      <table className="ledger-table agentic-table"><thead><tr><th>Id</th><th>Buys</th><th>When</th><th>Until</th><th>Stop / target</th><th>Why</th></tr></thead>
        <tbody>{snap!.triggers!.map((t) => <tr key={t.id}>
          <td><b>{t.id}</b><small>armed {t.armed_at} · {t.horizon || "intraday"}</small></td>
          <td>{t.contract.split(":")[1]} ×{t.lots}</td><td>{t.when}</td><td>{t.valid_until}</td>
          <td>{t.stop_premium ?? "—"} / {t.target_premium ?? "—"}</td><td className="agentic-why">{t.why}</td>
        </tr>)}</tbody></table>
    </div>}

    <div className="panel agentic-section">
      <div className="panel-title">Open positions ({p?.positions.filter((x) => x.quantity > 0).length ?? 0}) <small>click a contract for its chart</small></div>
      {p && p.positions.filter((x) => x.quantity > 0).length === 0 && <div className="agentic-empty">Flat.</div>}
      {p && p.positions.some((x) => x.quantity > 0) && <table className="ledger-table agentic-table"><thead><tr>
        <th>Contract</th><th>Lots</th><th>Avg → now</th><th>P&amp;L</th><th>Best / worst</th><th>Stop / target</th><th>Thesis and latest comment</th><th></th></tr></thead>
        <tbody>{newestFirst(p.positions.filter((x) => x.quantity > 0)).map((row, index, open) => <tr key={row.symbol}>
          <td className="openable-row" onClick={() => setFocus({ index, list: open.map((x) => ({ symbol: x.symbol, title: x.symbol.split(":")[1],
            subtitle: `${x.lots} lots @ ₹${x.average_price.toFixed(2)}${x.setup ? ` · ${x.setup.replace(/_/g, " ")}` : ""}`,
            markers: fillMarkers(fills, x.symbol, x.opened_at ?? undefined),
            levels: [{ price: x.average_price, label: "Avg", tone: "avg" as const },
              ...(x.stop_premium ? [{ price: x.stop_premium, label: "Stop", tone: "stop" as const }] : []),
              ...(x.target_premium ? [{ price: x.target_premium, label: "Target", tone: "target" as const }] : [])] })) })}><b>{row.symbol.split(":")[1]}</b><small>{row.expiry ? ` exp ${row.expiry.slice(5)}` : ""}{row.booked ? " · half booked" : ""}</small>
            <small>{row.horizon || "intraday"}{row.setup ? ` · ${row.setup.replace(/_/g, " ")}` : ""}{row.grade ? ` · grade ${row.grade}` : ""}{row.invalidation_level ? ` · wrong past ${row.invalidation_level}` : ""}</small></td>
          <td>{row.lots}</td>
          <td>{row.average_price.toFixed(2)} → {row.last_price.toFixed(2)}</td>
          <td className={tone(row.unrealized_pnl)}>{rupees(row.unrealized_pnl)}<small> {pct(row.return_pct)}</small></td>
          <td>{pct(row.max_return_pct, 0)} / {pct(row.min_return_pct, 0)}</td>
          <td>{row.stop_premium ? row.stop_premium.toFixed(2) : "—"} / {row.target_premium ? row.target_premium.toFixed(2) : "—"}</td>
          <td className="agentic-why"><div>{row.thesis || "—"}</div>{row.last_comment && row.last_comment !== row.thesis && <small>Latest: {row.last_comment}</small>}</td>
          <td><button className="agentic-btn small" disabled={!!busy}
            onClick={() => { if (window.confirm(`Close ${row.symbol} (${row.lots} lots) at market? (paper)`)) void post("/api/agentic/close-position", { symbol: row.symbol, expected_quantity: row.quantity }, row.symbol); }}>Close</button></td>
        </tr>)}</tbody></table>}
    </div>

    {closed.length > 0 && <div className="panel agentic-section">
      <div className="panel-title">Closed today ({closed.length}) <small>click a row for its chart</small></div>
      <table className="ledger-table agentic-table"><thead><tr><th>Contract</th><th>Lots</th><th>Entry → exit</th><th>P&amp;L</th><th>Best / worst</th><th>Exit reason</th></tr></thead>
        <tbody>{closed.map((row, index) => <tr key={row.closed_id} className="openable-row" onClick={() => setFocus({ index, list: closed.map((x) => ({
          symbol: x.symbol, title: x.symbol.split(":")[1], subtitle: `${x.lots} lots · ${(x.exit_reason || "").replace(/_/g, " ").toLowerCase()} ${pct(x.return_pct)}`,
          markers: fillMarkers(fills, x.symbol, x.entry_time, x.exit_time) })) })}>
          <td><b>{row.symbol.split(":")[1]}</b>{row.partial && <small> part</small>}</td><td>{row.lots}</td>
          <td>{row.entry_price.toFixed(2)} → {row.exit_price.toFixed(2)}</td>
          <td className={tone(row.realized_pnl)}>{rupees(row.realized_pnl)}<small> {pct(row.return_pct)}</small></td>
          <td>{pct(row.max_return_pct ?? undefined, 0)} / {pct(row.min_return_pct ?? undefined, 0)}</td>
          <td>{(row.exit_reason || "—").replace(/_/g, " ").toLowerCase()}</td></tr>)}</tbody></table>
    </div>}

    {(snap?.setups || []).length > 0 && <div className="panel agentic-section">
      <div className="panel-title">Setup scoreboard — what the lane is learning</div>
      <table className="ledger-table agentic-table"><thead><tr><th>Setup</th><th>Horizon</th><th>Trades</th><th>Wins</th><th>Avg return</th><th>Net P&amp;L</th></tr></thead>
        <tbody>{snap!.setups!.map((r) => <tr key={`${r.setup}-${r.horizon}`}><td>{r.setup.replace(/_/g, " ")}</td><td>{r.horizon}</td>
          <td>{r.trades}</td><td>{r.wins}</td><td>{r.avg_return_pct == null ? "—" : pct(r.avg_return_pct)}</td>
          <td className={tone(r.net_pnl_rs)}>{rupees(r.net_pnl_rs)}</td></tr>)}</tbody></table>
    </div>}

    <div className="panel agentic-section">
      <div className="panel-title agentic-journal-head">Decision journal
        <select className="heatmap-search" value={day} onChange={(event) => setDay(event.target.value)}>
          <option value="">Latest</option>{days.map((d) => <option key={d} value={d}>{d}</option>)}</select>
      </div>
      {cycles.length === 0 && <div className="agentic-empty">No cycles yet. The first runs at 09:20 on a trading day, or press “Think now”.</div>}
      {cycles.map((c) => <article key={c.cycle_id} className="agentic-cycle">
        <header>
          <b>{hhmm(c.at)}</b>
          <span className={`agentic-chip ${c.regime || ""}`}>{c.regime || "—"}{c.confidence !== null ? ` · ${Math.round((c.confidence || 0) * 100)}%` : ""}</span>
          <span className="agentic-meta">{c.trigger} · {c.mode === "grok" ? c.model : "rules only"}{c.latency_ms ? ` · ${(c.latency_ms / 1000).toFixed(1)} s` : ""}{c.prompt_tokens ? ` · ${c.prompt_tokens}/${c.completion_tokens}${c.reasoning_tokens ? `+${c.reasoning_tokens}r` : ""} tok` : ""}{c.cost_ticks ? ` · $${(c.cost_ticks / 1e10).toFixed(4)}` : ""}</span>
        </header>
        {c.operator_note && <p className="agentic-operator">You: {c.operator_note}</p>}
        <p className="agentic-view">{c.market_view}</p>
        {c.error && <p className="agentic-down">Model call failed: {c.error}</p>}
        {(c.accepted || []).length > 0 && <ul className="agentic-actions">{(c.accepted || []).map((a, i) => {
          const done = (c.executed || [])[i];
          return <li key={i}><span className={`agentic-act ${a.action}`}>{ACTION_LABEL[a.action] || a.action}</span>
            <b>{a.symbol?.split(":")[1] || a.contract_id || a.position_id}</b>{a.lots ? ` ×${a.lots}` : ""}
            {a.stop_premium ? ` · stop ${a.stop_premium}` : ""}{a.target_premium ? ` · target ${a.target_premium}` : ""}
            {done && done.status !== "done" && <small className={done.status === "failed" ? "agentic-down" : ""}> · {done.status}{done.price ? ` @ ${done.price.toFixed(2)}` : ""}{done.error ? `: ${done.error}` : ""}</small>}
            <div className="agentic-reason">{a.reason}{a.note ? ` (${a.note})` : ""}</div></li>;
        })}</ul>}
        {(c.rejected || []).length > 0 && <details className="agentic-rejected"><summary>{(c.rejected || []).length} blocked by the limits</summary>
          <ul>{(c.rejected || []).map((a, i) => <li key={i}><b>{ACTION_LABEL[a.action] || a.action} {a.contract_id || a.position_id}</b> — {a.rejected}. <small>{a.reason}</small></li>)}</ul></details>}
        {c.notes && <p className="agentic-notes">Note to self: {c.notes}</p>}
      </article>)}
    </div>
  </section>;
}
