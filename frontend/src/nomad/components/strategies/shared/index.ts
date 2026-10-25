/**
 * Shared strategy-desk components — the reusable building blocks every
 * /strategies/<lane> desk composes from (KPI strip, performance charts,
 * trade book). Import from "@/components/strategies/shared".
 */
export { StrategyStats } from "./StrategyStats";
export { TradeBook } from "./TradeBook";
export { PerformanceCharts } from "./PerformanceCharts";
export { PaperPerformance } from "./PaperPerformance";
export { MarketProfileChart, normalizeTpo, type TpoLevel } from "./MarketProfileChart";
export { OrderFlowPanel, type OrderFlow } from "./OrderFlowPanel";
export { CandleChart, type CandleBar, type ChartPriceLine, type ChartLineSeries } from "./CandleChart";
export { GammaDensity } from "./GammaDensity";
export { CHART, pnlColor } from "./chartTheme";
export { RrgScatter, QUADRANT_COLOR, quadrantColor, type RrgScatterPoint } from "./RrgScatter";
export { OptionChartModal, type OptionChartContract } from "./OptionChartModal";
export { OptionStudyChart, type StudyBar, type StudyLine } from "./OptionStudyChart";
