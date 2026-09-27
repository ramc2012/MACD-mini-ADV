import assert from "node:assert/strict";
import test from "node:test";
import { chartSourceStatus } from "../src/chartSource.ts";
import { quoteStamp } from "../src/watchMath.ts";
import type { Candle } from "../src/types.ts";

const now = Date.parse("2026-09-25T04:00:00Z"); // 09:30 IST
const bar: Candle = {
  symbol: "NSE:NIFTY50-INDEX", timestamp: Date.parse("2026-09-25T03:45:00Z") / 1000,
  open: 100, high: 102, low: 99, close: 101, volume: 0, closed: false,
};
const base = { bar, now, marketOpen: true, streamConnected: true, timeframeSeconds: 1800 };

test("chart source distinguishes fresh, aged, and absent exchange quotes", () => {
  assert.equal(chartSourceStatus({ ...base, quote: quoteStamp("2026-09-25T03:59:00Z", now) }).tone, "live");
  assert.match(chartSourceStatus({ ...base, quote: quoteStamp("2026-09-25T03:55:00Z", now) }).label, /Aged quote/);
  assert.match(chartSourceStatus(base).label, /No live quote/);
});

test("a fresh quote cannot hide a lagging candle stream", () => {
  const oldBar = { ...bar, timestamp: bar.timestamp - 3600 };
  assert.match(chartSourceStatus({ ...base, bar: oldBar, quote: quoteStamp("2026-09-25T03:59:00Z", now) }).label, /Bar lag/);
  assert.match(chartSourceStatus({ ...base, streamConnected: false }).label, /Stream reconnecting/);
});

test("after the session closes, the last bar is historical rather than alarming", () => {
  const closed = chartSourceStatus({ ...base, marketOpen: false, quote: quoteStamp("2026-09-24T10:00:00Z", now) });
  assert.equal(closed.tone, "muted");
  assert.match(closed.label, /closed/);
});
