import {dataScope} from "@/lib/api";
import {MarketContextPanel} from '@/MarketContextPanel';
import {LaneCandidates} from '@/LaneCandidates';
import {ShadowPortfolio,ShadowFollowup} from "@/ShadowPanels";
"use client";

/**
 * CBE Scanner desk — native v2.
 *
 * The Compression-Before-Expansion scanner runs a weekly alpha engine
 * (MACD + RSI + Relative-Rotation-Graph) over the NSE F&O universe and
 * paper-trades the top-N ranked names. This desk surfaces the scan output
 * end-to-end: cross-asset rotation, sector rotation, the stock-level RRG
 * scatter, the ranked alpha-candidate book, and the paper portfolio.
 *
 * Tabs:
 *   rotation     → cross-asset winner + sector-rotation ladder + RRG quadrant census
 *   candidates   → RRG scatter (signature viz) + ranked alpha-candidate table (sparklines)
 *   sectors      → sector winners vs Nifty50 (RS%, quadrant, leaders)
 *   performance  → PaperPerformance (equity curve, R-dist, trade book) from CBE paper endpoints
 *
 * Data: /api/cbe/latest (alpha_engine scan), /api/cbe/paper-summary,
 * /api/cbe/paper-positions. Scans are produced by the backend paper-agent
 * cycle (POST /api/cbe/scan) — this desk reads the latest persisted scan.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Briefcase, Compass, Layers3, Radar, Scale, ShieldCheck, Target, TrendingUp } from "lucide-react";

import {
  DeskShell,
  TransportBadge,
  MetricTile,
  REFRESH_MS,
  Section,
  StatusBadge,
  formatNumber,
  formatMoney,
  formatPct,
  formatIST,
  formatSignedMoney,
  tone,
  useUrlTab,
} from "@/components/desk-ui";
import { PaperPerformance } from "@/components/strategies/shared";
import { SignalQualityTab } from "@/components/strategies/overview/SignalQualityTab";
import { StrategyLiveStream } from "@/components/strategies/shared/StrategyLiveStream";
import { useStrategyPositionsStream, selectStrategySlice } from "@/hooks/useStrategyPositionsStream";
import type { PaperPosition, PositionsPayload } from "@/lib/strategy-stats";
import { api as apiClient } from "@/lib/api";
import { LastUpdated, newestTimestamp } from "@/components/common/LastUpdated";
import { LiveMarkCell } from "@/components/terminal/LiveMarkCell";
import { rowTapeSymbol } from "@/lib/marketSymbols";

import { RrgScatter, QUADRANT_COLOR, type RrgPoint } from "./RrgScatter";
import { SectorRotation, type SectorRow } from "./SectorRotation";
import { Sparkline } from "@/components/desk-ui";

const TABS = [
  { key: "portfolio", label: "Paper trading", icon: Briefcase },
  { key: "market", label: "Market", icon: Layers3 },
  { key: "rotation", label: "Rotation", icon: Radar },
  { key: "watchlist", label: "Watchlist", icon: Target },
  { key: "journal", label: "Journal", icon: Activity },
  { key: "candidates", label: "Candidates", icon: Compass },
  { key: "sectors", label: "Sectors", icon: Layers3 },
  { key: "performance", label: "Performance", icon: TrendingUp },
  { key: "signal-quality", label: "Signal quality", icon: ShieldCheck },
  { key: "live-stream", label: "Live stream", icon: Activity },
];

// ── Scan payload types (from /api/cbe/latest, source=alpha_engine) ──────────
type MacdMeta = { line?: number; signal?: number; cross_today?: boolean; label?: string };
type RsiMeta = { rsi?: number; label?: string };

type ScanResult = {
  instrument: string;
  composite_score?: number | null;
  composite_alpha_score?: number | null;
  directional_bias?: string | null;
  bias_conviction?: number | null;
  is_watchlist?: boolean | null;
  gate_passed?: boolean | null;
  sector_code?: string | null;
  sector_quadrant?: string | null;
  sector_rs_pct?: number | null;
  stock_quadrant?: string | null;
  stock_rs_pct?: number | null;
  stock_rank_in_sector?: number | null;
  macd_line?: number | null;
  macd_signal?: number | null;
  macd_hist?: number | null;
  macd_bullish?: boolean | null;
  macd_score?: number | null;
  macd_meta?: MacdMeta | null;
  rsi_14?: number | null;
  rsi_score?: number | null;
  rsi_meta?: RsiMeta | null;
  weekly_close_vs_ema20?: number | null;
  weekly_trend?: string | null;
  latest_close?: number | null;
  recent_closes_30d?: number[] | null;
  details?: {
    stock_selection?: { momentum_fast?: number; momentum_slow?: number; relative_momentum?: number; reason?: string; direction?: string };
    components?: { rsi?: number; macd?: number; asset?: number; stock?: number; sector?: number };
  } | null;
};

type ScanPayload = {
  shadow_only?: boolean;
  id?: string;
  source?: string;
  scan_date?: string | null;
  signal_session_date?: string | null;
  created_at?: string | null;
  source_as_of?: string | null;
  universe_size?: number;
  scored_count?: number;
  watchlist_count?: number;
  asset_winner?: string | null;
  equity_exposure_pct?: number | null;
  config?: {
    timeframe?: string;
    sectors_to_keep?: number;
    top_n_watchlist?: number;
    low_conviction_floor?: number;
  };
  results?: ScanResult[];
  watchlist?: ScanResult[];
};

type PaperSummary = {
  shadow_only?: boolean;
  admissions_blocked?: boolean;
  selection_performance?: { closed_count?: number; open_count?: number; realized_pnl?: number };
  performance_note?: string;
  prospective_performance?: { cohort?: string; closed_count?: number; open_count?: number; realized_pnl?: number };
  open_positions?: number;
  closed_positions?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  total_pnl?: number;
  initial_capital?: number;
  available_capital?: number;
  reserved_margin?: number;
  total_equity?: number;
  total_return_pct?: number;
  win_rate?: number;
  total_trades?: number;
  long_positions?: number;
  short_positions?: number;
  long_exposure?: number;
  short_exposure?: number;
  gross_exposure?: number;
  net_exposure?: number;
  gross_exposure_ratio?: number;
  net_exposure_ratio?: number;
  largest_position_exposure?: number;
  largest_position_ratio?: number;
  concentration_top3_ratio?: number;
  risk_budget_usage_ratio?: number;
  book_bias?: string;
  risk_flags?: string[];
  sector_exposures?: SectorExposure[];
  mandate?: HedgeMandate;
  current_drawdown?: number;
  max_drawdown?: number;
  transaction_costs?: number;
  estimated_open_costs?: number;
};

type MarkedCbePosition = PaperPosition & {
  current_price?: number | null;
  current_value?: number | null;
  current_pnl?: number | null;
  current_pnl_pct?: number | null;
  mark_source?: string;
  mark_scan_date?: string | null;
};

type SectorExposure = {
  sector: string;
  long_exposure?: number;
  short_exposure?: number;
  gross_exposure?: number;
  net_exposure?: number;
  gross_exposure_ratio?: number;
  names?: number;
};

type HedgeMandate = {
  strategy?: string;
  max_gross_exposure_ratio?: number;
  max_net_exposure_ratio?: number;
  max_single_name_ratio?: number;
  max_sector_exposure_ratio?: number;
  rebalance?: string;
  min_hold_trading_days?: number;
  hard_stop_loss_pct?: number;
  rebalance_drift_ratio?: number;
  execution_model?: string;
};

const DEFAULT_MANDATE: Required<Omit<HedgeMandate, "strategy">> = {
  max_gross_exposure_ratio: 1,
  max_net_exposure_ratio: 0.4,
  max_single_name_ratio: 0.1,
  max_sector_exposure_ratio: 0.3,
  rebalance: "weekly",
  min_hold_trading_days: 5,
  hard_stop_loss_pct: 0.05,
  rebalance_drift_ratio: 0.05,
  execution_model: "cash_longs_and_single_stock_futures_proxy_shorts",
};

const biasVariant = (b?: string | null) =>
  b === "bullish" ? "success" : b === "bearish" ? "error" : "neutral";
const trendTone = (t?: string | null) =>
  t === "up" ? "text-accent-green" : t === "down" ? "text-accent-red" : undefined;

export default function CbeDesk() {
  const isShadow = dataScope() === "shadow";
  // Hedge-fund portfolio construction is the headline view when the desk opens.
  const [activeTab, setActiveTab] = useUrlTab(isShadow ? "market" : "portfolio");

  const latestQuery = useQuery({
    queryKey: ["cbe", "latest"],
    queryFn: async () => (await apiClient.get("/api/cbe/latest")).data as ScanPayload,
    // Live-market page → keep the header freshness poll at ≤30s.
    refetchInterval: REFRESH_MS.snapshot,
    refetchOnWindowFocus: false,
  });

  const paperSummaryQuery = useQuery({
    queryKey: ["cbe", "paper-summary"],
    queryFn: async () => (await apiClient.get("/api/cbe/paper-summary")).data as PaperSummary,
    refetchInterval: REFRESH_MS.snapshot,
    refetchOnWindowFocus: false,
  });

  const paperPositionsQuery = useQuery({
    queryKey: ["cbe", "paper-positions"],
    queryFn: async () =>
      (await apiClient.get("/api/cbe/paper-positions", { params: { status: "all", limit: 500 } }))
        .data as PositionsPayload & { last_synced_at?: string },
    refetchInterval: REFRESH_MS.snapshot,
    refetchOnWindowFocus: false,
  });

  const journalQuery = useQuery({
    queryKey: ["cbe", "paper-journal"],
    queryFn: async () => (await apiClient.get("/api/cbe/paper-journal", {params:{limit:500}})).data as {count:number;records:Record<string, unknown>[]},
    enabled: activeTab === "journal", refetchInterval: REFRESH_MS.snapshot,
  });
  const scan = latestQuery.data;
  const results = useMemo(() => scan?.results ?? [], [scan?.results]);
  const watchlist = scan?.watchlist ?? [];

  // Live open-positions stream (shared /ws/positions-overview channel); active
  // on the portfolio/performance tabs, falls back to CBE's dedicated endpoints.
  const posStream = useStrategyPositionsStream({ enabled: activeTab === "portfolio" || activeTab === "performance" });
  const streamSlice = selectStrategySlice(posStream.data, "cbe");
  const streamLive = posStream.isStreamConnected && Boolean(streamSlice);
  const paperSum = (streamLive ? streamSlice?.summary : paperSummaryQuery.data) as PaperSummary | undefined;
  const positions = (streamLive ? streamSlice : paperPositionsQuery.data) as PositionsPayload | undefined;

  const spotMarks = useMemo(() => buildSpotMarkMap(results), [results]);
  const markedOpenBook = useMemo(
    () => markOpenPositions(positions?.open_positions ?? [], spotMarks, scan?.scan_date ?? null),
    [positions?.open_positions, scan?.scan_date, spotMarks],
  );
  const markedPositions = useMemo<PositionsPayload | undefined>(
    () =>
      positions
        ? {
            ...positions,
            open_positions: markedOpenBook,
            summary: markPaperSummary(positions.summary ?? paperSum, markedOpenBook),
          }
        : positions,
    [markedOpenBook, paperSum, positions],
  );
  const markedPaperSum = useMemo(
    () => markPaperSummary(paperSum ?? positions?.summary, positions?.open_positions ? markedOpenBook : undefined),
    [markedOpenBook, paperSum, positions?.open_positions, positions?.summary],
  );

  // RRG scatter points — every scored name, x=stock RS%, y=MACD histogram.
  const rrgPoints = useMemo<RrgPoint[]>(
    () =>
      results
        .filter((r) => r.stock_quadrant && r.stock_quadrant !== "unclassified")
        .map((r) => ({
          symbol: r.instrument,
          rs: r.stock_rs_pct ?? 0,
          momentum: r.macd_hist ?? 0,
          score: r.composite_alpha_score ?? 0,
          quadrant: r.stock_quadrant ?? "lagging",
          watchlist: !!r.is_watchlist,
        })),
    [results],
  );

  // Sector rotation ladder — collapse results into the distinct sectors.
  const sectors = useMemo<SectorRow[]>(() => {
    const map = new Map<string, SectorRow>();
    for (const r of results) {
      const code = r.sector_code;
      if (!code) continue;
      const row = map.get(code) ?? {
        code,
        quadrant: r.sector_quadrant ?? "lagging",
        rs: r.sector_rs_pct ?? 0,
        count: 0,
        leaders: 0,
      };
      row.count += 1;
      if (r.stock_quadrant === "leading") row.leaders += 1;
      map.set(code, row);
    }
    return Array.from(map.values());
  }, [results]);

  // Stock RRG quadrant census for the KPI strip.
  const census = useMemo(() => {
    const c = { leading: 0, improving: 0, weakening: 0, lagging: 0 };
    for (const r of results) {
      const q = r.stock_quadrant as keyof typeof c;
      if (q in c) c[q] += 1;
    }
    return c;
  }, [results]);

  const ranked = useMemo(
    () => [...results].sort((a, b) => (b.composite_alpha_score ?? 0) - (a.composite_alpha_score ?? 0)),
    [results],
  );

  const totalReturn = markedPaperSum?.total_return_pct ?? null;
  const openBook = markedOpenBook;
  const mandate = markedPaperSum?.mandate ?? DEFAULT_MANDATE;
  const riskFlags = markedPaperSum?.risk_flags ?? [];

  return (
    <DeskShell
      showStatus={!isShadow}
      title="CBE Paper Trader"
      description={isShadow ? "Daily trend cash-equity book · Hold for weeks to months; no same-day time exit, profit target or maximum holding period." : "Retained Nomad CBE account · recorded positions and history, read only."}
      asOf={scan?.source_as_of ?? scan?.created_at ?? scan?.scan_date ?? undefined}
      asOfLabel={isShadow ? "completed daily session" : "Updated"}
      // CBE scans land once per session (EOD alpha-engine pass) — freshness
      // thresholds follow that cadence, not the intraday default.
      asOfStaleSeconds={30 * 3600}
      asOfCriticalSeconds={54 * 3600}
      isFetching={latestQuery.isFetching}
      paperMode
      tabs={scan?.config?.timeframe === "daily" ? TABS.filter(t => !["rotation", "sectors"].includes(t.key)) : TABS}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      v1Href="http://localhost:3000/cbe"
      rightSlot={
        <div className="flex items-center gap-2">
          {activeTab === "performance" && !isShadow ? (
            <TransportBadge connected={streamLive} />
          ) : null}
          <StatusBadge
            label={`signal ${scan?.signal_session_date ?? scan?.scan_date ?? "—"}`}
            variant="info"
            icon={<Activity size={12} />}
          />
          <StatusBadge
            label={`${scan?.watchlist_count ?? 0} watchlist`}
            variant={scan?.watchlist_count ? "success" : "neutral"}
          />
          <StatusBadge
            label={riskFlags.includes("inside_hedge_mandate") ? "inside mandate" : `${riskFlags.length} risk flags`}
            variant={riskFlags.includes("inside_hedge_mandate") || !riskFlags.length ? "success" : "warn"}
            icon={<ShieldCheck size={12} />}
          />
          <StatusBadge label={isShadow ? "paper execution" : "retained history"} variant="warn" />
        </div>
      }
    >
      {paperSum?.performance_note && (
        <Section title="Execution evidence">
          <p className="text-xs text-text-muted">{paperSum.performance_note}</p>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <MetricTile label="Daily stock-selection closes" value={String(paperSum.selection_performance?.closed_count ?? 0)} detail={`${paperSum.selection_performance?.open_count ?? 0} open · new policy measured separately`} />
            <MetricTile label="Daily stock-selection realized P&L" value={formatMoney(paperSum.selection_performance?.realized_pnl ?? 0)} />
          </div>
        </Section>
      )}
      {/* ── Portfolio ───────────────────────────────────────────────────── */}
      {activeTab === "portfolio" && isShadow && <ShadowPortfolio lane="cbe"/>}
      {activeTab === "portfolio" && !isShadow ? (
        <div className="space-y-4">
          <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <MetricTile label="Paper equity" value={formatMoney(markedPaperSum?.total_equity)} />
            <MetricTile label="Net realized P&L" value={formatSignedMoney(markedPaperSum?.realized_pnl)} />
            <MetricTile label="Open / closed trades" value={`${markedPaperSum?.open_positions ?? 0} / ${markedPaperSum?.closed_positions ?? 0}`} />
            <MetricTile label="Selected watchlist" value={String(watchlist.length)} detail="Fresh prices and book limits required" />
          </section>
          <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            <MetricTile label="Gross" value={formatPct(markedPaperSum?.gross_exposure_ratio, 1)} detail={formatMoney(markedPaperSum?.gross_exposure)} />
            <MetricTile label="Net" value={formatSignedPct(markedPaperSum?.net_exposure_ratio, 1)} detail={bookBiasLabel(markedPaperSum?.book_bias)} color={tone(markedPaperSum?.net_exposure)} />
            <MetricTile label="Long sleeve" value={formatMoney(markedPaperSum?.long_exposure)} detail={`${markedPaperSum?.long_positions ?? 0} names`} color="text-accent-green" />
            <MetricTile label="Legacy short holdings" value={formatMoney(markedPaperSum?.short_exposure)} detail={`${markedPaperSum?.short_positions ?? 0} names`} color="text-accent-red" />
            <MetricTile label="Top 3" value={formatPct(markedPaperSum?.concentration_top3_ratio, 1)} detail="name concentration" />
            <MetricTile label="Equity budget" value={formatPct((scan?.equity_exposure_pct ?? 100) / 100, 0)} detail={`asset ${scan?.asset_winner ?? "—"}`} color="text-accent-amber" />
          </section>

          <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
            <Section
              title="Portfolio limits"
              icon={<ShieldCheck size={16} />}
              description="Limits for new daily long-only positions. Legacy short holdings keep their recorded management."
              rightSlot={
                <StatusBadge
                  label={riskFlags.includes("inside_hedge_mandate") || !openBook.length ? "clear" : "review"}
                  variant={riskFlags.includes("inside_hedge_mandate") || !openBook.length ? "success" : "warn"}
                />
              }
            >
              <MandatePanel summary={markedPaperSum} mandate={mandate} />
            </Section>

            <Section
              title="Sector exposure"
              icon={<Layers3 size={16} />}
              description="Gross exposure by sector, with long/short netting shown separately."
            >
              <SectorExposurePanel sectors={markedPaperSum?.sector_exposures ?? []} maxSector={mandate.max_sector_exposure_ratio ?? DEFAULT_MANDATE.max_sector_exposure_ratio} />
            </Section>
          </div>

          <Section
            title="Long/short sleeves"
            icon={<Scale size={16} />}
            description="Open CBE book by side, sorted by notional risk."
            rightSlot={
              <LastUpdated
                label="Marked"
                timestamp={newestTimestamp([
                  paperPositionsQuery.data?.last_synced_at,
                  ...openBook.map((p) => p.mark_time),
                ])}
                staleAfterSeconds={120}
                criticalAfterSeconds={900}
              />
            }
          >
            <div className="grid gap-3 xl:grid-cols-2">
              <SleeveTable title="Long book" side="long" positions={openBook} summary={markedPaperSum} />
              <SleeveTable title="Short book" side="short" positions={openBook} summary={markedPaperSum} />
            </div>
          </Section>


        </div>
      ) : null}

      {/* ── Rotation ─────────────────────────────────────────────────────── */}
      {activeTab === "rotation" ? (
        <div className="space-y-4">
          <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            <MetricTile label="Asset winner" value={scan?.asset_winner ?? "—"} detail="cross-asset rotation" color="text-accent-amber" />
            <MetricTile label="Universe" value={String(scan?.universe_size ?? 0)} detail={`${scan?.scored_count ?? 0} scored`} />
            <MetricTile label="Watchlist" value={String(scan?.watchlist_count ?? 0)} detail={`top-${scan?.config?.top_n_watchlist ?? "N"} gate`} />
            <MetricTile label="Leading" value={String(census.leading)} detail="stocks" color="text-accent-green" />
            <MetricTile label="Improving" value={String(census.improving)} detail="stocks" color="text-accent-blue" />
            <MetricTile label="Lagging" value={String(census.lagging + census.weakening)} detail="weak + lagging" color="text-accent-red" />
          </section>

          <Section
            title="Sector rotation vs Nifty50"
            icon={<Radar size={16} />}
            description="Relative strength ladder — where capital is rotating across sectors this week."
            rightSlot={<StatusBadge label={`timeframe ${scan?.config?.timeframe ?? "weekly"}`} variant="neutral" />}
          >
            <SectorRotation sectors={sectors} />
          </Section>

          <Section
            title="Cross-asset & quadrant census"
            icon={<Compass size={16} />}
            description="Asset-class winner drives the equity tilt; the stock-quadrant census shows breadth."
          >
            <div className="grid gap-3 md:grid-cols-4">
              <QuadrantCard q="leading" n={census.leading} note="strong + accelerating" />
              <QuadrantCard q="improving" n={census.improving} note="weak but accelerating" />
              <QuadrantCard q="weakening" n={census.weakening} note="strong but decelerating" />
              <QuadrantCard q="lagging" n={census.lagging} note="weak + decelerating" />
            </div>
          </Section>
        </div>
      ) : null}

      {activeTab === "market" && <MarketContextPanel lane="cbe"/>}
      {activeTab === "watchlist" && isShadow && <LaneCandidates lane="cbe"/>}
      {activeTab === "watchlist" && !isShadow && <Section title="Qualified long watchlist" description="Selected names from the latest persisted CBE scan. Selection does not guarantee a fill: fresh execution prices, available cash and concentration limits still apply.">
        <p className="mb-3 text-xs text-text-muted">{watchlist.length} selected · signal session {scan?.signal_session_date ?? scan?.scan_date ?? "pending"} · {paperSum?.admissions_blocked ? "New entries blocked: held quotes need recovery" : "Paper admission follows book limits"}</p>
        <CandidateTable rows={watchlist}/>
      </Section>}
      {activeTab === "journal" && <Section title={isShadow ? "CBE paper journal" : "Retained CBE execution journal"} description="Persisted paper entries and exits, including quantities, costs and reasons. Candidate scans are separate from this financial record.">
        {journalQuery.isLoading ? <p>Loading journal…</p> : journalQuery.isError ? <p role="alert">Journal could not be loaded.</p> : <>
          <p className="mb-3 text-xs text-text-muted">{journalQuery.data?.count ?? 0} recorded events · newest 500 shown</p>
          {!journalQuery.data?.records.length && <p>No paper execution events recorded yet.</p>}
          <div className="max-h-[650px] overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{["Recorded","Stock","Event / side","Quantity","Entry / exit","Net realized","Reason"].map(t=><th className="p-2" key={t}>{t}</th>)}</tr></thead>
          <tbody>{journalQuery.data?.records.map((r,i)=><tr key={i} className="border-t border-bg-border"><td className="p-2">{formatIST(r.recorded_at as string)}</td><td className="p-2">{String(r.instrument ?? "—")}</td><td className="p-2">{String(r.event ?? "—")} · {String(r.direction ?? "—")}</td><td className="p-2">{String(r.quantity ?? "—")}</td><td className="p-2">{formatMoney(r.entry_price as number)} / {formatMoney(r.exit_price as number)}</td><td className="p-2">{formatSignedMoney(r.realized_pnl as number)}</td><td className="p-2">{String(r.close_reason ?? r.entry_reason ?? "Paper entry")}</td></tr>)}</tbody></table></div>
        </>}
      </Section>}

      {/* ── Candidates ───────────────────────────────────────────────────── */}
      {activeTab === "candidates" ? (
        <div className="space-y-4">
          {scan?.config?.timeframe !== "daily" ? <Section
            title="Relative-Rotation Graph"
            icon={<Radar size={16} />}
            description="Each scored name plotted by relative strength (x) and MACD-histogram momentum (y). Ringed dots are on the watchlist."
          >
            <RrgScatter points={rrgPoints} />
          </Section> : null}

          <Section
            title="All stock candidates"
            icon={<TrendingUp size={16} />}
            description="The full evaluated universe, including rejected and neutral names. Only selected longs advance to the separate watchlist."
            rightSlot={<StatusBadge label={`${ranked.length} evaluated`} variant="neutral" />}
          >
            <CandidateTable rows={ranked} />
          </Section>


        </div>
      ) : null}

      {/* ── Sectors ──────────────────────────────────────────────────────── */}
      {activeTab === "sectors" ? (
        <div className="space-y-4">
          <Section
            title="Sector winners vs Nifty50"
            icon={<Layers3 size={16} />}
            description="Distinct sectors with RRG quadrant, relative strength, and the count of leading stocks within each."
          >
            <SectorTable sectors={[...sectors].sort((a, b) => b.rs - a.rs)} />
          </Section>
          <Section title="Sector rotation ladder" icon={<Radar size={16} />}>
            <SectorRotation sectors={sectors} />
          </Section>
        </div>
      ) : null}

      {/* ── Performance ──────────────────────────────────────────────────── */}
      {activeTab === "performance" && isShadow && <ShadowFollowup lane="cbe"/>}
      {activeTab === "performance" && !isShadow ? (
        <div className="space-y-4">
          <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            <MetricTile label="Open book" value={String(markedPaperSum?.open_positions ?? 0)} detail={`${markedPaperSum?.closed_positions ?? 0} closed`} />
            <MetricTile label="Total P&L" value={formatSignedMoney(markedPaperSum?.total_pnl)} detail={`real ${formatSignedMoney(markedPaperSum?.realized_pnl)}`} color={tone(markedPaperSum?.total_pnl)} />
            <MetricTile label="Unrealized" value={formatSignedMoney(markedPaperSum?.unrealized_pnl)} color={tone(markedPaperSum?.unrealized_pnl)} />
            <MetricTile label="Equity" value={formatSignedMoney(markedPaperSum?.total_equity)} detail={`init ${formatSignedMoney(markedPaperSum?.initial_capital)}`} />
            <MetricTile label="Return" value={totalReturn != null ? formatPct(totalReturn / 100, 2) : "—"} color={tone(totalReturn)} />
            <MetricTile label="Max drawdown" value={formatPct(markedPaperSum?.max_drawdown, 2)} detail={`current ${formatPct(markedPaperSum?.current_drawdown, 2)}`} color="text-accent-red" />
          </section>
          <PaperPerformance summary={markedPaperSum} positions={markedPositions} />
        </div>
      ) : null}
      {activeTab === "signal-quality" ? (
        <SignalQualityTab laneKeys={["cbe_scanner", "cbe_marks"]} title="CBE signal validation" />
      ) : null}
      {activeTab === "live-stream" ? (
        <StrategyLiveStream
          title="CBE Scanner"
          watchlist={watchlist.map((row) => ({ symbol: row.instrument }))}
          positionSources={["cbe"]}
        />
      ) : null}
    </DeskShell>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

type SpotMark = {
  symbol: string;
  latestClose?: number | null;
  sectorCode?: string | null;
  sectorQuadrant?: string | null;
  stockQuadrant?: string | null;
  alphaScore?: number | null;
  biasConviction?: number | null;
};

function finiteNumber(value: unknown): number | null {
  if (value == null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function buildSpotMarkMap(rows: ScanResult[]): Map<string, SpotMark> {
  const map = new Map<string, SpotMark>();
  for (const row of rows) {
    const symbol = String(row.instrument || "").trim().toUpperCase();
    if (!symbol) continue;
    map.set(symbol, {
      symbol,
      latestClose: finiteNumber(row.latest_close),
      sectorCode: row.sector_code,
      sectorQuadrant: row.sector_quadrant,
      stockQuadrant: row.stock_quadrant,
      alphaScore: finiteNumber(row.composite_alpha_score),
      biasConviction: finiteNumber(row.bias_conviction),
    });
  }
  return map;
}

function cbeQuantity(position: PaperPosition): number {
  return finiteNumber(position.quantity ?? position.quantity_units ?? position.qty) ?? 0;
}

function cbeEntryPrice(position: PaperPosition): number {
  return finiteNumber(position.entry_price ?? position.entry_premium) ?? 0;
}

function cbeMarkPrice(position: PaperPosition): number {
  return (
    finiteNumber(position.current_price) ??
    finiteNumber(position.latest_close) ??
    finiteNumber(position.latest_premium) ??
    finiteNumber(position.entry_price) ??
    finiteNumber(position.entry_premium) ??
    0
  );
}

function cbeSideSign(position: PaperPosition): number {
  return cbeDirection(position) === "short" ? -1 : 1;
}

function markOpenPositions(
  positions: PaperPosition[],
  spotMarks: Map<string, SpotMark>,
  scanDate?: string | null,
): MarkedCbePosition[] {
  return positions.map((position) => {
    const symbol = cbeSymbol(position).toUpperCase();
    const spot = spotMarks.get(symbol);
    const entry = cbeEntryPrice(position);
    const qty = cbeQuantity(position);
    const scanMark = finiteNumber(spot?.latestClose);
    const mark = scanMark ?? cbeMarkPrice(position);
    const entryValue = entry * qty;
    const currentValue = mark * qty;
    const sideSign = cbeSideSign(position);
    const slippageBps = finiteNumber(position.slippage_bps) ?? 0;
    const costBps = finiteNumber(position.transaction_cost_bps_per_leg) ?? 0;
    const adverseExit = mark * (sideSign > 0 ? 1 - slippageBps / 10_000 : 1 + slippageBps / 10_000);
    const grossCurrentPnl = (adverseExit - entry) * qty * sideSign;
    const estimatedCosts = (Math.abs(entry) + Math.abs(adverseExit)) * qty * costBps / 10_000;
    const currentPnl = grossCurrentPnl - estimatedCosts;
    const currentPnlPct = entryValue > 0 ? currentPnl / entryValue : null;

    return {
      ...position,
      underlying: symbol,
      trading_symbol: symbol,
      sector_code: spot?.sectorCode ?? position.sector_code,
      sector_quadrant: spot?.sectorQuadrant ?? position.sector_quadrant,
      stock_quadrant: spot?.stockQuadrant ?? position.stock_quadrant,
      confidence: finiteNumber(position.confidence ?? spot?.biasConviction ?? position.bias_conviction),
      alpha_score: finiteNumber(position.alpha_score ?? spot?.alphaScore),
      latest_alpha_score: finiteNumber(position.latest_alpha_score ?? spot?.alphaScore),
      entry_premium: entry,
      latest_premium: mark,
      latest_close: mark,
      quantity_units: qty,
      notional: finiteNumber(position.notional) ?? entryValue,
      current_price: mark,
      current_value: currentValue,
      current_pnl: currentPnl,
      current_pnl_pct: currentPnlPct,
      unrealized_pnl: currentPnl,
      mark_source: scanMark != null ? "scan spot" : "paper mark",
      mark_scan_date: scanMark != null ? scanDate ?? null : position.mark_time ?? null,
    };
  });
}

function markPaperSummary(summary: PaperSummary | undefined, open?: MarkedCbePosition[]): PaperSummary | undefined {
  if (!open) return summary;
  if (!summary && !open.length) return summary;
  const openPositions = open;
  const base = summary ?? {};
  const mandate = base.mandate ?? DEFAULT_MANDATE;
  const initialCapital = base.initial_capital ?? 1_000_000;
  const realized = base.realized_pnl ?? 0;
  const unrealized = openPositions.reduce((sum, p) => sum + (finiteNumber(p.current_pnl ?? p.unrealized_pnl) ?? 0), 0);
  const reserved = openPositions.reduce((sum, p) => sum + cbeNotional(p), 0);
  const totalEquity = initialCapital + realized + unrealized;
  const equityBase = totalEquity > 0 ? totalEquity : initialCapital;

  let longExposure = 0;
  let shortExposure = 0;
  const nameValues: number[] = [];
  const sectors = new Map<string, SectorExposure>();

  for (const position of openPositions) {
    const value = cbeCurrentValue(position);
    if (value <= 0) continue;
    const side = cbeDirection(position);
    const sector = String(position.sector_code || "unclassified");
    const row =
      sectors.get(sector) ??
      {
        sector,
        long_exposure: 0,
        short_exposure: 0,
        gross_exposure: 0,
        net_exposure: 0,
        names: 0,
      };

    if (side === "short") {
      shortExposure += value;
      row.short_exposure = (row.short_exposure ?? 0) + value;
    } else {
      longExposure += value;
      row.long_exposure = (row.long_exposure ?? 0) + value;
    }
    row.gross_exposure = (row.gross_exposure ?? 0) + value;
    row.net_exposure = (row.long_exposure ?? 0) - (row.short_exposure ?? 0);
    row.names = (row.names ?? 0) + 1;
    sectors.set(sector, row);
    nameValues.push(value);
  }

  nameValues.sort((a, b) => b - a);
  const gross = longExposure + shortExposure;
  const net = longExposure - shortExposure;
  const sectorExposures = Array.from(sectors.values())
    .map((s) => ({
      ...s,
      long_exposure: roundMoney(s.long_exposure),
      short_exposure: roundMoney(s.short_exposure),
      gross_exposure: roundMoney(s.gross_exposure),
      net_exposure: roundMoney(s.net_exposure),
      gross_exposure_ratio: equityBase ? roundRatio((s.gross_exposure ?? 0) / equityBase) : 0,
    }))
    .sort((a, b) => (b.gross_exposure ?? 0) - (a.gross_exposure ?? 0));

  const grossRatio = equityBase ? gross / equityBase : 0;
  const netRatio = equityBase ? net / equityBase : 0;
  const largestRatio = equityBase && nameValues.length ? nameValues[0] / equityBase : 0;
  const top3Ratio = equityBase ? nameValues.slice(0, 3).reduce((s, v) => s + v, 0) / equityBase : 0;
  const topSectorRatio = sectorExposures[0]?.gross_exposure_ratio ?? 0;
  const maxGross = mandate.max_gross_exposure_ratio ?? DEFAULT_MANDATE.max_gross_exposure_ratio;
  const maxNet = mandate.max_net_exposure_ratio ?? DEFAULT_MANDATE.max_net_exposure_ratio;
  const maxSingle = mandate.max_single_name_ratio ?? DEFAULT_MANDATE.max_single_name_ratio;
  const maxSector = mandate.max_sector_exposure_ratio ?? DEFAULT_MANDATE.max_sector_exposure_ratio;
  const drift = 1 + (mandate.rebalance_drift_ratio ?? DEFAULT_MANDATE.rebalance_drift_ratio);
  const riskFlags: string[] = [];

  // Match the backend's exact comparison (paper.py uses `> limit*drift`, no
  // epsilon) so the client-recomputed "inside mandate" badge never disagrees
  // with /paper-summary on a boundary case.
  if (grossRatio > maxGross * drift) riskFlags.push("gross_exposure_over_mandate");
  if (Math.abs(netRatio) > maxNet * drift) riskFlags.push("net_exposure_over_mandate");
  if (largestRatio > maxSingle * drift) riskFlags.push("single_name_over_mandate");
  if (topSectorRatio > maxSector * drift) riskFlags.push("sector_concentration_over_mandate");
  if (!riskFlags.length && openPositions.length) riskFlags.push("inside_hedge_mandate");

  return {
    ...base,
    open_positions: openPositions.length,
    realized_pnl: roundMoney(realized),
    unrealized_pnl: roundMoney(unrealized),
    total_pnl: roundMoney(realized + unrealized),
    initial_capital: initialCapital,
    available_capital: roundMoney(initialCapital + realized - reserved),
    reserved_margin: roundMoney(reserved),
    total_equity: roundMoney(totalEquity),
    total_return_pct: initialCapital ? roundRatio(((totalEquity - initialCapital) / initialCapital) * 100) : 0,
    long_positions: openPositions.filter((p) => cbeDirection(p) === "long").length,
    short_positions: openPositions.filter((p) => cbeDirection(p) === "short").length,
    long_exposure: roundMoney(longExposure),
    short_exposure: roundMoney(shortExposure),
    gross_exposure: roundMoney(gross),
    net_exposure: roundMoney(net),
    gross_exposure_ratio: roundRatio(grossRatio),
    net_exposure_ratio: roundRatio(netRatio),
    largest_position_exposure: roundMoney(nameValues[0] ?? 0),
    largest_position_ratio: roundRatio(largestRatio),
    concentration_top3_ratio: roundRatio(top3Ratio),
    risk_budget_usage_ratio: maxGross > 0 ? roundRatio(Math.min(1, grossRatio / maxGross)) : 0,
    book_bias: Math.abs(netRatio) < 0.05 ? "balanced" : netRatio > 0 ? "net_long" : "net_short",
    sector_exposures: sectorExposures,
    risk_flags: riskFlags,
    mandate,
  };
}

function roundMoney(value?: number | null): number {
  return Math.round((value ?? 0) * 100) / 100;
}

function roundRatio(value?: number | null): number {
  return Math.round((value ?? 0) * 10_000) / 10_000;
}

function formatSignedPct(value?: number | null, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${(value * 100).toFixed(digits)}%`;
}

function bookBiasLabel(value?: string | null) {
  if (!value) return "beta neutral";
  return value.replace(/_/g, " ");
}

function riskUsed(value?: number | null, limit?: number | null) {
  if (!value || !limit || limit <= 0) return 0;
  return Math.min(1, Math.abs(value) / limit);
}

function riskTone(value?: number | null, limit?: number | null) {
  const used = riskUsed(value, limit);
  if (used >= 0.95) return "text-accent-red";
  if (used >= 0.75) return "text-accent-amber";
  return "text-accent-green";
}

function MandatePanel({ summary, mandate }: { summary?: PaperSummary; mandate: HedgeMandate }) {
  const maxGross = mandate.max_gross_exposure_ratio ?? DEFAULT_MANDATE.max_gross_exposure_ratio;
  const maxNet = mandate.max_net_exposure_ratio ?? DEFAULT_MANDATE.max_net_exposure_ratio;
  const maxSingle = mandate.max_single_name_ratio ?? DEFAULT_MANDATE.max_single_name_ratio;
  const maxSector = mandate.max_sector_exposure_ratio ?? DEFAULT_MANDATE.max_sector_exposure_ratio;
  const flags = summary?.risk_flags ?? [];

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <MandateBar label="Gross exposure" value={summary?.gross_exposure_ratio ?? 0} limit={maxGross} />
        <MandateBar label="Net exposure" value={Math.abs(summary?.net_exposure_ratio ?? 0)} limit={maxNet} />
        <MandateBar label="Single name" value={summary?.largest_position_ratio ?? 0} limit={maxSingle} />
        <MandateBar
          label="Largest sector"
          value={summary?.sector_exposures?.[0]?.gross_exposure_ratio ?? 0}
          limit={maxSector}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        {(flags.length ? flags : ["awaiting_positions"]).map((flag) => (
          <StatusBadge
            key={flag}
            label={flag.replace(/_/g, " ")}
            variant={flag === "inside_hedge_mandate" || flag === "awaiting_positions" ? "success" : "warn"}
          />
        ))}
        <StatusBadge label={`${mandate.rebalance ?? DEFAULT_MANDATE.rebalance} rebalance`} variant="info" />
        <StatusBadge label={`${mandate.min_hold_trading_days ?? DEFAULT_MANDATE.min_hold_trading_days}d min hold`} variant="neutral" />
        {mandate.hard_stop_loss_pct != null ? (
          <StatusBadge label={`${formatPct(mandate.hard_stop_loss_pct, 0)} hard stop`} variant="warn" />
        ) : null}
      </div>
    </div>
  );
}

function MandateBar({ label, value, limit }: { label: string; value: number; limit: number }) {
  const used = riskUsed(value, limit);
  return (
    <div className="rounded-xl border border-bg-border bg-bg-primary/14 p-3">
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="font-semibold uppercase tracking-[0.12em] text-text-muted">{label}</span>
        <span className={`font-mono ${riskTone(value, limit)}`}>
          {formatPct(value, 1)} / {formatPct(limit, 0)}
        </span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-bg-primary/45">
        <div
          className="h-full rounded-full bg-accent-blue"
          style={{
            width: `${used * 100}%`,
            background:
              used >= 0.95
                ? "rgb(var(--accent-red))"
                : used >= 0.75
                  ? "rgb(var(--accent-amber))"
                  : "rgb(var(--accent-green))",
          }}
        />
      </div>
    </div>
  );
}

function SectorExposurePanel({
  sectors,
  maxSector,
}: {
  sectors: SectorExposure[];
  maxSector: number;
}) {
  if (!sectors.length) {
    return (
      <div className="flex h-[180px] items-center justify-center rounded-xl border border-dashed border-bg-border/60 text-sm text-text-muted">
        No sector exposure
      </div>
    );
  }
  const rows = sectors.slice(0, 8);
  const max = Math.max(maxSector, ...rows.map((s) => Math.abs(s.gross_exposure_ratio ?? 0)));
  return (
    <div className="space-y-2">
      {rows.map((s) => {
        const grossRatio = s.gross_exposure_ratio ?? 0;
        const net = s.net_exposure ?? 0;
        const used = max > 0 ? Math.min(1, grossRatio / max) : 0;
        return (
          <div key={s.sector} className="grid grid-cols-[minmax(100px,150px)_1fr_70px] items-center gap-2 text-[11.5px]">
            <div className="truncate font-medium text-text-secondary" title={s.sector}>
              {s.sector.replace(/_/g, " ")}
            </div>
            <div className="h-4 overflow-hidden rounded bg-bg-primary/25">
              <div
                className="h-full rounded"
                style={{
                  width: `${used * 100}%`,
                  background: grossRatio > maxSector ? "rgb(var(--accent-amber))" : "rgb(var(--accent-blue))",
                  opacity: 0.65,
                }}
              />
            </div>
            <div className="text-right font-mono text-text-muted">
              {formatPct(grossRatio, 1)}
            </div>
            <div className="col-start-2 col-end-4 -mt-1 flex items-center justify-between text-[10px] text-text-muted">
              <span>{s.names ?? 0} names</span>
              <span className={tone(net)}>{formatSignedMoney(net)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function cbeSymbol(position: PaperPosition): string {
  return String(position.instrument ?? position.symbol ?? position.underlying ?? position.trading_symbol ?? "—");
}

function cbeDirection(position: PaperPosition): string {
  return String(position.direction ?? position.side ?? "").toLowerCase();
}

function cbeNotional(position: PaperPosition): number {
  const direct = finiteNumber(position.notional) ?? 0;
  if (direct > 0) return direct;
  const entry = cbeEntryPrice(position);
  const quantity = cbeQuantity(position);
  return Math.max(0, entry * quantity);
}

function cbeUnrealized(position: PaperPosition): number {
  return finiteNumber(position.current_pnl ?? position.unrealized_pnl ?? position.mtm_pnl) ?? 0;
}

function cbeCurrentValue(position: PaperPosition): number {
  const direct = finiteNumber(position.current_value);
  if (direct != null) return direct;
  return Math.max(0, cbeMarkPrice(position) * cbeQuantity(position));
}

function cbePnlPct(position: PaperPosition): number | null {
  const explicit = finiteNumber(position.current_pnl_pct);
  if (explicit != null) return explicit;
  const notional = cbeNotional(position);
  return notional > 0 ? cbeUnrealized(position) / notional : null;
}

function SleeveTable({
  title,
  side,
  positions,
  summary,
}: {
  title: string;
  side: "long" | "short";
  positions: MarkedCbePosition[];
  summary?: PaperSummary;
}) {
  const equity = summary?.total_equity || summary?.initial_capital || 1;
  const rows = positions
    .filter((p) => cbeDirection(p) === side)
    .sort((a, b) => cbeCurrentValue(b) - cbeCurrentValue(a))
    .slice(0, 8);
  const sideColor = side === "long" ? "text-accent-green" : "text-accent-red";
  const totalValue = rows.reduce((sum, p) => sum + cbeCurrentValue(p), 0);

  return (
    <div className="rounded-xl border border-bg-border bg-bg-primary/12">
      <div className="flex items-center justify-between border-b border-bg-border/50 px-3 py-2">
        <div>
          <div className={`text-[11px] font-semibold uppercase tracking-[0.14em] ${sideColor}`}>{title}</div>
          <div className="mt-0.5 text-[10.5px] text-text-muted">{formatMoney(totalValue)} current value</div>
        </div>
        <StatusBadge label={`${rows.length} names`} variant={side === "long" ? "success" : "error"} />
      </div>
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-bg-border/40">
                {["Symbol", "Sector", "Qty", "Entry", "Spot", "Value", "uPnL"].map((h, i) => (
                  <th
                    key={h}
                    className={`px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-text-muted ${
                      i < 2 ? "text-left" : "text-right"
                    }`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((p, i) => {
                const pnl = cbeUnrealized(p);
                const pnlPct = cbePnlPct(p);
                return (
                  <tr key={p.position_id ?? `${side}-${i}`} className="border-b border-bg-border/20 last:border-b-0">
                    <td className="px-3 py-2">
                      <div className="font-mono text-[12px] font-semibold text-text-primary">{cbeSymbol(p)}</div>
                      <div className="text-[10px] text-text-muted">
                        {String(p.execution_model ?? p.mark_source ?? "mark").replace(/_/g, " ")}
                        {p.mark_scan_date ? ` · ${p.mark_scan_date}` : ""}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-[11px] text-text-muted">{String(p.sector_code ?? "—").replace(/_/g, " ")}</td>
                    <td className="px-3 py-2 text-right font-mono text-[12px] text-text-secondary">{formatNumber(cbeQuantity(p), 0)}</td>
                    <td className="px-3 py-2 text-right font-mono text-[12px] text-text-secondary">{formatNumber(cbeEntryPrice(p), 1)}</td>
                    <td className="px-3 py-2 text-right text-[12px] text-text-primary">
                      <LiveMarkCell symbol={rowTapeSymbol(p)} fallback={cbeMarkPrice(p)} fallbackAt={p.updated_at ?? p.marked_at ?? p.opened_at} decimals={1} />
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="font-mono text-[12px] text-text-primary">{formatMoney(cbeCurrentValue(p))}</div>
                      <div className="text-[10px] text-text-muted">{formatPct(cbeCurrentValue(p) / equity, 1)} wt</div>
                    </td>
                    <td className={`px-3 py-2 text-right font-mono text-[12px] ${tone(pnl)}`}>
                      <div>{formatSignedMoney(pnl)}</div>
                      <div className="text-[10px]">{pnlPct != null ? formatSignedPct(pnlPct, 1) : "—"}</div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-3 py-6 text-center text-sm text-text-muted">No {side} positions</div>
      )}
    </div>
  );
}

function QuadrantCard({ q, n, note }: { q: string; n: number; note: string }) {
  const col = QUADRANT_COLOR[q] || "rgb(var(--accent-blue))";
  return (
    <div className="rounded-xl border border-bg-border bg-bg-primary/14 p-3">
      <div className="flex items-center justify-between">
        <span className="text-[10.5px] uppercase tracking-[0.16em]" style={{ color: col }}>
          {q}
        </span>
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: col }} />
      </div>
      <div className="mt-1 text-2xl font-semibold text-text-primary">{n}</div>
      <div className="text-[11px] text-text-muted">{note}</div>
    </div>
  );
}

function CandidateTable({ rows }: { rows: ScanResult[] }) {
  if (rows.some(row => row.details?.stock_selection)) {
    return <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr>
      <th>Stock</th><th>Direction</th><th>EMA50</th><th>EMA200</th><th>63-session excess vs Nifty</th><th>Strength</th><th>Selection reason</th>
    </tr></thead><tbody>{rows.map(row => {
      const signal:any = row.details?.stock_selection;
      return <tr key={row.instrument}><td className="py-2">{row.instrument}{row.is_watchlist ? " ★" : ""}</td><td>{signal?.direction ?? "NEUTRAL"}</td>
        <td>{signal?.ema50?.toFixed(2) ?? "—"}</td><td>{signal?.ema200?.toFixed(2) ?? "—"}</td><td>{formatPct(signal?.relative_63, 2)}</td>
        <td>{row.composite_alpha_score?.toFixed(1) ?? "—"}</td><td>{signal?.reason ?? "Unavailable inputs"}</td></tr>;
    })}</tbody></table><p className="mt-2 text-xs text-text-muted">Strength is an ordinal research score, not a probability or expected return. Only qualified long directions can fund new CBE positions.</p></div>;
  }
  return (
    <div className="-mx-2 overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-bg-border/60">
            {["#", "Symbol", "Sector", "Quadrant", "Alpha", "Bias", "RS%", "MACD", "RSI", "Wk trend", "Close", "Trend", "Gate"].map((h, i) => (
              <th
                key={h}
                className={`px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-text-muted ${
                  i <= 2 ? "text-left" : "text-right"
                }`}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((r, i) => {
              const col = QUADRANT_COLOR[r.stock_quadrant ?? ""] || "rgb(var(--accent-blue))";
              return (
                <tr key={r.instrument} className="border-b border-bg-border/25 hover:bg-bg-primary/20">
                  <td className="px-2.5 py-1.5 text-left font-mono text-[11px] text-text-muted">{i + 1}</td>
                  <td className="px-2.5 py-1.5 text-left font-mono text-[12px] font-semibold text-text-primary">
                    {r.instrument}
                    {r.is_watchlist ? <span className="ml-1.5 text-accent-amber" title="on watchlist">★</span> : null}
                  </td>
                  <td className="px-2.5 py-1.5 text-left text-[11px] text-text-secondary">{(r.sector_code ?? "—").replace(/_/g, " ")}</td>
                  <td className="px-2.5 py-1.5 text-right">
                    <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: col, background: `${col}1f` }}>
                      {r.stock_quadrant ?? "—"}
                    </span>
                  </td>
                  <td className="px-2.5 py-1.5 text-right font-mono text-[12px] text-text-primary">{formatNumber(r.composite_alpha_score, 1)}</td>
                  <td className="px-2.5 py-1.5 text-right">
                    <StatusBadge label={r.directional_bias ?? "neutral"} variant={biasVariant(r.directional_bias)} />
                  </td>
                  <td className={`px-2.5 py-1.5 text-right font-mono text-[12px] ${tone(r.stock_rs_pct)}`}>{formatNumber(r.stock_rs_pct, 2)}</td>
                  <td className={`px-2.5 py-1.5 text-right font-mono text-[12px] ${r.macd_bullish ? "text-accent-green" : "text-accent-red"}`} title={r.macd_meta?.label ?? ""}>
                    {formatNumber(r.macd_hist, 3)}
                    {r.macd_meta?.cross_today ? <span className="ml-1 text-[9px] text-accent-amber">×</span> : null}
                  </td>
                  <td className="px-2.5 py-1.5 text-right font-mono text-[12px] text-text-secondary" title={r.rsi_meta?.label ?? ""}>{formatNumber(r.rsi_14, 1)}</td>
                  <td className={`px-2.5 py-1.5 text-right text-[11px] ${trendTone(r.weekly_trend)}`}>{r.weekly_trend ?? "—"}</td>
                  <td className="px-2.5 py-1.5 text-right font-mono text-[12px] text-text-secondary">{formatNumber(r.latest_close, 1)}</td>
                  <td className="px-2.5 py-1.5 text-right">
                    <div className="ml-auto w-[96px]">
                      <Sparkline values={r.recent_closes_30d} endDot />
                    </div>
                  </td>
                  <td className="px-2.5 py-1.5 text-right">
                    {r.gate_passed ? <span className="text-accent-green">✓</span> : <span className="text-text-muted">·</span>}
                  </td>
                </tr>
              );
            })
          ) : (
            <tr>
              <td colSpan={13} className="px-2.5 py-6 text-center text-sm text-text-muted">No candidates</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function SectorTable({ sectors }: { sectors: SectorRow[] }) {
  return (
    <div className="-mx-2 overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-bg-border/60">
            {["Sector", "Quadrant", "RS% vs Nifty50", "Leaders", "Scored", "Breadth"].map((h, i) => (
              <th key={h} className={`px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-text-muted ${i === 0 ? "text-left" : "text-right"}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sectors.length ? (
            sectors.map((s) => {
              const col = QUADRANT_COLOR[s.quadrant] || "rgb(var(--accent-blue))";
              const breadth = s.count ? s.leaders / s.count : 0;
              return (
                <tr key={s.code} className="border-b border-bg-border/25 hover:bg-bg-primary/20">
                  <td className="px-2.5 py-1.5 text-left font-medium text-text-primary">{s.code.replace(/_/g, " ")}</td>
                  <td className="px-2.5 py-1.5 text-right">
                    <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: col, background: `${col}1f` }}>
                      {s.quadrant}
                    </span>
                  </td>
                  <td className={`px-2.5 py-1.5 text-right font-mono text-[12px] ${tone(s.rs)}`}>{s.rs >= 0 ? "+" : ""}{s.rs.toFixed(2)}%</td>
                  <td className="px-2.5 py-1.5 text-right font-mono text-[12px] text-accent-green">{s.leaders}</td>
                  <td className="px-2.5 py-1.5 text-right font-mono text-[12px] text-text-secondary">{s.count}</td>
                  <td className="px-2.5 py-1.5 text-right">
                    <div className="ml-auto h-2 w-24 overflow-hidden rounded-full bg-bg-primary/40">
                      <div className="h-full rounded-full" style={{ width: `${breadth * 100}%`, background: col, opacity: 0.7 }} />
                    </div>
                  </td>
                </tr>
              );
            })
          ) : (
            <tr>
              <td colSpan={6} className="px-2.5 py-6 text-center text-sm text-text-muted">No sectors</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
