import assert from "node:assert/strict";
import {test} from "node:test";
import {ledgerCounts} from "../src/ledgerCountState.ts";
import {bookFillCounts} from "../src/ledgerMath.ts";
import type {Order, Trade} from "../src/types.ts";
const order = (id:string,status:string) => ({order_id:id,status} as Order);
const fill = (id:string,order_id:string,side:string,timestamp="2026-10-16T10:00:00+05:30") =>
  ({trade_id:id,order_id,side,timestamp,quantity:1} as Trade);

test("attempts, booked orders and partial fills have distinct counts and deduplicate snapshots",()=>{
  const orders=[order("a","FILLED"),order("b","REJECTED"),order("c","PENDING")];
  const trades=[fill("1","a","BUY"),fill("2","a","SELL")];
  assert.deepEqual(ledgerCounts([...orders,orders[0]], [...trades,trades[0]]),
    {orders:3,booked:1,rejected:1,other:1,trades:2,buys:1,sells:1,unlinked:0,notBooked:0,withoutFill:0});
});
test("missing or inconsistent order links are visible",()=>{
  const counts=ledgerCounts([order("a","FILLED"),order("b","REJECTED")], [fill("1","missing","BUY"),fill("2","b","SELL")]);
  assert.equal(counts.unlinked,1);assert.equal(counts.notBooked,1);assert.equal(counts.withoutFill,1);
});
test("weekend buy/sell counts retain Friday's book and exclude future fills and duplicates",()=>{
  const fills=[fill("1","a","BUY"),fill("2","b","SELL"),fill("3","c","SELL"),
    fill("old","d","BUY","2026-10-15T14:00:00+05:30"),fill("future","e","BUY","2026-10-19T10:00:00+05:30")];
  const now=Date.parse("2026-10-17T22:00:00+05:30");
  assert.deepEqual(bookFillCounts([...fills,fills[0]],now),{start:Date.parse("2026-10-16T08:00:00+05:30"),buys:1,sells:2});
  assert.equal(bookFillCounts(fills,Date.parse("2026-10-19T08:00:00+05:30")).sells,0);
});
test("holiday book rollover uses the same calendar as Closed today",()=>{
  const now=Date.parse("2026-10-19T10:00:00+05:30");
  assert.equal(bookFillCounts([fill("1","a","BUY")],now,new Set(["2026-10-19"])).buys,1);
});
