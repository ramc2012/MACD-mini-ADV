import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { ClosedBook, ClosedPosition, Excursion, Order, Portfolio, Position, Signal, Trade } from "./types";
import { BOOK_ROLL_LABEL, closedBookStart, completedRoundTrips, distinctPositions, observedExcursion, visibleClosedRows, type RoundTrip } from "./ledgerMath";

const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const currency = (value: number) => `₹${number.format(value)}`;
const clock = (value: string) => new Date(value).toLocaleTimeString("en-IN", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const entryStamp = (value: string) => new Date(value).toLocaleString("en-IN", {
  timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
});
// "08:00 IST" while the book started today; "Fri 08:00 IST" over a weekend.
const bookStartLabel = (start: number, now: number) => {
  const sameDay = new Date(start).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" }) === new Date(now).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  return `${sameDay ? "" : `${new Date(start).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "short" })} `}${BOOK_ROLL_LABEL}`;
};
const pct = (value?: number | null) => value === undefined || value === null ? "—" : `${value.toFixed(2)}%`;
const priceTitle = (price?: number | null) => price === undefined || price === null || !(price > 0) ? undefined : currency(price);
const lotsLabel = (row: { lots: number; lot_size: number }) => row.lot_size > 0 ? `${row.lots} × ${row.lot_size}` : "—";
const heldLabel = (ms: number) => { const m = Math.round(ms / 60_000); return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`; };

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
type BookKey = "CE" | "PE" | "OTHER" | "CLOSED";

const initialSorts: Record<LedgerTab, SortState> = {
  ORDERS: { key: "time", direction: "desc" },
  TRADES: { key: "time", direction: "desc" },
  PORTFOLIO: { key: "pnl", direction: "desc" },
  STATISTICS: { key: "value", direction: "desc" },
  SIGNALS: { key: "time", direction: "desc" },
};

export function TradingLedger({ tab, orders, trades, portfolio, signals, onOpenPosition, holidays }: {
  tab: LedgerTab;
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
  const rounds = useMemo(() => completedRoundTrips(trades), [trades]);
  const holidaySet = useMemo(() => new Set(holidays ?? []), [holidays]);
  // Backend records only: a trade-log reconstruction would carry a second P&L
  // definition (no fees, one row per fill, no excursion) beside the real one.
  // Each row's visible_until decides its life, so a Friday exit survives the
  // weekend exactly as long as the backend keeps it in the frame.
  const closedToday = useMemo(() => visibleClosedRows(portfolio.closed_positions ?? [], now, holidaySet), [portfolio.closed_positions, now, holidaySet]);
  const closedPositionCount = useMemo(() => distinctPositions(closedToday), [closedToday]);
  const bookSince = bookStartLabel(closedBookStart(now, holidaySet), now);

  const orderColumns: Column<Order>[] = [
    { key: "time", label: "Time (IST)", value: (row) => Date.parse(row.created_at), render: (row) => clock(row.created_at) },
    { key: "symbol", label: "Symbol", value: (row) => row.symbol },
    { key: "side", label: "Side", value: (row) => row.side, tone: (row) => row.side === "BUY" ? "positive" : "negative" },
    { key: "lots", label: "Lots", value: (row) => row.lots || 0, render: (row) => row.lots || "—" },
    { key: "quantity", label: "Units", value: (row) => row.quantity, render: (row) => `${row.quantity} (${row.lot_size || 1}/lot)` },
    { key: "fill", label: "Fill", value: (row) => row.fill_price || 0, render: (row) => row.fill_price ? currency(row.fill_price) : "—" },
    { key: "status", label: "Status", value: (row) => row.status },
  ];
  const tradeColumns: Column<Trade>[] = [
    { key: "time", label: "Time (IST)", value: (row) => Date.parse(row.timestamp), render: (row) => clock(row.timestamp) },
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
    { key: "time", label: "Time (IST)", value: (row) => Date.parse(row.timestamp as unknown as string), render: (row) => clock(row.timestamp as unknown as string) },
    { key: "symbol", label: "Symbol", value: (row) => row.symbol },
    { key: "side", label: "Side", value: (row) => row.side, tone: (row) => row.side === "BUY" ? "positive" : "negative" },
    { key: "trigger", label: "Trigger", value: (row) => row.kind },
    { key: "price", label: "Premium", value: (row) => row.price, render: (row) => currency(row.price) },
    { key: "macd", label: "MACD", value: (row) => row.macd, render: (row) => number.format(row.macd) },
  ];
  const liveSummary = useMemo(() => positionSummary(portfolio.positions.map((row) => ({
    invested: row.average_price * row.quantity, current: row.last_price * row.quantity, pnl: row.unrealized_pnl, symbol: row.symbol,
  }))), [portfolio.positions]);
  const closedSummary = useMemo(() => positionSummary(closedToday.map((row) => ({
    invested: row.entry_price * row.quantity, current: row.exit_price * row.quantity, pnl: row.realized_pnl, symbol: row.symbol,
  })), closedPositionCount), [closedToday, closedPositionCount]);
  const positionGroups = useMemo(() => {
    const groups: { key: "CE" | "PE" | "OTHER"; title: string; positions: Position[] }[] = [
      { key: "CE", title: "Call positions · CE", positions: [] },
      { key: "PE", title: "Put positions · PE", positions: [] },
      { key: "OTHER", title: "Other positions", positions: [] },
    ];
    portfolio.positions.forEach((position) => {
      const optionType = positionOptionType(position.symbol);
      groups.find((group) => group.key === optionType)?.positions.push(position);
    });
    return groups.filter((group) => group.key !== "OTHER" || group.positions.length > 0).map((group) => ({
      ...group,
      summary: positionSummary(group.positions.map((row) => ({
        invested: row.average_price * row.quantity,
        current: row.last_price * row.quantity,
        pnl: row.unrealized_pnl,
        symbol: row.symbol,
      }))),
    }));
  }, [portfolio.positions]);

  const bookTabsAvailable: { key: BookKey; label: string; count: number }[] = [
    ...positionGroups.map((group) => ({ key: group.key, label: group.key === "CE" ? "Calls · CE" : group.key === "PE" ? "Puts · PE" : "Other", count: group.positions.length })),
    { key: "CLOSED", label: "Closed today", count: closedPositionCount },
  ];
  // OTHER disappears when its last position is flattened; fall back rather than show a blank panel.
  const activeBook = bookTabsAvailable.some((row) => row.key === positionBook) ? positionBook : bookTabsAvailable[0].key;
  const activeGroup = positionGroups.find((group) => group.key === activeBook);
  // Open rows carry opened_at/average_price; closed slices carry the backend's entry_time/entry_price.
  const toFocus = (row: { symbol: string; opened_at?: string; average_price?: number; entry_time?: string; entry_price?: number }): FocusRow =>
    ({ symbol: row.symbol, entryTime: row.entry_time ?? row.opened_at ?? "", entryPrice: row.entry_price ?? row.average_price ?? 0 });
  const positionTabs = <div className="watch-tabs book-tabs position-tabs">{bookTabsAvailable.map((row) => <button key={row.key} className={`${activeBook === row.key ? "active" : ""}${row.key === "CLOSED" ? " closed" : ""}`} onClick={() => setPositionBook(row.key)}>{row.label}<span>{row.count}</span></button>)}</div>;

  return <section className="ledger-page">
    <div className="page-heading"><div><h1>{tab === "STATISTICS" ? "Trade statistics" : titleCase(tab)}</h1><p>{pageDescription(tab)}</p></div><span>{tabCount(tab, orders, trades, portfolio, signals, rounds)} records</span></div>
    <div className="full-ledger panel">
      {tab === "ORDERS" && <SortableTable rows={orders} columns={orderColumns} sort={sort} onSort={updateSort} empty="No paper orders recorded yet" />}
      {tab === "TRADES" && <SortableTable rows={trades} columns={tradeColumns} sort={sort} onSort={updateSort} empty="No paper fills recorded yet" />}
      {tab === "PORTFOLIO" && <div className="book-stack">{positionTabs}<div className="book-fill">
        {activeBook === "CLOSED"
          ? <PositionBook title={`Closed today · since ${bookSince}`} closed rows={closedToday} empty={`No positions closed since ${bookSince}`} summary={closedSummary} columns={closedColumns} sort={closedSort} onSort={updateClosedSort} toFocus={toFocus} onOpenPosition={onOpenPosition} />
          : activeGroup && <PositionBook title={activeGroup.title} rows={activeGroup.positions} summary={activeGroup.summary} columns={positionColumns} sort={sort} onSort={updateSort} toFocus={toFocus} onOpenPosition={onOpenPosition} />}
      </div><SummaryStrip title="Combined open-position summary" summary={liveSummary} /></div>}
      {tab === "SIGNALS" && <SortableTable rows={signals} columns={signalColumns} sort={sort} onSort={updateSort} empty="No MACD signals recorded" />}
      {tab === "STATISTICS" && <Statistics orders={orders} trades={trades} portfolio={portfolio} rounds={rounds} />}
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

function SummaryStrip({ title, summary }: { title: string; summary: BookSummary }) {
  return <div className="summary-strip">
    <b className="summary-title">{title}</b>
    <div><span>Positions</span><b>{summary.count} · {summary.winners}▲ {summary.losers}▼</b></div>
    <div><span>Invested</span><b>{currency(summary.invested)}</b></div>
    <div><span>Current value</span><b>{currency(summary.current)}</b></div>
    <div><span>Unrealized P&L</span><b className={summary.pnl >= 0 ? "positive" : "negative"}>{currency(summary.pnl)} ({summary.returnPct.toFixed(2)}%)</b></div>
    <div><span>Best / worst</span><b>{summary.best || "—"} / {summary.worst || "—"}</b></div>
  </div>;
}

function PositionBook<Row>({ title, rows, summary, columns, sort, onSort, toFocus, onOpenPosition, closed, empty }: {
  title: string;
  rows: Row[];
  empty?: string;
  summary: BookSummary;
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
      <span>Invested <b>{currency(summary.invested)}</b></span>
      <span>{closed ? "Proceeds" : "Current"} <b>{currency(summary.current)}</b></span>
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

function Statistics({ orders, trades, portfolio, rounds }: { orders: Order[]; trades: Trade[]; portfolio: Portfolio; rounds: RoundTrip[] }) {
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
    ["Filled / rejected orders", `${orders.filter((row) => row.status === "FILLED").length} / ${orders.filter((row) => row.status === "REJECTED").length}`],
    ["Open positions", number.format(portfolio.positions.length)],
    ["Live unrealized P&L", currency(portfolio.unrealized_pnl)],
  ];
  return <div className="statistics"><h2>Agent paper book</h2><div className="statistics-note">One trade runs from first entry until fully flat. Partial exits in open cycles are excluded from win rate and completed-trade returns. Returns include recorded fees; realized P&L in open cycles includes their entry fees.</div><div className="statistics-grid">{stats.map(([label, value]) => <Stat key={label} label={label} value={value} />)}</div>
    <h2>Completed trades · entry to flat</h2>
    <table className="ledger-table"><thead><tr><th>Symbol</th><th>Entry (IST)</th><th>Exit (IST)</th><th>Entries</th><th>Exits</th><th>Net P&L</th><th>Return</th></tr></thead><tbody>
      {[...rounds].reverse().map((row) => <tr key={`${row.symbol}:${row.entryTime}:${row.exitTime}`}><td>{row.symbol}</td><td>{entryStamp(row.entryTime)}</td><td>{entryStamp(row.exitTime)}</td><td>{row.entryCount}</td><td>{row.exitCount}</td><td className={row.pnl >= 0 ? "positive" : "negative"}>{currency(row.pnl)}</td><td>{pct(row.returnPct)}</td></tr>)}
      {!rounds.length && <tr><td colSpan={7}>No fully closed trades</td></tr>}
    </tbody></table>
  </div>;
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
function titleCase(value: string) { return value.charAt(0) + value.slice(1).toLowerCase(); }
function valueTone(value: string) { return value.includes("₹-") ? "negative" : value.includes("₹") && value !== "₹0" ? "positive" : ""; }
function tabCount(tab: LedgerTab, orders: Order[], trades: Trade[], portfolio: Portfolio, signals: Signal[], rounds: RoundTrip[]) {
  return tab === "ORDERS" ? orders.length : tab === "TRADES" ? trades.length : tab === "PORTFOLIO" ? portfolio.positions.length : tab === "SIGNALS" ? signals.length : rounds.length;
}
function pageDescription(tab: LedgerTab) { return tab === "ORDERS" ? "Complete persisted agent order log" : tab === "TRADES" ? "Paper fills" : tab === "PORTFOLIO" ? `Open agent-managed option positions, and today's closed ones until ${BOOK_ROLL_LABEL} on the next weekday` : tab === "SIGNALS" ? "Premium MACD zero-cross signals" : "Live paper results"; }
