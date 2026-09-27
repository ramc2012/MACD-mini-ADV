import assert from "node:assert/strict";
import test from "node:test";
import { observedBrokerStatus } from "../src/feedStatus.ts";

const now = Date.parse("2026-09-25T09:05:00Z");
const fresh = new Date(now - 10_000).toISOString();

test("fresh tick health clears an old stale broker event", () => {
  assert.equal(observedBrokerStatus("stale", { status: "connected", feed_alive: true }, fresh, now), "connected");
});

test("fresh health also identifies a feed that stopped after a connected event", () => {
  assert.equal(observedBrokerStatus("connected", { status: "connected", feed_alive: false }, fresh, now), "stale");
});

test("expired tokens and old or incomplete health never become a green feed", () => {
  assert.equal(observedBrokerStatus("token_expired", { status: "connected", feed_alive: true }, fresh, now), "token_expired");
  assert.equal(observedBrokerStatus("stale", { status: "connected", feed_alive: true }, new Date(now - 30_000).toISOString(), now), "stale");
  assert.equal(observedBrokerStatus("stale", { status: "connected" }, fresh, now), "stale");
});
