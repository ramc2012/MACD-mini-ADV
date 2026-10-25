/**
 * Standard refresh cadences. The v1 frontend had 7 different intervals
 * for the same data class — live snapshot was 20s on AI/FMP/DO, 90s on
 * MP, 60s on Orderflow, 120s on Charts. Pin them here so every desk
 * agrees on what "live" vs "summary" vs "slow" means.
 */
export const REFRESH_MS = {
  /** Per-bar / live snapshot. Anything tied to the current market bar. */
  live: 15_000,
  /** Snapshots that change at the cadence of paper-engine cycles (~30s). */
  snapshot: 30_000,
  /** Aggregations / summaries / coverage stats. */
  summary: 60_000,
  /** Background diagnostics — backtests, walk-forwards, dataset stats. */
  slow: 300_000,
} as const;
