/**
 * Minimal Black-Scholes gamma — used to recompute option gamma at hypothetical
 * spot levels for the gamma-progression curve (gamma is spot-dependent, so the
 * dealer GEX profile shifts as price moves). r defaults to 10% to match the
 * backend's NSE "exchange" greeks mode.
 */
const SQRT_2PI = Math.sqrt(2 * Math.PI);

export function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / SQRT_2PI;
}

/** Gamma is identical for calls and puts. S spot, K strike, T years, sigma vol (decimal). */
export function bsGamma(S: number, K: number, T: number, sigma: number, r = 0.1): number {
  if (S <= 0 || K <= 0 || T <= 0 || sigma <= 0) return 0;
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  return normPdf(d1) / (S * sigma * sqrtT);
}
