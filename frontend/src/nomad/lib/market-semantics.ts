/**
 * market-semantics — THE terminal's shared vocabulary.
 *
 * Every desk used to roll its own answer to "is this live?", "where did this
 * number come from?", "is this plan complete?". The answers diverged: one desk
 * called a websocket transport "live", another called a backfill-append flag
 * "live", a third called a successful HTTP 200 "live". This module is the ONE
 * place those questions are answered, so all surfaces derive them identically.
 *
 * Six orthogonal facts — never collapse them into one green light:
 *
 *   executionMode   paper | live | parked        — what happens to an order
 *   schedulerState  running | armed | paused | … — whether the loop will fire
 *   dataMode        live | historical_replay |   — what the numbers describe
 *                   bar_inference
 *   sourceGrade     observed | reconstructed |   — how the numbers were obtained
 *                   modelled_from_quotes |
 *                   modelled | bar_inferred | unavailable
 *   freshness       fresh | stale | absent       — when they were obtained
 *   sufficiency     ok | degraded | insufficient — whether they can be acted on
 *
 * Pure module: no React, no hooks, no fetching. Renderers live in
 * `components/desk-ui/SemanticBadges.tsx`; the live system facts (session
 * clock, feed health, brokers, auto-run) live in `hooks/useSystemState` and
 * `lib/market-hours`, which this module consumes rather than duplicates.
 */

import { freshnessTone } from "@/components/common/LastUpdated";
import { toDate } from "@/components/desk-ui/formatters";
import {
  NO_AGGRESSOR_TAPE_NOTE,
  classifySourceGrade,
  classifyStorageMode,
  normalizeSource,
  sourceGradeLabel,
  storageModeLabel,
  type BadgeVariant,
  type MarketFeature,
  type SourceGrade,
  type StorageMode,
} from "./flow-provenance";
import type { SchedulerState } from "./status-variants";

/**
 * Source grading lives in `./flow-provenance` (pure, dependency-free, unit
 * tested) because it is FEATURE-AWARE as of 2026-07-19: the quote/bar stream
 * grades `observed`, but any buy/sell-ATTRIBUTED number built on it grades
 * `modelled_from_quotes` — `backend/analytics/orderflow.py` states outright
 * that no Indian retail broker pushes aggressor-tagged trade prints, so CVD /
 * footprint sides / delta / aggression / absorption are approximations from
 * OHLCV bars + L1 snapshots, never measurements. Re-exported here so every
 * existing `@/lib/market-semantics` import keeps working unchanged.
 */
export {
  classifyAcquisitionSource,
  classifySourceGrade,
  classifyFlowGrade,
  classifyOfSource,
  classifyStorageMode,
  describeStorageMode,
  storageModeLabel,
  storageModeVariant,
  describeFlowDerivation,
  isFabricatedGrade,
  isInferredSideGrade,
  normalizeSource,
  sourceGradeLabel,
  sourceGradeVariant,
  isCapabilityMissing,
  missingCapability,
  AGGRESSOR_TAPE_AVAILABLE,
  FLOW_ATTRIBUTION_FEATURES,
  MISSING_CAPABILITIES,
  MISSING_CAPABILITY_KEYS,
  NO_AGGRESSOR_TAPE_NOTE,
} from "./flow-provenance";
export type {
  AcquisitionSource,
  BadgeVariant,
  CapabilityKey,
  MarketFeature,
  MissingCapability,
  OfSourceClass,
  OfSourceKind,
  SourceGrade,
  StorageMode,
} from "./flow-provenance";

/**
 * The colour semantics of state words (scheduler + setup stage) live in
 * `./status-variants` — pure and unit-tested — because "armed is not green" is
 * a contract that must be derived ONCE and not re-decided per surface.
 */
export {
  isActionableVariant,
  schedulerStateLabel,
  schedulerStateVariant,
  setupStageVariant,
} from "./status-variants";
export type { SchedulerState } from "./status-variants";

/**
 * The live verdict and the derivation of its DATA half live in
 * `./live-verdict-input` (pure, unit-tested). Re-exported so every existing
 * `@/lib/market-semantics` import keeps working unchanged.
 */
export {
  NO_OBSERVATION,
  liveVerdict,
  liveVerdictInputFor,
  pinnedObservationOf,
} from "./live-verdict-input";
export type {
  LiveVerdict,
  LiveVerdictInput,
  ObservedRowLike,
  PinnedObservation,
} from "./live-verdict-input";

// ─── Vocabulary ─────────────────────────────────────────────────────────────

export type ExecutionMode = "paper" | "live" | "parked" | "none";
export type DataMode = "live" | "historical_replay" | "bar_inference" | "unknown";
export type Freshness = "fresh" | "stale" | "absent";
export type Sufficiency = "ok" | "degraded" | "insufficient";

export type Completeness = {
  have: number | null;
  expect: number | null;
  label: string | null;
};

export type Provenance = {
  /** Raw backend source string, kept verbatim so nothing is laundered. */
  source: string | null;
  grade: SourceGrade;
  /**
   * Axis (ii) — how this read reached us (live path / stored snapshot /
   * backfill). Deliberately SEPARATE from `grade`: a snapshot read says nothing
   * about how the numbers in it were derived, and must never be rendered as if
   * it did.
   */
  storageMode: StorageMode;
  /** ISO timestamp of the observation (naive = UTC, repo convention). */
  asOf: string | null;
  ageSeconds: number | null;
  freshness: Freshness;
  /** Aggregation window: "1minute" / "3m" / "30minute" / "daily". */
  timeframe: string | null;
  completeness: Completeness;
  dataMode: DataMode;
  sufficiency: Sufficiency;
  /** degraded_reason, blocked_reasons, "no stop", … — always human-readable. */
  reasons: string[];
};

// ─── Data mode ──────────────────────────────────────────────────────────────

/**
 * Shape of the `data_status` blocks the backend actually emits. Auction's
 * live-snapshot and directional's snapshot.data_status differ in field names;
 * both are accepted here so callers pass their payload fragment verbatim.
 */
export type DataStatusLike = {
  live_mode?: boolean | null;
  live_appended?: boolean | null;
  snapshot_mode?: string | null;
  readiness_mode?: string | null;
  quote_source?: string | null;
  order_flow_source?: string | null;
  history_source?: string | null;
  execution_ready?: boolean | null;
  degraded_reason?: string | null;
  stale_data_seconds?: number | null;
  minute_history_age_seconds?: number | null;
  spot_age_seconds?: number | null;
  watchlist_age_seconds?: number | null;
  watchlist_rows_today?: number | null;
  watchlist_rows_latest?: number | null;
  tick_ready?: boolean | null;
  quote_ready?: boolean | null;
} | null | undefined;

/**
 * What do these numbers describe — the live auction, a replayed session, or a
 * fabrication from bars? Never returns `live` on the strength of a transport.
 */
export function classifyDataMode(ds: DataStatusLike): DataMode {
  if (!ds) return "unknown";
  const snapshotMode = normalizeSource(ds.snapshot_mode);
  const readinessMode = normalizeSource(ds.readiness_mode);
  if (
    snapshotMode === "historical_replay" ||
    snapshotMode === "replay" ||
    readinessMode === "latest_session" ||
    ds.live_mode === false
  ) {
    return "historical_replay";
  }
  const flowGrade = classifySourceGrade(ds.order_flow_source);
  const quoteGrade = classifySourceGrade(ds.quote_source);
  if (flowGrade === "bar_inferred" || quoteGrade === "bar_inferred") return "bar_inference";
  if (ds.live_mode === true) return "live";
  if (snapshotMode === "live") return "live";
  return "unknown";
}

const DATA_MODE_LABEL: Record<DataMode, string> = {
  live: "live data",
  historical_replay: "replay",
  bar_inference: "bar-inferred",
  unknown: "data mode unknown",
};

const DATA_MODE_VARIANT: Record<DataMode, BadgeVariant> = {
  live: "success",
  historical_replay: "warn",
  bar_inference: "warn",
  unknown: "neutral",
};

export function dataModeLabel(mode: DataMode): string {
  return DATA_MODE_LABEL[mode];
}

export function dataModeVariant(mode: DataMode): BadgeVariant {
  return DATA_MODE_VARIANT[mode];
}

// ─── Freshness ──────────────────────────────────────────────────────────────

export type FreshnessVerdict = {
  freshness: Freshness;
  ageSeconds: number | null;
  asOf: string | null;
};

export function ageSecondsOf(
  asOf: string | number | Date | null | undefined,
  nowMs: number = Date.now(),
): number | null {
  if (asOf == null || asOf === "") return null;
  const d = toDate(asOf);
  if (!d || Number.isNaN(d.getTime())) return null;
  return Math.max(0, (nowMs - d.getTime()) / 1000);
}

/**
 * Freshness verdict from a timestamp. Thresholds delegate to the SAME cutoffs
 * `LastUpdated` paints with (120s green → amber, 600s amber → red) so a badge
 * and its dot can never disagree.
 */
export function deriveFreshness(
  asOf: string | number | Date | null | undefined,
  opts: { nowMs?: number; staleAfterSeconds?: number; criticalAfterSeconds?: number } = {},
): FreshnessVerdict {
  const { nowMs = Date.now(), staleAfterSeconds = 120, criticalAfterSeconds = 600 } = opts;
  const ageSeconds = ageSecondsOf(asOf, nowMs);
  const tone = freshnessTone(ageSeconds, staleAfterSeconds, criticalAfterSeconds);
  const freshness: Freshness =
    tone === "none" ? "absent" : tone === "fresh" ? "fresh" : "stale";
  const iso =
    asOf == null || asOf === ""
      ? null
      : (() => {
          const d = toDate(asOf);
          return d && !Number.isNaN(d.getTime()) ? d.toISOString() : null;
        })();
  return { freshness, ageSeconds, asOf: iso };
}

const FRESHNESS_VARIANT: Record<Freshness, BadgeVariant> = {
  fresh: "success",
  stale: "warn",
  absent: "neutral",
};

export function freshnessVariant(f: Freshness): BadgeVariant {
  return FRESHNESS_VARIANT[f];
}

export function formatAgeShort(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

// ─── Execution mode ─────────────────────────────────────────────────────────

export type ModeLike = {
  mode?: string | null;
  paper_trading?: boolean | null;
  paper_only?: boolean | null;
  live?: boolean | null;
  execution_mode?: string | null;
} | null | undefined;

/** Lifted verbatim from useSystemState so the truth strip and desks agree. */
export function deriveExecutionMode(m: ModeLike): ExecutionMode {
  if (!m) return "none";
  const explicit = normalizeSource(m.execution_mode || m.mode);
  if (explicit === "parked") return "parked";
  if (explicit === "live") return "live";
  if (explicit === "paper") return "paper";
  if (m.live === true) return "live";
  if (m.paper_trading === false) return "live";
  if (m.paper_trading === true || m.paper_only === true) return "paper";
  return "none";
}

const EXECUTION_MODE_VARIANT: Record<ExecutionMode, BadgeVariant> = {
  paper: "info",
  live: "warn",
  parked: "neutral",
  none: "neutral",
};

export function executionModeVariant(m: ExecutionMode): BadgeVariant {
  return EXECUTION_MODE_VARIANT[m];
}

// ─── Scheduler state ────────────────────────────────────────────────────────

export type AutomationLike = {
  enabled?: boolean | null;
  loop_active?: boolean | null;
  running?: boolean | null;
  stale?: boolean | null;
  next_run_at?: string | null;
  last_error?: string | null;
  last_message?: string | null;
  execution_mode?: string | null;
} | null | undefined;

/**
 * ARMED IS NOT LIVE. A lane can be armed all weekend; that says the loop will
 * fire next session, not that anything is running or that data is flowing.
 */
export function deriveSchedulerState(a: AutomationLike): SchedulerState {
  if (!a) return "unknown";
  if (normalizeSource(a.execution_mode) === "parked") return "parked";
  if (a.last_error) return "error";
  if (a.running === true) return "running";
  if (a.enabled === false) return "disabled";
  if (a.enabled && a.loop_active && a.next_run_at) return "armed";
  if (a.enabled && a.loop_active === false) return "paused";
  if (a.enabled) return "armed";
  return "unknown";
}

/**
 * Labels and variants for `SchedulerState` live in `./status-variants` and are
 * re-exported above. ARMED IS NOT LIVE — and, since 2026-07-19, armed is not
 * GREEN either: green is reserved for healthy-live / actionable-confirmed.
 */

// ─── Sufficiency ────────────────────────────────────────────────────────────

export type SufficiencyInput = {
  grade?: SourceGrade;
  freshness?: Freshness;
  dataMode?: DataMode;
  executionReady?: boolean | null;
  degradedReason?: string | null;
  blockedReasons?: string[] | null;
  tickAgeMs?: number | null;
  tickLimitMs?: number | null;
  rowsToday?: number | null;
  rowsLatest?: number | null;
};

export type SufficiencyVerdict = { sufficiency: Sufficiency; reasons: string[] };

/**
 * Can this be acted on? `insufficient` = there is no usable observation at all;
 * `degraded` = there is one but it is inferred, stale-by-contract or the lane
 * itself said execution is not ready.
 */
export function deriveSufficiency(input: SufficiencyInput): SufficiencyVerdict {
  const reasons: string[] = [];
  const grade = input.grade ?? "unavailable";
  const freshness = input.freshness ?? "absent";

  const noObservation =
    grade === "unavailable" ||
    freshness === "absent" ||
    (input.rowsToday === 0 && (input.rowsLatest == null || input.rowsLatest === 0));

  if (grade === "unavailable") reasons.push("source not reported");
  if (freshness === "absent") reasons.push("no timestamp");
  if (input.rowsToday === 0 && (input.rowsLatest == null || input.rowsLatest === 0)) {
    reasons.push("no rows");
  }
  if (noObservation) return { sufficiency: "insufficient", reasons };

  if (input.executionReady === false) reasons.push("execution not ready");
  if (input.degradedReason) reasons.push(String(input.degradedReason));
  for (const r of input.blockedReasons ?? []) if (r) reasons.push(String(r));
  if (grade === "bar_inferred") reasons.push("inferred from bars, not a tape");
  // A stored snapshot IS a caveat (it is not the live path) but it is NOT an
  // accusation of bar inference — the reason names what is actually unknown.
  if (grade === "unknown_derivation") {
    reasons.push("stored snapshot — the payload does not report how it was derived");
  }
  // NOTE: `modelled_from_quotes` deliberately does NOT add a sufficiency
  // reason. Sufficiency answers "can this be acted on", and every flow lane
  // acts on quote-derived sides BY DESIGN — that is the lane's contract, not a
  // degradation. The derivation is stated in the badge/caption instead, so
  // this correction relabels without silently amber-ing every desk.
  if (input.dataMode === "historical_replay") reasons.push("replayed session");
  if (freshness === "stale") reasons.push("observation is stale");
  if (
    input.tickAgeMs != null &&
    input.tickLimitMs != null &&
    Number.isFinite(input.tickAgeMs) &&
    Number.isFinite(input.tickLimitMs) &&
    input.tickAgeMs > input.tickLimitMs
  ) {
    reasons.push(
      `tick age ${Math.round(input.tickAgeMs / 1000)}s over ${Math.round(input.tickLimitMs / 1000)}s limit`,
    );
  }

  return { sufficiency: reasons.length ? "degraded" : "ok", reasons };
}

const SUFFICIENCY_LABEL: Record<Sufficiency, string> = {
  ok: "sufficient",
  degraded: "degraded",
  insufficient: "insufficient",
};

const SUFFICIENCY_VARIANT: Record<Sufficiency, BadgeVariant> = {
  ok: "success",
  degraded: "warn",
  insufficient: "error",
};

export function sufficiencyLabel(s: Sufficiency): string {
  return SUFFICIENCY_LABEL[s];
}

export function sufficiencyVariant(s: Sufficiency): BadgeVariant {
  return SUFFICIENCY_VARIANT[s];
}

// ─── Provenance composer ────────────────────────────────────────────────────

export type ProvenanceInput = {
  source?: string | null;
  /**
   * What KIND of number this provenance describes. Defaults to `"quote"`, i.e.
   * the pre-2026-07-19 source-only grading, so every existing caller is
   * unchanged. Flow surfaces (CVD, footprint, delta, aggression, absorption)
   * MUST pass `"flow_attribution"` — the buy/sell split is inferred from
   * quotes, never observed (`backend/analytics/orderflow.py`).
   */
  feature?: MarketFeature;
  asOf?: string | number | Date | null;
  timeframe?: string | null;
  have?: number | null;
  expect?: number | null;
  completenessLabel?: string | null;
  dataStatus?: DataStatusLike;
  dataMode?: DataMode;
  executionReady?: boolean | null;
  degradedReason?: string | null;
  blockedReasons?: string[] | null;
  tickAgeMs?: number | null;
  tickLimitMs?: number | null;
  nowMs?: number;
  staleAfterSeconds?: number;
  criticalAfterSeconds?: number;
};

/** The single composer. Every panel's provenance affordance goes through it. */
export function provenanceOf(input: ProvenanceInput): Provenance {
  const ds = input.dataStatus;
  const source = input.source ?? ds?.order_flow_source ?? ds?.quote_source ?? ds?.history_source ?? null;
  const feature: MarketFeature = input.feature ?? "quote";
  const grade = classifySourceGrade(source, feature);
  const storageMode = classifyStorageMode(source);
  const { freshness, ageSeconds, asOf } = deriveFreshness(input.asOf, {
    nowMs: input.nowMs,
    staleAfterSeconds: input.staleAfterSeconds,
    criticalAfterSeconds: input.criticalAfterSeconds,
  });
  const dataMode = input.dataMode ?? classifyDataMode(ds);
  const { sufficiency, reasons } = deriveSufficiency({
    grade,
    freshness,
    dataMode,
    executionReady: input.executionReady ?? ds?.execution_ready ?? null,
    degradedReason: input.degradedReason ?? ds?.degraded_reason ?? null,
    blockedReasons: input.blockedReasons ?? null,
    tickAgeMs: input.tickAgeMs ?? null,
    tickLimitMs: input.tickLimitMs ?? null,
    rowsToday: ds?.watchlist_rows_today ?? null,
    rowsLatest: ds?.watchlist_rows_latest ?? null,
  });

  return {
    source: source == null || source === "" ? null : String(source),
    grade,
    storageMode,
    asOf,
    ageSeconds,
    freshness,
    timeframe: input.timeframe ?? null,
    completeness: {
      have: input.have ?? null,
      expect: input.expect ?? null,
      label: input.completenessLabel ?? null,
    },
    dataMode,
    sufficiency,
    reasons,
  };
}

/** Compact one-line rendering used by the provenance caption. */
export function describeProvenance(p: Provenance): string {
  const parts: string[] = [];
  parts.push(p.source ?? "source not reported");
  parts.push(sourceGradeLabel(p.grade).toLowerCase());
  // Storage mode is its OWN axis and is stated separately, never folded into
  // the derivation grade.
  if (p.storageMode === "snapshot" || p.storageMode === "backfilled") {
    parts.push(storageModeLabel(p.storageMode));
  }
  if (p.timeframe) parts.push(p.timeframe);
  if (p.completeness.label) parts.push(p.completeness.label);
  else if (p.completeness.have != null) {
    parts.push(
      p.completeness.expect != null
        ? `${p.completeness.have}/${p.completeness.expect}`
        : `${p.completeness.have}`,
    );
  }
  parts.push(
    p.freshness === "absent" ? "no timestamp" : `${formatAgeShort(p.ageSeconds)} old`,
  );
  if (p.dataMode !== "live") parts.push(dataModeLabel(p.dataMode));
  if (p.grade === "modelled_from_quotes") parts.push(NO_AGGRESSOR_TAPE_NOTE);
  return parts.join(" · ");
}

// ─── The live verdict ───────────────────────────────────────────────────────

/**
 * `liveVerdict`, `LiveVerdictInput`, `pinnedObservationOf` and
 * `liveVerdictInputFor` now live in `./live-verdict-input` (pure module,
 * unit-tested) and are re-exported at the top of this file. They moved because
 * the header of the market-structure workspace was hard-coding the DATA half of
 * the input (`freshness: "absent"`, `hasSymbolObservation: false`), which made
 * the verdict permanently dead; the derivation is now shared with the matrix
 * row so the two surfaces cannot disagree.
 */

// ─── R/R honesty ────────────────────────────────────────────────────────────

export type RiskPlanLike = {
  entry?: number | null;
  stop?: number | null;
  target1?: number | null;
  reward_risk?: number | null;
  r_multiple?: number | null;
} | null | undefined;

export type RrVerdict =
  | { ok: true; value: number; approx: boolean; text: string; missing: []; note?: string }
  | { ok: false; value: null; approx: false; text: string; missing: string[]; reason: string };

/**
 * Missing-preserving numeric coercion.
 *
 * `Number(null)` is 0 and `Number("")` is 0, so a plain Number()+isFinite check
 * silently promotes an ABSENT field to a measured zero. On the live convergence
 * payload (`{entry: 58565.15, stop: null, target1: null}`) that turned the R/R
 * suppression into a fabricated `1.00R` — the exact class of claim this module
 * exists to prevent. Null, undefined and empty string are missing, and stay so.
 */
const finite = (v: unknown): number | null => {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/**
 * R/R HONESTY RULE — a reward/risk number is meaningless without a real stop
 * and a real target. The lane payload can carry `reward_risk: 9.53` while
 * `stop` and `target1` are both null; rendering that as "9.53R" is a fabricated
 * claim about a plan that does not exist. This returns an explicit unavailable
 * verdict instead, naming what is missing.
 *
 * UI-ONLY: this suppresses a *display*. It does not touch capital or entry
 * enforcement anywhere (SIGNAL_VALIDATION_UNCAPPED is untouched).
 */
export function rrRender(risk: RiskPlanLike): RrVerdict {
  const missing: string[] = [];
  const entry = finite(risk?.entry);
  const stop = finite(risk?.stop);
  const target1 = finite(risk?.target1);

  if (entry == null) missing.push("entry");
  if (stop == null) missing.push("stop");
  if (target1 == null) missing.push("target");

  if (missing.length) {
    return {
      ok: false,
      value: null,
      approx: false,
      text: "R/R unavailable",
      missing,
      reason: `no ${missing.join(" / ")}`,
    };
  }
  const perUnitRisk = Math.abs((entry as number) - (stop as number));
  if (!(perUnitRisk > 1e-9)) {
    return {
      ok: false,
      value: null,
      approx: false,
      text: "R/R unavailable",
      missing: ["stop"],
      reason: "stop equals entry (zero risk)",
    };
  }
  const reported = finite(risk?.reward_risk);
  const computed = Math.abs((target1 as number) - (entry as number)) / perUnitRisk;
  // The rendered number must be the one the DISPLAYED plan implies. The lane's
  // own `reward_risk` can be computed against a different target (target2/3) or
  // a stale leg — taking it on trust reintroduces exactly the fabrication this
  // function exists to stop, just with a plausible-looking plan behind it. So:
  // render the plan-consistent number, and when the lane disagrees materially,
  // say so rather than silently picking a winner.
  const disagrees =
    reported != null && Math.abs(reported - computed) > Math.max(0.05, computed * 0.05);
  return {
    ok: true,
    value: computed,
    approx: disagrees,
    text: `${computed.toFixed(2)}R`,
    missing: [],
    note: disagrees ? `lane reports ${reported!.toFixed(2)}R against a different target` : undefined,
  };
}

/** Convenience for a two-line MetricTile: `{ value, detail }`. */
export function rrTile(risk: RiskPlanLike, detailWhenOk = "minimum 1.5R"): {
  value: string;
  detail: string;
  ok: boolean;
} {
  const v = rrRender(risk);
  return v.ok
    ? { value: v.text, detail: v.note ?? detailWhenOk, ok: true }
    : { value: v.text, detail: v.reason, ok: false };
}

// ─── Bounded imbalance (replaces unbounded buy/sell ratios) ─────────────────

export type Imbalance = {
  /** 0–100 share of the level's volume taken by `own`; null when there is none. */
  pct: number | null;
  /** own/other, clamped. Kept ONLY for threshold predicates, never rendered raw. */
  ratio: number | null;
  oneSided: boolean;
  own: number;
  other: number;
  total: number;
  /** true when pct was inverted from a ratio because raw volumes were absent. */
  derived: boolean;
};

/** Ratios above this are meaningless as magnitudes — clamp before any use. */
export const MAX_DISPLAY_RATIO = 999;

/**
 * Bounded imbalance from two volumes. A 6,630× "ratio" is an artefact of a zero
 * denominator, not a measurement; the honest statements are "100% one-sided"
 * plus the raw volumes.
 */
export function imbalanceOf(
  own: number | null | undefined,
  other: number | null | undefined,
  explicitRatio?: number | null,
): Imbalance {
  const o = Number(own);
  const t = Number(other);
  const ownV = Number.isFinite(o) && o > 0 ? o : 0;
  const otherV = Number.isFinite(t) && t > 0 ? t : 0;
  const total = ownV + otherV;

  if (total > 0) {
    const rawRatio = otherV > 0 ? ownV / otherV : Infinity;
    return {
      pct: (ownV / total) * 100,
      ratio: otherV > 0 ? Math.min(rawRatio, MAX_DISPLAY_RATIO) : MAX_DISPLAY_RATIO,
      oneSided: otherV === 0 && ownV > 0,
      own: ownV,
      other: otherV,
      total,
      derived: false,
    };
  }

  // No raw volumes — fall back to inverting a reported ratio, and SAY so.
  const r = Number(explicitRatio);
  if (Number.isFinite(r) && r > 0) {
    const clamped = Math.min(r, MAX_DISPLAY_RATIO);
    return {
      pct: (clamped / (1 + clamped)) * 100,
      ratio: clamped,
      oneSided: false,
      own: 0,
      other: 0,
      total: 0,
      derived: true,
    };
  }

  return { pct: null, ratio: null, oneSided: false, own: 0, other: 0, total: 0, derived: false };
}

/** Human text for an imbalance: "74% buy" / "one-sided (no buy)" / "—". */
export function describeImbalance(imb: Imbalance, ownLabel: string, otherLabel: string): string {
  if (imb.pct == null) return "—";
  if (imb.oneSided) return `one-sided (no ${otherLabel})`;
  return `${imb.pct.toFixed(0)}% ${ownLabel}${imb.derived ? " (derived)" : ""}`;
}

// ─── Missing vs zero vs stale ───────────────────────────────────────────────

export type ValueState = "missing" | "zero" | "value" | "stale";

/**
 * ONE rule set for the three states a cell can be in, applied everywhere:
 *   missing → em-dash, muted, dotted border, "not reported"
 *   zero    → the numeral 0, normal weight (a measured zero is information)
 *   stale   → the value dimmed + amber, with its age
 */
export function classifyValue(
  value: number | null | undefined,
  freshness: Freshness = "fresh",
): ValueState {
  if (value == null || !Number.isFinite(Number(value))) return "missing";
  if (freshness === "stale") return "stale";
  if (Number(value) === 0) return "zero";
  return "value";
}

export const VALUE_STATE_CLASS: Record<ValueState, string> = {
  missing: "text-text-muted border-dotted",
  zero: "text-text-secondary",
  value: "text-text-primary",
  stale: "text-accent-amber opacity-55",
};

export const VALUE_STATE_TITLE: Record<ValueState, string> = {
  missing: "not reported",
  zero: "measured zero",
  value: "",
  stale: "stale observation",
};
