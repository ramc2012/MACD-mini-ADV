import { useEffect, useMemo, useRef, useState } from "react";
import { TradingChart, DEFAULT_LAYERS, type PriceStyle } from "./Chart";
import { closeStudies, sessionCutoff } from "./chartMath";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";
import type { Candle, ChartLevel, ChartMarker, Indicator } from "./types";

/** One lane's view of one instrument: the stored bars with the MACD lane's studies, the lane's own fills as
 * markers and the position's stop / trail / target as lines. Self-loading, so any lane page can drop it in
 * without the terminal's selected-symbol state. History comes from /api/chart, which also serves stored
 * contracts outside the MACD watchlist (shadow and agentic lanes' strikes). */

type Period = "1D" | "5D" | "1M" | "3M" | "ALL";
const PERIODS: { key: Period; sessions: number }[] = [
  { key: "1D", sessions: 1 }, { key: "5D", sessions: 5 }, { key: "1M", sessions: 22 }, { key: "3M", sessions: 64 }, { key: "ALL", sessions: 0 },
];
const FRAMES: { seconds: number; label: string }[] = [
  { seconds: 300, label: "5m" }, { seconds: 900, label: "15m" }, { seconds: 1800, label: "30m" }, { seconds: 3600, label: "1h" }, { seconds: 86400, label: "1D" },
];
const STORE = "macd.laneChart";
type Prefs = { period: Period; frame: number; style: PriceStyle; rsi: boolean };
const DEFAULTS: Prefs = { period: "1M", frame: 1800, style: "candles", rsi: false };
const recall = (): Prefs => {
  try { return { ...DEFAULTS, ...JSON.parse(window.localStorage.getItem(STORE) || "{}") }; } catch { return DEFAULTS; }
};
const remember = (prefs: Prefs) => { try { window.localStorage.setItem(STORE, JSON.stringify(prefs)); } catch { /* not kept */ } };
const headers = (): HeadersInit => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});
type History = { key: string; candles: Candle[]; indicators: Indicator[] };

export function LaneChart({ symbol, markers = [], levels = [], refreshMs = 60_000, className = "" }: {
  symbol: string; markers?: ChartMarker[]; levels?: ChartLevel[]; refreshMs?: number; className?: string;
}) {
  const [prefs, setPrefs] = useState<Prefs>(recall);
  const [history, setHistory] = useState<History>();
  const [error, setError] = useState("");
  const [fitNonce, setFitNonce] = useState(0);
  const key = `${symbol}:${prefs.frame}`;
  const live = useRef(key);
  live.current = key;
  const update = (patch: Partial<Prefs>) => setPrefs((old) => { const next = { ...old, ...patch }; remember(next); return next; });

  useEffect(() => {
    let stopped = false;
    const controller = new AbortController();
    setError("");
    const load = () => requestJson<{ candles: Candle[]; indicators: Indicator[] }>(
      `${API_URL}/api/chart/${encodeURIComponent(symbol)}?timeframe_seconds=${prefs.frame}`, { headers: headers(), signal: controller.signal })
      .then((data) => { if (!stopped && live.current === key) { setHistory({ key, candles: data.candles, indicators: data.indicators }); setError(""); } })
      .catch((exc) => { if (!stopped && exc?.name !== "AbortError") setError(exc?.message?.includes("404") ? "No stored bars for this instrument" : String(exc?.message || exc)); });
    void load();
    const id = refreshMs > 0 ? window.setInterval(load, refreshMs) : 0;
    return () => { stopped = true; controller.abort(); if (id) window.clearInterval(id); };
  }, [symbol, prefs.frame, refreshMs, key]);

  const ready = history?.key === key;
  const all = useMemo(() => (ready ? history.candles : []), [ready, history]);
  const cutoff = useMemo(() => sessionCutoff(all, PERIODS.find((row) => row.key === prefs.period)?.sessions || 0), [all, prefs.period]);
  const candles = useMemo(() => (cutoff ? all.filter((row) => row.timestamp >= cutoff) : all), [all, cutoff]);
  const indicators = useMemo(() => (ready ? history.indicators.filter((row) => !cutoff || row.timestamp >= cutoff) : []), [ready, history, cutoff]);
  const studies = useMemo(() => { const rows = closeStudies(all); return cutoff ? rows.filter((row) => row.timestamp >= cutoff) : rows; }, [all, cutoff]);
  // A marker before the window would pin to the first bar and mislead; only those inside it are drawn.
  const shown = useMemo(() => markers.filter((m) => !candles.length || m.timestamp >= candles[0].timestamp - prefs.frame), [markers, candles, prefs.frame]);
  const layers = useMemo(() => ({ ...DEFAULT_LAYERS, priorDay: false, ib: prefs.frame < 3600, sessions: prefs.frame < 86400 }), [prefs.frame]);

  return <div className={`lane-chart ${className}`}>
    <div className="lane-chart-bar">
      <span className="radar-filter" aria-label="Bar size">{FRAMES.map((row) => <button key={row.seconds} className={prefs.frame === row.seconds ? "active" : ""} aria-pressed={prefs.frame === row.seconds} onClick={() => update({ frame: row.seconds })}>{row.label}</button>)}</span>
      <span className="radar-filter" aria-label="Period">{PERIODS.map((row) => <button key={row.key} className={prefs.period === row.key ? "active" : ""} aria-pressed={prefs.period === row.key} onClick={() => update({ period: row.key })}>{row.key}</button>)}</span>
      <span className="radar-filter" aria-label="Price style">
        <button className={prefs.style === "candles" ? "active" : ""} aria-pressed={prefs.style === "candles"} onClick={() => update({ style: "candles" })}>Candles</button>
        <button className={prefs.style === "ohlc" ? "active" : ""} aria-pressed={prefs.style === "ohlc"} onClick={() => update({ style: "ohlc" })}>OHLC</button>
      </span>
      <span className="radar-filter"><button className={prefs.rsi ? "active" : ""} aria-pressed={prefs.rsi} title="RSI(14) pane" onClick={() => update({ rsi: !prefs.rsi })}>RSI</button></span>
      <button className="chart-fit" title="Fit all visible bars" onClick={() => setFitNonce((n) => n + 1)}>Fit</button>
      <span className="lane-chart-key" aria-hidden="true">
        <i className="k-entry">▲ buy</i><i className="k-exit">▼ sell</i><i className="k-loss">▼ sell at a loss</i>
        {levels.map((level) => <i key={level.tone} className={`k-${level.tone}`}>— {level.label.toLowerCase()} {level.price.toFixed(2)}</i>)}
      </span>
    </div>
    <TradingChart candles={candles} indicators={indicators} studies={studies} markers={shown} levels={levels} error={error || (ready && !all.length ? "No stored bars for this instrument" : undefined)}
      timeframe={prefs.frame} layers={layers} paneCollapse={{ rsi: !prefs.rsi }} priceStyle={prefs.style}
      fitKey={`${key}:${prefs.period}:${ready ? "full" : "loading"}:${fitNonce}`} />
  </div>;
}

export type LaneChartFocus = { symbol: string; title?: string; subtitle?: string; markers?: ChartMarker[]; levels?: ChartLevel[] };

/** The lane chart in a dialog, stepping through a list of positions with ← / →; Esc closes. */
export function LaneChartModal({ list, index, onIndex, onClose }: {
  list: LaneChartFocus[]; index: number; onIndex: (index: number) => void; onClose: () => void;
}) {
  const panel = useRef<HTMLElement>(null);
  const focus = list[index];
  useEffect(() => {
    const previous = document.activeElement;
    panel.current?.querySelector<HTMLElement>(".position-chart-close")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowLeft" && list.length > 1) onIndex((index - 1 + list.length) % list.length);
      else if (event.key === "ArrowRight" && list.length > 1) onIndex((index + 1) % list.length);
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, [index, list.length, onClose, onIndex]);
  if (!focus) return null;
  return <div className="modal-backdrop position-chart-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={panel} className="position-chart-modal lane-chart-modal" role="dialog" aria-modal="true" aria-label={`Chart for ${focus.symbol}`}>
      <div className="position-chart-heading">
        <div><b>{focus.title ?? focus.symbol}</b>{focus.subtitle && <small>{focus.subtitle}</small>}</div>
        <div className="chart-controls">
          {list.length > 1 && <>
            <button className="chart-fit" title="Previous (←)" onClick={() => onIndex((index - 1 + list.length) % list.length)}>‹</button>
            <small>{index + 1} / {list.length}</small>
            <button className="chart-fit" title="Next (→)" onClick={() => onIndex((index + 1) % list.length)}>›</button>
          </>}
          <button className="position-chart-close" aria-label="Close chart" onClick={onClose}>×</button>
        </div>
      </div>
      <div className="position-chart-body"><LaneChart symbol={focus.symbol} markers={focus.markers} levels={focus.levels} /></div>
    </section>
  </div>;
}
