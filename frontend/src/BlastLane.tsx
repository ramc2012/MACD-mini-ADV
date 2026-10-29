import { useEffect, useMemo, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import { requestJson } from "./requestJson";
import { SortableTable, TradingLedger, type Column, type FocusRow, type SortState } from "./Ledger";
import { BlastPerformance, BlastStatus } from "./BlastBookViews";
import { istStamp } from "./istTime";
import { visibleClosedRows } from "./ledgerMath";
import {
  RISK_KEYS, VERDICT_ORDER, activeStop, changedSettings, fromForm, istDay, mergeCandidates, screenFunnel, toForm, verdictMeta,
  type BlastForm, type FunnelKey,
} from "./blastMath";
import type {
  BlastBook, BlastCandidate, BlastEntryPolicy, BlastJournalResponse, BlastJournalSummary, BlastSnapshot, BlastVerdictSummary,
  Order, Trade,
} from "./types";

/** The blast lane: a third paper book with its own screen, journal and risk overlay.
 *
 * Everything here reads the lane's own endpoints and its blast_* stream
 * frames. None of it can move the MACD lane's orders, trades or portfolio,
 * which is why App keeps this state apart from theirs.
 */

type Tab = "OVERVIEW" | "JOURNAL" | "POSITIONS" | "STATUS" | "PERFORMANCE" | "ORDERS" | "TRADES" | "SETTINGS";
const TABS: { key: Tab; label: string }[] = [
  { key: "OVERVIEW", label: "Terminal" },
  { key: "POSITIONS", label: "Portfolio" },
  { key: "STATUS", label: "Status" },
  { key: "ORDERS", label: "Orders" },
  { key: "TRADES", label: "Trades" },
  { key: "PERFORMANCE", label: "Performance" },
  { key: "JOURNAL", label: "Journal" },
  { key: "SETTINGS", label: "Settings" },
];
const TAB_STORE = "macd.blastTab";
// Safari's private mode throws on localStorage, and a lost preference must not take the page down.
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { /* preference not kept */ } };
const authHeaders = (): Record<string, string> => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});

const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const currency = (value: number) => `₹${number.format(value)}`;
const stamp = istStamp;
const clock = istStamp;
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
  evaluated: "Entry signals judged",
  judged: "Not held, with spot, breadth and history",
  premium: "Cleared premium vs spot",
  breadth: "Cleared side breadth",
  offHigh: "Cleared distance off high",
  liquid: "Liquid enough to size",
  taken: "Taken",
};

export function BlastLanePage({ snapshot, policy, live, liveOrders, liveTrades, revision, onSnapshot, onOpenPosition, holidays }: {
  snapshot?: BlastSnapshot;
  policy?: BlastEntryPolicy;
  /** Candidates pushed over the stream since the last snapshot. */
  live: BlastCandidate[];
  liveOrders: Order[];
  liveTrades: Trade[];
  /** Bumped on every terminal snapshot; a resync refetches what the stream may have missed. */
  revision: number;
  onSnapshot: (next: BlastSnapshot) => void;
  onOpenPosition?: (position: FocusRow, list: FocusRow[]) => void;
  holidays?: string[];
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
  const wantsBook = ready;
  useEffect(() => {
    if (!wantsBook || !ready) return;
    const controller = new AbortController();
    void requestJson<BlastBook>(`${API_URL}/api/blast/book`, { headers: authHeaders(), signal: controller.signal })
      .then((data) => { setBook(data); setBookError(""); })
      .catch((error) => { if (!controller.signal.aborted) setBookError(errorText(error)); });
    return () => controller.abort();
  }, [wantsBook, ready, revision, liveOrders.length, liveTrades.length, minute]);

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
  const closed = visibleClosedRows(portfolio.closed_positions ?? snapshot.closed_positions ?? [], now, new Set(holidays ?? []));
  const counts: Partial<Record<Tab, number>> = {
    JOURNAL: summary?.evaluated ?? 0,
    POSITIONS: portfolio.positions.length,
    ...(book ? { ORDERS: orders.filter((row) => istDay(row.created_at) === today).length,
      TRADES: trades.filter((row) => istDay(row.timestamp) === today).length } : {}),
  };
  const days = [today, ...(todayData?.days ?? journal?.days ?? []).filter((row) => row !== today)];

  return <section className="ledger-page blast-page">
    <div className="page-heading blast-heading">
      <div><h1>Blast lane</h1>
        <p>{snapshot.settings.strategy === "hma_kama"
          ? "HMA(20) × KAMA(10, 2, 30) crossovers on 30-minute option premium · exit on the cross back, a hard stop or the 4th session · its own paper book"
          : "Premium MACD signal-line crosses, screened by premium ÷ spot, own-side breadth and distance below the recent high · its own paper book"}</p></div>
      <div className="blast-badges">
        {!snapshot.enabled && <span className="mode alert" title="The lane is disabled. Tick-driven stops and marks are paused; use shadow mode to stop entries while retaining risk management.">Disabled · tick exits paused</span>}
        {snapshot.enabled && (snapshot.auto_trade
          ? <span className="feed-badge" title="Candidates that pass the screen are bought in the lane's paper book.">Auto-trade · paper</span>
          : <span className="mode" title="Every candidate is journalled and tracked forward. Nothing is bought.">Shadow · journal only</span>)}
        <span className="mode" title="This lane reads only data the engine already holds. Its broker handle can place paper orders and nothing else.">Broker: orders only</span>
        {snapshot.error && <span className="mode alert" title={snapshot.error}>Lane error</span>}
      </div>
    </div>

    <section className="blast-metrics" aria-label="Blast lane book">
      <Metric label="Equity" value={currency(portfolio.equity)} />
      <Metric label="Realized · active book lifetime" value={currency(portfolio.realized_pnl)} tone={portfolio.realized_pnl} />
      <Metric label="Open P&L · current marks" value={currency(portfolio.unrealized_pnl)} tone={portfolio.unrealized_pnl} />
      <Metric label="Open positions" value={`${portfolio.positions.length} · ${currency(portfolio.market_value)}`} />
      <Metric label="Today · signals / paper entries" value={`${summary?.evaluated ?? 0} / ${summary?.taken ?? 0}`} />
      <Metric label="Tracking forward" value={`${snapshot.health.watching} candidates`} />
    </section>

    <div className="watch-tabs blast-tabs" role="tablist" aria-label="Blast lane sections">
      {TABS.map((row) => <button key={row.key} id={`blast-tab-${row.key}`} type="button" role="tab" aria-controls="blast-tab-panel" aria-selected={tab === row.key} className={tab === row.key ? "active" : ""} onClick={() => pickTab(row.key)}>
        {row.label}{counts[row.key] !== undefined && <span className="mp-count">{counts[row.key]}</span>}
      </button>)}
    </div>

    <div className="blast-body" id="blast-tab-panel" role="tabpanel" aria-labelledby={`blast-tab-${tab}`}>
      {tab === "OVERVIEW" && <Overview snapshot={snapshot} policy={policy} summary={summary} recent={recent} error={todayError} />}
      {tab === "JOURNAL" && <JournalTab rows={journalRows} days={days} day={day} reason={reason} today={today}
        onDay={setDay} onReason={setReason} loading={journalLoading} error={journalError} hmaKama={snapshot.settings.strategy === "hma_kama"} />}
      {bookError && ["POSITIONS", "ORDERS", "TRADES", "STATUS", "PERFORMANCE"].includes(tab) && <div className="settings-error" role="alert">Book refresh failed: {bookError}. Showing the last received records.</div>}
      {(tab === "POSITIONS" || tab === "ORDERS" || tab === "TRADES") && <TradingLedger tab={tab === "POSITIONS" ? "PORTFOLIO" : tab}
        portfolio={portfolio} orders={orders} trades={trades} signals={book?.signals ?? []} onOpenPosition={onOpenPosition} holidays={holidays} ledgerReady={!!book && !bookError} />}
      {tab === "STATUS" && <BlastStatus snapshot={snapshot} orders={orders} trades={trades} ready={!!book && !bookError} />}
      {tab === "PERFORMANCE" && <BlastPerformance book={book} portfolio={portfolio} closed={closed} now={now} initialCapital={snapshot.settings.initial_capital} holidays={holidays} />}
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

function Overview({ snapshot, policy: livePolicy, summary, recent, error }: {
  snapshot: BlastSnapshot; policy?: BlastEntryPolicy; summary?: BlastJournalSummary; recent: BlastCandidate[]; error: string;
}) {
  const [verdictSort, setVerdictSort] = useState<SortState>({ key: "reason", direction: "asc" });
  const [recentSort, setRecentSort] = useState<SortState>({ key: "at", direction: "desc" });
  const settings = snapshot.settings;
  const policy = livePolicy ?? snapshot.screen.vix_policy;
  const normalPremium = policy?.normal_premium_limit_pct ?? settings.max_premium_pct;
  const hardPremium = policy?.hard_premium_limit_pct ?? settings.max_premium_pct;
  const breadthFloor = policy?.min_side_breadth ?? settings.min_breadth;
  const funnel = screenFunnel(summary);
  const cleared = (key: FunnelKey) => funnel.find((step) => step.key === key)?.count ?? 0;
  const hk = settings.strategy === "hma_kama";
  const gates: { key: string; title: string; rule: string; detail: string; count?: number }[] = hk ? [
    { key: "event", title: "Entry", rule: "HMA(20) crosses above KAMA(10, 2, 30)",
      detail: snapshot.strategy_rules?.entry ?? "On a closed 30-minute premium bar. HMA is the fast, low-lag line; KAMA flattens in chop and follows a clean trend." },
    { key: "exit", title: "Exit", rule: `HMA below KAMA · −${trim(settings.hard_stop_pct * 100)}% stop · 4 sessions`,
      detail: snapshot.strategy_rules?.exit ?? "On a closed bar, at the hard stop, or at 15:00 on the 4th session; expiry and stock T-2 exits still apply. No trailing stop." },
    { key: "liquid", title: "Selection liquidity", rule: `Cap ${trim(snapshot.screen.max_volume_share_pct)}% × max(volume, OI / 100)`,
      detail: "Uses selection-time contract data. Known liquidity caps whole lots; missing liquidity leaves this cap unavailable." },
    { key: "evidence", title: "Why this rule", rule: "Sep–Oct 2026 replay, 8,018 contracts",
      detail: "+4.3% / +4.8% a trade (Sep / Oct) after a 1.5% round trip; median day +1.0%; 21 of 39 days positive; worst day −11.3%. Two months: not proven." },
  ] : [
    { key: "event", title: "Entry event", rule: "MACD crosses its signal line",
      detail: snapshot.screen.entry },
    { key: "premium", title: "Premium vs spot", rule: `Normal ≤ ${trim(normalPremium)}% · maximum ${trim(hardPremium)}%`, count: cleared("premium"),
      detail: "Above the normal limit, the ticket shrinks by (normal limit ÷ premium ratio)². A 2× premium receives at most 25% of the usual ticket." },
    { key: "breadth", title: "Own-side breadth", rule: `≥ ${trim(breadthFloor * 100)}% above zero`, count: cleared("breadth"),
      detail: "The floor adjusts with India VIX. The minimum same-side cohort is still required." },
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
      <div className="panel-title"><span>{hk ? "Strategy" : "Screen"}</span><span>{cleared("judged")} judged today</span></div>
      <p className="blast-note">{hk ? "Closed 30-minute premium crossovers. Premium ratio is journal context; the legacy premium, breadth and VIX gates are inactive." : policy?.active
        ? `India VIX ${number.format(policy.vix ?? 0)} · premium and breadth limits adjusted`
        : `VIX ${policy?.vix_status ?? "unavailable"} · fixed premium and breadth limits apply`}</p>
      <div className="blast-gates">{gates.map((gate) => <div className="blast-gate" key={gate.key}>
        <b>{gate.title}</b><code>{gate.rule}</code>
        <small>{gate.detail}{gate.count !== undefined ? ` Cleared today: ${gate.count}.` : ""}</small>
      </div>)}</div>
    </section>

    <section className="panel blast-funnel">
      <div className="panel-title"><span>Today's funnel</span><span>{summary?.evaluated ?? 0} signals</span></div>
      <div className="blast-steps">{(hk ? [
        { key: "evaluated", label: "HMA/KAMA entry signals", count: summary?.evaluated ?? 0 },
        { key: "taken", label: "Paper entries filled", count: summary?.taken ?? 0 },
        { key: "rejected", label: "Paper entries refused", count: summary?.verdicts.find((row) => row.reason === "ENTRY_REJECTED")?.count ?? 0 },
        { key: "shadow", label: "Journal only", count: summary?.verdicts.find((row) => row.reason === "AUTO_TRADE_OFF")?.count ?? 0 },
      ].map((step) => ({ ...step, share: summary?.evaluated ? step.count / summary.evaluated : 0 }))
        : funnel.map((step) => ({ ...step, label: FUNNEL_LABEL[step.key] }))).map((step) => <div key={step.key} className={`blast-step${step.key === "taken" ? " final" : ""}`}>
        <span>{step.label}</span><b>{step.count}</b>
        <div className="blast-meter" aria-hidden="true"><i style={{ width: `${Math.round(step.share * 100)}%` }} /></div>
      </div>)}</div>
      {error && <p className="blast-note negative" role="status">{error}</p>}
    </section>

    <section className="panel blast-verdicts">
      <div className="panel-title"><span>Verdicts and forward excursion</span><span>control group</span></div>
      <p className="blast-note">{hk ? "Entry signals" : "Candidates that clear the premium gate"} are tracked for {trim(snapshot.screen.journal_horizon_hours)} hours. Max gain and drawdown are observed premium excursions before costs; they are not booked trade returns. Open tracking windows are incomplete.</p>
      <SortableTable rows={verdictRows} columns={verdictColumns} sort={verdictSort}
        onSort={(key) => setVerdictSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
        empty="No candidates judged today yet" />
    </section>

    <section className="panel blast-recent">
      <div className="panel-title"><span>Latest candidates</span><span>{recent.length}</span></div>
      <SortableTable rows={recent} columns={recentColumns} sort={recentSort}
        onSort={(key) => setRecentSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
        empty={hk ? "No HMA/KAMA crossovers journalled today" : "No signal-line crosses judged yet today"} />
    </section>
  </div>;
}

function JournalTab({ rows, days, day, reason, today, onDay, onReason, loading, error, hmaKama }: {
  rows: BlastCandidate[]; days: string[]; day: string; reason: string; today: string;
  onDay: (day: string) => void; onReason: (reason: string) => void; loading: boolean; error: string;
  hmaKama: boolean;
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
    { key: "vix", label: "VIX / floor", value: (row) => row.entry_policy?.vix ?? LOW,
      render: (row) => row.entry_policy ? `${finite(row.entry_policy.vix) ? number.format(row.entry_policy.vix) : "—"} / ${pct(row.entry_policy.min_side_breadth * 100, 0)}` : "—" },
    { key: "ticket", label: "Ticket", value: (row) => row.entry_size_multiplier ?? row.premium_size_multiplier ?? LOW,
      render: (row) => { const scale = row.entry_size_multiplier ?? row.premium_size_multiplier;
        return <span title={row.entry_rejection ?? "Share of the usual entry ticket"}>{finite(scale) ? pct(scale * 100, 0) : "—"}</span>; } },
    { key: "off_high", label: "Off high", value: (row) => row.off_high_pct ?? LOW, render: (row) => signed(row.off_high_pct) },
    { key: "macd", label: "Entry lines", value: (row) => row.strategy === "hma_kama" ? row.hma ?? LOW : row.macd,
      render: (row) => row.strategy === "hma_kama"
        ? `HMA ${finite(row.hma) ? row.hma.toFixed(3) : "—"} / KAMA ${finite(row.kama) ? row.kama.toFixed(3) : "—"}`
        : `MACD ${row.macd.toFixed(3)} / signal ${row.signal.toFixed(3)}` },
    { key: "entry_note", label: "Entry note", value: (row) => row.entry_rejection ?? "", render: (row) => row.entry_rejection ?? "—" },
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
    <SortableTable rows={rows} columns={hmaKama && rows.every((row) => row.strategy === "hma_kama")
      ? columns.filter((column) => !["breadth", "vix", "off_high", "bars"].includes(column.key)) : columns} sort={sort}
      onSort={(key) => setSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
      empty={reason ? `No "${verdictMeta(reason).label}" candidates for this selection` : "No candidates journalled for this selection"} />
  </section>;
}

type NumericField = Exclude<keyof BlastForm, "enabled" | "auto_trade" | "vix_adaptive_enabled">;

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
      <label>Strategy
        <select value={form.strategy} onChange={(event) => setField("strategy", event.target.value as BlastForm["strategy"])}>
          <option value="hma_kama">HMA(20) × KAMA crossover · 30-minute premium (since 28 Oct 2026)</option>
          <option value="macd_screen">Original screen · MACD signal-line cross, premium ÷ spot, breadth, off-high, trail</option>
        </select>
        <p className="field-help">Applies from the next closed bar. Open positions move to the chosen strategy's exits.</p>
      </label>
      <label className="toggle"><input type="checkbox" checked={form.enabled} onChange={(event) => setField("enabled", event.target.checked)} />Judge and journal entry signals</label>
      <p className="field-help">Disabling the lane also pauses tick-driven stops and marks. To stop new entries while retaining risk management, keep the lane enabled and turn off paper buying below.</p>
      <label className="toggle"><input type="checkbox" checked={form.auto_trade}
        onChange={(event) => { if (event.target.checked && !form.auto_trade) setConfirmAuto(true); else setField("auto_trade", event.target.checked); }} />
        Buy candidates that pass the screen (paper)</label>
      <p className="field-help">Off is shadow mode: every candidate is still journalled and tracked forward, and nothing is bought.</p>
      {confirmAuto && <div className="blast-warning" role="alert">
        <b>Turn on auto-trade?</b>
        <p>{form.strategy === "hma_kama"
          ? "HMA/KAMA replay results are research evidence. Prospective performance and executable costs remain unproven. This enables entries in the independent paper book using its current funding and risk settings."
          : "The legacy screen's short walk-forward sample does not establish profitability or position size. This enables entries in the independent paper book using its current funding and risk settings."}</p>
        <div className="settings-actions">
          <button className="secondary" onClick={() => setConfirmAuto(false)}>Keep shadow mode</button>
          <button className="primary" onClick={() => { setField("auto_trade", true); setConfirmAuto(false); }}>Turn it on, then save</button>
        </div>
      </div>}
    </div>

    {form.strategy !== "hma_kama" && <><div className="settings-section">
      <h3>Screen</h3>
      <div className="settings-grid three">
        {field("max_premium_pct", "Base premium limit · % of spot", "Normal premium limit at the reference VIX.", { step: "0.1" })}
        {field("min_breadth_pct", "Base breadth floor · %", "Same-side breadth required at the reference VIX.", { step: "1" })}
        {field("min_off_high_pct", "Below recent high · %", "How far below its own recent high the premium must be.", { step: "1" })}
      </div>
    </div>

    <div className="settings-section">
      <h3>VIX and rich-premium sizing</h3>
      <label className="toggle"><input type="checkbox" checked={form.vix_adaptive_enabled}
        onChange={(event) => setField("vix_adaptive_enabled", event.target.checked)} />Adjust premium and breadth limits with India VIX</label>
      <div className="settings-grid three">
        {field("vix_reference", "Reference VIX", "Base rules apply at this VIX. The VIX ratio is bounded from 0.5× to 2×.", { step: "1" })}
        {field("vix_breadth_adjustment_pct", "Breadth adjustment · percentage points", "Change when VIX doubles. A negative value lowers the required breadth.", { step: "1" })}
        {field("rich_premium_max_multiple", "Maximum rich premium · × normal limit", "Richer premiums receive smaller tickets; 2× premium means at most 25% of the usual ticket.", { step: "0.1" })}
        {field("vix_max_age_seconds", "Maximum VIX quote age · seconds", "Missing or older VIX quotes keep the fixed rules.", { step: "30" })}
      </div>
    </div></>}

    <div className="settings-section">
      <h3>Risk overlay</h3>
      {form.strategy === "hma_kama" && <p className="field-help">HMA/KAMA uses the hard stop, cross-back exit and session cap. Trailing-stop settings are inactive.</p>}
      <div className="settings-grid three">
        {field("hard_stop_pct", "Hard stop · %", held ? `Locked while ${held} position${held === 1 ? " is" : "s are"} open.` : "Loss from entry that closes the position.", { step: "1", disabled: held > 0 })}
        {field("trail_activation_pct", "Trail arms at · % gain", held ? "Locked while positions are open." : "Gain from entry at which the trail starts.", { step: "1", disabled: held > 0 || form.strategy === "hma_kama" })}
        {field("trail_pct", "Trail · % from peak", held ? "Locked while positions are open." : "Floored at the round-trip breakeven once armed.", { step: "1", disabled: held > 0 || form.strategy === "hma_kama" })}
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
