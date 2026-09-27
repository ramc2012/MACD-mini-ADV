import { AreaSeries, ColorType, createChart, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { orderedEquityPoints, realizedEquityPoints } from "./equityMath";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";
import type { EquityPoint, LiveEquityPoint, ResearchOpenPosition, ResearchSummary, ResearchTrade, Snapshot } from "./types";

const money = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const PERIODS = [{ label: "1D", value: "1d" }, { label: "1W", value: "1w" }, { label: "1M", value: "1m" }, { label: "All", value: "all" }] as const;
const TIMEFRAMES = [{ label: "5s", value: 5 }, { label: "1m", value: 60 }, { label: "5m", value: 300 }, { label: "30m", value: 1800 }, { label: "1D", value: 86400 }];

export function EquityCurve({ researchRows, researchTrades, researchOpenPositions, summary, parameters, snapshot, researchStatus }: { parameters?: Record<string, unknown>; snapshot?: Snapshot; researchStatus?: string; researchRows: EquityPoint[]; researchTrades: ResearchTrade[]; researchOpenPositions: ResearchOpenPosition[]; summary?: ResearchSummary }) {
  const [source, setSource] = useState<"LIVE" | "RESEARCH" | "WINNERS">("LIVE");
  const [period, setPeriod] = useState<"1d" | "1w" | "1m" | "all">("1d");
  const [timeframe, setTimeframe] = useState(60);
  const [liveRows, setLiveRows] = useState<LiveEquityPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState("");
  const [retry, setRetry] = useState(0);
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<{ api: IChartApi; line: ISeriesApi<"Area"> }>();

  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    let stopped = false;
    let inflight = false;
    const controller = new AbortController();
    setLiveRows([]); setLoading(true); setError(""); setUpdatedAt("");
    const load = () => {
      if (inflight) return;
      inflight = true;
      void requestJson<LiveEquityPoint[]>(`${API_URL}/api/equity-history?period=${period}&timeframe_seconds=${timeframe}`, { headers, signal: controller.signal })
        .then((data) => { if (!stopped) { setLiveRows(data); setError(""); setUpdatedAt(new Date().toLocaleTimeString("en-IN")); } })
        .catch((reason) => { if (!stopped) setError(reason instanceof Error ? reason.message : "Equity unavailable"); })
        .finally(() => { inflight = false; if (!stopped) setLoading(false); });
    };
    load();
    const timer = window.setInterval(load, 15_000);
    return () => { stopped = true; controller.abort(); clearInterval(timer); };
  }, [period, timeframe, retry]);

  const winnerRows = useMemo(() => {
    const initial = 1_000_000;
    let equity = initial;
    const closed = researchTrades.filter((row) => row.pnl > 0).sort((a, b) => Date.parse(a.exit_time) - Date.parse(b.exit_time));
    const curve = closed.map((row) => {
      equity += row.pnl;
      return { time: Math.floor(Date.parse(row.exit_time) / 1000) as Time, value: equity };
    });
    const openPnl = researchOpenPositions.filter((row) => row.unrealized_pnl > 0).reduce((sum, row) => sum + row.unrealized_pnl, 0);
    const openTimes = researchOpenPositions.filter((row) => row.unrealized_pnl > 0).map((row) => row.last_time).sort();
    const latest = openTimes[openTimes.length - 1];
    if (latest) curve.push({ time: Math.floor(Date.parse(latest) / 1000) as Time, value: equity + openPnl });
    return { curve, closed: closed.length, open: researchOpenPositions.filter((row) => row.unrealized_pnl > 0).length, final: equity + openPnl, pnl: equity + openPnl - initial };
  }, [researchTrades, researchOpenPositions]);
  const points = useMemo(() => {
    const rows = source === "LIVE"
      ? liveRows.map((row) => ({ time: Math.floor(Date.parse(row.timestamp) / 1000), value: row.equity }))
      : source === "WINNERS" ? winnerRows.curve.map(row => ({time: Number(row.time), value: row.value}))
        : realizedEquityPoints(researchTrades);
    return orderedEquityPoints(rows).map(row => ({...row, time: row.time as Time}));
  }, [source, liveRows, researchTrades, winnerRows]);
  const config = snapshot?.config;
  const risk = snapshot?.execution.risk;
  const show = (value: unknown) => value == null ? "Not recorded" : Array.isArray(value) ? value.join(" / ") : String(value);
  const comparison = [
    ["Candle seconds", show(parameters?.timeframe_seconds), show(config?.timeframe_seconds)],
    ["MACD periods", show(parameters?.macd), config ? `${config.fast} / ${config.slow} / ${config.signal}` : "Unavailable"],
    ["Entry confirmations", show(parameters?.entry_filter), risk?.entry_filter || "Unavailable"],
    ["Entry sizing", show(parameters?.quantity_rule), risk ? risk.target_position_notional ? `Target ₹${money.format(risk.target_position_notional)} in whole lots` : "One exchange lot" : "Unavailable"],
    ["Maximum lots", show(parameters?.max_trade_lots), show(risk?.max_trade_lots)],
    ["Hard stop %", show(parameters?.hard_stop_pct), show(risk?.hard_stop_pct)],
    ["Trailing stop %", show(parameters?.trailing_stop_pct), show(risk?.trailing_stop_pct)],
    ["Slippage bps / side", show(parameters?.slippage_bps_per_side), show(risk?.slippage_bps)],
    ["Signal / fill timing", summary?.method || "Not recorded", "Intrabar evaluation; paper fill at current quote"],
  ];
  const lastLive = liveRows[liveRows.length - 1];

  useEffect(() => {
    if (!element.current) return;
    const api = createChart(element.current, {
      width: element.current.clientWidth, height: element.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#090d14" }, textColor: "#8796aa" },
      grid: { vertLines: { color: "#18202d" }, horzLines: { color: "#18202d" } },
      rightPriceScale: { borderColor: "#263144" },
      timeScale: { borderColor: "#263144", timeVisible: true, secondsVisible: true },
      localization: { locale: "en-IN", priceFormatter: (value: number) => `₹${money.format(value)}` },
    });
    const line = api.addSeries(AreaSeries, { lineColor: "#4da3ff", topColor: "#4da3ff55", bottomColor: "#4da3ff08", lineWidth: 2 });
    chart.current = { api, line };
    const observer = new ResizeObserver(() => api.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => { observer.disconnect(); api.remove(); };
  }, []);
  const lastFit = useRef("");
  useEffect(() => {
    if (!chart.current) return;
    chart.current.line.applyOptions({ title: source === "LIVE" ? "Paper equity" : source === "WINNERS" ? "Hindsight winners only" : "Cumulative P&L" });
    chart.current.line.setData(points);
    // Fit only when the view parameters change — not on the 15s data poll,
    // which would keep resetting the user's zoom and pan.
    const fitKey = `${source}:${period}:${timeframe}:${winnerRows.final}`;
    if (points.length && lastFit.current !== fitKey) {
      lastFit.current = fitKey;
      chart.current.api.timeScale().fitContent();
    }
  }, [points, source, period, timeframe]);

  return <section className="ledger-page equity-page">
    <div className="page-heading">
      <div><h1>Equity curve</h1><p>{source === "LIVE" ? "Marked-to-market paper equity — every fill is logged instantly, ticks sampled every 5 seconds" : source === "WINNERS" ? "Hindsight-only: each research trade is included only after knowing it finished profitable; open winners are marked at the latest bar" : "Chronological realized P&L from the latest recorded walk-forward run"}</p></div>
      <div className="rrg-controls">
        {source === "LIVE" && <>
          <div className="radar-filter">{PERIODS.map((row) => <button key={row.value} className={period === row.value ? "active" : ""} onClick={() => setPeriod(row.value)}>{row.label}</button>)}</div>
          <div className="radar-filter">{TIMEFRAMES.map((row) => <button key={row.value} className={timeframe === row.value ? "active" : ""} onClick={() => setTimeframe(row.value)}>{row.label}</button>)}</div>
        </>}
        <div className="equity-toggle">{(["LIVE", "RESEARCH", "WINNERS"] as const).map((key) => <button key={key} className={source === key ? "active" : ""} onClick={() => setSource(key)}>{key === "LIVE" ? `Live paper (${liveRows.length})` : key === "WINNERS" ? `Winners only (${winnerRows.closed + winnerRows.open})` : `Walk-forward (${researchRows.length})`}</button>)}</div>
      </div>
    </div>
    {source === "LIVE" && (loading || error) && <div className="data-status" role="status">{loading ? "Loading equity…" : `${error}. ${updatedAt ? `Showing data fetched at ${updatedAt}.` : "Equity is unavailable."}`}<button onClick={() => setRetry((n) => n + 1)}>Retry</button></div>}
    {source === "RESEARCH" && <details className="research-comparison"><summary>Archived research versus current paper settings · {summary?.run_id || "no run available"}</summary><p>Results apply to the recorded run. Different sizing, filters, or execution timing require separate validation.</p><table className="ledger-table"><thead><tr><th>Parameter</th><th>Recorded research</th><th>Current paper configuration</th></tr></thead><tbody>{comparison.map(([key, recorded, current]) => <tr key={key}><td>{key}</td><td>{recorded}</td><td>{current}</td></tr>)}</tbody></table></details>}
    {source !== "LIVE" && researchStatus && <div className="data-status" role="status">Research: {researchStatus}</div>}
    {source === "LIVE" ? <div className="equity-metrics">
      <div><span>Equity</span><b>{lastLive ? `₹${money.format(lastLive.equity)}` : "—"}</b></div>
      <div><span>Realized P&L</span><b className={(lastLive?.realized_pnl || 0) >= 0 ? "positive" : "negative"}>{lastLive ? `₹${money.format(lastLive.realized_pnl)}` : "—"}</b></div>
      <div><span>Unrealized P&L</span><b className={(lastLive?.unrealized_pnl || 0) >= 0 ? "positive" : "negative"}>{lastLive ? `₹${money.format(lastLive.unrealized_pnl)}` : "—"}</b></div>
      <div><span>Cash</span><b>{lastLive ? `₹${money.format(lastLive.cash)}` : "—"}</b></div>
    </div> : source === "WINNERS" ? <div className="equity-metrics">
      <div><span>Marked equity</span><b className="positive">₹{money.format(winnerRows.final)}</b></div>
      <div><span>Hindsight P&L</span><b className="positive">₹{money.format(winnerRows.pnl)}</b></div>
      <div><span>Closed winners</span><b>{winnerRows.closed}</b></div>
      <div><span>Open winners marked</span><b>{winnerRows.open}</b></div>
    </div> : <div className="equity-metrics">
      <div><span>Net P&L</span><b className={(summary?.net_pnl || 0) >= 0 ? "positive" : "negative"}>{summary ? `₹${money.format(summary.net_pnl)}` : "—"}</b></div>
      <div><span>Maximum drawdown</span><b className="negative">{summary ? `₹${money.format(summary.max_drawdown)}` : "—"}</b></div>
      <div><span>Win rate</span><b>{summary ? `${summary.win_rate_pct.toFixed(2)}%` : "—"}</b></div>
      <div><span>Run</span><b>{summary?.run_id || "—"}</b></div>
    </div>}
    <div className="equity-chart panel" ref={element}>{!points.length && <div className="chart-empty">{source === "LIVE" ? loading ? "Loading equity…" : error ? "Equity unavailable — retry above" : "No equity samples in this period yet — fills log instantly and ticks sample every 5 seconds while the feed is live" : source === "WINNERS" ? "No profitable research trades or marked open winners yet" : "No closed research trades yet"}</div>}</div>
  </section>;
}
