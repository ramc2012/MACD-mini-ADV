/**
 * status-variants — the colour semantics of "state" words, in ONE place.
 *
 * ─── Why this module exists (2026-07-19, green-semantics correction) ─────────
 *
 * "Green" had come to mean four different things across the terminal: the loop
 * is armed, the lane is ready, the setup stage is ARMED, the data is live. Only
 * the last two of those are things a trader can act on; the first two say
 * "nothing is happening yet, but it would if the market were open". Painting
 * them the same colour is the false-green class Phase 0 set out to remove.
 *
 * THE CONTRACT — one sentence, and every surface derives from it:
 *
 *   GREEN is reserved for HEALTHY-LIVE or ACTIONABLE-CONFIRMED.
 *   ARMED / READY / WAITING is BLUE (info) — it is a promise, not a state.
 *   Anything degraded is amber, anything broken is red, anything unknown grey.
 *
 * Pure module: no React, no imports beyond types, so the contract is unit
 * testable on its own (`frontend-v2/tests/flow-provenance.test.ts`) and cannot
 * drift per-surface. `lib/market-semantics` re-exports everything here, so
 * every existing `@/lib/market-semantics` import keeps working unchanged.
 */
import type { BadgeVariant } from "./flow-provenance";

export type SchedulerState =
  | "running"
  | "armed"
  | "paused"
  | "disabled"
  | "parked"
  | "error"
  | "unknown";

const SCHEDULER_LABEL: Record<SchedulerState, string> = {
  running: "running",
  armed: "armed",
  paused: "paused",
  disabled: "disabled",
  parked: "parked",
  error: "loop error",
  unknown: "loop unknown",
};

const SCHEDULER_VARIANT: Record<SchedulerState, BadgeVariant> = {
  // The loop is actually turning — that IS a healthy-live fact.
  running: "success",
  // ARMED IS NOT RUNNING. A lane can be armed all weekend. Blue, never green.
  armed: "info",
  paused: "warn",
  disabled: "neutral",
  parked: "neutral",
  error: "error",
  unknown: "neutral",
};

export function schedulerStateLabel(s: SchedulerState): string {
  return SCHEDULER_LABEL[s];
}

export function schedulerStateVariant(s: SchedulerState): BadgeVariant {
  return SCHEDULER_VARIANT[s];
}

/**
 * Setup-lifecycle stages as the strategy lanes emit them.
 *
 * The same rule applies as for the scheduler: ARMED means "the structure is in
 * place and the lane is waiting" — it is not a trade and not a confirmation, so
 * it is blue. Only a stage that means the setup actually FIRED (or was
 * confirmed) earns green.
 */
const STAGE_VARIANT: Record<string, BadgeVariant> = {
  // Actionable-confirmed.
  TRIGGERED: "success",
  CONFIRMED: "success",
  ENTERED: "success",
  ACTIVE: "success",
  // Armed / waiting — a promise about the next bar, not a state to act on.
  ARMED: "info",
  READY: "info",
  WATCHING: "info",
  FORMING: "info",
  // Degraded / expired.
  MISSED_NO_CHASE: "warn",
  MISSED: "warn",
  STALE: "warn",
  // Inert.
  INVALIDATED: "neutral",
  EXPIRED: "neutral",
  FLAT: "neutral",
};

/** Badge variant for a lane's setup stage. Unknown stages are never green. */
export function setupStageVariant(stage?: string | null): BadgeVariant {
  const key = String(stage ?? "").trim().toUpperCase();
  if (!key) return "neutral";
  return STAGE_VARIANT[key] ?? "neutral";
}

/**
 * True when a variant makes a HEALTHY-LIVE / ACTIONABLE claim. Used by tests
 * (and available to any surface) to assert that a merely-armed state never
 * reaches for the success treatment.
 */
export function isActionableVariant(v: BadgeVariant): boolean {
  return v === "success";
}
