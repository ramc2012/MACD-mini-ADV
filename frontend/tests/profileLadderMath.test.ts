import assert from "node:assert/strict";
import { test } from "node:test";
import { bracketIndex, bracketLetters, bracketTime, ladderRows, nearestPriceIndex, priceDigits } from "../src/profileLadderMath.ts";

const levels = [
  { price: 100.10, tpo: 2, letters: "AB", volume: 20 },
  { price: 100.00, tpo: 1, letters: "C", volume: 5 },
];

test("a complete profile preserves exact tick rows, including untouched prices", () => {
  const rows = ladderRows(levels, 0.05, true);
  assert.deepEqual(rows.map((row) => [row.price, row.empty]), [
    [100.1, false], [100.05, true], [100, false],
  ]);
  assert.equal(rows[1].volume, 0);
  assert.equal(nearestPriceIndex(rows, 100.05), 1);
});

test("a sampled profile never invents zero-volume rows for omitted prices", () => {
  assert.deepEqual(ladderRows(levels, 0.05, false).map((row) => row.price), [100.1, 100]);
});

test("a malformed tick cannot create an unbounded ladder", () => {
  assert.equal(ladderRows(levels, 0.000001, true).length, 2);
});

test("TPO headings include only observed brackets and retain their clock time", () => {
  assert.deepEqual(bracketLetters([{ ...levels[0], letters: "AD" }], 1), ["A", "D"]);
  assert.deepEqual(bracketLetters([{ ...levels[0], letters: "A" }], 1), ["A"]);
  assert.deepEqual(bracketLetters([{ ...levels[0], letters: "K" }], 1), ["K"]);
  assert.equal(bracketTime(bracketIndex("K")), "14:15–14:45 IST");
  assert.equal(bracketTime(0), "09:15–09:45 IST");
  assert.equal(bracketTime(12), "15:15–15:45 IST");
});

test("prices use the tick precision published by the profile", () => {
  assert.equal(priceDigits(0.05, levels), 2);
  assert.equal(priceDigits(0.5, levels), 1);
});
