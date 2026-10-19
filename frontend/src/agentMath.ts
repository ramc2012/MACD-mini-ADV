// Formatting for the option agent's page: feature names a trader can read,
// signed percentages, and the basket curve's path.

const FEATURE_LABELS: Record<string, string> = {
  u_ret_intraday: "underlying today",
  u_ret_1d: "underlying 1D",
  u_ret_5d: "underlying 5D",
  u_ret_20d: "underlying 20D",
  u_vwap_dev: "vs VWAP",
  u_day_range_pos: "day-range position",
  u_range20_pos: "20D-range position",
  u_macd_hist: "30m MACD hist",
  u_rs_5d: "5D vs NIFTY",
  u_value_pos: "vs prior value area",
  u_spike_net: "volume-spike balance",
  u_rv_ratio: "realised-vol regime",
  o_moneyness: "moneyness",
  o_premium_pct: "premium / spot",
  o_dte: "days to expiry",
  o_ret_intraday: "premium today",
  o_ret_1d: "premium 1D",
  o_vol_surge: "volume surge",
  o_leverage: "leverage",
  is_index: "index",
};

export function featureLabel(name: string): string {
  return FEATURE_LABELS[name] || name;
}

/** "+1.23%" from a fraction; "—" when missing. */
export function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const v = value * 100;
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/** Scorecard votes and predicted returns use different units. */
export function indexPrediction(kind: string | undefined, value: number | null | undefined, conviction?: number | null) {
  if (kind === "scorecard") {
    const score = value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(0)}`;
    return { label: "Net score", value: score, detail: "conditions; trade requires at least 2 net votes" };
  }
  return { label: "Expected 1–3d", value: pct(value), detail: `edge over other side ${pct(conviction)}` };
}

export function tone(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value === 0) return "";
  return value > 0 ? "positive" : "negative";
}

/** The two strongest reasons, signed: "+ underlying 1D, − premium today". */
export function driverText(drivers: [string, number][] | undefined, count = 2): string {
  return (drivers || []).slice(0, count).map(([name, value]) => `${value >= 0 ? "+" : "−"} ${featureLabel(name)}`).join(", ");
}

/** SVG path for a curve of levels in a width x height box (y inverted). */
export function curvePath(levels: number[], width: number, height: number): string {
  if (levels.length < 2) return "";
  const lo = Math.min(...levels, 1);
  const hi = Math.max(...levels, 1);
  const span = hi - lo || 1;
  return levels.map((level, i) => {
    const x = (i / (levels.length - 1)) * width;
    const y = height - ((level - lo) / span) * height;
    return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

/** "₹-5.43 L" / "₹98.0k" / "₹640": compact signed rupees for P&L. */
export function rupees(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value < 0 ? "-" : value > 0 ? "+" : "";
  const abs = Math.abs(value);
  if (abs >= 100_000) return `${sign}₹${(abs / 100_000).toFixed(2)} L`;
  if (abs >= 1_000) return `${sign}₹${(abs / 1_000).toFixed(1)}k`;
  return `${sign}₹${abs.toFixed(0)}`;
}

/** Live mark against the entry: points, fraction, and rupees for one lot (null where unknown). */
export function sinceEntry(entry: number | null | undefined, ltp: number | null | undefined, lotSize?: number | null) {
  if (entry == null || ltp == null || !Number.isFinite(entry) || !Number.isFinite(ltp) || entry <= 0 || ltp <= 0) {
    return { points: null, fraction: null, perLot: null };
  }
  const points = ltp - entry;
  return { points, fraction: points / entry, perLot: lotSize && lotSize > 0 ? points * lotSize : null };
}
