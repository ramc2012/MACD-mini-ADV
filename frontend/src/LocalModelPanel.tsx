import {useEffect,useState} from 'react';
import {API_URL,API_TOKEN} from './runtime';

type Review={at:string;lane:string;purpose?:string;latency_ms:number;reviews:{id:string;verdict:string;reason:string}[]};
type Status={training_busy?:boolean;ready:boolean;model:string;mode:string;queue?:number;cache_hits?:number;detail?:string;last_error?:string;recent:Review[];lane_stats?:Record<string,{completed:number;approve:number;reject:number;wait:number;context_failures:number;validation_only:number}>;
 training?:{deployed_weight_finetune:boolean;smart?:{status:string};vanguard?:{status:string};cbe?:{status:string}}};
const stamp=(value:string)=>{const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} IST`;};

export function LocalModelPanel({lane}:{lane?:string}={}){
 const [state,setState]=useState<Status>();
 useEffect(()=>{let active=true;const read=async()=>{try{const r=await fetch(`${API_URL}/api/local-decisions`,{headers:API_TOKEN?{'X-Macd-Token':API_TOKEN}:{},signal:AbortSignal.timeout(6500)});if(!r.ok)throw Error('unavailable');const body=await r.json();if(active)setState(body);}catch{if(active)setState({ready:false,model:'qwen3.5:9b',mode:'unavailable',recent:[],detail:'Local review status is unavailable; filtered new entries wait.'});}};void read();const timer=window.setInterval(read,10000);return()=>{active=false;window.clearInterval(timer);};},[]);
 const latest=state?.recent?.find(r=>!lane||r.lane===lane);
 return <section style={{padding:16,margin:'14px 0',border:'1px solid #284557',borderRadius:10,background:'#0d1923',color:'#dce5f1',fontSize:12,lineHeight:1.6}} aria-label="Local Qwen decision review">
  <div style={{display:'flex',justifyContent:'space-between',gap:12,flexWrap:'wrap'}}><strong>Qwen 3.5 · local decision review</strong><span style={{color:state?.ready&&!state?.training_busy?'#68d5c1':'#efc47c'}}>{!state?'Checking model…':state.training_busy?'Research training active · new model-reviewed entries wait':state.ready?`Model available · ${state.mode} mode`:'Model unavailable · new filtered entries wait'}</span></div>
  <div>{state?.model??'qwen3.5:9b'} · Vanguard and CBE paper entries. Smart lanes use independent local allocation. Existing admission rules, sizing and exits remain authoritative.</div>
  <div style={{color:'#9aafc2'}}>One shared worker · {state?.queue??0} requests queued · {state?.cache_hits??0} cache hits since restart · local inference only</div>
  {state?.training&&<div style={{marginTop:6,color:'#9aafc2'}}>Served weights: {state.training.deployed_weight_finetune?'validated tuned model':'original local Qwen'}. Research tuning: Smart {state.training.smart?.status?.replaceAll('_',' ')??'pending'}; Vanguard {state.training.vanguard?.status?.replaceAll('_',' ')??'pending'}; CBE {state.training.cbe?.status?.replaceAll('_',' ')??'pending'}.</div>}
  {(state?.last_error||state?.detail)&&<p style={{margin:'5px 0',color:'#efc47c'}}>{state.last_error||state.detail}</p>}
  {lane&&state?.lane_stats?.[lane]&&<p>{state.lane_stats[lane].completed} completed reviews · {state.lane_stats[lane].approve} approvals · {state.lane_stats[lane].context_failures} captured context failures · {state.lane_stats[lane].validation_only} validation-only checks</p>}
  {latest?<div style={{borderTop:'1px solid #284557',marginTop:8,paddingTop:8}}>{latest.purpose==='validation_only'?'Validation only · ':'Latest '}{latest.lane} review · {stamp(latest.at)} · {(latest.latency_ms/1000).toFixed(1)}s{latest.reviews.map(r=><div key={r.id}><b>{r.verdict.toUpperCase()}</b> · {r.reason}</div>)}</div>:<div style={{marginTop:6,color:'#9aafc2'}}>No prospective review recorded yet. Model availability does not establish market-data readiness or trading edge.</div>}
 </section>;
}
