import { ColorType, createChart, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type MouseEventParams, type Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";

type Instrument = { underlying: string; kind: "index" | "stock" | "commodity"; first_day: string; last_day: string; days: number };
type Point = { t: number; spot: number; strike: number; ce: number; pe: number; straddle: number; ce_oi: number | null; pe_oi: number | null;
  ce_oi_band?: number | null; pe_oi_band?: number | null; band?: [number, number] | null; volume: number; vwap_day: number | null; vwap_week: number | null; vwap_month: number | null };
type Series = { underlying: string; expiry?: string; span: Span; day?: string; days?: string[]; strike_mode: "atm" | "fixed";
  expiries: { expiry: string; first_ts: number; last_ts: number; snapshots: number }[]; strikes?: number[];
  cadence_seconds?: number | null; resolution_seconds?: number; anchors?: Record<"day" | "week" | "month", number | null>;
  series_first_ts?: number; series_last_ts?: number; points: Point[]; note?: string; skipped_partial_snapshots?: number };
// OI view (28 Oct 2026): the ATM strike's own OI jumps whenever the ATM moves to a strike with a different OI (NIFTY's
// round 100-point strikes hold several times the 50-point ones). The default sums the day's opening ATM +-3 strikes, a set
// fixed for the session, so the line moves only when OI does.
type OiMode = "band" | "atm";
const oiOf = (p: Point | undefined, side: "ce" | "pe", mode: OiMode) => !p ? null : mode === "band" ? (p[`${side}_oi_band`] ?? null) : p[`${side}_oi`];
const OI_LABEL: Record<OiMode, string> = { band: "OI · opening ATM ±3 strikes", atm: "OI · ATM strike only" };
type Span = "day" | "week" | "expiry";
type Anchor = "day" | "week" | "month";

const SPANS: [Span, string][] = [["day", "Day"], ["week", "Week"], ["expiry", "Expiry series"]];
const ANCHORS: [Anchor, string][] = [["day", "Day VWAP"], ["week", "Week VWAP"], ["month", "Month VWAP"]];
// Straddle carries identity in blue; the three VWAPs differ by hue and dash so they read without colour too.
const COLORS = { straddle: "#55a9ff", day: "#ffad42", week: "#9b8cff", month: "#dc72d2", ce: "#22c893", pe: "#f15b6c" };
const DASH: Record<Anchor, LineStyle> = { day: LineStyle.Dashed, week: LineStyle.LargeDashed, month: LineStyle.Dotted };
const CLOCK = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const FULL = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const full = (ts?: number | null) => ts ? `${FULL.format(new Date(ts * 1000)).replace(/(\d+)\/(\d+)\/(\d+),?/, "$3-$2-$1")} IST` : "—";
const asDate = (value: Time) => typeof value === "number" ? new Date(value * 1000) : new Date(String(value));
const num = (v: number | null | undefined, d = 2) => v == null ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: v >= 1000 ? 0 : d });
const qty = (v: number | null | undefined) => v == null ? "—" : v >= 1e7 ? `${(v / 1e7).toFixed(2)} Cr` : v >= 1e5 ? `${(v / 1e5).toFixed(2)} L` : v.toLocaleString("en-IN");
// The same view over two chain stores: NSE (Market breadth) and the MCX lane's recorded commodity chains (18 Nov 2026).
export type StraddleSource = "nse" | "mcx";
const SOURCE = {
  nse: { api: "/api/auction/straddle", store: "macd.straddle", fallback: "NIFTY", title: "ATM Straddle & Open Interest", spot: "Spot",
    blurb: "ATM CE + PE premium with anchored VWAPs · ATM CE / PE open interest · stored option chains, no downloads", oiUnit: "contracts × lot" },
  mcx: { api: "/api/mcx/straddle", store: "macd.mcxStraddle", fallback: "CRUDEOIL", title: "MCX ATM Straddle & Open Interest", spot: "Futures",
    blurb: "ATM CE + PE premium with anchored VWAPs · CE / PE open interest · MCX chains the lane records every two minutes in session (since 18 Nov 2026, 23:31 IST)", oiUnit: "open contracts" },
} as const;
const recall = (store: string): Record<string, string> => { try { return JSON.parse(window.localStorage.getItem(store) || "{}"); } catch { return {}; } };
const remember = (store: string, value: Record<string, string>) => { try { window.localStorage.setItem(store, JSON.stringify(value)); } catch { return; } };
const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;

/** ATM straddle with day/week/month-anchored VWAPs and ATM CE/PE open interest, from the stored option chains. */
export function StraddlePage({ source = "nse" }: { source?: StraddleSource }) {
  const src = SOURCE[source];
  const saved = useMemo(() => recall(src.store), [src.store]);
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [underlying, setUnderlying] = useState(saved.underlying || src.fallback);
  const [expiry, setExpiry] = useState("");
  const [span, setSpan] = useState<Span>((saved.span as Span) || "day");
  const [day, setDay] = useState("");
  const [strike, setStrike] = useState("");
  const [oiMode, setOiMode] = useState<OiMode>(saved.oi === "atm" ? "atm" : "band");
  const [shown, setShown] = useState<Record<Anchor, boolean>>({ day: saved.vwap_day !== "0", week: saved.vwap_week !== "0", month: saved.vwap_month !== "0" });
  const [data, setData] = useState<Series>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    requestJson<{ instruments: Instrument[] }>(`${API_URL}${src.api}/instruments`, { headers, signal: controller.signal })
      .then((r) => setInstruments(r.instruments)).catch(() => undefined);
    return () => controller.abort();
  }, [src.api]);
  useEffect(() => remember(src.store, { underlying, span, oi: oiMode, vwap_day: shown.day ? "1" : "0", vwap_week: shown.week ? "1" : "0", vwap_month: shown.month ? "1" : "0" }), [underlying, span, shown, oiMode]);

  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams({ span });
    if (expiry) query.set("expiry", expiry);
    if (day) query.set("day", day);
    if (strike) query.set("strike", strike);
    const load = (quiet = false) => {
      if (!quiet) { setLoading(true); setError(""); }
      requestJson<Series>(`${API_URL}${src.api}/series/${encodeURIComponent(underlying)}?${query}`, { headers, signal: controller.signal })
        .then((r) => { setData(r); setError(""); })
        .catch((reason) => { if (reason.name !== "AbortError") setError(reason instanceof Error ? reason.message : "Straddle series request failed"); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    };
    load();
    // A past day is fixed; the latest one refreshes as new chains are stored.
    const timer = day ? undefined : window.setInterval(() => load(true), 60_000);
    return () => { controller.abort(); if (timer) clearInterval(timer); };
  }, [src.api, underlying, expiry, span, day, strike]);

  const pickUnderlying = (value: string) => { setUnderlying(value); setExpiry(""); setDay(""); setStrike(""); };
  const last = data?.points[data.points.length - 1];
  const indices = instruments.filter((r) => r.kind === "index"), stocks = instruments.filter((r) => r.kind === "stock"), commodities = instruments.filter((r) => r.kind === "commodity");
  const cadence = data?.cadence_seconds ? (data.cadence_seconds < 120 ? "1-minute chains" : `${Math.round(data.cadence_seconds / 60)}-minute chain sweep`) : "";
  const vsDay = last?.vwap_day ? last.straddle - last.vwap_day : null;

  return <section className={`ledger-page straddle-page straddle-${source}`}>
    <div className="page-heading ratio-heading">
      <div title={src.blurb}><h1>{src.title}</h1><p>{src.blurb}</p></div>
      <div className="straddle-controls">
        <select className="rrg-select" value={underlying} onChange={(e) => pickUnderlying(e.target.value)} aria-label="Instrument">
          {!instruments.length && <option value={underlying}>{underlying}</option>}
          {!!indices.length && <optgroup label="Indices">{indices.map((r) => <option key={r.underlying} value={r.underlying}>{r.underlying}</option>)}</optgroup>}
          {!!stocks.length && <optgroup label="F&O stocks">{stocks.map((r) => <option key={r.underlying} value={r.underlying}>{r.underlying}</option>)}</optgroup>}
          {!!commodities.length && <optgroup label="Commodities">{commodities.map((r) => <option key={r.underlying} value={r.underlying}>{r.underlying}</option>)}</optgroup>}
        </select>
        <select className="rrg-select" value={expiry} onChange={(e) => { setExpiry(e.target.value); setDay(""); setStrike(""); }} aria-label="Expiry">
          <option value="">Front expiry{data?.expiry && !expiry ? ` · ${data.expiry}` : ""}</option>
          {(data?.expiries ?? []).map((r) => <option key={r.expiry} value={r.expiry}>{r.expiry} · {r.snapshots} chains</option>)}
        </select>
        <span className="radar-filter" role="group" aria-label="Range">{SPANS.map(([value, label]) => <button key={value} aria-pressed={span === value} className={span === value ? "active" : ""} onClick={() => setSpan(value)}>{label}</button>)}</span>
        <select className="rrg-select" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Session">
          <option value="">Latest session{data?.day && !day ? ` · ${data.day}` : ""}</option>
          {[...(data?.days ?? [])].reverse().map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <select className="rrg-select" value={strike} onChange={(e) => setStrike(e.target.value)} aria-label="Strike">
          <option value="">Rolling ATM</option>
          {(data?.strikes ?? []).map((k) => <option key={k} value={String(k)}>{k} fixed</option>)}
        </select>
        <select className="rrg-select" value={oiMode} onChange={(e) => setOiMode(e.target.value as OiMode)} aria-label="Open interest view">
          <option value="band">OI: opening ATM ±3 strikes (fixed for the day)</option><option value="atm">OI: ATM strike only</option>
        </select>
      </div>
    </div>
    <div className="straddle-tiles">
      <div><span>Straddle {data?.strike_mode === "fixed" ? `· ${strike}` : "· ATM"}</span><b>{num(last?.straddle)}</b><small>{last ? `${last.strike} · CE ${num(last.ce)} + PE ${num(last.pe)}` : "—"}</small></div>
      <div><span>vs day VWAP</span><b className={vsDay == null ? "" : vsDay >= 0 ? "positive" : "negative"}>{vsDay == null ? "—" : `${vsDay >= 0 ? "+" : ""}${num(vsDay)}`}</b><small>{vsDay == null ? "No traded volume yet" : vsDay >= 0 ? "Premium above day VWAP" : "Premium below day VWAP"}</small></div>
      <div><span>CE {OI_LABEL[oiMode]}</span><b>{qty(oiOf(last, "ce", oiMode))}</b><small>{oiMode === "band" && last?.band ? `strikes ${last.band[0]}–${last.band[1]} · ${src.oiUnit}` : src.oiUnit}</small></div>
      <div><span>PE {OI_LABEL[oiMode]}</span><b>{qty(oiOf(last, "pe", oiMode))}</b><small>PE/CE {oiOf(last, "ce", oiMode) ? (Number(oiOf(last, "pe", oiMode)) / Number(oiOf(last, "ce", oiMode))).toFixed(2) : "—"}</small></div>
      <div><span>{src.spot}</span><b>{num(last?.spot)}</b><small>{full(last?.t)}</small></div>
    </div>
    <section className="panel straddle-panel">
      <div className="straddle-legend" aria-label="Chart legend">
        <span><i style={{ background: COLORS.straddle }} />Straddle</span>
        {ANCHORS.map(([key, label]) => <label key={key}><input type="checkbox" checked={shown[key]} onChange={(e) => setShown((s) => ({ ...s, [key]: e.target.checked }))} /><i className={`dash ${key}`} style={{ borderColor: COLORS[key] }} />{label}{data?.anchors?.[key] ? <small> since {full(data.anchors[key])}</small> : null}</label>)}
        <span className="sep" /><span><i style={{ background: COLORS.ce }} />CE {OI_LABEL[oiMode]}</span><span><i style={{ background: COLORS.pe }} />PE {OI_LABEL[oiMode]}</span>
      </div>
      <StraddleChart data={data} shown={shown} oiMode={oiMode} loading={loading} error={error} fitKey={`${underlying}:${data?.expiry}:${span}:${data?.day}:${strike}`} />
    </section>
    <div className="ratio-footer">
      <span>{data?.points.length ? `${data.underlying} ${data.expiry} · ${data.points.length} points at ${Math.round((data.resolution_seconds || 60) / 60)}-minute resolution · ${cadence} · series stored ${full(data.series_first_ts)} → ${full(data.series_last_ts)}` : source === "mcx" ? "No MCX chain recorded for this commodity yet." : "Select an instrument with stored option chains."}</span>
      <span role={error ? "alert" : undefined} className={error ? "negative" : "muted"}>{error || [data?.note, data?.skipped_partial_snapshots ? `${data.skipped_partial_snapshots} partial chain snapshots skipped` : ""].filter(Boolean).join(" · ")}</span>
    </div>
    <details className="panel straddle-table"><summary>Data table · latest first</summary>
      <div className="overflow-auto"><table><thead><tr>{["Time", "Spot", "Strike", "CE", "PE", "Straddle", "Day VWAP", "Week VWAP", "Month VWAP", "CE OI (ATM)", "PE OI (ATM)", "CE OI band", "PE OI band", "Volume"].map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{[...(data?.points ?? [])].reverse().slice(0, 400).map((p) => <tr key={p.t}><td>{full(p.t)}</td><td>{num(p.spot)}</td><td>{p.strike}</td><td>{num(p.ce)}</td><td>{num(p.pe)}</td><td>{num(p.straddle)}</td><td>{num(p.vwap_day)}</td><td>{num(p.vwap_week)}</td><td>{num(p.vwap_month)}</td><td>{qty(p.ce_oi)}</td><td>{qty(p.pe_oi)}</td><td>{qty(p.ce_oi_band ?? null)}</td><td>{qty(p.pe_oi_band ?? null)}</td><td>{qty(p.volume)}</td></tr>)}</tbody></table></div>
    </details>
  </section>;
}

type Handles = { chart: IChartApi; straddle: ISeriesApi<"Line">; vwap: Record<Anchor, ISeriesApi<"Line">>; ce: ISeriesApi<"Line">; pe: ISeriesApi<"Line"> };

function StraddleChart({ data, shown, oiMode, loading, error, fitKey }: { data?: Series; shown: Record<Anchor, boolean>; oiMode: OiMode; loading: boolean; error: string; fitKey: string }) {
  const element = useRef<HTMLDivElement>(null);
  const handles = useRef<Handles>();
  const lastFit = useRef("");
  const byTime = useRef(new Map<number, Point>());
  const [hover, setHover] = useState<Point>();
  useEffect(() => {
    if (!element.current) return;
    const chart = createChart(element.current, {
      width: element.current.clientWidth, height: element.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#090e16" }, textColor: "#8796aa", attributionLogo: false, panes: { separatorColor: "#263144", separatorHoverColor: "#3a4b66", enableResize: true } },
      grid: { vertLines: { color: "#151e2b" }, horzLines: { color: "#151e2b" } },
      rightPriceScale: { borderColor: "#263144", minimumWidth: 84 },
      timeScale: { borderColor: "#263144", timeVisible: true, secondsVisible: false, rightOffset: 3, tickMarkFormatter: (value: Time) => CLOCK.format(asDate(value)) },
      localization: { locale: "en-IN", timeFormatter: (value: Time) => full(typeof value === "number" ? value : asDate(value).getTime() / 1000) },
      crosshair: { vertLine: { color: "#61728a", labelBackgroundColor: "#243247" }, horzLine: { color: "#61728a", labelBackgroundColor: "#243247" } },
      // The chart covers most of the screen: the wheel and a vertical swipe scroll the page (owner, 18 Nov 2026: "not
      // able to scroll down"). Pan by dragging; zoom by pinching or dragging the time / price axis.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
    });
    const line = (color: string, pane: number, style = LineStyle.Solid, width: 1 | 2 = 2, format?: (v: number) => string) =>
      chart.addSeries(LineSeries, { color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: true,
        ...(format ? { priceFormat: { type: "custom", formatter: format, minMove: 1 } } : {}) }, pane);
    const straddle = line(COLORS.straddle, 0);
    const vwap = { day: line(COLORS.day, 0, DASH.day), week: line(COLORS.week, 0, DASH.week), month: line(COLORS.month, 0, DASH.month) };
    const ce = line(COLORS.ce, 1, LineStyle.Solid, 2, qty), pe = line(COLORS.pe, 1, LineStyle.Solid, 2, qty);
    chart.panes()[0]?.setStretchFactor(3);
    chart.panes()[1]?.setStretchFactor(2);
    const move = (param: MouseEventParams) => setHover(typeof param.time === "number" ? byTime.current.get(param.time) : undefined);
    chart.subscribeCrosshairMove(move);
    handles.current = { chart, straddle, vwap, ce, pe };
    const observer = new ResizeObserver(() => chart.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => { observer.disconnect(); chart.unsubscribeCrosshairMove(move); chart.remove(); handles.current = undefined; };
  }, []);
  useEffect(() => {
    const h = handles.current;
    if (!h) return;
    const points = data?.points ?? [];
    byTime.current = new Map(points.map((p) => [p.t, p]));
    const series = (pick: (p: Point) => number | null) => points.flatMap((p) => { const v = pick(p); return v == null ? [] : [{ time: p.t as Time, value: v }]; });
    h.straddle.setData(series((p) => p.straddle));
    h.vwap.day.setData(shown.day ? series((p) => p.vwap_day) : []);
    h.vwap.week.setData(shown.week ? series((p) => p.vwap_week) : []);
    h.vwap.month.setData(shown.month ? series((p) => p.vwap_month) : []);
    h.ce.setData(series((p) => oiOf(p, "ce", oiMode)));
    h.pe.setData(series((p) => oiOf(p, "pe", oiMode)));
    if (points.length && lastFit.current !== fitKey) { lastFit.current = fitKey; h.chart.timeScale().fitContent(); }
  }, [data, shown, oiMode, fitKey]);
  const p = hover;
  return <div className="straddle-chart" ref={element}>
    {p && <div className="straddle-readout" role="status"><b>{full(p.t)}</b> · strike {p.strike} · spot {num(p.spot)}<br />
      straddle <b>{num(p.straddle)}</b> (CE {num(p.ce)} + PE {num(p.pe)}) · VWAP d {num(p.vwap_day)} · w {num(p.vwap_week)} · m {num(p.vwap_month)}<br />
      CE OI {qty(p.ce_oi)} · PE OI {qty(p.pe_oi)} (strike {p.strike}){p.band ? <> · band {p.band[0]}–{p.band[1]}: CE {qty(p.ce_oi_band ?? null)} · PE {qty(p.pe_oi_band ?? null)}</> : null} · traded {qty(p.volume)}</div>}
    {!data?.points.length && <div className={`chart-empty${error ? " error" : ""}`}>{error || (loading ? "Reading stored option chains…" : data?.note || "No stored chain for this selection.")}</div>}
  </div>;
}
