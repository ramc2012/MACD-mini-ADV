// Instrument statistics for the Quant page, computed from the chart's own
// candles so every figure is on the timeframe the chart shows. Each one is
// read against the instrument's OWN history (percentiles, regimes), because a
// 25% volatility is calm for one contract and a storm for another.

export type Bar = { timestamp: number; open: number; high: number; low: number; close: number; volume: number };

const IST_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });

export function ema(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = [];
  const k = 2 / (period + 1);
  let prev: number | null = null;
  values.forEach((value, index) => {
    if (index < period - 1) { out.push(null); return; }
    if (prev === null) {
      prev = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
    } else {
      prev = value * k + prev * (1 - k);
    }
    out.push(prev);
  });
  return out;
}

/** Share of `history` strictly below `value`, 0..100. */
export function percentileRank(history: number[], value: number | null | undefined): number | null {
  const clean = history.filter((v) => Number.isFinite(v));
  if (value == null || !Number.isFinite(value) || clean.length < 20) return null;
  const below = clean.filter((v) => v < value).length;
  return Math.round((below / clean.length) * 100);
}

/** Bars since `series` last changed sign (0 = it changed on the last bar). */
export function barsSinceSignChange(series: (number | null)[]): number | null {
  let last: number | null = null;
  let count = 0;
  for (let i = series.length - 1; i >= 0; i--) {
    const v = series[i];
    if (v == null || !Number.isFinite(v)) continue;
    if (last === null) { last = v; continue; }
    if (Math.sign(v) !== Math.sign(last) && v !== 0) return count;
    count += 1;
  }
  return null;
}

/** Rolling annualised realised volatility (%) from same-session log returns. */
export function rollingVolatility(bars: Bar[], window: number, barsPerYear: number): (number | null)[] {
  const returns: (number | null)[] = bars.map((bar, i) => {
    if (i === 0) return null;
    const prev = bars[i - 1];
    // An overnight gap is not a within-session move.
    if (IST_DAY.format(prev.timestamp * 1000) !== IST_DAY.format(bar.timestamp * 1000)) return null;
    return prev.close > 0 && bar.close > 0 ? Math.log(bar.close / prev.close) : null;
  });
  return returns.map((_, i) => {
    const slice = returns.slice(Math.max(0, i - window + 1), i + 1).filter((r): r is number => r != null);
    if (slice.length < Math.max(5, window * 0.6)) return null;
    const mean = slice.reduce((a, b) => a + b, 0) / slice.length;
    const variance = slice.reduce((a, b) => a + (b - mean) ** 2, 0) / (slice.length - 1);
    return Math.sqrt(variance * barsPerYear) * 100;
  });
}

export function volatilityRegime(pctile: number | null): string {
  if (pctile == null) return "—";
  return pctile >= 90 ? "extreme" : pctile >= 70 ? "elevated" : pctile <= 20 ? "compressed" : "normal";
}

/** Average true range over `period` bars, as % of the last close. */
export function atrPct(bars: Bar[], period = 14): number | null {
  if (bars.length < period + 1) return null;
  const ranges = bars.slice(-period).map((bar, i, arr) => {
    const prevClose = i === 0 ? bars[bars.length - period - 1].close : arr[i - 1].close;
    return Math.max(bar.high - bar.low, Math.abs(bar.high - prevClose), Math.abs(bar.low - prevClose));
  });
  const last = bars[bars.length - 1].close;
  return last > 0 ? (ranges.reduce((a, b) => a + b, 0) / period / last) * 100 : null;
}

/** Return (%) of the last close against the close `sessions` sessions ago. */
export function sessionReturn(bars: Bar[], sessions: number): number | null {
  const closes = new Map<string, number>();
  for (const bar of bars) closes.set(IST_DAY.format(bar.timestamp * 1000), bar.close);
  const days = [...closes.keys()];
  if (days.length <= sessions) return null;
  const then = closes.get(days[days.length - 1 - sessions])!;
  const now = bars[bars.length - 1].close;
  return then > 0 ? (now / then - 1) * 100 : null;
}

/** Beta and correlation of bar returns against a benchmark over the last `window` shared bars. */
export function betaCorrelation(bars: Bar[], bench: Bar[], window = 120): { beta: number | null; correlation: number | null; n: number } {
  const benchClose = new Map(bench.map((b) => [b.timestamp, b.close]));
  const pairs: [number, number][] = [];
  for (let i = 1; i < bars.length; i++) {
    const a0 = benchClose.get(bars[i - 1].timestamp);
    const a1 = benchClose.get(bars[i].timestamp);
    if (!a0 || !a1 || !bars[i - 1].close) continue;
    if (IST_DAY.format(bars[i - 1].timestamp * 1000) !== IST_DAY.format(bars[i].timestamp * 1000)) continue;
    pairs.push([bars[i].close / bars[i - 1].close - 1, a1 / a0 - 1]);
  }
  const recent = pairs.slice(-window);
  if (recent.length < 30) return { beta: null, correlation: null, n: recent.length };
  const mx = recent.reduce((s, p) => s + p[0], 0) / recent.length;
  const my = recent.reduce((s, p) => s + p[1], 0) / recent.length;
  let cov = 0, vx = 0, vy = 0;
  for (const [x, y] of recent) { cov += (x - mx) * (y - my); vx += (x - mx) ** 2; vy += (y - my) ** 2; }
  return { beta: vy ? cov / vy : null, correlation: vx && vy ? cov / Math.sqrt(vx * vy) : null, n: recent.length };
}

/** Where the last close sits in the high-low range of the last `window` bars, 0..100. */
export function rangePosition(bars: Bar[], window = 20): number | null {
  const slice = bars.slice(-window);
  if (slice.length < window) return null;
  const high = Math.max(...slice.map((b) => b.high));
  const low = Math.min(...slice.map((b) => b.low));
  const last = slice[slice.length - 1].close;
  return high > low ? Math.round(((last - low) / (high - low)) * 100) : null;
}

/** Bars per year for annualising: 375 trading minutes a session, 250 sessions. */
export function barsPerYear(timeframeSeconds: number): number {
  return (375 * 60 / Math.max(60, timeframeSeconds)) * 250;
}
