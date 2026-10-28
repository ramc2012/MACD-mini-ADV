import {useEffect,useState} from 'react';
import {API_URL,API_TOKEN} from './runtime';
export function LaneIntelligencePanel({lane}:{lane:string}){
 const [state,setState]=useState<any>();
 useEffect(()=>{let active=true;const read=async()=>{try{const r=await fetch(`${API_URL}/api/paper-lanes/${lane}`,{headers:API_TOKEN?{'X-Macd-Token':API_TOKEN}:{},signal:AbortSignal.timeout(6000)});if(!r.ok)throw Error();const body=await r.json();if(active)setState(body.intelligence);}catch{if(active)setState(undefined);}};void read();const timer=window.setInterval(read,10000);return()=>{active=false;window.clearInterval(timer);};},[lane]);
 return <section aria-label="Local lane intelligence"><strong>{state?.model??'Checking local model…'}</strong><p>No Qwen dependency · current shared features · original quote and risk checks.</p><p>{state?.validation??'Local model status unavailable; entries require valid context.'}</p>{state?.last_review?.error?<p>{state.last_review.reason}</p>:state?.last_review?.reviews?.map((r:any)=><p key={r.id}><b>{r.verdict.toUpperCase()}</b> · {r.reason} · priority {r.priority?.toFixed(3)}</p>)}<p>Daily indicators, market profile, sector strength, current price continuation, source rank and OI positioning rank candidates. Stale optional context is neutral. Other shared features and provenance remain recorded for evaluation.</p></section>;
}
