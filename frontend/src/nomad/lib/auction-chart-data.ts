/** Strict chart adapters: missing observations remain gaps, never zero ticks. */
export type AuctionBar={timestamp:string;open:number;high:number;low:number;close:number;volume:number|null;delta:number|null;cumulative_delta:number|null;session_date?:string};
export type ProfileRow={price:number;count:number;letters?:string};
const finite=(v:unknown):number|null=>v==null||v===""?null:typeof v==="number"&&Number.isFinite(v)?v:null;
export function cleanChartBars(input:AuctionBar[]):AuctionBar[]{
 const map=new Map<number,AuctionBar>();
 for(const b of input||[]){const stamp=Date.parse(b.timestamp);const values=[b.open,b.high,b.low,b.close];
  if(!Number.isFinite(stamp)||values.some(v=>finite(v)==null||v<=0)||b.high<Math.max(b.open,b.close,b.low)||b.low>Math.min(b.open,b.close))continue;
  map.set(stamp,{...b,volume:finite(b.volume),delta:finite(b.delta),cumulative_delta:finite(b.cumulative_delta)});
 }
 return Array.from(map.entries()).sort(([a],[b])=>a-b).map(([,b])=>b);
}
export function profileRows(profile:unknown):ProfileRow[]{
 if(!profile||typeof profile!=="object")return [];
 const p=profile as Record<string,unknown>;const rows=p.tpo_rows??p.tpoRows;
 const source:Array<{price:unknown;count:unknown;letters?:unknown}>=Array.isArray(rows)?rows.map(r=>({price:r.price??r.level??r[0],count:r.count??r.tpo_count??r.tpos??r[1]??(typeof r.letters==="string"?r.letters.length:undefined),letters:r.letters})):Object.entries((p.tpo_counts??p.tpoCounts??{}) as Record<string,unknown>).map(([price,count])=>({price:Number(price),count,letters:(p.tpo_letters as Record<string,string>|undefined)?.[price]}));
 return source.map(r=>({price:Number(r.price),count:Number(r.count),letters:typeof r.letters==="string"?r.letters:undefined})).filter(r=>Number.isFinite(r.price)&&r.price>0&&Number.isFinite(r.count)&&r.count>0).sort((a,b)=>b.price-a.price);
}
export const chartTime=(stamp:string)=>Math.floor(Date.parse(stamp)/1000)+19800;
export const chartNumber=(v:number|null|undefined,digits=1)=>v==null||!Number.isFinite(v)?"—":new Intl.NumberFormat("en-IN",{maximumFractionDigits:digits}).format(v);

/** Coarser display bins count each observed period once, not once per source row. */
export function groupTpoRows(rows:ProfileRow[],factor:number):ProfileRow[]{
 if(factor<=1||rows.length<2||rows.some(r=>!r.letters))return rows;
 const steps=rows.slice(1).map((r,i)=>Math.abs(rows[i].price-r.price)).filter(v=>v>0);
 const step=Math.min(...steps)*factor;if(!Number.isFinite(step)||step<=0)return rows;
 const bins=new Map<number,Set<string>>();const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
 for(const row of rows){const index=Math.floor((row.price+step*1e-9)/step);const letters=bins.get(index)||new Set<string>();for(const letter of Array.from(row.letters!))letters.add(letter);bins.set(index,letters);}
 return Array.from(bins.entries()).map(([i,letters])=>({price:Number(((i+.5)*step).toFixed(8)),count:letters.size,letters:Array.from(letters).sort((a,b)=>alphabet.indexOf(a)-alphabet.indexOf(b)).join('')})).sort((a,b)=>b.price-a.price);
}
