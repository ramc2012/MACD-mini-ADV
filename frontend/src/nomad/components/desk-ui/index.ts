/**
 * Public surface for the desk-ui primitives module.
 *
 * Goal: every desk imports from a single `@/components/desk-ui` entry
 * instead of reaching into per-file modules. If we later move files
 * around (e.g. promote MetricTile into a `tiles/` subdir) the consumers
 * don't need to change.
 */

export { REFRESH_MS } from "./refresh";

export {
  formatMoney,
  formatSignedMoney,
  formatNumber,
  formatSignedNumber,
  formatPct,
  formatIST,
  formatISTTime,
  formatTimestamp,
  formatDuration,
  toDate,
} from "./formatters";

export {
  tone,
  directionTone,
  regimeTone,
  serviceStateTone,
  decisionTone,
} from "./tones";

export { MetricTile } from "./MetricTile";
export { Sparkline } from "./Sparkline";
export { StatusBadge } from "./StatusBadge";
export { Section } from "./Section";
export { DeskShell, useUrlTab, useUrlChoice, type DeskTab } from "./DeskShell";

/**
 * Semantic contract renderers — the shared vocabulary for
 * data mode / source grade / freshness / sufficiency / provenance.
 * Derivations live in `@/lib/market-semantics`; these only render them.
 */
export {
  DataModeBadge,
  SourceGradeBadge,
  StorageModeBadge,
  FreshnessBadge,
  FreshnessChip,
  SufficiencyBadge,
  ExecutionModeBadge,
  SchedulerBadge,
  TransportBadge,
  ProvenanceChip,
  SemanticValue,
} from "./SemanticBadges";
