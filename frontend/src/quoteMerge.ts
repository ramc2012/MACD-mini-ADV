const day=(ts:number)=>Math.floor((ts+19800000)/86400000);
/** Ignore older ticks; retain same-session depth/previous close across broker sources. */
export function mergeQuote(previous:any,symbol:string,ltp:number,ts:number,extras:Record<string,any>={}){
 if(!Number.isFinite(ts)||!Number.isFinite(ltp)||ltp<=0||previous&&ts<previous.ts)return null;
 const metadata=Object.fromEntries(Object.entries(extras).filter(([,v])=>v!=null&&Number.isFinite(v)));
 const retained=previous&&day(previous.ts)===day(ts)?previous:{};
 const next={...retained,...metadata,symbol,ltp,ts};
 if(previous&&ts===previous.ts&&Object.keys(next).every(k=>next[k]===previous[k]))return null;
 return {...next,prevLtp:previous?.ltp??null,dir:previous&&ltp>previous.ltp?'up':previous&&ltp<previous.ltp?'down':'flat',rxAt:Date.now(),coalesced:true};
}
