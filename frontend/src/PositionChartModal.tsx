import { useEffect, useRef } from "react";
import { TradingChart } from "./Chart";
import type { AuctionContext, Candle, ChartLayers, ChartMarker, Indicator, PaneCollapse } from "./types";

type PositionAudit = { signalTime?: string; signalPrice?: number; macd?: number; fillLatencyMs?: number; message?: string; intrabar?: boolean };
type PositionFocus = { position: { symbol: string; entryTime: string; entryPrice: number; audit?: PositionAudit }; list: { symbol: string; entryTime: string; entryPrice: number }[]; index: number };
type ChartPeriod = "1D" | "5D" | "1M" | "3M" | "ALL";
const periods: ChartPeriod[] = ["1D", "5D", "1M", "3M", "ALL"];

export function PositionChartModal({ focus, candles, current, indicators, markers, error, timeframe, period, rsiGate, rocGate, layers, references, paneCollapse, onPeriod, onPrevious, onNext, onClose }: {
  focus: PositionFocus; candles: Candle[]; current?: Candle; indicators: Indicator[]; markers: ChartMarker[]; error?: string; timeframe?: number; period: ChartPeriod; rsiGate?: number; rocGate?: number;
  layers?: ChartLayers; references?: AuctionContext; paneCollapse?: PaneCollapse;
  onPeriod: (period: ChartPeriod) => void; onPrevious: () => void; onNext: () => void; onClose: () => void;
}) {
  const panelRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const previousFocus = document.activeElement;
    const panel = panelRef.current;
    const focusable = () => [...(panel?.querySelectorAll<HTMLElement>("button:not([disabled]), [tabindex]:not([tabindex='-1'])") ?? [])]
      .filter((node) => node.getClientRects().length > 0);
    const initialNodes = focusable();
    initialNodes[initialNodes.length - 1]?.focus();
    const onTab = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const nodes = focusable();
      if (!nodes.length) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes[nodes.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === nodes[nodes.length - 1]) { event.preventDefault(); nodes[0].focus(); }
    };
    const onFocus = (event: FocusEvent) => {
      if (event.target instanceof Node && !panel?.contains(event.target)) {
        const nodes = focusable();
        nodes[nodes.length - 1]?.focus();
      }
    };
    document.addEventListener("keydown", onTab);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("keydown", onTab);
      document.removeEventListener("focusin", onFocus);
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowLeft") onPrevious();
      else if (event.key === "ArrowRight") onNext();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onPrevious, onNext]);

  return <div className="modal-backdrop position-chart-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={panelRef} className="position-chart-modal" role="dialog" aria-modal="true" aria-label={`Position chart for ${focus.position.symbol}`}>
      <div className="position-chart-heading">
        <div><b>{focus.position.symbol}</b><small>Entry ₹{focus.position.entryPrice.toFixed(2)}</small>{focus.position.audit?.signalTime ? <small className="signal-audit">{focus.position.audit.message || "MACD zero-cross"} {focus.position.audit.macd?.toFixed(4)} · signal → fill {focus.position.audit.fillLatencyMs}ms</small> : focus.position.audit?.message ? <small className="signal-audit muted">{focus.position.audit.message}</small> : null}</div>
        <div className="chart-controls">
          <span className="position-nav"><button aria-label="Previous open position" title="Previous open position (←)" onClick={onPrevious}>←</button><small>{focus.index + 1}/{focus.list.length} position</small><button aria-label="Next open position" title="Next open position (→)" onClick={onNext}>→</button></span>
          <span className="radar-filter">{periods.map((value, index) => <button key={value} className={period === value ? "active" : ""} title={`${value} (${index + 1})`} onClick={() => onPeriod(value)}>{value}</button>)}</span>
          <small>{(timeframe || 0) / 60}m</small>
          <button className="position-chart-close" aria-label="Close position chart" onClick={onClose}>×</button>
        </div>
      </div>
      <div className="position-chart-body"><TradingChart candles={candles} current={current} indicators={indicators} markers={markers} error={error} fitKey={`${focus.position.symbol}:${timeframe}:${period}`} rsiGate={rsiGate} rocGate={rocGate} timeframe={timeframe} layers={layers} references={references} paneCollapse={paneCollapse} /></div>
    </section>
  </div>;
}
