"use client";

/**
 * Multi-pane option study chart (TradingView-style) on lightweight-charts.
 *
 * Three stacked panes — price (candles + Bollinger band + KAMA + volume),
 * MACD, and RSI — rendered as separate lightweight-charts instances that
 * share ONE time domain and are kept in lock-step:
 *   • time scale  — pan/zoom on any pane propagates to the others
 *   • crosshair   — hovering any pane mirrors the vertical cursor on the rest
 *   • alignment   — a fixed right price-scale width pins every pane's plot area
 *                   to the same left edge, so bars line up vertically.
 * Only the bottom (RSI) pane shows the time axis, like TradingView panes.
 *
 * lightweight-charts is dynamically imported inside the effect so it never
 * touches SSR. Created once; series data is updated in place on prop change.
 */
import { useEffect, useRef } from "react";
import {formatIST} from "@/components/desk-ui";

import { CHART } from "@/components/strategies/shared";

export type StudyBar = { time: number | string; open: number; high: number; low: number; close: number; volume?: number };
export type StudyLine = {
  id: string;
  data: { time: number | string; value: number | null | undefined }[];
  color: string;
  lineWidth?: number;
  dashed?: boolean;
};

function toUnix(t: number | string): number {
  if (typeof t === "number") return t > 1e12 ? Math.floor(t / 1000) : t;
  const ms = Date.parse(t);
  return Math.floor((Number.isNaN(ms) ? Date.now() : ms) / 1000);
}

type LightweightTime = number | string | { year: number; month: number; day: number };

function chartTimeToDate(time: LightweightTime): Date | null {
  if (typeof time === "number") {
    const ms = time > 1e12 ? time : time * 1000;
    const parsed = new Date(ms);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  if (typeof time === "string") {
    const parsed = new Date(time);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  if (time && Number.isFinite(time.year) && Number.isFinite(time.month) && Number.isFinite(time.day)) {
    return new Date(Date.UTC(time.year, time.month - 1, time.day));
  }
  return null;
}

function formatISTChartTime(time: LightweightTime, _includeDate: boolean): string { const date=chartTimeToDate(time); return date?formatIST(date):""; }

// Build series data with ONE point per bar timestamp — a real value where the
// indicator is defined, otherwise a *whitespace* point ({ time } only) during
// the warm-up. This is what keeps the panes time-aligned: lightweight-charts
// syncs panes by LOGICAL (index) range, which equals time alignment only when
// every series spans the identical set of timestamps. If we dropped the
// warm-up nulls instead, MACD/RSI would have fewer points than the candles and
// their indices would map to different times → visibly misaligned panes.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildLine(bars: StudyBar[], values: (number | null | undefined)[] | undefined): any[] {
  if (!values) return [];
  const seen = new Set<number>();
  return bars
    .map((b, i) => ({ time: toUnix(b.time), v: values[i] }))
    .sort((a, b) => a.time - b.time)
    .filter((d) => (seen.has(d.time) ? false : seen.add(d.time)))
    .map((d) => (d.v != null && Number.isFinite(Number(d.v)) ? { time: d.time, value: Number(d.v) } : { time: d.time }));
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildHist(bars: StudyBar[], values: (number | null | undefined)[] | undefined, pos: string, neg: string): any[] {
  if (!values) return [];
  const seen = new Set<number>();
  return bars
    .map((b, i) => ({ time: toUnix(b.time), v: values[i] }))
    .sort((a, b) => a.time - b.time)
    .filter((d) => (seen.has(d.time) ? false : seen.add(d.time)))
    .map((d) =>
      d.v != null && Number.isFinite(Number(d.v))
        ? { time: d.time, value: Number(d.v), color: Number(d.v) >= 0 ? pos : neg }
        : { time: d.time },
    );
}

const PRICE_SCALE_WIDTH = 64; // fixed → every pane's plot area shares a left edge

export function OptionStudyChart({
  bars,
  overlays,
  macd,
  signal,
  histogram,
  rsi,
  height = 560,
}: {
  bars: StudyBar[];
  overlays: StudyLine[];
  macd?: (number | null)[];
  signal?: (number | null)[];
  histogram?: (number | null)[];
  rsi?: (number | null)[];
  height?: number;
}) {
  const priceRef = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const refs = useRef<{ charts: any[]; candle?: any; vol?: any; overlays: any[]; macdHist?: any; macdLine?: any; signalLine?: any; rsiLine?: any }>({ charts: [], overlays: [] });

  // ── create the three synced panes once ─────────────────────────────────
  useEffect(() => {
    let disposed = false;
    let ro: ResizeObserver | undefined;
    (async () => {
      const lw = await import("lightweight-charts");
      if (disposed || !priceRef.current || !macdRef.current || !rsiRef.current) return;
      const cs = getComputedStyle(priceRef.current.closest('.nomad-port') ?? document.documentElement);
      // CSS vars hold space-separated channels (Tailwind `rgb(var(--x))`);
      // lightweight-charts' parser needs comma-separated rgb().
      const col = (v: string) => `rgb(${(cs.getPropertyValue(v).trim() || "148 163 184").replace(/\s+/g, ", ")})`;

      const hPrice = Math.round(height * 0.58);
      const hMacd = Math.round(height * 0.21);
      const hRsi = height - hPrice - hMacd;

      const tickMarkFormatter = (time: LightweightTime, tickMarkType: number) => {
        const parsed = chartTimeToDate(time);
        if (!parsed) return "";
        if (tickMarkType === lw.TickMarkType.Year) {
          return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", year: "numeric" }).format(parsed);
        }
        if (tickMarkType === lw.TickMarkType.Month) {
          return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", month: "short" }).format(parsed);
        }
        if (tickMarkType === lw.TickMarkType.DayOfMonth) {
          return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit" }).format(parsed);
        }
        return formatISTChartTime(time, false);
      };

      const common = (h: number, showTime: boolean) => ({
        height: h,
        layout: { background: { type: lw.ColorType.Solid, color: "transparent" }, textColor: col("--text-secondary"), fontSize: 10 },
        grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.05)" } },
        rightPriceScale: { borderColor: col("--bg-border"), minimumWidth: PRICE_SCALE_WIDTH },
        localization: {
          locale: "en-IN",
          timeFormatter: (time: LightweightTime) => formatISTChartTime(time, true),
        },
        timeScale: {
          borderColor: col("--bg-border"),
          timeVisible: true,
          secondsVisible: false,
          visible: showTime,
          tickMarkFormatter,
        },
        crosshair: { mode: lw.CrosshairMode.Normal },
      });

      // Held as `any` (like the shared CandleChart): lightweight-charts types
      // lineWidth as a strict 1|2|3|4 union, which fights the float widths and
      // dynamic props we pass.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const priceChart: any = lw.createChart(priceRef.current, common(hPrice, false));
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const macdChart: any = lw.createChart(macdRef.current, common(hMacd, false));
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const rsiChart: any = lw.createChart(rsiRef.current, common(hRsi, true));

      // price pane: candles + volume + BB/KAMA overlays
      const candle = priceChart.addSeries(lw.CandlestickSeries, {
        upColor: col("--accent-green"),
        downColor: col("--accent-red"),
        wickUpColor: col("--accent-green"),
        wickDownColor: col("--accent-red"),
        borderVisible: false,
      });
      const vol = priceChart.addSeries(lw.HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
      priceChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
      const overlaySeries = overlays.map((o) =>
        priceChart.addSeries(lw.LineSeries, {
          color: o.color,
          lineWidth: o.lineWidth ?? 1,
          lineStyle: o.dashed ? 2 : 0,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        }),
      );

      // MACD pane: histogram + macd line + signal line, zero reference
      const macdHist = macdChart.addSeries(lw.HistogramSeries, { priceFormat: { type: "price", precision: 2, minMove: 0.01 }, priceLineVisible: false, lastValueVisible: false });
      const macdLine = macdChart.addSeries(lw.LineSeries, { color: CHART.blue, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
      const signalLine = macdChart.addSeries(lw.LineSeries, { color: CHART.amber, lineWidth: 1.2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      macdLine.createPriceLine({ price: 0, color: col("--bg-border"), lineWidth: 1, lineStyle: 0, axisLabelVisible: false });

      // RSI pane: line + 30/50/70 guides
      const rsiLine = rsiChart.addSeries(lw.LineSeries, { color: CHART.violet, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
      rsiLine.createPriceLine({ price: 70, color: CHART.red, lineWidth: 1, lineStyle: 2, axisLabelVisible: true });
      rsiLine.createPriceLine({ price: 50, color: col("--bg-border"), lineWidth: 1, lineStyle: 0, axisLabelVisible: false });
      rsiLine.createPriceLine({ price: 30, color: CHART.green, lineWidth: 1, lineStyle: 2, axisLabelVisible: true });

      refs.current = { charts: [priceChart, macdChart, rsiChart], candle, vol, overlays: overlaySeries, macdHist, macdLine, signalLine, rsiLine };

      // ── keep the three time scales in lock-step ──
      const charts = refs.current.charts;
      let syncing = false;
      charts.forEach((src: any) => {
        src.timeScale().subscribeVisibleLogicalRangeChange((range: any) => {
          if (syncing || !range) return;
          syncing = true;
          charts.forEach((t: any) => {
            if (t !== src) t.timeScale().setVisibleLogicalRange(range);
          });
          syncing = false;
        });
      });

      // ── mirror the crosshair (vertical cursor) across panes ──
      const seriesFor = new Map<any, any>([
        [priceChart, candle],
        [macdChart, macdLine],
        [rsiChart, rsiLine],
      ]);
      let crossing = false;
      charts.forEach((src: any) => {
        src.subscribeCrosshairMove((param: any) => {
          if (crossing) return;
          crossing = true;
          charts.forEach((t: any) => {
            if (t === src) return;
            const s = seriesFor.get(t);
            if (param.time === undefined || !param.point) t.clearCrosshairPosition();
            else {
              const price = s === rsiLine ? 50 : s === macdLine ? 0 : Number(param.seriesData?.get(candle)?.close ?? 0);
              t.setCrosshairPosition(price, param.time, s);
            }
          });
          crossing = false;
        });
      });

      pushData();
      priceChart.timeScale().fitContent();

      ro = new ResizeObserver(() => {
        const w = wrapRef.current?.clientWidth;
        if (w) charts.forEach((c: any) => c.applyOptions({ width: w }));
      });
      if (wrapRef.current) ro.observe(wrapRef.current);
      const w0 = wrapRef.current?.clientWidth;
      if (w0) charts.forEach((c: any) => c.applyOptions({ width: w0 }));
    })();
    return () => {
      disposed = true;
      ro?.disconnect();
      refs.current.charts.forEach((c) => c.remove());
      refs.current = { charts: [], overlays: [] };
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pushData() {
    const { candle, vol, overlays: ovSeries, macdHist, macdLine, signalLine, rsiLine, charts } = refs.current;
    if (!candle || !charts.length) return;

    const seen = new Set<number>();
    const candles = bars
      .map((b) => ({ time: toUnix(b.time), open: +b.open, high: +b.high, low: +b.low, close: +b.close }))
      .sort((a, b) => a.time - b.time)
      .filter((d) => (seen.has(d.time) ? false : seen.add(d.time)));
    candle.setData(candles);

    if (vol) {
      const vseen = new Set<number>();
      vol.setData(
        bars
          .map((b) => ({ time: toUnix(b.time), value: b.volume || 0, color: +b.close >= +b.open ? "rgba(0,212,163,0.4)" : "rgba(255,71,87,0.4)" }))
          .sort((a, b) => a.time - b.time)
          .filter((d) => (vseen.has(d.time) ? false : vseen.add(d.time))),
      );
    }

    // Overlays sit on the price pane (already spanning every candle), but we
    // still whitespace-fill them so their gaps render cleanly.
    ovSeries.forEach((s: any, i: number) => s.setData(buildLine(bars, overlays[i]?.data.map((p) => p.value))));

    macdHist?.setData(buildHist(bars, histogram, "rgba(0,212,163,0.55)", "rgba(255,71,87,0.55)"));
    macdLine?.setData(buildLine(bars, macd));
    signalLine?.setData(buildLine(bars, signal));
    rsiLine?.setData(buildLine(bars, rsi));
  }

  // update series in place on data change
  useEffect(() => {
    pushData();
    refs.current.charts[0]?.timeScale().fitContent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, overlays, macd, signal, histogram, rsi]);

  return (
    <div ref={wrapRef} className="relative w-full" style={{ height }}>
      <div ref={priceRef} className="w-full" />
      <div ref={macdRef} className="w-full" />
      <div ref={rsiRef} className="w-full" />
      {!bars?.length ? (
        <div className="absolute inset-0 flex items-center justify-center rounded-xl border border-dashed border-bg-border/60 text-sm text-text-muted">
          No premium bars available.
        </div>
      ) : null}
      {/* pane labels, top-left of each band */}
      {bars?.length ? (
        <>
          <PaneTag top={6} label="Premium · BB(20,2σ) · KAMA(10,2,30)" />
          <PaneTag top={Math.round(height * 0.58) + 6} label="MACD (12, 26, 9)" />
          <PaneTag top={Math.round(height * 0.79) + 6} label="RSI (14)" />
        </>
      ) : null}
    </div>
  );
}

function PaneTag({ top, label }: { top: number; label: string }) {
  return (
    <span
      className="pointer-events-none absolute left-2 z-10 rounded bg-bg-card/70 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.1em] text-text-muted"
      style={{ top }}
    >
      {label}
    </span>
  );
}
