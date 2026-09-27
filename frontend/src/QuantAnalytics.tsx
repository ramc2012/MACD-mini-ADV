import { useEffect, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import { requestJson } from "./requestJson";
import { LivePipeline } from "./LivePipeline";

type Analysis = {
  symbol: string;
  timeframe_seconds: number;
  candle_count: number;
  last_timestamp: number | null;
  last_close: number | null;
  ema_fast: number | null;
  ema_slow: number | null;
  macd: number | null;
  signal: number | null;
  histogram: number | null;
  fast_period?: number;
  slow_period?: number;
  signal_period?: number;
  realized_volatility_pct: number | null;
  trend: "bullish" | "bearish" | "neutral" | "no_data";
};

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: Analysis; fetchedAt: Date };

const priceFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const indicatorFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 5 });

function value(amount: number | null, format = indicatorFormat): string {
  return amount === null || !Number.isFinite(amount) ? "—" : format.format(amount);
}

function candleTime(timestamp: number | null): string {
  return timestamp === null ? "—" : new Date(timestamp * 1000).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short",
  });
}

export function QuantAnalyticsPage({ symbols, selected, timeframe, onSelect }: {
  symbols: string[];
  selected: string;
  timeframe?: number;
  onSelect: (symbol: string) => void;
}) {
  const symbol = symbols.includes(selected) ? selected : symbols[0] || "";
  const [refreshKey, setRefreshKey] = useState(0);
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    const query = new URLSearchParams({ symbol });
    if (timeframe) query.set("timeframe_seconds", String(timeframe));
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    setState({ kind: "loading" });
    void requestJson<Analysis>(`${API_URL}/parallel/analytics?${query}`, { headers, signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) {
          if (data.symbol !== symbol) throw new Error("Analytics returned a different instrument");
          setState({ kind: "ready", data, fetchedAt: new Date() });
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setState({ kind: "error", message: error instanceof Error ? error.message : "Analytics unavailable" });
      });
    return () => controller.abort();
  }, [symbol, timeframe, refreshKey]);

  const analysis = state.kind === "ready" ? state.data : undefined;
  // The engine's configured periods; an older analytics build omits them.
  const fast = analysis?.fast_period ?? 12;
  const slow = analysis?.slow_period ?? 26;
  const signal = analysis?.signal_period ?? 9;
  return <section className="ledger-page quant-page">
    <div className="page-heading quant-heading">
      <div>
        <h1>Quant analytics</h1>
        <p>Rust calculations from the selected instrument’s chart history, routed through the Go gateway.</p>
      </div>
      <div className="quant-controls">
        <label htmlFor="quant-symbol">Instrument</label>
        <select id="quant-symbol" value={symbol} disabled={!symbols.length} onChange={(event) => onSelect(event.target.value)}>
          {!symbols.length && <option value="">Waiting for instruments</option>}
          {symbols.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <button type="button" disabled={!symbol || state.kind === "loading"} onClick={() => setRefreshKey((key) => key + 1)}>Refresh</button>
      </div>
    </div>

    {!symbol ? <div className="quant-message panel" role="status">No subscribed instruments are available yet. The analytics page will be ready when the trading engine sends its symbol list.</div>
      : state.kind === "loading" ? <div className="quant-message panel" role="status">Calculating analytics for {symbol}…</div>
        : state.kind === "error" ? <div className="quant-message panel quant-error" role="alert">
          <strong>Analytics unavailable</strong><span>{state.message}</span><button type="button" onClick={() => setRefreshKey((key) => key + 1)}>Try again</button>
        </div>
          : analysis && analysis.candle_count === 0 ? <div className="quant-message panel" role="status">
            <strong>No chart candles for {symbol}</strong><span>Choose another instrument or refresh after historical candles arrive.</span>
          </div>
            : analysis && <>
              <div className="quant-summary panel">
                <div><span>Instrument</span><strong>{analysis.symbol}</strong></div>
                <div><span>Chart interval</span><strong>{analysis.timeframe_seconds / 60} min</strong></div>
                <div><span>Last candle · IST</span><strong>{candleTime(analysis.last_timestamp)}</strong></div>
                <div><span>Updated</span><strong>{state.kind === "ready" ? state.fetchedAt.toLocaleTimeString("en-IN") : "—"}</strong></div>
              </div>
              <div className="quant-grid">
                <QuantMetric label="Trend" value={analysis.trend.toUpperCase()} tone={analysis.trend} description="Direction of the MACD line" />
                <QuantMetric label="Last close" value={`₹${value(analysis.last_close, priceFormat)}`} description="Most recent stored candle close" />
                <QuantMetric label="Candles" value={priceFormat.format(analysis.candle_count)} description="Unique chart timestamps analyzed" />
                <QuantMetric label="Realized volatility" value={analysis.realized_volatility_pct === null ? "—" : `${value(analysis.realized_volatility_pct, priceFormat)}%`} description="Annualized from adjacent-bar log returns; overnight gaps excluded" />
                <QuantMetric label={`EMA ${fast}`} value={value(analysis.ema_fast)} description="Fast exponential moving average" />
                <QuantMetric label={`EMA ${slow}`} value={value(analysis.ema_slow)} description="Slow exponential moving average" />
                <QuantMetric label="MACD" value={value(analysis.macd)} tone={analysis.macd === null ? undefined : analysis.macd >= 0 ? "bullish" : "bearish"} description={`EMA ${fast} minus EMA ${slow}`} />
                <QuantMetric label="Signal" value={value(analysis.signal)} description={`EMA ${signal} of MACD`} />
                <QuantMetric label="Histogram" value={value(analysis.histogram)} tone={analysis.histogram === null ? undefined : analysis.histogram >= 0 ? "bullish" : "bearish"} description="MACD minus signal" />
              </div>
              <p className="quant-note">Read-only analysis of stored candles. These values do not place orders or change the trading engine’s strategy.</p>
            </>}

    <LivePipeline symbol={symbol} onSelect={onSelect} />
  </section>;
}

function QuantMetric({ label, value: metricValue, description, tone }: {
  label: string;
  value: string;
  description: string;
  tone?: "bullish" | "bearish" | "neutral" | "no_data";
}) {
  return <div className="quant-metric panel"><span>{label}</span><strong className={tone ? `quant-${tone}` : undefined}>{metricValue}</strong><small>{description}</small></div>;
}
