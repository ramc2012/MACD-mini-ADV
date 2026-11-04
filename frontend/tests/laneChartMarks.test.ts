import assert from "node:assert/strict";
import {test} from "node:test";
import {drawdowns, fillMarkers, positionLevels} from "../src/laneChartMarks.ts";
import type {Trade} from "../src/types.ts";

const fill = (side: string, price: number, quantity: number, timestamp: string, symbol = "NSE:SBIN-EQ") =>
  ({trade_id: `${side}${timestamp}`, symbol, side, price, quantity, lots: quantity, lot_size: 1, timestamp} as Trade);

test("fills become entry, add and exit markers; a sell below average cost is a loss", () => {
  const trades = [
    fill("BUY", 100, 10, "2026-11-03T04:00:00Z"), fill("BUY", 104, 10, "2026-11-03T05:00:00Z"),
    fill("SELL", 108, 5, "2026-11-04T05:00:00Z"), fill("SELL", 99, 15, "2026-11-05T05:00:00Z"),
    fill("BUY", 50, 1, "2026-11-03T04:00:00Z", "NSE:INFY-EQ"),
  ];
  const marks = fillMarkers(trades, "NSE:SBIN-EQ");
  assert.deepEqual(marks.map((m) => [m.label, m.tone]),
    [["BUY 10", "entry"], ["ADD 10", "add"], ["SELL 5", "exit"], ["SELL 15", "exit_loss"]]);
  assert.equal(marks[0].timestamp, Date.parse("2026-11-03T04:00:00Z") / 1000);
});

test("a position's window keeps only its own fills, and a later re-entry is an entry again", () => {
  const trades = [
    fill("BUY", 100, 10, "2026-11-03T04:00:00Z"), fill("SELL", 110, 10, "2026-11-03T06:00:00Z"),
    fill("BUY", 120, 10, "2026-11-04T04:00:00Z"),
  ];
  assert.deepEqual(fillMarkers(trades, "NSE:SBIN-EQ", "2026-11-03T04:00:00Z", "2026-11-03T06:00:00Z").map((m) => m.label), ["BUY 10", "SELL 10"]);
  assert.equal(fillMarkers(trades, "NSE:SBIN-EQ").at(-1)?.tone, "entry");
  assert.deepEqual(fillMarkers(trades, "NSE:SBIN-EQ", "2026-11-03T04:00:00Z").map((m) => m.label), ["BUY 10", "SELL 10"]);
});

test("levels skip missing prices; drawdowns run from the peak", () => {
  assert.deepEqual(positionLevels({average_price: 100, hard_stop: 97, trailing_stop: null, target_price: 0}).map((l) => l.tone), ["avg", "stop"]);
  assert.deepEqual(positionLevels(undefined), []);
  assert.deepEqual(drawdowns([100, 110, 99, 120]).map((v) => +v.toFixed(3)), [0, 0, -0.1, 0]);
});

test("a short opens with SHORT and closes with COVER, red when covered above its average", () => {
  const sym = "NSE:SBIN27JANFUT";
  const marks = fillMarkers([
    fill("SELL", 800, 10, "2026-11-04T04:00:00Z", sym), fill("BUY", 776, 5, "2026-11-05T04:00:00Z", sym),
    fill("BUY", 810, 5, "2026-11-06T04:00:00Z", sym), fill("BUY", 790, 10, "2026-11-07T04:00:00Z", sym),
  ], sym);
  assert.deepEqual(marks.map((m) => [m.label, m.tone]), [["SHORT 10", "short"], ["COVER 5", "cover"], ["COVER 5", "cover_loss"], ["BUY 10", "entry"]]);
  assert.deepEqual(fillMarkers([fill("SELL", 800, 10, "2026-11-04T04:00:00Z", sym), fill("BUY", 776, 10, "2026-11-05T04:00:00Z", sym),
    fill("SELL", 790, 10, "2026-11-06T04:00:00Z", sym)], sym, "2026-11-04T04:00:00Z").map((m) => m.label), ["SHORT 10", "COVER 10"]);
});
