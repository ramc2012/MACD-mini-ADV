import assert from "node:assert/strict";
import { test } from "node:test";
import {
  VERDICT_ORDER, activeStop, changedSettings, fromForm, istDay, mergeCandidates, screenFunnel, toForm, verdictMeta,
} from "../src/blastMath.ts";
import type { BlastCandidate, BlastSettings, BlastVerdictSummary } from "../src/types.ts";

const verdict = (reason: string, count: number): BlastVerdictSummary =>
  ({ reason, count, resolved: 0, mean_mfe_pct: null, mean_mae_pct: null });

const candidate = (over: Partial<BlastCandidate> = {}): BlastCandidate => ({
  id: "a", at: "2026-09-14T04:00:00+00:00", day: "2026-09-14", symbol: "NSE:SBIN26SEP800CE", bar_timestamp: 1,
  spot_symbol: "NSE:SBIN-EQ", option_type: "CE", strike: 800, expiry: "2026-09-29",
  premium: 6, spot: 800, premium_pct: 0.75, breadth: 0.8, off_high_pct: -40, high_ref: 10, lookback_bars: 30,
  macd: -0.4, signal: -0.5, histogram: 0.1, taken: 0, reason: "AUTO_TRADE_OFF", order_id: null,
  watch_until: "2026-09-16T04:00:00+00:00", ...over,
});

const SETTINGS: BlastSettings = {
  enabled: true, auto_trade: false, initial_capital: 1_000_000, max_positions: 10, target_notional: 100_000,
  max_premium_pct: 1.2, min_breadth: 0.5, min_off_high_pct: 25, hard_stop_pct: 0.5, trail_activation_pct: 0.3, trail_pct: 0.4,
};

test("the funnel subtracts each leg in the order the screen applies them", () => {
  const steps = screenFunnel({ evaluated: 100, verdicts: [
    verdict("ALREADY_HELD", 4), verdict("NO_SPOT", 5), verdict("NO_HISTORY", 5), verdict("PREMIUM_TOO_RICH", 60),
    verdict("BREADTH_TOO_THIN", 10), verdict("NOT_OFF_HIGH", 12), verdict("TOO_ILLIQUID", 1),
    verdict("AUTO_TRADE_OFF", 6), verdict("TAKEN", 2),
  ] });
  assert.deepEqual(Object.fromEntries(steps.map((step) => [step.key, step.count])),
    { evaluated: 100, judged: 86, premium: 26, breadth: 16, offHigh: 4, liquid: 3, taken: 2 });
  assert.equal(steps.find((step) => step.key === "offHigh")?.share, 0.04);
});

test("a contract the lane already holds never reaches the first leg", () => {
  const counts = (rows: ReturnType<typeof screenFunnel>) =>
    Object.fromEntries(rows.map((step) => [step.key, step.count]));
  const without = counts(screenFunnel({ evaluated: 10, verdicts: [verdict("TAKEN", 10)] }));
  const withHeld = counts(screenFunnel({ evaluated: 20, verdicts: [verdict("ALREADY_HELD", 10), verdict("TAKEN", 10)] }));
  assert.equal(withHeld.judged, without.judged);
  assert.equal(withHeld.premium, without.premium);
});

test("an empty day is an empty funnel, not a division by zero", () => {
  assert.ok(screenFunnel(undefined).every((step) => step.count === 0 && step.share === 0));
});

test("every backend verdict has a label and an explanation", () => {
  assert.equal(VERDICT_ORDER.length, 11);
  VERDICT_ORDER.forEach((reason) => assert.notEqual(verdictMeta(reason).help, ""));
  assert.equal(verdictMeta("SOMETHING_NEW").label, "something new");
});

test("a live frame never erases an excursion the stored row already has", () => {
  const stored = candidate({ mfe_pct: 150, mae_pct: -25, resolved: true });
  const live = candidate({ reason: "AUTO_TRADE_OFF" });
  const [merged] = mergeCandidates([stored], [live]);
  assert.equal(merged.mfe_pct, 150);
  assert.equal(merged.resolved, true);
});

test("candidates merge newest first, one per id, and honour the cap", () => {
  const rows = mergeCandidates(
    [candidate({ id: "old", at: "2026-09-14T04:00:00+00:00" })],
    [candidate({ id: "new", at: "2026-09-14T05:00:00+00:00" }), candidate({ id: "old", at: "2026-09-14T04:00:00+00:00" })],
  );
  assert.deepEqual(rows.map((row) => row.id), ["new", "old"]);
  assert.equal(mergeCandidates(rows, [], 1).length, 1);
});

test("the active stop is whichever would fire first", () => {
  assert.deepEqual(activeStop({ last_price: 6, hard_stop: 3, trailing_stop: null }), { kind: "hard", price: 3, distancePct: 50 });
  assert.equal(activeStop({ last_price: 12, hard_stop: 3, trailing_stop: 7.2 })?.kind, "trail");
  assert.equal(activeStop({ last_price: 0, hard_stop: 3, trailing_stop: null }), null);
});

test("form values round-trip, with fractions shown as percentages", () => {
  const form = toForm(SETTINGS);
  assert.equal(form.hard_stop_pct, "50");
  assert.equal(form.trail_activation_pct, "30");
  assert.equal(form.min_breadth_pct, "50");
  const parsed = fromForm(form);
  assert.ok(parsed.payload);
  assert.deepEqual(changedSettings(SETTINGS, parsed.payload), {});
});

test("an out-of-range value is refused before it reaches the backend", () => {
  assert.match(fromForm({ ...toForm(SETTINGS), hard_stop_pct: "100" }).error ?? "", /Hard stop/);
  assert.match(fromForm({ ...toForm(SETTINGS), max_positions: "2.5" }).error ?? "", /whole number/);
  assert.match(fromForm({ ...toForm(SETTINGS), max_premium_pct: "" }).error ?? "", /Premium limit/);
});

test("a save sends only what changed", () => {
  const parsed = fromForm({ ...toForm(SETTINGS), min_breadth_pct: "60", auto_trade: true });
  assert.deepEqual(changedSettings(SETTINGS, parsed.payload!), { auto_trade: true, min_breadth: 0.6 });
});

test("the session date is the IST date", () => {
  assert.equal(istDay("2026-09-13T18:29:00Z"), "2026-09-13");
  assert.equal(istDay("2026-09-13T18:30:00Z"), "2026-09-14");
});
