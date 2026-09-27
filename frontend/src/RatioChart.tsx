import { ColorType, createChart, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import type { OptionWatchRow, RatioHistory } from "./types";

const TIMEFRAMES = [300, 900, 1800] as const;
const CLOCK = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
type OptionSide = "CE" | "PE";
const SIDE_COLORS = { CE: ["#22c893", "#55a9ff", "#9b8cff"], PE: ["#f15b6c", "#ffad42", "#dc72d2"] };
// The ratio pane now carries one line and its average; give the average a
// dimmer tone of the same hue so a cross reads without a legend.
const RATIO_COLORS = { CE: "#55a9ff", PE: "#ffad42" };
const EMA_COLORS = { CE: "#9b8cff", PE: "#dc72d2" };
const asDate = (value: Time) => typeof value === "number" ? new Date(value * 1000) : new Date(String(value));
const SPOT_STORE = "macd.ratioSpot";
const TIMEFRAME_STORE = "macd.ratioTimeframe";
const SIDE_STORE = "macd.ratioSide";
// Safari's private mode throws on both halves of localStorage, and a rejected
// preference must not take the page down with it.
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { return; } };

export function RatioPage({ options }: { options: OptionWatchRow[] }) {
  const underlyings = useMemo(() => {
    const rows = new Map<string, string>();
    options.filter((row) => (row.moneyness || "ATM") === "ATM")
      .forEach((row) => rows.set(row.spot_symbol, row.underlying));
    return [...rows].map(([spot, name]) => ({ spot, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [options]);
  const [spot, setSpot] = useState("");
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>(
    () => TIMEFRAMES.find((seconds) => String(seconds) === recall(TIMEFRAME_STORE)) ?? 300);
  const [side, setSide] = useState<OptionSide>(() => recall(SIDE_STORE) === "PE" ? "PE" : "CE");
  const [data, setData] = useState<RatioHistory>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const pickSpot = (value: string) => { setSpot(value); remember(SPOT_STORE, value); };
  const pickTimeframe = (seconds: (typeof TIMEFRAMES)[number]) => { setTimeframe(seconds); remember(TIMEFRAME_STORE, String(seconds)); };
  const pickSide = (value: OptionSide) => { setSide(value); remember(SIDE_STORE, value); };

  // The remembered underlying only survives while it is still quoted: an index
  // that dropped out of the watchlist would otherwise request a ladder the API
  // has no contracts for and leave the page empty.
  useEffect(() => {
    if (!underlyings.length) return;
    setSpot((old) => {
      if (underlyings.some((row) => row.spot === old)) return old;
      const stored = recall(SPOT_STORE);
      if (underlyings.some((row) => row.spot === stored)) return stored;
      return (underlyings.find((row) => row.name === "NIFTY") || underlyings[0]).spot;
    });
  }, [underlyings]);

  useEffect(() => {
    if (!spot) return;
    const controller = new AbortController();
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const load = (quiet = false) => {
      if (!quiet) { setLoading(true); setData(undefined); }
      setError("");
      void fetch(`${API_URL}/api/ratios/${encodeURIComponent(spot)}?timeframe_seconds=${timeframe}`, { headers, signal: controller.signal })
        .then(async (response) => {
          if (!response.ok) throw new Error((await response.json()).detail || "Ratio history request failed");
          return response.json() as Promise<RatioHistory>;
        })
        .then(setData)
        .catch((reason) => { if (reason.name !== "AbortError") setError(reason instanceof Error ? reason.message : "Ratio history request failed"); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    };
    load();
    const timer = window.setInterval(() => load(true), 60_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [spot, timeframe]);

  // Both sides share one history request; switching tabs never downloads again.
  const selectedData = useMemo(() => data && ({
    ...data,
    contracts: data.contracts.filter((row) => row.side === side),
    ratios: data.ratios.filter((row) => row.side === side),
  }), [data, side]);
  const downloadErrors = selectedData?.contracts.filter((row) => data?.download_errors?.[row.symbol]).length || 0;
  const minimumBars = selectedData?.contracts.length ? Math.min(...selectedData.contracts.map((row) => data?.bars[row.symbol] || 0)) : 0;
  const emaPeriod = selectedData?.ratios[0]?.ema_period || 5;
  return <section className="ledger-page ratio-page">
    <div className="page-heading ratio-heading">
      <div><h1>Current-expiry Premium Ratios</h1><p>Premium closes above · ITM/OTM ratio below · analytical context only</p></div>
      <div className="ratio-controls">
        <select className="rrg-select" value={spot} onChange={(event) => pickSpot(event.target.value)} aria-label="Ratio underlying">
          {underlyings.map((row) => <option key={row.spot} value={row.spot}>{row.name}</option>)}
        </select>
        <span className="radar-filter">{TIMEFRAMES.map((seconds) => <button key={seconds} aria-pressed={timeframe === seconds} className={timeframe === seconds ? "active" : ""} onClick={() => pickTimeframe(seconds)}>{seconds / 60}m</button>)}</span>
        <span className={downloadErrors ? "mode alert" : "feed-badge"}>{data ? `${data.expiry} expiry` : "current expiry"}</span>
      </div>
    </div>
    <div className="ratio-tab-bar">
      <div className="ratio-side-tabs" role="tablist" aria-label="Option side">
        {(["CE", "PE"] as const).map((value) => <button key={value} id={`ratio-tab-${value}`} role="tab" aria-selected={side === value} aria-controls="ratio-side-panel" tabIndex={side === value ? 0 : -1} className={side === value ? `active ${value.toLowerCase()}` : ""} onClick={() => pickSide(value)} onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? "CE" : event.key === "End" ? "PE" : side === "CE" ? "PE" : "CE";
          pickSide(next);
          document.getElementById(`ratio-tab-${next}`)?.focus();
        }}>{value} <span>{value === "CE" ? "Calls" : "Puts"}</span></button>)}
      </div>
      <span className="ratio-tab-hint">One side at a time · ITM / ATM / OTM legs</span>
    </div>
    <section id="ratio-side-panel" role="tabpanel" aria-labelledby={`ratio-tab-${side}`} tabIndex={0} className="panel ratio-chart-panel">
      <div className="ratio-chart-label top"><b>{side} premium close</b><span>{selectedData?.contracts.map((row) => `${row.moneyness} ${row.strike}`).join(" · ") || "ITM · ATM · OTM"}</span></div>
      <div className="ratio-chart-label bottom"><b>{side} ITM/OTM</b><span>ratio · {emaPeriod} EMA</span></div>
      <PremiumRatioChart data={selectedData} side={side} loading={loading} error={error} fitKey={`${spot}:${timeframe}:${side}:${data?.expiry || ""}`} />
    </section>
    <div className="ratio-footer">
      <span>{selectedData ? `${selectedData.underlying} ${side} · ${selectedData.contracts.length} premium legs · ITM/OTM ratio with ${emaPeriod} EMA · minimum ${minimumBars} bars/leg` : "Select an underlying to load its current-expiry ladder."}</span>
      <span role={error ? "alert" : undefined} className={error || downloadErrors ? "negative" : "muted"}>{error || (downloadErrors ? `${downloadErrors} ${side} contracts could not be backfilled` : "The ratio uses matching timestamps and positive denominators")}</span>
    </div>
  </section>;
}

function PremiumRatioChart({ data, side, loading, error, fitKey }: { data?: RatioHistory; side: OptionSide; loading: boolean; error: string; fitKey: string }) {
  const element = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; premiums: ISeriesApi<"Line">[]; ratio: ISeriesApi<"Line">; ema: ISeriesApi<"Line"> }>();
  const lastFit = useRef("");
  useEffect(() => {
    if (!element.current) return;
    const chart = createChart(element.current, {
      width: element.current.clientWidth, height: element.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#090e16" }, textColor: "#8796aa", attributionLogo: false, panes: { separatorColor: "#263144", separatorHoverColor: "#3a4b66", enableResize: true } },
      grid: { vertLines: { color: "#151e2b" }, horzLines: { color: "#151e2b" } },
      rightPriceScale: { borderColor: "#263144", minimumWidth: 76 },
      timeScale: { borderColor: "#263144", timeVisible: true, secondsVisible: false, rightOffset: 3, tickMarkFormatter: (value: Time) => CLOCK.format(asDate(value)) },
      localization: { locale: "en-IN", timeFormatter: (value: Time) => `${CLOCK.format(asDate(value))} IST` },
      crosshair: { vertLine: { color: "#61728a", labelBackgroundColor: "#243247" }, horzLine: { color: "#61728a", labelBackgroundColor: "#243247" } },
    });
    const premiums = Array.from({ length: 3 }, (_, index) => chart.addSeries(LineSeries, { color: SIDE_COLORS.CE[index], lineWidth: 2, priceLineVisible: false, lastValueVisible: true }, 0));
    const ratioFormat = { type: "price", precision: 3, minMove: 0.001 } as const;
    const ratio = chart.addSeries(LineSeries, { color: RATIO_COLORS.CE, lineWidth: 2, priceLineVisible: false, lastValueVisible: true, priceFormat: ratioFormat }, 1);
    const ema = chart.addSeries(LineSeries, { color: EMA_COLORS.CE, lineWidth: 2, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: true, priceFormat: ratioFormat }, 1);
    ratio.createPriceLine({ price: 1, color: "#71819877", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "1.0" });
    chart.panes()[0]?.setStretchFactor(3);
    chart.panes()[1]?.setStretchFactor(2);
    api.current = { chart, premiums, ratio, ema };
    const observer = new ResizeObserver(() => chart.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => { observer.disconnect(); chart.remove(); api.current = undefined; };
  }, []);
  useEffect(() => {
    if (!api.current) return;
    // A timeframe change first renders the previous response, then clears it.
    // Reset the fit marker so the arriving response gets its own viewport.
    if (!data) lastFit.current = "";
    api.current.premiums.forEach((series, index) => {
      const row = data?.contracts[index];
      series.applyOptions({ title: row?.moneyness || "", color: SIDE_COLORS[side][index] });
      series.setData((row?.points || []).map((point) => ({ time: point.time as Time, value: point.value })));
    });
    const ratio = data?.ratios[0];
    api.current.ratio.applyOptions({ title: ratio?.label.replace(`${side} `, "") || "ITM/OTM", color: RATIO_COLORS[side] });
    api.current.ratio.setData((ratio?.points || []).map((point) => ({ time: point.time as Time, value: point.value })));
    api.current.ema.applyOptions({ title: ratio ? `${ratio.ema_period} EMA` : "", color: EMA_COLORS[side] });
    api.current.ema.setData((ratio?.ema || []).map((point) => ({ time: point.time as Time, value: point.value })));
    if (data?.contracts.some((row) => row.points.length) && lastFit.current !== fitKey) {
      lastFit.current = fitKey;
      api.current.chart.timeScale().fitContent();
    }
  }, [data, side, fitKey]);
  return <div className="ratio-chart" ref={element}>{(!data || !data.contracts.some((row) => row.points.length)) && <div className={`chart-empty${error ? " error" : ""}`}>{error || (loading ? "Pulling current-expiry minute history…" : "No synchronized premium history is available.")}</div>}</div>;
}
