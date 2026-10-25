"use client";

/**
 * Per-ATM-strike OHLC pop-up for the NSE signal desk.
 *
 * Renders the OPTION-PREMIUM series for one contract (underlying + expiry +
 * strike + side) as a TradingView-style multi-pane study: candles with
 * Bollinger Bands (20, 2σ) + Kaufman's adaptive MA (KAMA) on price, with MACD
 * and RSI panes below — all three panes time-synced, crosshair-linked and
 * vertically aligned (see OptionStudyChart). These are the four indicators the
 * 30m ATM MACD lane trades against. Opens from a row in the Signals watchlist.
 *
 * Data: GET /api/charts/option-ohlc?underlying&expiry&strike&option_type&interval
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CandlestickChart, ChevronLeft, ChevronRight, X } from "lucide-react";

import { REFRESH_MS, Section, StatusBadge, formatIST, formatNumber, tone } from "@/components/desk-ui";
import { CHART } from "@/components/strategies/shared";
import { OptionStudyChart, type StudyBar, type StudyLine } from "@/components/strategies/shared/OptionStudyChart";
import { getOptionOHLC } from "@/lib/api";

export type OptionChartContract = {
  underlying: string;
  direction: string; // CE | PE
  strike: number;
  expiry: string; // YYYY-MM-DD
  instrumentKey?: string | null;
  ltp?: number | null;
};

type Interval = "5minute" | "15minute" | "30minute";
const INTERVALS: Interval[] = ["5minute", "15minute", "30minute"];
const tfLabel = (tf: string) => tf.replace("minute", "m");

type Bar1 = { time: string; open: number; high: number; low: number; close: number; volume: number };
type IndicatorArrays = {
  macd?: (number | null)[];
  macd_signal?: (number | null)[];
  macd_histogram?: (number | null)[];
  rsi?: (number | null)[];
  bb_upper?: (number | null)[];
  bb_middle?: (number | null)[];
  bb_lower?: (number | null)[];
  kama?: (number | null)[];
};
type OptionOHLCResponse = {
  underlying?: string;
  expiry?: string;
  strike?: number;
  option_type?: string;
  interval?: string;
  bar_count?: number;
  bars?: Bar1[];
  indicators?: IndicatorArrays;
  detail?: string | null;
};

const lastOf = (arr?: (number | null)[]) => {
  if (!arr) return null;
  for (let i = arr.length - 1; i >= 0; i--) if (arr[i] != null) return arr[i] as number;
  return null;
};

export function OptionChartModal({
  contracts,
  index,
  onIndexChange,
  onClose,
}: {
  contracts: OptionChartContract[];
  index: number;
  onIndexChange: (i: number) => void;
  onClose: () => void;
}) {
  const [interval, setInterval] = useState<Interval>("30minute");
  const contract = contracts[index];
  const side = contract.direction?.toUpperCase() === "PE" ? "PE" : "CE";

  const hasPrev = index > 0;
  const hasNext = index < contracts.length - 1;
  const goPrev = () => hasPrev && onIndexChange(index - 1);
  const goNext = () => hasNext && onIndexChange(index + 1);

  // Lock body scroll while open (mount-only).
  useEffect(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prevOverflow;
    };
  }, []);

  // Keyboard: Escape closes, ←/→ step through the sorted list without closing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && hasPrev) onIndexChange(index - 1);
      else if (e.key === "ArrowRight" && hasNext) onIndexChange(index + 1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [index, hasPrev, hasNext, onClose, onIndexChange]);

  const query = useQuery({
    queryKey: ["option-ohlc", contract.underlying, contract.expiry, contract.strike, side, interval],
    queryFn: async () =>
      (
        await getOptionOHLC({
          underlying: contract.underlying,
          expiry: contract.expiry,
          strike: contract.strike,
          optionType: side,
          interval,
          limit: 400,
          instrumentKey: contract.instrumentKey ?? null,
        })
      ).data as OptionOHLCResponse,
    refetchInterval: REFRESH_MS.live * 8, // ~2m, matches the spot chart cadence
    refetchOnWindowFocus: false,
  });

  const data = query.data;
  const bars = useMemo<Bar1[]>(() => data?.bars ?? [], [data?.bars]);
  const indicators = useMemo<IndicatorArrays>(() => data?.indicators ?? {}, [data?.indicators]);

  const studyBars = useMemo<StudyBar[]>(
    () => bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume })),
    [bars],
  );

  // Bollinger band + KAMA as bar-aligned price overlays.
  const overlays = useMemo<StudyLine[]>(() => {
    if (!bars.length) return [];
    const line = (key: keyof IndicatorArrays): StudyLine["data"] =>
      bars.map((b, i) => ({ time: b.time, value: indicators[key]?.[i] ?? null }));
    return [
      { id: "bb_upper", data: line("bb_upper"), color: CHART.blue, lineWidth: 1 },
      { id: "bb_middle", data: line("bb_middle"), color: CHART.muted, lineWidth: 1, dashed: true },
      { id: "bb_lower", data: line("bb_lower"), color: CHART.blue, lineWidth: 1 },
      { id: "kama", data: line("kama"), color: CHART.amber, lineWidth: 2 },
    ];
  }, [bars, indicators]);

  const kpis = useMemo(() => {
    if (!bars.length) return null;
    const last = bars[bars.length - 1].close;
    const first = bars[0].close;
    return {
      last,
      chg: last - first,
      chgPct: first ? (last - first) / first : 0,
      kama: lastOf(indicators.kama),
      rsi: lastOf(indicators.rsi),
      macd: lastOf(indicators.macd),
      hist: lastOf(indicators.macd_histogram),
    };
  }, [bars, indicators]);

  const strikeLabel = Number.isInteger(contract.strike) ? String(contract.strike) : formatNumber(contract.strike, 2);
  const detail = data?.detail && bars.length === 0 ? data.detail : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-3 backdrop-blur-sm sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label={`OHLC chart for ${contract.underlying} ${strikeLabel} ${side}`}
      onClick={onClose}
    >
      <div
        className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-bg-border bg-bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 border-b border-bg-border/60 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <CandlestickChart size={18} className={side === "CE" ? "text-accent-green" : "text-accent-red"} />
            <div>
              <div className="flex items-center gap-2 text-[15px] font-semibold text-text-primary">
                <span>{contract.underlying}</span>
                <span className="font-mono">{strikeLabel}</span>
                <span
                  className={`rounded-md border px-1.5 py-0.5 text-[10px] font-bold ${
                    side === "CE"
                      ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
                      : "border-accent-red/40 bg-accent-red/10 text-accent-red"
                  }`}
                >
                  {side}
                </span>
              </div>
              <div className="text-[11px] text-text-muted">
                Exp {contract.expiry} · option premium · {tfLabel(interval)}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* Prev / next through the sorted watchlist — no need to close */}
            <div className="flex items-center rounded-lg border border-bg-border bg-bg-primary/30">
              <button
                type="button"
                onClick={goPrev}
                disabled={!hasPrev}
                aria-label="Previous instrument"
                title="Previous (←)"
                className="rounded-l-lg px-1.5 py-1 text-text-muted transition-colors enabled:hover:text-accent-blue disabled:cursor-not-allowed disabled:opacity-30"
              >
                <ChevronLeft size={16} />
              </button>
              <span className="min-w-[3.5rem] text-center font-mono text-[11px] text-text-secondary">
                {index + 1} / {contracts.length}
              </span>
              <button
                type="button"
                onClick={goNext}
                disabled={!hasNext}
                aria-label="Next instrument"
                title="Next (→)"
                className="rounded-r-lg px-1.5 py-1 text-text-muted transition-colors enabled:hover:text-accent-blue disabled:cursor-not-allowed disabled:opacity-30"
              >
                <ChevronRight size={16} />
              </button>
            </div>
            <div className="flex rounded-lg border border-bg-border bg-bg-primary/30 p-0.5">
              {INTERVALS.map((tf) => (
                <button
                  key={tf}
                  type="button"
                  onClick={() => setInterval(tf)}
                  className={`rounded-md px-2 py-0.5 text-[11px] font-medium transition-colors ${
                    interval === tf ? "bg-accent-blue/20 text-accent-blue" : "text-text-muted hover:text-text-secondary"
                  }`}
                >
                  {tfLabel(tf)}
                </button>
              ))}
            </div>
            {query.isFetching ? <StatusBadge label="loading" variant="info" /> : null}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close chart"
              className="rounded-lg border border-bg-border bg-bg-primary/30 p-1.5 text-text-muted transition-colors hover:text-text-primary"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {/* KPI strip */}
          <section className="grid grid-cols-3 gap-2 sm:grid-cols-6">
            <Kpi label="Last" value={formatNumber(kpis?.last, 2)} detail={kpis ? `${kpis.chg >= 0 ? "+" : ""}${formatNumber(kpis.chgPct * 100, 1)}%` : ""} color={tone(kpis?.chg)} />
            <Kpi label="KAMA" value={formatNumber(kpis?.kama, 2)} detail={kpis?.kama != null && kpis?.last != null ? (kpis.last >= kpis.kama ? "above" : "below") : ""} color={kpis?.kama != null && kpis?.last != null ? tone(kpis.last - kpis.kama) : undefined} />
            <Kpi label="RSI(14)" value={formatNumber(kpis?.rsi, 1)} color={kpis?.rsi != null ? (kpis.rsi >= 70 ? "text-accent-red" : kpis.rsi <= 30 ? "text-accent-green" : undefined) : undefined} />
            <Kpi label="MACD" value={formatNumber(kpis?.macd, 3)} color={tone(kpis?.macd)} />
            <Kpi label="Hist" value={formatNumber(kpis?.hist, 3)} color={tone(kpis?.hist)} />
            <Kpi label="Bars" value={String(bars.length)} detail={bars.length ? formatIST(bars[bars.length - 1].time) : ""} />
          </section>

          {query.isError ? (
            <div className="rounded-xl border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm text-accent-red">
              Failed to load chart data for this contract.
            </div>
          ) : null}
          {detail ? (
            <div className="rounded-xl border border-accent-amber/40 bg-accent-amber/10 px-4 py-3 text-sm text-accent-amber">{detail}</div>
          ) : null}

          {/* Time-synced price + MACD + RSI panes */}
          <Section title="Premium study · candles + MACD + RSI" icon={<CandlestickChart size={16} />}>
            <OptionStudyChart
              bars={studyBars}
              overlays={overlays}
              macd={indicators.macd}
              signal={indicators.macd_signal}
              histogram={indicators.macd_histogram}
              rsi={indicators.rsi}
              height={580}
            />
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] uppercase tracking-[0.12em] text-text-muted">
              <Legend color={CHART.blue} label="BB ±2σ(20)" />
              <Legend color={CHART.amber} label="KAMA(10,2,30)" />
              <Legend color={CHART.blue} label="MACD" />
              <Legend color={CHART.amber} label="signal" />
              <Legend color={CHART.violet} label="RSI(14)" />
              <span>{bars.length} bars · panes time-synced</span>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

function Kpi({ label, value, detail, color }: { label: string; value: string; detail?: string; color?: string }) {
  return (
    <div className="rounded-lg border border-bg-border bg-bg-primary/15 px-2.5 py-1.5">
      <div className="text-[9.5px] uppercase tracking-[0.12em] text-text-muted">{label}</div>
      <div className={`mt-0.5 font-mono text-[13px] font-semibold ${color || "text-text-primary"}`}>{value}</div>
      {detail ? <div className="text-[10px] text-text-muted">{detail}</div> : null}
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2 w-2.5 rounded-sm" style={{ backgroundColor: color }} aria-hidden />
      {label}
    </span>
  );
}
