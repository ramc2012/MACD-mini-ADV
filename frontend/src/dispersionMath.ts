import type { Indicator, OptionWatchRow } from "./types";

const istDay = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
});

/** A complete candle is live context only for a short interval in its own IST session. */
export function dispersionIsCurrent(time: number | undefined, timeframe: number, now = Date.now()): boolean {
  if (time === undefined || !Number.isFinite(time) || !Number.isFinite(now)) return false;
  const age = now - time * 1000;
  return age >= 0 && age <= Math.max(2 * timeframe * 1000, 120_000)
    && istDay.format(time * 1000) === istDay.format(now);
}

export type DispersionPoint = {
  time: number;
  ceAbove: number;
  peAbove: number;
  ceEligible: number;
  peEligible: number;
  total: number;
  source?: "live" | "reconstructed";
};

function latest(rows: Indicator[] | undefined, fallback?: Indicator | null) {
  return rows?.[rows.length - 1] || fallback;
}

/** Count only the newest completed option-candle cohort.
 *
 * A dormant contract can retain yesterday's last MACD. Mixing that value with
 * today's breadth makes the headline precise-looking but false, so stale
 * contracts remain visible in coverage instead of entering either count.
 */
export function computeDispersion(
  options: OptionWatchRow[], indicators: Record<string, Indicator[]>,
): DispersionPoint | undefined {
  const atmOptions = options.filter((row) => (row.moneyness || "ATM") === "ATM");
  const rows = atmOptions.map((row) => ({ row, point: latest(indicators[row.symbol], row.indicator) }))
    .filter((item): item is { row: OptionWatchRow; point: Indicator } =>
      item.point != null && Number.isFinite(item.point.macd) && Number.isFinite(item.point.timestamp));
  if (!rows.length) return undefined;
  const time = Math.max(...rows.map((item) => item.point.timestamp));
  const fresh = rows.filter((item) => item.point.timestamp === time);
  const ce = fresh.filter((item) => item.row.option_type === "CE");
  const pe = fresh.filter((item) => item.row.option_type === "PE");
  return {
    time,
    ceAbove: ce.filter((item) => item.point.macd > 0).length,
    peAbove: pe.filter((item) => item.point.macd > 0).length,
    ceEligible: ce.length,
    peEligible: pe.length,
    total: atmOptions.length,
    source: "live",
  };
}
