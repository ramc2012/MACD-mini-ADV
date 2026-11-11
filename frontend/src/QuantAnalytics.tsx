import { CandlestickSeries, ColorType, createChart, HistogramSeries, LineSeries, type IChartApi, type Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import { requestJson } from "./requestJson";
import { LivePipeline } from "./LivePipeline";
import {
  atrPct, barsPerYear, barsSinceSignChange, betaCorrelation, ema, percentileRank, rangePosition,
  rollingVolatility, sessionReturn, volatilityRegime, type Bar,
} from "./quantMath";

// Quant analytics: one instrument read against its own history, the whole
// live cross-section beside it, and the whale-edge study that says how much
// weight a whale read deserves. The pipeline diagnostics that used to fill
// the page are one collapsible strip at the bottom.

type Indicator = { timestamp: number; macd: number | null; signal: number | null; histogram: number | null };
type ChartPayload = { symbol: string; timeframe_seconds: number; candles: Bar[]; indicators: Indicator[]; macd_periods?: { fast: number; slow: number; signal: number } };
type Rust = { realized_volatility_pct: number | null; trend: string; candle_count: number };
type LiveRow = { symbol: string; ltp: number | null; macd: number | null; signal: number | null; histogram: number | null;
  realized_volatility_pct: number | null; warm: boolean; ticks: number; bars_closed: number; trend: string };
type WhaleName = { symbol: string; kind: string; probability?: number | null; side: string; patterns: string[]; score: number; events: number };
type StudyBin = { n: number; hit_rate?: number; lift?: number; z?: number | null };
type Study = { latest: null | { study_id: number; since: string; until: string; events: number; symbols: number; sessions: number; verdict: string;
  test?: StudyBin & { from: string; to: string }; control_test?: StudyBin; test_quintiles?: StudyBin[];
  features_test?: Record<string, Record<string, StudyBin>> } };

const TIMEFRAMES = [60, 300, 900, 1800, 3600];
const IST_OFFSET = 19_800;
const headers = () => (API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined);
const num = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const fmt = (v: number | null | undefined, digits = 2) => v == null || !Number.isFinite(v) ? "—" : v.toFixed(digits);
const signed = (v: number | null | undefined, digits = 2, suffix = "%") => v == null || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}${suffix}`;
const tone = (v: number | null | undefined) => v == null ? "" : v > 0 ? "positive" : v < 0 ? "negative" : "";
const kindOf = (symbol: string) => symbol.endsWith("-INDEX") ? "index" : symbol.endsWith("-EQ") ? "cash" : symbol.endsWith("FUT") ? "future" : "option";
const short = (symbol: string) => symbol.replace(/^NSE:|^BSE:/, "");

function useJson<T>(url: string | null, deps: unknown[], every = 0) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  useEffect(() => {
    if (!url) return;
    let stopped = false;
    const controller = new AbortController();
    const load = () => void requestJson<T>(url, { headers: headers(), signal: controller.signal })
      .then((value) => { if (!stopped) { setData(value); setError(""); } })
      .catch((reason) => { if (!stopped && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : "unavailable"); });
    load();
    const timer = every ? window.setInterval(load, every) : 0;
    return () => { stopped = true; controller.abort(); if (timer) window.clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error };
}

export function QuantAnalyticsPage({ symbols, selected, timeframe, onSelect }: {
  symbols: string[]; selected: string; timeframe?: number; onSelect: (symbol: string) => void;
}) {
  const symbol = selected || symbols[0] || "";
  const [frame, setFrame] = useState(timeframe && TIMEFRAMES.includes(timeframe) ? timeframe : 1800);
  const chart = useJson<ChartPayload>(symbol ? `${API_URL}/api/chart/${encodeURIComponent(symbol)}?timeframe_seconds=${frame}` : null, [symbol, frame], 60_000);
  const bench = useJson<ChartPayload>(`${API_URL}/api/chart/${encodeURIComponent("NSE:NIFTY50-INDEX")}?timeframe_seconds=${frame}`, [frame], 120_000);
  const rust = useJson<Rust>(symbol ? `${API_URL}/parallel/analytics?symbol=${encodeURIComponent(symbol)}&timeframe_seconds=${frame}` : null, [symbol, frame], 60_000);
  const whales = useJson<{ names: WhaleName[] }>(`${API_URL}/api/auction/whale/names?limit=500`, [], 60_000);
  const whaleBySymbol = useMemo(() => new Map((whales.data?.names || []).map((row) => [row.symbol, row])), [whales.data]);

  return <section className="ledger-page quant-page quant-v2">
    <div className="mp-head">
      <h1 title="Rust and engine analytics of one instrument against its own history, the live cross-section, and the whale-edge study">Quant analytics</h1>
      <div className="radar-filter" aria-label="Timeframe">{TIMEFRAMES.map((tf) => <button key={tf} className={frame === tf ? "active" : ""}
        onClick={() => setFrame(tf)}>{tf < 3600 ? `${tf / 60}m` : "1h"}</button>)}</div>
      <div className="rrg-controls">
        <input className="quant-search" list="quant-symbols" placeholder="Instrument…" defaultValue={symbol} key={symbol}
          onKeyDown={(event) => { if (event.key === "Enter") onSelect((event.target as HTMLInputElement).value.trim()); }}
          onChange={(event) => { if (symbols.includes(event.target.value)) onSelect(event.target.value); }} />
        <datalist id="quant-symbols">{symbols.map((item) => <option key={item} value={item} />)}</datalist>
      </div>
    </div>
    <div className="quant-body">
      <div className="quant-top">
        <section className="panel quant-chart-panel">
          <div className="panel-title"><span>{symbol ? short(symbol) : "—"} · {frame < 3600 ? `${frame / 60}m` : "1h"}</span>
            <span>{chart.data ? `${num.format(chart.data.candles.length)} bars` : chart.error || "loading…"}</span></div>
          {chart.data?.candles.length ? <InstrumentChart data={chart.data} /> : <div className="chart-empty">{chart.error || "Loading candles…"}</div>}
        </section>
        <InstrumentStats data={chart.data} bench={bench.data} rust={rust.data} frame={frame} whale={whaleBySymbol.get(symbol)} />
      </div>
      <div className="quant-bottom">
        <CrossSection whales={whaleBySymbol} selected={symbol} onSelect={onSelect} />
        <EdgeCard />
      </div>
      <details className="panel quant-pipeline">
        <summary>Pipeline health — gateway fan-out, tick bus, Rust shards</summary>
        <LivePipeline symbol={symbol} onSelect={onSelect} />
      </details>
    </div>
  </section>;
}

function InstrumentChart({ data }: { data: ChartPayload }) {
  const element = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  useEffect(() => {
    if (!element.current) return;
    const chart = createChart(element.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#0a111b" }, textColor: "#8a9bb2", fontSize: 10 },
      grid: { vertLines: { color: "#131c28" }, horzLines: { color: "#131c28" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#263246" },
      rightPriceScale: { borderColor: "#263246" },
    });
    chartRef.current = chart;
    const bars = data.candles.slice(-1500);
    const time = (ts: number) => (ts + IST_OFFSET) as Time;
    chart.addSeries(CandlestickSeries, { upColor: "#22c893", downColor: "#f15b6c", borderVisible: false, wickUpColor: "#22c893", wickDownColor: "#f15b6c" }, 0)
      .setData(bars.map((b) => ({ time: time(b.timestamp), open: b.open, high: b.high, low: b.low, close: b.close })));
    const periods = data.macd_periods || { fast: 12, slow: 26, signal: 9 };
    const closes = data.candles.map((b) => b.close);
    const offset = data.candles.length - bars.length;
    for (const [period, color] of [[periods.fast, "#e5c85a"], [periods.slow, "#72a7ff"]] as [number, string][]) {
      const line = ema(closes, period).slice(offset);
      chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, 0)
        .setData(bars.flatMap((b, i) => line[i] == null ? [] : [{ time: time(b.timestamp), value: line[i]! }]));
    }
    const indicators = new Map(data.indicators.map((row) => [row.timestamp, row]));
    const rows = bars.map((b) => indicators.get(b.timestamp)).filter((r): r is Indicator => !!r);
    chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, 1)
      .setData(rows.filter((r) => r.histogram != null).map((r) => ({ time: time(r.timestamp), value: r.histogram!, color: r.histogram! >= 0 ? "#22c89399" : "#f15b6c99" })));
    chart.addSeries(LineSeries, { color: "#72a7ff", lineWidth: 1, priceLineVisible: false }, 1)
      .setData(rows.filter((r) => r.macd != null).map((r) => ({ time: time(r.timestamp), value: r.macd! })));
    chart.addSeries(LineSeries, { color: "#e5c85a", lineWidth: 1, priceLineVisible: false }, 1)
      .setData(rows.filter((r) => r.signal != null).map((r) => ({ time: time(r.timestamp), value: r.signal! })));
    try { chart.panes()[1]?.setHeight(110); } catch { /* older library: default split */ }
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 160), to: bars.length + 3 });
    return () => { chart.remove(); chartRef.current = null; };
  }, [data]);
  return <div ref={element} className="quant-chart" />;
}

function InstrumentStats({ data, bench, rust, frame, whale }: {
  data?: ChartPayload; bench?: ChartPayload; rust?: Rust; frame: number; whale?: WhaleName;
}) {
  const stats = useMemo(() => {
    if (!data?.candles.length) return null;
    const bars = data.candles;
    const macd = data.indicators.map((r) => r.macd);
    const hist = data.indicators.map((r) => r.histogram);
    const last = data.indicators[data.indicators.length - 1];
    const vol = rollingVolatility(bars, 20, barsPerYear(frame));
    const volNow = vol[vol.length - 1];
    const volHistory = vol.filter((v): v is number => v != null);
    const absHist = hist.filter((h): h is number => h != null).map(Math.abs);
    const relation = bench?.candles.length && !data.symbol.endsWith("NIFTY50-INDEX") ? betaCorrelation(bars, bench.candles) : null;
    return {
      last, macdAge: barsSinceSignChange(macd), crossAge: barsSinceSignChange(hist),
      histPct: percentileRank(absHist, last?.histogram != null ? Math.abs(last.histogram) : null),
      volNow, volPct: percentileRank(volHistory, volNow), atr: atrPct(bars),
      r1: sessionReturn(bars, 1), r5: sessionReturn(bars, 5), r20: sessionReturn(bars, 20),
      range: rangePosition(bars), relation, close: bars[bars.length - 1].close,
    };
  }, [data, bench, frame]);
  if (!stats) return <section className="panel quant-stats"><div className="chart-empty">No candles yet.</div></section>;
  const trend = stats.last?.macd == null ? "—" : stats.last.macd >= 0 ? (stats.last.histogram! >= 0 ? "bullish, strengthening" : "bullish, fading")
    : (stats.last.histogram! <= 0 ? "bearish, strengthening" : "bearish, fading");
  const cells: [string, string, string, string?][] = [
    ["Last close", `₹${num.format(stats.close)}`, `20-bar range position ${stats.range ?? "—"}%`],
    ["Trend (MACD)", trend, `MACD ${fmt(stats.last?.macd)} · signal ${fmt(stats.last?.signal)}`, tone(stats.last?.macd)],
    ["Since zero-cross", stats.macdAge == null ? "—" : `${stats.macdAge} bars`, `signal cross ${stats.crossAge == null ? "—" : `${stats.crossAge} bars ago`}`],
    ["Histogram strength", stats.histPct == null ? "—" : `P${stats.histPct}`, `|${fmt(stats.last?.histogram)}| against its own history`, tone(stats.last?.histogram)],
    ["Realized vol (20 bars)", stats.volNow == null ? "—" : `${fmt(stats.volNow, 1)}%`, `P${stats.volPct ?? "—"} · ${volatilityRegime(stats.volPct)}`],
    ["ATR (14)", signed(stats.atr, 2, "%").replace("+", ""), "average true range, % of price"],
    ["Returns 1D / 5D / 20D", `${signed(stats.r1, 1)} / ${signed(stats.r5, 1)}`, `20D ${signed(stats.r20, 1)}`, tone(stats.r5)],
    ["vs NIFTY", stats.relation?.beta != null ? `β ${fmt(stats.relation.beta)}` : "—",
      stats.relation?.correlation != null ? `corr ${fmt(stats.relation.correlation)} over ${stats.relation.n} bars` : "benchmark"],
    ["Rust cross-check", rust ? `${rust.trend}` : "—", rust ? `RV ${fmt(rust.realized_volatility_pct, 1)}% · ${num.format(rust.candle_count)} candles` : "on-demand analysis"],
    ["Whale today", whale ? `${whale.side} · P ${whale.probability != null ? `${Math.round(whale.probability * 100)}%` : "—"}` : "none",
      whale ? `${whale.events} events · ${whale.patterns.slice(0, 2).join(" · ") || "—"}` : "no Layer A pattern today", whale ? (whale.side === "buy" ? "positive" : whale.side === "sell" ? "negative" : "") : ""],
  ];
  return <section className="panel quant-stats">
    <div className="panel-title"><span>Against its own history</span><span>{frame < 3600 ? `${frame / 60}m` : "1h"} bars</span></div>
    <div className="quant-stat-grid">{cells.map(([label, value, note, cls]) => <div key={label}>
      <span>{label}</span><b className={cls || ""}>{value}</b><small>{note}</small></div>)}</div>
  </section>;
}

type SortKey = "abs_histogram" | "histogram" | "volatility" | "whale";

function CrossSection({ whales, selected, onSelect }: { whales: Map<string, WhaleName>; selected: string; onSelect: (s: string) => void }) {
  const scan = useJson<{ rows: LiveRow[] }>(`${API_URL}/parallel/live/scan?sort=abs_histogram&limit=2000`, [], 15_000);
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("abs_histogram");
  const [whalesOnly, setWhalesOnly] = useState(false);
  const rows = scan.data?.rows || [];
  const breadth = useMemo(() => {
    const live = rows.filter((r) => r.warm && r.macd != null && kindOf(r.symbol) !== "option");
    // Option premiums run at hundreds of percent; the market read is the underlyings'.
    const vols = live.map((r) => r.realized_volatility_pct).filter((v): v is number => v != null).sort((a, b) => a - b);
    return {
      n: live.length, bullish: live.length ? Math.round(100 * live.filter((r) => (r.macd ?? 0) > 0).length / live.length) : null,
      rising: live.length ? Math.round(100 * live.filter((r) => (r.histogram ?? 0) > 0).length / live.length) : null,
      medianVol: vols.length ? vols[Math.floor(vols.length / 2)] : null, whales: whales.size,
    };
  }, [rows, whales]);
  const shown = useMemo(() => {
    const q = query.trim().toUpperCase();
    const list = rows.filter((r) => (kind === "all" || kindOf(r.symbol) === kind) && (!q || r.symbol.includes(q))
      && (!whalesOnly || whales.has(r.symbol)));
    const key = (r: LiveRow) => sort === "histogram" ? (r.histogram ?? -Infinity) : sort === "volatility" ? (r.realized_volatility_pct ?? -Infinity)
      : sort === "whale" ? (whales.get(r.symbol)?.probability ?? -1) : Math.abs(r.histogram ?? 0);
    return [...list].sort((a, b) => key(b) - key(a)).slice(0, 300);
  }, [rows, kind, query, sort, whalesOnly, whales]);
  return <section className="panel quant-cross">
    <div className="panel-title"><span>Live cross-section · 1-minute Rust shards</span><span>{rows.length} symbols</span></div>
    <div className="quant-breadth">
      <div><span>MACD above zero</span><b>{breadth.bullish ?? "—"}%</b><small>of {breadth.n} warm stocks, futures and indices</small></div>
      <div><span>Histogram rising</span><b>{breadth.rising ?? "—"}%</b><small>momentum improving now</small></div>
      <div><span>Median realized vol</span><b>{fmt(breadth.medianVol, 1)}%</b><small>stocks, futures, indices · 2-hour window</small></div>
      <div><span>Whale names today</span><b>{breadth.whales}</b><small>Layer A patterns (Auction page)</small></div>
    </div>
    <div className="quant-filters">
      <div className="radar-filter">{[["all", "All"], ["cash", "Stocks"], ["future", "Futures"], ["option", "Options"], ["index", "Indices"]].map(([k, label]) =>
        <button key={k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>{label}</button>)}</div>
      <div className="radar-filter">{([["abs_histogram", "|Hist|"], ["histogram", "Hist"], ["volatility", "Vol"], ["whale", "Whale P"]] as [SortKey, string][]).map(([k, label]) =>
        <button key={k} className={sort === k ? "active" : ""} onClick={() => setSort(k)}>{label}</button>)}</div>
      <button className={whalesOnly ? "of-toggle active" : "of-toggle"} onClick={() => setWhalesOnly((v) => !v)}>Whales only</button>
      <input className="quant-search" type="search" placeholder="Filter…" value={query} onChange={(e) => setQuery(e.target.value)} />
    </div>
    <div className="live-table-wrap"><table className="live-table">
      <thead><tr><th>Symbol</th><th>LTP</th><th>MACD</th><th>Hist</th><th>Vol %</th><th>Whale P</th><th>Lean</th><th>Pattern</th></tr></thead>
      <tbody>{shown.map((r, i) => { const w = whales.get(r.symbol); return <tr key={`${r.symbol}:${i}`}className={r.symbol === selected ? "selected" : ""} onClick={() => onSelect(r.symbol)}>
        <td title={r.symbol}>{short(r.symbol)}{r.warm ? "" : " ·"}</td><td>{fmt(r.ltp)}</td>
        <td className={tone(r.macd)}>{fmt(r.macd)}</td><td className={tone(r.histogram)}>{fmt(r.histogram)}</td>
        <td>{fmt(r.realized_volatility_pct, 1)}</td>
        <td>{w?.probability != null ? `${Math.round(w.probability * 100)}%` : w ? "·" : ""}</td>
        <td className={w?.side === "buy" ? "positive" : w?.side === "sell" ? "negative" : ""}>{w?.side ?? ""}</td>
        <td title={w?.patterns.join(" · ")}>{w?.patterns[0] ?? ""}</td></tr>; })}</tbody>
    </table></div>
    <p className="quant-note">Cross-section from the Rust live shards (1-minute bars). “·” marks MACD still warming. Click a row to chart it above.</p>
  </section>;
}

function EdgeCard() {
  const study = useJson<Study>(`${API_URL}/api/auction/whale/study`, [], 600_000);
  const s = study.data?.latest;
  const features = s?.features_test || {};
  const bins = Object.entries(features).flatMap(([feature, values]) => Object.entries(values)
    .filter(([, v]) => (v.n ?? 0) >= 150 && v.lift != null).map(([value, v]) => ({ label: `${feature.replace(/_/g, " ")}: ${value}`, ...v })));
  const best = [...bins].sort((a, b) => (b.lift ?? 0) - (a.lift ?? 0)).slice(0, 4);
  const worst = [...bins].sort((a, b) => (a.lift ?? 0) - (b.lift ?? 0)).slice(0, 3);
  return <section className="panel quant-edge">
    <div className="panel-title"><span>Whale edge · walk-forward study</span><span>{s ? `#${s.study_id} · ${s.since} → ${s.until}` : ""}</span></div>
    {!s ? <div className="chart-empty">{study.error || "No study yet — it runs nightly after the close."}</div> : <>
      <p className="quant-edge-verdict">{s.verdict}</p>
      <div className="quant-edge-grid">
        <div><span>Held-out spikes</span><b>{s.test?.hit_rate != null ? `${(s.test.hit_rate * 100).toFixed(1)}%` : "—"}</b>
          <small>vs ordinary minutes {s.control_test?.hit_rate != null ? `${(s.control_test.hit_rate * 100).toFixed(1)}%` : "—"}</small></div>
        {(s.test_quintiles || []).map((q, i) => <div key={i}><span>Quintile {i + 1}</span><b>{q.hit_rate != null ? `${(q.hit_rate * 100).toFixed(1)}%` : "—"}</b>
          <small>lift {q.lift ?? "—"} · z {q.z ?? "—"}</small></div>)}
      </div>
      <div className="quant-edge-lists">
        <div><span>Follows through more</span>{best.map((b) => <small key={b.label}>{b.label} — {(b.hit_rate! * 100).toFixed(1)}% (n {b.n})</small>)}</div>
        <div><span>Follows through less</span>{worst.map((b) => <small key={b.label}>{b.label} — {(b.hit_rate! * 100).toFixed(1)}% (n {b.n})</small>)}</div>
      </div>
      <p className="quant-note">{s.events.toLocaleString("en-IN")} minute-level spikes on {s.symbols} instruments over {s.sessions} sessions; weights fitted on the early sessions only and judged on the later ones. A hit is a move of at least half the instrument's typical 30-minute move in the spike's direction (stocks net of NIFTY).</p>
    </>}
  </section>;
}
