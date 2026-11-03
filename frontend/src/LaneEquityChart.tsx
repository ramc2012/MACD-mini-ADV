import { AreaSeries, ColorType, createChart, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type MouseEventParams, type Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { drawdowns } from "./laneChartMarks";

/** Equity charts for the lanes. One book: equity over time against its capital line, with the fall from the
 * running peak in a pane below. Several books: each book's return on capital on one % axis (never two scales).
 * Colours are the validated categorical slots (dataviz reference palette, dark steps, checked against the app's
 * #090d14 surface: every adjacent pair clears CVD ΔE 8.4 / normal 19.3); a book keeps its slot while shown. */

export type EquityPoint = { at: string; equity: number };
export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const SURFACE = "#090d14";
const GRID = "#18202d";
const TEXT = "#8796aa";
const istDateTime = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const istDay = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" });
const lakh = (value: number) => Math.abs(value) >= 10_000_000 ? `₹${(value / 10_000_000).toFixed(2)} Cr` : `₹${(value / 100_000).toFixed(2)} L`;
const signed = (value: number, digits = 2) => `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;

/** Points in time order, one per second (lightweight-charts needs strictly rising times), thinned to `max`. */
export function chartPoints(points: readonly EquityPoint[], max = 1500): { time: number; equity: number }[] {
  const byTime = new Map<number, number>();
  for (const point of points) {
    const time = Math.floor(Date.parse(point.at) / 1000);
    if (Number.isFinite(time) && Number.isFinite(point.equity)) byTime.set(time, point.equity);
  }
  const rows = [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([time, equity]) => ({ time, equity }));
  if (rows.length <= max) return rows;
  const step = rows.length / max;
  const out = Array.from({ length: max - 1 }, (_, i) => rows[Math.floor(i * step)]);
  out.push(rows[rows.length - 1]);
  return out;
}

function baseChart(element: HTMLDivElement, percent: boolean): IChartApi {
  return createChart(element, {
    layout: { background: { type: ColorType.Solid, color: SURFACE }, textColor: TEXT, fontSize: 11,
      panes: { separatorColor: "#263144", separatorHoverColor: "#3a4b66", enableResize: true } },
    grid: { vertLines: { visible: false }, horzLines: { color: GRID } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false,
      tickMarkFormatter: (value: Time) => istDay.format(new Date((value as number) * 1000)) },
    localization: { locale: "en-IN", timeFormatter: (value: Time) => `${istDateTime.format(new Date((value as number) * 1000))} IST`,
      priceFormatter: (value: number) => (percent ? `${value.toFixed(1)}%` : lakh(value)) },
    crosshair: { vertLine: { color: "#68758a", labelBackgroundColor: "#263144" }, horzLine: { color: "#68758a", labelBackgroundColor: "#263144" } },
    handleScroll: true, handleScale: true,
    width: element.clientWidth, height: element.clientHeight,
  });
}

function useResize(element: React.RefObject<HTMLDivElement>, chart: React.MutableRefObject<IChartApi | undefined>) {
  useEffect(() => {
    if (!element.current) return;
    const observer = new ResizeObserver(() => chart.current?.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [element, chart]);
}

/** One book's equity, its capital line and its drawdown. */
export function EquityChart({ points, capital, height = 220, label = "Equity" }: { points: readonly EquityPoint[]; capital: number; height?: number; label?: string }) {
  const element = useRef<HTMLDivElement>(null);
  const legend = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  const series = useRef<{ equity: ISeriesApi<"Line">; dd: ISeriesApi<"Area"> }>();
  const rows = useMemo(() => chartPoints(points), [points]);
  const falls = useMemo(() => drawdowns(rows.map((row) => row.equity)), [rows]);
  const byTime = useRef(new Map<number, [number, number]>());
  useResize(element, chart);

  const paint = (time?: number) => {
    const el = legend.current;
    if (!el) return;
    const last = rows[rows.length - 1];
    const at = time ?? last?.time;
    const hit = at === undefined ? undefined : byTime.current.get(at);
    if (!hit || at === undefined) { el.textContent = rows.length ? "" : "No equity points yet"; return; }
    const [equity, dd] = hit;
    el.innerHTML = `<b>${istDateTime.format(new Date(at * 1000))} IST</b><span>${label} <i>${lakh(equity)}</i></span>`
      + `<span>Return <i class="${equity >= capital ? "up" : "down"}">${capital ? signed((equity / capital - 1) * 100) : "—"}</i></span>`
      + `<span>Below peak <i class="${dd < 0 ? "down" : ""}">${signed(dd * 100)}</i></span>`;
  };

  useEffect(() => {
    if (!element.current) return;
    const api = baseChart(element.current, false);
    const equity = api.addSeries(LineSeries, { color: SERIES[0], lineWidth: 2, priceLineVisible: false, lastValueVisible: true }, 0);
    const dd = api.addSeries(AreaSeries, { lineColor: "#e66767", topColor: "#e6676700", bottomColor: "#e6676766", lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(1)}%` } }, 1);
    api.panes()[0]?.setStretchFactor(3);
    api.panes()[1]?.setStretchFactor(1);
    chart.current = api; series.current = { equity, dd };
    const onMove = (param: MouseEventParams<Time>) => paint(typeof param.time === "number" ? param.time : undefined);
    api.subscribeCrosshairMove(onMove);
    return () => { api.unsubscribeCrosshairMove(onMove); api.remove(); chart.current = undefined; series.current = undefined; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const s = series.current;
    if (!s || !chart.current) return;
    s.equity.setData(rows.map((row) => ({ time: row.time as Time, value: row.equity })));
    s.dd.setData(rows.map((row, i) => ({ time: row.time as Time, value: falls[i] * 100 })));
    byTime.current = new Map(rows.map((row, i) => [row.time, [row.equity, falls[i]]]));
    s.equity.priceLines().forEach((line) => s.equity.removePriceLine(line));
    if (capital > 0) s.equity.createPriceLine({ price: capital, color: "#68758a", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "Capital" });
    chart.current.timeScale().fitContent();
    paint();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, falls, capital]);

  return <div className="equity-chart" style={{ height }}>
    <div ref={element} className="equity-chart-canvas" aria-label={`${label} over time with drawdown below its peak`} />
    <div ref={legend} className="chart-legend equity-chart-legend" aria-live="off" />
  </div>;
}

export type ReturnBook = { lane: string; label: string; capital: number; points: readonly EquityPoint[] };

/** Several books as return on capital, one % axis. Up to eight shown; each keeps its colour while shown. */
export function ReturnsChart({ books, height = 260, initial }: { books: readonly ReturnBook[]; height?: number; initial?: string[] }) {
  const element = useRef<HTMLDivElement>(null);
  const legend = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  const lines = useRef(new Map<string, ISeriesApi<"Line">>());
  const zeroOn = useRef<ISeriesApi<"Line">>();
  const [slots, setSlots] = useState<Record<string, number>>(() => {
    const first = (initial ?? books.map((b) => b.lane)).slice(0, SERIES.length);
    return Object.fromEntries(first.map((lane, i) => [lane, i]));
  });
  useResize(element, chart);
  const shown = useMemo(() => books.filter((book) => slots[book.lane] !== undefined), [books, slots]);
  const data = useMemo(() => new Map(shown.map((book) => [book.lane,
    chartPoints(book.points, 800).map((row) => ({ time: row.time, value: book.capital ? (row.equity / book.capital - 1) * 100 : 0 }))])), [shown]);

  const toggle = (lane: string) => setSlots((old) => {
    if (old[lane] !== undefined) { const next = { ...old }; delete next[lane]; return next; }
    const used = new Set(Object.values(old));
    const free = SERIES.findIndex((_, i) => !used.has(i));
    return free < 0 ? old : { ...old, [lane]: free };
  });

  const paint = (time?: number) => {
    const el = legend.current;
    if (!el) return;
    if (time === undefined) { el.innerHTML = ""; return; }
    const cells = shown.map((book) => {
      const row = [...(data.get(book.lane) ?? [])].reverse().find((p) => p.time <= time);
      return row ? `<span><s style="background:${SERIES[slots[book.lane]]}"></s>${book.label} <i class="${row.value >= 0 ? "up" : "down"}">${signed(row.value, 1)}</i></span>` : "";
    }).join("");
    el.innerHTML = `<b>${istDateTime.format(new Date(time * 1000))} IST</b>${cells}`;
  };

  useEffect(() => {
    if (!element.current) return;
    const api = baseChart(element.current, true);
    chart.current = api;
    const onMove = (param: MouseEventParams<Time>) => paint(typeof param.time === "number" ? param.time : undefined);
    api.subscribeCrosshairMove(onMove);
    return () => { api.unsubscribeCrosshairMove(onMove); api.remove(); chart.current = undefined; lines.current.clear(); zeroOn.current = undefined; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const api = chart.current;
    if (!api) return;
    for (const [lane, line] of lines.current) {
      if (data.has(lane)) continue;
      if (zeroOn.current === line) zeroOn.current = undefined;
      api.removeSeries(line); lines.current.delete(lane);
    }
    for (const book of shown) {
      let line = lines.current.get(book.lane);
      if (!line) {
        line = api.addSeries(LineSeries, { lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: book.label });
        lines.current.set(book.lane, line);
      }
      line.applyOptions({ color: SERIES[slots[book.lane]] });
      line.setData((data.get(book.lane) ?? []).map((row) => ({ time: row.time as Time, value: row.value })));
      if (!zeroOn.current) {
        line.createPriceLine({ price: 0, color: "#68758a", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
        zeroOn.current = line;
      }
    }
    api.timeScale().fitContent();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  return <div className="returns-chart">
    <div className="returns-legend" role="group" aria-label="Books shown">
      {books.map((book) => {
        const slot = slots[book.lane];
        return <button key={book.lane} type="button" className={slot !== undefined ? "active" : ""} aria-pressed={slot !== undefined}
          disabled={slot === undefined && Object.keys(slots).length >= SERIES.length} title={slot === undefined && Object.keys(slots).length >= SERIES.length ? "Eight books at most: hide one first" : undefined}
          onClick={() => toggle(book.lane)}><s style={{ background: slot !== undefined ? SERIES[slot] : "transparent" }} />{book.label}</button>;
      })}
    </div>
    <div className="equity-chart" style={{ height }}>
      <div ref={element} className="equity-chart-canvas" aria-label="Return on capital per book" />
      <div ref={legend} className="chart-legend equity-chart-legend" aria-live="off" />
    </div>
  </div>;
}
