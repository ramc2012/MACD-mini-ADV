import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { ClosedBook, ClosedPosition, Excursion, Order, Portfolio, Position, Signal, Trade } from "./types";
import { BOOK_ROLL_LABEL, closedBookStart, completedRoundTrips, distinctPositions, indexUnderlying, INDEX_UNDERLYINGS, observedExcursion, optionSide, roundStats, visibleClosedRows, type RoundStats, type RoundTrip } from "./ledgerMath";

import { bookFillCounts } from "./ledgerMath";
import { LedgerCounts } from "./LedgerCounts";
import { attempts, rejectionsByReason } from "./ledgerCountState";
import { istDay, istStamp } from "./istTime";
export { istDay } from "./istTime";

const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const currency = (value: number) => `₹${number.format(value)}`;
const fullStamp = istStamp;
const entryStamp = istStamp;
// "08:00 IST" while the book started today; "Fri 08:00 IST" over a weekend.
const bookStartLabel = (start: number, now: number) => {
  const sameDay = new Date(start).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" }) === new Date(now).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  return `${sameDay ? "" : `${new Date(start).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "short" })} `}${BOOK_ROLL_LABEL}`;
};
const pct = (value?: number | null) => value === undefined || value === null ? "—" : `${value.toFixed(2)}%`;
const priceTitle = (price?: number | null) => price === undefined || price === null || !(price > 0) ? undefined : currency(price);
const lotsLabel = (row: { lots: number; lot_size: number }) => row.lot_size > 0 ? `${row.lots} × ${row.lot_size}` : "—";
const heldLabel = (ms: number) => { const m = Math.round(ms / 60_000); return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`; };
/** The IST calendar day (YYYY-MM-DD) of a timestamp. */
const dayHeading = (day: string) => new Date(`${day}T12:00:00+05:30`).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "short", day: "2-digit", month: "short", year: "numeric" });

export type LedgerTab = "ORDERS" | "TRADES" | "PORTFOLIO" | "STATISTICS" | "SIGNALS";
type SortValue = string | number;
export type SortState = { key: string; direction: "asc" | "desc" };
export type Column<Row> = {
  key: string;
  label: string;
  value: (row: Row) => SortValue;
  render?: (row: Row) => ReactNode;
  tone?: (row: Row) => "positive" | "negative" | "";
};
export type FocusRow = { symbol: string; entryTime: string; entryPrice: number };
type OpenPosition = Position & Excursion;
type BookKey = "CE" | "PE" | "OTHER" | "LONG" | "SHORT" | "CLOSED";
/** "options": the option lanes' CE / PE books. "sides": a book that sells short (spot lane futures): Long / Short. */
export type LedgerBooks = "options" | "sides";

const initialSorts: Record<LedgerTab, SortState> = {
  ORDERS: { key: "time", direction: "desc" },
  TRADES: { key: "time", direction: "desc" },
  PORTFOLIO: { key: "pnl", direction: "desc" },
  STATISTICS: { key: "value", direction: "desc" },
  SIGNALS: { key: "time", direction: "desc" },
};

export function TradingLedger({ tab, orders, trades, portfolio, signals, onOpenPosition, holidays, ledgerReady = true, books = "options", marginRate }: {
  tab: LedgerTab;
  books?: LedgerBooks;
  /** Futures: the share of notional a position blocks as margin. The books then show margin blocked and notional,
   *  not money invested, and P&L % is on margin. */
  marginRate?: number;
  ledgerReady?: boolean;
  /** Exchange holidays (YYYY-MM-DD) so the closed book rolls on trading days, not weekdays. */
  holidays?: string[];
  orders: Order[];
  trades: Trade[];
  portfolio: Portfolio & ClosedBook;
  signals: Signal[];
  onOpenPosition?: (position: FocusRow, list: FocusRow[]) => void;
}) {
  const [sorts, setSorts] = useState(initialSorts);
  const sort = sorts[tab];
  const updateSort = (key: string) => setSorts((old) => ({
    ...old,
    [tab]: { key, direction: old[tab].key === key && old[tab].direction === "desc" ? "asc" : "desc" },
  }));
  // The closed book's keys differ from the open book's, so a shared PORTFOLIO
  // sort would silently fall back to the first column on every tab switch.
  const [closedSort, setClosedSort] = useState<SortState>({ key: "closed", direction: "desc" });
  const updateClosedSort = (key: string) => setClosedSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }));
  const [positionBook, setPositionBook] = useState<BookKey>("CE");
  // Refreshed by the minute so rows fall off at 08:00 IST without a reload.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const rounds = useMemo(() => completedRoundTrips(trades, { shorts: books === "sides" }), [trades, books]);
  // Orders and trades open on the current IST day; the full log is one click away.
  const [allDays, setAllDays] = useState(false);
  const today = istDay(now);
  const todayOrders = useMemo(() => orders.filter((row) => istDay(row.created_at) === today), [orders, today]);
  const todayTrades = useMemo(() => trades.filter((row) => istDay(row.timestamp) === today), [trades, today]);
  const shownOrders = allDays ? orders : todayOrders;
  const shownTrades = allDays ? trades : todayTrades;
  const holidaySet = useMemo(() => new Set(holidays ?? []), [holidays]);
  // Backend records only: a trade-log reconstruction would carry a second P&L
  // definition (no fees, one row per fill, no excursion) beside the real one.
  // Each row's visible_until decides its life, so a Friday exit survives the
  // weekend exactly as long as the backend keeps it in the frame.
  const closedToday = useMemo(() => visibleClosedRows(portfolio.closed_positions ?? [], now, holidaySet), [portfolio.closed_positions, now, holidaySet]);
  const closedPositionCount = useMemo(() => distinctPositions(closedToday), [closedToday]);
  const bookFills = useMemo(() => bookFillCounts(trades, now, holidaySet), [trades, now, holidaySet]);
  const bookSince = bookStartLabel(closedBookStart(now, holidaySet), now);

  const orderColumns: Column<Order>[] = [
    { key: "time", label: "Time (IST)", value: (row) => Date.parse(row.created_at), render: (row) => fullStamp(row.created_at) },
    { key: "symbol", label: "Symbol", value: (row) => row.symbol },
    { key: "side", label: "Side", value: (row) => row.side, tone: (row) => row.side === "BUY" ? "positive" : "negative" },
    { key: "lots", label: "Lots", value: (row) => row.lots || 0, render: (row) => row.lots || "—" },
    { key: "quantity", label: "Units", value: (row) => row.quantity, render: (row) => `${row.quantity} (${row.lot_size || 1}/lot)` },
    { key: "fill", label: "Fill", value: (row) => row.fill_price || 0, render: (row) => row.fill_price ? currency(row.fill_price) : "—" },
    { key: "status", label: "Status", value: (row) => row.status, render: (row) => (row.repeat_count ?? 1) > 1
      ? <span title={`${row.repeat_count} attempts from ${fullStamp(row.first_at || row.created_at)}${row.reject_reasons?.length ? ` · ${row.reject_reasons.join(" | ")}` : ""}`}>{row.status} ×{row.repeat_count?.toLocaleString("en-IN")}</span>
      : <span title={row.reject_reason || undefined}>{row.status}</span> },
  ];
  const tradeColumns: Column<Trade>[] = [
    { key: "time", label: "Time (IST)", value: (row) => Date.parse(row.timestamp), render: (row) => fullStamp(row.timestamp) },
    { key: "symbol", label: "Symbol", value: (row) => row.symbol },
    { key: "side", label: "Side", value: (row) => row.side, tone: (row) => row.side === "BUY" ? "positive" : "negative" },
    { key: "lots", label: "Lots", value: (row) => row.lots || 0, render: (row) => row.lots || "—" },
    { key: "quantity", label: "Units", value: (row) => row.quantity, render: (row) => `${row.quantity} (${row.lot_size || 1}/lot)` },
    { key: "price", label: "Fill price", value: (row) => row.price, render: (row) => currency(row.price) },
    { key: "value", label: "Trade value", value: (row) => row.price * row.quantity, render: (row) => currency(row.price * row.quantity) },
  ];
  // Max/Min return share Change %'s base (average_price); a pyramided position
  // can therefore show a lower max than a minute earlier. The ₹ price rides in
  // the title so the column stays one number wide.
  // An unobserved path (zero or null price) renders "—" and sorts to the bottom.
  const maxReturn = (row: Excursion) => observedExcursion(row.max_price, row.max_return_pct);
  const minReturn = (row: Excursion) => observedExcursion(row.min_price, row.min_return_pct);
  const excursionColumns: Column<Excursion>[] = [
    { key: "maxReturn", label: "Max return %", value: (row) => maxReturn(row) ?? Number.NEGATIVE_INFINITY, render: (row) => <span title={priceTitle(row.max_price)}>{pct(maxReturn(row))}</span>, tone: (row) => (maxReturn(row) ?? 0) > 0 ? "positive" : "" },
    { key: "minReturn", label: "Min return %", value: (row) => minReturn(row) ?? Number.POSITIVE_INFINITY, render: (row) => <span title={priceTitle(row.min_price)}>{pct(minReturn(row))}</span>, tone: (row) => (minReturn(row) ?? 0) < 0 ? "negative" : "" },
  ];
  const positionColumns: Column<OpenPosition>[] = [
    { key: "symbol", label: "Symbol", value: (row) => row.symbol },
    { key: "opened", label: "Entry time (IST)", value: (row) => Date.parse(row.opened_at), render: (row) => entryStamp(row.opened_at) },
    { key: "lots", label: "Lots", value: (row) => row.lots, render: lotsLabel },
    { key: "quantity", label: "Units", value: (row) => row.quantity },
    { key: "average", label: "Average", value: (row) => row.average_price, render: (row) => currency(row.average_price) },
    { key: "ltp", label: "LTP", value: (row) => row.last_price, render: (row) => currency(row.last_price) },
    { key: "change", label: "Change", value: (row) => row.last_price - row.average_price, render: (row) => currency(row.last_price - row.average_price), tone: positionTone },
    { key: "changePct", label: "Change %", value: positionChangePct, render: (row) => `${positionChangePct(row).toFixed(2)}%`, tone: positionTone },
    ...excursionColumns,
    { key: "pnl", label: "Profit / Loss", value: (row) => row.unrealized_pnl, render: (row) => currency(row.unrealized_pnl), tone: positionTone },
  ];
  // Realized P&L is net of the slice's fees; the gross figure rides in the title.
  const closedColumns: Column<ClosedPosition>[] = [
    { key: "symbol", label: "Symbol", value: (row) => row.symbol, render: (row) => row.partial ? <span title={`Partial exit · ${row.remaining_quantity} units still open`}>{row.symbol} ·</span> : row.symbol },
    { key: "opened", label: "Entry time (IST)", value: (row) => Date.parse(row.entry_time), render: (row) => entryStamp(row.entry_time) },
    { key: "closed", label: "Exit time (IST)", value: (row) => Date.parse(row.exit_time), render: (row) => entryStamp(row.exit_time) },
    { key: "held", label: "Held", value: (row) => Date.parse(row.exit_time) - Date.parse(row.entry_time), render: (row) => heldLabel(Date.parse(row.exit_time) - Date.parse(row.entry_time)) },
    { key: "lots", label: "Lots", value: (row) => row.lots, render: lotsLabel },
    { key: "quantity", label: "Units", value: (row) => row.quantity },
    { key: "entry", label: "Entry", value: (row) => row.entry_price, render: (row) => currency(row.entry_price) },
    { key: "exit", label: "Exit", value: (row) => row.exit_price, render: (row) => currency(row.exit_price) },
    { key: "fees", label: "Fees", value: (row) => row.fees ?? 0, render: (row) => row.fees === undefined || row.fees === null ? "—" : currency(row.fees) },
    { key: "pnl", label: "Realized P&L", value: (row) => row.realized_pnl, render: (row) => <span title={row.gross_pnl === undefined || row.gross_pnl === null ? undefined : `Gross ${currency(row.gross_pnl)}`}>{currency(row.realized_pnl)}</span>, tone: closedTone },
    { key: "return", label: "Return %", value: (row) => row.return_pct, render: (row) => `${row.return_pct.toFixed(2)}%`, tone: closedTone },
    ...excursionColumns,
    { key: "reason", label: "Exit reason", value: (row) => row.exit_reason || "", render: (row) => row.exit_reason || "—" },
  ];
  const signalColumns: Column<Signal>[] = [
    { key: "time", label: "Time (IST)", value: (row) => Date.parse(row.timestamp as unknown as string), render: (row) => fullStamp(row.timestamp as unknown as string) },
    { key: "symbol", label: "Symbol", value: (row) => row.symbol },
    { key: "side", label: "Side", value: (row) => row.side, tone: (row) => row.side === "BUY" ? "positive" : "negative" },
    { key: "trigger", label: "Trigger", value: (row) => row.kind },
    { key: "price", label: "Premium", value: (row) => row.price, render: (row) => currency(row.price) },
    { key: "macd", label: "MACD", value: (row) => row.macd, render: (row) => number.format(row.macd) },
  ];
  const capital = (notional: number) => marginRate ? notional * marginRate : notional;
  const labels: CapitalLabels = marginRate ? { capital: "Margin", current: "Notional", proceeds: "Exit notional" } : DEFAULT_LABELS;
  const liveSummary = useMemo(() => positionSummary(portfolio.positions.map((row) => ({
    invested: capital(row.average_price * Math.abs(row.quantity)), current: row.last_price * Math.abs(row.quantity), pnl: row.unrealized_pnl, symbol: row.symbol,
  }))), [portfolio.positions, marginRate]);
  const closedSummary = useMemo(() => positionSummary(closedToday.map((row) => ({
    invested: capital(row.entry_price * row.quantity), current: row.exit_price * row.quantity, pnl: row.realized_pnl, symbol: row.symbol,
  })), closedPositionCount), [closedToday, closedPositionCount, marginRate]);
  const positionGroups = useMemo(() => {
    const groups: { key: "CE" | "PE" | "OTHER" | "LONG" | "SHORT"; title: string; positions: Position[] }[] = books === "sides" ? [
      { key: "LONG", title: "Long positions", positions: [] },
      { key: "SHORT", title: "Short positions", positions: [] },
    ] : [
      { key: "CE", title: "Call positions · CE", positions: [] },
      { key: "PE", title: "Put positions · PE", positions: [] },
      { key: "OTHER", title: "Other positions", positions: [] },
    ];
    portfolio.positions.forEach((position) => {
      const key = books === "sides" ? (position.quantity < 0 ? "SHORT" : "LONG") : positionOptionType(position.symbol);
      groups.find((group) => group.key === key)?.positions.push(position);
    });
    return groups.filter((group) => group.key !== "OTHER" || group.positions.length > 0).map((group) => ({
      ...group,
      summary: positionSummary(group.positions.map((row) => ({
        invested: capital(row.average_price * Math.abs(row.quantity)),
        current: row.last_price * Math.abs(row.quantity),
        pnl: row.unrealized_pnl,
        symbol: row.symbol,
      }))),
    }));
  }, [portfolio.positions, books, marginRate]);

  const bookTabsAvailable: { key: BookKey; label: string; count: number }[] = [
    ...positionGroups.map((group) => ({ key: group.key, label: BOOK_LABELS[group.key], count: group.positions.length })),
    { key: "CLOSED", label: "Closed today", count: closedPositionCount },
  ];
  // OTHER disappears when its last position is flattened; fall back rather than show a blank panel.
  const activeBook = bookTabsAvailable.some((row) => row.key === positionBook) ? positionBook : bookTabsAvailable[0].key;
  const activeGroup = positionGroups.find((group) => group.key === activeBook);
  // Open rows carry opened_at/average_price; closed slices carry the backend's entry_time/entry_price.
  const toFocus = (row: { symbol: string; opened_at?: string; average_price?: number; entry_time?: string; entry_price?: number }): FocusRow =>
    ({ symbol: row.symbol, entryTime: row.entry_time ?? row.opened_at ?? "", entryPrice: row.entry_price ?? row.average_price ?? 0 });
  const positionTabs = <div className="watch-tabs book-tabs position-tabs">{bookTabsAvailable.map((row) => <button key={row.key} className={`${activeBook === row.key ? "active" : ""}${row.key === "CLOSED" ? " closed" : ""}`} onClick={() => setPositionBook(row.key)} title={row.key === "CLOSED" ? `Since ${bookSince}: buy/sell counts are filled trades; partial exits count separately from closed positions.` : undefined}>{row.label}<span>{row.count}</span>{row.key === "CLOSED" && <><span className="book-side-count buy">Buy {ledgerReady ? bookFills.buys : "…"}</span><span className="book-side-count sell">Sell {ledgerReady ? bookFills.sells : "…"}</span></>}</button>)}</div>;

  const daily = tab === "ORDERS" || tab === "TRADES";
  const dayTotal = tab === "ORDERS" ? todayOrders.length : todayTrades.length;
  const allTotal = tab === "ORDERS" ? orders.length : trades.length;
  // The portfolio needs no page heading: its CE / PE / Closed tabs sit directly under the Portfolio view tab.
  return <section className={tab === "PORTFOLIO" ? "ledger-page portfolio-page" : "ledger-page"}>
    {tab !== "PORTFOLIO" && <div className="page-heading"><div><h1>{tab === "STATISTICS" ? "Trade statistics" : titleCase(tab)}{daily && !allDays && <small className="ledger-day"> · {dayHeading(today)}</small>}</h1><p>{daily && !allDays ? `${tab === "ORDERS" ? "Order attempts" : "Paper fills"} placed today (IST). ${allTotal.toLocaleString("en-IN")} in the full log.` : pageDescription(tab)}</p></div>
      {daily ? <span className="ledger-day-toggle"><b>{(allDays ? allTotal : dayTotal).toLocaleString("en-IN")}</b> {allDays ? "records" : "today"}<button type="button" onClick={() => setAllDays((v) => !v)} aria-pressed={allDays}>{allDays ? "Show today" : "Show all days"}</button></span> : <span>{tabCount(tab, orders, trades, portfolio, signals, rounds)} records</span>}</div>}
    {daily && <LedgerCounts orders={shownOrders} trades={shownTrades} ready={ledgerReady} />}
    <div className="full-ledger panel">
      {tab === "ORDERS" && <SortableTable rows={shownOrders} columns={orderColumns} sort={sort} onSort={updateSort} empty={allDays ? "No paper orders recorded yet" : `No orders today (${dayHeading(today)}). Show all days for the full log.`} />}
      {tab === "TRADES" && <SortableTable rows={shownTrades} columns={tradeColumns} sort={sort} onSort={updateSort} empty={allDays ? "No paper fills recorded yet" : `No fills today (${dayHeading(today)}). Show all days for the full log.`} />}
      {tab === "PORTFOLIO" && <div className="book-stack">{positionTabs}<div className="book-fill">
        {activeBook === "CLOSED"
          ? <PositionBook title={`Closed today · since ${bookSince}`} closed rows={closedToday} empty={`No positions closed since ${bookSince}`} summary={closedSummary} labels={labels} columns={closedColumns} sort={closedSort} onSort={updateClosedSort} toFocus={toFocus} onOpenPosition={onOpenPosition} />
          : activeGroup && <PositionBook title={activeGroup.title} rows={activeGroup.positions} summary={activeGroup.summary} labels={labels} columns={positionColumns} sort={sort} onSort={updateSort} toFocus={toFocus} onOpenPosition={onOpenPosition} />}
      </div><SummaryStrip title="Combined open-position summary" summary={liveSummary} labels={labels} /></div>}
      {tab === "SIGNALS" && <SortableTable rows={signals} columns={signalColumns} sort={sort} onSort={updateSort} empty="No MACD signals recorded" />}
      {tab === "STATISTICS" && <Statistics orders={orders} trades={trades} portfolio={portfolio} rounds={rounds} sides={books === "sides"} />}
    </div>
  </section>;
}

type BookSummary = { count: number; rows: number; invested: number; current: number; pnl: number; returnPct: number; winners: number; losers: number; best?: string; worst?: string };

// `count` defaults to one per row; the closed book passes its distinct
// position count so a staged exit's slices are not read as three positions.
function positionSummary(rows: { invested: number; current: number; pnl: number; symbol: string }[], count = rows.length): BookSummary {
  const invested = rows.reduce((sum, row) => sum + row.invested, 0);
  const current = rows.reduce((sum, row) => sum + row.current, 0);
  const pnl = rows.reduce((sum, row) => sum + row.pnl, 0);
  const sorted = [...rows].sort((a, b) => b.pnl - a.pnl);
  return {
    count, invested, current, pnl, rows: rows.length,
    returnPct: invested ? (pnl / invested) * 100 : 0,
    winners: rows.filter((row) => row.pnl > 0).length,
    losers: rows.filter((row) => row.pnl < 0).length,
    best: sorted[0]?.symbol.split(":")[1], worst: sorted.length > 1 ? sorted[sorted.length - 1].symbol.split(":")[1] : undefined,
  };
}

type CapitalLabels = { capital: string; current: string; proceeds: string };
const DEFAULT_LABELS: CapitalLabels = { capital: "Invested", current: "Current value", proceeds: "Proceeds" };

function SummaryStrip({ title, summary, labels = DEFAULT_LABELS }: { title: string; summary: BookSummary; labels?: CapitalLabels }) {
  return <div className="summary-strip">
    <b className="summary-title">{title}</b>
    <div><span>Positions</span><b>{summary.count} · {summary.winners}▲ {summary.losers}▼</b></div>
    <div><span>{labels.capital}</span><b>{currency(summary.invested)}</b></div>
    <div><span>{labels.current}</span><b>{currency(summary.current)}</b></div>
    <div><span>Unrealized P&L</span><b className={summary.pnl >= 0 ? "positive" : "negative"}>{currency(summary.pnl)} ({summary.returnPct.toFixed(2)}%)</b></div>
    <div><span>Best / worst</span><b>{summary.best || "—"} / {summary.worst || "—"}</b></div>
  </div>;
}

function PositionBook<Row>({ title, rows, summary, labels = DEFAULT_LABELS, columns, sort, onSort, toFocus, onOpenPosition, closed, empty }: {
  title: string;
  rows: Row[];
  empty?: string;
  summary: BookSummary;
  labels?: CapitalLabels;
  columns: Column<Row>[];
  sort: SortState;
  onSort: (key: string) => void;
  toFocus: (row: Row) => FocusRow;
  onOpenPosition?: (position: FocusRow, list: FocusRow[]) => void;
  closed?: boolean;
}) {
  const list = rows.map(toFocus);
  return <section className="position-book">
    <div className={closed ? "position-book-heading closed" : "position-book-heading"}>
      <b>{title}</b>
      <span>{summary.count} positions{closed && summary.rows !== summary.count ? ` · ${summary.rows} exits` : ""} · {summary.winners}▲ {summary.losers}▼</span>
      <span>{labels.capital} <b>{currency(summary.invested)}</b></span>
      <span>{closed ? labels.proceeds : labels === DEFAULT_LABELS ? "Current" : labels.current} <b>{currency(summary.current)}</b></span>
      <span>{closed ? "Realized" : "P&L"} <b className={summary.pnl >= 0 ? "positive" : "negative"}>{currency(summary.pnl)} ({summary.returnPct.toFixed(2)}%)</b></span>
      {closed && <span>Best / worst <b>{summary.best || "—"} / {summary.worst || "—"}</b></span>}
    </div>
    <div className="position-book-body">
      <SortableTable rows={rows} columns={columns} sort={sort} onSort={onSort} empty={empty ?? `No ${title.toLowerCase()}`} rowAction={onOpenPosition ? (row) => onOpenPosition(toFocus(row), list) : undefined} />
    </div>
  </section>;
}

export function SortableTable<Row>({ rows, columns, sort, onSort, empty, rowAction }: {
  rows: Row[]; columns: Column<Row>[]; sort: SortState; onSort: (key: string) => void; empty: string; rowAction?: (row: Row) => void;
}) {
  const sorted = useMemo(() => {
    const column = columns.find((item) => item.key === sort.key) || columns[0];
    const direction = sort.direction === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => compare(column.value(a), column.value(b)) * direction);
  }, [rows, columns, sort]);
  return <div className="ledger-table-wrap"><table className="ledger-table"><thead><tr>{columns.map((column) => <th key={column.key}><button onClick={() => onSort(column.key)}>{column.label}<span className={sort.key === column.key ? "sort active" : "sort"}>{sort.key === column.key ? (sort.direction === "asc" ? "▲" : "▼") : "↕"}</span></button></th>)}</tr></thead><tbody>{sorted.length ? sorted.map((row, index) => <tr key={index} className={rowAction ? "openable-row" : ""} onClick={() => rowAction?.(row)}>{columns.map((column, columnIndex) => <td key={column.key} className={column.tone?.(row) || ""}>{rowAction && columnIndex === 0 ? <button type="button" className="ledger-row-open" aria-label={`Open position chart for ${String(column.value(row))}`} onClick={(event) => { event.stopPropagation(); rowAction(row); }}>{column.render ? column.render(row) : String(column.value(row))}</button> : column.render ? column.render(row) : String(column.value(row))}</td>)}</tr>) : <tr><td colSpan={columns.length} className="empty">{empty}</td></tr>}</tbody></table></div>;
}

function Statistics({ orders, trades, portfolio, rounds, sides = false }: { orders: Order[]; trades: Trade[]; portfolio: Portfolio; rounds: RoundTrip[]; sides?: boolean }) {
  const winners = rounds.filter((row) => row.pnl > 0);
  const losers = rounds.filter((row) => row.pnl < 0);
  const grossProfit = winners.reduce((sum, row) => sum + row.pnl, 0);
  const grossLoss = Math.abs(losers.reduce((sum, row) => sum + row.pnl, 0));
  const net = rounds.reduce((sum, row) => sum + row.pnl, 0);
  const turnover = trades.reduce((sum, row) => sum + row.quantity * row.price, 0);
  const avg = (rows: RoundTrip[]) => rows.length ? rows.reduce((sum, row) => sum + row.pnl, 0) / rows.length : 0;
  const stats = [
    ["Completed trades", number.format(rounds.length)],
    ["Winning / losing trades", `${winners.length} / ${losers.length}`],
    ["Win rate", rounds.length ? `${(100 * winners.length / rounds.length).toFixed(1)}%` : "—"],
    ["Completed-trade net P&L", currency(net)],
    ["Realized in open cycles", currency(portfolio.realized_pnl - net)],
    ["Total realized P&L", currency(portfolio.realized_pnl)],
    ["Gross profit", currency(grossProfit)],
    ["Gross loss", currency(-grossLoss)],
    ["Profit factor", grossLoss ? (grossProfit / grossLoss).toFixed(2) : grossProfit ? "∞" : "—"],
    ["Average winner", currency(avg(winners))],
    ["Average loser", currency(avg(losers))],
    ["Best trade", currency(rounds.length ? Math.max(...rounds.map((row) => row.pnl)) : 0)],
    ["Worst trade", currency(rounds.length ? Math.min(...rounds.map((row) => row.pnl)) : 0)],
    ["Average return", rounds.length ? `${(rounds.reduce((sum, row) => sum + row.returnPct, 0) / rounds.length).toFixed(2)}%` : "—"],
    ["Turnover", currency(turnover)],
    ["Filled / rejected orders", `${orders.filter((row) => row.status === "FILLED").length} / ${attempts(orders.filter((row) => row.status === "REJECTED")).toLocaleString("en-IN")}`],
    ["Open positions", number.format(portfolio.positions.length)],
    ["Live unrealized P&L", currency(portfolio.unrealized_pnl)],
  ];
  return <div className="statistics"><h2>Agent paper book</h2><div className="statistics-note">One trade runs from first entry until fully flat. Partial exits in open cycles are excluded from win rate and completed-trade returns. Returns include recorded fees; realized P&L in open cycles includes their entry fees.</div><div className="statistics-grid">{stats.map(([label, value]) => <Stat key={label} label={label} value={value} />)}</div>
    {sides ? <SideStatistics rounds={rounds} portfolio={portfolio} /> : <IndexStatistics rounds={rounds} portfolio={portfolio} bookNet={net} />}
    <Rejections orders={orders} />
    <h2>Completed trades · entry to flat</h2>
    <table className="ledger-table"><thead><tr><th>Symbol</th>{sides && <th>Side</th>}<th>Entry (IST)</th><th>Exit (IST)</th><th>Entries</th><th>Exits</th><th>Net P&L</th><th>Return</th></tr></thead><tbody>
      {[...rounds].reverse().map((row) => <tr key={`${row.symbol}:${row.entryTime}:${row.exitTime}`}><td>{row.symbol}</td>{sides && <td className={row.side === "SHORT" ? "negative" : "positive"}>{row.side === "SHORT" ? "Short" : "Long"}</td>}<td>{entryStamp(row.entryTime)}</td><td>{entryStamp(row.exitTime)}</td><td>{row.entryCount}</td><td>{row.exitCount}</td><td className={row.pnl >= 0 ? "positive" : "negative"}>{currency(row.pnl)}</td><td>{pct(row.returnPct)}</td></tr>)}
      {!rounds.length && <tr><td colSpan={sides ? 8 : 7}>No fully closed trades</td></tr>}
    </tbody></table>
  </div>;
}

/** The book's index-option trades on their own (NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY, …): the same completed-trade
 * figures, then by side and by index. Hidden for a book with no index trades (spot, stock-only lanes). */
function IndexStatistics({ rounds, portfolio, bookNet }: { rounds: RoundTrip[]; portfolio: Portfolio; bookNet: number }) {
  const index = useMemo(() => rounds.filter((row) => indexUnderlying(row.symbol)), [rounds]);
  const open = portfolio.positions.filter((row) => indexUnderlying(row.symbol));
  if (!index.length && !open.length) return null;
  const all = roundStats(index);
  const ratio = (value: number | null, digits = 2) => value === null ? "—" : Number.isFinite(value) ? value.toFixed(digits) : "∞";
  const money = (value: number | null) => value === null ? "—" : currency(value);
  const rate = (value: number | null) => value === null ? "—" : `${(100 * value).toFixed(1)}%`;
  const stats = [
    ["Index trades", `${number.format(all.trades)} of ${number.format(rounds.length)}`],
    ["Winning / losing trades", `${all.wins} / ${all.losses}`],
    ["Win rate", rate(all.winRate)],
    ["Index net P&L", currency(all.net)],
    ["Share of completed net P&L", bookNet && all.trades ? `${(100 * all.net / bookNet).toFixed(0)}%` : "—"],
    ["Profit factor", ratio(all.profitFactor)],
    ["Average winner", money(all.avgWin)],
    ["Average loser", money(all.avgLoss)],
    ["Winner ÷ loser size", ratio(all.payoff)],
    ["Average return", pct(all.avgReturn)],
    ["Best trade", money(all.best)],
    ["Worst trade", money(all.worst)],
    ["Median hold", all.medianHeldMs === null ? "—" : heldLabel(all.medianHeldMs)],
    ["Open index positions", number.format(open.length)],
    ["Open index unrealized P&L", currency(open.reduce((sum, row) => sum + row.unrealized_pnl, 0))],
  ];
  const groups: [string, RoundStats][] = [
    ...(["CE", "PE"] as const).map((side) => [`All index ${side}`, roundStats(index.filter((row) => optionSide(row.symbol) === side))] as [string, RoundStats]),
    ...INDEX_UNDERLYINGS.flatMap((name): [string, RoundStats][] => {
      const rows = index.filter((row) => indexUnderlying(row.symbol) === name);
      if (!rows.length) return [];
      return [[name, roundStats(rows)], ...(["CE", "PE"] as const).map((side) => [`${name} ${side}`, roundStats(rows.filter((row) => optionSide(row.symbol) === side))] as [string, RoundStats])];
    }),
  ].filter(([, row]) => row.trades > 0);
  return <>
    <h2>Index options only</h2>
    <div className="statistics-note">Completed trades on index options ({INDEX_UNDERLYINGS.join(", ")}), entry to flat, fees included. Winner ÷ loser size is the average winner over the average loser; with a win rate under 50% it has to exceed (1 − win rate) ÷ win rate to pay.</div>
    <div className="statistics-grid">{stats.map(([label, value]) => <Stat key={label} label={label} value={value} />)}</div>
    {!!groups.length && <table className="ledger-table index-stats"><thead><tr><th>Index · side</th><th>Trades</th><th>Win rate</th><th>Net P&L</th><th>Profit factor</th><th>Avg winner</th><th>Avg loser</th><th>Winner ÷ loser</th><th>Avg return</th><th>Median hold</th></tr></thead><tbody>
      {groups.map(([label, row]) => <tr key={label} className={label.includes(" ") ? "" : "index-stats-total"}><td>{label}</td><td>{number.format(row.trades)}</td><td>{rate(row.winRate)}</td>
        <td className={row.net >= 0 ? "positive" : "negative"}>{currency(row.net)}</td><td>{ratio(row.profitFactor)}</td><td>{money(row.avgWin)}</td><td>{money(row.avgLoss)}</td>
        <td>{ratio(row.payoff)}</td><td>{pct(row.avgReturn)}</td><td>{row.medianHeldMs === null ? "—" : heldLabel(row.medianHeldMs)}</td></tr>)}
    </tbody></table>}
  </>;
}

/** A long-and-short book's completed trades by side: the same figures for longs and shorts on their own. */
function SideStatistics({ rounds, portfolio }: { rounds: RoundTrip[]; portfolio: Portfolio }) {
  const ratio = (value: number | null) => value === null ? "—" : Number.isFinite(value) ? value.toFixed(2) : "∞";
  const money = (value: number | null) => value === null ? "—" : currency(value);
  const rate = (value: number | null) => value === null ? "—" : `${(100 * value).toFixed(1)}%`;
  const rows: [string, RoundStats, Position[]][] = (["LONG", "SHORT"] as const).map((side) => [
    side === "LONG" ? "Long" : "Short", roundStats(rounds.filter((row) => row.side === side)),
    portfolio.positions.filter((row) => (row.quantity < 0) === (side === "SHORT"))]);
  return <>
    <h2>Long and short</h2>
    <div className="statistics-note">Completed trades by side, entry to flat. Return is on the entry notional. Winner ÷ loser is the average winner over the average loser.</div>
    <table className="ledger-table index-stats"><thead><tr><th>Side</th><th>Trades</th><th>Win rate</th><th>Net P&L</th><th>Profit factor</th><th>Avg winner</th><th>Avg loser</th><th>Winner ÷ loser</th><th>Avg return</th><th>Median hold</th><th>Open now</th><th>Open P&L</th></tr></thead><tbody>
      {rows.map(([label, row, open]) => { const openPnl = open.reduce((sum, p) => sum + p.unrealized_pnl, 0); return <tr key={label}><td>{label}</td><td>{number.format(row.trades)}</td><td>{rate(row.winRate)}</td>
        <td className={row.net >= 0 ? "positive" : "negative"}>{currency(row.net)}</td><td>{ratio(row.profitFactor)}</td><td>{money(row.avgWin)}</td><td>{money(row.avgLoss)}</td>
        <td>{ratio(row.payoff)}</td><td>{pct(row.avgReturn)}</td><td>{row.medianHeldMs === null ? "—" : heldLabel(row.medianHeldMs)}</td>
        <td>{open.length}</td><td className={openPnl >= 0 ? "positive" : "negative"}>{currency(openPnl)}</td></tr>; })}
    </tbody></table>
  </>;
}

function Rejections({ orders }: { orders: Order[] }) {
  const groups = rejectionsByReason(orders);
  if (!groups.length) return null;
  return <><h2>Rejected order attempts by reason</h2>
    <div className="statistics-note">A run of identical rejects (same contract, side and IST day) counts every attempt. Since 4 Nov 2026 a rejected automated exit backs off for 15 s, 60 s, then 300 s instead of retrying on every tick.</div>
    <table className="ledger-table"><thead><tr><th>Reason</th><th>Attempts</th><th>Contracts</th><th>Last (IST)</th></tr></thead><tbody>
      {groups.map((g) => <tr key={g.reason}><td>{g.reason}</td><td>{g.attempts.toLocaleString("en-IN")}</td>
        <td title={g.symbols.join(", ")}>{g.symbols.length} · {g.symbols.slice(0, 3).map((s) => s.replace(/^NSE:/, "")).join(", ")}{g.symbols.length > 3 ? " …" : ""}</td><td>{entryStamp(g.last)}</td></tr>)}
    </tbody></table></>;
}

function Stat({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><b className={label.includes("P&L") || label.includes("profit") || label.includes("loss") || label.includes("drawdown") ? valueTone(value) : ""}>{value}</b></div>; }

function compare(a: SortValue, b: SortValue) {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}
function positionChangePct(row: Position) { return row.average_price ? (row.last_price / row.average_price - 1) * 100 : 0; }
function positionTone(row: Position) { return row.unrealized_pnl >= 0 ? "positive" : "negative"; }
function closedTone(row: ClosedPosition) { return row.realized_pnl >= 0 ? "positive" : "negative"; }
function positionOptionType(symbol: string): "CE" | "PE" | "OTHER" {
  if (symbol.toUpperCase().endsWith("CE")) return "CE";
  if (symbol.toUpperCase().endsWith("PE")) return "PE";
  return "OTHER";
}
const BOOK_LABELS: Record<Exclude<BookKey, "CLOSED">, string> = { CE: "Calls · CE", PE: "Puts · PE", OTHER: "Other", LONG: "Long", SHORT: "Short" };
function titleCase(value: string) { return value.charAt(0) + value.slice(1).toLowerCase(); }
function valueTone(value: string) { return value.includes("₹-") ? "negative" : value.includes("₹") && value !== "₹0" ? "positive" : ""; }
function tabCount(tab: LedgerTab, orders: Order[], trades: Trade[], portfolio: Portfolio, signals: Signal[], rounds: RoundTrip[]) {
  return tab === "ORDERS" ? orders.length : tab === "TRADES" ? trades.length : tab === "PORTFOLIO" ? portfolio.positions.length : tab === "SIGNALS" ? signals.length : rounds.length;
}
function pageDescription(tab: LedgerTab) { return tab === "ORDERS" ? "Complete persisted agent order log" : tab === "TRADES" ? "Paper fills" : tab === "PORTFOLIO" ? `Open agent-managed option positions, and today's closed ones until ${BOOK_ROLL_LABEL} on the next weekday` : tab === "SIGNALS" ? "Premium MACD zero-cross signals" : "Live paper results"; }
