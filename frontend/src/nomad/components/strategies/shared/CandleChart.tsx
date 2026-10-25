"use client";

/**
 * Institutional candlestick chart on TradingView lightweight-charts.
 * Canvas-rendered (crosshair, fit-content, volume pane, price-line overlays),
 * themed live from the app's CSS variables, created once and updated in place
 * (preserves zoom). lightweight-charts is dynamically imported inside the
 * effect so it never touches SSR.
 */
import {LineSeries} from "lightweight-charts";
import { useEffect, useRef } from "react";

export type CandleBar = { time: number | string; open: number; high: number; low: number; close: number; volume?: number };
export type ChartPriceLine = { price: number; color: string; title: string; dashed?: boolean };
/** A line series drawn over the candles (e.g. Bollinger band, KAMA). Null values are gaps. */
export type ChartLineSeries = {
  id: string;
  data: { time: number | string; value: number | null | undefined }[];
  color: string;
  lineWidth?: number;
  dashed?: boolean;
  title?: string;
};

function toUnix(t: number | string): number {
  if (typeof t === "number") return t > 1e12 ? Math.floor(t / 1000) : t;
  const ms = Date.parse(t);
  return Math.floor((Number.isNaN(ms) ? Date.now() : ms) / 1000);
}

export function CandleChart({
  bars,
  priceLines = [],
  overlays = [],
  height = 420,
  showVolume = true,
  tzOffsetMinutes = 330,
  fitKey,
  onChartReady,
  onChartDispose,
}: {
  bars: CandleBar[];
  priceLines?: ChartPriceLine[];
  overlays?: ChartLineSeries[];
  height?: number;
  showVolume?: boolean;
  /**
   * OPT-IN pane linking (2026-07-19). Called ONCE with the live chart + candle
   * series after creation, so a parent (the Structure view's
   * `LinkedChartProvider`) can wire a shared crosshair / synchronized zoom
   * across several panes. Default undefined: when it is not passed NOTHING is
   * subscribed and the three existing call sites (ChartsWorkbench,
   * OrderflowWorkbench, OptionStudyChart) behave byte-identically.
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onChartReady?: (chart: any, series: any) => void;
  /** Paired with `onChartReady` — fires just before the chart is removed. */
  onChartDispose?: () => void;
  /** Display the time axis in this fixed UTC offset (default +330 = IST).
   *  lightweight-charts renders the axis in UTC, so we shift the plotted
   *  timestamps by the offset to show local session times (no DST for IST). */
  tzOffsetMinutes?: number;
  /** When this changes (e.g. `${symbol}:${timeframe}`) the viewport re-fits to
   *  the new series. On a periodic data refresh with the same key the viewport
   *  is left alone, preserving the trader's pan/zoom. Omit to fit only once. */
  fitKey?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const refs = useRef<{ chart?: any; candle?: any; vol?: any; lines: any[]; overlays: any[] }>({ lines: [], overlays: [] });
  // Tracks the fitKey the viewport was last fitted for. null until the first
  // non-empty push, so we always fit once on initial load.
  const fitRef = useRef<string | null>(null);
  // The create-once effect has [] deps, so it would capture the FIRST callback
  // identity forever. Held in refs and read at call time instead.
  const readyRef = useRef(onChartReady);
  const disposeRef = useRef(onChartDispose);
  readyRef.current = onChartReady;
  disposeRef.current = onChartDispose;

  // create chart once
  useEffect(() => {
    let disposed = false;
    let ro: ResizeObserver | undefined;
    (async () => {
      const lw = await import("lightweight-charts");
      if (disposed || !containerRef.current) return;
      const cs = getComputedStyle(document.documentElement);
      // The app's CSS vars hold space-separated RGB channels (Tailwind
      // `rgb(var(--x))` convention, e.g. `--text-secondary: 148 163 184`).
      // lightweight-charts' color parser rejects the CSS Color-4 space form
      // (`rgb(148 163 184)`) and throws inside createChart, which aborts the
      // whole chart — so normalise the triplet to comma-separated rgb().
      const col = (v: string) =>
        `rgb(${(cs.getPropertyValue(v).trim() || "148 163 184").replace(/\s+/g, ", ")})`;
      const chart = lw.createChart(containerRef.current, {
        height,
        layout: { background: { type: lw.ColorType.Solid, color: "transparent" }, textColor: col("--text-secondary"), fontSize: 10 },
        grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.05)" } },
        rightPriceScale: { borderColor: col("--bg-border") },
        timeScale: { borderColor: col("--bg-border"), timeVisible: true, secondsVisible: false },
        crosshair: { mode: lw.CrosshairMode.Normal },
      });
      const candle = chart.addSeries(lw.CandlestickSeries, {
        upColor: col("--accent-green"),
        downColor: col("--accent-red"),
        wickUpColor: col("--accent-green"),
        wickDownColor: col("--accent-red"),
        borderVisible: false,
      });
      refs.current.chart = chart;
      refs.current.candle = candle;
      if (showVolume) {
        const vol = chart.addSeries(lw.HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" } });
        chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
        refs.current.vol = vol;
      }
      ro = new ResizeObserver(() => {
        if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
      });
      ro.observe(containerRef.current);
      chart.applyOptions({ width: containerRef.current.clientWidth });
      pushData();
      // Opt-in only. No callback ⇒ no subscription, no behaviour change.
      readyRef.current?.(chart, candle);
    })();
    return () => {
      disposed = true;
      ro?.disconnect();
      if (refs.current.chart) disposeRef.current?.();
      refs.current.chart?.remove();
      refs.current = { lines: [], overlays: [] };
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pushData() {
    const { candle, vol, chart } = refs.current;
    if (!candle || !chart) return;
    // Shift plotted timestamps to the desk timezone so the (UTC-rendering)
    // axis shows local session hours instead of UTC (e.g. 09:15 IST, not 03:45).
    const tzShift = (tzOffsetMinutes || 0) * 60;
    const seen = new Set<number>();
    const data = [...bars]
      .map((b) => ({ time: toUnix(b.time) + tzShift, open: +b.open, high: +b.high, low: +b.low, close: +b.close }))
      .sort((a, b) => a.time - b.time)
      .filter((d) => (seen.has(d.time) ? false : seen.add(d.time)));
    candle.setData(data);

    // Line overlays (Bollinger bands, KAMA, …) — recreated from props each
    // push so add/remove of an overlay is reflected without re-mounting.
    refs.current.overlays.forEach((s) => chart.removeSeries(s));
    refs.current.overlays = overlays.map((ov) => {
      const series = chart.addSeries(LineSeries, {
        color: ov.color,
        lineWidth: ov.lineWidth ?? 1,
        lineStyle: ov.dashed ? 2 : 0,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      const oseen = new Set<number>();
      series.setData(
        ov.data
          .filter((p) => p.value != null && Number.isFinite(Number(p.value)))
          .map((p) => ({ time: toUnix(p.time) + tzShift, value: Number(p.value) }))
          .sort((a, b) => a.time - b.time)
          .filter((d) => (oseen.has(d.time) ? false : oseen.add(d.time))),
      );
      return series;
    });
    if (vol) {
      const vseen = new Set<number>();
      vol.setData(
        [...bars]
          .map((b) => ({ time: toUnix(b.time) + tzShift, value: b.volume || 0, color: +b.close >= +b.open ? "rgba(0,212,163,0.4)" : "rgba(255,71,87,0.4)" }))
          .sort((a, b) => a.time - b.time)
          .filter((d) => (vseen.has(d.time) ? false : vseen.add(d.time))),
      );
    }
    refs.current.lines.forEach((l) => candle.removePriceLine(l));
    refs.current.lines = priceLines
      .filter((pl) => Number.isFinite(pl.price))
      .map((pl) => candle.createPriceLine({ price: pl.price, color: pl.color, lineWidth: 1, lineStyle: pl.dashed ? 2 : 0, axisLabelVisible: true, title: pl.title }));
    // Fit the viewport only on first load or when fitKey changes (symbol /
    // timeframe switch). A periodic data refresh keeps the same key, so the
    // trader's pan/zoom is preserved instead of snapping back to full extent.
    if (data.length && fitRef.current !== (fitKey ?? "")) {
      chart.timeScale().fitContent();
      fitRef.current = fitKey ?? "";
    }
  }

  // update on data / overlay change
  useEffect(() => {
    pushData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, priceLines, overlays, fitKey]);

  // The container div must always render so the create-once effect can attach
  // the chart even when the first render has no bars yet (async data). When
  // empty we overlay the message instead of swapping the ref'd node out —
  // otherwise the chart would never initialise once data arrives.
  return (
    <div className="relative w-full" style={{ height }}>
      <div ref={containerRef} className="h-full w-full" />
      {!bars?.length ? (
        <div className="absolute inset-0 flex items-center justify-center rounded-xl border border-dashed border-bg-border/60 text-sm text-text-muted">
          No price bars available.
        </div>
      ) : null}
    </div>
  );
}
