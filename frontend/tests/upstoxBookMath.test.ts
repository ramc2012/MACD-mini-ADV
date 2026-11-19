import assert from "node:assert/strict";
import { test } from "node:test";
import { bookStats, filterBook, instrumentOf, netCurve, netReturnPct, type BookTrade } from "../src/upstoxBookMath.ts";

const c = (total: number) => ({ total, brokerage: total / 2, stt: total / 4, gst: total / 8, stamp_duty: 0, exchange: total / 8 });
const book: BookTrade[] = [
  { symbol: "NSE:TRENT26NOV3200CE", quantity: 225, average_price: 53.85, exit_price: 64.65, realized: 2430, charges: c(60), net_realized: 2370,
    closed_at: "2026-11-17T12:48:00+05:30", initiated_by: "lane", closed_by: "lane" },
  { symbol: "NSE:IEX26NOV120CE", quantity: 4350, average_price: 0.81, exit_price: 0.84, realized: 130.5, charges: c(140), net_realized: -9.5,
    closed_at: "2026-11-18T14:04:00+05:30", initiated_by: "lane", closed_by: "lane" },
  { symbol: "BSE:SENSEX26NOV82000CE", quantity: 20, average_price: 998.4, exit_price: 989, realized: -188, charges: c(88), net_realized: -276,
    closed_at: "2026-11-19T13:15:00+05:30", initiated_by: "lane", closed_by: "lane" },
  { symbol: "NSE:VOLTAS26NOV1160CE", quantity: 1500, bought: 1500, average_price: 19.44, exit_price: 26, realized: 9840, charges: c(100),
    closed_at: "2026-11-17T09:48:00+05:30", initiated_by: "owner", closed_by: "owner" },
  { symbol: "NSE:HINDUNILVR26DEC2000CE", quantity: 300, average_price: 68.45, exit_price: 103, realized: 10365, charges_pending: true,
    closed_at: "2026-11-19T12:44:00+05:30", initiated_by: "lane", closed_by: "owner" },
];

test("index options are told from stock options by the underlying", () => {
  assert.equal(instrumentOf("BSE:SENSEX26NOV82000CE"), "INDEX");
  assert.equal(instrumentOf("NSE:NIFTY26N1024000CE"), "INDEX");
  assert.equal(instrumentOf("NSE:NIFTYNXT5026NOV70000CE"), "INDEX");
  assert.equal(instrumentOf("NSE:TRENT26NOV3200CE"), "STOCK");
  assert.deepEqual(filterBook(book, "INDEX", "ALL").map((r) => r.symbol), ["BSE:SENSEX26NOV82000CE"]);
  assert.equal(filterBook(book, "STOCK", "OWNER").length, 1);
  assert.equal(filterBook(book, "STOCK", "LANE").length, 3);
});

test("every figure is after charges: a small gross win eaten by charges counts as a loss", () => {
  const s = bookStats(book);
  assert.equal(s.count, 5);
  assert.equal(s.wins, 3);                        // TRENT, VOLTAS, HINDUNILVR (charges not read yet: net = gross)
  assert.equal(s.losses, 2);                      // IEX made +130.5 gross, -9.5 after charges
  assert.equal(s.gross, 2430 + 130.5 - 188 + 9840 + 10365);
  assert.equal(s.charges.total, 60 + 140 + 88 + 100);
  assert.equal(Math.round(s.net * 100) / 100, Math.round((s.gross - s.charges.total) * 100) / 100);
  assert.equal(s.pending, 1);
  assert.deepEqual([s.openedByLane, s.openedByOwner, s.closedByLane, s.closedByOwner], [4, 1, 3, 2]);
  assert.equal(s.best, 10365);
  assert.equal(s.worst, -276);
  assert.ok(Math.abs((s.profitFactor ?? 0) - (2370 + 9740 + 10365) / 285.5) < 1e-9);
  assert.ok(Math.abs((netReturnPct(book[3]) ?? 0) - 9740 / (1500 * 19.44) * 100) < 1e-9);
});

test("the equity curve steps by net P&L in closing order and ends with the open P&L", () => {
  const curve = netCurve(book.slice(0, 3), 25_000, "2026-11-08T14:00:00+05:30", "2026-11-19T22:00:00+05:30", 975);
  assert.deepEqual(curve.map((p) => Math.round(p.equity * 100) / 100), [25_000, 27_370, 27_360.5, 27_084.5, 28_059.5]);
  assert.deepEqual(bookStats([]).winRate, null);
});
