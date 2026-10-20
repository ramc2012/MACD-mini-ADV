import { useCallback, useEffect, useMemo, useState } from "react";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";

// The agentic lane: a Grok-run paper options book. Every cycle the model reads the market and the book and
// answers with actions and its reasoning; code validates and executes them. This page shows the book with
// each position's thesis, the decision journal, and lets the operator ask for a cycle now (with a note),
// pause the lane, or close a position by hand.
type Position = {
  symbol: string; quantity: number; lots: number; lot_size: number; average_price: number; last_price: number;
  unrealized_pnl: number; return_pct: number; max_return_pct: number; min_return_pct: number;
  thesis?: string | null; last_comment?: string | null; stop_premium?: number | null; target_premium?: number | null;
  expiry?: string | null; booked?: number | null; opened_at?: string | null;
};
type Health = {
  enabled: boolean; paused: boolean; brain: string; model: string; cycle_minutes: number; next_cycle_at: string | null;
  cycles_today: number; pending_triggers: string[]; error: string | null; open_positions: number;
  last_cycle: { at: string; trigger: string; mode: string; market_view: string; regime: string; error: string | null } | null;
  limits: Record<string, number | string>;
};
type Snapshot = { capital: number; health: Health; portfolio: { cash: number; equity: number; realized_pnl: number; unrealized_pnl: number; positions: Position[] } };
type Act = { action: string; symbol?: string; contract_id?: string | null; position_id?: string | null; lots?: number | null;
  reason?: string; note?: string | null; rejected?: string; stop_premium?: number | null; target_premium?: number | null };
type Exec = { action: string; symbol: string; lots?: number | null; status: string; price?: number | null; error?: string };
type Cycle = { cycle_id: string; at: string; trigger: string; mode: string; model: string | null; market_view: string; regime: string | null;
  confidence: number | null; notes: string | null; operator_note: string | null; accepted: Act[] | null; rejected: Act[] | null;
  executed: Exec[] | null; latency_ms: number | null; prompt_tokens: number | null; completion_tokens: number | null; error: string | null };

const headers = (json = false): HeadersInit => ({ ...(API_TOKEN ? { "x-macd-token": API_TOKEN } : {}), ...(json ? { "Content-Type": "application/json" } : {}) });
const rupees = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `₹${Math.round(v).toLocaleString("en-IN")}`);
const pct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const hhmm = (iso?: string | null) => (iso ? new Date(iso).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false }) : "—");
const tone = (v: number) => (v > 0 ? "agentic-up" : v < 0 ? "agentic-down" : "");
const ACTION_LABEL: Record<string, string> = { open: "OPEN", add: "ADD", reduce: "REDUCE", close: "CLOSE", set_levels: "LEVELS", hold: "HOLD" };

type BrainTest = { ok: boolean; models: string[]; model: string; model_listed: boolean | null; reply: string | null;
  latency_ms: number | null; error: string | null; at: string | null };
type BrainStatus = { configured: boolean; key_source: "panel" | "environment" | "none"; key_hint: string | null; model: string;
  api_base: string; reasoning_effort: string; efforts: string[]; default_model: string; last_test: BrainTest | null;
  universe: string; universes: string[]; cycle_minutes: number; cycle_choices: number[] };

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
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const apply = (s: BrainStatus) => {
    setStatus(s); setModel(s.model); setEffort(s.reasoning_effort); setBase(s.api_base); setUniverse(s.universe); setCycle(s.cycle_minutes);
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
    const saved = await call("save", "PUT", "/api/agentic/brain", { api_key: key || null, model, api_base: base, reasoning_effort: effort, universe, cycle_minutes: cycle });
    if (saved) { setKey(""); setMessage("Saved."); }
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
      <p className="agentic-hint">Use an API key from <b>console.x.ai</b> (API usage is billed there from prepaid credits; a Grok / X Premium
        subscription does not include API access). The key is stored only in the app's runtime folder (owner-only file) and is never shown again.</p>
      <div className="agentic-form">
        <label>API key<input type="password" autoComplete="off" placeholder={status?.configured ? `saved ${status.key_hint} — leave blank to keep` : "xai-…"}
          value={key} onChange={(event) => setKey(event.target.value)} /></label>
        <label>Model<input list="agentic-models" value={model} placeholder={status?.default_model} onChange={(event) => setModel(event.target.value)} />
          <datalist id="agentic-models">{(test?.models || []).map((m) => <option key={m} value={m} />)}</datalist></label>
        <label>Reasoning effort<select value={effort} onChange={(event) => setEffort(event.target.value)}>
          <option value="">Model default</option>{(status?.efforts || []).map((e) => <option key={e} value={e}>{e}</option>)}</select></label>
        <label>API base<input value={base} onChange={(event) => setBase(event.target.value)} /></label>
        <label>Universe<select value={universe} onChange={(event) => setUniverse(event.target.value)}>
          <option value="index">Index options only (NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY)</option>
          <option value="all">Index + F&amp;O stock options</option></select></label>
        <label>Cycle<select value={cycle} onChange={(event) => setCycle(Number(event.target.value))}>
          {(status?.cycle_choices || [15, 30, 60, 120]).map((m) => <option key={m} value={m}>Every {m} min</option>)}</select></label>
      </div>
      <div className="agentic-controls">
        <button className="agentic-btn primary" disabled={!!busy} onClick={() => void save()}>{busy === "save" ? "Saving…" : "Save"}</button>
        <button className="agentic-btn" disabled={!!busy || !status?.configured} onClick={() => void call("test", "POST", "/api/agentic/brain/test")}>
          {busy === "test" ? "Testing…" : "Test connection"}</button>
        {status?.key_source === "panel" && <button className="agentic-btn" disabled={!!busy}
          onClick={() => { if (window.confirm("Remove the saved API key? The lane falls back to rules-only.")) void call("clear", "PUT", "/api/agentic/brain", { clear_key: true }); }}>Remove key</button>}
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

export function AgenticPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState("");
  const [equity, setEquity] = useState<{ at: string; equity: number }[]>([]);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, c, e] = await Promise.all([
        requestJson<Snapshot>(`${API_URL}/api/agentic`, { headers: headers() }),
        requestJson<{ cycles: Cycle[]; days: string[] }>(`${API_URL}/api/agentic/cycles?limit=60${day ? `&day=${day}` : ""}`, { headers: headers() }),
        requestJson<{ equity: { at: string; equity: number }[] }>(`${API_URL}/api/agentic/equity`, { headers: headers() }),
      ]);
      setSnap(s); setCycles(c.cycles); setDays(c.days); setEquity(e.equity); setError("");
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
    return p.equity - (before.length ? before[before.length - 1].equity : snap!.capital);
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

    <div className="panel agentic-section">
      <div className="panel-title">Open positions ({p?.positions.filter((x) => x.quantity > 0).length ?? 0})</div>
      {p && p.positions.filter((x) => x.quantity > 0).length === 0 && <div className="agentic-empty">Flat.</div>}
      {p && p.positions.some((x) => x.quantity > 0) && <table className="ledger-table agentic-table"><thead><tr>
        <th>Contract</th><th>Lots</th><th>Avg → now</th><th>P&amp;L</th><th>Best / worst</th><th>Stop / target</th><th>Thesis and latest comment</th><th></th></tr></thead>
        <tbody>{p.positions.filter((x) => x.quantity > 0).map((row) => <tr key={row.symbol}>
          <td><b>{row.symbol.split(":")[1]}</b><small>{row.expiry ? ` exp ${row.expiry.slice(5)}` : ""}{row.booked ? " · half booked" : ""}</small></td>
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
          <span className="agentic-meta">{c.trigger} · {c.mode === "grok" ? c.model : "rules only"}{c.latency_ms ? ` · ${(c.latency_ms / 1000).toFixed(1)} s` : ""}{c.prompt_tokens ? ` · ${c.prompt_tokens}/${c.completion_tokens} tok` : ""}</span>
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
