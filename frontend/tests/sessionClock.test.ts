import assert from "node:assert/strict";
import { test } from "node:test";
import { duration, extractAuthCode, sessionState, tokenLeft } from "../src/sessionClock.ts";

const ist = (text: string) => new Date(`${text}+05:30`);

test("the session phases and their countdowns follow IST", () => {
  assert.deepEqual(sessionState(ist("2026-09-30T07:43:00")).detail, "opens in 1h 32m");
  assert.equal(sessionState(ist("2026-09-30T09:05:00")).phase, "preopen");
  assert.equal(sessionState(ist("2026-09-30T09:05:00")).detail, "opens in 10m");
  const open = sessionState(ist("2026-09-30T13:25:00"));
  assert.equal(open.phase, "open");
  assert.equal(open.detail, "closes in 2h 05m");
  assert.equal(sessionState(ist("2026-09-30T15:30:00")).phase, "closed");
});

test("after the close, weekends and holidays point at the next trading morning", () => {
  assert.equal(sessionState(ist("2026-09-30T18:00:00")).detail, "opens tomorrow 09:15");
  assert.equal(sessionState(ist("2026-10-02T18:00:00")).detail, "opens Mon 09:15");            // Friday evening
  const holiday = sessionState(ist("2026-10-02T11:00:00"), ["2026-10-02"]);
  assert.equal(holiday.phase, "holiday");
  assert.equal(holiday.detail, "opens Mon 09:15");
});

test("the auth code is found in a bare code or the whole redirect URL", () => {
  const code = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.payload.sig";
  assert.equal(extractAuthCode(code), code);
  assert.equal(extractAuthCode(`https://trade.fyers.in/api-login/redirect-uri/index.html?s=ok&code=200&auth_code=${code}&state=None`), code);
  assert.equal(extractAuthCode(`s=ok&auth_code=${code}&state=x`), code);
  assert.equal(extractAuthCode("https://trade.fyers.in/api-login/redirect-uri/index.html?s=error"), null);
  assert.equal(extractAuthCode("hello world"), null);
  assert.equal(extractAuthCode("   "), null);
});

test("durations and token time left read naturally", () => {
  assert.equal(duration(42), "42m");
  assert.equal(duration(125), "2h 05m");
  assert.equal(duration(60 * 30), "1d 6h");
  const now = ist("2026-09-30T08:00:00");
  assert.deepEqual(tokenLeft("2026-10-01T00:30:00+00:00", now).text, "22h 00m left");
  assert.equal(tokenLeft("2026-09-30T00:30:00+00:00", now).expired, true);
  assert.equal(tokenLeft(null, now).text, "no token");
});
