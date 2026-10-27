"use client";

/** Vanguard research and prospective paper-trading desk. */
import { useMemo, useState } from "react";
import {MarketContextPanel} from '@/MarketContextPanel';
import {LaneCandidates} from '@/LaneCandidates';
import {dataScope} from '@/lib/api';
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BookOpen,
  BrainCircuit,
  Filter,
  FlaskConical,
  GitBranch,
  Info,
  Gauge,
  Layers,
  Sigma,
} from "lucide-react";

import {
  DeskShell,
  MetricTile,
  REFRESH_MS,
  Section,
  StatusBadge,
  formatIST,
  formatMoney,
  formatNumber,
  formatSignedMoney,
  tone,
  useUrlTab,
} from "@/components/desk-ui";
import {
  getVanguardAttribution,
  getVanguardBacktests,
  getVanguardBook,
  getVanguardLegacyBook,
  getVanguardCrossSection,
  getVanguardFunnel,
  getVanguardMarket,
  getVanguardModel,
  getVanguardObservationFollowup,
  getVanguardStrategyJournals,
  getVanguardWatchlist,
  getVanguardPipeline,
  getVanguardRisk,
  getVanguardSelection,
  getVanguardMp,
  getMpVerdicts,
  getVanguardOiFutures,
  getVanguardSentiment,
  getVanguardSummary,
  getVanguardSymbol,
} from "@/lib/api";
import { useQuote, useQuotesConnection } from "@/hooks/useQuoteStore";

import PaperTrading from "./PaperTrading";
import DecisionReadinessPanel from "./DecisionReadinessPanel";
import EquityChallenger from "./EquityChallenger";
import ObservationFollowup from "./ObservationFollowup";
import VanguardPerformance from "./VanguardPerformance";
import { DecisionFlowTab } from "./DecisionFlow";
import { MarketTab } from "./MarketTab";
import { ResearchTab } from "./ResearchTab";
import { MpTab } from "./MpTab";
import OiFuturesTab from "./OiFuturesTab";
import { SentimentTab } from "./SentimentTab";
import { SymbolDetail } from "./SymbolDetail";

type FunnelStage = { stage: string; surviving: number; gate: string };

/** A value the backend genuinely did not compute. Never rendered as 0. */
function Unmeasured({ why }: { why: string }) {
  return (
    <span className="text-text-muted" title={why}>
      not measured
    </span>
  );
}

function num(value: unknown): number | null {
  const n = typeof value === "string" ? Number(value) : value;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

export default function VanguardDesk() {
  const [activeTab, setActiveTab] = useUrlTab("paper");
  // Which symbol's detail is open. Kept here rather than in MarketTab so the
  // Decision tab's "these died here" chips can open the same panel — clicking a
  // casualty in the funnel and clicking it in the grid must land in one place.
  const [symbol, setSymbol] = useState<string | null>(null);
  const [watchlistSession, setWatchlistSession] = useState("");

  const openSymbol = (next: string | null) => {
    setSymbol(next);
    if (next) setActiveTab("market");
  };

  const summary = useQuery({
    queryKey: ["vanguard", "summary"],
    queryFn: (): Promise<any> => getVanguardSummary().then((r) => r.data),
    refetchInterval: REFRESH_MS.summary,
  });
  const funnel = useQuery({
    queryKey: ["vanguard", "funnel"],
    queryFn: (): Promise<any> => getVanguardFunnel().then((r) => r.data),
    refetchInterval: REFRESH_MS.summary,
    enabled: activeTab === "decision",
  });
  const selection = useQuery({
    queryKey: ["vanguard", "selection"],
    queryFn: (): Promise<any> => getVanguardSelection().then((r) => r.data),
    refetchInterval: REFRESH_MS.summary,
    enabled: activeTab === "decision",
  });
  const model = useQuery({
    queryKey: ["vanguard", "model"],
    queryFn: (): Promise<any> => getVanguardModel().then((r) => r.data),
    refetchInterval: REFRESH_MS.summary,
    enabled: activeTab === "model" || activeTab === "performance",
  });
  const watchlist = useQuery({
    queryKey: ["vanguard", "watchlist", watchlistSession],
    queryFn: (): Promise<any> => getVanguardWatchlist(20, watchlistSession || undefined).then((r) => r.data),
    refetchInterval: REFRESH_MS.summary,
    enabled: activeTab === "watchlist" || activeTab === "performance",
  });
  const strategyJournals = useQuery({
    queryKey: ["vanguard", "strategy-journals"],
    queryFn: (): Promise<any> => getVanguardStrategyJournals().then((r) => r.data),
    refetchInterval: REFRESH_MS.summary,
    enabled: activeTab === "watchlist",
  });
  const book = useQuery({
    queryKey: ["vanguard", "book"],
    queryFn: (): Promise<any> => getVanguardLegacyBook().then((r) => r.data),
    refetchInterval: REFRESH_MS.summary,
    enabled: activeTab === "book",
  });
  const attribution = useQuery({
    queryKey: ["vanguard", "attribution"],
    queryFn: (): Promise<any> => getVanguardAttribution().then((r) => r.data),
    enabled: activeTab === "attribution" || activeTab === "performance",
  });
  const backtests = useQuery({
    queryKey: ["vanguard", "backtests"],
    queryFn: (): Promise<any> => getVanguardBacktests().then((r) => r.data),
    enabled: activeTab === "backtest",
  });
  const pipeline = useQuery({
    queryKey: ["vanguard", "pipeline"],
    queryFn: (): Promise<any> => getVanguardPipeline().then((r) => r.data),
    enabled: activeTab === "pipeline",
  });
  // The market grid is needed by BOTH the Market tab and the Decision tab (the
  // conviction histogram is over the whole evaluated universe, not just
  // survivors), so it is not gated on a single tab.
  const market = useQuery({
    queryKey: ["vanguard", "market"],
    queryFn: (): Promise<any> => getVanguardMarket().then((r) => r.data),
    refetchInterval: REFRESH_MS.summary,
    enabled: (activeTab === "market" && dataScope()==='retained') || activeTab === "decision",
  });
  const detail = useQuery({
    queryKey: ["vanguard", "symbol", symbol],
    queryFn: (): Promise<any> => getVanguardSymbol(symbol as string).then((r) => r.data),
    enabled: Boolean(symbol),
  });
  const crossSection = useQuery({
    queryKey: ["vanguard", "cross-section"],
    queryFn: (): Promise<any> => getVanguardCrossSection().then((r) => r.data),
    enabled: activeTab === "research",
  });
  const sentiment = useQuery({
    queryKey: ["vanguard", "sentiment"],
    queryFn: (): Promise<any> => getVanguardSentiment().then((r) => r.data),
    enabled: activeTab === "sentiment",
  });
  const mp = useQuery({
    queryKey: ["vanguard", "mp"],
    queryFn: (): Promise<any> => getVanguardMp().then((r) => r.data),
    enabled: activeTab === "mp",
    refetchInterval: REFRESH_MS.summary,
  });
  const oiFutures = useQuery({
    queryKey: ["vanguard", "oi-futures"],
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    queryFn: (): Promise<any> => getVanguardOiFutures().then((r) => r.data),
    enabled: activeTab === "oiFutures",
    refetchInterval: REFRESH_MS.summary,
  });
  const mpVerdicts = useQuery({
    queryKey: ["mp", "verdicts"],
    queryFn: (): Promise<any> => getMpVerdicts().then((r) => r.data),
    enabled: activeTab === "mp",
    staleTime: Infinity,
  });
  const risk = useQuery({
    queryKey: ["vanguard", "risk"],
    queryFn: (): Promise<any> => getVanguardRisk().then((r) => r.data),
    enabled: activeTab === "research",
  });
  const followupPerformance = useQuery({
    queryKey: ["vanguard", "observation-followup", "swing", "performance"],
    queryFn: (): Promise<any> => getVanguardObservationFollowup("swing").then((r) => r.data),
    enabled: activeTab === "performance",
    refetchInterval: REFRESH_MS.summary,
  });

  const s = summary.data;
  const thresholds = s?.thresholds;
  const capital = s?.capital;

  return (
    <DeskShell
      title="Vanguard"
      description="ATM option paper trading over 1–3 sessions. Current-session candidates require fresh Qwen approval, a fresh ATM check and executable quotes before fills. Stops, targets, expiry and scheduled exits are managed independently."
      asOf={s?.latest_timing_bar ?? null}
      asOfLabel="latest timing bar"
      paperMode
      isFetching={summary.isFetching}
      tabs={[
        { key: "paper", label: "Paper trading", icon: BookOpen },
        { key: "market", label: "Market", icon: Layers },
        { key: "decision", label: "Decision flow", icon: Filter },
        { key: "model", label: "Model", icon: BrainCircuit },
        { key: "watchlist", label: "Watchlist", icon: Activity },
        { key: "followup", label: "Stock skill", icon: Activity },
        { key: "equityChallenger", label: "Stock challenger", icon: FlaskConical },
        { key: "performance", label: "Research performance", icon: Activity },
        { key: "sentiment", label: "Sentiment", icon: Gauge },
        { key: "oiFutures", label: "Futures OI", icon: Layers },
        { key: "mp", label: "MP structure", icon: Layers },
        { key: "book", label: "Legacy book", icon: BookOpen },
        { key: "research", label: "Research", icon: Sigma },
        { key: "attribution", label: "Attribution", icon: Activity },
        { key: "backtest", label: "Backtest", icon: FlaskConical },
        { key: "pipeline", label: "Pipeline", icon: GitBranch },
      ]}
      activeTab={activeTab}
      onTabChange={setActiveTab}
    >
      {activeTab !== "paper" && s?.activity?.swing && (
        <Section title="Current swing activity">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <MetricTile label="Research observations" value={String(s.activity.swing.item_count)} detail={`Source session ${s.activity.swing.source_session}`} />
            <MetricTile label="Actionable selections" value={String(s.activity.swing.actionable_count)} detail="After confidence, expected return and risk checks" />
            <MetricTile label="Legacy ticket closes" value={String(s.legacy_book?.closed_positions ?? 0)} detail="Separate from current swing observations" />
          </div>
          <p className="mt-3 text-xs text-text-muted">{s.activity.swing.actionable_note || "Actionable selections remain paper only."}</p>
        </Section>
      )}
      {activeTab === "paper" && <div className="space-y-5">{dataScope()==='retained'&&<DecisionReadinessPanel />}<PaperTrading /></div>}
      {activeTab === "equityChallenger" && <EquityChallenger />}
      {activeTab === "market" && dataScope()==='shadow' && <MarketContextPanel lane="vanguard"/>}
      {activeTab === "market" && dataScope()==='retained' &&
        (symbol ? (
          <SymbolDetail
            data={detail.data}
            loading={detail.isLoading}
            thresholds={market.data?.thresholds}
            onBack={() => setSymbol(null)}
          />
        ) : (
          <MarketTab
            market={market.data}
            selectedSymbol={symbol}
            onPickSymbol={openSymbol}
          />
        ))}
      {activeTab === "decision" && (
        <DecisionFlowTab
          funnel={funnel.data}
          selection={selection.data}
          market={market.data}
          onPickSymbol={openSymbol}
        />
      )}
      {activeTab === "model" && <ModelTab data={model.data} />}
      {activeTab === "followup" && <ObservationFollowup />}
      {activeTab === "performance" && (
        <VanguardPerformance
          summary={{...summary.data,book:summary.data?.legacy_book}}
          model={model.data}
          watchlist={watchlist.data}
          attribution={attribution.data}
          followup={followupPerformance.data}
        />
      )}
      {activeTab === "watchlist" && dataScope()==='shadow' && <LaneCandidates lane="vanguard"/>}
      {activeTab === "watchlist" && dataScope()==='retained' && <WatchlistTab data={watchlist.data} strategies={strategyJournals.data}
        selectedSession={watchlistSession} onSession={setWatchlistSession}
        onBtst={() => setActiveTab("mp")} />}
      {activeTab === "sentiment" && <SentimentTab data={sentiment.data} />}
      {activeTab === "oiFutures" && <OiFuturesTab data={oiFutures.data} />}
      {activeTab === "mp" && <MpTab data={mp.data} verdicts={mpVerdicts.data} />}
      {activeTab === "research" && (
        <ResearchTab crossSection={crossSection.data} risk={risk.data} />
      )}
      {activeTab === "book" && <BookTab book={book.data} capital={capital} summary={{...s,book:s?.legacy_book}} />}
      {activeTab === "attribution" && <AttributionTab data={attribution.data} />}
      {activeTab === "backtest" && <BacktestTab data={backtests.data} />}
      {activeTab === "pipeline" && <PipelineTab data={pipeline.data} />}
    </DeskShell>
  );
}

// ─── Selection ──────────────────────────────────────────────────────────────
//
// SelectionTab lived here and drew the funnel plus the ticket table. Both moved
// to ./DecisionFlow.tsx when the funnel stopped being a re-derivation of M6's
// filter and became a projection of the journal M6 writes (see migration 006).
// Deleted rather than left dormant: a second, older renderer of the same
// numbers is exactly how the funnel and the selector drifted apart in the first
// place.

function ModelTab({ data }: { data?: any }) {
  const model = data?.model;
  const directional = model?.horizon_bars === 24;
  const test = directional
    ? (model?.metrics?.test_watchlist_top10 ?? {})
    : (model?.metrics?.test ?? {});
  const validation = directional
    ? (model?.metrics?.validation_watchlist_top10 ?? {})
    : (model?.metrics?.validation ?? {});
  const recent = data?.recent ?? [];
  const ratioCoverage = data?.ratio_coverage ?? {};
  const pct = (value: unknown, digits = 2) => {
    const parsed = num(value);
    return parsed == null ? "—" : `${(parsed * 100).toFixed(digits)}%`;
  };

  if (!model) {
    return (
      <Section title="Nonlinear option selector" icon={<BrainCircuit size={16} />}>
        <p className="text-sm text-text-secondary">No versioned model has been registered.</p>
      </Section>
    );
  }

  const active = model.status === "paper_active";
  return (
    <div className="space-y-4">
      <Section
        title={directional ? "1–2 session directional selector" : "Distributional option-P&L selector"}
        icon={<BrainCircuit size={16} />}
        description={directional
          ? "M2–M5 and option-premium ratios are nonlinear inputs. The network chooses CE versus PE direction for the same underlying and ranks names by their conditional-median margin over a 1–2-session target."
          : "M2–M5 are nonlinear inputs, not sequential vetoes. The network ranks both ATM calls and puts by next-bar return quantiles, uncertainty and an explicit cost assumption."}
        rightSlot={
          <StatusBadge
            label={model.status.replaceAll("_", " ")}
            variant={active ? "success" : "warn"}
          />
        }
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-6">
          <MetricTile label="Train" value={formatNumber(num(model.n_train), 0)} />
          <MetricTile label="Validation" value={formatNumber(num(model.n_validation), 0)} />
          <MetricTile label="Historical test" value={formatNumber(num(model.n_test), 0)} />
          <MetricTile
            label={directional ? "Test top-10 direction" : "Test selected net"}
            value={pct(directional ? test.net_mean : test.selected_net_mean)}
            color={tone(num(directional ? test.net_mean : test.selected_net_mean))}
          />
          <MetricTile label="Positive test sessions" value={pct(test.positive_session_rate, 1)} />
          <MetricTile label="Round-trip cost" value={pct(model.cost_pct, 1)} />
          <MetricTile
            label="Model inputs"
            value={formatNumber((model.feature_names ?? []).length, 0)}
            detail="raw features; missing flags added"
          />
          <MetricTile
            label="Audited resolved"
            value={formatNumber(num(data?.cumulative?.resolved), 0)}
            detail={
              num(data?.cumulative?.resolved)
                ? `net mean ${pct(data?.cumulative?.realized_net_mean)}`
                : `${data?.cumulative?.legacy ?? 0} legacy-timing rows excluded`
            }
          />
        </div>
        <div
          className={`mt-3 rounded-xl border px-3 py-2 text-sm ${
            active
              ? "border-accent-green/30 bg-accent-green/5 text-text-secondary"
              : "border-accent-amber/30 bg-accent-amber/5 text-accent-amber"
          }`}
        >
          {active
            ? "Holdout promotion passed. The model may emit broker-free paper tickets; M7 supplies size."
            : directional
              ? `Historical shadow gate ${model.metrics?.historical_gate_passed ? "passed" : "failed"}; no promotion is allowed. Validation top-10 ${pct(validation.net_mean)}; test top-10 ${pct(test.net_mean)}.`
              : `Holdout promotion failed, so this version records shadow predictions only. Validation selected net ${pct(validation.selected_net_mean)}; test selected net ${pct(test.selected_net_mean)}.`}
        </div>
        <p className="mt-3 text-xs text-text-muted">
          {model.cost_provenance}. Artifact {String(model.artifact_sha256).slice(0, 12)}… · test {model.test_start} → {model.test_end}.
        </p>
        <p className="mt-2 text-xs text-text-muted">
          {directional
            ? "Frozen training version; daily EOD shadow scoring only. Target averages side-adjusted underlying returns at the next one and two session closes from the next-session first close. The overnight gap before entry is excluded."
            : "Frozen shadow observation; no automatic nightly retraining or promotion. Option inputs must match a completed timing bar. Flow and sector RS use the previous completed session."}
        </p>
      </Section>

      <Section
        title="Option-premium ratio inputs"
        description="Same-expiry chain structure is fed directly to the nonlinear selector. Missing or unreliable wings remain explicit missing inputs; they are never replaced by a favorable value."
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <MetricTile label="Ratio snapshots" value={formatNumber(num(ratioCoverage.snapshots), 0)} />
          <MetricTile label="ATM straddles" value={formatNumber(num(ratioCoverage.straddles), 0)} />
          <MetricTile label="Premium PCR" value={formatNumber(num(ratioCoverage.premium_pcr), 0)} />
          <MetricTile
            label="Valid 25Δ wings"
            value={formatNumber(num(ratioCoverage.valid_wings), 0)}
            detail={
              num(ratioCoverage.snapshots)
                ? `${(((num(ratioCoverage.valid_wings) ?? 0) / (num(ratioCoverage.snapshots) ?? 1)) * 100).toFixed(1)}% coverage`
                : "quality-gated"
            }
          />
        </div>
        <p className="mt-3 text-xs leading-5 text-text-muted">
          Inputs: ATM straddle/spot, DTE-normalized straddle, equal-delta strangle/straddle,
          25Δ put and call IV relative to ATM, wing skew, ATM put/call premium, ATM call/put
          extrinsic value, premium-turnover PCR, side-specific ITM/OTM extrinsic ratios, and
          chain breadth. ATM uses the nearest common strike to spot because a synchronized
          forward series is unavailable; premium PCR measures turnover, not trade aggressor direction.
        </p>
      </Section>

      <Section
        title="Latest shadow ranking"
        description={`${data?.predictions?.evaluated ?? 0} call/put contracts evaluated at ${formatIST(data?.predictions?.ts)}. ${directional ? "Directional margin is CE versus PE for the same name." : "A shadow row can look attractive and still cannot create a ticket."}`}
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
                <th className="py-2 pr-3">Contract</th>
                <th className="py-2 pr-3 text-right">Q10</th>
                <th className="py-2 pr-3 text-right">Median</th>
                <th className="py-2 pr-3 text-right">Q90</th>
                <th className="py-2 pr-3 text-right">{directional ? "Directional margin" : "Conservative edge"}</th>
                <th className="py-2 pr-3">Decision</th>
              </tr>
            </thead>
            <tbody>
              {recent.slice(0, 20).map((row: any) => (
                <tr
                  key={`${row.ts}-${row.symbol}-${row.option_type}`}
                  className="border-b border-bg-border/50"
                >
                  <td className="py-2 pr-3">
                    <div className="font-medium text-text-primary">{row.symbol} {row.option_type}</div>
                    <div className="font-mono text-[11px] text-text-muted">{row.instrument}</div>
                  </td>
                  <td className="py-2 pr-3 text-right font-mono">{pct(row.q10_return)}</td>
                  <td className="py-2 pr-3 text-right font-mono">{pct(row.q50_return)}</td>
                  <td className="py-2 pr-3 text-right font-mono">{pct(row.q90_return)}</td>
                  <td className={`py-2 pr-3 text-right font-mono ${tone(num(directional ? row.ranking_score : row.conservative_edge))}`}>
                    {pct(directional ? row.ranking_score : row.conservative_edge)}
                  </td>
                  <td className="max-w-[260px] py-2 pr-3 text-xs text-text-secondary">{row.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

type VanguardLane = "swing_1_2d" | "gap_overnight" | "oversold_mtf";

function LaneTabs({ lane, onLane, journals }: { lane: VanguardLane; onLane: (lane: VanguardLane) => void; journals?: any }) {
  const labels: [VanguardLane, string][] = [
    ["swing_1_2d", "Swing 1–3d"], ["gap_overnight", "Overnight"], ["oversold_mtf", "Oversold MTF"],
  ];
  return <div className="inline-flex rounded-xl border border-bg-border bg-bg-card p-1" role="tablist" aria-label="Vanguard strategy journals">
    {labels.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={lane === key}
      onClick={() => onLane(key)}
      className={`rounded-lg px-4 py-2 text-sm transition-colors ${lane === key ? "bg-accent-blue text-white" : "text-text-secondary hover:text-text-primary"}`}>
      {label} <span className="ml-1 font-mono text-xs opacity-75">{journals?.[key]?.length ?? 0}</span>
    </button>)}
  </div>;
}

function StrategyJournal({ strategy, rows, onBtst }: { strategy: VanguardLane; rows: any[]; onBtst: () => void }) {
  const title = strategy === "gap_overnight" ? "Overnight strategy journal" : "Oversold MTF strategy journal";
  const pct = (value: unknown) => {
    const parsed = num(value);
    return parsed == null ? "—" : `${parsed >= 0 ? "+" : ""}${(parsed * 100).toFixed(2)}%`;
  };
  return <Section title={title} icon={<BookOpen size={16} />}
    description="Independent durable paper ledger; no neural watchlist rows are mixed into this strategy.">
    <div className="mb-3 flex items-center justify-between text-xs text-text-muted">
      <span>{rows.length} retained events</span>
      <button onClick={onBtst} className="text-accent-blue underline">Open market-profile lane</button>
    </div>
    {!rows.length ? <p className="text-sm text-text-secondary">No journal events yet.</p> : <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead><tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
          {['Signal', 'Symbol', 'Entry', 'Exit / latest', 'Net return', 'State'].map((label) => <th key={label} className="py-2 pr-3">{label}</th>)}
        </tr></thead>
        <tbody>{rows.map((row: any) => <tr key={row.event_key} className="border-b border-bg-border/50">
          <td className="py-2 pr-3 font-mono text-xs">{row.source_session}</td>
          <td className="py-2 pr-3 font-medium text-text-primary">{row.symbol}</td>
          <td className="py-2 pr-3 font-mono">{formatNumber(num(row.entry_mark), 2)}</td>
          <td className="py-2 pr-3 font-mono">{formatNumber(num(row.latest_mark), 2)}</td>
          <td className={`py-2 pr-3 font-mono ${tone(num(row.realized_return_pct))}`}>{pct(row.realized_return_pct)}</td>
          <td className="py-2 pr-3"><StatusBadge label={String(row.status).replaceAll("_", " ")}
            variant={row.status === "closed" ? "success" : "warn"} /></td>
        </tr>)}</tbody>
      </table>
    </div>}
  </Section>;
}

function CurrentSwingRow({ row, provisional, sharedReason }: {
  row: any; provisional: boolean; sharedReason?: string | null;
}) {
  const quote = useQuote(row.live_symbol ?? row.instrument);
  const freshQuote = quote?.ltp != null
    && Date.now() - quote.rxAt <= 30_000
    && Math.abs(Date.now() - quote.ts) <= 30_000;
  const persisted = num(row.latest_mark ?? row.source_mark);
  const liveMark = row.status === "closed" ? persisted : freshQuote ? num(quote?.ltp) : persisted;
  const entry = num(row.entry_mark);
  const hasPostEntryMark = row.entry_ts != null && row.latest_ts != null
    && Date.parse(row.latest_ts) > Date.parse(row.entry_ts);
  const measured = row.status === "closed" || freshQuote || hasPostEntryMark;
  const liveReturn = measured && entry != null && entry > 0 && liveMark != null
    ? liveMark / entry - 1 : null;
  const pct = (value: unknown) => {
    const parsed = num(value);
    return parsed == null ? "—" : `${parsed >= 0 ? "+" : ""}${(parsed * 100).toFixed(2)}%`;
  };
  const trackedReturns = [1, 2, 3].map((horizon) => ({
    horizon,
    value: row[`day_${horizon}_return_pct`],
  }));
  return <tr className="border-b border-bg-border/50">
    <td className="py-2 pr-3 font-mono text-text-muted">#{row.side_rank ?? row.rank}</td>
    <td className="py-2 pr-3">
      <div className="font-medium text-text-primary">{row.symbol} {row.option_type}</div>
      <div className="font-mono text-[11px] text-text-muted">{row.instrument}</div>
      {row.contract_kind && <div className="text-[10px] text-text-muted">{row.contract_kind} · D+{row.horizon_sessions}</div>}
    </td>
    <td className="py-2 pr-3 text-right font-mono">{row.horizon_sessions ? `D+${row.horizon_sessions}` : pct(row.q50_return)}</td>
    <td className={`py-2 pr-3 text-right font-mono ${tone(num(row.combined_score ?? row.ranking_score))}`}>
      {row.combined_score != null ? formatNumber(num(row.combined_score), 3) : pct(row.ranking_score)}
    </td>
    <td className="py-2 pr-3 text-right font-mono">{formatNumber(entry, 2)}</td>
    <td className="py-2 pr-3 text-right font-mono">
      {formatNumber(liveMark, 2)}
      {row.status !== "closed" && freshQuote && <div className="text-[10px] text-accent-green">live · ≤150 ms batch</div>}
      {row.status !== "closed" && !freshQuote && row.entry_mark != null && !hasPostEntryMark
        && <div className="text-[10px] text-accent-amber">entry mark only</div>}
    </td>
    <td className={`py-2 pr-3 text-right font-mono ${tone(liveReturn)}`}>{pct(liveReturn)}</td>
    {trackedReturns.map(({ horizon, value }) => <td key={horizon}
      className={`py-2 pr-3 text-right font-mono ${tone(num(value))}`}>{pct(value)}</td>)}
    <td className="py-2 pr-3"><StatusBadge
      label={String(row.status ?? (provisional ? "provisional" : "awaiting_entry")).replaceAll("_", " ")}
      variant={row.status === "closed" ? "success" : provisional ? "info" : "warn"} /></td>
    <td className="py-2 pr-3">
      {row.actionable_reason !== undefined
        ? <>
            {row.paper_position && <StatusBadge label="shadow tracked" variant="info" />}
            <StatusBadge label={row.actionable ? "actionable" : "research only"}
              variant={row.actionable ? "success" : "info"} />
            {row.actionable_reason && row.actionable_reason !== sharedReason
              && <div className="mt-1 max-w-[22rem] text-[10px] leading-snug text-text-muted">
                {row.actionable_reason}
              </div>}
            {row.actionable && row.sizing_lots != null && <div className="mt-1 font-mono text-[10px] text-text-muted">
              {row.sizing_lots} lot(s) · {row.sizing_method}
            </div>}
          </>
        : <StatusBadge
            label={row.combined_score != null ? "rank selected" : row.qualified ? "positive margin" : "no margin"}
            variant={row.combined_score != null || row.qualified ? "success" : "warn"} />}
    </td>
  </tr>;
}


/** The daily output is two layers, so the desk shows two, not one list with a
 *  flag. The research ranking is mandatory and always complete; the actionable
 *  list is allowed to be empty and has to say why it is. */
function ActionableBanner({ swing, total }: { swing: any; total: number }) {
  const actionable = swing?.actionable;
  if (!actionable) return null;
  const empty = (actionable.count ?? 0) === 0;
  return <div className={`rounded-xl border p-3 text-sm ${empty
    ? "border-accent-amber/40 bg-accent-amber/5" : "border-accent-green/40 bg-accent-green/5"}`}>
    <div className="flex flex-wrap items-center gap-2">
      <StatusBadge label={`actionable ${actionable.count ?? 0}/${total || 20}`} variant={empty ? "warn" : "success"} />
      <span className="text-text-secondary">
        Gates: {actionable.gates}. An empty actionable list is a valid daily output.
      </span>
    </div>
    {actionable.note && <p className="mt-2 text-xs text-text-muted">{actionable.note}</p>}
  </div>;
}

function WatchlistTab({ data, strategies, selectedSession, onSession, onBtst }: {
  data?: any; strategies?: any; selectedSession: string; onSession: (session: string) => void; onBtst: () => void;
}) {
  const [lane, setLane] = useState<VanguardLane>("swing_1_2d");
  const [view, setView] = useUrlTab("current", "watchView");
  const run = data?.latest;
  const items = data?.items ?? [];
  const history = data?.history ?? [];
  const preview = data?.preview;
  const previewItems = data?.preview_items ?? [];
  const swingRun = strategies?.swing?.latest;
  const currentRun = swingRun ?? data?.current;
  const currentHead = swingRun ?? preview ?? currentRun;
  const currentItems = swingRun ? (strategies?.swing?.items ?? [])
    : preview ? previewItems : (data?.current_items ?? []);
  const research = strategies?.swing?.research_ranking ?? { CE: [], PE: [] };
  const quotesConnected = useQuotesConnection();
  const summary = data?.exit_summary ?? {};
  const benchmark = data?.market_benchmark ?? {};
  const modelSuccesses = data?.model_successes ?? [];
  const laneTabs = <LaneTabs lane={lane} onLane={setLane} journals={strategies?.journals} />;
  const pct = (value: unknown, digits = 2) => {
    const parsed = num(value);
    return parsed == null ? "—" : `${parsed >= 0 ? "+" : ""}${(parsed * 100).toFixed(digits)}%`;
  };
  const peaks = [...items].filter((r: any) => r.exit_analysis?.max_return_pct != null)
    .sort((a: any, b: any) => b.exit_analysis.max_return_pct - a.exit_analysis.max_return_pct)
    .slice(0, 3);
  if (lane !== "swing_1_2d") return <div className="space-y-4">{laneTabs}
    <StrategyJournal strategy={lane} rows={strategies?.journals?.[lane] ?? []} onBtst={onBtst} />
  </div>;
  if (!run && !currentHead) return <div className="space-y-4">{laneTabs}
    <Section title="Daily model watchlist" icon={<Activity size={16} />}>
      <p className="text-sm text-text-secondary">No current ranking or frozen list is available.</p>
    </Section>
  </div>;
  const winners = num(run?.winners) ?? 0;
  const advancedMarks = currentItems.filter((row: any) => row.entry_ts && row.latest_ts
    && Date.parse(row.latest_ts) > Date.parse(row.entry_ts)).length;
  const unknownSourceVolume = currentItems.filter((row: any) => Number(row.option_volume) === 0).length;
  return (
    <div className="space-y-4">
      {laneTabs}
      <div className="inline-flex rounded-xl border border-bg-border bg-bg-card p-1" role="tablist" aria-label="Vanguard watchlist views">
        <button
          type="button"
          role="tab"
          aria-selected={view === "current"}
          onClick={() => setView("current")}
          className={`rounded-lg px-4 py-2 text-sm transition-colors ${view === "current" ? "bg-accent-blue text-white" : "text-text-secondary hover:text-text-primary"}`}
        >
          Current watchlist <span className="ml-1 font-mono text-xs opacity-75">{currentItems.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "frozen"}
          onClick={() => setView("frozen")}
          className={`rounded-lg px-4 py-2 text-sm transition-colors ${view === "frozen" ? "bg-accent-blue text-white" : "text-text-secondary hover:text-text-primary"}`}
        >
          Frozen watchlist <span className="ml-1 font-mono text-xs opacity-75">{run?.item_count ?? 0}</span>
        </button>
      </div>

      {view === "current" && (currentHead ? <Section
        title="Current · 1–3 session directional ranking"
        icon={<Activity size={16} />}
        description={preview && !swingRun
          ? "Latest completed model snapshot. It remains provisional until the EOD freeze."
          : "Latest automatically frozen model emission. Its membership is immutable while next-session marks update."}
        rightSlot={<div className="flex items-center gap-2">
          <StatusBadge label={quotesConnected ? "quote socket connected" : "persisted marks"} variant="info" />
          <StatusBadge label={preview && !swingRun ? "provisional · observation only" : currentHead.status.replaceAll("_", " ")}
            variant={preview && !swingRun ? "info" : currentHead.status === "closed" ? "success" : "warn"} />
        </div>}
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <MetricTile label="Session" value={currentHead.source_session} detail={formatIST(currentHead.prediction_ts)} />
          <MetricTile label="Ranked" value={String(currentHead.item_count ?? 0)}
            detail={swingRun ? `${research.CE.length} CE · ${research.PE.length} PE` : "one side per underlying"} />
          <MetricTile
            label={swingRun ? "Actionable" : "Positive margin"}
            value={swingRun ? `${strategies?.swing?.actionable?.count ?? 0}/${research.CE.length + research.PE.length || 20}` : `${currentHead.qualified ?? 0}/${currentHead.item_count ?? 0}`}
            detail={swingRun ? "after confidence, liquidity and M7" : "chosen CE/PE margin above zero"}
          />
          <MetricTile label="Model" value={String(currentHead.direction_model_version ?? currentHead.model_version).replace("mlp_quantile_", "")} />
        </div>
        {swingRun && <>
          <p className="mt-3 rounded-lg border border-accent-amber/40 bg-accent-amber/5 p-3 text-xs text-accent-amber">
            {advancedMarks}/{currentItems.length} contracts have a post-entry archived mark.
            {unknownSourceVolume > 0 && ` ${unknownSourceVolume} decision-bar snapshots report zero volume; chain snapshots can omit traded volume, so this is not proof of no trades.`}
            {' '}Unchanged entry marks are excluded from running-return claims. D+1–D+3 outcomes remain pending until their sessions complete.
          </p>
          <div className="mt-3 grid grid-cols-3 gap-2">
            {[1, 2, 3].map((horizon) => <MetricTile key={horizon}
              label={`Tracked D+${horizon}`}
              value={pct(swingRun[`day_${horizon}_avg_return_pct`] ?? currentHead[`day_${horizon}_avg_return_pct`])}
              detail={`${swingRun[`day_${horizon}_resolved`] ?? currentHead[`day_${horizon}_resolved`] ?? 0} resolved`} />)}
          </div>
          <div className="mt-3"><ActionableBanner swing={strategies?.swing}
            total={research.CE.length + research.PE.length} /></div>
        </>}
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[1320px] text-sm">
            <thead>
              <tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
                <th className="py-2 pr-3">Rank</th>
                <th className="py-2 pr-3">Contract</th>
                <th className="py-2 pr-3 text-right">{swingRun ? "Horizon" : "Median"}</th>
                <th className="py-2 pr-3 text-right">{swingRun ? "Combined rank" : "Directional margin"}</th>
                <th className="py-2 pr-3 text-right">Entry</th>
                <th className="py-2 pr-3 text-right">Latest</th>
                <th className="py-2 pr-3 text-right">Gross return</th>
                <th className="py-2 pr-3 text-right">D+1 return</th>
                <th className="py-2 pr-3 text-right">D+2 return</th>
                <th className="py-2 pr-3 text-right">D+3 return</th>
                <th className="py-2 pr-3">Tracker state</th>
                <th className="py-2 pr-3">Qualification</th>
              </tr>
            </thead>
            <tbody>
              {swingRun
                ? (["CE", "PE"] as const).flatMap((side) => [
                    <tr key={`head-${side}`} className="border-b border-bg-border/50">
                      <td colSpan={12} className="pt-4 pb-1 text-[11px] uppercase tracking-wider text-text-muted">
                        Top {research[side].length} {side}
                      </td>
                    </tr>,
                    ...research[side].map((row: any) => <CurrentSwingRow
                      key={`${row.symbol}-${row.option_type}-${row.horizon_sessions ?? 0}`}
                      row={row} provisional={false}
                      sharedReason={strategies?.swing?.actionable?.note} />),
                  ])
                : currentItems.map((row: any) => <CurrentSwingRow key={`${row.symbol}-${row.option_type}-${row.horizon_sessions ?? 0}`}
                    row={row} provisional={Boolean(preview && !swingRun)} />)}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-accent-amber">
          The top-10 CE and top-10 PE rows are frozen shadow observations, not executed paper positions. Exact-contract marks are tracked at D+1, D+2 and D+3; no broker order is created.
        </p>
      </Section> : <Section title="Current watchlist" icon={<Activity size={16} />}>
        <p className="text-sm text-text-secondary">No current 1–3 session ranking is available.</p>
      </Section>)}

      {view === "frozen" && (run ? <>
      <Section title={run.horizon_bars === 24 ? "Neural 1–2 session watchlist" : "Historical neural watchlist"} icon={<Activity size={16} />}
        description={data.provenance}
        rightSlot={
          <select aria-label="Watchlist source session" value={selectedSession}
            onChange={(e) => onSession(e.target.value)}
            className="rounded-lg border border-bg-border bg-bg-card px-3 py-2 text-sm text-text-primary">
            <option value="">Latest completed list</option>
            {history.map((r: any) => <option key={r.source_session} value={r.source_session}>
              {r.source_session} · {r.item_count} names
            </option>)}
          </select>
        }>
        <p className="mb-3 text-xs text-text-muted">
          {data.btst_note} <button onClick={onBtst} className="text-accent-blue underline">View BTST / MP book</button>
        </p>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <MetricTile label="Frozen session" value={run.source_session} detail={run.status.replaceAll("_", " ")} />
          <MetricTile label="Full-session paths" value={`${run.resolved ?? 0}/${run.item_count}`} detail={run.track_session ?? "awaiting next session"} />
          <MetricTile label="Hold return · gross" value={pct(run.avg_return_pct)} color={tone(num(run.avg_return_pct))} />
          <MetricTile label="Winners" value={run.resolved ? `${winners}/${run.resolved}` : "—"} detail="positive complete-session returns" />
        </div>
        <div className="mt-2 grid gap-2 md:grid-cols-3">
          <MetricTile label="Runner exits · net" value={pct(summary.runner_net_mean)}
            color={tone(num(summary.runner_net_mean))} detail={`${summary.runner_exited ?? 0}/${items.length} resolved · assumed 1% cost`} />
          <MetricTile label="Paired hold · net" value={pct(summary.paired_hold_net_mean)}
            color={tone(num(summary.paired_hold_net_mean))} detail="same contracts as resolved exits" />
          <MetricTile label="Stop-only control · net" value={pct(summary.stop_only_net_mean)}
            color={tone(num(summary.stop_only_net_mean))} detail="same 15% stop, without trailing" />
        </div>
        <p className="mt-3 text-xs text-text-muted">{data.performance_basis}</p>
        <p className="mt-1 text-xs text-accent-amber">{data.horizon_note}</p>
        {summary.fully_paired && <p className="mt-2 rounded-lg border border-bg-border p-3 text-sm text-text-secondary">
          {num(summary.runner_net_mean)! < num(summary.paired_hold_net_mean)!
            ? "This replay's runner underperformed holding to the same cutoff. It is not promoted. "
            : "This is a single-list comparison, not proof of a repeatable improvement. "}
          Worst contract: runner {pct(summary.worst_runner_net)}, hold {pct(summary.worst_paired_hold_net)}.
          These are individual premium returns, not portfolio drawdown.
        </p>}
        <p className="mt-1 break-all font-mono text-[10px] text-text-muted">
          Model: {run.model_version} · frozen {formatIST(run.generated_at)}
        </p>
        {!items.length && <p className="mt-3 text-sm text-accent-amber">
          This historical frozen list used the former threshold-only membership rule, so no observation rows were retained. Select an earlier session for performance; future frozen lists retain the top ranking and label qualification separately.
        </p>}
      </Section>

      {!!modelSuccesses.length && <Section title="Model selection successes"
        description="Positive full-session outcomes among this frozen top-ten list. Missing final candles are excluded from successes.">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[700px] text-sm">
            <thead><tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
              {['Model rank', 'Contract', 'Entry', '15:15 mark', 'Gross return', 'Market top-ten'].map((label) =>
                <th key={label} className="py-2 pr-3">{label}</th>)}
            </tr></thead>
            <tbody>{modelSuccesses.map((row: any) => <tr key={row.instrument} className="border-b border-bg-border/50">
              <td className="py-2 pr-3 font-mono text-text-muted">#{row.rank}</td>
              <td className="py-2 pr-3">
                <div className="font-medium text-text-primary">{row.symbol} {row.option_type}</div>
                <div className="font-mono text-[10px] text-text-muted">{row.instrument}</div>
              </td>
              <td className="py-2 pr-3 font-mono">{formatNumber(num(row.entry_mark), 2)}</td>
              <td className="py-2 pr-3 font-mono">{formatNumber(num(row.close_mark), 2)}</td>
              <td className="py-2 pr-3 font-mono text-accent-green">{pct(row.return_pct)}</td>
              <td className="py-2 pr-3">
                {row.market_side_rank
                  ? <StatusBadge label={`#${row.market_side_rank} ${row.option_type}`} variant="success" />
                  : <span className="text-xs text-text-muted">outside top 10</span>}
              </td>
            </tr>)}</tbody>
          </table>
        </div>
      </Section>}

      {benchmark.available && <Section title="Market-data top ten · CE and PE"
        description={`Hindsight benchmark for ${benchmark.track_session}: same exact contracts available to the model, ranked from the next session's first 30-minute close to the 15:15 IST cutoff. It is not a model selection.`}>
        <div className="grid gap-4 xl:grid-cols-2">
          {([['CE', benchmark.ce], ['PE', benchmark.pe]] as [string, any[]][]).map(([side, rows]) =>
            <div key={side} className="overflow-x-auto rounded-xl border border-bg-border p-3">
              <div className="mb-2 flex items-center justify-between">
                <h4 className="text-sm font-medium text-text-primary">Top 10 {side}</h4>
                <span className="text-xs text-text-muted">{benchmark.coverage?.[side.toLowerCase()] ?? 0} complete paths</span>
              </div>
              <table className="w-full min-w-[500px] text-sm">
                <thead><tr className="border-b border-bg-border text-left text-[10px] uppercase tracking-wider text-text-muted">
                  <th className="py-2 pr-2">Rank</th><th className="py-2 pr-2">Contract</th>
                  <th className="py-2 pr-2 text-right">Entry</th><th className="py-2 pr-2 text-right">15:15</th>
                  <th className="py-2 text-right">Return</th>
                </tr></thead>
                <tbody>{rows.map((row: any) => <tr key={row.instrument} className="border-b border-bg-border/50">
                  <td className="py-2 pr-2 font-mono text-text-muted">#{row.side_rank}</td>
                  <td className="py-2 pr-2">
                    <div className="font-medium text-text-primary">{row.symbol} {side}</div>
                    {row.model_selected && <div className="text-[10px] text-accent-blue">model top-ten selection</div>}
                  </td>
                  <td className="py-2 pr-2 text-right font-mono">{formatNumber(num(row.entry_mark), 2)}</td>
                  <td className="py-2 pr-2 text-right font-mono">{formatNumber(num(row.close_mark), 2)}</td>
                  <td className="py-2 text-right font-mono text-accent-green">{pct(row.return_pct)}</td>
                </tr>)}</tbody>
              </table>
            </div>)}
        </div>
        <p className="mt-3 text-xs text-text-muted">
          Universe: {benchmark.universe}. The table requires both the 09:15 and 14:45 candle labels, available at 09:45 and 15:15 IST respectively.
        </p>
      </Section>}

      {!!peaks.length && <Section title="Largest post-entry opportunities"
        description="Actual candle highs after the first-close entry. These peaks are not guaranteed fills, realized profit, or model accuracy.">
        <div className="grid gap-3 md:grid-cols-3">
          {peaks.map((r: any) => <div key={r.symbol} className="rounded-xl border border-accent-green/20 bg-accent-green/5 p-4">
            <div className="text-sm text-text-primary">{r.symbol} {r.option_type}</div>
            <div className="mt-1 font-mono text-2xl text-accent-green">{pct(r.exit_analysis.max_return_pct)}</div>
            <div className="mt-1 text-xs text-text-muted">
              Peak in bar {formatIST(r.exit_analysis.peak_bar_ts)}
            </div>
            <div className="mt-3 flex justify-between text-xs text-text-secondary">
              <span>Hold {pct(r.return_pct)}</span>
              <span>Runner net {pct(r.exit_analysis.runner?.net_return_pct)}</span>
            </div>
          </div>)}
        </div>
      </Section>}

      <Section title="Profit-protection runner · shadow only"
        description={!summary.analysed ? "Registered shadow policy; no entry path has been evaluated for this list yet." :
          summary.prospective ? "Policy registered before entry; prospective observation." :
          "Retrospective replay — policy introduced after this session. Not a validated improvement."}>
        <div className="grid gap-3 text-sm text-text-secondary md:grid-cols-4">
          <div><span className="font-medium text-text-primary">Protect capital</span><br />Initial stop −15%.</div>
          <div><span className="font-medium text-text-primary">Protect a winner</span><br />After +20%, lock entry plus assumed costs.</div>
          <div><span className="font-medium text-text-primary">Keep the runner</span><br />After +30%, lock 50% of peak gain. No profit cap.</div>
          <div><span className="font-medium text-text-primary">Finish the session</span><br />Exit at 15:15 IST. No overnight extension.</div>
        </div>
        <p className="mt-3 text-xs text-text-muted">
          Ratchets take effect in the next candle only. Gaps through a stop use the next open, not a guaranteed stop price.
          Missing candles invalidate the affected path. This simulation creates no ticket or broker order and changes no held position.
          Version {data.exit_policy?.version ?? "not registered"} · 30-minute resolution.
        </p>
      </Section>

      {!!items.length && <Section title="Contract return and exit audit" description="Compare the same exact contracts. Source marks are context only; the opening gap is not credited.">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1120px] text-sm">
            <thead><tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
              {["Rank", "Contract", "Entry close", "Last close", "Hold gross", "Post-entry best", "Post-entry worst",
                "Runner exit", "Runner net", "State"].map((s) => <th key={s} className="py-2 pr-3">{s}</th>)}
            </tr></thead>
            <tbody>{items.map((r: any) => {
              const a = r.exit_analysis;
              const exit = a?.runner;
              return <tr key={r.rank} className="border-b border-bg-border/50 align-top">
                <td className="py-3 pr-3 font-mono text-text-muted">#{r.rank}</td>
                <td className="py-3 pr-3">
                  <div className="font-medium text-text-primary">{r.symbol} {r.option_type}</div>
                  <div className="font-mono text-[10px] text-text-muted">{r.instrument}</div>
                  <div className="mt-1 text-[10px] text-text-muted">Model edge {pct(r.conservative_edge)}</div>
                </td>
                <td className="py-3 pr-3 font-mono">
                  {formatNumber(num(r.entry_mark), 2)}
                  <div className="text-[10px] text-text-muted">{a?.entry_available_at ? formatIST(a.entry_available_at) : "not audited"}</div>
                </td>
                <td className="py-3 pr-3 font-mono">
                  {formatNumber(num(r.latest_mark), 2)}
                  <div className="text-[10px] text-text-muted">{a?.latest_available_at ? formatIST(a.latest_available_at) : "not audited"}</div>
                </td>
                <td className={`py-3 pr-3 font-mono ${tone(num(r.return_pct))}`}>{pct(r.return_pct)}</td>
                <td className="py-3 pr-3 font-mono text-accent-green">{pct(a?.max_return_pct)}</td>
                <td className="py-3 pr-3 font-mono text-accent-red">{pct(a?.min_return_pct)}</td>
                <td className="py-3 pr-3">
                  <div className="font-mono">{formatNumber(num(exit?.exit_mark), 2)}</div>
                  <div className="text-[10px] text-text-muted">{exit?.reason?.replaceAll("_", " ") ?? exit?.status ?? "not audited"}</div>
                </td>
                <td className={`py-3 pr-3 font-mono ${tone(num(exit?.net_return_pct))}`}>{pct(exit?.net_return_pct)}</td>
                <td className="py-3 pr-3">
                  <StatusBadge label={r.status.replaceAll("_", " ")} variant={r.status === "closed" ? "success" : "warn"} />
                  {a?.source_mark_age_minutes > 0 && <div className="mt-1 text-[10px] text-accent-amber">
                    Legacy source {a.source_mark_age_minutes}m older than prediction
                  </div>}
                </td>
              </tr>;
            })}</tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-text-muted">
          Original pre-correction values are retained in the audit record. Best/worst now exclude prices before entry.
          Gross and net figures are equal-weight option-premium returns, not a capital-sized portfolio.
        </p>
      </Section>}
      </> : <Section title="Frozen watchlist" icon={<Activity size={16} />}>
        <p className="text-sm text-text-secondary">No frozen list is available yet.</p>
      </Section>)}
    </div>
  );
}

// ─── Book ───────────────────────────────────────────────────────────────────

function BookTab({ book, capital, summary }: { book?: any; capital?: any; summary?: any }) {
  const closed = book?.closed ?? [];
  const open = book?.open_positions ?? [];
  const equity = book?.equity_curve ?? [];
  const hitRate = num(summary?.book?.hit_rate);

  return (
    <div className="space-y-4">
      <Section title="Paper book" icon={<BookOpen size={16} />}>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-5">
          <MetricTile label="Equity" value={formatMoney(num(capital?.ending_equity))} />
          <MetricTile
            label="Realized P&L · all closes"
            value={formatSignedMoney(num(summary?.book?.realized_pnl))}
            color={tone(num(summary?.book?.realized_pnl))}
          />
          <MetricTile label="Open" value={String(open.length)} />
          <MetricTile label="Closed" value={String(summary?.book?.closed_positions ?? closed.length)} />
          <MetricTile
            label="Hit rate"
            value={hitRate == null ? "—" : `${(hitRate * 100).toFixed(1)}%`}
            detail={hitRate == null ? "no closed trades yet" : undefined}
          />
        </div>
        <p className="mt-3 text-xs text-text-muted">
          Unrealized P&amp;L is <Unmeasured why="Nothing marks open paper positions to market; a 0 here would read as a flat book." />{" "}
          — open positions are not marked to market, so it is genuinely uncomputed rather than zero.
        </p>
      </Section>

      <Section
        title="Closed trades"
        description="Every fill is simulated against real historical bars. `fill_method` is carried through verbatim so a simulated fill is never mistaken for a broker fill."
      >
        {closed.length === 0 ? (
          <p className="text-sm text-text-secondary">
            No position has ever been closed, because none has ever been opened — the selector has
            not emitted a ticket. See the Selection tab for which gate is binding.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm">
              <thead>
                <tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
                  <th className="py-2 pr-3">Symbol</th>
                  <th className="py-2 pr-3">Instrument</th>
                  <th className="py-2 pr-3 text-right">Entry</th>
                  <th className="py-2 pr-3 text-right">Exit</th>
                  <th className="py-2 pr-3">Reason</th>
                  <th className="py-2 pr-3 text-right">P&amp;L</th>
                  <th className="py-2 pr-3 text-right">R</th>
                </tr>
              </thead>
              <tbody>
                {closed.map((t: any) => (
                  <tr key={t.id} className="border-b border-bg-border/50">
                    <td className="py-2 pr-3 font-medium text-text-primary">{t.symbol}</td>
                    <td className="py-2 pr-3 font-mono text-xs text-text-secondary">{t.instrument}</td>
                    <td className="py-2 pr-3 text-right font-mono">{formatNumber(num(t.fill_price), 2)}</td>
                    <td className="py-2 pr-3 text-right font-mono">{formatNumber(num(t.exit_price), 2)}</td>
                    <td className="py-2 pr-3 text-xs text-text-secondary">{t.exit_reason}</td>
                    <td className={`py-2 pr-3 text-right font-mono ${tone(num(t.pnl_rupees))}`}>
                      {formatSignedMoney(num(t.pnl_rupees))}
                    </td>
                    <td className={`py-2 pr-3 text-right font-mono ${tone(num(t.r_multiple))}`}>
                      {formatNumber(num(t.r_multiple), 2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {equity.length > 0 && (
        <Section title="Equity curve" description="One row per session, written by M9's own capital rollup.">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead>
                <tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
                  <th className="py-2 pr-3">Session</th>
                  <th className="py-2 pr-3 text-right">Start</th>
                  <th className="py-2 pr-3 text-right">Realized</th>
                  <th className="py-2 pr-3 text-right">End</th>
                </tr>
              </thead>
              <tbody>
                {equity.map((row: any) => (
                  <tr key={row.dt} className="border-b border-bg-border/50">
                    <td className="py-2 pr-3 font-mono text-xs">{row.dt}</td>
                    <td className="py-2 pr-3 text-right font-mono">{formatMoney(num(row.starting_equity))}</td>
                    <td className={`py-2 pr-3 text-right font-mono ${tone(num(row.realized_pnl))}`}>
                      {formatSignedMoney(num(row.realized_pnl))}
                    </td>
                    <td className="py-2 pr-3 text-right font-mono">{formatMoney(num(row.ending_equity))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

// ─── Attribution ────────────────────────────────────────────────────────────

function AttributionTab({ data }: { data?: any }) {
  const latest = data?.latest;
  const report = latest?.report ?? {};
  const ic = report.component_ic ?? {};
  const deciles = report.conviction_decile_report ?? [];
  const adequate = report.decile_sample_size_adequate;

  return (
    <div className="space-y-4">
      <Section
        title="Cumulative record"
        icon={<Activity size={16} />}
        description={
          latest
            ? `M10 rollup as of ${latest.as_of_date}, over every closed paper trade up to that date.`
            : "No attribution run has been recorded."
        }
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <MetricTile label="Closed trades" value={String(latest?.n_tickets_closed ?? 0)} />
          <MetricTile
            label="Hit rate"
            value={latest?.hit_rate == null ? "—" : `${(latest.hit_rate * 100).toFixed(1)}%`}
            detail={latest?.hit_rate == null ? "no closed trades" : undefined}
          />
          <MetricTile
            label="Avg R"
            value={latest?.avg_r == null ? "—" : formatNumber(num(latest.avg_r), 3)}
            color={tone(num(latest?.avg_r))}
            detail={latest?.avg_r == null ? "no closed trades" : "R = P&L ÷ premium paid"}
          />
          <MetricTile
            label="Decile monotonic"
            value={
              latest?.conviction_decile_monotonic == null
                ? "—"
                : latest.conviction_decile_monotonic
                  ? "yes"
                  : "no"
            }
            detail={adequate === false ? "sample too small to be a verdict" : undefined}
          />
        </div>
        {adequate === false && (
          <p className="mt-3 text-xs text-accent-amber">
            Sample below the module&apos;s own threshold — these figures are reported, not
            concluded. The decile check is not a verdict yet.
          </p>
        )}
      </Section>

      <Section
        title="Per-component information coefficient"
        description="Correlation between each fused component score and the eventual R. Suppressed entirely until each component has enough paired observations — a coefficient from a handful of points is noise wearing a number."
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
          {["flow", "sector_rs", "timing", "regime", "leadlag"].map((k) => {
            const entry = ic[k] ?? {};
            return (
              <MetricTile
                key={k}
                size="sm"
                label={k.replace("_", " ")}
                value={entry.ic == null ? "—" : formatNumber(num(entry.ic), 3)}
                color={tone(num(entry.ic))}
                detail={entry.ic == null ? entry.reason ?? "not computed" : `n=${entry.n}`}
              />
            );
          })}
        </div>
      </Section>

      {deciles.length > 0 && (
        <Section title="Conviction deciles">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-sm">
              <thead>
                <tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
                  <th className="py-2 pr-3">Bucket</th>
                  <th className="py-2 pr-3">Conviction range</th>
                  <th className="py-2 pr-3 text-right">n</th>
                  <th className="py-2 pr-3 text-right">Win rate</th>
                  <th className="py-2 pr-3 text-right">Avg R</th>
                </tr>
              </thead>
              <tbody>
                {deciles.map((d: any) => (
                  <tr key={d.bucket} className="border-b border-bg-border/50">
                    <td className="py-2 pr-3 font-mono">{d.bucket}</td>
                    <td className="py-2 pr-3 font-mono text-xs">
                      {(d.conviction_range ?? []).join(" – ")}
                    </td>
                    <td className="py-2 pr-3 text-right font-mono">{d.n}</td>
                    <td className="py-2 pr-3 text-right font-mono">
                      {(Number(d.win_rate) * 100).toFixed(0)}%
                    </td>
                    <td className={`py-2 pr-3 text-right font-mono ${tone(num(d.avg_r))}`}>
                      {formatNumber(num(d.avg_r), 3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

// ─── Backtest ───────────────────────────────────────────────────────────────

function BacktestTab({ data }: { data?: any }) {
  const latest = data?.latest;
  const report = latest?.report ?? {};
  const deciles = report.conviction_decile_report ?? [];

  return (
    <div className="space-y-4">
      <Section
        title="Historical replay"
        icon={<FlaskConical size={16} />}
        description={
          latest
            ? `M8 replay of ${formatIST(latest.start_ts)} → ${formatIST(latest.end_ts)}, run ${formatIST(latest.run_at)}.`
            : "No backtest has been run."
        }
        rightSlot={<StatusBadge label="not a track record" variant="warn" />}
      >
        {!latest ? (
          <p className="text-sm text-text-secondary">No replay recorded yet.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <MetricTile label="Candidates" value={String(report.candidates_evaluated ?? 0)} />
              <MetricTile
                label="Emitted & closed"
                value={String(report.emitted_trades_closed ?? 0)}
                detail={
                  (report.emitted_trades_unresolved ?? 0) + (report.emitted_trades_without_sizing ?? 0) > 0
                    ? `${report.emitted_trades_unresolved ?? 0} unresolved, ${report.emitted_trades_without_sizing ?? 0} unsized`
                    : undefined
                }
              />
              <MetricTile
                label="Expectancy (net)"
                value={
                  report.expectancy_net_rupees == null
                    ? "—"
                    : formatSignedMoney(num(report.expectancy_net_rupees))
                }
                color={tone(num(report.expectancy_net_rupees))}
                detail="after modelled costs"
              />
              <MetricTile
                label="Max drawdown"
                value={
                  report.max_drawdown_rupees == null
                    ? "—"
                    : formatSignedMoney(num(report.max_drawdown_rupees))
                }
                color={tone(num(report.max_drawdown_rupees))}
              />
            </div>
            {report.decile_check_sample_size_adequate === false && (
              <p className="mt-3 text-xs text-accent-amber">
                Sample below the harness&apos;s own adequacy threshold — reported, not concluded.
              </p>
            )}
          </>
        )}
      </Section>

      {deciles.length > 0 && (
        <Section
          title="Conviction deciles (replay)"
          description="Computed over EVERY filter-passing candidate, not just emitted tickets — an emitted-only check would be tautological while conviction never reaches the emission threshold."
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-sm">
              <thead>
                <tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
                  <th className="py-2 pr-3">Bucket</th>
                  <th className="py-2 pr-3">Conviction range</th>
                  <th className="py-2 pr-3 text-right">n</th>
                  <th className="py-2 pr-3 text-right">Win rate</th>
                  <th className="py-2 pr-3 text-right">Avg R</th>
                </tr>
              </thead>
              <tbody>
                {deciles.map((d: any) => (
                  <tr key={d.bucket} className="border-b border-bg-border/50">
                    <td className="py-2 pr-3 font-mono">{d.bucket}</td>
                    <td className="py-2 pr-3 font-mono text-xs">
                      {(d.conviction_range ?? []).join(" – ")}
                    </td>
                    <td className="py-2 pr-3 text-right font-mono">{d.n}</td>
                    <td className="py-2 pr-3 text-right font-mono">
                      {(Number(d.win_rate) * 100).toFixed(0)}%
                    </td>
                    <td className={`py-2 pr-3 text-right font-mono ${tone(num(d.avg_r))}`}>
                      {formatNumber(num(d.avg_r), 3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

// ─── Pipeline ───────────────────────────────────────────────────────────────

function PipelineTab({ data }: { data?: any }) {
  const feeds = data?.feeds ?? [];
  const sessions = data?.recent_sessions ?? [];
  const withAll = data?.sessions_with_all_inputs ?? 0;
  const examined = data?.sessions_examined ?? 0;
  const ingest = data?.ingest_log ?? [];

  const coverageWarning = useMemo(
    () => examined > 0 && withAll < examined,
    [examined, withAll],
  );

  return (
    <div className="space-y-4">
      <Section
        title="Feed coverage"
        icon={<GitBranch size={16} />}
        description="M6 needs flow + sector RS (prior session) AND regime + timing (same bar) simultaneously. Where those coverages fail to intersect, no threshold tuning can produce a trade."
      >
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {feeds.map((f: any) => (
            <div key={f.table} className="rounded-xl border border-bg-border bg-bg-secondary/25 px-3 py-2">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-text-primary">{f.label}</span>
                <StatusBadge label={f.cadence === "bar" ? "per bar" : "per session"} variant="info" />
              </div>
              <div className="mt-1 font-mono text-xs text-text-secondary">
                {formatNumber(num(f.rows), 0)} rows · {f.entities} entities
              </div>
              <div className="mt-0.5 font-mono text-[11px] text-text-muted">
                {f.first_ts ? formatIST(f.first_ts) : "—"} → {f.last_ts ? formatIST(f.last_ts) : "—"}
              </div>
            </div>
          ))}
        </div>
        {coverageWarning && (
          <p className="mt-3 text-xs text-accent-amber">
            Only {withAll} of the last {examined} sessions carry all three per-symbol inputs. On the
            others the selector cannot form a candidate at all — a DATA gap, not a strategy decision.
          </p>
        )}
      </Section>

      {sessions.length > 0 && (
        <Section title="Recent sessions" description="Row counts per input, newest first.">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-sm">
              <thead>
                <tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
                  <th className="py-2 pr-3">Session</th>
                  <th className="py-2 pr-3 text-right">Flow</th>
                  <th className="py-2 pr-3 text-right">Timing</th>
                  <th className="py-2 pr-3 text-right">Regime</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((row: any) => {
                  const complete =
                    (row.flow_rows ?? 0) > 0 && (row.timing_rows ?? 0) > 0 && (row.regime_rows ?? 0) > 0;
                  return (
                    <tr key={String(row.session)} className="border-b border-bg-border/50">
                      <td className="py-2 pr-3 font-mono text-xs">
                        {String(row.session)}
                        {!complete && <span className="ml-2 text-accent-amber">incomplete</span>}
                      </td>
                      <td className={`py-2 pr-3 text-right font-mono ${row.flow_rows ? "" : "text-accent-red"}`}>
                        {row.flow_rows ?? 0}
                      </td>
                      <td className={`py-2 pr-3 text-right font-mono ${row.timing_rows ? "" : "text-accent-red"}`}>
                        {row.timing_rows ?? 0}
                      </td>
                      <td className={`py-2 pr-3 text-right font-mono ${row.regime_rows ? "" : "text-accent-red"}`}>
                        {row.regime_rows ?? 0}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {ingest.length > 0 && (
        <Section title="Collector log" description="Every M1 ingest run, successful or not (doctrine #5).">
          <div className="max-h-72 overflow-y-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-bg-border text-left text-[11px] uppercase tracking-wider text-text-muted">
                  <th className="py-2 pr-3">Collector</th>
                  <th className="py-2 pr-3">Target</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3 text-right">Rows</th>
                </tr>
              </thead>
              <tbody>
                {ingest.map((row: any, i: number) => (
                  <tr key={i} className="border-b border-bg-border/50">
                    <td className="py-2 pr-3 font-mono text-xs">{row.collector}</td>
                    <td className="py-2 pr-3 font-mono text-xs text-text-secondary">
                      {row.target_date ?? "—"}
                    </td>
                    <td className="py-2 pr-3">
                      <StatusBadge
                        label={row.status}
                        variant={row.status === "ok" ? "success" : row.status === "error" ? "error" : "neutral"}
                      />
                    </td>
                    <td className="py-2 pr-3 text-right font-mono">{row.rows_written ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}
