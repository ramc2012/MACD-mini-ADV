import assert from "node:assert/strict";
import { test } from "node:test";
import { atrPct, barsSinceSignChange, betaCorrelation, ema, percentileRank, rangePosition, rollingVolatility, sessionReturn, volatilityRegime, type Bar } from "../src/quantMath.ts";

const DAY = 86_400;
const start = Date.UTC(2026, 8, 1, 3, 45) / 1000;        // 09:15 IST
const bars = (closes: number[], perDay = 75, step = 300): Bar[] => closes.map((close, i) => ({
  timestamp: start + Math.floor(i / perDay) * DAY + (i % perDay) * step,
  open: close, high: close * 1.001, low: close * 0.999, close, volume: 100,
}));

test("ema seeds with a simple mean and then smooths", () => {
  const out = ema([1, 2, 3, 4, 5], 3);
  assert.deepEqual(out.slice(0, 2), [null, null]);
  assert.equal(out[2], 2);
  assert.equal(out[3], 3);
});

test("percentile rank needs history and counts values strictly below", () => {
  const history = Array.from({ length: 100 }, (_, i) => i);
  assert.equal(percentileRank(history, 50), 50);
  assert.equal(percentileRank(history.slice(0, 10), 5), null);
  assert.equal(volatilityRegime(95), "extreme");
  assert.equal(volatilityRegime(10), "compressed");
});

test("bars since the last sign change", () => {
  assert.equal(barsSinceSignChange([-1, -2, 1, 2, 3]), 2);
  assert.equal(barsSinceSignChange([1, 2, 3]), null);
  assert.equal(barsSinceSignChange([-1, 1]), 0);
});

test("returns count sessions, not bars", () => {
  const closes = Array.from({ length: 75 * 6 }, (_, i) => 100 + Math.floor(i / 75) * 10);
  const series = bars(closes);
  assert.ok(Math.abs(sessionReturn(series, 1)! - (150 / 140 - 1) * 100) < 1e-9);
  assert.ok(Math.abs(sessionReturn(series, 5)! - 50) < 1e-9);
  assert.equal(sessionReturn(series, 20), null);
});

test("beta and correlation against a benchmark", () => {
  const bench = Array.from({ length: 200 }, (_, i) => 100 * (1 + 0.001 * Math.sin(i)));
  const stock = bench.map((v) => 100 * (1 + 2 * (v / 100 - 1)));
  const result = betaCorrelation(bars(stock, 200), bars(bench, 200));
  assert.ok(result.beta! > 1.9 && result.beta! < 2.1);
  assert.ok(result.correlation! > 0.99);
});

test("volatility, ATR and range position", () => {
  const closes = Array.from({ length: 150 }, (_, i) => 100 + (i % 2 ? 1 : -1));
  const series = bars(closes);
  const vol = rollingVolatility(series, 20, 18_750);
  assert.equal(vol[5], null);
  assert.ok(vol[149]! > 0);
  assert.ok(atrPct(series)! > 1.9);
  assert.equal(rangePosition(bars([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])), 100);
});
