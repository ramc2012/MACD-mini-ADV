import {useSyncExternalStore} from 'react';
import type {Tick} from '../../types';
import {dataScope} from '../lib/api';
export type Quote={symbol:string;ltp:number;prevLtp:number|null;dir:'up'|'down'|'flat';ts:number;rxAt:number;coalesced:boolean;bid?:number;ask?:number;bidQty?:number;askQty?:number;volume?:number};
const values=new Map<string,Quote>(); const listeners=new Set<()=>void>(); let connected=false;
export function updateQuotes(ticks:Record<string,Tick>,online:boolean){let changed=connected!==online;connected=online;for(const [symbol,tick] of Object.entries(ticks)){const ts=Date.parse(tick.timestamp);const previous=values.get(symbol);if(previous?.ts===ts&&previous?.ltp===tick.ltp)continue;values.set(symbol,{symbol,ltp:tick.ltp,volume:tick.volume,prevLtp:previous?.ltp??null,dir:previous&&tick.ltp>previous.ltp?'up':previous&&tick.ltp<previous.ltp?'down':'flat',ts,rxAt:Date.now(),coalesced:true});changed=true;}if(changed)listeners.forEach(fn=>fn());}
const subscribe=(fn:()=>void)=>{listeners.add(fn);return()=>{listeners.delete(fn)};};
export function useQuote(symbol?:string|null){return useSyncExternalStore(subscribe,()=>dataScope()==='shadow'&&symbol?values.get(symbol):undefined,()=>undefined);}
let knownSymbols:string[]=[];
export function useKnownSymbols(){return useSyncExternalStore(subscribe,()=>{if(knownSymbols.length!==values.size)knownSymbols=[...values.keys()];return knownSymbols;},()=>knownSymbols);}
export function useQuotesConnection(){return useSyncExternalStore(subscribe,()=>connected,()=>false);}
