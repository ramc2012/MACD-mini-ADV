import assert from "node:assert/strict";
import { test } from "node:test";
import { istDay, istStamp } from "../src/istTime.ts";

test("journal dates remain ISO at the IST midnight boundary regardless of locale formatting", () => {
  assert.equal(istDay("2026-10-28T18:29:59Z"), "2026-10-28");
  assert.equal(istDay("2026-10-28T18:30:00Z"), "2026-10-29");
  assert.equal(istDay("2026-12-31T18:30:00Z"), "2027-01-01");
});

test("owner-facing timestamps include the full IST date and seconds", () => {
  assert.equal(istStamp("2026-10-29T10:00:04Z"), "2026-10-29 15:30:04 IST");
  assert.equal(istStamp(null), "—");
  assert.equal(istStamp("invalid"), "—");
});
