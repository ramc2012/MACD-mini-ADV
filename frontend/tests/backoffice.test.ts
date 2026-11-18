import { test } from "node:test";
import assert from "node:assert/strict";
import { heat, holdText, paperSummary, paperTotals, periodLabel, query, splitKinds, type Calendar, type LaneSummary } from "../src/backofficeMath.ts";
import { inLaneDay, laneDayLabel, laneDayStart } from "../src/laneDay.ts";

test("the lane day starts at 08:00 IST on the latest trading day whose 08:00 has passed", () => {
  // Wed 18 Nov 2026 09:05 IST -> 18 Nov 08:00 IST (02:30 UTC)
  assert.equal(new Date(laneDayStart(new Date("2026-11-18T03:35:00Z"))).toISOString(), "2026-11-18T02:30:00.000Z");
  // 07:59 IST still belongs to Tuesday's day
  assert.equal(new Date(laneDayStart(new Date("2026-11-18T02:29:00Z"))).toISOString(), "2026-11-17T02:30:00.000Z");
  // Sunday and Saturday roll back to Friday; a Monday holiday too
  assert.equal(new Date(laneDayStart(new Date("2026-11-22T10:00:00Z"))).toISOString(), "2026-11-20T02:30:00.000Z");
  assert.equal(new Date(laneDayStart(new Date("2026-11-23T10:00:00Z"), ["2026-11-23"])).toISOString(), "2026-11-20T02:30:00.000Z");
  // MCX evening stays in the same day
  assert.equal(new Date(laneDayStart(new Date("2026-11-18T18:00:00Z"))).toISOString(), "2026-11-18T02:30:00.000Z");
  assert.equal(laneDayLabel(new Date("2026-11-18T03:35:00Z")), "Wed 18 Nov");
});

test("rows before the lane day are dropped; undated rows are kept", () => {
  const start = laneDayStart(new Date("2026-11-18T03:35:00Z"));
  assert.equal(inLaneDay("2026-11-18T04:00:00+00:00", start), true);
  assert.equal(inLaneDay("2026-11-18T07:59:00+05:30", start), false);
  assert.equal(inLaneDay("2026-11-17T15:20:00+05:30", start), false);
  assert.equal(inLaneDay(undefined, start), true);
});

const lane = (name: string, kind: "paper" | "real", equity: number, realized: number): LaneSummary => ({
  lane: name, label: name, kind, capital: 100, starting_capital: 100, capital_added: 0, since: null, note: null, realized, equity,
  closed: { trades: 2, wins: 1, win_rate: 0.5, net: realized, profit_factor: 1, avg_win: 1, avg_loss: -1 }, months: [], years: [], sessions: 1,
});

test("real money stays out of the paper totals", () => {
  const calendar: Calendar = { period: "day", periods: ["2026-11-18"], lanes: [
    { lane: "macd", label: "MACD", kind: "paper", cells: { "2026-11-18": 100.5 } },
    { lane: "blast", label: "Blast", kind: "paper", cells: { "2026-11-18": -40.25, "2026-11-17": 5 } },
    { lane: "upstox_live", label: "Upstox", kind: "real", cells: { "2026-11-18": 9999 } },
  ] };
  assert.deepEqual(paperTotals(calendar), { "2026-11-18": 60.25, "2026-11-17": 5 });
  const summary = paperSummary([lane("a", "paper", 110, 10), lane("b", "paper", 95, -5), lane("u", "real", 500, 400)]);
  assert.deepEqual(summary, { lanes: 2, capital: 200, equity: 205, realized: 5, trades: 4 });
  assert.equal(splitKinds([lane("u", "real", 0, 0), lane("a", "paper", 0, 0)]).real.length, 1);
});

test("labels, heat levels, hold text and query strings", () => {
  assert.equal(periodLabel("2026-11-18"), "18 Nov");
  assert.equal(periodLabel("2026-11"), "Nov 2026");
  assert.equal(periodLabel("2026"), "2026");
  assert.equal(heat(100, 100), 3);
  assert.equal(heat(-10, 100), -1);
  assert.equal(heat(0.5, 100), 0);
  assert.equal(heat(undefined, 100), 0);
  assert.equal(holdText("2026-11-18T09:15:00+05:30", "2026-11-18T09:50:00+05:30"), "35m");
  assert.equal(holdText("2026-11-16T09:15:00+05:30", "2026-11-18T11:15:00+05:30"), "2d 2h");
  assert.equal(holdText(undefined, "2026-11-18T09:50:00+05:30"), "—");
  assert.equal(query({ lane: "macd", start: "", end: undefined, page: 2 }), "?lane=macd&page=2");
  assert.equal(query({}), "");
});
