"use client";
import {useEffect,useMemo,useRef,useState} from "react";
import {profileRows,groupTpoRows,chartNumber,type ProfileRow} from "@/lib/auction-chart-data";
import {CHART} from "./chartTheme";
export type TpoLevel=ProfileRow;
export const normalizeTpo=profileRows;
const positive=(v:unknown)=>v!=null&&Number.isFinite(Number(v))&&Number(v)>0?Number(v):null;

// Full canonical time-at-price distribution; no close-price volume surrogate.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function MarketProfileChart({profile,lastPrice,height=460,prior=null,showLegend=true}:{profile:any;lastPrice?:number|null;height?:number;prior?:{vah?:number|null;val?:number|null;poc?:number|null}|null;showLegend?:boolean}){
 const sourceRows=useMemo(()=>profileRows(profile),[profile]);
 const [grouping,setGrouping]=useState(1);
 const rows=useMemo(()=>groupTpoRows(sourceRows,grouping),[sourceRows,grouping]);
 const wrapper=useRef<HTMLDivElement>(null);const [width,setWidth]=useState(600);
 useEffect(()=>{const element=wrapper.current;if(!element)return;const observer=new ResizeObserver(()=>setWidth(Math.max(480,element.clientWidth)));observer.observe(element);return()=>observer.disconnect();},[rows.length>0]);
 const [mode,setMode]=useState<"distribution"|"letters"|"split">("distribution");
 const [rowPixels,setRowPixels]=useState(0);const [selected,setSelected]=useState<number|null>(null);
 const [showPrior,setShowPrior]=useState(true);const [showIb,setShowIb]=useState(true);
 const control="rounded border border-bg-border px-2 py-1 text-[10px] text-text-secondary hover:bg-bg-hover focus-visible:ring-2 focus-visible:ring-accent-blue";
 if(!rows.length)return <div role="status" className="flex items-center justify-center rounded-lg border border-dashed border-bg-border p-6 text-xs text-text-muted" style={{height}}>Canonical TPO distribution unavailable for this session.</div>;
 const alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
 const periods=Array.from(new Set(rows.flatMap(r=>Array.from(r.letters||"")))).sort((a,b)=>alphabet.indexOf(a)-alphabet.indexOf(b));
 const hasLetters=periods.length>0;const displayMode=hasLetters?mode:"distribution";
 const left=76,right=118,plot=width-left-right;
 const rowH=rowPixels|| (displayMode==="distribution"?Math.max(.25,(height-48)/rows.length):18);
 const svgHeight=Math.max(height-48,rows.length*rowH+28);const max=Math.max(...rows.map(r=>r.count),1);
 const y=(i:number)=>14+(i+.5)*rowH;
 const closest=(p:number)=>rows.reduce((best,r,i)=>Math.abs(r.price-p)<Math.abs(rows[best].price-p)?i:best,0);
 const poc=positive(profile.poc),vah=positive(profile.vah),val=positive(profile.val);
 const ibh=positive(profile.initial_balance_high??profile.ib_high??profile.ib?.high),ibl=positive(profile.initial_balance_low??profile.ib_low??profile.ib?.low);
 const previous=prior??profile.prior;
 const references=[{p:poc,label:"POC",color:CHART.amber},{p:vah,label:"VAH",color:CHART.blue},{p:val,label:"VAL",color:CHART.blue},{p:positive(lastPrice),label:"Last",color:CHART.text},...(showPrior?[{p:positive(previous?.poc),label:"Prior POC",color:CHART.violet},{p:positive(previous?.vah),label:"Prior VAH",color:CHART.violet},{p:positive(previous?.val),label:"Prior VAL",color:CHART.violet}]:[])].filter(r=>r.p!=null&&r.p<=sourceRows[0].price&&r.p>=sourceRows[sourceRows.length-1].price);
 const focus=selected==null?null:rows[Math.min(selected,rows.length-1)];
 return <div ref={wrapper} className="space-y-3" aria-label="Interactive Market Profile">
  <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex gap-1" role="group" aria-label="Profile display mode">{([['distribution','Distribution'],['letters','TPO letters'],['split','Split periods']] as const).map(([key,label])=><button key={key} disabled={key!=="distribution"&&!hasLetters} aria-pressed={displayMode===key} onClick={()=>{setMode(key);if(key!=="distribution"&&sourceRows.length>150&&grouping===1)setGrouping(sourceRows.length>500?10:5);setRowPixels(0);}} className={`${control} ${displayMode===key?"border-accent-blue/60 bg-accent-blue/10 text-accent-blue":""} disabled:opacity-30`}>{label}</button>)}</div><div className="flex gap-1"><button className={control} onClick={()=>setRowPixels(Math.max(displayMode==="distribution"?0.25:10,rowH-2))} aria-label="Compress profile rows">−</button><button className={control} onClick={()=>setRowPixels(Math.min(28,rowH+2))} aria-label="Expand profile rows">+</button><button className={control} onClick={()=>{setRowPixels(0);setSelected(null);}}>Fit</button></div></div>
  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-text-muted"><span>{rows.length} price rows · {rows.reduce((n,r)=>n+r.count,0)} TPOs{grouping>1?" · grouped display":""}</span><label className="flex items-center gap-1">Row size<select aria-label="Profile row grouping" value={grouping} onChange={e=>{setGrouping(Number(e.target.value));setRowPixels(0);}} className="rounded border border-bg-border bg-bg-primary p-1">{[1,5,10,20].map(n=><option key={n} value={n} disabled={n>1&&sourceRows.some(r=>!r.letters)}>{n}×</option>)}</select></label><label className="flex items-center gap-1"><input type="checkbox" checked={showIb} onChange={e=>setShowIb(e.target.checked)}/>Initial balance</label><label className="flex items-center gap-1"><input type="checkbox" checked={showPrior} onChange={e=>setShowPrior(e.target.checked)}/>Prior value</label><span className="ml-auto font-mono text-text-secondary">{focus?`${chartNumber(focus.price,2)} · ${focus.count} TPO · ${focus.letters||'letters unavailable'}`:'Hover a row · ↑ ↓ to inspect'}</span></div>
  <div tabIndex={0} aria-label="Profile chart. Arrow keys inspect price rows." className="overflow-auto rounded-lg border border-bg-border bg-bg-primary outline-none focus-visible:ring-1 focus-visible:ring-accent-blue" style={{maxHeight:height}} onKeyDown={e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setSelected(i=>Math.max(0,Math.min(rows.length-1,(i??0)+(e.key==='ArrowDown'?1:-1))));}}}>
   <svg role="img" aria-label={`${displayMode} Market Profile, ${rows.length} recorded price levels`} viewBox={`0 0 ${width} ${svgHeight}`} style={{width:"100%",minWidth:480,height:svgHeight}} preserveAspectRatio="none" onMouseLeave={()=>setSelected(null)}>
    <title>TPO Market Profile with point of control, value area, prior value and initial balance</title>
    {showIb&&ibh!=null&&ibl!=null&&<rect x={left-10} y={y(closest(ibh))-rowH/2} width={6} height={Math.max(rowH,y(closest(ibl))-y(closest(ibh))+rowH)} fill={CHART.violet} opacity={.65}/>}
    {rows.map((r,i)=>{const inVa=val!=null&&vah!=null&&r.price>=val&&r.price<=vah;const isPoc=poc!=null&&i===closest(poc);const fill=isPoc?CHART.amber:inVa?CHART.blue:CHART.muted;const letters=Array.from(r.letters||"");const cell=displayMode==='split'?Math.min(24,plot/Math.max(periods.length,1)):Math.min(18,plot/Math.max(max,1));return <g key={r.price} onMouseEnter={()=>setSelected(i)}>
     <rect x={0} y={y(i)-rowH/2} width={width} height={rowH} fill={selected===i?'rgba(148,163,184,.13)':'transparent'}/>
     {(i%Math.max(1,Math.ceil(14/rowH))===0||isPoc)&&<text x={left-18} y={y(i)+3} textAnchor="end" fill={isPoc?CHART.amber:CHART.muted} fontSize={11} fontFamily="monospace">{chartNumber(r.price,2)}</text>}
     {displayMode==='distribution'?<rect x={left} y={y(i)-Math.max(.5,rowH-1)/2} width={Math.max(1,r.count/max*plot)} height={Math.max(.5,rowH-1)} fill={fill} opacity={isPoc?1:inVa?.7:.35}/>:letters.map((letter,j)=>{const index=displayMode==='split'?periods.indexOf(letter):j;return <g key={`${letter}-${j}`}><rect x={left+index*cell} y={y(i)-rowH/2} width={cell-1} height={rowH-1} fill={fill} opacity={isPoc?.22:.09}/><text x={left+index*cell+cell/2} y={y(i)+4} textAnchor="middle" fill={fill} fontSize={Math.min(12,rowH-2)} fontFamily="monospace">{letter}</text></g>;})}
     {rowH>=12&&<text x={width-right+12} y={y(i)+3} fill={CHART.muted} fontSize={10} fontFamily="monospace">{r.count}</text>}
     {(profile.single_prints||[]).some((p:number)=>Math.abs(p-r.price)<1e-6)&&<circle cx={left-4} cy={y(i)} r={Math.max(1,Math.min(3,rowH/3))} fill={CHART.red}/>}
    </g>;})}
    {references.map((r,index)=>{const yy=y(closest(r.p!));return <g key={r.label} style={{pointerEvents:'none'}}><line x1={left} x2={width-right} y1={yy} y2={yy} stroke={r.color} strokeDasharray={r.label==='POC'?'':'4 4'} opacity={.55}/><text x={width-right+38} y={yy+3+(references.slice(0,index).some(g=>Math.abs(y(closest(g.p!))-yy)<12)?12:0)} fill={r.color} fontSize={10} fontFamily="monospace">{r.label}</text></g>;})}
   </svg>
  </div>
  {showLegend&&<div className="flex flex-wrap gap-3 text-[10px] text-text-muted"><span className="text-accent-amber">● POC</span><span className="text-accent-blue">● Value area</span><span className="text-accent-red">● Single prints</span><span className="text-accent-purple">│ Initial balance / prior</span><span>{grouping>1?'Grouped rows; value references retain source resolution. ':''}{profile.poor_high?'Unfinished high · ':''}{profile.poor_low?'Unfinished low':''}</span></div>}
 </div>;
}
