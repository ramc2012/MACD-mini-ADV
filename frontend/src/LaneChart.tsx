import { useEffect, useMemo, useRef, useState } from "react";
import { TradingChart, DEFAULT_LAYERS, type PriceStyle } from "./Chart";
import { closeStudies, sessionCutoff } from "./chartMath";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";
import { MCX_QUOTES_EVENT, type McxQuote } from "./mcxMath";
import { mcxFormingBar, type McxStreamMinute } from "./mcxChartMath";
import type { Candle, ChartLevel, ChartMarker, Indicator } from "./types";

/** One lane's view of one instrument: the stored bars with the MACD lane's studies, the lane's own fills as
 * markers and the position's stop / trail / target as lines. Self-loading, so any lane page can drop it in
 * without the terminal's selected-symbol state. History comes from /api/chart, which also serves stored
 * contracts outside the MACD watchlist (shadow and agentic lanes' strikes). */

type Period = "1D" | "5D" | "1M" | "3M" | "ALL";
// Labels say "Mo" so a 3-month window never reads like the 3-minute bar size beside it.
const PERIODS: { key: Period; label: string; sessions: number }[] = [
  { key: "1D", label: "1D", sessions: 1 }, { key: "5D", label: "5D", sessions: 5 }, { key: "1M", label: "1Mo", sessions: 22 },
  { key: "3M", label: "3Mo", sessions: 64 }, { key: "ALL", label: "All", sessions: 0 },
];
const FRAMES: { seconds: number; label: string }[] = [
  { seconds: 180, label: "3m" }, { seconds: 300, label: "5m" }, { seconds: 900, label: "15m" }, { seconds: 1800, label: "30m" }, { seconds: 3600, label: "1h" }, { seconds: 86400, label: "1D" },
];
const STORE = "macd.laneChart";
type Prefs = { period: Period; frame: number; style: PriceStyle; rsi: boolean; studies: boolean };
const DEFAULTS: Prefs = { period: "1M", frame: 1800, style: "candles", rsi: false, studies: true };
function recall(store: string, defaults: Prefs): Prefs {
  try { return { ...defaults, ...JSON.parse(window.localStorage.getItem(store) || "{}") }; } catch { return defaults; }
}
function remember(store: string, prefs: Prefs) {
  try { window.localStorage.setItem(store, JSON.stringify(prefs)); } catch { /* not kept */ }
}
const headers = (): HeadersInit => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});
type ChartPayload = { candles: Candle[]; indicators: Indicator[]; forming_minutes?: Candle[]; observed_ms?: number };
type History = ChartPayload & { key: string };
type LiveMcxChart = { key: string; minutes: McxStreamMinute[] };

export function LaneChart({ symbol, markers = [], levels = [], refreshMs = 60_000, className = "" }: {
  symbol: string; markers?: ChartMarker[]; levels?: ChartLevel[]; refreshMs?: number; className?: string;
}) {
  const mcx = symbol.startsWith("MCX:");
  const store = mcx ? `${STORE}.mcx` : STORE;
  const [prefs, setPrefs] = useState<Prefs>(() => recall(store, mcx ? { ...DEFAULTS, style: "ohlc" } : DEFAULTS));
  const [history, setHistory] = useState<History>();
  const [mcxTail, setMcxTail] = useState<LiveMcxChart>();
  const [error, setError] = useState("");
  const [fitNonce, setFitNonce] = useState(0);
  const key = `${symbol}:${prefs.frame}`;
  const live = useRef(key);
  live.current = key;
  const update = (patch: Partial<Prefs>) => setPrefs((old) => { const next = { ...old, ...patch }; remember(store, next); return next; });

  useEffect(() => {
    let stopped = false;
    const controller = new AbortController();
    setError("");
    let pending = false;
    const load = () => {
      if (pending) return;
      pending = true;
      void requestJson<ChartPayload>(
        `${API_URL}/api/chart/${encodeURIComponent(symbol)}?timeframe_seconds=${prefs.frame}`, { headers: headers(), signal: controller.signal, cache: "no-store" })
        .then((data) => { if (!stopped && live.current === key) { setHistory({ ...data, key }); setError(""); } })
        .catch((exc) => { if (!stopped && exc?.name !== "AbortError") setError(exc?.message?.includes("404") ? "No stored bars for this instrument" : String(exc?.message || exc)); })
        .finally(() => { pending = false; });
    };
    void load();
    const interval = mcx ? 15_000 : refreshMs;
    const id = interval > 0 ? window.setInterval(load, interval) : 0;
    return () => { stopped = true; controller.abort(); if (id) window.clearInterval(id); };
  }, [symbol, prefs.frame, refreshMs, key, mcx]);

  useEffect(() => {
    if (!mcx) return;
    function updateMinutes(event: Event) {
      const quotes = (event as CustomEvent<(McxQuote & { minutes?: Candle[] })[]>).detail;
      const q = Array.isArray(quotes) ? quotes.find(row => row.symbol === symbol && row.source === "ws") : undefined;
      if (!q?.minutes?.length) return;
      const minutes = q.minutes;
      setMcxTail(old => {
        const rows = new Map((old?.key === key ? old.minutes : []).map(stamped => [stamped.row.timestamp, stamped]));
        for (const row of minutes) {
          const prior = rows.get(row.timestamp);
          if (row.symbol === symbol && (!prior || (q.updated_ms >= prior.seen && (row.closed || !prior.row.closed)))) {
            rows.set(row.timestamp, { row, seen: q.updated_ms });
          }
        }
        return { key, minutes: [...rows.values()].sort((a,b) => a.row.timestamp - b.row.timestamp).slice(-96) };
      });
    }
    window.addEventListener(MCX_QUOTES_EVENT, updateMinutes);
    return () => window.removeEventListener(MCX_QUOTES_EVENT, updateMinutes);
  }, [mcx, symbol, key]);

  const ready = history?.key === key;
  const all = useMemo(() => (ready ? history.candles : []), [ready, history]);
  const current = useMemo(() => ready && mcx ? mcxFormingBar(history.forming_minutes ?? [],
    mcxTail?.key === key ? mcxTail.minutes : [], history.observed_ms ?? 0, all.at(-1)?.timestamp ?? -1, prefs.frame) : undefined,
    [ready, mcx, history, mcxTail, key, all, prefs.frame]);
  const cutoff = useMemo(() => sessionCutoff(all, PERIODS.find((row) => row.key === prefs.period)?.sessions || 0), [all, prefs.period]);
  const candles = useMemo(() => (cutoff ? all.filter((row) => row.timestamp >= cutoff) : all), [all, cutoff]);
  const indicators = useMemo(() => (ready ? history.indicators.filter((row) => !cutoff || row.timestamp >= cutoff) : []), [ready, history, cutoff]);
  const studies = useMemo(() => { const rows = closeStudies(all); return cutoff ? rows.filter((row) => row.timestamp >= cutoff) : rows; }, [all, cutoff]);
  // A marker before the window would pin to the first bar and mislead; only those inside it are drawn.
  const shown = useMemo(() => markers.filter((m) => !candles.length || m.timestamp >= candles[0].timestamp - prefs.frame), [markers, candles, prefs.frame]);
  const layers = useMemo(() => ({ ...DEFAULT_LAYERS, priorDay: false, ib: !mcx && prefs.studies && prefs.frame < 3600, sessions: prefs.frame < 86400, studies: prefs.studies }), [prefs.frame, prefs.studies, mcx]);

  return <div className={`lane-chart ${className}`}>
    <div className="lane-chart-bar">
      <span className="radar-filter" aria-label="Bar size">{(mcx ? [{ seconds: 60, label: "1m" }, ...FRAMES] : FRAMES).map((row) => <button key={row.seconds} className={prefs.frame === row.seconds ? "active" : ""} aria-pressed={prefs.frame === row.seconds} onClick={() => update({ frame: row.seconds })}>{row.label}</button>)}</span>
      <span className="radar-filter" aria-label="Period">{PERIODS.map((row) => <button key={row.key} className={prefs.period === row.key ? "active" : ""} aria-pressed={prefs.period === row.key} onClick={() => update({ period: row.key })}>{row.label}</button>)}</span>
      <span className="radar-filter" aria-label="Price style">
        <button className={prefs.style === "candles" ? "active" : ""} aria-pressed={prefs.style === "candles"} onClick={() => update({ style: "candles" })}>Candles</button>
        <button className={prefs.style === "ohlc" ? "active" : ""} aria-pressed={prefs.style === "ohlc"} onClick={() => update({ style: "ohlc" })}>OHLC</button>
      </span>
      <span className="radar-filter">
        <button className={prefs.studies ? "active" : ""} aria-pressed={prefs.studies} title={mcx ? "Bollinger bands, KAMA, HMA 20 and VWAP" : "Bollinger bands, KAMA, HMA 20, VWAP and the initial balance on the price pane"} onClick={() => update({ studies: !prefs.studies })}>Overlays</button>
        <button className={prefs.rsi ? "active" : ""} aria-pressed={prefs.rsi} title="RSI(14) pane" onClick={() => update({ rsi: !prefs.rsi })}>RSI</button>
      </span>
      <button className="chart-fit" title="Fit all visible bars" onClick={() => setFitNonce((n) => n + 1)}>Fit</button>
      <span className="lane-chart-key" aria-hidden="true">
        {(!mcx || markers.length > 0) && <><i className="k-entry">▲ buy</i><i className="k-exit">▼ sell</i><i className="k-loss">▼ sell at a loss</i></>}
        {levels.map((level) => <i key={level.tone} className={`k-${level.tone}`}>— {level.label.toLowerCase()} {level.price.toFixed(2)}</i>)}
      </span>
    </div>
    <TradingChart candles={candles} current={current} indicators={indicators} studies={studies} markers={shown} levels={levels} error={error || (ready && !all.length && !current ? "No stored bars for this instrument" : undefined)}
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
