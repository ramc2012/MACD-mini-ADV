import assert from "node:assert/strict";
import { test } from "node:test";
import { changedSettings, fromForm, macdState, newestMcxQuote, orderedRoots, sessionLabel, shortChanges, toForm, toShortForm, verdictMeta } from "../src/mcxMath.ts";
import type { McxContractRow, McxQuote, McxSettings, McxShortSettings } from "../src/mcxMath.ts";

const SETTINGS: McxSettings = {
  enabled: true, auto_trade: false, roots_csv: "GOLD,SILVER,CRUDEOIL,COPPER,NATURALGAS", initial_capital: 1_000_000,
  target_notional: 100_000, max_entry_lots: 10, max_positions: 10, timeframe_seconds: 0, min_days_to_expiry: 1,
  expiry_flatten_ist: "23:00", rest_fallback_seconds: 60, lot_multipliers_csv: "",
};

test("an untouched form saves nothing", () => {
  const parsed = fromForm(toForm(SETTINGS));
  assert.ok(parsed.payload);
  assert.deepEqual(changedSettings(SETTINGS, parsed.payload), {});
});

test("only changed fields are sent, with minutes turned into seconds", () => {
  const form = { ...toForm(SETTINGS), auto_trade: true, timeframe_minutes: "30", roots: ["CRUDEOIL", "GOLD"] };
  const parsed = fromForm(form);
  assert.ok(parsed.payload);
  assert.deepEqual(changedSettings(SETTINGS, parsed.payload), { auto_trade: true, timeframe_seconds: 1800, roots_csv: "GOLD,CRUDEOIL" });
});

test("the owner's ticket and opposite-cross rules default on and save only when switched", () => {
  const form = toForm(SETTINGS);
  assert.equal(form.ticket_ceiling, true);
  assert.equal(form.opposite_cross_exit, true);
  const off = fromForm({ ...form, opposite_cross_exit: false });
  assert.ok(off.payload);
  assert.deepEqual(changedSettings(SETTINGS, off.payload), { opposite_cross_exit: false });
  assert.equal(verdictMeta("LANE_FULL").label, "Lane full");
});

test("roots keep the display order whatever order they were ticked in", () => {
  assert.deepEqual(orderedRoots(["NATURALGAS", "GOLD", "ZINC"]), ["GOLD", "NATURALGAS", "ZINC"]);
  const server = { ...SETTINGS, roots_csv: "CRUDEOIL,GOLD" };
  const parsed = fromForm(toForm(server));
  assert.ok(parsed.payload);
  assert.deepEqual(changedSettings(server, parsed.payload), {});
});

test("invalid values are refused before they reach the server", () => {
  assert.match(fromForm({ ...toForm(SETTINGS), roots: [] }).error ?? "", /at least one/);
  assert.match(fromForm({ ...toForm(SETTINGS), expiry_flatten_ist: "25:00" }).error ?? "", /HH:MM/);
  assert.match(fromForm({ ...toForm(SETTINGS), max_entry_lots: "1.5" }).error ?? "", /whole number/);
  assert.equal(fromForm({ ...toForm(SETTINGS), expiry_flatten_ist: "" }).payload?.expiry_flatten_ist, "");
});

test("contract MACD state reads warming, bars and side of zero", () => {
  const row = { warming: false, macd: -0.4, bars: 40 } as McxContractRow;
  assert.deepEqual(macdState(row), { label: "below zero", tone: "negative" });
  assert.equal(macdState({ ...row, warming: true }).label, "warming");
  assert.equal(macdState({ ...row, macd: null }).label, "40 bars");
});

test("session and verdict labels", () => {
  const session = { open: true, phase: "evening", opens_at: "09:00", closes_at: "23:30", next_open: "", holiday: null, calendar_covers_year: true };
  assert.equal(sessionLabel(session), "MCX open · evening · closes 23:30");
  assert.equal(sessionLabel({ ...session, open: false, phase: "holiday", holiday: "Christmas" }), "MCX holiday · Christmas");
  assert.equal(verdictMeta("TAKEN").label, "Taken");
  assert.equal(verdictMeta("SOMETHING_NEW").label, "SOMETHING_NEW");
});

test("a late polling snapshot cannot overwrite a newer MCX price move or OI", () => {
  const price = { symbol: "MCX:CRUDEOIL", ltp: 6000, move: "up", updated_ms: 1200, seen_ms: 1200,
    price_ms: 1100, day: "2026-11-16", source: "ws", prev_close: 5950, change: 50, change_pct: 0.84,
    volume: 250, oi: 750, oi_change: 25, oi_ms: 1200 } satisfies McxQuote;
  const older = { ...price, updated_ms: 1000, ltp: 5995, move: "down" as const, oi: null };
  assert.equal(newestMcxQuote(older, price), price);
  const oiUpdate = { ...price, oi: 800, oi_change: 75, oi_ms: 1500, updated_ms: 1500 };
  assert.equal(newestMcxQuote(oiUpdate, price), oiUpdate);
});

const SHORT: McxShortSettings = {
  auto_trade: true, capital: 2_500_000, ticket: 300_000, max_lots: 300, max_positions: 1,
  price_scan_csv: "GOLD:8,SILVER:12,CRUDEOIL:30,NATURALGAS:13,COPPER:9", volatility_scan_pct: 25, extreme_cover: 0.35,
  min_charge_pct: 3, elm_pct: 1,
};

test("selling settings send only changed short_ fields and refuse a malformed scan list", () => {
  assert.deepEqual(shortChanges(SHORT, toShortForm(SHORT)).payload, {});
  const form = { ...toShortForm(SHORT), ticket: "500000", price_scan_csv: "gold:9, silver:12" };
  assert.deepEqual(shortChanges(SHORT, form).payload, { short_ticket: 500_000, short_price_scan_csv: "GOLD:9,SILVER:12" });
  assert.match(shortChanges(SHORT, { ...toShortForm(SHORT), price_scan_csv: "GOLD=9" }).error ?? "", /GOLD:8/);
  assert.match(shortChanges(SHORT, { ...toShortForm(SHORT), extreme_cover: "2" }).error ?? "", /0 to 1/);
});
