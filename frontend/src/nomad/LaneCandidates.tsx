import {useQuery} from '@tanstack/react-query';
import {api} from './lib/api';
import {usePaper} from './PaperLanePanel';
import {Section,formatIST,formatNumber} from './components/desk-ui';
import {LiveMarkCell} from './components/terminal/LiveMarkCell';
import {useLaneLive} from './hooks/useLaneLive';

export function LaneCandidates({lane}:{lane:'vanguard'|'cbe'}){
 const {data}=useQuery({queryKey:['lane-candidates',lane],queryFn:async()=>(await api.get(`/api/shadow-lanes/${lane}`)).data,refetchInterval:5000});
 const paper=usePaper(lane).data,live=useLaneLive();
 const pub=data?.publications?.find((p:any)=>!paper?.account_id||p.paper_account_id===paper.account_id);
 const rows=pub?.watchlist??[],decisions=paper?.decisions??[],failures=(paper?.events??[]).filter((e:any)=>['QWEN_UNAVAILABLE','LOCAL_CONTEXT_UNAVAILABLE'].includes(e.kind)).length;
 return <Section title={lane==='vanguard'?'ATM option candidates':'Daily cash candidates'} description="The frozen pool is separate from paper fills. Local review and fresh executable quotes are required. Source ranks are research inputs, not expected profit.">
 <p className="text-xs mb-3">{rows.length} frozen candidates · {decisions.length} completed model entry verdicts · {failures} recorded service failures · {paper?.fills?.length??0} fills</p>
 {pub&&<p className="text-xs text-text-muted mb-3">Published {formatIST(pub.decision_at)} · source session {pub.source_session}</p>}
 {!rows.length?<p>No frozen pool in this account epoch. {data?.readiness?.status?.replaceAll('_',' ')} · {(data?.readiness?.blockers??[]).join(' · ')}</p>:<div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{['Rank','Stock','Side / hold','Exact contract','Latest quote','Source mark','Paper state','Local review / admission reason'].map(h=><th key={h} className="p-2">{h}</th>)}</tr></thead><tbody>{rows.map((r:any,i:number)=>{const order=paper?.orders.find(o=>o.cohort===pub.id&&o.symbol===r.symbol);const meta=(live as any).contracts?.[r.instrument];const symbol=lane==='cbe'?`NSE:${r.symbol}-EQ`:meta?.canonical??r.canonical;const d=decisions.find(x=>x.order_id===order?.id);return <tr className="border-t border-bg-border" key={r.instrument}><td className="p-2">{r.rank??i+1}</td><td>{r.symbol}</td><td>{r.option_type??'LONG'} · {r.horizon_sessions} sessions</td><td>{lane==='vanguard'?`${r.expiry} · ${r.strike} · ATM`:'Cash spot'}</td><td><LiveMarkCell symbol={symbol} fallback={r.source_mark} fallbackAt={r.source_mark_ts}/></td><td>{formatNumber(r.source_mark)}</td><td>{order?.state??'Not queued'}</td><td className="p-2 max-w-sm">{d?`${d.verdict}: ${d.payload.reason}`:order?.reason==='Awaiting entry window and fresh Qwen review'?'Awaiting entry window and local review':order?.reason??'Exact metadata/admission check did not create an order'}</td></tr>;})}</tbody></table></div>}
 </Section>;
}
