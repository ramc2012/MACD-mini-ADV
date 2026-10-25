"use client";

/**
 * SemanticBadges — the ONE renderer set for the terminal's semantic contract.
 *
 * Pairs with `lib/market-semantics.ts`: that module decides *what is true*,
 * these components decide *how it looks*. Every badge here emits a plain
 * `StatusBadge`, so nothing new has to be styled and every desk that adopts
 * the contract instantly reads identically to every other desk.
 *
 * The rule this file exists to enforce: a surface may not invent its own
 * answer to "is this live / where did it come from / can I act on it".
 */
import { clsx } from "clsx";

import { LastUpdated } from "@/components/common/LastUpdated";
import { StatusBadge } from "./StatusBadge";
import {
  type DataMode,
  type ExecutionMode,
  type Freshness,
  type Provenance,
  type SchedulerState,
  type SourceGrade,
  type StorageMode,
  type Sufficiency,
  type ValueState,
  VALUE_STATE_CLASS,
  VALUE_STATE_TITLE,
  classifyValue,
  dataModeLabel,
  dataModeVariant,
  describeProvenance,
  executionModeVariant,
  formatAgeShort,
  freshnessVariant,
  schedulerStateLabel,
  schedulerStateVariant,
  describeStorageMode,
  sourceGradeLabel,
  sourceGradeVariant,
  storageModeLabel,
  storageModeVariant,
  sufficiencyLabel,
  sufficiencyVariant,
} from "@/lib/market-semantics";

/** What the numbers describe — live auction, replayed session, or bar fiction. */
export function DataModeBadge({
  mode,
  title,
  className,
}: {
  mode: DataMode;
  title?: string;
  className?: string;
}) {
  return (
    <span title={title ?? `data mode: ${dataModeLabel(mode)}`}>
      <StatusBadge
        label={dataModeLabel(mode)}
        variant={dataModeVariant(mode)}
        className={clsx(mode === "bar_inference" && "animate-pulse", className)}
      />
    </span>
  );
}

/** How the numbers were obtained. Never promotes an unrecognised source. */
export function SourceGradeBadge({
  grade,
  source,
  className,
}: {
  grade: SourceGrade;
  source?: string | null;
  className?: string;
}) {
  return (
    <span title={`source: ${source || "not reported"} · grade: ${sourceGradeLabel(grade).toLowerCase()}`}>
      <StatusBadge
        label={sourceGradeLabel(grade)}
        variant={sourceGradeVariant(grade)}
        className={clsx(grade === "bar_inferred" && "animate-pulse", className)}
      />
    </span>
  );
}

/**
 * HOW IT WAS READ — a separate axis from HOW IT WAS DERIVED.
 *
 * A stored snapshot may contain perfectly good observed quotes; grading it
 * "bar inferred" (as this app briefly did) claims a worse provenance than the
 * payload supports, which is the same class of false claim as claiming a better
 * one. So the storage mode gets its OWN chip and never touches the grade.
 */
export function StorageModeBadge({
  mode,
  source,
  className,
}: {
  mode: StorageMode;
  source?: string | null;
  className?: string;
}) {
  if (mode === "unknown") return null;
  return (
    <span title={describeStorageMode(mode, source)}>
      <StatusBadge
        label={storageModeLabel(mode)}
        variant={storageModeVariant(mode)}
        className={className}
      />
    </span>
  );
}

/** When they were obtained — delegates to the canonical LastUpdated pill. */
export function FreshnessBadge({
  asOf,
  label = "Updated",
  staleAfterSeconds,
  criticalAfterSeconds,
  className,
}: {
  asOf?: string | number | Date | null;
  label?: string;
  staleAfterSeconds?: number;
  criticalAfterSeconds?: number;
  className?: string;
}) {
  return (
    <LastUpdated
      timestamp={asOf ?? null}
      label={label}
      staleAfterSeconds={staleAfterSeconds}
      criticalAfterSeconds={criticalAfterSeconds}
      className={className}
    />
  );
}

/** Compact freshness for dense grids where LastUpdated is too wide. */
export function FreshnessChip({
  freshness,
  ageSeconds,
  className,
}: {
  freshness: Freshness;
  ageSeconds?: number | null;
  className?: string;
}) {
  const label =
    freshness === "absent" ? "no timestamp" : `${formatAgeShort(ageSeconds)} old`;
  return <StatusBadge label={label} variant={freshnessVariant(freshness)} className={className} />;
}

/** Whether it can be acted on, and why not. */
export function SufficiencyBadge({
  sufficiency,
  reasons = [],
  className,
}: {
  sufficiency: Sufficiency;
  reasons?: string[];
  className?: string;
}) {
  return (
    <span title={reasons.length ? reasons.join(" · ") : undefined}>
      <StatusBadge
        label={sufficiencyLabel(sufficiency)}
        variant={sufficiencyVariant(sufficiency)}
        className={className}
      />
    </span>
  );
}

/** Paper vs live vs parked — what would happen to an order. */
export function ExecutionModeBadge({ mode, className }: { mode: ExecutionMode; className?: string }) {
  if (mode === "none") return null;
  return (
    <StatusBadge
      label={mode}
      variant={executionModeVariant(mode)}
      className={className}
    />
  );
}

/** Loop state. ARMED IS NOT LIVE — the label never says "live". */
export function SchedulerBadge({ state, className }: { state: SchedulerState; className?: string }) {
  if (state === "unknown") return null;
  return (
    <StatusBadge
      label={schedulerStateLabel(state)}
      variant={schedulerStateVariant(state)}
      className={className}
    />
  );
}

/**
 * TRANSPORT, not data. A websocket being up says nothing about whether the
 * market is open or the numbers are fresh, so this badge is forbidden from
 * using the word "live" — it says "ws" or "poll" and nothing more.
 */
export function TransportBadge({
  connected,
  className,
}: {
  connected: boolean;
  className?: string;
}) {
  return (
    <StatusBadge
      label={connected ? "ws" : "poll"}
      variant={connected ? "info" : "neutral"}
      className={className}
    />
  );
}

/**
 * ProvenanceChip — the 0d affordance. Every chart/panel states its source,
 * grade, aggregation, completeness, timestamp and data mode in one line.
 *
 *   market_ticks · observed · 3m · 124/124 bars · 2d old · replay
 */
export function ProvenanceChip({
  provenance,
  density = "inline",
  className,
}: {
  provenance: Provenance;
  density?: "inline" | "caption";
  className?: string;
}) {
  const p = provenance;
  const detail = [
    `source: ${p.source ?? "not reported"}`,
    `grade: ${sourceGradeLabel(p.grade).toLowerCase()}`,
    `data mode: ${dataModeLabel(p.dataMode)}`,
    `as of: ${p.asOf ?? "not reported"}`,
    `age: ${p.freshness === "absent" ? "unknown" : formatAgeShort(p.ageSeconds)}`,
    `aggregation: ${p.timeframe ?? "not reported"}`,
    `completeness: ${
      p.completeness.label ??
      (p.completeness.have != null
        ? `${p.completeness.have}${p.completeness.expect != null ? `/${p.completeness.expect}` : ""}`
        : "not reported")
    }`,
    `sufficiency: ${sufficiencyLabel(p.sufficiency)}`,
    ...(p.reasons.length ? [`reasons: ${p.reasons.join("; ")}`] : []),
  ].join("\n");

  if (density === "caption") {
    return (
      <div
        className={clsx(
          "mt-1.5 flex items-center gap-1.5 font-mono text-[10px] leading-4 text-text-muted",
          className,
        )}
        title={detail}
      >
        <span
          className={clsx(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            p.grade === "observed"
              ? "bg-accent-green"
              : p.grade === "unavailable" || p.grade === "unknown_derivation"
                ? "bg-text-muted"
                : "bg-accent-amber",
          )}
        />
        <span className="truncate">{describeProvenance(p)}</span>
      </div>
    );
  }

  return (
    <span className={clsx("inline-flex items-center gap-1.5", className)} title={detail}>
      <SourceGradeBadge grade={p.grade} source={p.source} />
      <StorageModeBadge mode={p.storageMode} source={p.source} />
      {p.dataMode !== "live" ? <DataModeBadge mode={p.dataMode} /> : null}
      {p.sufficiency !== "ok" ? (
        <SufficiencyBadge sufficiency={p.sufficiency} reasons={p.reasons} />
      ) : null}
    </span>
  );
}

/**
 * SemanticValue — missing vs zero vs stale, rendered distinctly.
 *
 * A blank cell, a measured zero and a stale reading are three different facts
 * and used to look identical. This is the one component that keeps them apart:
 * missing is an em-dash with a dotted underline, zero is the numeral, stale is
 * dimmed amber with its age.
 */
export function SemanticValue({
  value,
  format,
  freshness = "fresh",
  ageSeconds,
  className,
  suffix,
}: {
  value: number | null | undefined;
  /** Formatter for the non-missing case; defaults to a plain string cast. */
  format?: (v: number) => string;
  freshness?: Freshness;
  ageSeconds?: number | null;
  className?: string;
  suffix?: string;
}) {
  const state: ValueState = classifyValue(value, freshness);
  if (state === "missing") {
    return (
      <span
        className={clsx(
          "border-b border-dotted border-text-muted/60 font-mono text-text-muted",
          className,
        )}
        title={VALUE_STATE_TITLE.missing}
      >
        —
      </span>
    );
  }
  const n = Number(value);
  const text = format ? format(n) : String(n);
  return (
    <span
      className={clsx("font-mono", VALUE_STATE_CLASS[state], className)}
      title={
        state === "stale"
          ? `stale observation · ${formatAgeShort(ageSeconds)} old`
          : state === "zero"
            ? VALUE_STATE_TITLE.zero
            : undefined
      }
    >
      {text}
      {suffix ?? ""}
    </span>
  );
}

export { classifyValue };
