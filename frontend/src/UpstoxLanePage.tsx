import { useEffect, useState } from "react";
import "./shadowLanes.css";

// The Upstox live lane (upstox_live/lane.py, served at /api/upstox-live/ by nginx). Read-only view: this page never
// places, changes or cancels an order, and it has no control to arm the lane (the owner does that on the Mac).
type Pos = { symbol: string; instrument: string; quantity: number; average_price: number; last_price?: number | null;
  unrealized_pnl?: number | null; return_pct?: number | null; opened_at?: string; macd_seen?: boolean; realized?: number; lot_size?: number };
type Closed = Pos & { closed_at?: string; exit_price?: number };
type Order = { order_id: string; symbol: string; side: string; quantity: number; price: number; placed_at: string; reason?: string;
  status?: string; filled?: number; message?: string; dry?: boolean; open?: boolean };
type Fill = { order_id: string; symbol: string; side: string; quantity: number; price: number; at: string };
type Decision = { at: string; kind: string; symbol?: string; [k: string]: unknown };
type Snapshot = { label: string; armed: boolean; mode: string; capital: number; deployed: number; free: number; realized: number;
  unrealized: number; equity: number; upstox_funds?: { available_margin?: number; used_margin?: number } | null;
  positions: Pos[]; closed: Closed[]; orders: Order[]; fills: Fill[]; decisions: Decision[];
  account: { positions: Record<string, unknown>[]; orders: Record<string, unknown>[]; trades: Record<string, unknown>[] };
  macd_open: string[]; macd_checked_at?: string; read_at?: string; read_error?: string | null; engine_error?: string | null;
  rules: Record<string, string>; checked_at: string };

const TABS = ["Positions", "Orders", "Trades", "Decisions", "Upstox account", "Rules"] as const;
type Tab = typeof TABS[number];
const rs = (v?: number | null, signed = false) => v == null ? "—" : `${v < 0 ? "−" : signed && v > 0 ? "+" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const px = (v?: number | null) => v == null ? "—" : v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const tone = (v?: number | null) => v == null || v === 0 ? "" : v > 0 ? "positive" : "negative";
const sym = (s: string) => s.replace(/^(NSE|BSE):/, "");
function stamp(value?: string) {
  if (!value) return "—";
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(d) + " IST";
}
const detail = (d: Decision) => Object.entries(d).filter(([k]) => !["at", "kind", "symbol"].includes(k))
  .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ");

export function UpstoxLanePage() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("Positions");
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const r = await fetch("/api/upstox-live/snapshot", { signal: controller.signal, cache: "no-store" });
        if (!r.ok) throw new Error(`Upstox lane service returned ${r.status}`);
        setData(await r.json()); setError("");
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
    };
    void load(); const timer = window.setInterval(load, 5_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);
  const d = data;
  return <section className="agent-page shadow-lane-page">
    <div className="agent-head panel-row"><div><b>Upstox live lane · MACD signals</b>
      <span className="agent-sub">Real Upstox orders inside a {rs(d?.capital)} fund: the first new MACD signal whose one lot fits is bought; it is sold when the MACD lane closes it.</span></div>
      <span className="agent-chip" style={d?.armed ? { background: "#5a1f24", color: "#ffb4b4" } : undefined}>{d ? (d.armed ? "ARMED · real orders" : "DRY RUN · no orders sent") : "…"}</span></div>
    <nav className="shadow-tabs" aria-label="Upstox lane views">{TABS.map(t => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}</nav>
    {error && <div className="agent-error" role="alert">{error}</div>}
    {d?.read_error && <div className="agent-error" role="alert">Upstox read failed: {d.read_error}</div>}
    {d?.engine_error && <div className="agent-error" role="alert">MACD engine read failed: {d.engine_error}</div>}
    {!d && !error && <div className="agent-empty" role="status">Loading Upstox lane…</div>}
    {d && <>
      <div className="shadow-metrics">
        <div className="panel"><small>Equity</small><b>{rs(d.equity)}</b><span>fund {rs(d.capital)}</span></div>
        <div className="panel"><small>Booked / open P&amp;L</small><b className={tone(d.realized + d.unrealized)}>{rs(d.realized, true)} / {rs(d.unrealized, true)}</b><span>{d.positions.length} open · {d.closed.length} closed</span></div>
        <div className="panel"><small>Free for the next signal</small><b>{rs(d.free)}</b><span>deployed {rs(d.deployed)}</span></div>
        <div className="panel"><small>Upstox available margin</small><b>{rs(d.upstox_funds?.available_margin)}</b><span>read {stamp(d.read_at)}</span></div>
      </div>
      {tab === "Positions" && <div className="panel shadow-panel"><h2>Open positions</h2>
        {!d.positions.length ? <div className="agent-empty">No open positions.</div> : <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Contract</th><th>Qty</th><th>Avg</th><th>LTP</th><th>P&amp;L</th><th>Return</th><th>Opened</th><th>MACD lane</th></tr></thead>
          <tbody>{d.positions.map(p => <tr key={p.symbol}><td>{sym(p.symbol)}</td><td>{p.quantity}</td><td>{px(p.average_price)}</td><td>{px(p.last_price)}</td><td className={tone(p.unrealized_pnl)}>{rs(p.unrealized_pnl, true)}</td><td className={tone(p.return_pct)}>{p.return_pct == null ? "—" : `${p.return_pct.toFixed(1)}%`}</td><td>{stamp(p.opened_at)}</td><td>{d.macd_open.includes(p.symbol) ? "holding" : p.macd_seen ? "closed → exiting" : "not yet seen"}</td></tr>)}</tbody></table></div>}
        <h2>Closed</h2>
        {!d.closed.length ? <div className="agent-empty">No closed trades yet.</div> : <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Contract</th><th>Qty</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Opened</th><th>Closed</th></tr></thead>
          <tbody>{[...d.closed].reverse().map((p, i) => <tr key={`${p.symbol}-${i}`}><td>{sym(p.symbol)}</td><td>{p.lot_size ?? "—"}</td><td>{px(p.average_price)}</td><td>{px(p.exit_price)}</td><td className={tone(p.realized)}>{rs(p.realized, true)}</td><td>{stamp(p.opened_at)}</td><td>{stamp(p.closed_at)}</td></tr>)}</tbody></table></div>}
      </div>}
      {tab === "Orders" && <div className="panel shadow-panel"><h2>Lane orders</h2>
        {!d.orders.length ? <div className="agent-empty">No orders yet.</div> : <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Placed</th><th>Contract</th><th>Side</th><th>Qty</th><th>Limit</th><th>Status</th><th>Filled</th><th>Why</th><th>Order id</th></tr></thead>
          <tbody>{d.orders.map(o => <tr key={o.order_id}><td>{stamp(o.placed_at)}</td><td>{sym(o.symbol)}</td><td className={o.side === "BUY" ? "positive" : "negative"}>{o.side}</td><td>{o.quantity}</td><td>{px(o.price)}</td><td>{o.status}{o.message ? ` · ${o.message}` : ""}</td><td>{o.filled ?? 0}</td><td>{o.reason}</td><td>{o.dry ? "dry run" : o.order_id}</td></tr>)}</tbody></table></div>}</div>}
      {tab === "Trades" && <div className="panel shadow-panel"><h2>Fills (from Upstox trades)</h2>
        {!d.fills.length ? <div className="agent-empty">No fills yet.</div> : <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Time</th><th>Contract</th><th>Side</th><th>Qty</th><th>Price</th><th>Value</th><th>Order id</th></tr></thead>
          <tbody>{d.fills.map((f, i) => <tr key={`${f.order_id}-${i}`}><td>{stamp(f.at)}</td><td>{sym(f.symbol)}</td><td className={f.side === "BUY" ? "positive" : "negative"}>{f.side}</td><td>{f.quantity}</td><td>{px(f.price)}</td><td>{rs(f.price * f.quantity)}</td><td>{f.order_id}</td></tr>)}</tbody></table></div>}</div>}
      {tab === "Decisions" && <div className="panel shadow-panel"><h2>Every signal and what the lane did</h2>
        {!d.decisions.length ? <div className="agent-empty">No new MACD signals since the lane started.</div> : <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Time</th><th>Decision</th><th>Contract</th><th>Detail</th></tr></thead>
          <tbody>{d.decisions.map((r, i) => <tr key={`${r.at}-${i}`}><td>{stamp(r.at)}</td><td>{r.kind.replace(/_/g, " ").toLowerCase()}</td><td>{r.symbol ? sym(r.symbol) : "—"}</td><td>{detail(r)}</td></tr>)}</tbody></table></div>}</div>}
      {tab === "Upstox account" && <div className="panel shadow-panel"><h2>Whole Upstox account today (all orders, not only this lane's)</h2>
        <p className="agent-sub">Positions {d.account.positions.length} · orders {d.account.orders.length} · trades {d.account.trades.length} · read {stamp(d.read_at)}</p>
        {d.account.positions.length > 0 && <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Instrument</th><th>Qty</th><th>Avg buy</th><th>LTP</th><th>P&amp;L</th><th>Product</th></tr></thead>
          <tbody>{d.account.positions.map((p, i) => <tr key={i}><td>{String(p.trading_symbol ?? p.tradingsymbol ?? p.instrument_token)}</td><td>{String(p.quantity ?? "")}</td><td>{px(Number(p.buy_price ?? p.average_price))}</td><td>{px(Number(p.last_price))}</td><td className={tone(Number(p.pnl))}>{rs(Number(p.pnl), true)}</td><td>{String(p.product ?? "")}</td></tr>)}</tbody></table></div>}
        {d.account.orders.length > 0 && <div className="live-table-wrap"><table className="live-table"><thead><tr><th>Time</th><th>Instrument</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th><th>Tag</th></tr></thead>
          <tbody>{d.account.orders.map((o, i) => <tr key={i}><td>{String(o.order_timestamp ?? "")}</td><td>{String(o.trading_symbol ?? o.tradingsymbol ?? o.instrument_token)}</td><td>{String(o.transaction_type ?? "")}</td><td>{String(o.quantity ?? "")}</td><td>{px(Number(o.average_price || o.price))}</td><td>{String(o.status ?? "")}</td><td>{String(o.tag ?? "")}</td></tr>)}</tbody></table></div>}
      </div>}
      {tab === "Rules" && <div className="panel shadow-panel"><h2>How the lane trades</h2>
        {Object.entries(d.rules).map(([k, v]) => <p key={k}><b>{k}:</b> {v}</p>)}
        <p className="agent-sub">MACD lane holds {d.macd_open.length} contracts (checked {stamp(d.macd_checked_at)}). This page only reads; arming, disarming and stopping happen on the Mac.</p></div>}
    </>}
  </section>;
}
