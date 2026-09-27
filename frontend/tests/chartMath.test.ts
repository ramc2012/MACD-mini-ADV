import assert from "node:assert/strict";
import { test } from "node:test";
import {
  IST_OFFSET, initialBalance, mergeLiveCandles, referenceLevels, sessionCutoff, sessionKey, sessionOpen, sessionStarts,
} from "../src/chartMath.ts";
import type { AuctionContext, Candle, PeriodRow, ProfileRow } from "../src/types.ts";

const ist = (day: string, clock: string) => {
  const [year, month, date] = day.split("-").map(Number);
  const [hour, minute] = clock.split(":").map(Number);
  return Date.UTC(year, month - 1, date, hour, minute) / 1000 - IST_OFFSET;
};
const bar = (timestamp: number, extra: Partial<Candle> = {}): Candle =>
  ({ symbol: "NSE:NIFTY50-INDEX", timestamp, open: 100, high: 101, low: 99, close: 100, volume: 0, closed: true, ...extra });
/** A 30-minute session of bars from 09:15 to 15:00 for one IST day. */
const session = (day: string) => Array.from({ length: 13 }, (_, i) => bar(ist(day, "09:15") + i * 1800));
const profile = (extra: Partial<ProfileRow>): ProfileRow => ({
  symbol: "NSE:NIFTY50-INDEX", day: "2026-09-01", open: null, high: null, low: null, close: null, poc: null, vah: null, val: null,
  ib_high: null, ib_low: null, volume: 0, buy_volume: 0, sell_volume: 0, cumulative_delta: 0, imbalance: null, trades: 0,
  day_type: null, value_migration: null, levels: 0, source: "ticks", ...extra,
});
const context = (extra: Partial<AuctionContext>): AuctionContext => ({
  symbol: "NSE:NIFTY50-INDEX", as_of: "2026-09-02T09:20:00+05:30", price: null, sessions_available: 30, value_migration: "flat",
  prior_day: null, week: null, month: null, naked_pocs: [], levels: {}, location: {}, alignment: "none", latest_session: null,
  regime: { regime_id: "r", summary: "", start: "2026-07-01", session_end: "15:30", nifty_lot: 65, nifty_futures_tick: 0.1, weekly_expiry: null },
  ...extra,
});
const ALL_LAYERS = { priorDay: true, week: true, nakedPocs: true, ib: true };

test("session open round-trips through the session key and shares the key with the close", () => {
  const open = ist("2026-09-02", "09:15");
  assert.equal(open, 1_788_320_700); // 2026-09-02T03:45:00Z
  assert.equal(sessionOpen(sessionKey(open)), open);
  assert.equal(sessionKey(ist("2026-09-02", "15:15")), sessionKey(open));
  assert.equal(sessionKey(ist("2026-09-03", "09:15")), sessionKey(open) + 1);
});

test("session starts mark every session after the first, even one missing its opening bar", () => {
  const rows = [...session("2026-08-31"), ...session("2026-09-01").slice(2), ...session("2026-09-02")];
  assert.deepEqual(sessionStarts(rows), [ist("2026-09-01", "10:15"), ist("2026-09-02", "09:15")]);
  assert.deepEqual(sessionStarts([]), []);
});

test("a one-session period is the latest session, not the trailing twenty-four hours", () => {
  const days = ["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02"];
  const rows = days.flatMap(session);
  const cutoff = sessionCutoff(rows, 1);
  assert.equal(cutoff, sessionKey(ist("2026-09-02", "09:15")) * 86_400 - IST_OFFSET);
  const visible = rows.filter((row) => row.timestamp >= cutoff);
  assert.deepEqual(visible, session("2026-09-02"));
  // At 09:20 the old wall-clock rule showed yesterday from 09:20 onward.
  assert.ok(visible.every((row) => row.timestamp >= ist("2026-09-02", "00:00")));
  assert.equal(sessionCutoff(rows, 5), sessionKey(ist("2026-08-27", "09:15")) * 86_400 - IST_OFFSET);
  assert.equal(sessionCutoff(rows, 0), 0);
  assert.equal(sessionCutoff([], 3), 0);
  assert.equal(sessionCutoff(rows, 99), sessionKey(ist("2026-08-20", "09:15")) * 86_400 - IST_OFFSET);
});

test("the initial balance is the first hour from 09:15 and stays forming until that hour has closed", () => {
  const first = bar(ist("2026-09-02", "09:15"), { high: 110, low: 95 });
  const second = bar(ist("2026-09-02", "09:45"), { high: 120, low: 90, closed: false });
  const third = bar(ist("2026-09-02", "10:15"), { high: 200, low: 10 });
  const forming = initialBalance([...session("2026-09-01"), first, second], 1800);
  assert.deepEqual(forming, { session: sessionKey(first.timestamp), high: 120, low: 90, complete: false });
  const closedSecond = initialBalance([first, { ...second, closed: true }], 1800);
  assert.equal(closedSecond?.complete, true);
  const withThird = initialBalance([first, second, third], 1800);
  assert.deepEqual(withThird, { session: sessionKey(first.timestamp), high: 120, low: 90, complete: true });
  assert.equal(initialBalance([], 1800), undefined);
  assert.equal(initialBalance([third], 1800), undefined);
});

test("live bars only append after the stored history and never replace a stored row", () => {
  const stored = session("2026-09-02").slice(0, 3);
  const last = stored[stored.length - 1];
  const replacement = bar(last.timestamp, { close: 999 });
  const older = bar(stored[0].timestamp, { close: 999 });
  assert.equal(mergeLiveCandles(stored, [replacement, older]), stored);
  const later = bar(last.timestamp + 3600, { close: 3 });
  const next = bar(last.timestamp + 1800, { close: 2 });
  const merged = mergeLiveCandles(stored, [later, next, { ...next, close: 22 }]);
  assert.deepEqual(merged.slice(0, 3), stored);
  assert.deepEqual(merged.slice(3).map((row) => [row.timestamp, row.close]), [[next.timestamp, 22], [later.timestamp, 3]]);
  assert.equal(mergeLiveCandles(stored, []), stored);
});

test("reference levels follow the layer toggles, skip null levels and cap naked POCs", () => {
  const priorDay = profile({ day: "2026-09-01", poc: 24_500, vah: null, val: 24_400 });
  const week: PeriodRow = { ...profile({ poc: 24_600, vah: 24_800, val: 24_300 }), period: "week", period_start: "2026-08-31", period_end: "2026-09-04", sessions: 3, naked_pocs: [] };
  const ctx = context({ prior_day: priorDay, week, naked_pocs: Array.from({ length: 12 }, (_, i) => 24_000 + i * 10) });
  const ib = { session: 1, high: 24_700, low: 24_450, complete: false };
  assert.deepEqual(referenceLevels(ctx, ib, { priorDay: false, week: false, nakedPocs: false, ib: false }), []);
  const levels = referenceLevels(ctx, ib, ALL_LAYERS);
  const byKey = new Map(levels.map((level) => [level.key, level]));
  assert.equal(byKey.get("pd_poc")?.title, "PD POC 09/01");
  assert.equal(byKey.has("pd_vah"), false);
  assert.equal(byKey.get("pd_val")?.kind, "pd_va");
  assert.deepEqual(["wk_poc", "wk_vah", "wk_val"].map((key) => byKey.get(key)?.price), [24_600, 24_800, 24_300]);
  assert.equal(levels.filter((level) => level.kind === "naked").length, 8);
  assert.equal(byKey.get("npoc_0")?.title, "NAKED POC");
  assert.equal(byKey.get("npoc_1")?.title, "");
  assert.equal(byKey.get("ib_high")?.kind, "ib_forming");
  assert.equal(byKey.get("ib_low")?.title, "IB L (forming)");
  const complete = referenceLevels(undefined, { ...ib, complete: true }, ALL_LAYERS);
  assert.deepEqual(complete.map((level) => [level.key, level.kind, level.title]), [["ib_high", "ib", "IB H"], ["ib_low", "ib", "IB L"]]);
  assert.deepEqual(referenceLevels(undefined, undefined, ALL_LAYERS), []);
});
