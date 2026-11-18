import assert from "node:assert/strict";
import { test } from "node:test";
import { istStamp, upstoxCode, upstoxHeadline } from "../src/upstoxLoginMath.ts";

test("Upstox code is found in the redirect address, a bare query or the code itself", () => {
  assert.equal(upstoxCode("https://127.0.0.1:5000/callback?code=AbC123&state=s1"), "AbC123");
  assert.equal(upstoxCode("code=Zx9&state=s"), "Zx9");
  assert.equal(upstoxCode("  AbC123  "), "AbC123");
  assert.equal(upstoxCode("https://127.0.0.1:5000/callback?state=s1"), "");
  assert.equal(upstoxCode("not a code at all"), "");
  assert.equal(upstoxCode(""), "");
});

test("the card is green only while today's order token is valid", () => {
  const now = Date.parse("2026-11-18T08:00:00+05:30");
  const app = { api_key: true, secret: true, redirect_uri: true };
  assert.equal(upstoxHeadline(undefined).tone, "pending");
  assert.equal(upstoxHeadline({ fields: {} }, now).text, "Upstox app details needed once");
  assert.equal(upstoxHeadline({ fields: app }, now).text, "Not logged in to Upstox");
  assert.equal(upstoxHeadline({ fields: app, trade_token_present: true, trade_token_expired: true }, now).text, "Today's Upstox login needed");
  const live = { fields: app, trade_token_present: true, trade_token_expired: false, trade_token_expires_at: "2026-11-18T22:00:00+00:00" };
  assert.equal(upstoxHeadline(live, now).tone, "ok");
  // a page left open past the expiry turns red without waiting for the next status read
  assert.equal(upstoxHeadline(live, Date.parse("2026-11-19T04:00:00+05:30")).tone, "down");
});

test("expiry stamps are in full IST", () => {
  assert.equal(istStamp("2026-11-18T22:00:00+00:00"), "Thu, 19 Nov 2026, 03:30:00 IST");
  assert.equal(istStamp(null), "—");
});
