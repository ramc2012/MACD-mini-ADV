import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BOOK_DAY_ROLL_HOUR, IST_OFFSET_MS, closedBookStart, closedRowVisibleUntil, closedVisibleUntil, completedRoundTrips,
  distinctPositions, observedExcursion, visibleClosedRows,
} from "../src/ledgerMath.ts";
import type { ClosedPosition, Trade } from "../src/types.ts";

/** Epoch ms for an IST wall-clock moment. */
const ist = (day: string, clock: string) => {
  const [year, month, date] = day.split("-").map(Number);
  const [hour, minute] = clock.split(":").map(Number);
  return Date.UTC(year, month - 1, date, hour, minute) - IST_OFFSET_MS;
};
const iso = (day: string, clock: string) => new Date(ist(day, clock)).toISOString();

// 2026-08-28 is a Friday; 08-31 a Monday.
const FRI = "2026-08-28", SAT = "2026-08-29", SUN = "2026-08-30", MON = "2026-08-31", TUE = "2026-09-01", THU = "2026-08-27";
// Boundary clock strings derived from the rule, so moving the roll hour moves
// the probes with it instead of leaving them silently testing the wall.
const hh = (h: number) => String(h).padStart(2, "0");
const ROLL = `${hh(BOOK_DAY_ROLL_HOUR)}:00`;
const BEFORE = `${hh(BOOK_DAY_ROLL_HOUR - 1)}:59`;
const AFTER = `${hh(BOOK_DAY_ROLL_HOUR)}:01`;
const WELL_AFTER = `${hh(BOOK_DAY_ROLL_HOUR + 1)}:00`;

test("the book rolls at 08:00 IST", () => {
  // Pinned deliberately. The roll must sit AFTER the session roll, which
  // fires on the first tick of the new day -- Fyers republishes the prior
  // close well before the bell, so a 06:00 boundary was pruning the previous
  // day's round trips at ~07:0x, before anyone had read them.
  assert.equal(BOOK_DAY_ROLL_HOUR, 8);
});

test("closedBookStart rolls at the roll hour, not midnight", () => {
  assert.equal(closedBookStart(ist(TUE, BEFORE)), ist(MON, ROLL));
  assert.equal(closedBookStart(ist(TUE, ROLL)), ist(TUE, ROLL));
  assert.equal(closedBookStart(ist(TUE, "23:00")), ist(TUE, ROLL));
  assert.equal(closedBookStart(ist(TUE, "00:30")), ist(MON, ROLL));
});

test("closedBookStart steps back over the weekend to Friday's roll", () => {
  assert.equal(closedBookStart(ist(SAT, WELL_AFTER)), ist(FRI, ROLL));
  assert.equal(closedBookStart(ist(SUN, "12:00")), ist(FRI, ROLL));
  assert.equal(closedBookStart(ist(MON, BEFORE)), ist(FRI, ROLL));
  assert.equal(closedBookStart(ist(MON, WELL_AFTER)), ist(MON, ROLL));
});

test("closedVisibleUntil mirrors the backend: the roll hour on the next weekday after the exit's IST date", () => {
  assert.equal(closedVisibleUntil(ist(THU, "15:20")), ist(FRI, ROLL));
  assert.equal(closedVisibleUntil(ist(FRI, "15:20")), ist(MON, ROLL));
  assert.equal(closedVisibleUntil(ist(MON, "09:20")), ist(TUE, ROLL));
  // The IST date decides, even when the UTC date differs (after 18:30 UTC).
  assert.equal(closedVisibleUntil(ist(FRI, "23:50")), ist(MON, ROLL));
});

const slice = (extra: Partial<ClosedPosition>): ClosedPosition => ({
  closed_id: "c1", position_id: "NSE:NIFTY2690224500CE|" + iso(FRI, "10:00"), symbol: "NSE:NIFTY2690224500CE", lane: "macd", side: "LONG",
  quantity: 75, lots: 1, lot_size: 75, entry_time: iso(FRI, "10:00"), entry_price: 100, exit_time: iso(FRI, "15:20"), exit_price: 110,
  gross_pnl: 750, fees: 0, realized_pnl: 750, return_pct: 10, max_price: 115, min_price: 98, max_return_pct: 15, min_return_pct: -2,
  exit_reason: "eod", partial: false, remaining_quantity: 0, exit_trade_id: "t1", visible_until: iso(MON, ROLL), ...extra,
});

test("a Friday exit stays on the desk through the weekend and leaves at Monday's roll", () => {
  const rows = [slice({})];
  assert.equal(visibleClosedRows(rows, ist(FRI, "23:00")).length, 1);
  assert.equal(visibleClosedRows(rows, ist(SAT, WELL_AFTER)).length, 1);
  assert.equal(visibleClosedRows(rows, ist(MON, BEFORE)).length, 1);
  assert.equal(visibleClosedRows(rows, ist(MON, ROLL)).length, 0);
  assert.equal(visibleClosedRows(rows, ist(MON, AFTER)).length, 0);
});

test("a Thursday exit is gone by Friday's roll", () => {
  const rows = [slice({ exit_time: iso(THU, "15:20"), visible_until: iso(FRI, ROLL) })];
  assert.equal(visibleClosedRows(rows, ist(THU, "23:00")).length, 1);
  assert.equal(visibleClosedRows(rows, ist(FRI, BEFORE)).length, 1);
  assert.equal(visibleClosedRows(rows, ist(FRI, WELL_AFTER)).length, 0);
});

test("the backend's visible_until wins; a row without one gets the same rule client-side", () => {
  assert.equal(closedRowVisibleUntil(slice({ visible_until: iso(TUE, ROLL) })), ist(TUE, ROLL));
  assert.equal(closedRowVisibleUntil({ exit_time: iso(FRI, "15:20"), visible_until: "" }), ist(MON, ROLL));
  assert.equal(closedRowVisibleUntil({ exit_time: iso(FRI, "15:20"), visible_until: undefined as unknown as string }), ist(MON, ROLL));
});

test("distinctPositions counts a staged exit once", () => {
  const rows = [slice({ closed_id: "a", partial: true, remaining_quantity: 75 }), slice({ closed_id: "b" }), slice({ closed_id: "c", position_id: "other|x" })];
  assert.equal(distinctPositions(rows), 2);
  assert.equal(distinctPositions([]), 0);
});

test("observedExcursion treats a zero price as unobserved, not as never moved", () => {
  assert.equal(observedExcursion(0, 0), null);
  assert.equal(observedExcursion(null, 0), null);
  assert.equal(observedExcursion(undefined, undefined), null);
  assert.equal(observedExcursion(115, 15), 15);
  assert.equal(observedExcursion(98, -2), -2);
  assert.equal(observedExcursion(100, 0), 0);
});

const fill = (side: "BUY" | "SELL", clock: string, quantity: number, price: number, extra: Partial<Trade> = {}): Trade => ({
  trade_id: `${side}-${clock}`, symbol: "NSE:NIFTY2690224500CE", side, quantity, lots: quantity / 75, lot_size: 75, price, timestamp: iso(FRI, clock), ...extra,
});

test("completedRoundTrips counts one flat cycle with entry and exit fills", () => {
  const fills = [fill("BUY", "10:00", 75, 100), fill("BUY", "10:30", 75, 110), fill("SELL", "11:00", 75, 120), fill("SELL", "11:30", 75, 90)];
  assert.deepEqual(completedRoundTrips(fills.slice(0, 3)), []);
  const rounds = completedRoundTrips(fills);
  assert.equal(rounds.length, 1);
  assert.equal(rounds[0].entryPrice, 105);
  assert.equal(rounds[0].exitPrice, 105);
  assert.equal(rounds[0].pnl, 0);
  assert.equal(rounds[0].lots, 2);
  assert.equal(rounds[0].entryCount, 2);
  assert.equal(rounds[0].exitCount, 2);
  assert.equal(rounds[0].heldMs, 5_400_000);
});

test("completed trades include fees, additions after a partial exit, and separate reentries", () => {
  const fills = [fill("BUY", "10:00", 150, 100, {fees: 20}), fill("SELL", "10:30", 75, 120, {fees: 20}), fill("BUY", "11:00", 75, 90, {fees: 20}), fill("SELL", "11:30", 150, 110, {fees: 20})];
  const first = completedRoundTrips([...fills, fills[0]])[0];
  assert.equal(first.pnl, 9000 + 16500 - 15000 - 6750 - 80);
  assert.equal(first.entryCount, 2);
  assert.equal(first.exitCount, 2);
  const rounds = completedRoundTrips([...fills, fill("BUY", "12:00", 75, 100), fill("SELL", "12:30", 75, 101)]);
  assert.equal(rounds.length, 2);
  assert.equal(rounds[1].pnl, 75);
});

test("completedRoundTrips ignores a SELL with nothing to close and sorts fills by time", () => {
  const rounds = completedRoundTrips([fill("SELL", "11:00", 75, 120), fill("BUY", "10:00", 75, 100)]);
  assert.equal(rounds.length, 1);
  assert.equal(rounds[0].exitPrice, 120);
  assert.equal(completedRoundTrips([fill("SELL", "11:00", 75, 120)]).length, 0);
});

test("completedRoundTrips does not invent a lot size", () => {
  const rounds = completedRoundTrips([fill("BUY", "10:00", 75, 100, { lot_size: 0 }), fill("SELL", "11:00", 75, 120, { lot_size: 0 })]);
  assert.equal(rounds[0].lotSize, 0);
  assert.equal(rounds[0].lots, 0);
});

// 14 Sep 2026 (Monday) was an NSE trading holiday: Ganesh Chaturthi.
const HOLIDAY = new Set(["2026-09-14"]);
const FRI_BEFORE_HOLIDAY = "2026-09-11", HOLIDAY_MON = "2026-09-14", TUE_AFTER_HOLIDAY = "2026-09-15";

test("a Friday exit survives a Monday exchange holiday", () => {
  const exit = ist(FRI_BEFORE_HOLIDAY, "15:20");
  assert.equal(closedVisibleUntil(exit, HOLIDAY), ist(TUE_AFTER_HOLIDAY, ROLL));
  // Without the calendar it would have rolled off on the holiday morning.
  assert.equal(closedVisibleUntil(exit), ist(HOLIDAY_MON, ROLL));
});

test("the book start skips back over a holiday to the last session's roll", () => {
  assert.equal(closedBookStart(ist(TUE_AFTER_HOLIDAY, BEFORE), HOLIDAY), ist(FRI_BEFORE_HOLIDAY, ROLL));
  assert.equal(closedBookStart(ist(HOLIDAY_MON, "12:00"), HOLIDAY), ist(FRI_BEFORE_HOLIDAY, ROLL));
  assert.equal(closedBookStart(ist(TUE_AFTER_HOLIDAY, WELL_AFTER), HOLIDAY), ist(TUE_AFTER_HOLIDAY, ROLL));
});

test("a row without a backend stamp uses the holiday calendar too", () => {
  const row = { exit_time: iso(FRI_BEFORE_HOLIDAY, "15:20"), visible_until: "" };
  assert.equal(visibleClosedRows([row], ist(HOLIDAY_MON, "09:00"), HOLIDAY).length, 1);
  assert.equal(visibleClosedRows([row], ist(TUE_AFTER_HOLIDAY, WELL_AFTER), HOLIDAY).length, 0);
});
