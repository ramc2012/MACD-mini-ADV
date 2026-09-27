import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries, LineSeries, LineStyle, TickMarkType,
  type IChartApi, type IPanePrimitive, type IPanePrimitivePaneView, type IPriceLine, type IPrimitivePaneRenderer, type ISeriesApi,
  type ISeriesMarkersPluginApi, type MouseEventParams, type PaneAttachedParameter, type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { initialBalance, referenceLevels, sessionKey, sessionStarts, type ReferenceKind } from "./chartMath";
import type { AuctionContext, Candle, ChartLayers, ChartMarker, Indicator, PaneCollapse } from "./types";

export const DEFAULT_LAYERS: ChartLayers = { volume: true, legend: true, priorDay: true, week: false, nakedPocs: false, ib: true, sessions: true };
export const DEFAULT_PANES: PaneCollapse = { rsi: false, roc: false };

type Props = {
  candles: Candle[]; current?: Candle; indicators: Indicator[]; markers?: ChartMarker[]; error?: string; fitKey: string; rsiGate?: number; rocGate?: number;
  timeframe?: number; layers?: ChartLayers; references?: AuctionContext; paneCollapse?: PaneCollapse;
};
const istDateTime = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const istDay = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" });
const istMonthYear = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", month: "short", year: "numeric" });
const istClock = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });
const asDate = (value: Time) => {
  if (typeof value === "number") return new Date(value * 1000);
  if (typeof value === "string") return new Date(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return new Date(`${value.year}-${pad(value.month)}-${pad(value.day)}T00:00:00+05:30`);
};
const fmt = (value?: number | null, digits = 2) => (value === undefined || value === null || Number.isNaN(value) ? "—" : value.toFixed(digits));
const UP = "#22c893";
const DOWN = "#f15b6c";
const volumeColor = (bar: Candle) => (bar.close >= bar.open ? "#22c89366" : "#f15b6c66");
// Pane heights are proportional to stretch (minimum 2px), so a factor near
// zero collapses a pane to a sliver whose separator stays draggable.
const STRETCH = { price: 5.3, macd: 2.0, rsi: 1.5, roc: 1.2, collapsed: 0.0001 };

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];
/** Dashed vertical line at the first bar of each session, drawn per pane so
 * all four panes mark the same x. A time-scale tick alone is not enough: the
 * axis may not place a tick on the opening bar at every zoom level. */
class SessionSeparators implements IPanePrimitive<Time> {
  private chart?: PaneAttachedParameter<Time>["chart"];
  private requestUpdate?: () => void;
  private times: number[] = [];
  private readonly views: IPanePrimitivePaneView[] = [{
    zOrder: () => "bottom",
    renderer: () => ({ draw: (target: Target) => this.draw(target) }),
  }];
  attached({ chart, requestUpdate }: PaneAttachedParameter<Time>) { this.chart = chart; this.requestUpdate = requestUpdate; }
  detached() { this.chart = undefined; this.requestUpdate = undefined; }
  paneViews() { return this.views; }
  setTimes(times: number[]) { this.times = times; this.requestUpdate?.(); }
  private draw(target: Target) {
    const chart = this.chart;
    if (!chart || !this.times.length) return;
    const scale = chart.timeScale();
    const half = scale.options().barSpacing / 2;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      context.save();
      context.strokeStyle = "#34435c";
      context.lineWidth = 1;
      context.setLineDash([3, 4]);
      for (const time of this.times) {
        const x = scale.timeToCoordinate(time as Time);
        if (x === null) continue;
        const px = Math.round(x - half) + 0.5;
        context.beginPath(); context.moveTo(px, 0); context.lineTo(px, mediaSize.height); context.stroke();
      }
      context.restore();
    });
  }
}

// One style per family so the family reads from the line alone; axis labels
// only on POC and IB, or six labels pile onto the KAMA/VWAP last-value tags.
const LEVEL_STYLE: Record<ReferenceKind, { color: string; lineWidth: 1 | 2; lineStyle: LineStyle; axisLabelVisible: boolean }> = {
  pd_poc: { color: "#ffad42", lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true },
  pd_va: { color: "#ffad42aa", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false },
  wk_poc: { color: "#4da3ff", lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true },
  wk_va: { color: "#4da3ffaa", lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: false },
  naked: { color: "#9b8cff", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false },
  ib: { color: "#c7d3e2", lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: true },
  ib_forming: { color: "#c7d3e299", lineWidth: 1, lineStyle: LineStyle.SparseDotted, axisLabelVisible: true },
};

type ChartHandles = {
  chart: IChartApi; candle: ISeriesApi<"Candlestick">; candleMarkers: ISeriesMarkersPluginApi<Time>; volume: ISeriesApi<"Histogram">;
  bbUpper: ISeriesApi<"Line">; bbMiddle: ISeriesApi<"Line">; bbLower: ISeriesApi<"Line">; kama: ISeriesApi<"Line">; vwap: ISeriesApi<"Line">;
  line: ISeriesApi<"Line">; signal: ISeriesApi<"Line">; hist: ISeriesApi<"Histogram">;
  kamaRsi: ISeriesApi<"Line">; kamaRoc: ISeriesApi<"Line">; separators: SessionSeparators[];
};

export function TradingChart({ candles, current, indicators, markers = [], error, fitKey, rsiGate = 65, rocGate = 0.5, timeframe, layers = DEFAULT_LAYERS, references, paneCollapse = DEFAULT_PANES }: Props) {
  const element = useRef<HTMLDivElement>(null);
  const legend = useRef<HTMLDivElement>(null);
  const lastFit = useRef("");
  const rows = useRef<Candle[]>([]);
  const indexByTime = useRef(new Map<number, number>());
  const indicatorByTime = useRef(new Map<number, Indicator>());
  const vwapByTime = useRef(new Map<number, number>());
  // VWAP state after the last closed row, so the forming bar's VWAP can be
  // recomputed per tick without walking the session again.
  const vwapTail = useRef({ session: Number.NaN, tradedValue: 0, tradedVolume: 0 });
  const priceLines = useRef(new Map<string, { price: number; kind: ReferenceKind; line: IPriceLine }>());
  const charts = useRef<ChartHandles>();
  // The bar under the crosshair, or undefined when the pointer is off the
  // data.  Held in a ref rather than passed per call because the legend is
  // repainted from the tick path too, and that path has no pointer position.
  const hovered = useRef<number>();
  const painted = useRef("");

  // Painted straight into the DOM: crosshair moves arrive per mouse frame and
  // must not go through React state.  Every interpolated value is a number or
  // a formatted date from our own payload, never text from outside.
  const paintLegend = () => {
    const el = legend.current;
    if (!el) return;
    const list = rows.current;
    if (!list.length) { el.innerHTML = ""; painted.current = ""; return; }
    // A stationary pointer emits no mouse events, but series.update() fires
    // crosshairMoved on every tick.  Reading the hovered bar from the ref
    // instead of the tail keeps the hovered row on screen while the tape
    // moves -- unconditionally painting the last bar made the legend useless
    // for any bar but the forming one during market hours.  A timestamp that
    // is no longer in the data (the symbol changed under the pointer) falls
    // back to the tail rather than freezing on the previous symbol's row.
    const at = hovered.current;
    const index = at === undefined ? list.length - 1 : (indexByTime.current.get(at) ?? list.length - 1);
    const bar = list[index];
    const prev = list[index - 1];
    const change = prev ? bar.close - prev.close : 0;
    const pct = prev && prev.close ? (change / prev.close) * 100 : 0;
    const tone = bar.close >= bar.open ? "up" : "down";
    const changeTone = change >= 0 ? "up" : "down";
    const ind = indicatorByTime.current.get(bar.timestamp);
    const vwap = vwapByTime.current.get(bar.timestamp);
    const cell = (label: string, value: string, cls = "") => `<span>${label} <i class="${cls}">${value}</i></span>`;
    const html = `<b>${istDateTime.format(new Date(bar.timestamp * 1000))}${bar.closed ? "" : " · forming"}</b>`
      + cell("O", fmt(bar.open), tone) + cell("H", fmt(bar.high), tone) + cell("L", fmt(bar.low), tone) + cell("C", fmt(bar.close), tone)
      + cell("Δ", `${change >= 0 ? "+" : ""}${fmt(change)} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)`, changeTone)
      + cell("Vol", bar.volume ? compact.format(bar.volume) : "—")
      + cell("VWAP", fmt(vwap))
      + cell("MACD", `${fmt(ind?.macd, 3)} / ${fmt(ind?.signal, 3)} / ${fmt(ind?.histogram, 3)}`, ind ? (ind.histogram >= 0 ? "up" : "down") : "")
      + cell("KAMA", fmt(ind?.kama)) + cell("RSI", fmt(ind?.kama_rsi, 1)) + cell("ROC", `${fmt(ind?.kama_roc)}%`);
    // Each series.update() re-fires crosshairMoved, so one tick asks for four
    // identical repaints of a hovered row; comparing before writing keeps it
    // to the one write that actually changes something.
    if (html === painted.current) return;
    painted.current = html;
    el.innerHTML = html;
  };

  // Tick path: only the forming bar changes, so three series get update()
  // instead of thirteen getting setData().
  const applyCurrent = (api: ChartHandles, bar: Candle) => {
    const list = rows.current;
    const last = list[list.length - 1];
    // update() throws for a time older than the last point; a stale event for
    // an earlier bar is dropped here and drawn by the next full rebuild.
    if (last && bar.timestamp < last.timestamp) return;
    if (last && last.timestamp === bar.timestamp) list[list.length - 1] = bar;
    else { list.push(bar); indexByTime.current.set(bar.timestamp, list.length - 1); }
    api.candle.update({ time: bar.timestamp as Time, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
    api.volume.update({ time: bar.timestamp as Time, value: Math.max(0, bar.volume || 0), color: volumeColor(bar) });
    const tail = vwapTail.current;
    const volume = Math.max(0, bar.volume || 0);
    const sameSession = sessionKey(bar.timestamp) === tail.session;
    const tradedValue = (sameSession ? tail.tradedValue : 0) + ((bar.high + bar.low + bar.close) / 3) * volume;
    const tradedVolume = (sameSession ? tail.tradedVolume : 0) + volume;
    if (tradedVolume > 0) {
      vwapByTime.current.set(bar.timestamp, tradedValue / tradedVolume);
      api.vwap.update({ time: bar.timestamp as Time, value: tradedValue / tradedVolume });
    }
  };

  useEffect(() => {
    if (!element.current) return;
    // One chart, four panes (price / MACD / KAMA-RSI / KAMA-ROC). A single
    // time scale and a single crosshair make time alignment structural: the
    // previous build ran four chart instances mirrored by visible-range and
    // crosshair subscriptions, and panes with different data extents could
    // still place the same timestamp at different x pixels.
    const chart = createChart(element.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#090d14" }, textColor: "#8796aa",
        panes: { separatorColor: "#263144", separatorHoverColor: "#3a4b66", enableResize: true },
      },
      grid: { vertLines: { color: "#18202d" }, horzLines: { color: "#18202d" } },
      rightPriceScale: { borderColor: "#263144", minimumWidth: 76 },
      timeScale: {
        borderColor: "#263144", timeVisible: true, secondsVisible: false, rightOffset: 6,
        // Day-change ticks carry the date and intraday ticks the clock, so the
        // axis itself marks sessions instead of repeating "dd/mm HH:mm".  A
        // year tick carries the year: TickMarkType.Year sorts below DayOfMonth,
        // so lumping it in with the days printed "01 Jan" for every 1 January
        // in the ALL period with nothing to tell the years apart.
        tickMarkFormatter: (value: Time, type: TickMarkType) => (
          type === TickMarkType.Year ? istMonthYear.format(asDate(value))
            : type <= TickMarkType.DayOfMonth ? istDay.format(asDate(value))
              : istClock.format(asDate(value))
        ),
      },
      localization: { locale: "en-IN", timeFormatter: (value: Time) => `${istDateTime.format(asDate(value))} IST` },
      crosshair: { vertLine: { color: "#68758a", labelBackgroundColor: "#263144" }, horzLine: { color: "#68758a", labelBackgroundColor: "#263144" } },
      width: element.current.clientWidth,
      height: element.current.clientHeight,
    });
    const candle = chart.addSeries(CandlestickSeries, { upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN }, 0);
    const candleMarkers = createSeriesMarkers(candle, []);
    // Volume sits on its own overlay scale so it never bends the price axis.
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: "volume", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false }, 0);
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    const bbUpper = chart.addSeries(LineSeries, { color: "#7083a455", lineWidth: 1, title: "BB Upper", lastValueVisible: false, priceLineVisible: false }, 0);
    const bbMiddle = chart.addSeries(LineSeries, { color: "#7083a499", lineWidth: 1, title: "BB 20", lastValueVisible: false, priceLineVisible: false }, 0);
    const bbLower = chart.addSeries(LineSeries, { color: "#7083a455", lineWidth: 1, title: "BB Lower", lastValueVisible: false, priceLineVisible: false }, 0);
    const kama = chart.addSeries(LineSeries, { color: "#c084fc", lineWidth: 2, title: "KAMA", lastValueVisible: true, priceLineVisible: false }, 0);
    const vwap = chart.addSeries(LineSeries, { color: "#f5b84b", lineWidth: 2, title: "VWAP", lastValueVisible: true, priceLineVisible: false }, 0);
    // All three MACD series must share one price scale.  An empty scale id
    // creates an overlay scale, which made histogram zero render at a
    // different height from MACD/signal zero even though the values agreed.
    const hist = chart.addSeries(HistogramSeries, { priceFormat: { type: "price" }, priceScaleId: "right", lastValueVisible: false, priceLineVisible: false }, 1);
    const line = chart.addSeries(LineSeries, { color: "#4da3ff", lineWidth: 2, title: "MACD", priceScaleId: "right" }, 1);
    const signal = chart.addSeries(LineSeries, { color: "#ffad42", lineWidth: 1, title: "Signal", priceScaleId: "right" }, 1);
    hist.createPriceLine({ price: 0, color: "#7083a477", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
    const kamaRsi = chart.addSeries(LineSeries, { color: "#f5b84b", lineWidth: 2, title: "KAMA RSI" }, 2);
    kamaRsi.createPriceLine({ price: rsiGate, color: "#f5b84b77", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "RSI gate" });
    kamaRsi.createPriceLine({ price: 50, color: "#7083a455", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
    const kamaRoc = chart.addSeries(LineSeries, { color: UP, lineWidth: 2, title: "KAMA ROC %" }, 3);
    kamaRoc.createPriceLine({ price: rocGate, color: "#7083a477", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "ROC gate" });
    kamaRsi.priceScale().applyOptions({ scaleMargins: { top: 0.12, bottom: 0.12 } });
    const separators = chart.panes().map((pane) => { const primitive = new SessionSeparators(); pane.attachPrimitive(primitive); return primitive; });
    charts.current = { chart, candle, candleMarkers, volume, bbUpper, bbMiddle, bbLower, kama, vwap, line, signal, hist, kamaRsi, kamaRoc, separators };
    const onCrosshair = (param: MouseEventParams<Time>) => {
      hovered.current = typeof param.time === "number" ? param.time : undefined;
      paintLegend();
    };
    chart.subscribeCrosshairMove(onCrosshair);
    const observer = new ResizeObserver(() => {
      chart.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight });
    });
    observer.observe(element.current);
    return () => { chart.unsubscribeCrosshairMove(onCrosshair); observer.disconnect(); chart.remove(); charts.current = undefined; priceLines.current.clear(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Full rebuild on history, indicators and markers — not on `current`, which
  // changes at tick rate and goes through applyCurrent instead.
  useEffect(() => {
    const api = charts.current;
    if (!api) return;
    const closedRows = [...new Map(candles.map((row) => [row.timestamp, row])).values()].sort((a, b) => a.timestamp - b.timestamp);
    const lastClosed = closedRows.length ? closedRows[closedRows.length - 1].timestamp : 0;
    const forming = current && current.timestamp >= lastClosed ? current : undefined;
    // History may already carry the forming bar (today's bars come from the
    // engine's own history); the stream copy wins and the VWAP counts it once.
    const base = forming ? closedRows.filter((row) => row.timestamp < forming.timestamp) : closedRows;
    const all = forming ? [...base, forming] : base;
    const indicatorsByTime = [...new Map(indicators.map((row) => [row.timestamp, row])).values()].sort((a, b) => a.timestamp - b.timestamp);
    api.candle.setData(base.map((c) => ({ time: c.timestamp as Time, open: c.open, high: c.high, low: c.low, close: c.close })));
    api.volume.setData(base.map((c) => ({ time: c.timestamp as Time, value: Math.max(0, c.volume || 0), color: volumeColor(c) })));
    const candleTimes = all.map((c) => c.timestamp);
    api.candleMarkers.setMarkers(markers.flatMap((marker) => {
      const time = candleTimes.filter((candidate) => candidate <= marker.timestamp).pop();
      return time === undefined ? [] : [{ time: time as Time, position: marker.tone === "stop" ? "aboveBar" as const : "belowBar" as const, color: marker.tone === "stop" ? DOWN : marker.tone === "signal" ? "#f5b84b" : "#4da3ff", shape: marker.tone === "stop" ? "arrowDown" as const : "arrowUp" as const, text: `${marker.label} ₹${marker.price.toFixed(2)}` }];
    }));
    api.bbUpper.setData(indicatorsByTime.filter((p) => p.bb_upper != null).map((p) => ({ time: p.timestamp as Time, value: p.bb_upper as number })));
    api.bbMiddle.setData(indicatorsByTime.filter((p) => p.bb_middle != null).map((p) => ({ time: p.timestamp as Time, value: p.bb_middle as number })));
    api.bbLower.setData(indicatorsByTime.filter((p) => p.bb_lower != null).map((p) => ({ time: p.timestamp as Time, value: p.bb_lower as number })));
    api.kama.setData(indicatorsByTime.filter((p) => p.kama != null).map((p) => ({ time: p.timestamp as Time, value: p.kama as number })));
    // VWAP resets at each IST session.  A zero-volume candle contributes no
    // invented volume, so the line remains a true traded-volume average.
    let session = Number.NaN;
    let tradedValue = 0;
    let tradedVolume = 0;
    vwapByTime.current.clear();
    const vwapRows = base.flatMap((candle) => {
      const candleSession = sessionKey(candle.timestamp);
      if (candleSession !== session) { session = candleSession; tradedValue = 0; tradedVolume = 0; }
      const volume = Math.max(0, candle.volume || 0);
      tradedValue += ((candle.high + candle.low + candle.close) / 3) * volume;
      tradedVolume += volume;
      if (tradedVolume <= 0) return [];
      vwapByTime.current.set(candle.timestamp, tradedValue / tradedVolume);
      return [{ time: candle.timestamp as Time, value: tradedValue / tradedVolume }];
    });
    vwapTail.current = { session, tradedValue, tradedVolume };
    api.vwap.setData(vwapRows);
    api.line.setData(indicatorsByTime.map((p) => ({ time: p.timestamp as Time, value: p.macd })));
    api.signal.setData(indicatorsByTime.map((p) => ({ time: p.timestamp as Time, value: p.signal })));
    api.hist.setData(indicatorsByTime.map((p) => ({ time: p.timestamp as Time, value: p.histogram, color: p.histogram >= 0 ? "#22c89399" : "#f15b6c99" })));
    api.kamaRsi.setData(indicatorsByTime.filter((p) => p.kama_rsi != null).map((p) => ({ time: p.timestamp as Time, value: p.kama_rsi as number })));
    api.kamaRoc.setData(indicatorsByTime.filter((p) => p.kama_roc != null).map((p) => ({ time: p.timestamp as Time, value: p.kama_roc as number })));
    rows.current = base;
    indexByTime.current = new Map(base.map((c, i) => [c.timestamp, i]));
    indicatorByTime.current = new Map(indicatorsByTime.map((p) => [p.timestamp, p]));
    if (forming) applyCurrent(api, forming);
    api.separators.forEach((primitive) => primitive.setTimes(layers.sessions ? sessionStarts(all) : []));
    // Index symbols report volume 0; an empty histogram would only steal the
    // bottom of the price pane, so the margin goes with the series.
    const volumeVisible = layers.volume && all.some((c) => (c.volume || 0) > 0);
    api.volume.applyOptions({ visible: volumeVisible });
    api.candle.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: volumeVisible ? 0.22 : 0.06 } });
    paintLegend();
    // Fit only when the symbol/timeframe/period/data-stage changes — never on
    // live ticks, so user zoom and pan survive streaming updates.
    if (all.length && lastFit.current !== fitKey) {
      lastFit.current = fitKey;
      api.chart.timeScale().fitContent();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, indicators, markers, fitKey, layers.sessions, layers.volume]);

  useEffect(() => {
    const api = charts.current;
    if (!api || !current) return;
    applyCurrent(api, current);
    paintLegend();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  // Reference lines are recreated only when a level's price changes: the IB
  // moves during the first hour, nothing else moves intraday, and churning
  // price lines per tick would flicker their axis labels.
  useEffect(() => {
    const api = charts.current;
    if (!api) return;
    const all = rows.current;
    const step = timeframe || (all.length > 1 ? Math.min(...all.slice(1).map((c, i) => c.timestamp - all[i].timestamp)) : 1800);
    const levels = referenceLevels(references, initialBalance(all, step), layers);
    const wanted = new Map(levels.map((level) => [level.key, level]));
    for (const [key, entry] of priceLines.current) {
      const level = wanted.get(key);
      // The kind matters as much as the price: an IB whose extreme never moved
      // still changes from forming to complete at 10:15.
      if (!level || level.price !== entry.price || level.kind !== entry.kind) {
        api.candle.removePriceLine(entry.line);
        priceLines.current.delete(key);
      }
    }
    for (const level of levels) {
      if (priceLines.current.has(level.key)) continue;
      const line = api.candle.createPriceLine({ price: level.price, title: level.title, ...LEVEL_STYLE[level.kind] });
      priceLines.current.set(level.key, { price: level.price, kind: level.kind, line });
    }
  }, [references, layers, timeframe, candles, current?.timestamp, current?.high, current?.low]);

  useEffect(() => {
    const api = charts.current;
    if (!api) return;
    const stretch = [STRETCH.price, STRETCH.macd, paneCollapse.rsi ? STRETCH.collapsed : STRETCH.rsi, paneCollapse.roc ? STRETCH.collapsed : STRETCH.roc];
    api.chart.panes().forEach((pane, index) => pane.setStretchFactor(stretch[index] ?? 1));
    // Hidden as well as collapsed, so nothing paints into the sliver.
    api.kamaRsi.applyOptions({ visible: !paneCollapse.rsi });
    api.kamaRoc.applyOptions({ visible: !paneCollapse.roc });
  }, [paneCollapse.rsi, paneCollapse.roc]);

  const empty = candles.length === 0 && !current;
  return <div className="chart-stack">
    <div ref={element} className="chart-panes" aria-label="Price, volume, MACD, KAMA RSI and KAMA ROC chart" />
    <div ref={legend} className="chart-legend" hidden={!layers.legend} aria-live="off" />
    {empty && <div className={`chart-empty${error ? " error" : ""}`}>{error || "Loading broker candle history…"}</div>}
  </div>;
}
