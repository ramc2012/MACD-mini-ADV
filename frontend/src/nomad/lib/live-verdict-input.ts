/**
 * live-verdict-input — the ONE derivation of the live verdict's DATA half.
 *
 * ─── Why this module exists (2026-07-19, P0 header-verdict correction) ───────
 *
 * `liveVerdict()` already decides whether any surface may say "live". But the
 * market-structure header was calling it with `freshness: "absent"` and
 * `hasSymbolObservation: false` HARD-CODED, so the header could only ever say
 * "no observation" — on a Monday, with the session open, the feed connected and
 * fresh rows in the matrix. A permanently-dead verdict is as dishonest as a
 * permanently-green one: it is a claim about the data that the data does not
 * support.
 *
 * The fix is not "pass something better at the call site" — it is to make the
 * header and the matrix row read the SAME derived observation. This module is
 * that shared derivation:
 *
 *   pinnedObservationOf(row)   ← the selected matrix row, already decorated by
 *                                `decorateRows` (the same object whose freshness
 *                                and sufficiency the Readiness cell renders)
 *   liveVerdictInputFor(...)   ← that observation + the shared system facts
 *
 * Because both surfaces consume one decorated row array, the header and the row
 * cannot disagree: there is no second freshness computation to drift.
 *
 * Pure module: only TYPE imports, so it is unit-testable with no React, no
 * fetching and no path-alias resolution (see tests/flow-provenance.test.ts).
 */
import type { BadgeVariant } from "./flow-provenance";
import type { DataMode, Freshness } from "./market-semantics";

// ─── The verdict itself ─────────────────────────────────────────────────────

export type LiveVerdictInput = {
  sessionOpen: boolean;
  feedOnline: boolean;
  transportConnected?: boolean;
  dataMode?: DataMode;
  freshness?: Freshness;
  /** Is there an actual symbol-level observation in hand (not just a socket)? */
  hasSymbolObservation?: boolean;
};

export type LiveVerdict = {
  live: boolean;
  label: string;
  variant: BadgeVariant;
  reason: string;
};

/**
 * ONE function decides whether any surface may say "live". Precedence is the
 * invariant established by LiveMarkBadge and is deliberately pessimistic:
 * market closed → feed offline → tape offline → no observation → replay →
 * stale → live. Nothing else in the app may short-circuit it.
 *
 * Moved here (from `market-semantics`) so the verdict and the derivation of its
 * DATA half live in one dependency-free module that the honesty tests can
 * execute directly. `market-semantics` re-exports it, so every caller is
 * unchanged.
 */
export function liveVerdict(input: LiveVerdictInput): LiveVerdict {
  const {
    sessionOpen,
    feedOnline,
    transportConnected = true,
    dataMode = "unknown",
    freshness = "absent",
    hasSymbolObservation = true,
  } = input;

  if (!sessionOpen) {
    return { live: false, label: "market closed", variant: "neutral", reason: "session closed" };
  }
  if (!feedOnline) {
    return { live: false, label: "feed offline", variant: "warn", reason: "feed offline" };
  }
  if (!transportConnected) {
    return { live: false, label: "tape offline", variant: "warn", reason: "transport down" };
  }
  if (!hasSymbolObservation || freshness === "absent") {
    return { live: false, label: "no observation", variant: "neutral", reason: "no symbol-level data" };
  }
  if (dataMode === "historical_replay") {
    return { live: false, label: "replay", variant: "warn", reason: "historical replay" };
  }
  if (dataMode === "bar_inference") {
    return { live: false, label: "bar-inferred", variant: "warn", reason: "inferred from bars" };
  }
  if (freshness === "stale") {
    return { live: false, label: "stale", variant: "warn", reason: "observation is stale" };
  }
  return { live: true, label: "● live data", variant: "success", reason: "fresh observed data" };
}

/** The minimum shape of a decorated matrix row this module needs. */
export type ObservedRowLike = {
  freshness?: Freshness | null;
  /** ISO instant of the instrument's OWN observation, verbatim from the lane. */
  asOf?: string | null;
  provenance?: { dataMode?: DataMode | null } | null;
} | null | undefined;

/** What the workspace actually knows about the pinned instrument. */
export type PinnedObservation = {
  freshness: Freshness;
  dataMode: DataMode;
  /**
   * True only when a per-symbol observation genuinely exists: a timestamp the
   * lane reported AND a freshness verdict derived from it. A row that is merely
   * present in the universe (no `as_of`) is NOT an observation.
   */
  present: boolean;
};

export const NO_OBSERVATION: PinnedObservation = {
  freshness: "absent",
  dataMode: "unknown",
  present: false,
};

/**
 * Derive the pinned instrument's observation from its decorated matrix row.
 * Never optimistic: a missing row, a missing timestamp or an `absent` freshness
 * all collapse to "no observation".
 */
export function pinnedObservationOf(row: ObservedRowLike): PinnedObservation {
  if (!row) return NO_OBSERVATION;
  const freshness: Freshness = row.freshness ?? "absent";
  const hasTimestamp = row.asOf != null && String(row.asOf).trim() !== "";
  if (!hasTimestamp || freshness === "absent") return NO_OBSERVATION;
  return {
    freshness,
    dataMode: row.provenance?.dataMode ?? "unknown",
    present: true,
  };
}

/**
 * Compose the `liveVerdict()` input from the shared system facts plus the
 * pinned observation. No caller may hard-code the data half again — that is
 * exactly the defect this replaces.
 */
export function liveVerdictInputFor(args: {
  sessionOpen: boolean;
  feedOnline: boolean;
  transportConnected?: boolean;
  observation: PinnedObservation;
}): LiveVerdictInput {
  return {
    sessionOpen: args.sessionOpen,
    feedOnline: args.feedOnline,
    transportConnected: args.transportConnected ?? true,
    dataMode: args.observation.dataMode,
    freshness: args.observation.freshness,
    hasSymbolObservation: args.observation.present,
  };
}
