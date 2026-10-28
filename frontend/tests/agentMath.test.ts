import assert from "node:assert/strict";
import { test } from "node:test";
import { curvePath, driverText, featureLabel, pct, tone } from "../src/agentMath.ts";

test("percentages are signed and missing values are a dash", () => {
  assert.equal(pct(0.0123), "+1.2%");
  assert.equal(pct(-0.5, 0), "-50%");
  assert.equal(pct(null), "—");
  assert.equal(tone(0.1), "positive");
  assert.equal(tone(-0.1), "negative");
  assert.equal(tone(0), "");
});

test("drivers read as trader language, strongest first", () => {
  assert.equal(driverText([["u_ret_1d", 0.2], ["o_ret_intraday", -0.1], ["o_dte", 0.01]]), "+ underlying 1D, − premium today");
  assert.equal(featureLabel("unknown_feature"), "unknown_feature");
});

test("the basket curve spans the box and includes the starting level", () => {
  const path = curvePath([1, 1.1, 0.95], 100, 20);
  assert.ok(path.startsWith("M0.0,"));
  assert.ok(path.includes("L100.0,"));
  assert.equal(curvePath([1], 100, 20), "");
});

test("rupees read in lakhs and thousands, signed", async () => {
  const { rupees } = await import("../src/agentMath.ts");
  assert.equal(rupees(-543478), "-₹5.43 L");
  assert.equal(rupees(98_000), "+₹98.0k");
  assert.equal(rupees(640), "+₹640");
  assert.equal(rupees(null), "—");
});

test("the live mark reads against the entry, per lot where the lot is known", async () => {
  const { sinceEntry } = await import("../src/agentMath.ts");
  const m = sinceEntry(100, 112.5, 75);
  assert.equal(m.points, 12.5);
  assert.ok(Math.abs(m.fraction! - 0.125) < 1e-12);
  assert.equal(m.perLot, 937.5);
  assert.equal(sinceEntry(100, 90).perLot, null);
  assert.deepEqual(sinceEntry(null, 90), { points: null, fraction: null, perLot: null });
  assert.deepEqual(sinceEntry(100, 0), { points: null, fraction: null, perLot: null });
});

test('cold-start trend scores are not displayed as expected option returns',async()=>{
 const {indexPrediction}=await import('../src/agentMath.ts');
 assert.deepEqual(indexPrediction('rule_score',.04),{label:'Underlying trend',value:'+4.0%',detail:'local rule score; not expected premium return'});
});
