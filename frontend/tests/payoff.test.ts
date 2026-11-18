import { test } from "node:test";
import assert from "node:assert/strict";
import {
  breakevens, bsGreeks, bsPrice, expiryPnl, extremes, impliedVol, netPremium, roughMargin, spotGrid, strategyGreeks, strategyPnl, yearsTo,
  type Leg,
} from "../src/payoffMath.ts";
import { haircutShare } from "../src/backofficeMath.ts";

const EXPIRY = "2026-11-23";
const NOW = new Date("2026-11-18T05:00:00Z");   // 10:30 IST
const leg = (over: Partial<Leg>): Leg => ({ id: "x", kind: "CE", side: "BUY", strike: 100, lots: 1, lotSize: 10, price: 5, iv: 20, expiry: EXPIRY, ...over });

test("Black-Scholes prices obey put-call parity and implied vol inverts the price", () => {
  const years = yearsTo(EXPIRY, NOW);
  const call = bsPrice(100, 100, years, 0.2, true), put = bsPrice(100, 100, years, 0.2, false);
  assert.ok(Math.abs(call - put - (100 - 100 * Math.exp(-0.065 * years))) < 1e-6);
  assert.ok(Math.abs((impliedVol(call, 100, 100, years, true) ?? 0) - 20) < 1e-3);
  assert.equal(impliedVol(0.01, 120, 100, years, true), null);       // below intrinsic
  assert.equal(bsPrice(110, 100, 0, 0.2, true), 10);                 // expiry: intrinsic
});

test("Greeks have the textbook signs and a call minus a put is delta one", () => {
  const years = yearsTo(EXPIRY, NOW);
  const c = bsGreeks(100, 100, years, 0.2, true), p = bsGreeks(100, 100, years, 0.2, false);
  assert.ok(c.delta > 0.4 && c.delta < 0.6 && p.delta < 0);
  assert.ok(Math.abs(c.delta - p.delta - 1) < 1e-9);
  assert.ok(c.gamma > 0 && c.theta < 0 && c.vega > 0);
});

test("a long call pays premium, loses at most it, and breaks even at strike + premium", () => {
  const legs = [leg({})];
  assert.equal(netPremium(legs), 50);
  assert.equal(expiryPnl(legs, 90), -50);
  assert.equal(expiryPnl(legs, 115), 100);
  const grid = spotGrid(100, 30, 301, [100]).map((s) => ({ spot: s, pnl: expiryPnl(legs, s) }));
  const [be] = breakevens(grid);
  assert.ok(Math.abs(be - 105) < 0.05);
  const ext = extremes(legs, 100);
  assert.equal(ext.maxProfit, null);                 // unlimited upside
  assert.ok(Math.abs((ext.maxLoss ?? 0) + 50) < 1e-6);
});

test("a short straddle has unlimited loss, capped profit and two breakevens", () => {
  const legs = [leg({ id: "a", side: "SELL", price: 4 }), leg({ id: "b", kind: "PE", side: "SELL", price: 4 })];
  assert.equal(netPremium(legs), -80);
  const ext = extremes(legs, 100);
  assert.equal(ext.maxLoss, null);
  assert.ok(Math.abs((ext.maxProfit ?? 0) - 80) < 1e-6);
  const grid = spotGrid(100, 30, 301, [100]).map((s) => ({ spot: s, pnl: expiryPnl(legs, s) }));
  const bes = breakevens(grid);
  assert.equal(bes.length, 2);
  assert.ok(Math.abs(bes[0] - 92) < 0.05 && Math.abs(bes[1] - 108) < 0.05);
});

test("a bull call spread is bounded both ways and its margin is its maximum loss", () => {
  const legs = [leg({ id: "a", strike: 100, price: 5 }), leg({ id: "b", strike: 110, side: "SELL", price: 1.5 })];
  const ext = extremes(legs, 100);
  assert.ok(Math.abs((ext.maxProfit ?? 0) - 65) < 1e-6);   // (10 - 3.5) x 10
  assert.ok(Math.abs((ext.maxLoss ?? 0) + 35) < 1e-6);
  assert.equal(roughMargin(legs, 100, 20), 35);                    // a debit spread needs its net debit only
  // the credit version (sell 100, buy 110): width 100 less the 50 received, plus the 15 paid for the wing = 65
  const credit = [leg({ id: "a", strike: 100, side: "SELL", price: 5 }), leg({ id: "b", strike: 110, price: 1.5 })];
  assert.equal(roughMargin(credit, 100, 20), 65);
  // a naked short: 20% of contract value less premium
  assert.equal(roughMargin([leg({ side: "SELL" })], 100, 20), 200 - 50);
});

test("value before expiry sits above the expiry payoff for a long option and IV raises it", () => {
  const legs = [leg({})];
  const before = strategyPnl(legs, 100, NOW), atExpiry = expiryPnl(legs, 100);
  assert.ok(before > atExpiry);
  assert.ok(strategyPnl(legs, 100, NOW, 5) > before);
  const g = strategyGreeks(legs, 100, NOW);
  assert.ok(g.delta > 0 && g.delta < 10);            // 10 units
});

test("the spread haircut share is measured against the size of the recorded net", () => {
  assert.equal(haircutShare({ net: 1000, extra: 250 }), 25);
  assert.equal(haircutShare({ net: -1000, extra: 250 }), 25);
  assert.equal(haircutShare({ net: 0, extra: 5 }), null);
});
