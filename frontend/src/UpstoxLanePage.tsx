import { useCallback, useEffect, useMemo, useState } from "react";
import { readableUpstreamError } from "./apiError";
import { rupees, tone } from "./agentMath";
import { TradingLedger, type FocusRow } from "./Ledger";
import { LaneChart, LaneChartModal, type LaneChartFocus } from "./LaneChart";
import { UpstoxClosedTrades, UpstoxPerformance, UpstoxTrades, type ChargeDay, type ClosedTrade, type TradeRow } from "./UpstoxBook";
import type { Charges } from "./upstoxBookMath";
import { fillMarkers, positionLevels } from "./laneChartMarks";
import type { ClosedPosition, Order, Portfolio, Position, Tick, Trade } from "./types";
import { inLaneDay, laneDayStart } from "./laneDay";

// The Upstox live lane (upstox_live/lane.py, served at /api/upstox-live/ by nginx): MACD lane signals traded with
// REAL Upstox orders sized from the account's available margin (return % is on the Rs 25,000 start). Laid out like the MACD and Spot lanes -- summary cards, then
// Terminal / Portfolio / Status / Orders / Trades / Performance / Journal -- and fed into the same ledger tables.
// Read-only: this page never places, changes or cancels an order; the owner arms once (Settings -> Upstox live) and
// the lane stays armed until Kill.
type LanePos = { symbol: string; instrument: string; quantity: number; average_price: number; last_price?: number | null;
  unrealized_pnl?: number | null; return_pct?: number | null; opened_at?: string | null; macd_seen?: boolean; realized?: number; lot_size?: number | null; bought?: number };
type LaneClosed = LanePos & { closed_at?: string; exit_price?: number; charges?: Charges; charges_pending?: boolean; net_realized?: number;
  initiated_by?: "lane" | "owner"; closed_by?: "lane" | "owner" };
type LaneOrder = { order_id: string; symbol: string; side: string; quantity: number; price: number; placed_at: string; reason?: string;
  status?: string; filled?: number; message?: string | null; dry?: boolean; open?: boolean; lot_size?: number | null };
type LaneFill = { order_id: string; symbol: string; side: string; quantity: number; price: number; at: string };
type Decision = { at: string; kind: string; symbol?: string; [k: string]: unknown };
type Snapshot = { label: string; armed: boolean; mode: string; capital: number; deployed: number; free: number; realized: number;
  cap?: number | null; fund_basis?: string; in_flight?: number; charges_allowance?: number; armed_since?: string | null;
  settings?: { sides: "BOTH" | "CE" | "PE" }; weekly_index_roots?: string[];
  unrealized: number; equity: number; upstox_funds?: { available_margin?: number; used_margin?: number; payin_amount?: number } | null;
  positions: LanePos[]; closed: LaneClosed[]; orders: LaneOrder[]; fills: LaneFill[]; decisions: Decision[];
  trade_rows?: TradeRow[]; charges_total?: number; open_charges?: number; realized_net?: number; charge_days?: Record<string, ChargeDay>;
  charges_error?: { day?: string; error: string } | null;
  account: { positions: Record<string, unknown>[]; orders: Record<string, unknown>[]; trades: Record<string, unknown>[]; lane_instruments?: string[] };
  macd_open: string[]; macd_checked_at?: string; read_at?: string | null; read_error?: string | null; engine_error?: string | null;
  order_api?: { ok: boolean; detail: string; checked_at: string } | null; orders_today?: { day: string; count: number }; max_orders_per_day?: number;
  started_at?: string; rules: Record<string, string>; checked_at: string };

type Tab = "TERMINAL" | "PORTFOLIO" | "STATUS" | "ORDERS" | "TRADES" | "PERFORMANCE" | "JOURNAL";
// Portfolio first and the default (owner, 18 Nov 2026); a new store key drops the "Terminal" every browser had saved.
const TABS: [Tab, string][] = [["PORTFOLIO", "Portfolio"], ["TERMINAL", "Terminal"], ["STATUS", "Status"], ["ORDERS", "Orders"], ["TRADES", "Trades"],
  ["PERFORMANCE", "Performance"], ["JOURNAL", "Journal"]];
const TAB_STORE = "macd.upstoxTab.v2";
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { /* not kept */ } };

const when = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return iso;
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.weekday} ${p.day} ${p.month} ${p.year} ${p.hour}:${p.minute}:${p.second} IST`;
};
const istDay = (iso: string) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(iso));
const short = (symbol: string) => symbol.replace(/^(NSE|BSE):/, "");
const percent = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const words = (text: string | null | undefined) => (text || "—").replace(/_/g, " ").toLowerCase();
const rupeeText = (value: number) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
const DECISION_TONE: Record<string, string> = { ORDER_SENT: "positive", FILL: "positive", CLOSED: "positive",
  ORDER_ERROR: "negative", ORDER_REJECTED: "negative", CANCEL_ERROR: "negative" };
const ENTRY_KINDS = ["ORDER_SENT", "SKIP", "SKIP_FUNDS", "ORDER_ERROR", "ORDER_LIMIT"];
const detail = (d: Decision) => Object.entries(d).filter(([k]) => !["at", "kind", "symbol"].includes(k))
  .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ");

/** 08:00 IST on the next weekday that is not an exchange holiday: when the MACD desk rolls a closed trade off its book. */
function visibleUntil(exit: string, holidays: string[] = []): string {
  const day = new Date(`${istDay(exit)}T08:00:00+05:30`);
  const weekdayIst = (t: Date) => new Date(t.getTime() + 5.5 * 3600_000).getUTCDay();
  do { day.setUTCDate(day.getUTCDate() + 1); } while ([0, 6].includes(weekdayIst(day)) || holidays.includes(istDay(day.toISOString())));
  return day.toISOString();
}

export function UpstoxLanePage({ ticks = {}, holidays }: { ticks?: Record<string, Tick>; holidays?: string[] }) {
  const [tab, setTab] = useState<Tab>(() => (TABS.some(([key]) => key === recall(TAB_STORE)) ? recall(TAB_STORE) as Tab : "PORTFOLIO"));
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [kind, setKind] = useState("");
  const [journalView, setJournalView] = useState<"DECISIONS" | "CLOSED">(() => recall("macd.upstoxJournal") === "CLOSED" ? "CLOSED" : "DECISIONS");
  const pickJournal = (next: "DECISIONS" | "CLOSED") => { setJournalView(next); remember("macd.upstoxJournal", next); };
  const [focus, setFocus] = useState<{ list: LaneChartFocus[]; index: number }>();
  const pickTab = (next: Tab) => { setTab(next); remember(TAB_STORE, next); };

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const r = await fetch("/api/upstox-live/snapshot", { signal: controller.signal, cache: "no-store" });
        if (!r.ok) throw new Error(`Upstox lane service returned HTTP ${r.status}`);
        setData(await r.json()); setError("");
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
    };
    void load(); const timer = window.setInterval(load, 5_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);

  const d = data;
  const capital = d?.capital ?? 25_000;
  const lotOf = useCallback((symbol: string, fallback?: number | null) =>
    fallback || d?.positions.find((p) => p.symbol === symbol)?.lot_size || d?.orders.find((o) => o.symbol === symbol && o.lot_size)?.lot_size || 1, [d]);

  // The lane's records in the shapes the MACD ledger tables read.
  const positions = useMemo<Position[]>(() => (d?.positions ?? []).map((p) => {
    const last = p.last_price ?? ticks[p.symbol]?.ltp ?? p.average_price;
    const lot = lotOf(p.symbol, p.lot_size);
    return { symbol: p.symbol, position_id: `${p.symbol}|${p.opened_at ?? ""}`, quantity: p.quantity, lots: Math.max(1, Math.round(p.quantity / lot)), lot_size: lot,
      average_price: p.average_price, last_price: last, unrealized_pnl: (last - p.average_price) * p.quantity,
      return_pct: p.average_price ? (last / p.average_price - 1) * 100 : 0, opened_at: p.opened_at || d?.checked_at || "" };
  }), [d, ticks, lotOf]);
  const orders = useMemo<Order[]>(() => (d?.orders ?? []).filter((o) => !o.dry).map((o) => {
    const lot = lotOf(o.symbol, o.lot_size);
    const fill = d?.fills.find((f) => f.order_id === o.order_id);
    return { order_id: o.order_id, symbol: o.symbol, side: o.side, quantity: o.quantity, lots: Math.max(1, Math.round(o.quantity / lot)), lot_size: lot,
      order_type: "LIMIT", status: (o.status || (o.open ? "open" : "—")).toUpperCase(), fill_price: fill?.price,
      created_at: o.placed_at, reject_reason: o.message || (o.reason ? `${o.reason} · limit ${o.price.toFixed(2)}` : null) };
  }), [d, lotOf]);
  const trades = useMemo<Trade[]>(() => (d?.fills ?? []).map((f, i) => {
    const lot = lotOf(f.symbol);
    return { trade_id: `${f.order_id}-${i}`, order_id: f.order_id, symbol: f.symbol, side: f.side, quantity: f.quantity,
      lots: Math.max(1, Math.round(f.quantity / lot)), lot_size: lot, price: f.price, timestamp: f.at };
  }), [d, lotOf]);
  const closedRows = useMemo<ClosedPosition[]>(() => (d?.closed ?? []).map((c, i) => {
    const qty = c.bought || c.quantity || c.lot_size || 1;
    const lot = c.lot_size || 1;
    const exit = c.exit_price ?? c.average_price;
    const at = c.closed_at || d?.checked_at || new Date().toISOString();
    return { closed_id: `${c.symbol}-${i}`, position_id: `${c.symbol}|${c.opened_at ?? ""}`, symbol: c.symbol, lane: "upstox_live", side: "LONG",
      quantity: qty, lots: Math.max(1, Math.round(qty / lot)), lot_size: lot, entry_time: c.opened_at || at, entry_price: c.average_price,
      exit_time: at, exit_price: exit, gross_pnl: c.realized ?? 0, fees: c.charges?.total ?? 0,
      realized_pnl: c.net_realized ?? (c.realized ?? 0) - (c.charges?.total ?? 0),
      return_pct: c.average_price ? (exit / c.average_price - 1) * 100 : 0, max_price: null, min_price: null, max_return_pct: null, min_return_pct: null,
      exit_reason: "MACD lane exit", partial: false, remaining_quantity: 0, exit_trade_id: "", visible_until: visibleUntil(at, holidays) };
  }), [d, holidays]);
  const closedTrades = useMemo<ClosedTrade[]>(() => (d?.closed ?? []).filter((c) => c.closed_at)
    .map((c) => ({ ...c, closed_at: c.closed_at as string, exit_price: c.exit_price ?? c.average_price })), [d]);
  const unrealized = positions.reduce((sum, p) => sum + p.unrealized_pnl, 0);
  const marketValue = positions.reduce((sum, p) => sum + p.last_price * p.quantity, 0);
  // After Upstox's actual charges (owner, 19 Nov 2026); the lane reports gross and net.
  const realized = d?.realized_net ?? d?.realized ?? 0;
  const charges = d?.charges_total ?? 0;
  const openCharges = d?.open_charges ?? 0;    // entry charges already paid on what is held
  const equity = capital + realized + unrealized - openCharges;
  const portfolio = useMemo<Portfolio>(() => ({ equity, cash: d?.free ?? 0, market_value: marketValue,
    realized_pnl: realized, unrealized_pnl: unrealized, positions, closed_positions: closedRows, lane: "upstox_live" }),
  [equity, capital, realized, d, marketValue, unrealized, positions, closedRows]);

  const today = istDay(new Date().toISOString());
  // The ledger tabs show the lane day only (owner, 18 Nov 2026); Performance and the Back office keep the history.
  const dayStart = laneDayStart(new Date(), holidays);
  const todayOrders = useMemo(() => orders.filter((o) => inLaneDay(o.created_at, dayStart)), [orders, dayStart]);
  const todayTrades = useMemo(() => trades.filter((t) => inLaneDay(t.timestamp, dayStart)), [trades, dayStart]);
  const decisions = useMemo(() => d?.decisions ?? [], [d]);
  const todayDecisions = decisions.filter((r) => istDay(r.at) === today);
  const signalsSeen = todayDecisions.filter((r) => ENTRY_KINDS.includes(r.kind) && r.side !== "SELL").length;
  const kinds = useMemo(() => [...new Set(decisions.map((r) => r.kind))].sort(), [decisions]);
  const journal = kind ? decisions.filter((r) => r.kind === kind) : decisions;
  const counts: Partial<Record<Tab, number>> = { PORTFOLIO: positions.length, ORDERS: orders.filter((o) => istDay(o.created_at) === today).length,
    TRADES: (d?.trade_rows ?? []).filter((t) => inLaneDay(t.at, dayStart)).length, JOURNAL: todayDecisions.length };

  const watch = useMemo(() => {
    const held = [...positions].sort((a, b) => (b.return_pct ?? 0) - (a.return_pct ?? 0));
    const seen = new Set(held.map((p) => p.symbol));
    const recent = decisions.filter((r) => r.symbol && !seen.has(r.symbol) && (seen.add(r.symbol), true)).slice(0, 40);
    return { held, recent };
  }, [positions, decisions]);
  const chartSymbol = selected || watch.held[0]?.symbol || watch.recent[0]?.symbol || d?.macd_open[0] || "";
  const chartPosition = positions.find((p) => p.symbol === chartSymbol);
  const chartMarkers = useMemo(() => fillMarkers(trades, chartSymbol), [trades, chartSymbol]);

  const focusFor = useCallback((row: FocusRow): LaneChartFocus => {
    const open = positions.find((p) => p.symbol === row.symbol);
    const done = closedRows.find((c) => c.symbol === row.symbol && Math.abs(Date.parse(c.entry_time) - Date.parse(row.entryTime)) < 10_000);
    return { symbol: row.symbol, title: short(row.symbol),
      subtitle: `Upstox fill ₹${row.entryPrice.toFixed(2)} · ${when(row.entryTime)}${done && !open ? ` · exit ${done.exit_price.toFixed(2)} ${percent(done.return_pct, 2)}` : ""}`,
      markers: fillMarkers(trades, row.symbol, row.entryTime, open ? null : done?.exit_time), levels: positionLevels(open) };
  }, [positions, closedRows, trades]);
  const openChart = useCallback((row: FocusRow, list: FocusRow[]) => {
    const rows = list.length ? list : [row];
    setFocus({ list: rows.map(focusFor), index: Math.max(0, rows.findIndex((r) => r.symbol === row.symbol && r.entryTime === row.entryTime)) });
  }, [focusFor]);

  const orderApi = d?.order_api;
  const orderDetail = readableUpstreamError(orderApi?.detail);
  const available = d?.upstox_funds?.available_margin;
  const problems = [error, d?.read_error && `Upstox read failed: ${d.read_error}`, d?.engine_error && `MACD engine read failed: ${d.engine_error}`,
    orderApi && !orderApi.ok && `Upstox order API: ${orderDetail}`].filter(Boolean) as string[];

  return <section className="ledger-page blast-page spot-page upstox-page">
    <div className="page-heading blast-heading">
      <div><h1>Upstox live lane</h1>
        <p>MACD lane signals traded with real Upstox orders sized from the account's available funds{d?.cap ? ` (capped at ₹${rupeeText(d.cap)})` : ""} · {d?.settings?.sides === "CE" ? "calls only" : d?.settings?.sides === "PE" ? "puts only" : "calls and puts"}, weekly-index ({(d?.weekly_index_roots ?? ["NIFTY", "SENSEX"]).join(", ")}) signals first · every new option BUY signal whose one lot fits is bought (LIMIT, product D) · sold when the MACD lane closes the contract · armed once, until Kill</p></div>
      <div className="blast-badges">
        <span className={d?.armed ? "mode alert" : "feed-badge"} title={d?.armed ? `Armed${d.armed_since ? ` since ${when(d.armed_since)}` : ""}: real orders until Kill (Settings → Upstox live)` : "Not armed: no orders are sent"}>
          {d ? (d.armed ? "ARMED · real money" : "Not armed · no orders") : "…"}</span>
        <span className={orderApi && !orderApi.ok ? "mode alert" : "feed-badge"} title={orderApi ? `${orderDetail} · checked ${when(orderApi.checked_at)}` : "Not checked yet"}>
          Order API {orderApi ? (orderApi.ok ? "ok" : "blocked") : "…"}</span>
        <span className={d?.read_error ? "mode alert" : "feed-badge"} title={d?.read_error || `Account read ${when(d?.read_at)}`}>Account read {d ? (d.read_error ? "failing" : "ok") : "…"}</span>
      </div>
    </div>

    <section className="blast-metrics" aria-label="Upstox lane book">
      <Metric label="Equity" value={`₹${rupeeText(equity)}`} />
      <Metric label={`Return on ₹${rupeeText(capital)} start`} value={percent((equity / capital - 1) * 100, 2)} tone={equity - capital} />
      <Metric label={`Realized after ₹${rupeeText(Math.round(charges))} charges`} value={rupees(realized)} tone={realized} />
      <Metric label="Open P&L · current marks" value={rupees(unrealized)} tone={unrealized} />
      <Metric label="Open positions" value={`${positions.length} · ₹${rupeeText(Math.round(marketValue))}`} />
      <Metric label="Free for next signal / Upstox margin" value={`₹${rupeeText(Math.round(d?.free ?? 0))} / ${available == null ? "—" : `₹${rupeeText(Math.round(available))}`}`} />
      <Metric label="Today · signals / orders / fills" value={`${signalsSeen} / ${counts.ORDERS ?? 0} / ${counts.TRADES ?? 0}`} />
    </section>

    <div className="watch-tabs blast-tabs" role="tablist" aria-label="Upstox lane sections">
      {TABS.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => pickTab(key)}>
        {label}{counts[key] ? <span className="mp-count">{counts[key]}</span> : null}
      </button>)}
    </div>
    {problems.map((text) => <div key={text} className="settings-error" role="alert">{text}</div>)}
    {!d && !error && <div className="agent-empty state-loading" role="status">Loading the Upstox lane…</div>}

    {d && <div className="blast-body" role="tabpanel">
      {tab === "TERMINAL" && <div className="workspace terminal-workspace spot-terminal">
        <aside className="watchlist panel">
          <div className="panel-title">Held at Upstox <span>{watch.held.length}</span></div>
          {watch.held.length === 0 && <p className="watch-empty">Nothing held.</p>}
          {watch.held.map((p) => <button key={p.symbol} type="button" className={`spot-watch-row${chartSymbol === p.symbol ? " active" : ""}`} onClick={() => setSelected(p.symbol)}>
            <b>{short(p.symbol)}</b><span>{p.last_price.toFixed(2)}</span><span className={tone(p.return_pct ?? 0)}>{percent(p.return_pct, 2)}</span>
            <small>{p.quantity} qty @ {p.average_price.toFixed(2)} · MACD lane {d.macd_open.includes(p.symbol) ? "holding" : "closed → exiting"}</small>
          </button>)}
          <div className="panel-title">Recent signals <span>{watch.recent.length}</span></div>
          {watch.recent.map((r, i) => <button key={`${r.at}-${i}`} type="button" className={`spot-watch-row${chartSymbol === r.symbol ? " active" : ""}`} onClick={() => setSelected(r.symbol || "")}>
            <b>{short(r.symbol || "")}</b><span>{typeof r.price === "number" ? r.price.toFixed(2) : "—"}</span><span className={DECISION_TONE[r.kind] ?? ""}>{words(r.kind)}</span>
            <small>{when(r.at)}</small>
          </button>)}
          {!watch.held.length && !watch.recent.length && <p className="watch-empty">The lane acts on MACD signals published after it started ({when(d.started_at)}). The MACD lane holds {d.macd_open.length} contracts now.</p>}
        </aside>
        <section className="chart panel">
          <div className="panel-title"><span>{chartSymbol ? short(chartSymbol) : "No contract yet"}</span>
            {chartPosition && <span className={tone(chartPosition.return_pct ?? 0)}>{chartPosition.quantity} qty @ {chartPosition.average_price.toFixed(2)} · {percent(chartPosition.return_pct, 2)} · opened {when(chartPosition.opened_at)}</span>}
          </div>
          {chartSymbol ? <LaneChart symbol={chartSymbol} markers={chartMarkers} levels={positionLevels(chartPosition)} /> :
            <div className="agent-empty">The chart opens on the first held contract or signal.</div>}
        </section>
      </div>}

      {tab === "TRADES" && <UpstoxTrades rows={d.trade_rows ?? []} dayStart={dayStart} />}

      {(tab === "PORTFOLIO" || tab === "ORDERS") && <TradingLedger tab={tab} orders={todayOrders} trades={todayTrades}
        portfolio={portfolio} signals={[]} onOpenPosition={openChart} holidays={holidays} ledgerReady />}

      {tab === "STATUS" && <div className="spot-status">
        <section className="panel agent-panel">
          <div className="panel-title">Rules</div>
          <table className="live-table agent-table"><tbody>
            {Object.entries(d.rules).map(([k, v]) => <tr key={k}><th>{words(k)}</th><td>{v}</td></tr>)}
            <tr><th>sessions</th><td>Signals follow the MACD engine's calendar: regular sessions plus special sessions (Muhurat Sun 8 Nov 2026: F&amp;O 14:20–15:40 IST), which count as part of the Friday session</td></tr>
          </tbody></table>
        </section>
        <section className="panel agent-panel">
          <div className="panel-title">Lane health</div>
          <table className="live-table agent-table"><tbody>
            <tr><th>Mode</th><td className={d.armed ? "negative" : ""}>{d.armed ? `ARMED${d.armed_since ? ` since ${when(d.armed_since)}` : ""}: real orders until Kill` : "Not armed: offered signals are journalled as skipped, no order is sent"}</td></tr>
            <tr><th>Order API</th><td className={orderApi && !orderApi.ok ? "negative" : ""}>{orderApi ? `${orderDetail} · ${when(orderApi.checked_at)}` : "not checked yet"}</td></tr>
            <tr><th>Account read</th><td className={d.read_error ? "negative" : ""}>{d.read_error || "ok"} · {when(d.read_at)}</td></tr>
            <tr><th>Upstox funds</th><td>{available == null ? "—" : `₹${rupeeText(available)} available`}{d.upstox_funds?.used_margin ? ` · ₹${rupeeText(d.upstox_funds.used_margin)} used` : ""}</td></tr>
            <tr><th>Free for next entry</th><td>₹{rupeeText(d.free)} · {d.fund_basis ?? "—"}{d.in_flight ? ` (less ₹${rupeeText(d.in_flight)} sent since the last read)` : ""}{d.cap ? ` · cap ₹${rupeeText(d.cap)}` : " · no lane cap"} · ₹{rupeeText(d.charges_allowance ?? 0)} kept back per order for charges</td></tr>
            <tr><th>MACD engine</th><td className={d.engine_error ? "negative" : ""}>{d.engine_error || `ok · MACD lane holds ${d.macd_open.length} contracts`} · {when(d.macd_checked_at)}</td></tr>
            <tr><th>Orders today</th><td>{d.orders_today?.day === today ? d.orders_today.count : 0} of {d.max_orders_per_day ?? "—"} sent</td></tr>
            <tr><th>Started</th><td>{when(d.started_at)}</td></tr>
          </tbody></table>
        </section>
        <section className="panel agent-panel">
          <div className="panel-title">Whole Upstox account today <small>all orders, not only this lane's</small></div>
          <p className="agent-sub">Positions {d.account.positions.length} · orders {d.account.orders.length} · trades {d.account.trades.length} · read {when(d.read_at)}</p>
          {d.account.positions.length > 0 && <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Instrument</th><th>Qty</th><th>Avg buy</th><th>LTP</th><th>P&amp;L</th><th>Product</th></tr></thead>
            <tbody>{d.account.positions.map((p, i) => <tr key={i}><td>{String(p.trading_symbol ?? p.tradingsymbol ?? p.instrument_token)}</td><td>{String(p.quantity ?? "")}</td>
              <td>{Number(p.buy_price ?? p.average_price).toFixed(2)}</td><td>{Number(p.last_price).toFixed(2)}</td><td className={tone(Number(p.pnl))}>{rupees(Number(p.pnl))}</td><td>{String(p.product ?? "")}</td></tr>)}</tbody></table></div>}
          {d.account.orders.length > 0 && <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Time</th><th>Instrument</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th><th>Tag</th></tr></thead>
            <tbody>{d.account.orders.map((o, i) => <tr key={i}><td>{String(o.order_timestamp ?? "")}</td><td>{String(o.trading_symbol ?? o.tradingsymbol ?? o.instrument_token)}</td>
              <td>{String(o.transaction_type ?? "")}</td><td>{String(o.quantity ?? "")}</td><td>{Number(o.average_price || o.price).toFixed(2)}</td><td>{String(o.status ?? "")}</td><td>{String(o.tag ?? "")}</td></tr>)}</tbody></table></div>}
        </section>
      </div>}

      {tab === "PERFORMANCE" && <UpstoxPerformance closed={closedTrades} capital={capital}
        startedAt={d.started_at} chargeDays={d.charge_days} chargesError={d.charges_error} />}

      {tab === "JOURNAL" && <section className="panel agent-panel">
        <div className="panel-title">{journalView === "CLOSED" ? "Journal — closed trades, after charges" : "Journal — every MACD signal and what the lane did"}
          {journalView === "DECISIONS" && <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="">all decisions</option>{kinds.map((k) => <option key={k} value={k}>{words(k)}</option>)}</select>}
        </div>
        <div className="risk-tabs upstox-filter upstox-journal-view" role="group" aria-label="Journal view">
          <button type="button" aria-pressed={journalView === "DECISIONS"} onClick={() => pickJournal("DECISIONS")}>Decisions {decisions.length}</button>
          <button type="button" aria-pressed={journalView === "CLOSED"} onClick={() => pickJournal("CLOSED")}>Closed trades {closedTrades.length}</button>
        </div>
        {journalView === "CLOSED" ? <UpstoxClosedTrades closed={closedTrades} /> : <>
        {journal.length === 0 ? <div className="agent-empty">No new MACD signals since the lane started ({when(d.started_at)}).</div> :
          <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr><th>Time</th><th>Decision</th><th>Contract</th><th>Detail</th></tr></thead>
            <tbody>{journal.map((r, i) => <tr key={`${r.at}-${i}`} className={r.symbol ? "openable-row" : ""} onClick={() => { if (r.symbol) { setSelected(r.symbol); pickTab("TERMINAL"); } }}>
              <td>{when(r.at)}</td><td className={DECISION_TONE[r.kind] ?? ""}>{words(r.kind)}</td><td>{r.symbol ? short(r.symbol) : "—"}</td><td>{detail(r)}</td></tr>)}</tbody></table></div>}
        </>}
      </section>}
    </div>}
    {focus && <LaneChartModal list={focus.list} index={focus.index} onIndex={(index) => setFocus((old) => old && { ...old, index })} onClose={() => setFocus(undefined)} />}
  </section>;
}

function Metric({ label, value, tone: sign }: { label: string; value: string; tone?: number }) {
  return <div><span>{label}</span><b className={sign === undefined ? "" : sign >= 0 ? "positive" : "negative"}>{value}</b></div>;
}
