import assert from "node:assert/strict";
import test from "node:test";

import { asOf, bandPath, compact, extent, linePath, linear, nearestIndex, observedAt, sessionDomain, sessionTicks, strikeWindow, ticks } from "../src/optionsDeskMath.ts";

test("scales, extents and round ticks", () => {
  const x = linear([0, 10], [100, 200]);
  assert.equal(x(5), 150);
  assert.equal(linear([3, 3], [0, 10])(3), 5);
  assert.deepEqual(extent([1, null, 3], 0), [1, 3]);
  assert.deepEqual(extent([], 0), [0, 1]);
  assert.deepEqual(extent([2, 4], 0, 0), [0, 4]);
  assert.deepEqual(ticks([0, 100], 4), [0, 25, 50, 75, 100]);
  assert.deepEqual(ticks([12.3, 16.9], 4), [14, 16]);
});

test("the session runs 09:15-15:30 IST with whole-hour ticks", () => {
  const [a, b] = sessionDomain("2026-11-13");
  assert.equal(new Date(a * 1000).toISOString(), "2026-11-13T03:45:00.000Z");
  assert.equal(b - a, 375 * 60);
  const t = sessionTicks([a, b]);
  assert.equal(t.length, 6);
  assert.equal(new Date(t[0] * 1000).toISOString(), "2026-11-13T04:30:00.000Z");   // 10:00 IST
});

test("nearest and as-of lookups", () => {
  const rows = [{ ts: 10 }, { ts: 20 }, { ts: 40 }];
  assert.equal(nearestIndex(rows, 33), 2);
  assert.equal(nearestIndex(rows, 29), 1);
  assert.equal(nearestIndex([], 5), -1);
  assert.equal(asOf(rows, 39)?.ts, 20);
  assert.equal(asOf(rows, 1)?.ts, 10);
});

test("compact numbers and paths", () => {
  assert.equal(compact(123456789), "12.35 Cr");
  assert.equal(compact(-250000), "−2.50 L");
  assert.equal(compact(4200, true), "+4,200");
  assert.equal(compact(null), "—");
  assert.equal(linePath([[0, 1], [1, null], [2, 3], [3, 4]]), "M0.0,1.0M2.0,3.0L3.0,4.0");
  assert.equal(bandPath([[0, 1, 5], [2, 2, 6]]), "M0.0,1.0L2.0,2.0L2.0,6.0L0.0,5.0Z");
});

test("a pinned value does not read future snapshots", () => {
  const points = [{ ts: 10, value: 7 }, { ts: 22, value: 8 }];
  assert.equal(observedAt(points, 9), undefined);
  assert.equal(observedAt(points, 21)?.value, 7);
  assert.equal(observedAt(points, 22)?.value, 8);
  assert.equal(observedAt([], 22), undefined);
});

test("the ATM window counts actual captured strikes even when spacing is uneven", () => {
  const rows = [100, 115, 120, 150, 160].map(strike => ({ strike }));
  assert.deepEqual(strikeWindow(rows, 120, 1).map(row => row.strike), [115, 120, 150]);
  assert.deepEqual(strikeWindow(rows, 100, 2).map(row => row.strike), [100, 115, 120]);
  assert.equal(strikeWindow(rows, 120, 0), rows);
});
