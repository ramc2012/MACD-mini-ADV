/** Display valuation only. Ledger quantities, cash and costs remain authoritative. */
export function priceMove(mark:number|undefined,reference:number|undefined){
 if(!(Number(mark)>0)||!(Number(reference)>0))return null;
 const change=Number(mark)-Number(reference);return {change,percent:change/Number(reference)*100};
}
export function smartMark(portfolio:any,ticks:Record<string,{ltp:number}>){
 const positions=(portfolio.positions??[]).map((p:any)=>{const last_price=ticks[p.symbol]?.ltp>0?ticks[p.symbol].ltp:p.last_price;return {...p,last_price,unrealized_pnl:(last_price-p.average_price)*p.quantity};});
 const market_value=positions.reduce((v:number,p:any)=>v+p.quantity*p.last_price,0);
 return {...portfolio,positions,market_value,equity:portfolio.cash+market_value,unrealized_pnl:positions.reduce((v:number,p:any)=>v+p.unrealized_pnl,0)};
}
export function paperMark(position:any,policy:any,quote?:any){
 const newer=quote?.exchange_ts_ms>=Date.parse(position.last_at??'')&&quote?.bid>0;
 const bid=newer?quote.bid:position.last_bid;
 const tick=position.tick||0.05;
 const exit=Math.floor((bid*(1-policy.slippage_bps/10000)+1e-9)/tick)*tick;
 const notional=Math.round(exit*position.quantity*100),fee=Math.round(notional*policy.fee_bps/10000);
 return {bid,netValue:(notional-fee)/100,pnl:(notional-fee-Math.round(position.entry*position.quantity*100)-position.entry_fee)/100};
}
export function currentSmartBook(base:any,stream:any,ticks:Record<string,{ltp:number}>){
 const stored=base?.portfolio;
 const fresh=stream?.__receivedAt>=(base?.requestedAt??0)?stream:undefined;
 if(!stored&&!fresh)return null;
 const book=fresh?{...fresh,positions:fresh.positions.map((p:any)=>({...stored?.positions.find((x:any)=>x.symbol===p.symbol&&x.position_id===p.position_id),...p}))}:stored;
 return smartMark(book,ticks);
}
export function paperQuote(position:any,live:any){
 let payload=position.payload;
 try{if(typeof payload==='string')payload=JSON.parse(payload);}catch{payload={};}
 // An option's mark must never fall back to its underlying cash price.
 const canonical=live.contracts?.[position.instrument]?.canonical??payload?.canonical??
   (position.instrument?.startsWith('NSE_EQ|')?`NSE:${position.symbol}-EQ`:undefined);
 return canonical?live.quotes[canonical]:undefined;
}
