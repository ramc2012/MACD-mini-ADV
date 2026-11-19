import { useMemo, useState } from "react";
import { tone } from "./agentMath";
import { EquityChart } from "./LaneEquityChart";
import { SortableTable, type Column, type SortState } from "./Ledger";
import { bookStats, chargesOf, filterBook, grossOf, instrumentOf, netCurve, netOf, netReturnPct, openedBy,
  type BookTrade, type Instrument, type Who } from "./upstoxBookMath";
import { inLaneDay } from "./laneDay";

// The Upstox live lane's Performance and Trades tabs (owner, 19 Nov 2026). One set of metrics from the lane's own
// closed-trade list, after Upstox's actual charges, filterable by instrument (index / stock options) and by who opened
// the trade; the Trades tab says who initiated every execution. Replaces the shared paper-ledger tables here, which
// rebuilt trades from fills and so missed the trades the owner opened or closed by hand.

export type ClosedTrade = BookTrade & { closed_at: string; exit_price: number };
export type TradeRow = { id: string; order_id?: string; symbol: string; side: string; quantity: number; price: number; at: string;
  initiated_by: "lane" | "owner"; why: string; charges: number | null };
export type ChargeDay = { upstox_total: number; executions: number; read_at: string };

const short = (symbol: string) => symbol.replace(/^(NSE|BSE):/, "").split("|")[0];
const inr = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value) ? "—"
  : `${value < 0 ? "-" : ""}₹${new Intl.NumberFormat("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Math.abs(value))}`;
const signed = (value: number | null | undefined) => value == null ? "—" : `${value > 0 ? "+" : ""}${inr(value)}`;
const pct = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const toneOf = (value: number | null | undefined): "positive" | "negative" | "" => !value ? "" : value > 0 ? "positive" : "negative";
const when = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", day: "2-digit", month: "short",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(d) + " IST";
};
const WHO_LABEL = { LANE: "Lane", OWNER: "You" } as const;

function Toggle<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (next: T) => void }) {
  return <div className="risk-tabs upstox-filter" role="group" aria-label={label}><span>{label}</span>
    {options.map(([key, text]) => <button key={key} type="button" aria-pressed={value === key} onClick={() => onChange(key)}>{text}</button>)}
  </div>;
}

function Stat({ label, value, note, sign }: { label: string; value: string; note?: string; sign?: number | null }) {
  return <div><span>{label}</span><b className={sign == null ? "" : tone(sign)}>{value}</b>{note && <small>{note}</small>}</div>;
}

/** The Instrument / Opened-by filters shared by Performance and the Journal's closed trades. */
function useBookFilter(closed: ClosedTrade[]) {
  const [instrument, setInstrument] = useState<Instrument>("ALL");
  const [who, setWho] = useState<Who>("ALL");
  const rows = useMemo(() => filterBook(closed, instrument, who), [closed, instrument, who]);
  const all = filterBook(closed, "ALL", who);
  const counts = { ALL: all.length, INDEX: all.filter((r) => instrumentOf(r.symbol) === "INDEX").length, STOCK: all.filter((r) => instrumentOf(r.symbol) === "STOCK").length };
  const filters = <div className="upstox-filters">
    <Toggle label="Instrument" value={instrument} onChange={setInstrument}
      options={[["ALL", `All ${counts.ALL}`], ["INDEX", `Index options ${counts.INDEX}`], ["STOCK", `Stock options ${counts.STOCK}`]]} />
    <Toggle label="Opened by" value={who} onChange={setWho} options={[["ALL", "Anyone"], ["LANE", "Lane"], ["OWNER", "You"]]} />
  </div>;
  return { rows, instrument, who, filters, filtered: instrument !== "ALL" || who !== "ALL" };
}

/** Closed trades only (owner, 19 Nov 2026: one equity line against the capital line, no drawdown pane; open P&L is in
 * the page header, not here). */
export function UpstoxPerformance({ closed, capital, startedAt, chargeDays = {}, chargesError }: {
  closed: ClosedTrade[]; capital: number; startedAt?: string;
  chargeDays?: Record<string, ChargeDay>; chargesError?: { day?: string; error: string } | null;
}) {
  const { rows, instrument, who, filters, filtered } = useBookFilter(closed);
  const s = useMemo(() => bookStats(rows), [rows]);
  const curve = useMemo(() => netCurve(rows, capital, startedAt, undefined), [rows, capital, startedAt]);
  const upstoxTotal = Object.values(chargeDays).reduce((sum, d) => sum + d.upstox_total, 0);
  const scope = [instrument === "INDEX" ? "index options" : instrument === "STOCK" ? "stock options" : "all options",
    who === "LANE" ? "opened by the lane" : who === "OWNER" ? "opened by you" : ""].filter(Boolean).join(", ");
  return <section className="panel agent-panel upstox-perf">
      <div className="panel-title">Performance after charges <span>{s.count} closed trade{s.count === 1 ? "" : "s"}{filtered ? ` · ${scope}` : ""}</span></div>
      {filters}
      <EquityChart points={curve} capital={capital} height={220} label="Booked equity after charges" drawdown={false} />
      <div className="statistics-grid upstox-stats">
        <Stat label="Closed trades" value={String(s.count)}
          note={`opened: lane ${s.openedByLane} · you ${s.openedByOwner} — closed: lane ${s.closedByLane} · you ${s.closedByOwner}`} />
        <Stat label="Net P&L after charges" value={signed(s.net)} sign={s.net} note={`${pct(s.net / capital * 100, 2)} of the ₹${capital.toLocaleString("en-IN")} start`} />
        <Stat label="Gross P&L" value={signed(s.gross)} sign={s.gross} note="before charges" />
        <Stat label="Charges (Upstox)" value={inr(-s.charges.total)} sign={-s.charges.total}
          note={`brokerage ${inr(s.charges.brokerage)} · STT ${inr(s.charges.stt)} · GST ${inr(s.charges.gst)} · exchange ${inr(s.charges.exchange)} · stamp ${inr(s.charges.stamp_duty)}`} />
        <Stat label="Won / lost" value={`${s.wins} / ${s.losses}`} note={`win rate ${s.winRate == null ? "—" : `${s.winRate.toFixed(1)}%`}${s.count - s.wins - s.losses ? ` · ${s.count - s.wins - s.losses} flat` : ""}`} />
        <Stat label="Profit factor" value={s.profitFactor == null ? "—" : s.profitFactor.toFixed(2)} note={`net won ${inr(s.netWin, 0)} ÷ net lost ${inr(s.netLoss, 0)}`} />
        <Stat label="Average win / loss" value={`${signed(s.avgWin)} / ${signed(s.avgLoss)}`} note={s.sizeRatio == null ? undefined : `a win is ${s.sizeRatio.toFixed(2)}× a loss`} />
        <Stat label="Best / worst trade" value={`${signed(s.best)} / ${signed(s.worst)}`} />
        <Stat label="Average net return" value={pct(s.avgReturnPct, 2)} sign={s.avgReturnPct} note="per trade, on the premium paid" />
      </div>
      <p className="statistics-note upstox-note">Charges are Upstox's own figures: each day's actual F&amp;O charges from its P&amp;L report
        ({inr(upstoxTotal)} over {Object.keys(chargeDays).length} days, including any trades of yours outside the lane), split over that day's executions —
        brokerage per order, STT on sells, stamp duty on buys, exchange and GST by value.
        {s.pending ? ` ${s.pending} trade${s.pending === 1 ? " waits" : "s wait"} for Upstox's report; shown at gross until then.` : ""}
        {chargesError ? ` Last read failed${chargesError.day ? ` for ${chargesError.day}` : ""}: ${chargesError.error}` : ""}</p>
  </section>;
}

/** Every closed trade, after charges: shown in the Journal next to the decisions (owner, 19 Nov 2026). */
export function UpstoxClosedTrades({ closed }: { closed: ClosedTrade[] }) {
  const { rows, filters } = useBookFilter(closed);
  const [sort, setSort] = useState<SortState>({ key: "closed", direction: "desc" });
  const columns: Column<ClosedTrade>[] = [
    { key: "closed", label: "Closed (IST)", value: (r) => Date.parse(r.closed_at), render: (r) => when(r.closed_at) },
    { key: "contract", label: "Contract", value: (r) => short(r.symbol) },
    { key: "type", label: "Type", value: (r) => instrumentOf(r.symbol) === "INDEX" ? "Index" : "Stock" },
    { key: "opened", label: "Opened by", value: (r) => WHO_LABEL[openedBy(r)] },
    { key: "closedby", label: "Closed by", value: (r) => r.closed_by === "owner" ? "You" : "Lane" },
    { key: "qty", label: "Qty", value: (r) => r.bought || r.quantity },
    { key: "entry", label: "Entry", value: (r) => r.average_price, render: (r) => r.average_price.toFixed(2) },
    { key: "exit", label: "Exit", value: (r) => r.exit_price, render: (r) => r.exit_price.toFixed(2) },
    { key: "gross", label: "Gross P&L", value: grossOf, render: (r) => signed(grossOf(r)), tone: (r) => toneOf(grossOf(r)) },
    { key: "charges", label: "Charges", value: chargesOf, render: (r) => r.charges_pending ? "pending" : inr(chargesOf(r)) },
    { key: "net", label: "Net P&L", value: netOf, render: (r) => signed(netOf(r)), tone: (r) => toneOf(netOf(r)) },
    { key: "return", label: "Net return", value: (r) => netReturnPct(r) ?? 0, render: (r) => pct(netReturnPct(r), 2), tone: (r) => toneOf(netReturnPct(r)) },
  ];
  const onSort = (key: string) => setSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }));
  return <>
    {filters}
    <SortableTable rows={rows} columns={columns} sort={sort} onSort={onSort} empty="No closed trades in this filter" />
  </>;
}

export function UpstoxTrades({ rows, dayStart }: { rows: TradeRow[]; dayStart: number }) {
  const [allDays, setAllDays] = useState(false);
  const [who, setWho] = useState<Who>("ALL");
  const [sort, setSort] = useState<SortState>({ key: "time", direction: "desc" });
  const shown = rows.filter((r) => (allDays || inLaneDay(r.at, dayStart)) && (who === "ALL" || (who === "OWNER") === (r.initiated_by === "owner")));
  const columns: Column<TradeRow>[] = [
    { key: "time", label: "Time (IST)", value: (r) => Date.parse(r.at), render: (r) => when(r.at) },
    { key: "contract", label: "Contract", value: (r) => short(r.symbol) },
    { key: "side", label: "Side", value: (r) => r.side, tone: (r) => r.side === "BUY" ? "positive" : "negative" },
    { key: "who", label: "Initiated by", value: (r) => r.initiated_by === "owner" ? "You" : "Lane",
      render: (r) => <span className={r.initiated_by === "owner" ? "mode" : "feed-badge"}>{r.initiated_by === "owner" ? "You" : "Lane"}</span> },
    { key: "why", label: "Why", value: (r) => r.why },
    { key: "qty", label: "Qty", value: (r) => r.quantity },
    { key: "price", label: "Price", value: (r) => r.price, render: (r) => r.price.toFixed(2) },
    { key: "value", label: "Value", value: (r) => r.price * r.quantity, render: (r) => inr(r.price * r.quantity) },
    { key: "charges", label: "Charges", value: (r) => r.charges ?? -1, render: (r) => r.charges == null ? "pending" : inr(r.charges) },
  ];
  const onSort = (key: string) => setSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }));
  return <section className="panel agent-panel">
    <div className="panel-title">Trades {allDays ? "· all days" : "· today"} <span>{shown.length}</span></div>
    <div className="upstox-filters">
      <Toggle label="Show" value={allDays ? "ALL" : "TODAY"} onChange={(v) => setAllDays(v === "ALL")} options={[["TODAY", "Today"], ["ALL", `All days ${rows.length}`]]} />
      <Toggle label="Initiated by" value={who} onChange={setWho} options={[["ALL", "Anyone"], ["LANE", "Lane"], ["OWNER", "You"]]} />
    </div>
    <p className="statistics-note upstox-note">Every execution in the lane's book: the lane's own orders (MACD signal entries and exits) and the trades you made by hand
      on positions the lane manages (an option you bought and the lane adopted, a lane position you sold, Exit all). Charges are each trade's share of Upstox's actual charges for that day.</p>
    <SortableTable rows={shown} columns={columns} sort={sort} onSort={onSort}
      empty={allDays ? "No trades recorded yet" : "No trades today. Show all days for the full list."} />
  </section>;
}

