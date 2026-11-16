import assert from "node:assert/strict";
import test from "node:test";

import { frontNiftyFuture, niftyFirst } from "../src/defaults.ts";

test("the front NIFTY future is the nearest month, not BANKNIFTY or FINNIFTY", () => {
  const symbols = ["NSE:BANKNIFTY26NOVFUT", "NSE:NIFTY26DECFUT", "NSE:FINNIFTY26NOVFUT", "NSE:NIFTY27JANFUT", "NSE:NIFTY26NOVFUT", "NSE:SBIN-EQ"];
  assert.equal(frontNiftyFuture(symbols), "NSE:NIFTY26NOVFUT");
  assert.equal(frontNiftyFuture(["NSE:NIFTY27JANFUT", "NSE:NIFTY26DECFUT"]), "NSE:NIFTY26DECFUT");
  assert.equal(frontNiftyFuture(["NSE:SBIN-EQ"]), undefined);
});

test("lists open on the NIFTY 50 index when they have it", () => {
  assert.equal(niftyFirst(["NSE:ABB-EQ", "NSE:NIFTY50-INDEX"]), "NSE:NIFTY50-INDEX");
  assert.equal(niftyFirst(["NSE:ABB-EQ", "NSE:SBIN-EQ"]), "NSE:ABB-EQ");
  assert.equal(niftyFirst([]), "");
});
