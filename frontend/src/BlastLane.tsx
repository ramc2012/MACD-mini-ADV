import { useEffect, useMemo, useState, type ReactNode } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import { requestJson } from "./requestJson";
import { SortableTable, type Column, type FocusRow, type SortState } from "./Ledger";
import { BOOK_ROLL_LABEL, observedExcursion, visibleClosedRows } from "./ledgerMath";
import {
  RISK_KEYS, VERDICT_ORDER, activeStop, changedSettings, fromForm, istDay, mergeCandidates, screenFunnel, toForm, verdictMeta,
  type BlastForm, type FunnelKey,
} from "./blastMath";
import type {
  BlastBook, BlastCandidate, BlastJournalResponse, BlastJournalSummary, BlastSnapshot, BlastVerdictSummary,
  ClosedPosition, Order, Portfolio, Position, Trade,
} from "./types";

/** The blast lane: a third paper book with its own screen, journal and risk overlay.
 *
 * Everything here reads the lane's own endpoints and its blast_* stream
 * frames. None of it can move the MACD lane's orders, trades or portfolio,
 * which is why App keeps this state apart from theirs.
 */

type Tab = "OVERVIEW" | "JOURNAL" | "POSITIONS" | "ORDERS" | "TRADES" | "SETTINGS";
const TABS: { key: Tab; label: string }[] = [
  { key: "OVERVIEW", label: "Overview" },
  { key: "JOURNAL", label: "Journal" },
  { key: "POSITIONS", label: "Positions" },
  { key: "ORDERS", label: "Orders" },
  { key: "TRADES", label: "Trades" },
  { key: "SETTINGS", label: "Settings" },
];
const TAB_STORE = "macd.blastTab";
// Safari's private mode throws on localStorage, and a lost preference must not take the page down.
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { /* preference not kept */ } };
const authHeaders = (): Record<string, string> => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});

const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const currency = (value: number) => `₹${number.format(value)}`;
const STAMP = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const CLOCK = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const stamp = (value?: string | null) => (value ? STAMP.format(new Date(value)) : "—");
const clock = (value?: string | null) => (value ? CLOCK.format(new Date(value)) : "—");
const finite = (value?: number | null): value is number => typeof value === "number" && Number.isFinite(value);
const pct = (value?: number | null, digits = 2) => (finite(value) ? `${value.toFixed(digits)}%` : "—");
const signed = (value?: number | null, digits = 1) => (finite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%` : "—");
const tone = (value?: number | null): "positive" | "negative" | "" => (finite(value) ? (value >= 0 ? "positive" : "negative") : "");
const trim = (value: number) => String(Number(value.toFixed(4)));
const short = (symbol: string) => symbol.split(":")[1] ?? symbol;
// Missing values sort to the bottom of a descending column.
const LOW = Number.NEGATIVE_INFINITY;
const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : "Request failed");
const FUNNEL_LABEL: Record<FunnelKey, string> = {
  evaluated: "Signal-line crosses judged",
  judged: "Not held, with spot, breadth and history",
  premium: "Cleared premium vs spot",
  breadth: "Cleared side breadth",
  offHigh: "Cleared distance off high",
  liquid: "Liquid enough to size",
  taken: "Taken",
};

export function BlastLanePage({ snapshot, live, liveOrders, liveTrades, revision, onSnapshot, onOpenPosition }: {
  snapshot?: BlastSnapshot;
  /** Candidates pushed over the stream since the last snapshot. */
  live: BlastCandidate[];
  liveOrders: Order[];
  liveTrades: Trade[];
  /** Bumped on every terminal snapshot; a resync refetches what the stream may have missed. */
  revision: number;
  onSnapshot: (next: BlastSnapshot) => void;
  onOpenPosition?: (position: FocusRow, list: FocusRow[]) => void;
}) {
  const ready = !!snapshot;
  const [tab, setTab] = useState<Tab>(() => {
    const stored = recall(TAB_STORE);
    return TABS.some((row) => row.key === stored) ? stored as Tab : "OVERVIEW";
  });
  const pickTab = (next: Tab) => { setTab(next); remember(TAB_STORE, next); };
  const [minute, setMinute] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    // Excursions are written back every 30s and closed rows age out at 08:00 IST.
    const timer = window.setInterval(() => { setMinute((count) => count + 1); setNow(Date.now()); }, 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const today = istDay(now);
  // A burst of candidates refreshes the server-side counts once, a moment later.
  const [liveNonce, setLiveNonce] = useState(0);
  useEffect(() => {
    if (!live.length) return;
    const timer = window.setTimeout(() => setLiveNonce((count) => count + 1), 3_000);
    return () => window.clearTimeout(timer);
  }, [live.length]);

  const [todayData, setTodayData] = useState<BlastJournalResponse>();
  const [todayError, setTodayError] = useState("");
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    void requestJson<BlastJournalResponse>(`${API_URL}/api/blast/journal?day=${today}&limit=60`, { headers: authHeaders(), signal: controller.signal })
      .then((data) => { setTodayData(data); setTodayError(""); })
      .catch((reason) => { if (!controller.signal.aborted) setTodayError(errorText(reason)); });
    return () => controller.abort();
  }, [ready, today, minute, liveNonce, revision]);

  const [day, setDay] = useState(() => istDay());
  const [reason, setReason] = useState("");
  const [journal, setJournal] = useState<BlastJournalResponse>();
  const [journalLoading, setJournalLoading] = useState(false);
  const [journalError, setJournalError] = useState("");
  useEffect(() => {
    if (tab !== "JOURNAL" || !ready) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ limit: "1000" });
    if (day) params.set("day", day);
    if (reason) params.set("reason", reason);
    setJournalLoading(true);
    void requestJson<BlastJournalResponse>(`${API_URL}/api/blast/journal?${params}`, { headers: authHeaders(), signal: controller.signal })
      .then((data) => { setJournal(data); setJournalError(""); })
      .catch((error) => { if (!controller.signal.aborted) setJournalError(errorText(error)); })
      .finally(() => { if (!controller.signal.aborted) setJournalLoading(false); });
    return () => controller.abort();
  }, [tab, ready, day, reason, minute, liveNonce, revision]);

  const [book, setBook] = useState<BlastBook>();
  const [bookError, setBookError] = useState("");
  const wantsBook = tab === "ORDERS" || tab === "TRADES";
  useEffect(() => {
    if (!wantsBook || !ready) return;
    const controller = new AbortController();
    void requestJson<BlastBook>(`${API_URL}/api/blast/book`, { headers: authHeaders(), signal: controller.signal })
      .then((data) => { setBook(data); setBookError(""); })
      .catch((error) => { if (!controller.signal.aborted) setBookError(errorText(error)); });
    return () => controller.abort();
  }, [wantsBook, ready, revision, liveOrders.length, liveTrades.length]);

  const journalRows = useMemo(() => mergeCandidates(
    journal?.rows ?? [],
    live.filter((row) => (!day || row.day === day) && (!reason || row.reason === reason)),
    1000,
  ), [journal, live, day, reason]);
  const recent = useMemo(() => mergeCandidates(todayData?.rows ?? [], live.filter((row) => row.day === today), 40), [todayData, live, today]);
  const orders = useMemo(() => mergeById(book?.orders ?? [], liveOrders, "order_id"), [book, liveOrders]);
  const trades = useMemo(() => mergeById(book?.trades ?? [], liveTrades, "trade_id"), [book, liveTrades]);

  if (!snapshot) {
    return <section className="ledger-page blast-page">
      <div className="page-heading"><div><h1>Blast lane</h1><p>Waiting for the lane's first snapshot…</p></div></div>
      <section className="panel blast-empty" role="status">No blast lane state has arrived yet. It comes with the terminal snapshot once the backend that runs the lane is connected.</section>
    </section>;
  }

  const portfolio = snapshot.portfolio;
  const summary: BlastJournalSummary | undefined = todayData?.summary
    ?? (snapshot.journal_summary.day === today ? snapshot.journal_summary : undefined);
  const funnel = screenFunnel(summary);
  const passed = funnel.find((step) => step.key === "offHigh")?.count ?? 0;
  const closed = visibleClosedRows(portfolio.closed_positions ?? snapshot.closed_positions ?? [], now);
  const counts: Partial<Record<Tab, number>> = {
    JOURNAL: summary?.evaluated ?? 0,
    POSITIONS: portfolio.positions.length,
    ...(book ? { ORDERS: orders.length, TRADES: trades.length } : {}),
  };
  const days = [today, ...(todayData?.days ?? journal?.days ?? []).filter((row) => row !== today)];

  return <section className="ledger-page blast-page">
    <div className="page-heading blast-heading">
      <div><h1>Blast lane</h1>
        <p>Premium MACD signal-line crosses, screened by premium ÷ spot, own-side breadth and distance below the recent high · its own paper book</p></div>
      <div className="blast-badges">
        {!snapshot.enabled && <span className="mode alert" title="The screen is off. Open positions keep their stop and trail.">Disabled</span>}
        {snapshot.enabled && (snapshot.auto_trade
          ? <span className="feed-badge" title="Candidates that pass the screen are bought in the lane's paper book.">Auto-trade · paper</span>
          : <span className="mode" title="Every candidate is journalled and tracked forward. Nothing is bought.">Shadow · journal only</span>)}
        <span className="mode" title="This lane reads only data the engine already holds. Its broker handle can place paper orders and nothing else.">Broker: orders only</span>
        {snapshot.error && <span className="mode alert" title={snapshot.error}>Lane error</span>}
      </div>
    </div>

    <section className="blast-metrics" aria-label="Blast lane book">
      <Metric label="Equity" value={currency(portfolio.equity)} />
      <Metric label="Realized" value={currency(portfolio.realized_pnl)} tone={portfolio.realized_pnl} />
      <Metric label="Unrealized" value={currency(portfolio.unrealized_pnl)} tone={portfolio.unrealized_pnl} />
      <Metric label="Open positions" value={`${portfolio.positions.length} · ${currency(portfolio.market_value)}`} />
      <Metric label="Today · judged / passed / taken" value={`${summary?.evaluated ?? 0} / ${passed} / ${summary?.taken ?? 0}`} />
      <Metric label="Tracking forward" value={`${snapshot.health.watching} candidates`} />
    </section>

    <div className="watch-tabs blast-tabs" role="tablist" aria-label="Blast lane sections">
      {TABS.map((row) => <button key={row.key} role="tab" aria-selected={tab === row.key} className={tab === row.key ? "active" : ""} onClick={() => pickTab(row.key)}>
        {row.label}{counts[row.key] !== undefined && <span className="mp-count">{counts[row.key]}</span>}
      </button>)}
    </div>

    <div className="blast-body">
      {tab === "OVERVIEW" && <Overview snapshot={snapshot} summary={summary} recent={recent} error={todayError} />}
      {tab === "JOURNAL" && <JournalTab rows={journalRows} days={days} day={day} reason={reason} today={today}
        onDay={setDay} onReason={setReason} loading={journalLoading} error={journalError} />}
      {tab === "POSITIONS" && <PositionsTab portfolio={portfolio} closed={closed} onOpenPosition={onOpenPosition} />}
      {tab === "ORDERS" && <OrdersTab orders={orders} error={bookError} loaded={!!book} />}
      {tab === "TRADES" && <TradesTab trades={trades} error={bookError} loaded={!!book} />}
      {tab === "SETTINGS" && <SettingsTab snapshot={snapshot} onSaved={onSnapshot} />}
    </div>
  </section>;
}

function Metric({ label, value, tone: sign }: { label: string; value: string; tone?: number }) {
  return <div><span>{label}</span><b className={sign === undefined ? "" : sign >= 0 ? "positive" : "negative"}>{value}</b></div>;
}

function Verdict({ reason }: { reason: string }) {
  const meta = verdictMeta(reason);
  return <span className={`verdict ${meta.tone}`} title={meta.help}>{meta.label}</span>;
}

function Overview({ snapshot, summary, recent, error }: {
  snapshot: BlastSnapshot; summary?: BlastJournalSummary; recent: BlastCandidate[]; error: string;
}) {
  const [verdictSort, setVerdictSort] = useState<SortState>({ key: "reason", direction: "asc" });
  const [recentSort, setRecentSort] = useState<SortState>({ key: "at", direction: "desc" });
  const settings = snapshot.settings;
  const funnel = screenFunnel(summary);
  const cleared = (key: FunnelKey) => funnel.find((step) => step.key === key)?.count ?? 0;
  const gates: { key: string; title: string; rule: string; detail: string; count?: number }[] = [
    { key: "event", title: "Entry event", rule: "MACD crosses its signal line",
      detail: "MACD 12/26/9 on a closed one-minute premium bar, whatever the strategy timeframe. The zero-cross is not required: almost every screened candidate still has MACD below zero." },
    { key: "premium", title: "Premium vs spot", rule: `≤ ${trim(settings.max_premium_pct)}% of spot`, count: cleared("premium"),
      detail: "The load-bearing leg. In walk-forward, dropping it halved the rate at which picks doubled." },
    { key: "breadth", title: "Own-side breadth", rule: `≥ ${trim(settings.min_breadth * 100)}% above zero`, count: cleared("breadth"),
      detail: "Share of tracked contracts on the same side whose one-minute premium MACD is above zero, counting contracts with a bar in the last five minutes." },
    { key: "offHigh", title: "Off its recent high", rule: `≥ ${trim(settings.min_off_high_pct)}% below`, count: cleared("offHigh"),
      detail: `High of the last ${snapshot.screen.recent_high_lookback_bars} closed one-minute bars, not counting the bar being judged.` },
    { key: "liquid", title: "Liquidity", rule: `≤ ${trim(snapshot.screen.max_volume_share_pct)}% of its own volume`, count: cleared("liquid"),
      detail: "The entry is sized down to this share of what the contract itself trades, and declined when even one lot exceeds it." },
  ];
  const verdictRows = useMemo(() => summary?.verdicts ?? [], [summary]);
  const order = VERDICT_ORDER as readonly string[];
  const verdictColumns: Column<BlastVerdictSummary>[] = [
    { key: "reason", label: "Verdict", value: (row) => order.indexOf(row.reason), render: (row) => <Verdict reason={row.reason} /> },
    { key: "count", label: "Candidates", value: (row) => row.count },
    { key: "resolved", label: "Window closed", value: (row) => row.resolved, render: (row) => `${row.resolved} / ${row.count}` },
    { key: "mfe", label: "Mean max gain", value: (row) => row.mean_mfe_pct ?? LOW, render: (row) => signed(row.mean_mfe_pct), tone: (row) => tone(row.mean_mfe_pct) },
    { key: "mae", label: "Mean max drawdown", value: (row) => row.mean_mae_pct ?? LOW, render: (row) => signed(row.mean_mae_pct), tone: (row) => (finite(row.mean_mae_pct) && row.mean_mae_pct < 0 ? "negative" : "") },
  ];
  const recentColumns: Column<BlastCandidate>[] = [
    { key: "at", label: "Time", value: (row) => Date.parse(row.at), render: (row) => clock(row.at) },
    { key: "symbol", label: "Contract", value: (row) => row.symbol, render: (row) => <span title={row.symbol}>{short(row.symbol)}</span> },
    { key: "reason", label: "Verdict", value: (row) => row.reason, render: (row) => <Verdict reason={row.reason} /> },
    // Premium ÷ spot lives in the journal; four columns are what this narrow panel holds without clipping.
    { key: "mfe", label: "Max gain", value: (row) => row.mfe_pct ?? LOW, render: (row) => signed(row.mfe_pct), tone: (row) => tone(row.mfe_pct) },
  ];
  return <div className="blast-grid">
    <section className="panel blast-screen">
      <div className="panel-title"><span>Screen</span><span>{cleared("judged")} judged today</span></div>
      <div className="blast-gates">{gates.map((gate) => <div className="blast-gate" key={gate.key}>
        <b>{gate.title}</b><code>{gate.rule}</code>
        <small>{gate.detail}{gate.count !== undefined ? ` Cleared today: ${gate.count}.` : ""}</small>
      </div>)}</div>
    </section>

    <section className="panel blast-funnel">
      <div className="panel-title"><span>Today's funnel</span><span>{summary?.evaluated ?? 0} crosses</span></div>
      <div className="blast-steps">{funnel.map((step) => <div key={step.key} className={`blast-step${step.key === "taken" ? " final" : ""}`}>
        <span>{FUNNEL_LABEL[step.key]}</span><b>{step.count}</b>
        <div className="blast-meter" aria-hidden="true"><i style={{ width: `${Math.round(step.share * 100)}%` }} /></div>
      </div>)}</div>
      {error && <p className="blast-note negative" role="status">{error}</p>}
    </section>

    <section className="panel blast-risk">
      <div className="panel-title"><span>Book and risk</span><span>{snapshot.auto_trade ? "auto-trade" : "shadow"}</span></div>
      <div className="blast-kv">
        <span>Hard stop</span><b>−{trim(settings.hard_stop_pct * 100)}%</b>
        <span>Trailing stop</span><b>{trim(settings.trail_pct * 100)}% from peak, from +{trim(settings.trail_activation_pct * 100)}%</b>
        <span>Scale-outs · pyramiding</span><b>None · none</b>
        <span>Size per entry</span><b>{currency(settings.target_notional)}</b>
        <span>Max open positions</span><b>{settings.max_positions}</b>
        <span>Capital</span><b>{currency(settings.initial_capital)}</b>
        <span>Market data</span><b>Engine memory only</b>
      </div>
      <p className="blast-note">The trail never arms below the round-trip breakeven. An expiring contract is flattened at 15:20 IST from the lane's own contract record.</p>
    </section>

    <section className="panel blast-verdicts">
      <div className="panel-title"><span>Verdicts and forward excursion</span><span>control group</span></div>
      <p className="blast-note">Every candidate that clears the premium gate is tracked for {trim(snapshot.screen.journal_horizon_hours)} hours whether or not the lane bought it. Compare the passed rows' excursion with the declined ones to see whether each leg earns its place.</p>
      <SortableTable rows={verdictRows} columns={verdictColumns} sort={verdictSort}
        onSort={(key) => setVerdictSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
        empty="No candidates judged today yet" />
    </section>

    <section className="panel blast-recent">
      <div className="panel-title"><span>Latest candidates</span><span>{recent.length}</span></div>
      <SortableTable rows={recent} columns={recentColumns} sort={recentSort}
        onSort={(key) => setRecentSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
        empty="No signal-line crosses judged yet today" />
    </section>
  </div>;
}

function JournalTab({ rows, days, day, reason, today, onDay, onReason, loading, error }: {
  rows: BlastCandidate[]; days: string[]; day: string; reason: string; today: string;
  onDay: (day: string) => void; onReason: (reason: string) => void; loading: boolean; error: string;
}) {
  const [sort, setSort] = useState<SortState>({ key: "at", direction: "desc" });
  const allSessions = !day;
  const columns: Column<BlastCandidate>[] = [
    { key: "at", label: "Time (IST)", value: (row) => Date.parse(row.at), render: (row) => (allSessions ? stamp(row.at) : clock(row.at)) },
    { key: "symbol", label: "Contract", value: (row) => row.symbol, render: (row) => <span title={row.symbol}>{short(row.symbol)}</span> },
    { key: "reason", label: "Verdict", value: (row) => row.reason, render: (row) => <Verdict reason={row.reason} /> },
    { key: "premium", label: "Premium", value: (row) => row.premium, render: (row) => currency(row.premium) },
    { key: "spot", label: "Spot", value: (row) => row.spot ?? LOW, render: (row) => (finite(row.spot) ? number.format(row.spot) : "—") },
    { key: "premium_pct", label: "Prem / spot", value: (row) => row.premium_pct ?? LOW, render: (row) => pct(row.premium_pct) },
    { key: "breadth", label: "Breadth", value: (row) => row.breadth ?? LOW, render: (row) => (finite(row.breadth) ? pct(row.breadth * 100, 0) : "—") },
    { key: "off_high", label: "Off high", value: (row) => row.off_high_pct ?? LOW, render: (row) => signed(row.off_high_pct) },
    { key: "macd", label: "MACD / signal", value: (row) => row.macd, render: (row) => `${row.macd.toFixed(3)} / ${row.signal.toFixed(3)}` },
    { key: "bars", label: "Bars", value: (row) => row.lookback_bars },
    { key: "mfe", label: "Max gain", value: (row) => row.mfe_pct ?? LOW, render: (row) => signed(row.mfe_pct), tone: (row) => tone(row.mfe_pct) },
    { key: "mae", label: "Max drawdown", value: (row) => row.mae_pct ?? LOW, render: (row) => signed(row.mae_pct), tone: (row) => (finite(row.mae_pct) && row.mae_pct < 0 ? "negative" : "") },
    { key: "window", label: "Tracking", value: (row) => (row.resolved ? 2 : row.watch_until ? 1 : 0),
      render: (row) => (row.resolved ? "Window closed" : row.watch_until ? `Until ${stamp(row.watch_until)}` : "Not tracked") },
  ];
  return <section className="panel blast-panel-fill">
    <div className="blast-toolbar">
      <select className="rrg-select" value={day} onChange={(event) => onDay(event.target.value)} aria-label="Session">
        {days.map((row) => <option key={row} value={row}>{row === today ? `Today · ${row}` : row}</option>)}
        <option value="">All sessions</option>
      </select>
      <span className="radar-filter" role="group" aria-label="Filter by verdict">
        <button className={!reason ? "active" : ""} aria-pressed={!reason} onClick={() => onReason("")}>All</button>
        {VERDICT_ORDER.map((row) => <button key={row} className={reason === row ? "active" : ""} aria-pressed={reason === row}
          title={verdictMeta(row).help} onClick={() => onReason(row)}>{verdictMeta(row).label}</button>)}
      </span>
      <span className="blast-count" role="status">{loading ? "Loading…" : `${rows.length} candidate${rows.length === 1 ? "" : "s"}`}</span>
    </div>
    {error && <div className="settings-error blast-inline-error" role="alert">{error}</div>}
    <SortableTable rows={rows} columns={columns} sort={sort}
      onSort={(key) => setSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
      empty={reason ? `No "${verdictMeta(reason).label}" candidates for this selection` : "No candidates journalled for this selection"} />
  </section>;
}

function PositionsTab({ portfolio, closed, onOpenPosition }: {
  portfolio: Portfolio; closed: ClosedPosition[]; onOpenPosition?: (position: FocusRow, list: FocusRow[]) => void;
}) {
  const [openSort, setOpenSort] = useState<SortState>({ key: "pnl", direction: "desc" });
  const [closedSort, setClosedSort] = useState<SortState>({ key: "exit", direction: "desc" });
  const openFocus = (row: Position): FocusRow => ({ symbol: row.symbol, entryTime: row.opened_at, entryPrice: row.average_price });
  const closedFocus = (row: ClosedPosition): FocusRow => ({ symbol: row.symbol, entryTime: row.entry_time, entryPrice: row.entry_price });
  const openList = portfolio.positions.map(openFocus);
  const closedList = closed.map(closedFocus);
  const excursion = (price?: number | null, value?: number | null) => observedExcursion(price, value);
  const openColumns: Column<Position>[] = [
    { key: "symbol", label: "Contract", value: (row) => row.symbol, render: (row) => <span title={row.symbol}>{short(row.symbol)}</span> },
    { key: "opened", label: "Entry (IST)", value: (row) => Date.parse(row.opened_at), render: (row) => stamp(row.opened_at) },
    { key: "lots", label: "Lots", value: (row) => row.lots, render: (row) => (row.lot_size > 0 ? `${row.lots} × ${row.lot_size}` : "—") },
    { key: "average", label: "Average", value: (row) => row.average_price, render: (row) => currency(row.average_price) },
    { key: "ltp", label: "LTP", value: (row) => row.last_price, render: (row) => currency(row.last_price) },
    { key: "pnl", label: "P&L", value: (row) => row.unrealized_pnl, render: (row) => currency(row.unrealized_pnl), tone: (row) => tone(row.unrealized_pnl) },
    { key: "return", label: "Return", value: (row) => row.return_pct ?? 0, render: (row) => signed(row.return_pct, 2), tone: (row) => tone(row.return_pct) },
    { key: "max", label: "Max return", value: (row) => excursion(row.max_price, row.max_return_pct) ?? LOW, render: (row) => signed(excursion(row.max_price, row.max_return_pct), 2) },
    { key: "min", label: "Min return", value: (row) => excursion(row.min_price, row.min_return_pct) ?? LOW, render: (row) => signed(excursion(row.min_price, row.min_return_pct), 2) },
    { key: "stop", label: "Active stop", value: (row) => activeStop(row)?.price ?? LOW,
      render: (row) => { const stop = activeStop(row); return stop ? `${stop.kind === "trail" ? "Trail" : "Hard"} ${currency(stop.price)}` : "—"; } },
    { key: "room", label: "Room to stop", value: (row) => activeStop(row)?.distancePct ?? LOW, render: (row) => pct(activeStop(row)?.distancePct) },
  ];
  const closedColumns: Column<ClosedPosition>[] = [
    { key: "symbol", label: "Contract", value: (row) => row.symbol,
      render: (row) => <span title={row.partial ? `Partial exit · ${row.remaining_quantity} units still open` : row.symbol}>{short(row.symbol)}{row.partial ? " ·" : ""}</span> },
    { key: "entry", label: "Entry (IST)", value: (row) => Date.parse(row.entry_time), render: (row) => stamp(row.entry_time) },
    { key: "exit", label: "Exit (IST)", value: (row) => Date.parse(row.exit_time), render: (row) => stamp(row.exit_time) },
    { key: "entry_price", label: "Entry", value: (row) => row.entry_price, render: (row) => currency(row.entry_price) },
    { key: "exit_price", label: "Exit", value: (row) => row.exit_price, render: (row) => currency(row.exit_price) },
    { key: "pnl", label: "Realized", value: (row) => row.realized_pnl, render: (row) => currency(row.realized_pnl), tone: (row) => tone(row.realized_pnl) },
    { key: "return", label: "Return", value: (row) => row.return_pct, render: (row) => signed(row.return_pct, 2), tone: (row) => tone(row.return_pct) },
    { key: "max", label: "Max return", value: (row) => excursion(row.max_price, row.max_return_pct) ?? LOW, render: (row) => signed(excursion(row.max_price, row.max_return_pct), 2) },
    { key: "min", label: "Min return", value: (row) => excursion(row.min_price, row.min_return_pct) ?? LOW, render: (row) => signed(excursion(row.min_price, row.min_return_pct), 2) },
    { key: "reason", label: "Exit reason", value: (row) => row.exit_reason || "", render: (row) => row.exit_reason || "—" },
  ];
  const openPnl = portfolio.positions.reduce((sum, row) => sum + row.unrealized_pnl, 0);
  const realized = closed.reduce((sum, row) => sum + row.realized_pnl, 0);
  return <div className="blast-split">
    <section className="panel">
      <div className="panel-title"><span>Open positions</span><span className={tone(openPnl)}>{portfolio.positions.length} · {currency(openPnl)}</span></div>
      <SortableTable rows={portfolio.positions} columns={openColumns} sort={openSort}
        onSort={(key) => setOpenSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
        rowAction={onOpenPosition ? (row) => onOpenPosition(openFocus(row), openList) : undefined}
        empty="The lane holds nothing" />
    </section>
    <section className="panel">
      <div className="panel-title"><span>Closed since {BOOK_ROLL_LABEL}</span><span className={tone(realized)}>{closed.length} exit{closed.length === 1 ? "" : "s"} · {currency(realized)}</span></div>
      <SortableTable rows={closed} columns={closedColumns} sort={closedSort}
        onSort={(key) => setClosedSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
        rowAction={onOpenPosition ? (row) => onOpenPosition(closedFocus(row), closedList) : undefined}
        empty={`Nothing closed since ${BOOK_ROLL_LABEL}`} />
    </section>
  </div>;
}

function OrdersTab({ orders, error, loaded }: { orders: Order[]; error: string; loaded: boolean }) {
  const [sort, setSort] = useState<SortState>({ key: "time", direction: "desc" });
  const columns: Column<Order>[] = [
    { key: "time", label: "Time (IST)", value: (row) => Date.parse(row.created_at), render: (row) => stamp(row.created_at) },
    { key: "symbol", label: "Contract", value: (row) => row.symbol, render: (row) => <span title={row.symbol}>{short(row.symbol)}</span> },
    { key: "side", label: "Side", value: (row) => row.side, tone: (row) => (row.side === "BUY" ? "positive" : "negative") },
    { key: "lots", label: "Lots", value: (row) => row.lots || 0, render: (row) => `${row.lots || "—"} × ${row.lot_size || 1}` },
    { key: "fill", label: "Fill", value: (row) => row.fill_price ?? LOW, render: (row) => (row.fill_price ? currency(row.fill_price) : "—") },
    { key: "status", label: "Status", value: (row) => row.status },
  ];
  return <BookPanel title="Paper orders" count={orders.length} error={error} loaded={loaded}>
    <SortableTable rows={orders} columns={columns} sort={sort}
      onSort={(key) => setSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
      empty={loaded ? "The lane has placed no orders" : "Loading orders…"} />
  </BookPanel>;
}

function TradesTab({ trades, error, loaded }: { trades: Trade[]; error: string; loaded: boolean }) {
  const [sort, setSort] = useState<SortState>({ key: "time", direction: "desc" });
  const columns: Column<Trade>[] = [
    { key: "time", label: "Time (IST)", value: (row) => Date.parse(row.timestamp), render: (row) => stamp(row.timestamp) },
    { key: "symbol", label: "Contract", value: (row) => row.symbol, render: (row) => <span title={row.symbol}>{short(row.symbol)}</span> },
    { key: "side", label: "Side", value: (row) => row.side, tone: (row) => (row.side === "BUY" ? "positive" : "negative") },
    { key: "lots", label: "Lots", value: (row) => row.lots || 0, render: (row) => `${row.lots || "—"} × ${row.lot_size || 1}` },
    { key: "price", label: "Fill price", value: (row) => row.price, render: (row) => currency(row.price) },
    { key: "value", label: "Value", value: (row) => row.price * row.quantity, render: (row) => currency(row.price * row.quantity) },
  ];
  return <BookPanel title="Paper fills" count={trades.length} error={error} loaded={loaded}>
    <SortableTable rows={trades} columns={columns} sort={sort}
      onSort={(key) => setSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
      empty={loaded ? "The lane has no fills" : "Loading fills…"} />
  </BookPanel>;
}

function BookPanel({ title, count, error, loaded, children }: { title: string; count: number; error: string; loaded: boolean; children: ReactNode }) {
  return <section className="panel blast-panel-fill">
    <div className="panel-title"><span>{title}</span><span>{loaded ? count : "…"}</span></div>
    {error && <div className="settings-error blast-inline-error" role="alert">{error}</div>}
    {children}
  </section>;
}

type NumericField = Exclude<keyof BlastForm, "enabled" | "auto_trade">;

function SettingsTab({ snapshot, onSaved }: { snapshot: BlastSnapshot; onSaved: (next: BlastSnapshot) => void }) {
  const [form, setForm] = useState<BlastForm>(() => toForm(snapshot.settings));
  const [dirty, setDirty] = useState(false);
  const [confirmAuto, setConfirmAuto] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  // Another client's save arrives with the next snapshot; adopt it unless this form has unsaved edits.
  useEffect(() => { if (!dirty) setForm(toForm(snapshot.settings)); }, [snapshot.settings, dirty]);
  const held = snapshot.portfolio.positions.length;

  function setField<K extends keyof BlastForm>(key: K, value: BlastForm[K]) {
    setForm((old) => ({ ...old, [key]: value }));
    setDirty(true);
    setMessage("");
    setError("");
  }
  function reset() {
    setForm(toForm(snapshot.settings));
    setDirty(false);
    setConfirmAuto(false);
    setMessage("");
    setError("");
  }
  async function save() {
    const parsed = fromForm(form);
    if (!parsed.payload) { setError(parsed.error || "Check the highlighted values"); return; }
    const changes = changedSettings(snapshot.settings, parsed.payload);
    if (!Object.keys(changes).length) { setDirty(false); setMessage("Nothing to save."); return; }
    if (held && RISK_KEYS.some((key) => key in changes)) {
      setError(`Close the ${held} open blast position${held === 1 ? "" : "s"} before changing the stop or trail.`);
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${API_URL}/api/blast/settings`, {
        method: "PUT", headers: { "content-type": "application/json", ...authHeaders() }, body: JSON.stringify(changes),
      });
      const body: unknown = await response.json().catch(() => undefined);
      if (!response.ok) throw new Error(detailOf(body) || `Save failed (HTTP ${response.status})`);
      const next = body as BlastSnapshot;
      onSaved(next);
      setForm(toForm(next.settings));
      setDirty(false);
      setConfirmAuto(false);
      setMessage("Saved. The screen uses the new values from the next closed bar.");
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setSaving(false);
    }
  }
  const field = (key: NumericField, label: string, help: string, options: { step?: string; disabled?: boolean } = {}) =>
    <label>{label}
      <input type="number" inputMode="decimal" step={options.step ?? "any"} value={form[key]} disabled={options.disabled}
        onChange={(event) => setField(key, event.target.value)} />
      <p className="field-help">{help}</p>
    </label>;

  return <section className="panel blast-settings">
    <div className="settings-section">
      <h3>Lane</h3>
      <label className="toggle"><input type="checkbox" checked={form.enabled} onChange={(event) => setField("enabled", event.target.checked)} />Screen and journal signal-line crosses</label>
      <p className="field-help">Off stops new candidates only. Open positions keep their stop, trail and expiry exit.</p>
      <label className="toggle"><input type="checkbox" checked={form.auto_trade}
        onChange={(event) => { if (event.target.checked && !form.auto_trade) setConfirmAuto(true); else setField("auto_trade", event.target.checked); }} />
        Buy candidates that pass the screen (paper)</label>
      <p className="field-help">Off is shadow mode: every candidate is still journalled and tracked forward, and nothing is bought.</p>
      {confirmAuto && <div className="blast-warning" role="alert">
        <b>Turn on auto-trade?</b>
        <p>The walk-forward behind this screen established the direction, not the size. On eight unseen sessions it lifted the share of picks that doubled from 7.9% to 17.5%, but the return per pick was +5.7% with a 95% interval of −1.4% to +18.1%, on 57 picks. A few weeks of shadow journal is what would settle it.</p>
        <div className="settings-actions">
          <button className="secondary" onClick={() => setConfirmAuto(false)}>Keep shadow mode</button>
          <button className="primary" onClick={() => { setField("auto_trade", true); setConfirmAuto(false); }}>Turn it on, then save</button>
        </div>
      </div>}
    </div>

    <div className="settings-section">
      <h3>Screen</h3>
      <div className="settings-grid three">
        {field("max_premium_pct", "Premium limit · % of spot", "Premium as a percentage of the underlying's price. The strongest leg.", { step: "0.1" })}
        {field("min_breadth_pct", "Breadth floor · %", "Share of same-side contracts with premium MACD above zero.", { step: "1" })}
        {field("min_off_high_pct", "Below recent high · %", "How far below its own recent high the premium must be.", { step: "1" })}
      </div>
    </div>

    <div className="settings-section">
      <h3>Risk overlay</h3>
      <div className="settings-grid three">
        {field("hard_stop_pct", "Hard stop · %", held ? `Locked while ${held} position${held === 1 ? " is" : "s are"} open.` : "Loss from entry that closes the position.", { step: "1", disabled: held > 0 })}
        {field("trail_activation_pct", "Trail arms at · % gain", held ? "Locked while positions are open." : "Gain from entry at which the trail starts.", { step: "1", disabled: held > 0 })}
        {field("trail_pct", "Trail · % from peak", held ? "Locked while positions are open." : "Floored at the round-trip breakeven once armed.", { step: "1", disabled: held > 0 })}
      </div>
    </div>

    <div className="settings-section">
      <h3>Book</h3>
      <div className="settings-grid three">
        {field("target_notional", "Size per entry · ₹", "Whole lots nearest this notional, capped by cash.", { step: "1000" })}
        {field("max_positions", "Max open positions", "Entries beyond this are journalled as refused.", { step: "1" })}
        {field("initial_capital", "Capital · ₹", "Changing it moves cash by the difference; positions and P&L are kept.", { step: "10000" })}
      </div>
    </div>

    {error && <div className="settings-error" role="alert">{error}</div>}
    {message && <div className="settings-message" role="status">{message}</div>}
    <div className="settings-actions">
      <button className="secondary" onClick={reset} disabled={saving || !dirty}>Discard changes</button>
      <button className="primary" onClick={() => void save()} disabled={saving || !dirty}>{saving ? "Saving…" : "Save lane settings"}</button>
    </div>
  </section>;
}

function detailOf(body: unknown): string {
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => (item as { msg?: string }).msg).filter(Boolean).join("; ");
  return "";
}

function mergeById<Row extends Record<string, unknown>>(stored: Row[], live: Row[], key: keyof Row): Row[] {
  const rows = new Map(stored.map((row) => [String(row[key]), row]));
  live.forEach((row) => rows.set(String(row[key]), row));
  return [...rows.values()];
}
