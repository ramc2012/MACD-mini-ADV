import type { BlastCandidate, BlastJournalSummary, BlastSettings, Position } from "./types";

// Pure arithmetic for the Blast lane page. Type-only imports, no JSX and no
// runtime imports from other modules, so `node --test` can load it directly
// (see tests/blastMath.test.ts).

export type VerdictGroup = "taken" | "passed" | "declined" | "data";
export type VerdictTone = "good" | "setup" | "bad" | "neutral" | "muted";
export type VerdictInfo = { label: string; group: VerdictGroup; tone: VerdictTone; help: string };

// Mirrors the verdict constants in blast_engine.py. The screen applies its
// legs in exactly the order the declined verdicts appear here, which is what
// lets screenFunnel reconstruct the funnel from verdict counts alone.
export const VERDICT_ORDER = [
  "TAKEN", "AUTO_TRADE_OFF", "ENTRY_REJECTED",
  "TOO_ILLIQUID", "NOT_OFF_HIGH", "BREADTH_TOO_THIN", "PREMIUM_TOO_RICH",
  "NO_HISTORY", "NO_BREADTH", "NO_SPOT", "ALREADY_HELD",
] as const;

const VERDICTS: Record<string, VerdictInfo> = {
  TAKEN: { label: "Taken", group: "taken", tone: "good", help: "Passed every leg of the screen and the lane entered." },
  AUTO_TRADE_OFF: { label: "Passed · shadow", group: "passed", tone: "setup", help: "Passed every leg. Auto-trade is off, so it was journalled and tracked, not bought." },
  ENTRY_REJECTED: { label: "Passed · order refused", group: "passed", tone: "bad", help: "Passed the screen, but the paper order was refused: cash, the position cap or a stale quote." },
  ALREADY_HELD: { label: "Held · not judged", group: "data", tone: "muted", help: "The lane already holds this contract, so the screen never judged it. Its excursion is tracked on the position, not here." },
  TOO_ILLIQUID: { label: "Too illiquid", group: "declined", tone: "neutral", help: "Even one lot would have been too large a share of what the contract itself trades." },
  NOT_OFF_HIGH: { label: "Near recent high", group: "declined", tone: "neutral", help: "The premium was not far enough below its own recent high." },
  BREADTH_TOO_THIN: { label: "Thin breadth", group: "declined", tone: "neutral", help: "Too few contracts on the same side had premium MACD above zero." },
  PREMIUM_TOO_RICH: { label: "Rich vs spot", group: "declined", tone: "neutral", help: "The premium was above the limit as a percentage of the underlying's price." },
  NO_HISTORY: { label: "Short history", group: "data", tone: "muted", help: "Too few closed bars to measure the recent high." },
  NO_BREADTH: { label: "No breadth", group: "data", tone: "muted", help: "Too few tracked contracts on that side to read a breadth fraction." },
  NO_SPOT: { label: "No spot", group: "data", tone: "muted", help: "No price for the underlying had arrived on the stream yet." },
};

export function verdictMeta(reason: string): VerdictInfo {
  return VERDICTS[reason] ?? { label: reason.replace(/_/g, " ").toLowerCase(), group: "declined", tone: "neutral", help: "" };
}

export type FunnelKey = "evaluated" | "judged" | "premium" | "breadth" | "offHigh" | "liquid" | "taken";
export type FunnelStep = { key: FunnelKey; count: number; share: number };

/** How many of the day's candidates survived each leg, in the order the screen applies them. */
export function screenFunnel(summary?: Pick<BlastJournalSummary, "evaluated" | "verdicts"> | null): FunnelStep[] {
  const counts = new Map((summary?.verdicts ?? []).map((row) => [row.reason, row.count]));
  const count = (reason: string) => counts.get(reason) ?? 0;
  const total = [...counts.values()].reduce((sum, value) => sum + value, 0);
  const evaluated = summary?.evaluated ?? total;
  // Contracts the lane already holds leave before the first leg, so they are
  // not part of the population any leg is measured against.
  const judged = evaluated - count("ALREADY_HELD")
    - count("NO_SPOT") - count("NO_BREADTH") - count("NO_HISTORY");
  const premium = judged - count("PREMIUM_TOO_RICH");
  const breadth = premium - count("BREADTH_TOO_THIN");
  const offHigh = breadth - count("NOT_OFF_HIGH");
  const liquid = offHigh - count("TOO_ILLIQUID");
  const steps: [FunnelKey, number][] = [
    ["evaluated", evaluated], ["judged", judged], ["premium", premium],
    ["breadth", breadth], ["offHigh", offHigh], ["liquid", liquid], ["taken", count("TAKEN")],
  ];
  return steps.map(([key, value]) => {
    const safe = Math.max(0, value);
    return { key, count: safe, share: evaluated > 0 ? safe / evaluated : 0 };
  });
}

/** Newest first, one row per id.
 *
 * A later sighting fills gaps but never blanks a value: a live stream frame
 * carries no excursion yet, and must not erase one the stored row already has.
 */
export function mergeCandidates(stored: BlastCandidate[], live: BlastCandidate[], cap = 500): BlastCandidate[] {
  const rows = new Map<string, BlastCandidate>();
  for (const row of [...stored, ...live]) {
    const existing = rows.get(row.id);
    rows.set(row.id, existing ? overlay(existing, row) : row);
  }
  return [...rows.values()].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).slice(0, cap);
}

function overlay<Row extends object>(base: Row, update: Row): Row {
  const next = { ...base } as Record<string, unknown>;
  for (const [key, value] of Object.entries(update)) {
    if (value !== null && value !== undefined) next[key] = value;
  }
  return next as Row;
}

const IST_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });

/** The IST session date, in the backend journal's YYYY-MM-DD form. */
export function istDay(at: number | string | Date = Date.now()): string {
  return IST_DAY.format(new Date(at));
}

export type ActiveStop = { kind: "hard" | "trail"; price: number; distancePct: number };

/** Whichever stop would fire first, and how far the mark is above it. */
export function activeStop(position: Pick<Position, "last_price" | "hard_stop" | "trailing_stop">): ActiveStop | null {
  const hard = position.hard_stop ?? 0;
  const trail = position.trailing_stop ?? 0;
  const price = Math.max(hard, trail);
  if (!(price > 0) || !(position.last_price > 0)) return null;
  return { kind: trail > hard ? "trail" : "hard", price, distancePct: (position.last_price - price) / position.last_price * 100 };
}

// The risk overlay cannot be edited under open positions; the backend answers 409.
export const RISK_KEYS = ["hard_stop_pct", "trail_activation_pct", "trail_pct"] as const;

// Form values are text so a half-typed number is not coerced mid-edit, and the
// three fractions are shown as percentages: "50" is a 50% stop, never 0.5.
export type BlastForm = {
  enabled: boolean; auto_trade: boolean;
  initial_capital: string; max_positions: string; target_notional: string;
  max_premium_pct: string; min_breadth_pct: string; min_off_high_pct: string;
  hard_stop_pct: string; trail_activation_pct: string; trail_pct: string;
};

const asText = (value: number) => String(Number(value.toFixed(6)));

export function toForm(settings: BlastSettings): BlastForm {
  return {
    enabled: settings.enabled,
    auto_trade: settings.auto_trade,
    initial_capital: asText(settings.initial_capital),
    max_positions: asText(settings.max_positions),
    target_notional: asText(settings.target_notional),
    max_premium_pct: asText(settings.max_premium_pct),
    min_breadth_pct: asText(settings.min_breadth * 100),
    min_off_high_pct: asText(settings.min_off_high_pct),
    hard_stop_pct: asText(settings.hard_stop_pct * 100),
    trail_activation_pct: asText(settings.trail_activation_pct * 100),
    trail_pct: asText(settings.trail_pct * 100),
  };
}

/** Validate against the backend's own bounds (BlastSettingsInput) and convert back. */
export function fromForm(form: BlastForm): { payload?: BlastSettings; error?: string } {
  const errors: string[] = [];
  const read = (text: string, label: string, min: number, max: number,
    { minOpen = false, maxOpen = false, integer = false } = {}) => {
    const raw = String(text).trim();
    const value = Number(raw);
    const low = minOpen ? value <= min : value < min;
    const high = maxOpen ? value >= max : value > max;
    if (raw === "" || !Number.isFinite(value) || low || high || (integer && !Number.isInteger(value))) {
      errors.push(`${label} must be ${integer ? "a whole number " : ""}${minOpen ? "above" : "at least"} ${min} and ${maxOpen ? "below" : "at most"} ${max}.`);
      return Number.NaN;
    }
    return value;
  };
  const payload: BlastSettings = {
    enabled: form.enabled,
    auto_trade: form.auto_trade,
    initial_capital: read(form.initial_capital, "Capital", 1, 1_000_000_000_000),
    max_positions: read(form.max_positions, "Max open positions", 1, 100, { integer: true }),
    target_notional: read(form.target_notional, "Size per entry", 0, 5_000_000),
    max_premium_pct: read(form.max_premium_pct, "Premium limit (% of spot)", 0, 100, { minOpen: true }),
    min_breadth: read(form.min_breadth_pct, "Breadth floor (%)", 0, 100) / 100,
    min_off_high_pct: read(form.min_off_high_pct, "Distance below the recent high (%)", 0, 99),
    hard_stop_pct: read(form.hard_stop_pct, "Hard stop (%)", 0, 100, { minOpen: true, maxOpen: true }) / 100,
    trail_activation_pct: read(form.trail_activation_pct, "Trail activation (%)", 0, 500, { minOpen: true, maxOpen: true }) / 100,
    trail_pct: read(form.trail_pct, "Trail (%)", 0, 100, { minOpen: true, maxOpen: true }) / 100,
  };
  return errors.length ? { error: errors[0] } : { payload };
}

/** Only the keys that actually differ, so an unrelated save never touches the locked risk overlay. */
export function changedSettings(current: BlastSettings, next: BlastSettings): Partial<BlastSettings> {
  const changes: Record<string, unknown> = {};
  (Object.keys(next) as (keyof BlastSettings)[]).forEach((key) => {
    const before = current[key];
    const after = next[key];
    const same = typeof before === "number" && typeof after === "number" ? Math.abs(before - after) < 1e-9 : before === after;
    if (!same) changes[key] = after;
  });
  return changes as Partial<BlastSettings>;
}
