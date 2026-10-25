"use client";

/**
 * One-line performance + trade-book surface for any desk.
 *
 * <PaperPerformance summary={paperSummary} positions={paperPositions} />
 *
 * Renders the KPI strip, equity/monthly/R charts, and the open/closed
 * trade book from the canonical paper payloads. This is what every desk's
 * "Performance" / "Trade book" tab should mount.
 */
import {
  type PaperSummary,
  type PositionsPayload,
} from "@/lib/strategy-stats";
import { StrategyStats } from "./StrategyStats";
import { PerformanceCharts } from "./PerformanceCharts";
import { TradeBook } from "./TradeBook";

export function PaperPerformance({
  summary,
  positions,
  showStats = true,
  showCharts = true,
  showTradeBook = true,
}: {
  summary?: PaperSummary;
  positions?: PositionsPayload;
  showStats?: boolean;
  showCharts?: boolean;
  showTradeBook?: boolean;
}) {
  const open = positions?.open_positions ?? [];
  const closed = positions?.closed_positions ?? [];
  const sum = summary ?? positions?.summary;

  return (
    <div className="space-y-4">
      {showStats ? <StrategyStats summary={sum} closed={closed} /> : null}
      {showCharts ? (
        <PerformanceCharts closed={closed} initialCapital={sum?.initial_capital ?? 0} />
      ) : null}
      {showTradeBook ? <TradeBook open={open} closed={closed} /> : null}
    </div>
  );
}
