import assert from "node:assert/strict";
import { test } from "node:test";
import { arrowHead, axis, coilPath, directions, filled, rowHeight, story, type CoilRow } from "../src/sectorCoilMath.ts";

test("axis always includes zero and uses a round step", () => {
  const a = axis([-6.2, 41.5]);
  assert.ok(a.lo <= -6.2 && a.hi >= 41.5 && a.ticks.includes(0));
  assert.equal(a.step, 10);
  assert.deepEqual(axis([2, 3]).ticks[0] <= 0, true);
});

test("a reversal turns with a half circle on the far side, a continuation with a loop", () => {
  // up to 100, back to 40: one U-turn bulging right (sweep 1)
  const uTurn = coilPath([0, 100, 40], 10, 10);
  assert.match(uTurn, /^M0\.0,10\.0 L100\.0,10\.0 A5\.00,5\.00 0 0 1 100\.0,20\.0 L40\.0,20\.0$/);
  // down to -20, back up to 10: U-turn bulging left (sweep 0)
  assert.match(coilPath([0, -20, 10], 0, 8), / A4\.00,4\.00 0 0 0 -20\.0,8\.0 /);
  // up, then further up: an S loop that leaves and arrives heading right
  const loop = coilPath([0, 50, 80], 0, 10);
  assert.match(loop, / C59\.0,0\.0 41\.0,10\.0 50\.0,10\.0 L80\.0,10\.0$/);
});

test("flat rows keep the previous direction; nulls carry forward", () => {
  assert.deepEqual(directions([0, 10, 10, 5]), [1, 1, -1]);
  assert.deepEqual(filled([0, null, 4, null]), [0, 0, 4, 4]);
  assert.ok(rowHeight(2) === 11 && rowHeight(44) === 2.6);
  assert.match(arrowHead([0, 10, 4], 0, 10), /^M-0\.5,10\.0 /);                  // heading left at 4: tip left of it
});

test("story names the biggest give-back from a peak and the biggest gainer of the last step", () => {
  const rows: CoilRow[] = [
    { symbol: "B", label: "NIFTY 50", kind: "benchmark", values: [0, 5, 6, 8] },
    { symbol: "IT", label: "IT", kind: "sector", values: [0, 20, 31, 9.5] },
    { symbol: "EN", label: "Energy", kind: "sector", values: [0, 10, 41, 38] },
    { symbol: "PB", label: "PSU Bank", kind: "sector", values: [0, -4, -2, 6] },
  ];
  const s = story(rows, ["2025-12-31", "2026-09-30", "2026-10-06", "2026-11-06"], (d) => d.slice(5));
  assert.equal(s.title, "The big reversal");
  assert.equal(s.giver, "IT");
  assert.equal(s.gainer, "PSU Bank");
  assert.match(s.text, /On 10-06 IT was \+31\.0% since 12-31; it is \+9\.5% now\./);
});
