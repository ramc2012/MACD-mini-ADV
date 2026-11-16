import assert from "node:assert/strict";
import { test } from "node:test";
import { mcxFormingBar } from "../src/mcxChartMath.ts";
import type { Candle } from "../src/types.ts";

const open = Date.parse("2026-11-16T09:00:00+05:30") / 1000;
const minute = (offset: number, price: number, volume: number, closed = true): Candle => ({
  symbol: "MCX:GOLDM26DECFUT", timestamp: open + offset * 60,
  open: price, high: price + 1, low: price - 1, close: price, volume, closed,
});

test("MCX chart merges the freshest observation and counts each minute's volume once", () => {
  const stored = [minute(0, 11, 2), minute(30, 12, 4), minute(31, 13, 3, false)];
  const streamed = [
    { row: minute(30, 14, 9), seen: 99 },
    { row: minute(31, 16, 7), seen: 102 },
    { row: minute(32, 15, 2, false), seen: 104 },
  ];
  assert.deepEqual(mcxFormingBar(stored, streamed, 100, open, 1800), {
    ...minute(30, 12, 13, false), high: 17, low: 11, close: 15,
  });
  assert.equal(mcxFormingBar(stored, streamed, 100, open + 1800, 1800), undefined);
});

test("a daily MCX bar uses the 09:00 IST session anchor even for an evening-only session", () => {
  const evening = minute(8 * 60, 20, 9);
  assert.equal(mcxFormingBar([evening], [], 100, open - 86400, 86400)?.timestamp, open);
});
