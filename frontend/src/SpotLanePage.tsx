import { useCallback, useEffect, useMemo, useState } from "react";
import { rupees, tone } from "./agentMath";
import { TradingLedger, type FocusRow } from "./Ledger";
import { LaneChart, LaneChartModal, type LaneChartFocus } from "./LaneChart";
import { EquityChart } from "./LaneEquityChart";
import { fillMarkers, positionLevels } from "./laneChartMarks";
import type { ClosedPosition, Order, Portfolio, Tick, Trade } from "./types";
import { currentSmartBook } from "./laneMarks";
import { API_TOKEN, API_URL } from "./runtime";

// The spot MACD lane (engine spot_lane.py): stock futures, long on the stock's 30-min MACD crossing up through zero
// and short on it crossing down, one exchange lot, held 3-5 sessions. Laid out like the MACD and Blast lanes: a
// terminal with the chart, then the book's ledger (Long / Short books).
const LANE = "spot_macd";

type Stats = { trades: number; wins: number; win_rate: number | null; net: number; profit_factor: number | null; avg_win: number | null; avg_loss: number | null };
type Summary = { lane: string; capital: number; equity: number; cash: number; return_pct: number; realized: number; unrealized: number;
  open_positions: number; max_drawdown: number; all: Stats; by_exit_reason: Record<string, Stats>; decisions: Record<string, number> };
type Side = "LONG" | "SHORT";
type Position = { symbol: string; position_id?: string; quantity: number; lots: number; lot_size: number; average_price: number; last_price: number;
  unrealized_pnl: number; opened_at: string; hard_stop: number; trailing_stop: number | null; exit_stage?: number; entry_stage?: number;
  hold_until: string | null; max_return_pct?: number; peak_price?: number; side?: Side; underlying?: string | null; notional?: number; margin?: number; expiry?: string | null };
type Rules = { entry: string; instrument?: string; lots?: number; margin_pct?: number; sizing: string; hard_stop_pct: number; trail_activation_pct: number; trail_pct: number;
  staged_exits: number[]; invalidate_below_mfe_pct: number; min_hold_sessions?: number; max_hold_sessions: number; long_only?: boolean };
type Health = { pending: number; error: string | null; last_batch_at: string | null; deployed: number; cash: number; book_full: boolean;
  margin_used?: number; free_margin?: number; exposure?: number; long_positions?: number; short_positions?: number;
  futures?: { chosen: number; resolved_at: string | null; error: string | null; expiries: string[] };
  rules: Rules; validation: string; counts: Record<string, number> };
type Snapshot = { requestedAt?: number; lane: string; capital: number; portfolio: { cash: number; equity: number; positions: Position[] }; health: Health };
type Book = { orders: Order[]; trades: Trade[]; portfolio: Portfolio; closed_positions: ClosedPosition[] };
type ClosedTrade = { position_id: string; symbol: string; entry_time: string; exit_time: string; entry_price: number; lots: number; pnl: number;
  return_pct: number; exit_reason: string | null; max_return_pct: number | null; closed: boolean; side?: Side; underlying?: string | null };
type JournalRow = { id: string; at: string; symbol: string; option_type?: string; decision: string; ticket: number | null; lots: number | null;
  signal_price: number | null; check_price: number | null; detail: Record<string, unknown> };
type Tab = "TERMINAL" | "PORTFOLIO" | "STATUS" | "ORDERS" | "TRADES" | "PERFORMANCE" | "JOURNAL";
const TABS: [Tab, string][] = [["TERMINAL", "Terminal"], ["PORTFOLIO", "Portfolio"], ["STATUS", "Status"], ["ORDERS", "Orders"], ["TRADES", "Trades"],
  ["PERFORMANCE", "Performance"], ["JOURNAL", "Journal"]];
const TAB_STORE = "macd.spotTab";

const headers = (): HeadersInit => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});
async function call<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { headers: headers() });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${path} failed (HTTP ${response.status})`);
  }
  return response.json() as Promise<T>;
}
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { /* not kept */ } };

const when = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} IST`;
};
const istDay = (iso: string) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(iso));
const short = (symbol: string) => symbol.replace(/^(NSE|BSE):/, "").replace(/-EQ$/, "");
const sideOf = (p: { quantity: number; side?: Side }): Side => p.side ?? (p.quantity < 0 ? "SHORT" : "LONG");
const sideLabel = (side?: string | null) => side === "SHORT" ? "Short" : side === "LONG" ? "Long" : "—";
const sideTone = (side?: string | null) => side === "SHORT" ? "negative" : side === "LONG" ? "positive" : "";
/** Return in the position's favour: up for a long, down for a short. */
const favour = (p: { quantity: number; side?: Side; last_price: number; average_price: number }) =>
  (sideOf(p) === "SHORT" ? -1 : 1) * (p.last_price / p.average_price - 1) * 100;
const percent = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const pct = (fraction: number) => `${+(fraction * 100).toFixed(2)}%`;
const money = (value: number) => value >= 10_000_000 ? `₹${(value / 10_000_000).toFixed(2)} Cr` : `₹${(value / 100_000).toFixed(2)} L`;
const words = (text: string | null | undefined) => (text || "—").replace(/_/g, " ").toLowerCase();
const DECISIONS = ["", "TAKEN", "ALREADY_HELD", "WAITING_PRICE", "NO_PRICE", "SESSION_CLOSED", "NO_CASH", "NOT_TRADABLE", "STALE", "ENTRY_REJECTED"];
type ChartView = "STOCK" | "FUTURE";

export function SpotLanePage({ ticks, portfolio, holidays }: { ticks: Record<string, Tick>; portfolio?: unknown; holidays?: string[] }) {
  const [tab, setTab] = useState<Tab>(() => (TABS.some(([key]) => key === recall(TAB_STORE)) ? recall(TAB_STORE) as Tab : "TERMINAL"));
  const [summary, setSummary] = useState<Summary | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [book, setBook] = useState<Book | null>(null);
  const [curve, setCurve] = useState<{ at: string; equity: number }[]>([]);
  const [closed, setClosed] = useState<ClosedTrade[]>([]);
  const [journal, setJournal] = useState<JournalRow[]>([]);
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState("");
  const [decision, setDecision] = useState("");
  const [selected, setSelected] = useState("");          // a stock (cash symbol): the signal is read on it
  const [view, setView] = useState<ChartView | "">("");
  const [focus, setFocus] = useState<{ list: LaneChartFocus[]; index: number }>();
  const [error, setError] = useState<string | null>(null);
  const pickTab = (next: Tab) => { setTab(next); remember(TAB_STORE, next); };

  const loadSlow = useCallback(async () => {
    try {
      const [sum, eq, done, ledger] = await Promise.all([call<Summary>(`/api/lanes/${LANE}/summary`),
        call<{ equity: { at: string; equity: number }[] }>(`/api/lanes/${LANE}/equity`), call<{ trades: ClosedTrade[] }>(`/api/lanes/${LANE}/trades?limit=500`),
        call<Book>(`/api/lanes/${LANE}/book?limit=2000`)]);
      setSummary(sum); setCurve(eq.equity); setClosed(done.trades); setBook(ledger); setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, []);
  const loadFast = useCallback(async () => {
    try {
      const query = new URLSearchParams({ limit: "400", ...(day ? { day } : {}), ...(decision ? { decision } : {}) });
      const requestedAt = Date.now();
      const [snap, rows] = await Promise.all([call<Snapshot>(`/api/lanes/${LANE}/snapshot`),
        call<{ rows: JournalRow[]; days: string[] }>(`/api/lanes/${LANE}/journal?${query}`)]);
      setSnapshot({ ...snap, requestedAt }); setJournal(rows.rows); setDays(rows.days); setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, [day, decision]);
  useEffect(() => { void loadSlow(); const id = window.setInterval(loadSlow, 15_000); return () => window.clearInterval(id); }, [loadSlow]);
  useEffect(() => { void loadFast(); const id = window.setInterval(loadFast, 5_000); return () => window.clearInterval(id); }, [loadFast]);

  const health = snapshot?.health;
  const rules = health?.rules;
  const live = currentSmartBook(snapshot, portfolio, ticks);
  const positions: Position[] = live?.positions || [];
  const capital = snapshot?.capital ?? summary?.capital ?? 0;
  const equity = live?.equity ?? summary?.equity ?? 0;
  const cash = live?.cash ?? summary?.cash ?? 0;
  const trades = book?.trades ?? [];
  const today = istDay(new Date().toISOString());
  const exposure = health?.exposure ?? positions.reduce((sum, p) => sum + Math.abs(p.quantity) * p.last_price, 0);
  const marginUsed = health?.margin_used ?? exposure * (rules?.margin_pct ?? 0.25);
  const freeMargin = equity - marginUsed;
  const ledgerPortfolio = useMemo<Portfolio>(() => ({
    equity, cash, market_value: positions.reduce((sum, p) => sum + p.last_price * p.quantity, 0),
    realized_pnl: summary?.realized ?? book?.portfolio.realized_pnl ?? 0,
    unrealized_pnl: positions.reduce((sum, p) => sum + (p.last_price - p.average_price) * p.quantity, 0),
    positions: positions.map((p) => ({ ...p, unrealized_pnl: (p.last_price - p.average_price) * p.quantity })),
    closed_positions: book?.closed_positions ?? [], lane: LANE,
  }), [equity, cash, positions, summary, book]);

  // The terminal's list: what is held first (by stock), then stocks the lane was offered recently.
  const watch = useMemo(() => {
    const held = [...positions].sort((a, b) => favour(b) - favour(a));
    const seen = new Set(held.map((p) => p.underlying || p.symbol));
    const recent = journal.filter((row) => !seen.has(row.symbol) && (seen.add(row.symbol), true)).slice(0, 40);
    return { held, recent };
  }, [positions, journal]);
  const stock = selected || watch.held[0]?.underlying || watch.recent[0]?.symbol || "";
  const chartPosition = positions.find((p) => p.underlying === stock);
  const future = chartPosition?.symbol || String(journal.find((row) => row.symbol === stock && row.detail.future)?.detail.future ?? "");
  const chartView: ChartView = view || (chartPosition ? "FUTURE" : "STOCK");
  const chartSymbol = chartView === "FUTURE" && future ? future : stock;
  const chartMarkers = useMemo(() => (chartSymbol === future ? fillMarkers(trades, future) : []), [trades, chartSymbol, future]);

  const focusFor = useCallback((row: FocusRow): LaneChartFocus => {
    const open = positions.find((p) => p.symbol === row.symbol && Math.abs(Date.parse(p.opened_at) - Date.parse(row.entryTime)) < 10_000);
    const done = closed.find((t) => t.symbol === row.symbol && Math.abs(Date.parse(t.entry_time) - Date.parse(row.entryTime)) < 10_000);
    const until = open ? null : done?.exit_time;
    const side = open ? sideOf(open) : done?.side;
    const stockName = open?.underlying || done?.underlying;
    return { symbol: row.symbol, title: `${short(row.symbol)}${side ? ` · ${sideLabel(side)}` : ""}`,
      subtitle: `${stockName ? `${short(stockName)} future · ` : ""}Entry ₹${row.entryPrice.toFixed(2)} · ${when(row.entryTime)}${done && !open ? ` · ${words(done.exit_reason)} ${percent(done.return_pct, 2)}` : ""}`,
      markers: fillMarkers(trades, row.symbol, row.entryTime, until), levels: positionLevels(open) };
  }, [positions, closed, trades]);
  const openChart = useCallback((row: FocusRow, list: FocusRow[]) => {
    const rows = list.length ? list : [row];
    setFocus({ list: rows.map(focusFor), index: Math.max(0, rows.findIndex((r) => r.symbol === row.symbol && r.entryTime === row.entryTime)) });
  }, [focusFor]);

  const all = summary?.all;
  const reasons = Object.entries(summary?.by_exit_reason || {}).sort((a, b) => b[1].trades - a[1].trades);
  const todayRows = journal.filter((row) => istDay(row.at) === today);
  const counts: Partial<Record<Tab, number>> = {
    PORTFOLIO: positions.length, ORDERS: (book?.orders ?? []).filter((o) => istDay(o.created_at) === today).length,
    TRADES: trades.filter((t) => istDay(t.timestamp) === today).length, JOURNAL: todayRows.length,
  };
  const hold = rules ? (rules.min_hold_sessions ? `held ${rules.min_hold_sessions}–${rules.max_hold_sessions} sessions` : `hold ${rules.max_hold_sessions} sessions`) : "";
  const lots = rules?.lots ?? 1;
  const longs = health?.long_positions ?? positions.filter((p) => p.quantity > 0).length;
  const shorts = health?.short_positions ?? positions.filter((p) => p.quantity < 0).length;

  return <section className="ledger-page blast-page spot-page">
    <div className="page-heading blast-heading">
      <div><h1>Spot MACD lane</h1>
        <p>Stock futures on each stock's 30-min MACD zero cross: long on a cross up, short on a cross down · {lots} exchange lot{lots === 1 ? "" : "s"} · {hold} · its own paper book</p></div>
      <div className="blast-badges">
        <span className="feed-badge" title="Every zero cross on a stock is traded in this lane's paper book; no score cut-off.">Auto-trade · paper</span>
        {rules?.long_only && <span className="mode" title="spot_short_enabled is off">Shorts off</span>}
        {health?.book_full && <span className="mode alert" title="No free margin: new signals are journalled as no cash">Margin full</span>}
        {health?.futures?.error && <span className="mode alert" title={health.futures.error}>Futures gaps</span>}
        {(error || health?.error) && <span className="mode alert" title={error || health?.error || ""}>Lane error</span>}
      </div>
    </div>

    <section className="blast-metrics" aria-label="Spot lane book">
      <Metric label="Equity" value={`₹${rupeeText(equity)}`} />
      <Metric label={`Return on ${capital ? money(capital) : "capital"}`} value={capital ? percent((equity / capital - 1) * 100, 2) : "—"} tone={equity - capital} />
      <Metric label="Realized · lifetime" value={rupees(summary?.realized ?? 0)} tone={summary?.realized ?? 0} />
      <Metric label="Open P&L · current marks" value={rupees(ledgerPortfolio.unrealized_pnl)} tone={ledgerPortfolio.unrealized_pnl} />
      <Metric label="Open · long / short" value={`${longs} / ${shorts} · ${money(exposure)}`} />
      <Metric label={`Margin used / free${rules?.margin_pct ? ` (≈${+(rules.margin_pct * 100).toFixed(1)}%)` : ""}`} value={`${money(marginUsed)} / ${money(freeMargin)}`} />
      <Metric label="Today · signals / taken" value={`${todayRows.length} / ${todayRows.filter((r) => r.decision === "TAKEN").length}`} />
    </section>

    <div className="watch-tabs blast-tabs" role="tablist" aria-label="Spot lane sections">
      {TABS.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => pickTab(key)}>
        {label}{counts[key] ? <span className="mp-count">{counts[key]}</span> : null}
      </button>)}
    </div>
    {(error || health?.error) && <div className="settings-error" role="alert">{error || health?.error}</div>}

    <div className="blast-body" role="tabpanel">
      {tab === "TERMINAL" && <div className="workspace terminal-workspace spot-terminal">
        <aside className="watchlist panel">
          <div className="panel-title">Held <span>{watch.held.length}</span></div>
          {watch.held.length === 0 && <p className="watch-empty">Nothing held.</p>}
          {watch.held.map((p) => {
            const ret = favour(p);
            const side = sideOf(p);
            return <button key={p.symbol} type="button" className={`spot-watch-row${stock === p.underlying ? " active" : ""}`} onClick={() => { setSelected(p.underlying || p.symbol); setView(""); }}>
              <b>{short(p.underlying || p.symbol)} <i className={sideTone(side)}>{sideLabel(side)}</i></b><span>{p.last_price.toFixed(2)}</span><span className={tone(ret)}>{percent(ret, 2)}</span>
              <small>{short(p.symbol)} · {p.lots} lot{p.lots === 1 ? "" : "s"} × {p.lot_size} · stop {p.hard_stop.toFixed(2)}{p.trailing_stop ? ` · trail ${p.trailing_stop.toFixed(2)}` : ""}</small>
            </button>;
          })}
          <div className="panel-title">Recent signals <span>{watch.recent.length}</span></div>
          {watch.recent.map((row) => <button key={row.id} type="button" className={`spot-watch-row${stock === row.symbol ? " active" : ""}`} onClick={() => { setSelected(row.symbol); setView(""); }}>
            <b>{short(row.symbol)} <i className={sideTone(row.option_type)}>{sideLabel(row.option_type)}</i></b><span>{row.signal_price?.toFixed(2) ?? "—"}</span><span className={row.decision === "TAKEN" ? "positive" : ""}>{words(row.decision)}</span>
            <small>{when(row.at)}</small>
          </button>)}
          {!watch.held.length && !watch.recent.length && <p className="watch-empty">The lane trades the next 30-min MACD zero cross on a stock: long on a cross up, short on a cross down, in the stock's future.</p>}
        </aside>
        <section className="chart panel">
          <div className="panel-title"><span>{chartSymbol ? short(chartSymbol) : "No stock yet"}</span>
            {stock && <span className="radar-filter" aria-label="Chart instrument">
              <button className={chartView === "STOCK" ? "active" : ""} aria-pressed={chartView === "STOCK"} title="The stock: the MACD the lane trades on" onClick={() => setView("STOCK")}>Stock · signal</button>
              <button className={chartView === "FUTURE" ? "active" : ""} aria-pressed={chartView === "FUTURE"} disabled={!future} title={future ? `${short(future)}: fills, stop and trail` : "No future chosen for this stock yet"} onClick={() => setView("FUTURE")}>Future · fills</button>
            </span>}
            {chartPosition && <span className={tone(favour(chartPosition))}>
              {sideLabel(sideOf(chartPosition))} {chartPosition.lots} lot{chartPosition.lots === 1 ? "" : "s"} @ {chartPosition.average_price.toFixed(2)} · {percent(favour(chartPosition), 2)} · hold until {when(chartPosition.hold_until)}</span>}
          </div>
          {chartSymbol ? <LaneChart symbol={chartSymbol} markers={chartMarkers} levels={chartSymbol === future ? positionLevels(chartPosition) : []} /> :
            <div className="agent-empty">The chart opens on the first held stock or signal.</div>}
        </section>
      </div>}

      {(tab === "PORTFOLIO" || tab === "ORDERS" || tab === "TRADES") && <TradingLedger tab={tab} orders={book?.orders ?? []} trades={trades}
        portfolio={ledgerPortfolio} signals={[]} onOpenPosition={openChart} holidays={holidays} ledgerReady={!!book} books="sides" />}

      {tab === "STATUS" && <div className="spot-status">
        <section className="panel agent-panel">
          <div className="panel-title">Rules</div>
          {rules ? <table className="live-table agent-table"><tbody>
            <tr><th>Entry</th><td>{rules.entry}; filled at the next fresh futures quote; one position per stock (an opposite cross while held does not reverse it); no score cut-off</td></tr>
            <tr><th>Instrument</th><td>{rules.instrument ?? "the stock's future"}; {rules.sizing}{health?.futures ? ` · ${health.futures.chosen} stocks mapped${health.futures.expiries.length ? ` (expiry ${health.futures.expiries.join(", ")})` : ""} at ${when(health.futures.resolved_at)}` : ""}</td></tr>
            <tr><th>Hold</th><td>{rules.min_hold_sessions ? `Until 15:25 on the ${ordinal(rules.min_hold_sessions)} session after entry only the hard stop can close it; ` : ""}out at 15:25 on the {ordinal(rules.max_hold_sessions)} session</td></tr>
            <tr><th>Hard stop</th><td>{pct(rules.hard_stop_pct)} against the entry (below a long, above a short), always live</td></tr>
            <tr><th>Profit</th><td>A third out at {rules.staged_exits.map(pct).join(", ")} in favour, in whole lots (a position of fewer lots than that has none); a {pct(rules.trail_pct)} trail once the best is {pct(rules.trail_activation_pct)} in favour, never worse than breakeven</td></tr>
            <tr><th>MACD exit</th><td>The stock's MACD closes a 30-minute bar back across zero (below for a long, above for a short) while the best was under {pct(rules.invalidate_below_mfe_pct)}</td></tr>
            <tr><th>Evidence</th><td>{health?.validation}</td></tr>
          </tbody></table> : <div className="agent-empty">Waiting for the lane…</div>}
        </section>
        <section className="panel agent-panel">
          <div className="panel-title">Lane health</div>
          <table className="live-table agent-table"><tbody>
            <tr><th>Margin</th><td>{money(marginUsed)} used · {money(freeMargin)} free of {money(equity)} equity · exposure {money(exposure)}</td></tr>
            <tr><th>Futures</th><td>{health?.futures ? `${health.futures.chosen} chosen at ${when(health.futures.resolved_at)}${health.futures.error ? ` · ${health.futures.error}` : ""}` : "—"}</td></tr>
            <tr><th>Queued signals</th><td>{health?.pending ?? 0}</td></tr>
            <tr><th>Last batch</th><td>{when(health?.last_batch_at)}</td></tr>
            <tr><th>Decisions (lifetime)</th><td>{Object.entries(summary?.decisions || {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${words(k)} ${n}`).join(" · ") || "—"}</td></tr>
          </tbody></table>
        </section>
      </div>}

      {tab === "PERFORMANCE" && <>
        <section className="panel agent-panel">
          <div className="panel-title">Equity and drawdown <span>{curve.length} points</span></div>
          <EquityChart points={curve} capital={capital} height={260} />
          <div className="agent-stats lane-stats">
            <div className="agent-stat"><span>Closed trades</span><b>{all?.trades ?? 0}</b><small>win {all?.win_rate == null ? "—" : `${Math.round(all.win_rate * 100)}%`} · PF {all?.profit_factor == null ? "—" : all.profit_factor.toFixed(2)}</small></div>
            <div className="agent-stat"><span>Avg win / loss</span><b className="positive">{all?.avg_win == null ? "—" : rupees(all.avg_win)}</b><small className="negative">{all?.avg_loss == null ? "—" : rupees(all.avg_loss)}</small></div>
            <div className="agent-stat"><span>Max drawdown</span><b className={(summary?.max_drawdown ?? 0) > 0 ? "negative" : ""}>{rupees(-(summary?.max_drawdown ?? 0))}</b><small>{capital ? percent(-(summary?.max_drawdown ?? 0) / capital * 100) : "—"} of capital</small></div>
          </div>
        </section>
        <div className="agent-bottom">
          <section className="panel agent-panel">
            <div className="panel-title">Exits by reason</div>
            {reasons.length === 0 ? <div className="agent-empty">No closed trades yet.</div> :
              <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Reason</th><th>Trades</th><th>Win</th><th>Net</th><th>PF</th></tr></thead>
                <tbody>{reasons.map(([reason, s]) => <tr key={reason}><td>{words(reason)}</td><td>{s.trades}</td><td>{s.win_rate == null ? "—" : `${Math.round(s.win_rate * 100)}%`}</td>
                  <td className={tone(s.net)}>{rupees(s.net)}</td><td>{s.profit_factor == null ? "—" : s.profit_factor.toFixed(2)}</td></tr>)}</tbody></table></div>}
          </section>
          <section className="panel agent-panel">
            <div className="panel-title">Closed trades ({closed.length}) <small>click a row for its chart</small></div>
            {closed.length === 0 ? <div className="agent-empty">No closed trades yet.</div> :
              <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr>
                <th>Stock</th><th>Side</th><th>Future</th><th>Entry</th><th>Exit</th><th>Lots</th><th>P&amp;L</th><th>Return</th><th>Best</th><th>Exit reason</th>
              </tr></thead><tbody>{closed.map((t) => <tr key={t.position_id} className="openable-row" onClick={() => openChart({ symbol: t.symbol, entryTime: t.entry_time, entryPrice: t.entry_price },
                closed.map((c) => ({ symbol: c.symbol, entryTime: c.entry_time, entryPrice: c.entry_price })))}>
                <td>{short(t.underlying || t.symbol)}</td><td className={sideTone(t.side)}>{sideLabel(t.side)}</td><td>{short(t.symbol)}</td><td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td><td>{t.lots}</td>
                <td className={tone(t.pnl)}>{rupees(t.pnl)}</td><td className={tone(t.return_pct)}>{percent(t.return_pct, 2)}</td>
                <td>{percent(t.max_return_pct, 2)}</td><td>{words(t.exit_reason)}{t.closed ? "" : " (part)"}</td>
              </tr>)}</tbody></table></div>}
          </section>
        </div>
        <TradingLedger tab="STATISTICS" orders={book?.orders ?? []} trades={trades} portfolio={ledgerPortfolio} signals={[]} holidays={holidays} ledgerReady={!!book} books="sides" />
      </>}

      {tab === "JOURNAL" && <section className="panel agent-panel">
        <div className="panel-title">Journal — every signal and what was done
          <select value={day} onChange={(e) => setDay(e.target.value)}><option value="">all days</option>{days.map((d) => <option key={d} value={d}>{d}</option>)}</select>
          <select value={decision} onChange={(e) => setDecision(e.target.value)}>{DECISIONS.map((d) => <option key={d} value={d}>{d ? words(d) : "all decisions"}</option>)}</select>
        </div>
        {journal.length === 0 ? <div className="agent-empty">No decisions journalled{day ? ` on ${day}` : ""} yet.</div> :
          <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr>
            <th>Time</th><th>Stock</th><th>Side</th><th>Decision</th><th>Signal</th><th>Lots</th><th>Signal → fill</th>
          </tr></thead><tbody>{journal.map((row) => <tr key={row.id} className="openable-row" onClick={() => { setSelected(row.symbol); pickTab("TERMINAL"); }} title="Open on the terminal chart">
            <td>{when(row.at)}</td><td>{short(row.symbol)}</td><td className={sideTone(row.option_type)}>{sideLabel(row.option_type)}</td>
            <td className={row.decision === "TAKEN" ? "positive" : ""}>{words(row.decision)}<details onClick={(e) => e.stopPropagation()}><summary>ⓘ</summary><pre>{JSON.stringify(row.detail, null, 2)}</pre></details></td>
            <td>{String(row.detail.kind ?? "").includes("INTRABAR") ? "intrabar" : row.detail.kind ? "bar close" : "—"}</td><td>{row.lots ?? "—"}</td>
            <td>{row.signal_price?.toFixed(2) ?? "—"}{row.check_price != null && <small> → {row.check_price.toFixed(2)}</small>}</td>
          </tr>)}</tbody></table></div>}
      </section>}
    </div>
    {focus && <LaneChartModal list={focus.list} index={focus.index} onIndex={(index) => setFocus((old) => old && { ...old, index })} onClose={() => setFocus(undefined)} />}
  </section>;
}

const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
const rupeeText = (value: number) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);

function Metric({ label, value, tone: sign }: { label: string; value: string; tone?: number }) {
  return <div><span>{label}</span><b className={sign === undefined ? "" : sign >= 0 ? "positive" : "negative"}>{value}</b></div>;
}
