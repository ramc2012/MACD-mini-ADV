import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { LedgerCounts } from "./LedgerCounts";
import { closedBookStart } from "./ledgerMath";
import { istStamp } from "./istTime";
import type { BlastBook, BlastSnapshot, ClosedPosition, Order, Portfolio, Trade } from "./types";

const money = (value: number) => `₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value)}`;
const tone = (value: number) => value < 0 ? "negative" : "positive";
const share = (value: number, total: number) => total > 0 ? `${(100 * value / total).toFixed(2)}%` : "—";
const time = (value: number) => istStamp(new Date(value).toISOString());

export function BlastStatus({ snapshot, orders, trades, ready }: { snapshot: BlastSnapshot; orders: Order[]; trades: Trade[]; ready: boolean }) {
  const { settings, portfolio, health } = snapshot;
  const hk = settings.strategy === "hma_kama";
  return <div className="status-stack blast-status">
    {portfolio.cash < 0 && <div className="settings-error" role="alert">Cash is negative: {money(portfolio.cash)}. Review book funding and reconciliation before increasing exposure.</div>}
    <section className="panel">
      <div className="panel-title"><span>Readiness</span><span>Paper book</span></div>
      <div className="blast-kv">
        <span>Strategy</span><b>{hk ? "HMA(20) / KAMA(10, 2, 30) · 30m premium" : "MACD signal-line screen"}</b>
        <span>Entry mode</span><b>{!snapshot.enabled ? "Disabled · tick exits paused" : snapshot.auto_trade ? "Automatic paper entries" : "Journal only · entries off"}</b>
        <span>Warmed contracts</span><b>{health.warmed_symbols ?? health.symbols_with_history}</b>
        <span>Signal bar</span><b>{(health.signal_bar_seconds ?? snapshot.screen.bar_seconds ?? (hk ? 1800 : 60)) / 60} minutes</b>
        <span>Last candidate since restart</span><b>{istStamp(health.last_candidate_at)}</b>
        <span>Signals / entries since restart</span><b>{health.evaluated_since_start} / {health.taken_since_start}</b>
        <span>Late bars skipped</span><b>{health.late_bars_skipped ?? "Unavailable"}</b>
        <span>Lane error</span><b>{snapshot.error ?? health.error ?? "None reported"}</b>
      </div>
    </section>
    <section className="panel">
      <div className="panel-title"><span>Exposure and risk</span><span>{portfolio.positions.length} open positions</span></div>
      <div className="blast-kv">
        <span>Cash</span><b className={portfolio.cash < 0 ? "negative" : ""}>{money(portfolio.cash)}</b>
        <span>Gross market value / equity</span><b>{money(portfolio.market_value)} / {share(portfolio.market_value, portfolio.equity)}</b>
        <span>Target entry / position limit</span><b>{money(settings.target_notional)} / {settings.max_positions}</b>
        <span>Hard stop</span><b>−{settings.hard_stop_pct * 100}% from entry</b>
        <span>Exit rule</span><b>{hk ? "HMA below KAMA · 4th session at 15:00" : `Trail ${(settings.trail_pct * 100).toFixed(1)}% from peak`}</b>
        <span>Trailing stop</span><b>{hk ? "Inactive for HMA/KAMA" : `Arms at +${settings.trail_activation_pct * 100}%`}</b>
        <span>Selection liquidity cap</span><b>{snapshot.screen.max_volume_share_pct}% × max(volume, OI / 100)</b>
        <span>Initial capital</span><b>{money(settings.initial_capital)}</b>
      </div>
      <p className="blast-note">Missing selection liquidity leaves that cap unavailable. Expiry and stock T−2 exits also apply. Marks depend on observed quotes; open P&amp;L is not a guaranteed exit value.</p>
    </section>
    <LedgerCounts orders={orders} trades={trades} ready={ready} />
  </div>;
}

export function BlastPerformance({ book, portfolio, closed, now, initialCapital, holidays }: {
  book?: BlastBook; portfolio: Portfolio; closed: ClosedPosition[]; now: number; initialCapital: number; holidays?: string[];
}) {
  const start = closedBookStart(now, new Set(holidays ?? []));
  const rows = [...closed].filter((row) => Date.parse(row.exit_time) >= start && Date.parse(row.exit_time) <= now)
    .sort((a, b) => Date.parse(a.exit_time) - Date.parse(b.exit_time));
  const net = rows.reduce((sum, row) => sum + row.realized_pnl, 0);
  const fees = rows.reduce((sum, row) => sum + row.fees, 0);
  const gross = rows.reduce((sum, row) => sum + row.gross_pnl, 0);
  const wins = rows.filter((row) => row.realized_pnl > 0);
  const losses = rows.filter((row) => row.realized_pnl < 0);
  const winPnl = wins.reduce((sum, row) => sum + row.realized_pnl, 0);
  const lossPnl = -losses.reduce((sum, row) => sum + row.realized_pnl, 0);
  let total = 0;
  const values = new Map<number, number>([[start, 0]]);
  rows.forEach((row) => { total += row.realized_pnl; values.set(Date.parse(row.exit_time), total); });
  const points = [...values].map(([at, pnl]) => ({ at, pnl }));
  return <div className="blast-performance">
    <section className="blast-metrics" aria-label="Active Blast book performance">
      {[["Closed net P&L · book day", money(net)], ["Gross / allocated fees", `${money(gross)} / ${money(fees)}`],
        ["Closed slices · wins / losses", `${rows.length} · ${wins.length} / ${losses.length}`],
        ["Win rate / profit factor", `${share(wins.length, rows.length)} / ${lossPnl ? (winPnl / lossPnl).toFixed(2) : "—"}`],
        ["Open marked P&L", money(portfolio.unrealized_pnl)], ["Active book return since funding", share(portfolio.equity - initialCapital, initialCapital)]].map(([label, value]) =>
        <div key={label}><span>{label}</span><b>{value}</b></div>)}
    </section>
    <section className="panel blast-performance-curve">
      <div className="panel-title"><span>Realized P&amp;L · active book day</span><span className={tone(net)}>{money(net)}</span></div>
      <p className="blast-note">From {time(start)} · closed slices net of allocated fees. Open marks and earlier archived books are excluded.</p>
      {rows.length ? <div className="blast-realized-chart" role="img" aria-label={`Cumulative realized P&L from ${rows.length} closed slices: ${money(net)}`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 12, right: 20, bottom: 18, left: 24 }}>
            <CartesianGrid stroke="#263144" strokeDasharray="3 3" />
            <XAxis dataKey="at" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(at: number) => time(at).slice(11, 16)} tick={{ fill: "#8796aa" }} minTickGap={40} />
            <YAxis tickFormatter={(value: number) => money(value)} width={105} tick={{ fill: "#8796aa" }} />
            <Tooltip labelFormatter={(at) => time(Number(at))} formatter={(value) => [money(Number(value)), "Closed net P&L"]} contentStyle={{ background: "#0c111a", border: "1px solid #263144", color: "#dbe5f2" }} />
            <Line type="stepAfter" dataKey="pnl" stroke="#4da3ff" strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div> : <p className="blast-empty">No recorded exits in this book day.</p>}
    </section>
    <p className="blast-note">{rows.length > 0 && fees === 0 ? "Recorded charges are zero; these results exclude unrecorded brokerage and statutory fees. " : ""}Open P&amp;L covers each position's holding period. Active book return compares current equity with configured funding; it is not today's equity change. {book ? book.equity.length ? "Recorded equity samples exist in the active book." : "No equity-history samples are recorded in the active book; today's equity change is unavailable." : "Loading the active book…"}</p>
  </div>;
}
