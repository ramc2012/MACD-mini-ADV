import assert from "node:assert/strict";
import { test } from "node:test";
import { computeDispersion, dispersionIsCurrent } from "../src/dispersionMath.ts";
import { createSequenceGate } from "../src/streamSequence.ts";
import { requestJson } from "../src/requestJson.ts";
import type { Indicator, OptionWatchRow } from "../src/types.ts";

test("partially warmed option snapshots skip null and missing indicators", () => {
  const option = (symbol: string, indicator: unknown) => ({ symbol, option_type: "CE", moneyness: "ATM", indicator }) as OptionWatchRow;
  const rows = [option("null", null), option("absent", undefined), option("ready", { timestamp: 100, macd: 1 }), option("old", { timestamp: 90, macd: 3 })];
  assert.deepEqual(computeDispersion(rows, {}), {time: 100, ceAbove: 1, peAbove: 0, ceEligible: 1, peEligible: 0, total: 4, source: "live"});
  assert.equal(computeDispersion(rows.slice(0, 2), {}), undefined);
  assert.equal(computeDispersion(rows.slice(0, 1), {null: [{timestamp: 100, macd: 2} as Indicator]})?.ceAbove, 1);
});

test("dispersion from an earlier session is historical even with complete cohort coverage", () => {
  const fridayClose = Date.parse("2026-09-25T10:00:00Z") / 1000;
  assert.equal(dispersionIsCurrent(fridayClose, 1800, Date.parse("2026-09-25T10:30:00Z")), true);
  assert.equal(dispersionIsCurrent(fridayClose, 1800, Date.parse("2026-09-27T04:00:00Z")), false);
  assert.equal(dispersionIsCurrent(undefined, 1800), false);
});

test("a sequence gap pauses deltas until an authoritative snapshot arrives", () => {
  const gate = createSequenceGate();
  assert.equal(gate({type: "tick", seq: 3}), "skip");
  assert.equal(gate({type: "snapshot", seq: 10}), "accept");
  assert.equal(gate({type: "trade", seq: 11}), "accept");
  assert.equal(gate({type: "tick", seq: 13}), "request");
  assert.equal(gate({type: "portfolio", seq: 14}), "skip");
  assert.equal(gate({type: "snapshot", seq: 20}), "accept");
  assert.equal(gate({type: "trade", seq: 19}), "skip");
  assert.equal(gate({type: "tick", seq: 21}), "accept");
  assert.equal(gate({type: "snapshot_required", seq: 22}), "request");
  assert.equal(gate({type: "snapshot", seq: 0}), "accept"); // Server restart.
  assert.equal(gate({type: "tick", seq: 1}), "accept");
});

test("HTTP failure is distinct from a successful empty result", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response("unavailable", {status: 503}));
  await assert.rejects(requestJson("http://localhost/example"), /HTTP 503/);
  t.mock.restoreAll();
  t.mock.method(globalThis, "fetch", async () => new Response("[]", {status: 200}));
  assert.deepEqual(await requestJson("http://localhost/example"), []);
});

import { matchesWatchSearch, quoteStamp } from "../src/watchMath.ts";
test("watch search matches multiple terms across contract metadata", () => {
  const row = {symbol: "NSE:NIFTY26SEP25000CE", underlying: "NIFTY", strike: 25000, option_type: "CE", expiry: "2026-09-29"} as OptionWatchRow;
  assert.equal(matchesWatchSearch(row, "  nifty 25000 ce "), true);
  assert.equal(matchesWatchSearch(row, "nifty pe"), false);
  assert.equal(matchesWatchSearch(row, ""), true);
});
test("quote age uses the Indian session date and handles missing quotes", () => {
  assert.equal(quoteStamp(undefined).label, "No quote");
  assert.equal(quoteStamp("invalid").title, "unavailable");
  assert.equal(quoteStamp("2026-09-06T20:00:00Z", "2026-09-07T03:00:00Z").old, false);
  assert.match(quoteStamp("2026-09-07T03:00:00Z", "2026-09-07T03:03:00Z").label, /aged/);
  assert.equal(quoteStamp("2026-09-07T03:00:00Z", Date.parse("2026-09-07T03:03:00Z")).aged, true);
  assert.equal(quoteStamp("2026-09-04T10:00:00Z", "2026-09-07T03:00:00Z").old, true);
});

import { orderedEquityPoints, realizedEquityPoints } from "../src/equityMath.ts";
test("research equity accumulates chronologically and combines simultaneous exits", () => {
  const trades = [{exit_time:"2026-09-07T10:00:00Z",pnl:-5}, {exit_time:"2026-09-07T09:00:00Z",pnl:10}, {exit_time:"2026-09-07T09:00:00Z",pnl:20}];
  const curve = realizedEquityPoints(trades);
  assert.deepEqual(curve.map(row=>row.value), [30,25]);
  assert.ok(curve[0].time < curve[1].time);
  assert.deepEqual(orderedEquityPoints([{time:2,value:10},{time:1,value:2},{time:2,value:11},{time:NaN,value:9}]), [{time:1,value:2},{time:2,value:11}]);
});
