/**
 * flow-provenance — source grading, and the FEATURE-AWARE correction to it.
 *
 * ─── Why this module exists (2026-07-19) ────────────────────────────────────
 *
 * Grading used to be purely SOURCE-aware: `market_ticks` in ⇒ `observed` out,
 * and every consumer then rendered its order-flow panel as REAL/observed. That
 * overstates the evidence, and the backend says so in plain text:
 *
 *   backend/analytics/orderflow.py (module docstring)
 *     "Indian retail brokers do not push public trade prints to subscribers,
 *      so true Lee-Ready CVD (each trade tagged buy/sell by aggressor) is not
 *      available. These functions approximate the same intuitions from OHLCV
 *      bars + L1 snapshots."
 *
 * Ground truth in the schema (db/migrations/versions/001_initial_schema.py:118):
 * `market_ticks` stores time, symbol, ltp, OHLC, CUMULATIVE volume, oi, bid,
 * ask, bid_qty, ask_qty. There is NO trade_id, NO last-trade quantity and NO
 * broker-supplied aggressor/side. `institutional_convergence/engine.py`
 * `build_footprint` buckets `ltp` and infers the buy/sell split heuristically.
 *
 * So the honest split is:
 *
 *   the QUOTE STREAM and the OHLCV BARS  → genuinely OBSERVED
 *   every buy/sell ATTRIBUTION built on  → MODELLED FROM QUOTES
 *   them (CVD, footprint buy/sell split,
 *   delta, aggression, absorption)
 *   an aggressor-tagged TRADE PRINT tape → UNAVAILABLE (we do not have one)
 *
 * `classifySourceGrade(source)` with no feature keeps its ORIGINAL, unchanged
 * behaviour — it answers "how was this source obtained?", which is what the
 * readiness gates in `OrderflowWorkbench.evaluateReadiness` and
 * `MpDesk.ofBadgeOf` ask. Passing a `feature` answers the sharper question
 * "how was THIS NUMBER obtained?", which is what every flow surface must show.
 *
 * ─── The FOUR AXES (2026-07-19, second correction) ──────────────────────────
 *
 * The first correction over-shot in one place: `snapshot` was added to the
 * bar-inferred set, so the saved ATM watchlist rendered "snapshot · bar
 * inferred · inferred from bars, not a tape". Nothing in that payload says so —
 * a stored snapshot may contain directly OBSERVED quotes. Claiming a WORSE
 * grade than the evidence supports is the same defect as claiming a better one,
 * just pointing the other way.
 *
 * So a datum carries FOUR independent facts, and no axis may set another:
 *
 *   (i)   ACQUISITION SOURCE  what produced it   quote stream | bar | book |
 *                                                model | unknown
 *   (ii)  STORAGE-READ MODE   how we read it     live | snapshot | backfilled |
 *                                                unknown
 *   (iii) FEATURE DERIVATION  how the NUMBER     observed | modelled_from_quotes
 *                             was derived        | modelled | bar_inferred |
 *                                                unknown_derivation | unavailable
 *   (iv)  LIVE-vs-REPLAY      what session it    (lives in `market-semantics`
 *                             describes           as `DataMode`)
 *
 * `snapshot` is a statement about (ii) ONLY. It therefore grades
 * `unknown_derivation` on (iii) — "the payload did not say" — and renders as
 * its own storage chip, never as a bar-inference accusation. The genuinely
 * bar-fabricated sources (`bar_inference`, `historical_bar_inference`,
 * `spot_index_proxy`, `bar_proxy`, …) are untouched and still grade
 * `bar_inferred`.
 *
 * Pure module: no React, no imports, no I/O — so it is unit-testable on its
 * own (see `frontend-v2/tests/flow-provenance.test.ts`).
 */

// ─── Vocabulary ─────────────────────────────────────────────────────────────

export type SourceGrade =
  | "observed"
  /** A genuine book/tape rebuild whose per-event side is still inferred. */
  | "reconstructed"
  /**
   * Derived from an OBSERVED quote/bar stream, but the buy/sell attribution
   * on top of it is a heuristic — there is no aggressor tape to check it
   * against. CVD, footprint sides, delta, aggression and absorption are all
   * this grade no matter how good the underlying quote stream is.
   */
  | "modelled_from_quotes"
  | "modelled"
  | "bar_inferred"
  /**
   * The payload told us HOW IT WAS READ (a stored snapshot, a persisted blob)
   * but not how the underlying numbers were acquired. Strictly weaker than
   * `observed` and strictly stronger than `bar_inferred`: we do not know, and
   * saying "bar inferred" would invent a fact. Distinct from `unavailable`,
   * which means the payload named no source at all.
   */
  | "unknown_derivation"
  | "unavailable";

/** Axis (i) — what produced the datum, independent of how it was stored. */
export type AcquisitionSource = "quote_stream" | "bar" | "book" | "model" | "unknown";

/** Axis (ii) — how this read reached us. NOT a derivation grade. */
export type StorageMode = "live" | "snapshot" | "backfilled" | "unknown";

/** Badge variants supported by `StatusBadge` — the contract emits only these. */
export type BadgeVariant = "neutral" | "success" | "warn" | "error" | "info";

/**
 * What KIND of number is being graded.
 *
 *   quote        — ltp / bid / ask / bid_qty / ask_qty snapshot        (observed)
 *   bar          — OHLCV bar                                          (observed)
 *   flow_attribution — CVD, footprint buy/sell split, delta,
 *                  aggression, absorption, imbalance                  (modelled)
 *   trade_print  — aggressor-tagged prints. WE DO NOT HAVE THESE from
 *                  any wired broker; always grades `unavailable`.
 */
export type MarketFeature = "quote" | "bar" | "flow_attribution" | "trade_print";

/** The buy/sell-attribution features. Kept as data so tests can enumerate. */
export const FLOW_ATTRIBUTION_FEATURES = [
  "cvd",
  "cumulative_delta",
  "delta",
  "footprint",
  "footprint_sides",
  "aggressive_buy_volume",
  "aggressive_sell_volume",
  "aggression",
  "absorption",
  "trade_imbalance",
] as const;

/**
 * Standing capability statement. No wired Indian retail broker pushes public
 * aggressor-tagged trade prints, so this is a constant, not a runtime probe.
 */
export const AGGRESSOR_TAPE_AVAILABLE = false as const;
export const NO_AGGRESSOR_TAPE_NOTE =
  "no aggressor tape — buy/sell sides are inferred from quotes";

// ─── Source sets (source-level grading; unchanged from the original) ────────

/**
 * Directly observed QUOTE/BAR streams. Note what this does and does not say:
 * the ltp/bid/ask stream is genuinely observed; the buy/sell split someone
 * later computes from it is NOT (see `MarketFeature`).
 */
const OBSERVED_SOURCES = new Set(["market_ticks", "tick_reconstruction", "ticks", "live_tick"]);

const RECONSTRUCTED_SOURCES = new Set(["tick_reconstruction_book", "depth_reconstruction"]);

/** Fabricated from OHLCV bars — never a tape. */
const BAR_INFERRED_SOURCES = new Set([
  "bar_inference",
  "bar_proxy",
  "bar_fallback",
  "bar_proxy_timeout",
  "insufficient_ticks",
  "spot_index_proxy",
  "historical_bar_inference",
  "scan_guarded",
]);

/**
 * STORAGE-READ sources (axis ii). These say WHERE the read came from and
 * nothing whatsoever about how the numbers were produced — `snapshot` used to
 * live in the bar-inferred set above, which made the terminal accuse a stored
 * quote of being fabricated from bars.
 */
const SNAPSHOT_STORAGE_SOURCES = new Set([
  "snapshot",
  "snapshot_cache",
  "cached_snapshot",
  "persisted_snapshot",
  "last_snapshot",
  "stored_snapshot",
]);

const BACKFILL_STORAGE_SOURCES = new Set(["backfill", "backfilled", "history", "historical"]);

/** Sources that ARE the live read path (a stream we are attached to now). */
const LIVE_STORAGE_SOURCES = new Set([
  "market_ticks",
  "tick_reconstruction",
  "ticks",
  "live_tick",
  "tick_reconstruction_book",
  "depth_reconstruction",
]);

/** Computed by a model (Black-Scholes greeks, policy heads, fitted regimes). */
const MODELLED_SOURCES = new Set([
  "black_scholes",
  "bs_model",
  "greeks_model",
  "policy",
  "policy_head",
  "modelled",
  "model",
  "synthetic_quote",
]);

const UNAVAILABLE_SOURCES = new Set(["", "unknown", "unavailable", "none", "null"]);

export function normalizeSource(source?: string | null): string {
  return String(source ?? "").trim().toLowerCase();
}

/**
 * Axis (ii) — the STORAGE-READ mode. Never claims `live` for a source it does
 * not recognise: an unknown string reads `unknown`, not "live".
 */
export function classifyStorageMode(source?: string | null): StorageMode {
  const s = normalizeSource(source);
  if (!s || UNAVAILABLE_SOURCES.has(s)) return "unknown";
  if (SNAPSHOT_STORAGE_SOURCES.has(s) || s.includes("snapshot")) return "snapshot";
  if (
    BACKFILL_STORAGE_SOURCES.has(s) ||
    s.startsWith("timescaledb") ||
    s.startsWith("timescale_") ||
    s.includes("backfill")
  ) {
    return "backfilled";
  }
  if (LIVE_STORAGE_SOURCES.has(s)) return "live";
  return "unknown";
}

/**
 * Axis (i) — what actually produced the datum. A storage-mode string carries
 * no acquisition information, so it answers `unknown` rather than guessing.
 */
export function classifyAcquisitionSource(source?: string | null): AcquisitionSource {
  const s = normalizeSource(source);
  if (!s || UNAVAILABLE_SOURCES.has(s)) return "unknown";
  if (OBSERVED_SOURCES.has(s)) return "quote_stream";
  if (RECONSTRUCTED_SOURCES.has(s)) return "book";
  if (BAR_INFERRED_SOURCES.has(s)) return "bar";
  if (MODELLED_SOURCES.has(s)) return "model";
  return "unknown";
}

const STORAGE_MODE_LABEL: Record<StorageMode, string> = {
  live: "live read",
  snapshot: "snapshot read",
  backfilled: "backfilled read",
  unknown: "read mode unknown",
};

const STORAGE_MODE_VARIANT: Record<StorageMode, BadgeVariant> = {
  // Not green: "we are reading the live path" is a transport fact, and this
  // module refuses to let a transport fact wear the actionable colour.
  live: "info",
  // Amber states a TRUE fact about the read — it is not the live path — and
  // deliberately says nothing about how the numbers were derived.
  snapshot: "warn",
  backfilled: "warn",
  unknown: "neutral",
};

export function storageModeLabel(mode: StorageMode): string {
  return STORAGE_MODE_LABEL[mode];
}

export function storageModeVariant(mode: StorageMode): BadgeVariant {
  return STORAGE_MODE_VARIANT[mode];
}

export function describeStorageMode(mode: StorageMode, source?: string | null): string {
  const s = normalizeSource(source);
  switch (mode) {
    case "live":
      return `read from the live path${s ? ` (${s})` : ""}`;
    case "snapshot":
      return `read from a stored snapshot${s ? ` (${s})` : ""} — a storage mode, NOT a statement about how the numbers were derived`;
    case "backfilled":
      return `read from persisted history${s ? ` (${s})` : ""}`;
    default:
      return "the payload did not say how this was read";
  }
}

/** Source-level grade only: "how was this stream obtained?". */
function gradeOfSourceString(source?: string | null): SourceGrade {
  const s = normalizeSource(source);
  if (UNAVAILABLE_SOURCES.has(s)) return "unavailable";
  if (OBSERVED_SOURCES.has(s)) return "observed";
  // A STORAGE mode is not a derivation. We know how it was read and nothing
  // about how it was produced — so say exactly that.
  if (SNAPSHOT_STORAGE_SOURCES.has(s)) return "unknown_derivation";
  if (RECONSTRUCTED_SOURCES.has(s)) return "reconstructed";
  if (BAR_INFERRED_SOURCES.has(s)) return "bar_inferred";
  if (MODELLED_SOURCES.has(s)) return "modelled";
  // TimescaleDB history reads are observed prints replayed from storage.
  if (s.startsWith("timescaledb") || s.startsWith("timescale_")) return "observed";
  if (s.includes("bar_") || s.includes("_proxy") || s.includes("inference")) return "bar_inferred";
  if (s.includes("tick")) return "reconstructed";
  // Unrecognised string: do NOT promote it. Unverified reads as inferred-grade.
  return "bar_inferred";
}

/**
 * Classify a backend source string into the shared grade vocabulary.
 *
 * `feature` is the honesty correction. Without it the answer is about the
 * STREAM (and is byte-identical to the pre-2026-07-19 behaviour, so no gate
 * that calls this bare can change). With `feature: "flow_attribution"` the
 * answer is about the buy/sell-attributed NUMBER, which can never be better
 * than `modelled_from_quotes` because no wired broker sends an aggressor tape.
 */
export function classifySourceGrade(
  source?: string | null,
  feature: MarketFeature = "quote",
): SourceGrade {
  if (feature === "trade_print") {
    // There is no aggressor-tagged print tape on any wired broker. Reserved
    // so the terminal can SAY that rather than silently grading something else.
    return "unavailable";
  }
  const grade = gradeOfSourceString(source);
  if (feature !== "flow_attribution") return grade;
  // Quote/bar-grade evidence, buy/sell-attributed by heuristic ⇒ modelled.
  if (grade === "observed" || grade === "reconstructed") return "modelled_from_quotes";
  // `unknown_derivation` stays unknown: we cannot claim the sides were inferred
  // from quotes when we do not know what the snapshot contains.
  return grade;
}

/** Convenience: grade a buy/sell-attribution number (CVD, footprint, delta). */
export function classifyFlowGrade(source?: string | null): SourceGrade {
  return classifySourceGrade(source, "flow_attribution");
}

/** True when a grade means "no buy/sell side here was actually observed". */
export function isInferredSideGrade(grade: SourceGrade): boolean {
  return grade === "modelled_from_quotes" || grade === "bar_inferred" || grade === "modelled";
}

const GRADE_LABEL: Record<SourceGrade, string> = {
  observed: "OBSERVED",
  reconstructed: "RECONSTRUCTED",
  modelled_from_quotes: "INFERRED FROM QUOTES",
  modelled: "MODELLED",
  bar_inferred: "BAR INFERRED",
  unknown_derivation: "DERIVATION UNKNOWN",
  unavailable: "SOURCE UNKNOWN",
};

const GRADE_VARIANT: Record<SourceGrade, BadgeVariant> = {
  observed: "success",
  reconstructed: "info",
  // NOT success — an inferred side must never read as a measured one.
  modelled_from_quotes: "info",
  modelled: "info",
  bar_inferred: "warn",
  // Neutral, not amber: "we don't know" is not an accusation.
  unknown_derivation: "neutral",
  unavailable: "neutral",
};

export function sourceGradeLabel(grade: SourceGrade): string {
  return GRADE_LABEL[grade];
}

export function sourceGradeVariant(grade: SourceGrade): BadgeVariant {
  return GRADE_VARIANT[grade];
}

/** True when a grade means "this was fabricated from bars, not observed". */
export function isFabricatedGrade(grade: SourceGrade): boolean {
  return grade === "bar_inferred" || grade === "unavailable";
}

// ─── Order-flow badge classification ────────────────────────────────────────

/**
 * `quote_derived` replaces the old `real` kind. Nothing an order-flow surface
 * renders is a trade print, so no kind may be called "real".
 */
export type OfSourceKind = "quote_derived" | "bar_inferred" | "unknown";

export type OfSourceClass = {
  kind: OfSourceKind;
  /** Terse badge text. Never asserts a trade print. */
  label: string;
  /** The grade of the buy/sell attribution this badge sits on. */
  grade: SourceGrade;
  /** Tooltip body — states the derivation in one line. */
  note: string;
};

/**
 * The order-flow streams this badge may call quote-derived (tick-granular).
 * Deliberately narrower than `classifySourceGrade` — a TimescaleDB history
 * read grades `observed` for provenance purposes but is not a live order-flow
 * stream, so it must never claim tick granularity here.
 */
const OF_TICK_STREAM_SOURCES = new Set([
  "market_ticks",
  "tick_reconstruction",
  "tick_reconstruction_book",
]);

const BAR_LABEL_SOURCES = new Set([
  "bar_inference",
  "bar_proxy",
  "bar_fallback",
  "bar_proxy_timeout",
  "spot_index_proxy",
]);

/**
 * Classify an order-flow source string for display.
 *
 * The old labels were "REAL TICKS" / "REAL TICKS · BOOK". They asserted trade
 * prints the feed has never carried. Every label now names the derivation.
 */
export function classifyOfSource(source?: string | null): OfSourceClass {
  const s = normalizeSource(source);
  const grade = classifyFlowGrade(s);

  if (OF_TICK_STREAM_SOURCES.has(s)) {
    return {
      kind: "quote_derived",
      label:
        s === "tick_reconstruction_book"
          ? "BOOK QUOTES · SIDES INFERRED"
          : "TICK QUOTES · SIDES INFERRED",
      grade,
      note:
        s === "tick_reconstruction_book"
          ? `order flow rebuilt from the L2 book snapshot stream (${s}); ${NO_AGGRESSOR_TAPE_NOTE}`
          : `order flow rebuilt from the L1 quote/tick stream (${s}); ${NO_AGGRESSOR_TAPE_NOTE}`,
    };
  }
  if (grade === "unavailable") {
    return {
      kind: "unknown",
      label: "SOURCE UNKNOWN",
      grade,
      note: "the payload did not report an order-flow source — treat with suspicion",
    };
  }
  if (s === "insufficient_ticks") {
    return {
      kind: "bar_inferred",
      label: "INSUFFICIENT TICKS · BAR INFERRED",
      grade,
      note: `too few quotes in the window, so flow fell back to OHLCV bars; ${NO_AGGRESSOR_TAPE_NOTE}`,
    };
  }
  if (classifyStorageMode(s) === "snapshot") {
    // Axis (ii) only. The old code fell through to the "unverified ⇒ bar
    // inferred" branch below and printed "BAR PROXY"-flavoured text for a
    // stored read whose contents may be perfectly good observed quotes.
    return {
      kind: "unknown",
      label: "SNAPSHOT READ · DERIVATION UNKNOWN",
      grade,
      note: `${describeStorageMode("snapshot", s)}; the payload does not report what produced the underlying numbers`,
    };
  }
  if (BAR_LABEL_SOURCES.has(s)) {
    return {
      kind: "bar_inferred",
      label: "BAR PROXY · SIDES INFERRED",
      grade,
      note: `order flow fabricated from OHLCV bar shape (${s}), not from a quote stream; ${NO_AGGRESSOR_TAPE_NOTE}`,
    };
  }
  // Recognised by the contract but not a known OF stream name — show it
  // verbatim and flag it rather than silently promoting it.
  return {
    kind: "bar_inferred",
    label: `${s.replace(/_/g, " ").toUpperCase()} · UNVERIFIED`,
    grade,
    note: `unrecognised order-flow source "${s}" — graded down, not promoted; ${NO_AGGRESSOR_TAPE_NOTE}`,
  };
}

/**
 * One-line caption for any buy/sell-attributed series, e.g.
 * `"CVD · inferred from quotes · no aggressor tape"`.
 */
export function describeFlowDerivation(featureLabel: string, source?: string | null): string {
  const { kind } = classifyOfSource(source);
  const basis =
    kind === "quote_derived" ? "inferred from quotes" : kind === "bar_inferred" ? "inferred from bars" : "source unknown";
  return `${featureLabel} · ${basis} · no aggressor tape`;
}

// ─── Structurally-absent capabilities ───────────────────────────────────────
//
// Some panels a trading terminal is EXPECTED to have cannot be built on this
// stack at all — not "no data today", but "no wired feed carries this". Those
// two cases must look different on screen, so the absence is declared HERE as
// data (one table) and rendered by one card component. A panel that would need
// a missing capability renders the card; it never renders a proxy in the slot
// the real thing would occupy.
//
// Kept pure and dependency-free so the capability card, the depth panel and any
// future horizon/lane gating all read the SAME record and cannot drift.

export type CapabilityKey = "BROKER_AGGRESSOR_PRINTS" | "DEPTH_L2";

export type MissingCapability = {
  key: CapabilityKey;
  /** Short display name of the capability itself. */
  label: string;
  /** What a panel would use it for. */
  wants: string;
  /** Why it is absent, in prose the trader can act on. */
  reason: string;
  /** The repo file that establishes the absence — quoted, not paraphrased. */
  citation: string;
  /**
   * True when no runtime probe can flip it on today's wired brokers. Both of
   * these are permanent in that sense: they are feed-capability gaps, not
   * outages, so they never render as "stale" or "loading".
   */
  permanent: true;
  /** What IS available instead, so the card is not a dead end. */
  insteadUse: string;
};

export const MISSING_CAPABILITIES: Record<CapabilityKey, MissingCapability> = {
  BROKER_AGGRESSOR_PRINTS: {
    key: "BROKER_AGGRESSOR_PRINTS",
    label: "Aggressor-tagged trade prints",
    wants:
      "a per-trade feed carrying price, size and the exchange's own buyer/seller-initiated flag",
    reason:
      "No wired Indian retail broker pushes public trade prints to subscribers. The market_ticks table stores quotes only — ltp, OHLC, CUMULATIVE volume, oi, bid, ask, bid_qty, ask_qty — with no trade id, no per-trade size and no broker-supplied side. Every buy/sell attribution in this terminal is therefore inferred from those quotes.",
    citation: "backend/analytics/orderflow.py (module docstring); flow-provenance AGGRESSOR_TAPE_AVAILABLE = false",
    permanent: true,
    insteadUse:
      "the quote-derived flow panels, which state their derivation on their face (CVD, footprint sides, aggression)",
  },
  DEPTH_L2: {
    key: "DEPTH_L2",
    label: "Real L2 order-book depth",
    wants: "the full resting-liquidity ladder, per price level, updating per event",
    reason:
      "The DOM and heatmap served by the backend are built from a broker depth PROXY plus market-profile levels, and raw exchange MBO sweep data is not available. Rendering them in a slot labelled 'depth' would present a reconstruction as an observed book.",
    citation: "backend/api/routers/orderflow.py — reference_model.heatmap / reference_model.whales",
    permanent: true,
    insteadUse:
      "the L1 quote spread and queue-pressure metrics, which ARE observed, and the OI wall ladder from the option chain",
  },
};

/** All declared gaps, for surfaces that enumerate rather than look one up. */
export const MISSING_CAPABILITY_KEYS = Object.keys(MISSING_CAPABILITIES) as CapabilityKey[];

export function missingCapability(key: CapabilityKey): MissingCapability {
  return MISSING_CAPABILITIES[key];
}

/**
 * True when a capability is structurally absent. Written as a lookup rather
 * than a boolean constant so a future wired feed is a one-line table change.
 */
export function isCapabilityMissing(key: string): boolean {
  return Object.prototype.hasOwnProperty.call(MISSING_CAPABILITIES, key);
}
