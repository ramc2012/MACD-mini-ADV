import assert from "node:assert/strict";
import { test } from "node:test";
import { footprintColumns, priceRangesOverlap, priceViewport } from "../src/footprintViewport.ts";

test("wide trading range keeps exchange rows legible without changing row size", () => {
  const view = priceViewport(23_400, 23_500, 0.5, 390, 23_480);
  assert.equal(view.fullRows, 201);
  assert.equal(view.rows, 21);
  assert.equal(view.rowHeight, 390 / 21);
  assert.equal(view.topPrice - view.bottomPrice, 20 * 0.5);
  assert.ok(view.topPrice >= 23_480 && view.bottomPrice <= 23_480);
  assert.equal(view.hiddenAbove + view.hiddenBelow, 180);
});

test("price pan and zoom remain within the source range", () => {
  const high = priceViewport(100, 110, 1, 130, 500, 5);
  const low = priceViewport(100, 110, 1, 130, -500, 5);
  assert.deepEqual([high.topPrice, high.bottomPrice], [110, 106]);
  assert.deepEqual([low.topPrice, low.bottomPrice], [104, 100]);
  const all = priceViewport(100, 110, 1, 130, 106, 500);
  assert.equal(all.rows, 11);
  assert.equal(all.hiddenAbove, 0);
  assert.equal(all.hiddenBelow, 0);
});

test("a single traded price is one row and stays centered", () => {
  const view = priceViewport(100.05, 100.05, 0.05, 390, 100.05);
  assert.equal(view.rows, 1);
  assert.equal(view.rowHeight, 28);
  assert.ok(Math.abs(view.topPrice - 100.05) < 1e-8);
});

test("a stacked price band remains visible when it spans the cropped price window", () => {
  assert.equal(priceRangesOverlap(120, 80, 95, 105), true);
  assert.equal(priceRangesOverlap(120, 110, 95, 105), false);
  assert.equal(priceRangesOverlap(90, 80, 95, 105), false);
});

test("sparse and dense footprint columns stay next to the price scale", () => {
  const one = footprintColumns(1900, 1);
  assert.deepEqual(one, { left: 1748, width: 152, right: 1900, usedWidth: 152 });
  const nine = footprintColumns(1900, 9);
  assert.deepEqual(nine, { left: 532, width: 152, right: 1900, usedWidth: 1368 });
  const compact = footprintColumns(480, 8);
  assert.deepEqual(compact, { left: 0, width: 60, right: 480, usedWidth: 480 });
});
