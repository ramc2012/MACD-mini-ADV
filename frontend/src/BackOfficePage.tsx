import { lazy, Suspense, useMemo, useState } from "react";
import { rupees, tone } from "./agentMath";
import { EquityChart } from "./LaneEquityChart";
import { API_TOKEN, API_URL } from "./runtime";
import { useCachedJson } from "./useCachedJson";
import "./backoffice.css";
import {
  heat, holdText, istDaysAgo, ORDER_LANES, paperSummary, paperTotals, periodLabel, query, splitKinds, VIEWS,
  haircutShare, type Calendar, type LaneStatement, type SpreadReport, type LaneSummary, type Page, type PeriodRow, type SplitRow, type Summary, type TradeRow, type View,
} from "./backofficeMath";
import type { Order } from "./types";

// Back office (owner, 18 Nov 2026): lane pages show only today; every lane's history lives here,
// lane-wise, day-wise, monthly and yearly, with registers and CSV. Read-only: nothing here trades.
const ScoreboardPage = lazy(() => import("./ScoreboardPage").then((m) => ({ default: m.ScoreboardPage })));

const lakh = (value: number) => Math.abs(value) >= 10_000_000 ? `₹${(value / 10_000_000).toFixed(2)} Cr` : `₹${(value / 100_000).toFixed(2)} L`;
const percent = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
const pf = (value: number | null | undefined) => value == null ? "—" : value.toFixed(2);
const winPct = (value: number | null | undefined) => value == null ? "—" : `${Math.round(value)}%`;
const stamp = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const when = (iso?: string) => { const t = Date.parse(iso ?? ""); return Number.isFinite(t) ? stamp.format(t) : "—"; };
const price = (value?: number | null) => value == null || !Number.isFinite(value) ? "—" : value.toFixed(2);

type Csv = (view: "trades" | "days" | "months" | "years", lane?: string) => void;

async function downloadCsv(path: string, name: string): Promise<void> {
  const response = await fetch(`${API_URL}${path}`, { headers: API_TOKEN ? { "x-macd-token": API_TOKEN } : {} });
  if (!response.ok) throw new Error(`CSV export failed (HTTP ${response.status})`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

export function BackOfficePage({ view, onView }: { view: View; onView: (view: View) => void }) {
  const summary = useCachedJson<Summary>("/api/backoffice/summary", 60_000);
  const lanes = useMemo(() => summary.data?.lanes ?? [], [summary.data]);
  const [lane, setLane] = useState("macd");
  const [start, setStart] = useState(() => istDaysAgo(30));
  const [end, setEnd] = useState("");
  const [symbol, setSymbol] = useState("");
  const [csvError, setCsvError] = useState("");
  const exportCsv: Csv = (csvView, csvLane) => {
    setCsvError("");
    const params = { view: csvView, lane: csvLane, start: csvView === "trades" || csvView === "days" ? start : undefined, end: csvView === "trades" || csvView === "days" ? end : undefined };
    void downloadCsv(`/api/backoffice/export.csv${query(params)}`, `backoffice-${csvView}-${csvLane || "all"}.csv`).catch((exc) => setCsvError(String(exc instanceof Error ? exc.message : exc)));
  };
  const needsRange = view === "DAILY" || view === "LANE" || view === "TRADES" || view === "ORDERS" || view === "SPREAD";
  const needsLane = view === "LANE" || view === "TRADES" || view === "ORDERS";
  const laneOptions = view === "ORDERS" ? lanes.filter((row) => ORDER_LANES.includes(row.lane)) : lanes;

  return <div className="agent-page lanes-page backoffice-page">
    <div className="agent-head panel-row">
      <div><b>Back office</b><span className="agent-sub">every lane's history · lane pages show today only{summary.data ? ` · as of ${when(summary.data.as_of)} IST` : ""}</span></div>
      <div className="agent-actions"><button className="settings-button" onClick={summary.reload}>Refresh</button></div>
    </div>
    <nav className="sub-tabs" aria-label="Back office views">{VIEWS.map(([key, label]) => <button key={key} className={view === key ? "active" : ""} aria-current={view === key ? "page" : undefined} onClick={() => onView(key)}>{label}</button>)}</nav>
    {(needsRange || needsLane) && <section className="panel agent-panel backoffice-filters">
      {needsLane && <label>Lane <select value={view === "TRADES" ? lane : laneOptions.some((row) => row.lane === lane) ? lane : laneOptions[0]?.lane ?? ""} onChange={(e) => setLane(e.target.value)}>
        {view === "TRADES" && <option value="">All lanes</option>}
        {laneOptions.map((row) => <option key={row.lane} value={row.lane}>{row.label}{row.kind === "real" ? " (real money)" : ""}</option>)}</select></label>}
      <label>From <input type="date" value={start} max={end || undefined} onChange={(e) => setStart(e.target.value)} /></label>
      <label>To <input type="date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} /></label>
      {view === "TRADES" && <label>Symbol <input type="search" value={symbol} placeholder="NIFTY, RELIANCE…" onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></label>}
      <button className="settings-button" onClick={() => { setStart(""); setEnd(""); }}>All time</button>
      <button className="settings-button" onClick={() => { setStart(istDaysAgo(30)); setEnd(""); }}>Last 30 days</button>
    </section>}
    {summary.error && <div className="agent-error" role="alert">{summary.error}</div>}
    {csvError && <div className="agent-error" role="alert">{csvError}</div>}
    {!summary.data && !summary.error ? <div className="agent-empty state-loading" role="status">Loading the back office…</div>
      : view === "OVERVIEW" ? <Overview lanes={lanes} />
      : view === "DAILY" ? <PeriodMatrix period="day" start={start} end={end} />
      : view === "MONTHLY" ? <Periods lanes={lanes} kind="months" onCsv={exportCsv} />
      : view === "YEARLY" ? <Periods lanes={lanes} kind="years" onCsv={exportCsv} />
      : view === "LANE" ? <Statement lane={lane} start={start} end={end} onCsv={exportCsv} />
      : view === "TRADES" ? <Trades lane={lane} start={start} end={end} symbol={symbol} onCsv={exportCsv} />
      : view === "SPREAD" ? <Spread start={start} end={end} />
      : <Orders lane={laneOptions.some((row) => row.lane === lane) ? lane : laneOptions[0]?.lane ?? "macd"} start={start} end={end} />}
  </div>;
}

function Overview({ lanes }: { lanes: LaneSummary[] }) {
  const { paper, real } = splitKinds(lanes);
  const total = paperSummary(lanes);
  const row = (b: LaneSummary) => b.error ? <tr key={b.lane}><td><b>{b.label}</b></td><td colSpan={10} className="negative">{b.error}</td></tr> : <tr key={b.lane} title={b.note ?? undefined}>
    <td><b>{b.label}</b>{b.note && <small> ⓘ</small>}</td><td>{b.since ? periodLabel(b.since) + " " + b.since.slice(0, 4) : "—"}</td>
    <td>{lakh(b.capital)}{b.capital_added ? <small> (+{lakh(b.capital_added)} added)</small> : null}</td><td>{lakh(b.equity)}</td>
    <td className={tone(b.realized)}>{rupees(b.realized)}</td>
    <td className={tone(b.equity - b.capital)}>{b.capital ? percent(((b.equity - b.capital) / b.capital) * 100) : "—"}</td>
    <td>{b.closed.trades}</td><td>{b.closed.win_rate == null ? "—" : `${Math.round(b.closed.win_rate * 100)}%`}</td><td>{pf(b.closed.profit_factor)}</td>
    <td>{b.sessions}</td>
    <td className={tone(b.months.at(-1)?.pnl)}>{b.months.at(-1) ? <>{rupees(b.months.at(-1)!.pnl)}<small> {periodLabel(b.months.at(-1)!.period)}</small></> : "—"}</td>
  </tr>;
  const head = <thead><tr><th>Lane</th><th>Since</th><th>Capital</th><th>Equity</th><th>Realised</th><th>Return</th><th>Trades</th><th>Win</th><th>PF</th><th>Sessions</th><th>This month</th></tr></thead>;
  return <>
    <section className="panel agent-panel">
      <div className="panel-title">Paper lanes <small>{total.lanes} books · capital {lakh(total.capital)} · equity {lakh(total.equity)} · realised <span className={tone(total.realized)}>{rupees(total.realized)}</span> · {total.trades} closed trades</small></div>
      <div className="live-table-wrap"><table className="live-table agent-table">{head}<tbody>{paper.map(row)}</tbody></table></div>
    </section>
    {real.length > 0 && <section className="panel agent-panel">
      <div className="panel-title">Real money <small>Upstox live: kept apart, never added into the paper totals</small></div>
      <div className="live-table-wrap"><table className="live-table agent-table">{head}<tbody>{real.map(row)}</tbody></table></div>
    </section>}
    <Suspense fallback={<div className="agent-empty state-loading" role="status">Loading the scoreboard…</div>}><ScoreboardPage /></Suspense>
  </>;
}

function PeriodMatrix({ period, start, end }: { period: "day" | "month" | "year"; start?: string; end?: string }) {
  const { data, error } = useCachedJson<Calendar>(`/api/backoffice/calendar${query({ period, start, end })}`);
  const totals = useMemo(() => paperTotals(data), [data]);
  const scale = useMemo(() => Math.max(1, ...Object.values(totals).map(Math.abs), ...(data?.lanes ?? []).flatMap((l) => Object.values(l.cells).map(Math.abs))), [data, totals]);
  if (error) return <div className="agent-error" role="alert">{error}</div>;
  if (!data) return <div className="agent-empty state-loading" role="status">Loading…</div>;
  const { paper, real } = splitKinds(data.lanes);
  const cell = (value: number | undefined, key: string) => <td key={key} className={`bo-heat bo-heat-${heat(value, scale)} ${tone(value)}`}>{value == null ? "" : rupees(value)}</td>;
  return <section className="panel agent-panel">
    <div className="panel-title">{period === "day" ? "Daily" : period === "month" ? "Monthly" : "Yearly"} P&amp;L by lane <small>equity change each IST day, capital top-ups netted out (open marks count) · newest first</small></div>
    {data.periods.length === 0 ? <div className="agent-empty">No sessions in this range.</div> :
      <div className="live-table-wrap"><table className="live-table agent-table bo-matrix"><thead><tr><th>Lane</th>{data.periods.map((p) => <th key={p}>{periodLabel(p)}</th>)}</tr></thead>
        <tbody>
          {paper.map((l) => <tr key={l.lane}><td>{l.label}</td>{data.periods.map((p) => cell(l.cells[p], p))}</tr>)}
          <tr className="bo-total"><td><b>Paper total</b></td>{data.periods.map((p) => cell(totals[p], p))}</tr>
          {real.map((l) => <tr key={l.lane} className="bo-real"><td>{l.label} <small>real money</small></td>{data.periods.map((p) => cell(l.cells[p], p))}</tr>)}
        </tbody></table></div>}
  </section>;
}

function PeriodTable({ rows, unit }: { rows: PeriodRow[]; unit: "months" | "years" }) {
  return <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr>
    <th>{unit === "months" ? "Month" : "Year"}</th><th>P&amp;L</th><th>Return</th><th>Realised</th><th>Opening</th><th>Closing</th><th>Capital added</th>
    <th>Trades</th><th>Win</th><th>PF</th><th>Best day</th><th>Worst day</th><th>Max DD</th><th>Sessions</th>
  </tr></thead><tbody>{[...rows].reverse().map((r) => <tr key={r.period}>
    <td><b>{periodLabel(r.period)}</b></td><td className={tone(r.pnl)}>{rupees(r.pnl)}</td><td className={tone(r.return_pct)}>{percent(r.return_pct)}</td>
    <td className={tone(r.realized)}>{rupees(r.realized)}</td><td>{lakh(r.opening_equity)}</td><td>{lakh(r.closing_equity)}</td>
    <td>{r.capital_added ? lakh(r.capital_added) : "—"}</td><td>{r.trades}</td><td>{winPct(r.win_rate)}</td><td>{pf(r.profit_factor)}</td>
    <td className={tone(r.best_day_pnl)}>{rupees(r.best_day_pnl)}<small> {periodLabel(r.best_day)}</small></td>
    <td className={tone(r.worst_day_pnl)}>{rupees(r.worst_day_pnl)}<small> {periodLabel(r.worst_day)}</small></td>
    <td className={r.max_drawdown < 0 ? "negative" : ""}>{rupees(r.max_drawdown)}</td><td>{r.sessions}</td>
  </tr>)}</tbody></table></div>;
}

function Periods({ lanes, kind, onCsv }: { lanes: LaneSummary[]; kind: "months" | "years"; onCsv: Csv }) {
  const books = lanes.filter((b) => !b.error && b[kind].length);
  return <>
    <PeriodMatrix period={kind === "months" ? "month" : "year"} />
    {books.map((b) => <section key={b.lane} className="panel agent-panel">
      <div className="panel-title">{b.label}{b.kind === "real" ? " (real money)" : ""} <small>capital {lakh(b.capital)}{b.capital_added ? `, ${lakh(b.capital_added)} added since ${lakh(b.starting_capital)}` : ""}</small>
        <button className="settings-button" onClick={() => onCsv(kind, b.lane)}>CSV</button></div>
      <PeriodTable rows={b[kind]} unit={kind} />
    </section>)}
  </>;
}

function SplitTable({ title, rows }: { title: string; rows: SplitRow[] }) {
  return <div className="bo-split"><div className="panel-title">{title}</div>
    {rows.length === 0 ? <div className="agent-empty">No exits.</div> :
      <table className="live-table agent-table"><thead><tr><th></th><th>Net</th><th>Trades</th><th>Win</th><th>PF</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.key}><td>{r.key.replace(/_/g, " ")}</td><td className={tone(r.net)}>{rupees(r.net)}</td><td>{r.trades}</td><td>{winPct(r.win_rate)}</td><td>{pf(r.profit_factor)}</td></tr>)}</tbody></table>}
  </div>;
}

function Statement({ lane, start, end, onCsv }: { lane: string; start: string; end: string; onCsv: Csv }) {
  const { data, error } = useCachedJson<LaneStatement>(lane ? `/api/backoffice/lanes/${encodeURIComponent(lane)}${query({ start, end })}` : null);
  const points = useMemo(() => (data?.days ?? []).map((d) => ({ at: `${d.day}T15:30:00+05:30`, equity: d.equity })), [data]);
  if (error) return <div className="agent-error" role="alert">{error}</div>;
  if (!data) return <div className="agent-empty state-loading" role="status">Loading the statement…</div>;
  const days = data.days;
  const net = days.reduce((sum, d) => sum + d.pnl, 0);
  const realized = days.reduce((sum, d) => sum + d.realized, 0);
  const trades = days.reduce((sum, d) => sum + d.trades, 0);
  const wins = days.reduce((sum, d) => sum + d.wins, 0);
  const gw = days.reduce((sum, d) => sum + d.gross_win, 0), gl = days.reduce((sum, d) => sum + d.gross_loss, 0);
  return <>
    <section className="panel agent-panel">
      <div className="panel-title">{data.label}{data.kind === "real" ? " (real money)" : ""} statement <small>{days.length} sessions · P&amp;L <span className={tone(net)}>{rupees(net)}</span> · realised <span className={tone(realized)}>{rupees(realized)}</span> · {trades} trades · win {trades ? `${Math.round((wins / trades) * 100)}%` : "—"} · PF {gl < 0 ? (gw / -gl).toFixed(2) : "—"}</small>
        <button className="settings-button" onClick={() => onCsv("days", lane)}>Days CSV</button></div>
      {data.note && <p className="agent-sub">{data.note}</p>}
      {points.length > 1 && <EquityChart points={points} capital={data.capital} height={220} label={`${data.label} equity (IST close)`} />}
    </section>
    <section className="panel agent-panel bo-splits">
      <SplitTable title="By underlying" rows={data.splits.underlying} />
      <SplitTable title="CE / PE / FUT / EQ" rows={data.splits.kind} />
      <SplitTable title="By exit reason" rows={data.splits.exit_reason} />
      <SplitTable title="By hold time" rows={data.splits.hold} />
    </section>
    <section className="panel agent-panel">
      <div className="panel-title">Day by day <small>newest first</small></div>
      {days.length === 0 ? <div className="agent-empty">No sessions in this range.</div> :
        <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Day (IST)</th><th>P&amp;L</th><th>Realised</th><th>Mark change</th><th>Equity</th><th>Capital added</th><th>Trades</th><th>Wins</th><th>Exits</th><th>Fees</th></tr></thead>
          <tbody>{[...days].reverse().map((d) => <tr key={d.day}><td>{d.day}</td><td className={tone(d.pnl)}>{rupees(d.pnl)}</td><td className={tone(d.realized)}>{rupees(d.realized)}</td>
            <td className={tone(d.unrealized_change)}>{rupees(d.unrealized_change)}</td><td>{lakh(d.equity)}</td><td>{d.capital_added ? lakh(d.capital_added) : ""}</td>
            <td>{d.trades}</td><td>{d.wins}</td><td>{d.exits}</td><td>{d.fees ? rupees(-d.fees) : ""}</td></tr>)}</tbody></table></div>}
    </section>
    <Trades lane={lane} start={start} end={end} symbol="" onCsv={onCsv} />
  </>;
}

function Pager({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return <div className="bo-pager"><button className="settings-button" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Newer</button>
    <span>page {page} of {pages} · {total.toLocaleString("en-IN")} rows</span>
    <button className="settings-button" disabled={page >= pages} onClick={() => onPage(page + 1)}>Older ›</button></div>;
}

function Trades({ lane, start, end, symbol, onCsv }: { lane: string; start: string; end: string; symbol: string; onCsv: Csv }) {
  const [page, setPage] = useState(1);
  const filterKey = `${lane}|${start}|${end}|${symbol}`;
  const [lastKey, setLastKey] = useState(filterKey);
  if (lastKey !== filterKey) { setLastKey(filterKey); setPage(1); }
  const { data, error } = useCachedJson<Page<TradeRow>>(`/api/backoffice/trades${query({ lane, start, end, symbol, page, size: 100 })}`);
  return <section className="panel agent-panel">
    <div className="panel-title">Trade register <small>each exit slice; a staged exit is several rows · newest first{data ? ` · net ${rupees(data.net)}` : ""}</small>
      <button className="settings-button" onClick={() => onCsv("trades", lane || undefined)}>CSV</button></div>
    {error ? <div className="agent-error" role="alert">{error}</div> : !data ? <div className="agent-empty state-loading" role="status">Loading…</div> : data.rows.length === 0 ? <div className="agent-empty">No trades in this range.</div> : <>
      <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Lane</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry (IST)</th><th>Entry</th><th>Exit (IST)</th><th>Exit</th><th>Held</th><th>P&amp;L</th><th>Return</th><th>Exit reason</th></tr></thead>
        <tbody>{data.rows.map((r, i) => <tr key={`${r.lane}-${r.position_id}-${r.exit_time}-${i}`}>
          <td>{r.lane}</td><td>{r.symbol}{r.partial ? <small> partial</small> : null}</td><td>{r.side}</td><td>{r.quantity ?? "—"}</td>
          <td>{when(r.entry_time)}</td><td>{price(r.entry_price)}</td><td>{when(r.exit_time)}</td><td>{price(r.exit_price)}</td><td>{holdText(r.entry_time, r.exit_time)}</td>
          <td className={tone(r.pnl)}>{rupees(r.pnl)}</td><td className={tone(r.return_pct ?? undefined)}>{r.return_pct == null ? "—" : percent(Number(r.return_pct))}</td>
          <td>{(r.exit_reason ?? "").replace(/_/g, " ").toLowerCase()}</td></tr>)}</tbody></table></div>
      <Pager page={data.page} size={data.size} total={data.total} onPage={setPage} />
    </>}
  </section>;
}

function Orders({ lane, start, end }: { lane: string; start: string; end: string }) {
  const [page, setPage] = useState(1);
  const filterKey = `${lane}|${start}|${end}`;
  const [lastKey, setLastKey] = useState(filterKey);
  if (lastKey !== filterKey) { setLastKey(filterKey); setPage(1); }
  const { data, error } = useCachedJson<Page<Order>>(`/api/backoffice/orders${query({ lane, start, end, page, size: 200 })}`);
  return <section className="panel agent-panel">
    <div className="panel-title">Order register <small>{lane} · every order, rejected ones included · newest first</small></div>
    {error ? <div className="agent-error" role="alert">{error}</div> : !data ? <div className="agent-empty state-loading" role="status">Loading…</div> : data.rows.length === 0 ? <div className="agent-empty">No orders in this range.</div> : <>
      <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Time (IST)</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Type</th><th>Status</th><th>Fill</th><th>Reason</th></tr></thead>
        <tbody>{data.rows.map((o) => <tr key={o.order_id}><td>{when(o.created_at)}</td><td>{o.symbol}</td><td>{o.side}</td><td>{o.quantity}</td><td>{o.order_type}</td>
          <td className={o.status === "REJECTED" ? "negative" : ""}>{o.status}</td><td>{price(o.fill_price)}</td><td><small>{o.reject_reason ?? ""}</small></td></tr>)}</tbody></table></div>
      <Pager page={data.page} size={data.size} total={data.total} onPage={setPage} />
    </>}
  </section>;
}

function Spread({ start, end }: { start?: string; end?: string }) {
  const { data, error, loading } = useCachedJson<SpreadReport>(`/api/backoffice/spread${query({ start, end })}`);
  if (error) return <div className="agent-error" role="alert">{error}</div>;
  if (!data) return <div className="agent-empty state-loading" role="status">{loading ? "Checking every fill against its stored quote…" : "Loading…"}</div>;
  const { paper, real } = splitKinds(data.lanes);
  const row = (l: SpreadReport["lanes"][number]) => <tr key={l.lane} className={l.kind === "real" ? "bo-real" : undefined}>
    <td><b>{l.label}</b>{l.kind === "real" && <small> real money</small>}</td><td>{l.slices}</td>
    <td className={l.coverage_pct != null && l.coverage_pct < 80 ? "negative" : ""}>{l.coverage_pct == null ? "—" : `${Math.round(l.coverage_pct)}%`}</td>
    <td>{l.median_entry_spread_pct == null ? "—" : `${l.median_entry_spread_pct.toFixed(2)}%`}</td>
    <td className={tone(l.net)}>{rupees(l.net)}</td><td className={l.extra ? "negative" : ""}>{l.extra ? rupees(-l.extra) : "—"}</td>
    <td className={tone(l.net_at_quote)}><b>{rupees(l.net_at_quote)}</b></td>
    <td>{!l.covered_legs || haircutShare(l) == null ? "—" : `${Math.round(haircutShare(l)!)}%`}</td>
  </tr>;
  const months = data.lanes.flatMap((l) => l.months.map((m) => ({ ...m, lane: l.label })));
  return <>
    <section className="panel agent-panel">
      <div className="panel-title">Spread-aware second mark <small>each fill checked against its contract's stored quoted spread at that minute · {data.source}</small></div>
      <p className="agent-sub bo-note">Recorded fills are optimistic in different ways per lane (MACD, Smart, Spot, Blast, MCX buying and Agentic cross the quote only when one rode on the same tick as the price, else last price + 5 bps; MCX selling and Auction charge a fixed slippage; Vanguard/CBE fill at the displayed ask/bid). Here every entry and exit is charged half the quoted spread, less whatever the recorded fill already paid beyond the minute's last price. <b>Net at quote</b> is the lane's result had every fill crossed the quote. Fills with no stored quote within {Math.round(data.window_seconds / 60)} minutes are not charged, so read coverage beside it. Measurement only: no lane reads it.</p>
      <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Lane</th><th>Exit slices</th><th>Fills covered</th><th>Median entry spread</th><th>Recorded net</th><th>Spread haircut</th><th>Net at quote</th><th>Haircut vs |net|</th></tr></thead>
        <tbody>{paper.map(row)}{real.map(row)}</tbody></table></div>
    </section>
    <section className="panel agent-panel">
      <div className="panel-title">By month <small>newest first</small></div>
      <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Lane</th><th>Month</th><th>Exit slices</th><th>Covered</th><th>Recorded net</th><th>Haircut</th><th>Net at quote</th></tr></thead>
        <tbody>{months.map((m) => <tr key={`${m.lane}|${m.month}`}><td>{m.lane}</td><td>{periodLabel(m.month)}</td><td>{m.slices}</td>
          <td>{m.coverage_pct == null ? "—" : `${Math.round(m.coverage_pct)}%`}</td><td className={tone(m.net)}>{rupees(m.net)}</td>
          <td className={m.extra ? "negative" : ""}>{m.extra ? rupees(-m.extra) : "—"}</td><td className={tone(m.net_at_quote)}>{rupees(m.net_at_quote)}</td></tr>)}</tbody></table></div>
    </section>
  </>;
}
