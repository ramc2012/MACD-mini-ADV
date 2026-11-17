import { useCallback, useEffect, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import { requestJson } from "./requestJson";
import { LaneChartModal, type LaneChartFocus } from "./LaneChart";
import { SellingTab } from "./McxSelling";
import {
  MCX_QUOTES_EVENT, MCX_ROOTS, ROOT_LABEL, ROOT_UNIT, changedSettings, fromForm, macdState, newestMcxQuote, sessionLabel, toForm, verdictMeta,
  type McxDecision, type McxForm, type McxQuote, type McxSnapshot,
} from "./mcxMath";

/** The lane snapshot owns settings, indicators, and the book. The
 * existing terminal stream supplies frequent quotes while this page is open. */
const WATCHLIST = { FUTURES: "FUT", CALLS: "CE", PUTS: "PE" } as const;
type WatchTab = keyof typeof WATCHLIST;
type Tab = WatchTab | "POSITIONS" | "SELLING" | "SIGNALS" | "BOOK" | "RULES" | "SETTINGS";
const TABS: { key: Tab; label: string }[] = [
  { key: "FUTURES", label: "Futures" },
  { key: "CALLS", label: "Calls" },
  { key: "PUTS", label: "Puts" },
  { key: "POSITIONS", label: "Positions" },
  { key: "SELLING", label: "Selling" },
  { key: "SIGNALS", label: "Signals" },
  { key: "BOOK", label: "Orders & trades" },
  { key: "RULES", label: "Rules & data" },
  { key: "SETTINGS", label: "Settings" },
];
const TAB_STORE = "macd.mcxTab";
const POLL_MS = 5_000;
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { /* not kept */ } };
const authHeaders = (): Record<string, string> => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});

const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const currency = (value?: number | null) => (finite(value) ? `₹${number.format(value)}` : "—");
const STAMP = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const stamp = (value?: string | null) => (value ? STAMP.format(new Date(value)) : "—");
const finite = (value?: number | null): value is number => typeof value === "number" && Number.isFinite(value);
const fixed = (value?: number | null, digits = 2) => (finite(value) ? number.format(Number(value.toFixed(digits))) : "—");
const signed = (value?: number | null, digits = 1) => (finite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%` : "—");
const tone = (value?: number | null) => (finite(value) ? (value >= 0 ? "positive" : "negative") : "");
const short = (symbol: string) => symbol.split(":")[1] ?? symbol;
const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : "Request failed");

type BookRow = Record<string, unknown>;
type Book = { orders: BookRow[]; trades: BookRow[] };

export function McxLanePage({ connected }: { connected: boolean }) {
  const [snapshot, setSnapshot] = useState<McxSnapshot>();
  const [focus, setFocus] = useState<{ list: LaneChartFocus[]; index: number }>();
  const closeChart = useCallback(() => setFocus(undefined), []);
  const chartIndex = useCallback((index: number) => setFocus(old => old ? { ...old, index } : undefined), []);
  const openChart = useCallback((list: LaneChartFocus[], index: number) => setFocus({ list, index }), []);
  const [liveQuotes, setLiveQuotes] = useState<Record<string, McxQuote>>({});
  const [clock, setClock] = useState(() => Date.now());
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>(() => {
    const stored = recall(TAB_STORE);
    return TABS.some((row) => row.key === stored) ? stored as Tab : "FUTURES";
  });
  const pickTab = (next: Tab) => { setTab(next); remember(TAB_STORE, next); };

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      setSnapshot(await requestJson<McxSnapshot>(`${API_URL}/api/mcx/snapshot`, { headers: authHeaders(), signal, cache: "no-store" }));
      setError("");
    } catch (reason) {
      if (!signal?.aborted) setError(errorText(reason));
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = window.setInterval(() => void load(controller.signal), POLL_MS);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [load]);
  useEffect(() => {
    const receive = (event: Event) => {
      const rows = (event as CustomEvent<McxQuote[]>).detail;
      if (!Array.isArray(rows)) return;
      setLiveQuotes((old) => {
        const next = { ...old };
        for (const row of rows) next[row.symbol] = newestMcxQuote(next[row.symbol], row) ?? row;
        return next;
      });
    };
    window.addEventListener(MCX_QUOTES_EVENT, receive);
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => { window.removeEventListener(MCX_QUOTES_EVENT, receive); window.clearInterval(timer); };
  }, []);

  if (!snapshot) {
    return <section className="ledger-page blast-page">
      <div className="page-heading"><div><h1>MCX lane</h1><p>Waiting for the lane's first snapshot…</p></div></div>
      <section className="panel blast-empty" role="status">{error || "Loading the MCX lane…"}</section>
    </section>;
  }

  const { health, portfolio } = snapshot;
  const taken = health.decisions.TAKEN ?? 0;
  const quoteFor = (symbol: string) => newestMcxQuote(snapshot.quotes?.[symbol], liveQuotes[symbol]);
  const newestWs = Math.max(0, ...snapshot.contracts.map((row) => {
    const q = liveQuotes[row.symbol];
    return q?.source === "ws" ? q.seen_ms : 0;
  }));
  const live = connected && newestWs > 0 && clock - newestWs < 6_000;
  const watch = tab === "FUTURES" || tab === "CALLS" || tab === "PUTS" ? WATCHLIST[tab] : undefined;
  const counts: Partial<Record<Tab, number>> = {
    FUTURES: snapshot.contracts.filter((row) => row.option_type === "FUT").length,
    CALLS: snapshot.contracts.filter((row) => row.option_type === "CE").length,
    PUTS: snapshot.contracts.filter((row) => row.option_type === "PE").length,
    POSITIONS: portfolio.positions.length,
    SELLING: snapshot.shorts?.open.length ?? 0,
    SIGNALS: snapshot.decisions.length,
  };
  const problems = [
    health.error,
    ...Object.entries(health.selection.errors ?? {}).map(([root, text]) => `${root}: ${text}`),
    ...Object.entries(health.warmup.failed ?? {}).map(([symbol, text]) => `${short(symbol)}: ${text}`),
    ...Object.entries(health.multiplier_warnings ?? {}).map(([root, text]) => `${root}: ${text}`),
    ...Object.entries(health.lane_errors ?? {}).map(([where, row]) => `${where} (${row.count}): ${row.last}`),
    health.rest_fallback.error ? `REST fallback: ${health.rest_fallback.error}` : null,
    health.session.calendar_covers_year ? null : "The MCX holiday list has no entries for this year; add them with mcx_holidays_csv.",
  ].filter(Boolean) as string[];

  return <section className="ledger-page blast-page">
    <div className="page-heading blast-heading">
      <div><h1>MCX lane</h1>
        <p>Premium MACD zero-cross on ATM {[...health.roots, ...(health.mini_roots ?? [])].map((root) => ROOT_LABEL[root] ?? root).join(", ")} options · MACD lane rules · its own paper book</p></div>
      <div className="blast-badges">
        <span className={health.session.open ? "feed-badge" : "mode"}>{sessionLabel(health.session)}</span>
        {health.session.open && <span className={live ? "feed-badge" : "mode"}>{live ? "Live quotes" : "Snapshot quotes"}</span>}
        {!health.enabled && <span className="mode alert" title="No new signals. Open positions keep their stops.">Disabled</span>}
        {health.enabled && (health.auto_trade
          ? <span className="feed-badge" title="Zero-crosses are bought in the lane's paper book.">Auto-trade · paper</span>
          : <span className="mode" title="Every signal is recorded with its verdict. Nothing is bought.">Signals only</span>)}
        <span className="mode" title="This lane can never place a live order.">Paper only</span>
        {problems.length > 0 && <span className="mode alert" title={problems.join("\n")}>{problems.length} issue{problems.length === 1 ? "" : "s"}</span>}
      </div>
    </div>

    <section className="blast-metrics" aria-label="MCX lane book">
      <Metric label="Equity" value={currency(portfolio.equity)} />
      <Metric label="Realized" value={currency(portfolio.realized_pnl)} tone={portfolio.realized_pnl} />
      <Metric label="Unrealized" value={currency(portfolio.unrealized_pnl)} tone={portfolio.unrealized_pnl} />
      <Metric label="Open positions" value={`${portfolio.positions.length} · ${currency(portfolio.market_value)}`} />
      <Metric label="Signals · taken" value={`${health.signals_emitted} · ${taken}`} />
      <Metric label="Feed today" value={`${number.format(health.ws_ticks_today)} ticks${health.rest_fallback.ticks ? ` · ${health.rest_fallback.ticks} REST` : ""}`} />
    </section>

    <div className="watch-tabs blast-tabs mcx-tabs" role="tablist" aria-label="MCX lane sections">
      {TABS.map((row) => <button key={row.key} role="tab" aria-selected={tab === row.key} className={tab === row.key ? "active" : ""} onClick={() => pickTab(row.key)}>
        {row.label}{counts[row.key] !== undefined && <span className="mp-count">{counts[row.key]}</span>}
      </button>)}
    </div>

    <div className="blast-body">
      {error && <div className="settings-error blast-inline-error" role="alert">Refresh failed: {error}. Showing the last snapshot.</div>}
      {watch && <ContractsTab snapshot={snapshot} side={watch} quoteFor={quoteFor} clock={clock} onChart={openChart} />}
      {tab === "POSITIONS" && <PositionsTab snapshot={snapshot} quoteFor={quoteFor} />}
      {tab === "SELLING" && <SellingTab snapshot={snapshot} onSaved={setSnapshot} />}
      {tab === "SIGNALS" && <SignalsTab rows={snapshot.decisions} counts={health.decisions} />}
      {tab === "BOOK" && <BookTab />}
      {tab === "RULES" && <RulesTab snapshot={snapshot} problems={problems} />}
      {tab === "SETTINGS" && <SettingsTab snapshot={snapshot} onSaved={setSnapshot} />}
    </div>
    {focus && <LaneChartModal list={focus.list} index={focus.index} onIndex={chartIndex} onClose={closeChart} />}
  </section>;
}

function Metric({ label, value, tone: value_tone }: { label: string; value: string; tone?: number }) {
  return <div><span>{label}</span><b className={tone(value_tone)}>{value}</b></div>;
}

type QuoteFor = (symbol: string) => McxQuote | undefined;
const movementClass = (quote?: McxQuote) => quote?.move === "up" || quote?.move === "down" ? `mcx-price-${quote.move}` : "";
const withSign = (value?: number | null, digits = 2) => finite(value) ? `${value > 0 ? "+" : ""}${fixed(value, digits)}` : "—";
const ageOf = (stamp: number, clock: number) => Math.max(0, Math.floor((clock - stamp) / 1000));

function ContractsTab({ snapshot, side, quoteFor, clock, onChart }: {
  snapshot: McxSnapshot; side: "FUT" | "CE" | "PE"; quoteFor: QuoteFor; clock: number;
  onChart: (list: LaneChartFocus[], index: number) => void;
}) {
  const { health } = snapshot;
  const rows = snapshot.contracts.filter((row) => row.option_type === side);
  const chartList = rows.map(row => ({
    symbol: row.symbol, title: short(row.symbol), subtitle: `MCX · ${row.display} · ${row.expiry}`,
  }));
  const selected = health.selection;
  const headings = ["Commodity", "Contract", "Strike", "Expiry", "LTP", "Prev close", "Chg", "% chg", "OI chg", "OI", "Volume", "MACD", "Hist.", "Bars", "Lot units", "Held", "Last tick"];
  return <section className="panel blast-panel-fill mcx-contracts">
      <div className="panel-title"><span>{side === "FUT" ? "Futures" : side === "CE" ? "Calls" : "Puts"}</span>
        <span>{selected.selected_at ? `selected ${stamp(selected.selected_at)}${selected.source === "saved" ? " · restored" : ""}` : "not selected yet"}</span></div>
      <div className="ledger-table-wrap"><table className="ledger-table mcx-watchlist">
        <thead><tr>{headings.map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
        <tbody>{rows.length === 0
          ? <tr><td className="empty" colSpan={headings.length}>{health.enabled ? "No contracts in this watchlist." : "The lane is switched off."}</td></tr>
          : rows.map((row, index) => {
            const state = macdState(row);
            const q = quoteFor(row.symbol);
            const future = quoteFor(row.futures_symbol);
            const freshOi = q?.oi_ms && (!health.session.open || clock - q.oi_ms < 5 * 60_000) ? q : undefined;
            const age = q ? ageOf(q.price_ms, clock) : row.ws_age_seconds;
            return <tr key={row.symbol} title={row.symbol} className="mcx-chart-row" onClick={() => onChart(chartList, index)}>
              <td><b>{ROOT_LABEL[row.root] ?? row.root}</b><small className="mcx-unit">{ROOT_UNIT[row.root] ?? ""}</small></td>
              <td><button type="button" className="mcx-chart-link" aria-label={`Open chart for ${short(row.symbol)}`}
                onClick={(event) => { event.stopPropagation(); onChart(chartList, index); }}>{short(row.symbol)}</button>{row.retained ? " · carried" : ""}
                {side !== "FUT" && <small className="mcx-unit">Fut {fixed(future?.ltp ?? row.futures_ltp)} · {short(row.futures_symbol)}</small>}</td>
              <td>{side === "FUT" ? "—" : number.format(row.strike)}</td>
              <td>{row.expiry}{finite(row.days_to_expiry) ? ` · ${row.days_to_expiry}d` : ""}</td>
              <td className={movementClass(q)}>{fixed(q?.ltp ?? row.ltp)}</td>
              <td>{fixed(q?.prev_close)}</td>
              <td className={tone(q?.change)}>{withSign(q?.change)}</td>
              <td className={tone(q?.change_pct)}>{signed(q?.change_pct, 2)}</td>
              <td className={tone(freshOi?.oi_change)} title="Change from previous session open interest">{withSign(freshOi?.oi_change, 0)}</td>
              <td title={freshOi?.oi_ms ? `OI updated ${ageOf(freshOi.oi_ms, clock)}s ago` : "No fresh OI from FYERS"}>{fixed(freshOi?.oi, 0)}</td>
              <td>{fixed(q?.volume, 0)}</td>
              <td className={state.tone}>{finite(row.macd) ? fixed(row.macd, 3) : "—"} <small>{state.label}</small></td>
              <td className={tone(row.histogram)}>{fixed(row.histogram, 3)}</td>
              <td>{row.bars}</td>
              <td>{number.format(row.lot_size)}</td>
              <td>{row.held_lots || "—"}</td>
              <td>{finite(age) ? `${age < 60 ? Math.round(age) + "s" : Math.floor(age / 60) + "m"} ago${q?.source === "rest" ? " · REST" : ""}` : "—"}</td>
            </tr>;
          })}</tbody>
      </table></div>
    </section>;
}

function RulesTab({ snapshot, problems }: { snapshot: McxSnapshot; problems: string[] }) {
  const { health, risk } = snapshot;
  return <div className="mcx-rules-grid">
    <section className="panel mcx-rules-panel">
      <div className="panel-title"><span>Rules</span><span>{risk.inherited_from}</span></div>
      <div className="blast-kv">
        <span>Entry</span><b>{risk.entry_filter}</b>
        <span>MACD</span><b>({risk.macd_periods.join(", ")}) on {health.timeframe_seconds / 60}-min premium bars</b>
        <span>Hard stop</span><b>−{fixed(risk.hard_stop_pct, 1)}%</b>
        <span>Trailing stop</span><b>{fixed(risk.trailing_stop_pct, 1)}% from peak, from +{fixed(risk.trailing_activation_profit_pct, 1)}%</b>
        <span>Scale in · out</span><b>{risk.scale_in_profit_pct.length ? `+${risk.scale_in_profit_pct.join("/")}% · +${risk.scale_out_profit_pct.join("/")}%` : "none"}</b>
        <span>Invalidation exit</span><b>{risk.macd_invalidation_exit ? "on" : "off"}</b>
        <span>Size per entry</span><b>{currency(risk.target_position_notional)}</b>
        <span>Max positions</span><b>{risk.max_positions}</b>
        <span>Session</span><b>{health.session.opens_at}–{health.session.closes_at} IST</b>
        <span>Expiry exit</span><b>{snapshot.settings.expiry_flatten_ist || "off"}</b>
      </div>
      <p className="blast-note">Stops, trail and MACD periods are the MACD lane's own settings; change them there. Lot units turn a premium into rupees: a GOLD premium is per 10 g on a 1 kg lot, so one lot is 100 units.</p>
    </section>
    <section className="panel mcx-rules-panel">
      <div className="panel-title"><span>Warm-up and data</span><span>{health.warmup.state ?? "—"}</span></div>
      <div className="blast-kv">
        <span>Contracts warmed</span><b>{health.warmup.loaded ?? 0} of {health.warmup.symbols ?? 0}{health.warmup.state === "done" ? ` · ${health.warmup.from_store ?? 0} from the database, ${health.warmup.downloaded ?? 0} downloaded${health.warmup.held ? `, ${health.warmup.held} waiting for the download switch` : ""}` : ""}</b>
        <span>Subscribed</span><b>{health.subscribed} symbols{health.ladder ? ` · ${health.ladder} ladder strikes, ${health.futures ?? 0} futures` : ""}</b>
        <span>Bar database</span><b>{health.store?.bars ? `${number.format(health.store.bars)} one-minute bars · ${health.store.contracts ?? 0} contracts · ${health.store.megabytes ?? 0} MB` : "building"}</b>
        <span>Downloaded once</span><b>{health.store?.downloaded_once ?? 0} contracts{health.history?.backfill_running ? " · in progress" : ""}{health.history?.failed_count ? ` · ${health.history.failed_count} failed` : ""}</b>
        <span>New downloads</span><b>{health.history?.downloads_allowed ? "allowed for contracts not yet in the database" : "off · live bars only"}</b>
        <span>REST fallback</span><b>{health.rest_fallback.seconds ? `after ${health.rest_fallback.seconds}s silence · ${health.rest_fallback.polls} polls` : "off"}</b>
      </div>
      {problems.length > 0 && <div className="settings-error mcx-problems" role="status">{problems.map((text) => <div key={text}>{text}</div>)}</div>}
    </section>
  </div>;
}

function PositionsTab({ snapshot, quoteFor }: { snapshot: McxSnapshot; quoteFor: QuoteFor }) {
  const open = snapshot.portfolio.positions;
  const closed = snapshot.portfolio.closed_positions ?? snapshot.closed_positions ?? [];
  return <div className="blast-split mcx-split">
    <section className="panel">
      <div className="panel-title"><span>Open</span><span>{open.length}</span></div>
      <div className="ledger-table-wrap"><table className="ledger-table">
        <thead><tr>{["Contract", "Lots", "Avg", "Last", "P&L", "Return", "Best", "Hard stop", "Trail", "Opened"].map((label) => <th key={label}><button tabIndex={-1}>{label}</button></th>)}</tr></thead>
        <tbody>{open.length === 0 ? <tr><td className="empty" colSpan={10}>No open MCX positions</td></tr> : open.map((row) => <tr key={row.symbol}>
          <td>{short(row.symbol)}</td><td>{row.lots} × {number.format(row.lot_size)}</td><td>{fixed(row.average_price)}</td><td className={movementClass(quoteFor(row.symbol))}>{fixed(quoteFor(row.symbol)?.ltp ?? row.last_price)}</td>
          <td className={tone(row.unrealized_pnl)}>{currency(row.unrealized_pnl)}</td><td className={tone(row.return_pct)}>{signed(row.return_pct)}</td>
          <td>{signed(row.max_return_pct)}</td><td>{fixed(row.hard_stop)}</td><td>{fixed(row.trailing_stop)}</td><td>{stamp(row.opened_at)}</td>
        </tr>)}</tbody>
      </table></div>
    </section>
    <section className="panel">
      <div className="panel-title"><span>Closed</span><span>until 08:00 IST next trading morning</span></div>
      <div className="ledger-table-wrap"><table className="ledger-table">
        <thead><tr>{["Contract", "Qty", "Entry", "Exit", "P&L", "Return", "Reason", "Closed"].map((label) => <th key={label}><button tabIndex={-1}>{label}</button></th>)}</tr></thead>
        <tbody>{closed.length === 0 ? <tr><td className="empty" colSpan={8}>Nothing closed this session</td></tr> : closed.map((row, index) => <tr key={`${row.symbol}-${row.exit_time}-${index}`}>
          <td>{short(row.symbol)}</td><td>{number.format(row.quantity)}</td><td>{fixed(row.entry_price)}</td><td>{fixed(row.exit_price)}</td>
          <td className={tone(row.realized_pnl)}>{currency(row.realized_pnl)}</td><td className={tone(row.return_pct)}>{signed(row.return_pct)}</td>
          <td>{row.exit_reason ?? "—"}</td><td>{stamp(row.exit_time)}</td>
        </tr>)}</tbody>
      </table></div>
    </section>
  </div>;
}

function SignalsTab({ rows, counts }: { rows: McxDecision[]; counts: Record<string, number> }) {
  return <section className="panel blast-panel-fill">
    <div className="panel-title"><span>Signals and verdicts</span>
      <span>{Object.entries(counts).map(([verdict, count]) => `${verdictMeta(verdict).label} ${count}`).join(" · ") || "none since start"}</span></div>
    <p className="blast-note">Every premium MACD zero-cross the lane fires, with what the lane did about it. The newest 60 since the engine started.</p>
    <div className="ledger-table-wrap"><table className="ledger-table">
      <thead><tr>{["Time", "Contract", "Leg", "Premium", "MACD", "Verdict", "Lots", "Detail"].map((label) => <th key={label}><button tabIndex={-1}>{label}</button></th>)}</tr></thead>
      <tbody>{rows.length === 0 ? <tr><td className="empty" colSpan={8}>No signals yet</td></tr> : rows.map((row, index) => <tr key={`${row.at}-${row.symbol}-${index}`}>
        <td>{stamp(row.at)}</td><td>{short(row.symbol)}</td><td>{row.option_type}</td><td>{fixed(row.price)}</td><td>{fixed(row.macd, 3)}</td>
        <td className={row.verdict === "TAKEN" ? "positive" : ""} title={verdictMeta(row.verdict).help}>{verdictMeta(row.verdict).label}</td>
        <td>{row.lots || "—"}</td><td>{row.detail || ""}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

function BookTab() {
  const [book, setBook] = useState<Book>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    requestJson<Book>(`${API_URL}/api/mcx/book`, { headers: authHeaders(), signal: controller.signal })
      .then(setBook).catch((reason) => { if (!controller.signal.aborted) setError(errorText(reason)); });
    return () => controller.abort();
  }, []);
  const cell = (row: BookRow, key: string) => {
    const value = row[key];
    if (key === "symbol" && typeof value === "string") return short(value);
    if ((key === "created_at" || key === "timestamp") && typeof value === "string") return stamp(value);
    if (typeof value === "number") return fixed(value);
    return value === null || value === undefined ? "—" : String(value);
  };
  const table = (rows: BookRow[], keys: string[], labels: string[], empty: string) =>
    <div className="ledger-table-wrap"><table className="ledger-table">
      <thead><tr>{labels.map((label) => <th key={label}><button tabIndex={-1}>{label}</button></th>)}</tr></thead>
      <tbody>{rows.length === 0 ? <tr><td className="empty" colSpan={keys.length}>{empty}</td></tr>
        : rows.map((row, index) => <tr key={index}>{keys.map((key) => <td key={key}>{cell(row, key)}</td>)}</tr>)}</tbody>
    </table></div>;
  if (error) return <div className="settings-error" role="alert">{error}</div>;
  if (!book) return <section className="panel blast-empty" role="status">Loading the book…</section>;
  return <div className="blast-split mcx-split">
    <section className="panel"><div className="panel-title"><span>Orders</span><span>{book.orders.length}</span></div>
      {table(book.orders, ["created_at", "symbol", "side", "lots", "quantity", "status", "fill_price"],
        ["Time", "Contract", "Side", "Lots", "Units", "Status", "Fill"], "No MCX orders yet")}</section>
    <section className="panel"><div className="panel-title"><span>Trades</span><span>{book.trades.length}</span></div>
      {table(book.trades, ["timestamp", "symbol", "side", "lots", "quantity", "price"],
        ["Time", "Contract", "Side", "Lots", "Units", "Price"], "No MCX trades yet")}</section>
  </div>;
}

function SettingsTab({ snapshot, onSaved }: { snapshot: McxSnapshot; onSaved: (next: McxSnapshot) => void }) {
  const [form, setForm] = useState<McxForm>(() => toForm(snapshot.settings));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const held = snapshot.portfolio.positions.length;
  useEffect(() => { if (!dirty) setForm(toForm(snapshot.settings)); }, [snapshot.settings, dirty]);
  const setField = <K extends keyof McxForm>(key: K, value: McxForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setDirty(true); setMessage(""); setError("");
  };
  const toggleRoot = (root: string, on: boolean) =>
    setField("roots", on ? [...new Set([...form.roots, root])] : form.roots.filter((item) => item !== root));

  async function send(path: string, init: RequestInit, done: string) {
    setSaving(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${API_URL}${path}`, init);
      const body: unknown = await response.json().catch(() => undefined);
      if (!response.ok) throw new Error(detailOf(body) || `Request failed (HTTP ${response.status})`);
      const next = body as McxSnapshot;
      onSaved(next);
      setForm(toForm(next.settings));
      setDirty(false);
      setMessage(done);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setSaving(false);
    }
  }
  function save() {
    const parsed = fromForm(form);
    if (!parsed.payload) { setError(parsed.error || "Check the values"); return; }
    const changes = changedSettings(snapshot.settings, parsed.payload);
    if (!Object.keys(changes).length) { setDirty(false); setMessage("Nothing to save."); return; }
    if (held && "initial_capital" in changes) { setError("Close the MCX positions before changing the lane's capital."); return; }
    void send("/api/mcx/settings", { method: "PUT", headers: { "content-type": "application/json", ...authHeaders() }, body: JSON.stringify(changes) },
      "Saved. A change of commodities or expiry rule re-selects contracts; a new bar length re-warms the MACD.");
  }
  const field = (key: "initial_capital" | "target_notional" | "max_entry_lots" | "max_positions" | "timeframe_minutes" | "min_days_to_expiry" | "rest_fallback_seconds",
    label: string, help: string, step = "any", disabled = false) =>
    <label>{label}
      <input type="number" inputMode="decimal" step={step} value={form[key]} disabled={disabled} onChange={(event) => setField(key, event.target.value)} />
      <p className="field-help">{help}</p>
    </label>;

  return <section className="panel blast-settings">
    <div className="settings-section">
      <h3>Lane</h3>
      <label className="toggle"><input type="checkbox" checked={form.enabled} onChange={(event) => setField("enabled", event.target.checked)} />Select MCX contracts and evaluate signals</label>
      <p className="field-help">Off unsubscribes today's contracts. Open positions keep their stops, trail and expiry exit.</p>
      <label className="toggle"><input type="checkbox" checked={form.auto_trade} onChange={(event) => setField("auto_trade", event.target.checked)} />Buy premium zero-crosses (paper)</label>
      <p className="field-help">Off records every signal with its verdict and buys nothing. This lane never places a live order.</p>
      <label className="toggle"><input type="checkbox" checked={form.history_downloads} onChange={(event) => setField("history_downloads", event.target.checked)} />Download history for contracts not yet in the database</label>
      <p className="field-help">The lane keeps its own one-minute bar database. A contract is downloaded once, the first time the lane watches it, and kept up from live ticks after that; a stretch the feed was down is filled the same way. Off: nothing is downloaded while the app's history hold is on, and a new contract has only its live bars.</p>
    </div>

    <div className="settings-section">
      <h3>Commodities</h3>
      <div className="mcx-roots">{MCX_ROOTS.map((root) => <label className="toggle" key={root}>
        <input type="checkbox" checked={form.roots.includes(root)} onChange={(event) => toggleRoot(root, event.target.checked)} />
        {ROOT_LABEL[root]} <small className="mcx-unit">{ROOT_UNIT[root]}</small></label>)}</div>
      <p className="field-help">ATM call and put of each, on the nearest option series at least the minimum days from expiry.</p>
    </div>

    <div className="settings-section">
      <h3>Signals and contracts</h3>
      <div className="settings-grid three">
        {field("timeframe_minutes", "Bar length · minutes", "0 follows the MACD lane's timeframe. Bars are anchored at 09:00 IST.", "1")}
        {field("min_days_to_expiry", "Minimum days to expiry", "1 never selects a series on its expiry day, as on the MACD lane.", "1")}
        <label>Expiry exit · IST
          <input type="text" inputMode="numeric" placeholder="23:00" value={form.expiry_flatten_ist} onChange={(event) => setField("expiry_flatten_ist", event.target.value)} />
          <p className="field-help">A holding expiring today is sold from this time (MCX options devolve into futures). Blank disables.</p>
        </label>
        {field("rest_fallback_seconds", "REST fallback · seconds", "Poll quotes for a contract the websocket has been silent on this long. 0 disables.", "15")}
      </div>
    </div>

    <div className="settings-section">
      <h3>Book</h3>
      <div className="settings-grid three">
        {field("target_notional", "Ticket per position · ₹", form.ticket_ceiling
          ? "Whole lots that fit under this; the position never grows past it. One ATM GOLD lot is often ₹1.5–2.5 lakh, so its mini carries gold."
          : "Whole lots nearest this, never below one lot, plus the MACD lane's adds.", "1000")}
        {field("max_entry_lots", "Max lots per entry", "Ceiling on the notional sizing.", "1")}
        {field("max_positions", "Max open positions", "Across all commodities, both legs and minis. 1 = one trade at a time.", "1")}
        {field("initial_capital", "Capital · ₹", held ? `Locked while ${held} position${held === 1 ? " is" : "s are"} open.` : "The lane's own paper capital.", "10000", held > 0)}
      </div>
      <label className="toggle"><input type="checkbox" checked={form.ticket_ceiling} onChange={(event) => setField("ticket_ceiling", event.target.checked)} />The ticket is a ceiling</label>
      <p className="field-help">On: never more than the ticket in one position, no pyramid adds, and a contract whose one lot costs more is not bought. Off: whole lots nearest the ticket, at least one, with the MACD lane's adds.</p>
      <label className="toggle"><input type="checkbox" checked={form.opposite_cross_exit} onChange={(event) => setField("opposite_cross_exit", event.target.checked)} />An opposite cross closes the trade</label>
      <p className="field-help">A premium zero-cross on the other side of the same commodity (call vs put, full-size or mini) sells the holding; the new signal can then take the slot.</p>
    </div>

    {error && <div className="settings-error" role="alert">{error}</div>}
    {message && <div className="settings-message" role="status">{message}</div>}
    <div className="settings-actions">
      <button className="secondary" disabled={saving} onClick={() => void send("/api/mcx/reselect", { method: "POST", headers: authHeaders() }, "Contracts re-selected.")}>Re-select contracts now</button>
      <button className="secondary" onClick={() => { setForm(toForm(snapshot.settings)); setDirty(false); setError(""); setMessage(""); }} disabled={saving || !dirty}>Discard changes</button>
      <button className="primary" onClick={save} disabled={saving || !dirty}>{saving ? "Saving…" : "Save lane settings"}</button>
    </div>
  </section>;
}

function detailOf(body: unknown): string {
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => (item as { msg?: string }).msg).filter(Boolean).join("; ");
  return "";
}
