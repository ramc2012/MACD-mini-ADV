import { ColorType, createChart, LineSeries, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import type { Indicator, OptionWatchRow } from "./types";

import { computeDispersion, dispersionIsCurrent, type DispersionPoint } from "./dispersionMath";
export { computeDispersion } from "./dispersionMath";
export type { DispersionPoint } from "./dispersionMath";

const IST_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
});
const CLOCK = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
});
const chartTime = (time: Time) => typeof time === "number" ? `${CLOCK.format(time * 1000)} IST` : "";
type Scale = "COUNT" | "SHARE";
const SCALE_STORE = "macd.dispersionScale";
// Safari's private mode throws on both halves of localStorage, and a rejected
// preference must not take the page down with it.
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { return; } };
// Opening on the whole stored series squeezes today into a few pixels: the
// desk reads intraday rotation, so the viewport starts on the last three IST
// sessions and the rest stays loaded to pan back into.
const ZOOM_SESSIONS = 3;

/** Where the newest `sessions` IST sessions start, and how many were found. */
function recentSessions(rows: { time: number }[], sessions: number) {
  let found = 0;
  let day = "";
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const current = IST_DAY.format(rows[index].time * 1000);
    if (current === day) continue;
    if (found === sessions) return { from: rows[index + 1].time, found };
    day = current;
    found += 1;
  }
  return { from: rows[0].time, found };
}

export function useDispersionSeries(
  options: OptionWatchRow[], indicators: Record<string, Indicator[]>, timeframe: number,
) {
  const [current, setCurrent] = useState<DispersionPoint>();
  const [history, setHistory] = useState<DispersionPoint[]>([]);
  const [error, setError] = useState("");

  // computeDispersion scans every ATM contract, and `indicators` is replaced
  // once per arriving indicator event — the whole universe lands within a
  // second or two of a candle boundary. Recomputing per event cost ~80ms of
  // main thread each bar for a number that only changes once; sample the
  // latest props on a timer instead.
  const inputs = useRef({ options, indicators });
  inputs.current = { options, indicators };
  useEffect(() => {
    const sample = () => setCurrent((old) => {
      const next = computeDispersion(inputs.current.options, inputs.current.indicators);
      return next && !samePoint(old, next) ? next : old;
    });
    sample();
    const timer = window.setInterval(sample, 2_000);
    return () => clearInterval(timer);
  }, []);

  // The series is durable now: the engine records every completed bar and
  // research/dispersion_reconstruct.py backfills days that predate it. The
  // browser no longer has to be the archive.
  useEffect(() => {
    const controller = new AbortController();
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const load = () => void fetch(`${API_URL}/api/dispersion?timeframe_seconds=${timeframe}&limit=2000`,
      { headers, signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json()).detail || "Dispersion history request failed");
        return response.json() as Promise<{ points: DispersionPoint[] }>;
      })
      .then((data) => { setHistory(data.points || []); setError(""); })
      .catch((reason) => { if (reason.name !== "AbortError") setError(reason instanceof Error ? reason.message : "Dispersion history request failed"); });
    load();
    const timer = window.setInterval(load, 60_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [timeframe]);

  // The stored bar for the newest candle lags by up to one sampling interval,
  // so the live point takes precedence for its own timestamp.
  const series = useMemo(() => {
    if (!current) return history;
    return [...history.filter((row) => row.time !== current.time), current].sort((a, b) => a.time - b.time);
  }, [history, current]);

  return { current, history: series, error };
}

function samePoint(a: DispersionPoint | undefined, b: DispersionPoint) {
  return !!a && a.time === b.time && a.ceAbove === b.ceAbove && a.peAbove === b.peAbove
    && a.ceEligible === b.ceEligible && a.peEligible === b.peEligible && a.total === b.total;
}

export function DispersionPage({ current, history, error, timeframe }: {
  current?: DispersionPoint; history: DispersionPoint[]; error?: string; timeframe: number;
}) {
  const [scale, setScale] = useState<Scale>(() => recall(SCALE_STORE) === "COUNT" ? "COUNT" : "SHARE");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const pickScale = (value: Scale) => { setScale(value); remember(SCALE_STORE, value); };
  const isCurrent = dispersionIsCurrent(current?.time, timeframe, now);
  const eligible = (current?.ceEligible || 0) + (current?.peEligible || 0);
  const coverage = current?.total ? eligible / current.total * 100 : 0;
  const spread = (current?.ceAbove || 0) - (current?.peAbove || 0);
  const lead = spread > 0 ? "Call breadth leads" : spread < 0 ? "Put breadth leads" : "Breadth balanced";
  const days = new Set(history.map((row) => IST_DAY.format(row.time * 1000))).size;
  const rebuilt = history.filter((row) => row.source === "reconstructed").length;
  return <section className="ledger-page dispersion-page">
    <div className="page-heading dispersion-heading">
      <div><h1>CE / PE MACD Dispersion</h1>
        <p>Option contracts whose completed-candle MACD is above zero · {Math.round(timeframe / 60)}m basis · stored server-side</p></div>
      <div className="dispersion-controls">
        <span className="radar-filter">{(["SHARE", "COUNT"] as const).map((value) => <button key={value} aria-pressed={scale === value} className={scale === value ? "active" : ""} onClick={() => pickScale(value)}>{value === "SHARE" ? "% of side" : "Contracts"}</button>)}</span>
        <span className={isCurrent && coverage >= 90 ? "feed-badge" : "mode"}>{current ? `${coverage.toFixed(1)}% ${isCurrent ? "fresh coverage" : "coverage at last close"}` : "Waiting for breadth"}</span>
      </div>
    </div>
    <div className="dispersion-grid">
      <section className="panel dispersion-chart-panel">
        <div className="panel-title"><span>Positive MACD breadth</span>
          <span>{current ? `AS OF ${CLOCK.format(current.time * 1000)} IST` : "WAITING FOR INDICATORS"}</span></div>
        <DispersionChart rows={history} scale={scale} error={error} />
      </section>
      <aside className="dispersion-side">
        <BreadthCard kind="CE" value={current?.ceAbove || 0} eligible={current?.ceEligible || 0} tone="ce" />
        <BreadthCard kind="PE" value={current?.peAbove || 0} eligible={current?.peEligible || 0} tone="pe" />
        <section className="panel dispersion-read">
          <span>{isCurrent ? "Current lead" : "Lead at last close"}</span><b className={spread >= 0 ? "positive" : "negative"}>{current ? lead : "No breadth yet"}</b>
          <strong>{Math.abs(spread)} contracts</strong>
          <p>Positive CE premium momentum is call-side breadth; positive PE premium momentum is put-side breadth. This is market context, not an entry signal.</p>
        </section>
        <section className="panel dispersion-coverage">
          <span>Eligible at latest close</span><b>{eligible} / {current?.total || 0}</b>
          <div><i style={{ width: `${Math.min(100, coverage)}%` }} /></div>
          <p>Stale option MACD values are excluded rather than carried into the current count.</p>
          <p className="dispersion-provenance">{history.length
            ? `${history.length} stored bars over ${days} session${days === 1 ? "" : "s"}${rebuilt ? ` · ${rebuilt} reconstructed` : ""}`
            : "No stored bars yet."} Cohort size changes between sessions, so compare in % of side, not contracts.</p>
        </section>
      </aside>
    </div>
  </section>;
}

function BreadthCard({ kind, value, eligible, tone }: { kind: string; value: number; eligible: number; tone: "ce" | "pe" }) {
  const pct = eligible ? value / eligible * 100 : 0;
  return <section className={`panel breadth-card ${tone}`}>
    <div><span>{kind} MACD &gt; 0</span><small>{value} of {eligible} at latest close</small></div>
    <b>{value}</b><strong>{pct.toFixed(1)}%</strong>
    <div className="breadth-meter"><i style={{ width: `${pct}%` }} /></div>
  </section>;
}

const share = (above: number, eligible: number) => eligible ? above / eligible * 100 : 0;

function DispersionChart({ rows, scale, error }: { rows: DispersionPoint[]; scale: Scale; error?: string }) {
  const element = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; ce: ISeriesApi<"Line">; pe: ISeriesApi<"Line"> }>();
  const zoomed = useRef("");
  useEffect(() => {
    if (!element.current) return;
    const chart = createChart(element.current, {
      width: element.current.clientWidth, height: element.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#090e16" }, textColor: "#8796aa", attributionLogo: false },
      localization: { timeFormatter: chartTime },
      grid: { vertLines: { color: "#151e2b" }, horzLines: { color: "#151e2b" } },
      crosshair: { vertLine: { color: "#61728a", labelBackgroundColor: "#243247" }, horzLine: { color: "#61728a", labelBackgroundColor: "#243247" } },
      rightPriceScale: { borderColor: "#263144", minimumWidth: 56 },
      timeScale: { borderColor: "#263144", timeVisible: true, secondsVisible: false, rightOffset: 4, tickMarkFormatter: chartTime },
    });
    const ce = chart.addSeries(LineSeries, { color: "#22c893", lineWidth: 2, title: "CE > 0", crosshairMarkerRadius: 4 });
    const pe = chart.addSeries(LineSeries, { color: "#f15b6c", lineWidth: 2, title: "PE > 0", crosshairMarkerRadius: 4 });
    api.current = { chart, ce, pe };
    const observer = new ResizeObserver(() => chart.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => { observer.disconnect(); chart.remove(); api.current = undefined; };
  }, []);
  useEffect(() => {
    if (!api.current) return;
    // Percent and contract counts share one price scale, so the format has to
    // follow the toggle or a 62% breadth reads as "62 contracts".
    const format = scale === "SHARE"
      ? { type: "custom" as const, minMove: 0.1, formatter: (value: number) => `${value.toFixed(0)}%` }
      : { type: "price" as const, precision: 0, minMove: 1 };
    api.current.ce.applyOptions({ priceFormat: format });
    api.current.pe.applyOptions({ priceFormat: format });
    api.current.ce.setData(rows.map((row) => ({
      time: row.time as Time, value: scale === "SHARE" ? share(row.ceAbove, row.ceEligible) : row.ceAbove })));
    api.current.pe.setData(rows.map((row) => ({
      time: row.time as Time, value: scale === "SHARE" ? share(row.peAbove, row.peEligible) : row.peAbove })));
    if (rows.length) {
      // Same guard as the old fitContent(): the viewport is set once and the
      // minute poll never yanks the desk out of whatever it panned to. The
      // session count is part of the key because the live point renders before
      // the stored history answers — zooming only on that first lone bar would
      // leave the page stuck at one candle's width once the series landed.
      const { from, found } = recentSessions(rows, ZOOM_SESSIONS);
      const key = `${scale}:${found}`;
      const to = rows[rows.length - 1].time;
      if (zoomed.current !== key) {
        if (from < to) api.current.chart.timeScale().setVisibleRange({ from: from as Time, to: to as Time });
        else api.current.chart.timeScale().fitContent();
        zoomed.current = key;
      }
    }
  }, [rows, scale]);
  return <div className="dispersion-chart" ref={element}>
    {!rows.length && <div className={`chart-empty${error ? " error" : ""}`}>
      {error || "No stored breadth yet — the series begins with the next completed cohort."}</div>}
  </div>;
}
