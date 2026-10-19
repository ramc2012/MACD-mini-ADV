import test from "node:test";
import assert from "node:assert/strict";
import { indexPrediction } from "../src/agentMath.ts";

test("scorecard votes are never formatted as expected percentage returns", () => {
  const value = indexPrediction("scorecard", 1, 1);
  assert.equal(value.label, "Net score");
  assert.equal(value.value, "+1");
  assert.equal(indexPrediction("scorecard", -2, -2).value, "-2");
  assert.equal(indexPrediction("scorecard", null).value, "—");
});

test("return models keep fractional-return units", () => {
  const value = indexPrediction("return", 0.125, 0.05);
  assert.equal(value.value, "+12.5%");
  assert.equal(value.detail, "edge over other side +5.0%");
});
