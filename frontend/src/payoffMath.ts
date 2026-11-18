// Strategy builder maths (owner, 18 Nov 2026): payoff at expiry, value before expiry and Greeks for a set of option
// legs, under European Black-Scholes on spot with r = 6.5% (the engine's option_greeks.py convention).
// Pure functions; the page is OptionsLab.tsx.

export const RATE = 0.065;
const YEAR_MS = 365 * 86_400_000;

export type Leg = {
  id: string;
  kind: "CE" | "PE" | "FUT";
  side: "BUY" | "SELL";
  strike: number;          // FUT: ignored
  lots: number;
  lotSize: number;
  price: number;           // entry premium (FUT: entry price)
  iv: number;              // implied vol in % (used before expiry)
  expiry: string;          // ISO date
  symbol?: string;
};

export type Greeks = { delta: number; gamma: number; theta: number; vega: number };

function cdf(x: number): number {
  // Abramowitz-Stegun 7.1.26 via erf; |error| < 1.5e-7.
  const t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-(x * x) / 2);
  return x >= 0 ? 0.5 * (1 + y) : 0.5 * (1 - y);
}

function pdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

/** Years from `now` to 15:30 IST on the expiry date (floored at zero). */
export function yearsTo(expiry: string, now: Date): number {
  const close = Date.parse(`${expiry}T15:30:00+05:30`);
  return Math.max(0, (close - now.getTime()) / YEAR_MS);
}

/** Black-Scholes price; at or past expiry it is the intrinsic value. */
export function bsPrice(spot: number, strike: number, years: number, vol: number, call: boolean): number {
  if (years <= 0 || vol <= 0) return Math.max(0, call ? spot - strike : strike - spot);
  const root = Math.sqrt(years);
  const d1 = (Math.log(spot / strike) + (RATE + vol * vol / 2) * years) / (vol * root);
  const d2 = d1 - vol * root;
  return call ? spot * cdf(d1) - strike * Math.exp(-RATE * years) * cdf(d2)
    : strike * Math.exp(-RATE * years) * cdf(-d2) - spot * cdf(-d1);
}

/** Per-unit Greeks: delta, gamma, theta per calendar day, vega per one IV point. */
export function bsGreeks(spot: number, strike: number, years: number, vol: number, call: boolean): Greeks {
  if (years <= 0 || vol <= 0) {
    const itm = call ? spot > strike : spot < strike;
    return { delta: itm ? (call ? 1 : -1) : 0, gamma: 0, theta: 0, vega: 0 };
  }
  const root = Math.sqrt(years);
  const d1 = (Math.log(spot / strike) + (RATE + vol * vol / 2) * years) / (vol * root);
  const d2 = d1 - vol * root;
  const decay = -spot * pdf(d1) * vol / (2 * root);
  const carry = RATE * strike * Math.exp(-RATE * years);
  const theta = call ? decay - carry * cdf(d2) : decay + carry * cdf(-d2);
  return { delta: call ? cdf(d1) : cdf(d1) - 1, gamma: pdf(d1) / (spot * vol * root), theta: theta / 365, vega: spot * pdf(d1) * root / 100 };
}

/** Bisection implied vol (in %) for a premium, or null below intrinsic. */
export function impliedVol(premium: number, spot: number, strike: number, years: number, call: boolean): number | null {
  if (!(premium > 0 && spot > 0 && strike > 0 && years > 0)) return null;
  if (premium <= bsPrice(spot, strike, years, 1e-6, call) + 1e-9) return null;
  let low = 0.001, high = 5;
  if (bsPrice(spot, strike, years, high, call) < premium) return null;
  for (let i = 0; i < 80; i++) {
    const mid = (low + high) / 2;
    if (bsPrice(spot, strike, years, mid, call) < premium) low = mid; else high = mid;
  }
  return ((low + high) / 2) * 100;
}

const sign = (leg: Leg) => (leg.side === "BUY" ? 1 : -1);
export const units = (leg: Leg) => leg.lots * leg.lotSize;

/** One leg's value per unit at `spot`, `years` before ITS expiry, with IV shifted by `ivShift` points. */
export function legValue(leg: Leg, spot: number, now: Date, ivShift = 0): number {
  if (leg.kind === "FUT") return spot;
  const years = yearsTo(leg.expiry, now);
  return bsPrice(spot, leg.strike, years, Math.max(0.01, leg.iv + ivShift) / 100, leg.kind === "CE");
}

/** Strategy P&L (Rs) at `spot` and moment `at` (today, a what-if date, or expiry), IV shifted by `ivShift` points. */
export function strategyPnl(legs: readonly Leg[], spot: number, at: Date, ivShift = 0): number {
  return legs.reduce((sum, leg) => sum + sign(leg) * units(leg) * (legValue(leg, spot, at, ivShift) - leg.price), 0);
}

/** P&L at each leg's own expiry (the nearest expiry when legs differ: later legs keep time value). */
export function expiryPnl(legs: readonly Leg[], spot: number): number {
  if (!legs.length) return 0;
  const first = legs.map((leg) => leg.expiry).sort()[0];
  return strategyPnl(legs, spot, new Date(Date.parse(`${first}T15:30:00+05:30`)));
}

export function strategyGreeks(legs: readonly Leg[], spot: number, at: Date, ivShift = 0): Greeks {
  const out = { delta: 0, gamma: 0, theta: 0, vega: 0 };
  for (const leg of legs) {
    const q = sign(leg) * units(leg);
    if (leg.kind === "FUT") { out.delta += q; continue; }
    const g = bsGreeks(spot, leg.strike, yearsTo(leg.expiry, at), Math.max(0.01, leg.iv + ivShift) / 100, leg.kind === "CE");
    out.delta += q * g.delta; out.gamma += q * g.gamma; out.theta += q * g.theta; out.vega += q * g.vega;
  }
  return out;
}

/** Premium paid (+) or received (-) to open the legs. */
export function netPremium(legs: readonly Leg[]): number {
  return legs.filter((leg) => leg.kind !== "FUT").reduce((sum, leg) => sum + sign(leg) * units(leg) * leg.price, 0);
}

/** Spot grid around `center` (+/- `widthPct` %), `n` points, plus every strike inside it. */
export function spotGrid(center: number, widthPct: number, n: number, strikes: readonly number[] = []): number[] {
  const lo = center * (1 - widthPct / 100), hi = center * (1 + widthPct / 100);
  const grid = Array.from({ length: n }, (_, i) => lo + ((hi - lo) * i) / (n - 1));
  return [...new Set([...grid, ...strikes.filter((k) => k > lo && k < hi)])].sort((a, b) => a - b);
}

/** Spots where the expiry P&L crosses zero (linear interpolation on the grid). */
export function breakevens(points: readonly { spot: number; pnl: number }[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    if (a.pnl === 0) out.push(a.spot);
    else if ((a.pnl < 0 && b.pnl > 0) || (a.pnl > 0 && b.pnl < 0)) out.push(a.spot + ((0 - a.pnl) * (b.spot - a.spot)) / (b.pnl - a.pnl));
  }
  return out;
}

/** Max profit / loss at expiry over the grid; "unlimited" when the edge of a wide grid is still sloping outward. */
export function extremes(legs: readonly Leg[], spot: number): { maxProfit: number | null; maxLoss: number | null } {
  if (!legs.length) return { maxProfit: 0, maxLoss: 0 };
  const grid = spotGrid(spot, 95, 400, legs.map((leg) => leg.strike));
  const values = grid.map((s) => expiryPnl(legs, s));
  const far = expiryPnl(legs, spot * 3), edge = values[values.length - 1];
  const upSlope = far - edge;
  let maxProfit: number | null = Math.max(...values), maxLoss: number | null = Math.min(...values);
  if (upSlope > 1) maxProfit = null;           // keeps rising with spot
  if (upSlope < -1) maxLoss = null;            // keeps falling with spot
  return { maxProfit, maxLoss };
}

/**
 * Rough margin to hold the legs (NOT the exchange's SPAN): bought options need their premium; each sold option or
 * future is charged `shortPct` % of its contract value, less the premium received, floored at a tenth of it; a sold
 * option covered by a bought one of the same type and expiry (a spread) is charged its maximum loss instead (nothing
 * more for a debit spread).
 */
export function roughMargin(legs: readonly Leg[], spot: number, shortPct: number): number {
  let margin = 0;
  const bought = legs.filter((leg) => leg.side === "BUY" && leg.kind !== "FUT");
  const covered = new Set<string>();
  for (const leg of legs) {
    if (leg.kind === "FUT") { margin += (units(leg) * spot * shortPct) / 100; continue; }
    if (leg.side === "BUY") { margin += units(leg) * leg.price; continue; }
    const hedge = bought.find((b) => !covered.has(b.id) && b.kind === leg.kind && b.expiry === leg.expiry && units(b) >= units(leg));
    if (hedge) {
      covered.add(hedge.id);
      // Debit spread (bought strike nearer the money): the premium received offsets the debit. Credit spread: the
      // width at risk less the premium received (the bought leg's premium is already counted above).
      const debit = leg.kind === "CE" ? hedge.strike <= leg.strike : hedge.strike >= leg.strike;
      margin += debit ? -units(leg) * leg.price : Math.max(0, Math.abs(hedge.strike - leg.strike) * units(leg) - units(leg) * leg.price);
      continue;
    }
    const value = units(leg) * spot;
    margin += Math.max((value * shortPct) / 100 - units(leg) * leg.price, value * shortPct / 1000);
  }
  return Math.max(0, margin);
}

/** Ready-made strategies around the ATM strike `atm` with strike step `step`. */
export const TEMPLATES: Record<string, { label: string; legs: { kind: "CE" | "PE"; side: "BUY" | "SELL"; offset: number }[] }> = {
  long_call: { label: "Long call", legs: [{ kind: "CE", side: "BUY", offset: 0 }] },
  long_put: { label: "Long put", legs: [{ kind: "PE", side: "BUY", offset: 0 }] },
  bull_call: { label: "Bull call spread", legs: [{ kind: "CE", side: "BUY", offset: 0 }, { kind: "CE", side: "SELL", offset: 2 }] },
  bear_put: { label: "Bear put spread", legs: [{ kind: "PE", side: "BUY", offset: 0 }, { kind: "PE", side: "SELL", offset: -2 }] },
  long_straddle: { label: "Long straddle", legs: [{ kind: "CE", side: "BUY", offset: 0 }, { kind: "PE", side: "BUY", offset: 0 }] },
  short_straddle: { label: "Short straddle", legs: [{ kind: "CE", side: "SELL", offset: 0 }, { kind: "PE", side: "SELL", offset: 0 }] },
  long_strangle: { label: "Long strangle", legs: [{ kind: "CE", side: "BUY", offset: 2 }, { kind: "PE", side: "BUY", offset: -2 }] },
  iron_condor: { label: "Iron condor", legs: [{ kind: "PE", side: "BUY", offset: -4 }, { kind: "PE", side: "SELL", offset: -2 }, { kind: "CE", side: "SELL", offset: 2 }, { kind: "CE", side: "BUY", offset: 4 }] },
  iron_fly: { label: "Iron butterfly", legs: [{ kind: "PE", side: "BUY", offset: -3 }, { kind: "PE", side: "SELL", offset: 0 }, { kind: "CE", side: "SELL", offset: 0 }, { kind: "CE", side: "BUY", offset: 3 }] },
};
