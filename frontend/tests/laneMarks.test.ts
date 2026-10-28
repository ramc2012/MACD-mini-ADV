import {test} from 'node:test';
import assert from 'node:assert/strict';
import {priceMove,smartMark,paperMark,currentSmartBook,paperQuote} from '../src/laneMarks.ts';
import {mergeQuote} from '../src/quoteMerge.ts';
test('day and entry change use the reference denominator and reject missing baselines',()=>{
 assert.deepEqual(priceMove(110,100),{change:10,percent:10});assert.equal(priceMove(1,0),null);assert.equal(priceMove(undefined,10),null);
});
test('smart live totals use actual remaining quantities and reconcile with row P/L',()=>{
 const p=smartMark({cash:800,positions:[{symbol:'X',quantity:2,average_price:100,last_price:100}]},{X:{ltp:120}});
 assert.equal(p.equity,1040);assert.equal(p.unrealized_pnl,40);assert.equal(p.positions[0].unrealized_pnl,40);
 assert.equal(smartMark({cash:1040,positions:[]},{X:{ltp:500}}).equity,1040);
});
test('paper liquidation uses fresh bid, adverse tick rounding and both entry/exit fees',()=>{
 const p={quantity:100,entry:100,entry_fee:1000,last_bid:101,tick:.05,last_at:'2026-10-28T04:00:00Z'};
 const policy={slippage_bps:5,fee_bps:10};
 const q={bid:110,exchange_ts_ms:Date.parse(p.last_at)+1000};
 const m=paperMark(p,policy,q);assert.equal(m.bid,110);assert.equal(m.netValue,10979.01);assert.equal(m.pnl,969.01);
 assert.equal(paperMark(p,policy,{bid:200,exchange_ts_ms:0}).bid,101);
});
test('same-session broker updates retain previous close/depth; older ticks never win',()=>{
 const a=mergeQuote(null,'X',100,1793160000000,{prevClose:90,bid:99,volume:10});
 const b=mergeQuote(a,'X',101,a.ts+1000,{volume:11,prevClose:undefined});
 assert.equal(b.prevClose,90);assert.equal(b.bid,99);assert.equal(mergeQuote(b,'X',1,a.ts),null);
 const c=mergeQuote(b,'X',101,b.ts,{bid:100});assert.equal(c.bid,100);
 const next=mergeQuote(c,'X',102,c.ts+86400000,{});assert.equal(next.prevClose,undefined);assert.equal(next.bid,undefined);
});

test('reconnect REST snapshot supersedes an older stream portfolio, newer stream fills win',()=>{
 const base={requestedAt:200,portfolio:{cash:1200,positions:[],realized_pnl:200}};
 const old={__receivedAt:100,cash:800,positions:[{symbol:'X',quantity:2,average_price:100,last_price:100}]};
 assert.equal(currentSmartBook(base,old,{}).positions.length,0);
 assert.equal(currentSmartBook(base,old,{}).realized_pnl,200);
 assert.equal(currentSmartBook(base,{...old,__receivedAt:300},{}).positions.length,1);
});

test('option liquidation cannot use the underlying cash quote when contract metadata is absent',()=>{
 const live={quotes:{'NSE:X-EQ':{bid:1000},'NSE:XCE':{bid:10}},contracts:{}};
 assert.equal(paperQuote({symbol:'X',instrument:'NSE_FO|1'},live),undefined);
 assert.equal(paperQuote({symbol:'X',instrument:'NSE_EQ|1'},live).bid,1000);
 assert.equal(paperQuote({symbol:'X',instrument:'NSE_FO|1',payload:JSON.stringify({canonical:'NSE:XCE'})},live).bid,10);
});
