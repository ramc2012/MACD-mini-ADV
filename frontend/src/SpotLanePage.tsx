import { useCallback, useEffect, useMemo, useState } from "react";
import { rupees, tone } from "./agentMath";
import { TradingLedger, type FocusRow } from "./Ledger";
import { LaneChart, LaneChartModal, type LaneChartFocus } from "./LaneChart";
import { EquityChart } from "./LaneEquityChart";
import { fillMarkers, positionLevels } from "./laneChartMarks";
import type { ClosedPosition, Order, Portfolio, Tick, Trade } from "./types";
import { currentSmartBook } from "./laneMarks";
import { API_TOKEN, API_URL } from "./runtime";

// The spot MACD lane (engine spot_lane.py): cash equities on the MACD lane's own stock signals, Rs 1 L tickets,
// held 3-5 sessions. Laid out like the MACD and Blast lanes: a terminal with the chart, then the book's ledger.
const LANE = "spot_macd";

type Stats = { trades: number; wins: number; win_rate: number | null; net: number; profit_factor: number | null; avg_win: number | null; avg_loss: number | null };
type Summary = { lane: string; capital: number; equity: number; cash: number; return_pct: number; realized: number; unrealized: number;
  open_positions: number; max_drawdown: number; all: Stats; by_exit_reason: Record<string, Stats>; decisions: Record<string, number> };
type Position = { symbol: string; position_id?: string; quantity: number; lots: number; lot_size: number; average_price: number; last_price: number;
  unrealized_pnl: number; opened_at: string; hard_stop: number; trailing_stop: number | null; exit_stage?: number; entry_stage?: number;
  hold_until: string | null; max_return_pct?: number; peak_price?: number };
type Rules = { entry: string; ticket: number; sizing: string; hard_stop_pct: number; trail_activation_pct: number; trail_pct: number;
  staged_exits: number[]; invalidate_below_mfe_pct: number; min_hold_sessions?: number; max_hold_sessions: number };
type Health = { pending: number; error: string | null; last_batch_at: string | null; deployed: number; cash: number; book_full: boolean;
  rules: Rules; validation: string; counts: Record<string, number> };
type Snapshot = { requestedAt?: number; lane: string; capital: number; portfolio: { cash: number; equity: number; positions: Position[] }; health: Health };
type Book = { orders: Order[]; trades: Trade[]; portfolio: Portfolio; closed_positions: ClosedPosition[] };
type ClosedTrade = { position_id: string; symbol: string; entry_time: string; exit_time: string; entry_price: number; lots: number; pnl: number;
  return_pct: number; exit_reason: string | null; max_return_pct: number | null; closed: boolean };
type JournalRow = { id: string; at: string; symbol: string; decision: string; ticket: number | null; lots: number | null;
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
const percent = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const pct = (fraction: number) => `${+(fraction * 100).toFixed(2)}%`;
const money = (value: number) => value >= 10_000_000 ? `₹${(value / 10_000_000).toFixed(2)} Cr` : `₹${(value / 100_000).toFixed(2)} L`;
const words = (text: string | null | undefined) => (text || "—").replace(/_/g, " ").toLowerCase();
const DECISIONS = ["", "TAKEN", "ALREADY_HELD", "WAITING_PRICE", "NO_PRICE", "SESSION_CLOSED", "LOT_ABOVE_TICKET", "NO_CASH", "NOT_TRADABLE", "STALE", "ENTRY_REJECTED"];

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
  const [selected, setSelected] = useState("");
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
  const ledgerPortfolio = useMemo<Portfolio>(() => ({
    equity, cash, market_value: positions.reduce((sum, p) => sum + p.last_price * p.quantity, 0),
    realized_pnl: summary?.realized ?? book?.portfolio.realized_pnl ?? 0,
    unrealized_pnl: positions.reduce((sum, p) => sum + (p.last_price - p.average_price) * p.quantity, 0),
    positions: positions.map((p) => ({ ...p, lots: p.quantity, lot_size: 1, unrealized_pnl: (p.last_price - p.average_price) * p.quantity })),
    closed_positions: book?.closed_positions ?? [], lane: LANE,
  }), [equity, cash, positions, summary, book]);

  // The terminal's list: what is held first, then stocks the lane was offered recently.
  const watch = useMemo(() => {
    const held = [...positions].sort((a, b) => b.last_price / b.average_price - a.last_price / a.average_price);
    const seen = new Set(held.map((p) => p.symbol));
    const recent = journal.filter((row) => !seen.has(row.symbol) && (seen.add(row.symbol), true)).slice(0, 40);
    return { held, recent };
  }, [positions, journal]);
  const chartSymbol = selected || watch.held[0]?.symbol || watch.recent[0]?.symbol || "";
  const chartPosition = positions.find((p) => p.symbol === chartSymbol);
  const chartMarkers = useMemo(() => fillMarkers(trades, chartSymbol), [trades, chartSymbol]);

  const focusFor = useCallback((row: FocusRow): LaneChartFocus => {
    const open = positions.find((p) => p.symbol === row.symbol && Math.abs(Date.parse(p.opened_at) - Date.parse(row.entryTime)) < 10_000);
    const done = closed.find((t) => t.symbol === row.symbol && Math.abs(Date.parse(t.entry_time) - Date.parse(row.entryTime)) < 10_000);
    const until = open ? null : done?.exit_time;
    return { symbol: row.symbol, title: short(row.symbol),
      subtitle: `Entry ₹${row.entryPrice.toFixed(2)} · ${when(row.entryTime)}${done && !open ? ` · ${words(done.exit_reason)} ${percent(done.return_pct, 2)}` : ""}`,
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

  return <section className="ledger-page blast-page spot-page">
    <div className="page-heading blast-heading">
      <div><h1>Spot MACD lane</h1>
        <p>MACD lane stock signals bought as cash equities · {rules ? money(rules.ticket) : "₹1 L"} tickets · long only, carried overnight · {hold} · its own paper book</p></div>
      <div className="blast-badges">
        <span className="feed-badge" title="Every MACD-lane stock buy is bought in this lane's paper book; no score cut-off.">Auto-trade · paper</span>
        {health?.book_full && <span className="mode alert" title="Cash is below one ticket: new signals are journalled as no cash">Book full</span>}
        {(error || health?.error) && <span className="mode alert" title={error || health?.error || ""}>Lane error</span>}
      </div>
    </div>

    <section className="blast-metrics" aria-label="Spot lane book">
      <Metric label="Equity" value={`₹${rupeeText(equity)}`} />
      <Metric label={`Return on ${capital ? money(capital) : "capital"}`} value={capital ? percent((equity / capital - 1) * 100, 2) : "—"} tone={equity - capital} />
      <Metric label="Realized · lifetime" value={rupees(summary?.realized ?? 0)} tone={summary?.realized ?? 0} />
      <Metric label="Open P&L · current marks" value={rupees(ledgerPortfolio.unrealized_pnl)} tone={ledgerPortfolio.unrealized_pnl} />
      <Metric label="Open positions" value={`${positions.length} · ${money(ledgerPortfolio.market_value)}`} />
      <Metric label="Cash" value={money(cash)} />
      <Metric label="Today · signals / bought" value={`${todayRows.length} / ${todayRows.filter((r) => r.decision === "TAKEN").length}`} />
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
            const ret = (p.last_price / p.average_price - 1) * 100;
            return <button key={p.symbol} type="button" className={`spot-watch-row${chartSymbol === p.symbol ? " active" : ""}`} onClick={() => setSelected(p.symbol)}>
              <b>{short(p.symbol)}</b><span>{p.last_price.toFixed(2)}</span><span className={tone(ret)}>{percent(ret, 2)}</span>
              <small>{p.quantity} sh · stop {p.hard_stop.toFixed(2)}{p.trailing_stop ? ` · trail ${p.trailing_stop.toFixed(2)}` : ""}</small>
            </button>;
          })}
          <div className="panel-title">Recent signals <span>{watch.recent.length}</span></div>
          {watch.recent.map((row) => <button key={row.id} type="button" className={`spot-watch-row${chartSymbol === row.symbol ? " active" : ""}`} onClick={() => setSelected(row.symbol)}>
            <b>{short(row.symbol)}</b><span>{row.signal_price?.toFixed(2) ?? "—"}</span><span className={row.decision === "TAKEN" ? "positive" : ""}>{words(row.decision)}</span>
            <small>{when(row.at)}</small>
          </button>)}
          {!watch.held.length && !watch.recent.length && <p className="watch-empty">The lane buys on the MACD lane's next stock signal (30-min MACD crossing above zero).</p>}
        </aside>
        <section className="chart panel">
          <div className="panel-title"><span>{chartSymbol ? short(chartSymbol) : "No stock yet"}</span>
            {chartPosition && <span className={tone(chartPosition.last_price - chartPosition.average_price)}>
              {chartPosition.quantity} sh @ {chartPosition.average_price.toFixed(2)} · {percent((chartPosition.last_price / chartPosition.average_price - 1) * 100, 2)} · hold until {when(chartPosition.hold_until)}</span>}
          </div>
          {chartSymbol ? <LaneChart symbol={chartSymbol} markers={chartMarkers} levels={positionLevels(chartPosition)} /> :
            <div className="agent-empty">The chart opens on the first held stock or signal.</div>}
        </section>
      </div>}

      {(tab === "PORTFOLIO" || tab === "ORDERS" || tab === "TRADES") && <TradingLedger tab={tab} orders={book?.orders ?? []} trades={trades}
        portfolio={ledgerPortfolio} signals={[]} onOpenPosition={openChart} holidays={holidays} ledgerReady={!!book} />}

      {tab === "STATUS" && <div className="spot-status">
        <section className="panel agent-panel">
          <div className="panel-title">Rules</div>
          {rules ? <table className="live-table agent-table"><tbody>
            <tr><th>Entry</th><td>{rules.entry}; bought at the next fresh quote, whole shares within {money(rules.ticket)} and cash; one position per stock; no score cut-off</td></tr>
            <tr><th>Hold</th><td>{rules.min_hold_sessions ? `Until 15:25 on the ${ordinal(rules.min_hold_sessions)} session after entry only the hard stop can close it; ` : ""}out at 15:25 on the {ordinal(rules.max_hold_sessions)} session</td></tr>
            <tr><th>Hard stop</th><td>{pct(rules.hard_stop_pct)} below the entry, always live</td></tr>
            <tr><th>Profit</th><td>A third out at +{rules.staged_exits.map(pct).join(", +")}; a {pct(rules.trail_pct)} trail once the best is +{pct(rules.trail_activation_pct)}, never below breakeven</td></tr>
            <tr><th>MACD exit</th><td>MACD closes a 30-minute bar below zero while the best was under +{pct(rules.invalidate_below_mfe_pct)}</td></tr>
            <tr><th>Evidence</th><td>{health?.validation}</td></tr>
          </tbody></table> : <div className="agent-empty">Waiting for the lane…</div>}
        </section>
        <section className="panel agent-panel">
          <div className="panel-title">Lane health</div>
          <table className="live-table agent-table"><tbody>
            <tr><th>Deployed</th><td>{health ? money(health.deployed) : "—"} of {money(capital)}</td></tr>
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
                <th>Stock</th><th>Entry</th><th>Exit</th><th>Shares</th><th>P&amp;L</th><th>Return</th><th>Best</th><th>Exit reason</th>
              </tr></thead><tbody>{closed.map((t) => <tr key={t.position_id} className="openable-row" onClick={() => openChart({ symbol: t.symbol, entryTime: t.entry_time, entryPrice: t.entry_price },
                closed.map((c) => ({ symbol: c.symbol, entryTime: c.entry_time, entryPrice: c.entry_price })))}>
                <td>{short(t.symbol)}</td><td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td><td>{t.lots}</td>
                <td className={tone(t.pnl)}>{rupees(t.pnl)}</td><td className={tone(t.return_pct)}>{percent(t.return_pct, 2)}</td>
                <td>{percent(t.max_return_pct, 2)}</td><td>{words(t.exit_reason)}{t.closed ? "" : " (part)"}</td>
              </tr>)}</tbody></table></div>}
          </section>
        </div>
        <TradingLedger tab="STATISTICS" orders={book?.orders ?? []} trades={trades} portfolio={ledgerPortfolio} signals={[]} holidays={holidays} ledgerReady={!!book} />
      </>}

      {tab === "JOURNAL" && <section className="panel agent-panel">
        <div className="panel-title">Journal — every signal and what was done
          <select value={day} onChange={(e) => setDay(e.target.value)}><option value="">all days</option>{days.map((d) => <option key={d} value={d}>{d}</option>)}</select>
          <select value={decision} onChange={(e) => setDecision(e.target.value)}>{DECISIONS.map((d) => <option key={d} value={d}>{d ? words(d) : "all decisions"}</option>)}</select>
        </div>
        {journal.length === 0 ? <div className="agent-empty">No decisions journalled{day ? ` on ${day}` : ""} yet.</div> :
          <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr>
            <th>Time</th><th>Stock</th><th>Decision</th><th>Signal</th><th>Shares</th><th>Signal → fill</th>
          </tr></thead><tbody>{journal.map((row) => <tr key={row.id} className="openable-row" onClick={() => { setSelected(row.symbol); pickTab("TERMINAL"); }} title="Open on the terminal chart">
            <td>{when(row.at)}</td><td>{short(row.symbol)}</td>
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
