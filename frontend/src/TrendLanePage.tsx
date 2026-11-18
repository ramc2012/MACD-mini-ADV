import { useCallback, useEffect, useMemo, useState } from "react";
import { rupees, tone } from "./agentMath";
import { TradingLedger, type FocusRow } from "./Ledger";
import { LaneChart, LaneChartModal, type LaneChartFocus } from "./LaneChart";
import { EquityChart } from "./LaneEquityChart";
import { fillMarkers } from "./laneChartMarks";
import type { ClosedPosition, Order, Portfolio, Tick, Trade } from "./types";
import { currentSmartBook } from "./laneMarks";
import { API_TOKEN, API_URL } from "./runtime";

// The trend-state index lane (engine trend_lane.py; owner, 18 Nov 2026): each index's 30-minute trend state -- flat,
// initiate, add, reduce, flip -- traded as about-2%-ITM monthly options in its own paper book.
const LANE = "trend_index";
const INDEX_SPOT: Record<string, string> = { NIFTY: "NSE:NIFTY50-INDEX", BANKNIFTY: "NSE:NIFTYBANK-INDEX", FINNIFTY: "NSE:FINNIFTY-INDEX",
  MIDCPNIFTY: "NSE:MIDCPNIFTY-INDEX", SENSEX: "BSE:SENSEX-INDEX", NIFTYNXT50: "NSE:NIFTYNXT50-INDEX" };

type Stats = { trades: number; win_rate: number | null; net: number; profit_factor: number | null; avg_win: number | null; avg_loss: number | null };
type Summary = { capital: number; equity: number; cash: number; realized: number; max_drawdown: number; all: Stats; by_exit_reason: Record<string, Stats> };
type IndexState = { direction?: "UP" | "DOWN" | "NONE"; units?: number; held_units: number; confirmed_at?: string | null; age_bars?: number; fade?: number;
  pullback_atr?: number; last_action?: string | null; last_bar?: string | null; recent?: [string, string, number][]; pick_error?: string | null };
type Pick = { index: string; option_type: string; symbol: string; strike: number; expiry: string; lot_size: number; spot: number; at: string };
type Health = { error: string | null; last_evaluated_at: string | null; activated_at: string; indices: Record<string, IndexState>; picks: Pick[];
  rules: Record<string, string | number>; validation: string; counts: Record<string, number> };
type Position = { symbol: string; quantity: number; lots: number; lot_size: number; average_price: number; last_price: number; opened_at: string;
  index?: string | null; option_type?: string | null; strike?: number | null; expiry?: string | null };
type Snapshot = { capital: number; portfolio: { cash: number; equity: number; positions: Position[] }; health: Health };
type Book = { orders: Order[]; trades: Trade[]; portfolio: Portfolio; closed_positions: ClosedPosition[] };
type ClosedTrade = { position_id: string; symbol: string; entry_time: string; exit_time: string; entry_price: number; lots: number; pnl: number;
  return_pct: number; exit_reason: string | null; closed: boolean };
type JournalRow = { id: string; at: string; symbol: string; option_type?: string; decision: string; lots: number | null; detail: Record<string, unknown> };
type Tab = "STATES" | "PORTFOLIO" | "ORDERS" | "TRADES" | "PERFORMANCE" | "JOURNAL";
const TABS: [Tab, string][] = [["STATES", "Trend states"], ["PORTFOLIO", "Portfolio"], ["ORDERS", "Orders"], ["TRADES", "Trades"], ["PERFORMANCE", "Performance"], ["JOURNAL", "Journal"]];
const TAB_STORE = "macd.trendTab";

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
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} IST`;
};
const istDay = (iso: string) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(iso));
const short = (symbol: string) => symbol.replace(/^(NSE|BSE):/, "").replace(/-INDEX$/, "");
const percent = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const money = (value: number) => value >= 10_000_000 ? `₹${(value / 10_000_000).toFixed(2)} Cr` : `₹${(value / 100_000).toFixed(2)} L`;
const words = (text: string | null | undefined) => (text || "—").replace(/_/g, " ").toLowerCase();
const dirTone = (d?: string) => d === "UP" ? "positive" : d === "DOWN" ? "negative" : "";

export function TrendLanePage({ ticks, portfolio, holidays }: { ticks: Record<string, Tick>; portfolio?: unknown; holidays?: string[] }) {
  const [tab, setTab] = useState<Tab>(() => (TABS.some(([key]) => key === recall(TAB_STORE)) ? recall(TAB_STORE) as Tab : "STATES"));
  const [summary, setSummary] = useState<Summary | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [book, setBook] = useState<Book | null>(null);
  const [curve, setCurve] = useState<{ at: string; equity: number }[]>([]);
  const [closed, setClosed] = useState<ClosedTrade[]>([]);
  const [journal, setJournal] = useState<JournalRow[]>([]);
  const [selected, setSelected] = useState("NIFTY");
  const [focus, setFocus] = useState<{ list: LaneChartFocus[]; index: number }>();
  const [error, setError] = useState<string | null>(null);
  const pickTab = (next: Tab) => { setTab(next); remember(TAB_STORE, next); };

  const loadSlow = useCallback(async () => {
    try {
      const [sum, eq, done, ledger] = await Promise.all([call<Summary>(`/api/lanes/${LANE}/summary`),
        call<{ equity: { at: string; equity: number }[] }>(`/api/lanes/${LANE}/equity?today=true`), call<{ trades: ClosedTrade[] }>(`/api/lanes/${LANE}/trades?limit=500&today=true`),
        call<Book>(`/api/lanes/${LANE}/book?limit=2000&today=true`)]);
      setSummary(sum); setCurve(eq.equity); setClosed(done.trades); setBook(ledger); setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, []);
  const loadFast = useCallback(async () => {
    try {
      const [snap, rows] = await Promise.all([call<Snapshot>(`/api/lanes/${LANE}/snapshot`), call<{ rows: JournalRow[] }>(`/api/lanes/${LANE}/journal?limit=300`)]);
      setSnapshot(snap); setJournal(rows.rows); setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, []);
  useEffect(() => { void loadSlow(); const id = window.setInterval(loadSlow, 15_000); return () => window.clearInterval(id); }, [loadSlow]);
  useEffect(() => { void loadFast(); const id = window.setInterval(loadFast, 5_000); return () => window.clearInterval(id); }, [loadFast]);

  const health = snapshot?.health;
  const live = currentSmartBook(snapshot, portfolio, ticks);
  const positions: Position[] = (live?.positions as Position[] | undefined) || [];
  const capital = snapshot?.capital ?? summary?.capital ?? 0;
  const equity = live?.equity ?? summary?.equity ?? 0;
  const cash = live?.cash ?? summary?.cash ?? 0;
  const trades = book?.trades ?? [];
  const today = istDay(new Date().toISOString());
  const unrealized = positions.reduce((sum, p) => sum + (p.last_price - p.average_price) * p.quantity, 0);
  const ledgerPortfolio = useMemo<Portfolio>(() => ({
    equity, cash, market_value: positions.reduce((sum, p) => sum + p.last_price * p.quantity, 0),
    realized_pnl: summary?.realized ?? book?.portfolio.realized_pnl ?? 0, unrealized_pnl: unrealized,
    positions: positions.map((p) => ({ ...p, unrealized_pnl: (p.last_price - p.average_price) * p.quantity })) as Portfolio["positions"],
    closed_positions: book?.closed_positions ?? [], lane: LANE,
  }), [equity, cash, positions, summary, book, unrealized]);
  const indices = Object.entries(health?.indices || {});
  const held = positions.filter((p) => p.index === selected);
  const chartSymbol = held[0]?.symbol || INDEX_SPOT[selected] || "";
  const markers = useMemo(() => (held[0] ? fillMarkers(trades, held[0].symbol) : []), [trades, held]);
  const focusFor = useCallback((row: FocusRow): LaneChartFocus => ({ symbol: row.symbol, title: short(row.symbol),
    subtitle: `Entry ₹${row.entryPrice.toFixed(2)} · ${when(row.entryTime)}`, markers: fillMarkers(trades, row.symbol, row.entryTime), levels: [] }), [trades]);
  const openChart = useCallback((row: FocusRow, list: FocusRow[]) => {
    const rows = list.length ? list : [row];
    setFocus({ list: rows.map(focusFor), index: Math.max(0, rows.findIndex((r) => r.symbol === row.symbol && r.entryTime === row.entryTime)) });
  }, [focusFor]);
  const all = summary?.all;
  const counts: Partial<Record<Tab, number>> = { PORTFOLIO: positions.length, ORDERS: (book?.orders ?? []).filter((o) => istDay(o.created_at) === today).length,
    TRADES: trades.filter((t) => istDay(t.timestamp) === today).length, JOURNAL: journal.filter((r) => istDay(r.at) === today).length };

  return <section className="ledger-page blast-page spot-page">
    <div className="page-heading blast-heading">
      <div><h1>Trend index lane</h1>
        <p>Each index's 30-minute trend state traded as about-2%-ITM monthly options: initiate at the reversal, add on a resumed strong trend, reduce when old and fading, flip at the next reversal · {money(capital || 500000)} paper book</p></div>
      <div className="blast-badges">
        <span className="feed-badge" title="Every state change is traded; cash is the only gate.">Auto-trade · paper</span>
        {(error || health?.error) && <span className="mode alert" title={error || health?.error || ""}>Lane error</span>}
      </div>
    </div>

    <section className="blast-metrics" aria-label="Trend lane book">
      <Metric label="Equity" value={rupees(equity)} />
      <Metric label={`Return on ${capital ? money(capital) : "capital"}`} value={capital ? percent((equity / capital - 1) * 100, 2) : "—"} tone={equity - capital} />
      <Metric label="Realized · lifetime" value={rupees(summary?.realized ?? 0)} tone={summary?.realized ?? 0} />
      <Metric label="Open P&L · current marks" value={rupees(unrealized)} tone={unrealized} />
      <Metric label="Open legs · cash" value={`${positions.length} · ${money(cash)}`} />
      <Metric label="Active since" value={when(health?.activated_at)} />
    </section>

    <div className="watch-tabs blast-tabs" role="tablist" aria-label="Trend lane sections">
      {TABS.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => pickTab(key)}>
        {label}{counts[key] ? <span className="mp-count">{counts[key]}</span> : null}
      </button>)}
    </div>
    {(error || health?.error) && <div className="settings-error" role="alert">{error || health?.error}</div>}

    <div className="blast-body" role="tabpanel">
      {tab === "STATES" && <>
        <section className="panel agent-panel">
          <div className="panel-title">Trend states <span>evaluated {when(health?.last_evaluated_at)}</span></div>
          <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr>
            <th>Index</th><th>Trend</th><th>Since</th><th>Age (bars)</th><th>Strength</th><th>Pullback</th><th>Units P1 / held</th><th>Last bar</th><th>Contract</th>
          </tr></thead><tbody>{indices.map(([index, s]) => {
            const pick = health?.picks.find((p) => p.index === index && p.option_type === (s.direction === "DOWN" ? "PE" : "CE"));
            return <tr key={index} className={`openable-row${selected === index ? " active" : ""}`} onClick={() => setSelected(index)}>
              <td>{index}</td><td className={dirTone(s.direction)}>{s.direction === "UP" ? "Up" : s.direction === "DOWN" ? "Down" : "—"}</td>
              <td>{when(s.confirmed_at)}</td><td>{s.age_bars ?? "—"}</td><td>{s.fade == null ? "—" : `${Math.round(s.fade * 100)}% of peak`}</td>
              <td>{s.pullback_atr == null ? "—" : `${s.pullback_atr.toFixed(2)} ATR`}</td><td>{s.units ?? 0} / {s.held_units}</td>
              <td>{when(s.last_bar)}{s.last_action ? ` · ${words(s.last_action)}` : ""}</td>
              <td>{pick ? `${short(pick.symbol)} · ${pick.expiry}` : s.pick_error ? <span className="negative">{s.pick_error}</span> : "—"}</td>
            </tr>;
          })}</tbody></table></div>
          {!indices.length && <div className="agent-empty">Waiting for the lane's first evaluation (09:15–15:30 IST on trading days).</div>}
        </section>
        <section className="chart panel">
          <div className="panel-title"><span>{short(chartSymbol)}</span>{held[0] && <span className={tone(held[0].last_price - held[0].average_price)}>
            {held.reduce((n, p) => n + p.lots, 0)} lot(s) @ {held[0].average_price.toFixed(2)} · {percent((held[0].last_price / held[0].average_price - 1) * 100, 2)}</span>}</div>
          {chartSymbol && <LaneChart symbol={chartSymbol} markers={markers} levels={[]} />}
        </section>
        <section className="panel agent-panel">
          <div className="panel-title">Rules</div>
          <table className="live-table agent-table"><tbody>
            {Object.entries(health?.rules || {}).map(([k, v]) => <tr key={k}><th>{words(k)}</th><td>{String(v)}</td></tr>)}
            <tr><th>Evidence</th><td>{health?.validation ?? "—"}</td></tr>
          </tbody></table>
        </section>
      </>}

      {(tab === "PORTFOLIO" || tab === "ORDERS" || tab === "TRADES") && <TradingLedger tab={tab} orders={book?.orders ?? []} trades={trades}
        portfolio={ledgerPortfolio} signals={[]} onOpenPosition={openChart} holidays={holidays} ledgerReady={!!book} />}

      {tab === "PERFORMANCE" && <>
        <section className="panel agent-panel">
          <div className="panel-title">Equity and drawdown · today <span>{curve.length} points</span></div>
          <EquityChart points={curve} capital={capital} height={260} />
          <div className="agent-stats lane-stats">
            <div className="agent-stat"><span>Closed trades</span><b>{all?.trades ?? 0}</b><small>win {all?.win_rate == null ? "—" : `${Math.round(all.win_rate * 100)}%`} · PF {all?.profit_factor == null ? "—" : all.profit_factor.toFixed(2)}</small></div>
            <div className="agent-stat"><span>Avg win / loss</span><b className="positive">{all?.avg_win == null ? "—" : rupees(all.avg_win)}</b><small className="negative">{all?.avg_loss == null ? "—" : rupees(all.avg_loss)}</small></div>
            <div className="agent-stat"><span>Max drawdown</span><b className={(summary?.max_drawdown ?? 0) > 0 ? "negative" : ""}>{rupees(-(summary?.max_drawdown ?? 0))}</b></div>
          </div>
        </section>
        <section className="panel agent-panel">
          <div className="panel-title">Closed trades today ({closed.length})</div>
          {closed.length === 0 ? <div className="agent-empty">No closed trades today.</div> :
            <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr><th>Contract</th><th>Entry</th><th>Exit</th><th>Lots</th><th>P&amp;L</th><th>Return</th><th>Exit reason</th></tr></thead>
              <tbody>{closed.map((t) => <tr key={t.position_id}><td>{short(t.symbol)}</td><td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td><td>{t.lots}</td>
                <td className={tone(t.pnl)}>{rupees(t.pnl)}</td><td className={tone(t.return_pct)}>{percent(t.return_pct, 2)}</td><td>{words(t.exit_reason)}{t.closed ? "" : " (part)"}</td></tr>)}</tbody></table></div>}
        </section>
      </>}

      {tab === "JOURNAL" && <section className="panel agent-panel">
        <div className="panel-title">Journal — every state change and what was done</div>
        {journal.length === 0 ? <div className="agent-empty">No decisions journalled yet.</div> :
          <div className="live-table-wrap lane-scroll"><table className="live-table agent-table"><thead><tr><th>Time</th><th>Index</th><th>Contract</th><th>Action</th><th>Decision</th><th>Lots</th></tr></thead>
            <tbody>{journal.map((row) => <tr key={row.id}><td>{when(row.at)}</td><td>{String(row.detail.index ?? "—")}</td><td>{short(row.symbol)}</td>
              <td>{words(String(row.detail.action ?? ""))}</td>
              <td className={row.decision === "TAKEN" ? "positive" : ""}>{words(row.decision)}<details><summary>ⓘ</summary><pre>{JSON.stringify(row.detail, null, 2)}</pre></details></td>
              <td>{row.lots ?? "—"}</td></tr>)}</tbody></table></div>}
      </section>}
    </div>
    {focus && <LaneChartModal list={focus.list} index={focus.index} onIndex={(index) => setFocus((old) => old && { ...old, index })} onClose={() => setFocus(undefined)} />}
  </section>;
}

function Metric({ label, value, tone: sign }: { label: string; value: string; tone?: number }) {
  return <div><span>{label}</span><b className={sign === undefined ? "" : sign >= 0 ? "positive" : "negative"}>{value}</b></div>;
}
