import {useSyncExternalStore} from 'react';
import {mergeQuote} from '../../quoteMerge';
import type {Tick} from '../../types';
import {dataScope} from '../lib/api';
export type Quote={symbol:string;ltp:number;prevLtp:number|null;dir:'up'|'down'|'flat';ts:number;rxAt:number;coalesced:boolean;bid?:number;ask?:number;bidQty?:number;askQty?:number;volume?:number;prevClose?:number};
const values=new Map<string,Quote>();const listeners=new Set<()=>void>();const channels={macd:false,mi:false};let connected=false;
function put(symbol:string,ltp:number,ts:number,extras:Partial<Quote>={}){const next=mergeQuote(values.get(symbol),symbol,ltp,ts,extras);if(!next)return false;values.set(symbol,next as Quote);return true;}
function notify(changed:boolean){const next=channels.macd||channels.mi;if(connected!==next){connected=next;changed=true;}if(changed)listeners.forEach(fn=>fn());}
export function updateQuotes(ticks:Record<string,Tick>,online:boolean){channels.macd=online;let changed=false;for(const [symbol,t] of Object.entries(ticks))changed=put(symbol,t.ltp,Date.parse(t.timestamp),{volume:t.volume,prevClose:t.prev_close})||changed;notify(changed);}
export function updateSharedQuotes(ticks:Record<string,any>,online:boolean){channels.mi=online;let changed=false;for(const [symbol,t] of Object.entries(ticks))changed=put(symbol,t.ltp,t.exchange_ts_ms,{prevClose:t.prev_close,volume:t.cum_volume,bid:t.bid,ask:t.ask,bidQty:t.bid_qty,askQty:t.ask_qty})||changed;notify(changed);}
const subscribe=(fn:()=>void)=>{listeners.add(fn);return()=>{listeners.delete(fn)};};
export function useQuote(symbol?:string|null){return useSyncExternalStore(subscribe,()=>dataScope()==='shadow'&&symbol?values.get(symbol):undefined,()=>undefined);}
let knownSymbols:string[]=[];
export function useKnownSymbols(){return useSyncExternalStore(subscribe,()=>{if(knownSymbols.length!==values.size)knownSymbols=[...values.keys()];return knownSymbols;},()=>knownSymbols);}
export function useQuotesConnection(){return useSyncExternalStore(subscribe,()=>connected,()=>false);}
