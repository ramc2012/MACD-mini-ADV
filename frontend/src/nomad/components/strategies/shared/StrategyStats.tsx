"use client";

/**
 * Strategy KPI strip — the canonical performance header for every desk.
 *
 * Consumes the lane's /paper-summary plus the closed-position list and
 * renders a uniform grid of MetricTiles: equity, net P&L, win rate,
 * profit factor, expectancy, drawdown/Sharpe, avg win/loss, trade counts.
 */
import {
  MetricTile,
  formatMoney,
  formatNumber,
  formatPct,
  formatSignedMoney,
  tone,
} from "@/components/desk-ui";
import {
  type PaperPosition,
  type PaperSummary,
  deriveTradeStats,
} from "@/lib/strategy-stats";

export function StrategyStats({
  summary,
  closed = [],
  dense = false,
}: {
  summary?: PaperSummary;
  closed?: PaperPosition[];
  dense?: boolean;
}) {
  const s = summary || {};
  const stats = deriveTradeStats(closed);
  const net = (s.realized_pnl ?? 0) + (s.unrealized_pnl ?? 0);
  const pf = stats.profitFactor;
  const winRate = s.win_rate ?? stats.winRate;
  const winTone =
    winRate >= 0.5 ? "text-accent-green" : winRate > 0 ? "text-text-primary" : "text-text-muted";

  const size = dense ? "sm" : "md";

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
      <MetricTile size={size} label="Equity" value={formatMoney(s.total_equity)} detail={`Return ${formatPct(s.total_return_pct, 2, { asPercent: true })}`} color={tone(s.total_return_pct)} />
      <MetricTile size={size} label="Net P&L" value={formatSignedMoney(net)} detail={`Real ${formatSignedMoney(s.realized_pnl)} · uPnL ${formatSignedMoney(s.unrealized_pnl)}`} color={tone(net)} />
      <MetricTile size={size} label="Win rate" value={formatPct(winRate)} detail={`${stats.wins}W · ${stats.losses}L`} color={winTone} />
      <MetricTile size={size} label="Profit factor" value={pf === Infinity ? "∞" : formatNumber(pf, 2)} detail={stats.avgR != null ? `Avg ${formatNumber(stats.avgR, 2)}R` : "—"} color={tone(pf - 1)} />
      <MetricTile size={size} label="Expectancy" value={formatSignedMoney(stats.expectancy)} detail="per trade" color={tone(stats.expectancy)} />
      <MetricTile size={size} label="Max DD · Sharpe" value={formatPct(s.max_drawdown)} detail={`Sharpe ${formatNumber(s.sharpe_ratio, 2)}`} />
      <MetricTile size={size} label="Avg win" value={formatSignedMoney(stats.avgWin)} detail={`Best ${formatSignedMoney(stats.bestPnl)}`} color={tone(stats.avgWin)} />
      <MetricTile size={size} label="Avg loss" value={formatSignedMoney(stats.avgLoss)} detail={`Worst ${formatSignedMoney(stats.worstPnl)}`} color={tone(stats.avgLoss)} />
      <MetricTile size={size} label="Trades" value={String(s.total_trades ?? stats.count)} detail={`${s.open_positions ?? 0} open · ${s.closed_positions ?? stats.count} closed`} />
      <MetricTile size={size} label="Avg hold" value={stats.avgHoldHours != null ? `${formatNumber(stats.avgHoldHours, 1)}h` : "—"} detail="entry → exit" />
      <MetricTile size={size} label="Capital free" value={formatMoney(s.available_capital)} detail={`Reserved ${formatMoney(s.reserved_margin)}`} />
      <MetricTile size={size} label="Gross" value={formatSignedMoney(stats.net)} detail={`+${formatMoney(stats.grossWin)} / -${formatMoney(stats.grossLoss)}`} color={tone(stats.net)} />
    </div>
  );
}
