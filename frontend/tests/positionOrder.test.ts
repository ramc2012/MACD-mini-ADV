import assert from "node:assert/strict";
import test from "node:test";

import { newestFirst } from "../src/positionOrder.ts";

test("the last entered position comes first", () => {
  const rows = [
    { symbol: "A", opened_at: "2026-11-05T05:50:20+00:00" },
    { symbol: "B", opened_at: "2026-11-11T08:46:51+00:00" },
    { symbol: "C", opened_at: "2026-11-08T09:47:11+00:00" },
  ];
  assert.deepEqual(newestFirst(rows).map((r) => r.symbol), ["B", "C", "A"]);
  assert.deepEqual(rows.map((r) => r.symbol), ["A", "B", "C"]);           // the input is not reordered
});

test("rows without an entry time go last and keep their order", () => {
  const rows = [
    { symbol: "X", opened_at: null },
    { symbol: "A", opened_at: "2026-11-10T04:00:00Z" },
    { symbol: "Y", opened_at: "" },
    { symbol: "B", opened_at: "2026-11-11T04:00:00Z" },
  ];
  assert.deepEqual(newestFirst(rows).map((r) => r.symbol), ["B", "A", "X", "Y"]);
});

test("another entry-time field can be named", () => {
  const rows = [{ id: 1, entry_time: "2026-11-10T04:00:00Z" }, { id: 2, entry_time: "2026-11-11T04:00:00Z" }];
  assert.deepEqual(newestFirst(rows, (r) => r.entry_time).map((r) => r.id), [2, 1]);
});
