import {useQuery} from '@tanstack/react-query';
import {api} from './lib/api';
import {MetricTile,Section,formatIST,formatNumber} from './components/desk-ui';

export function useShadow(lane='vanguard') {
  return useQuery({queryKey:['macd-shadow',lane],queryFn:async()=>(await api.get(`/api/shadow-lanes/${lane}`)).data,refetchInterval:30000});
}
export function ShadowPortfolio({lane='vanguard'}:{lane?:string}) {
  const query=useShadow(lane), d=query.data, publications=d?.publications??[],outcomes=d?.outcomes??[];
  return <div className="space-y-4"><Section title="Fresh MACD shadow book" description="Prospective observations in their own journal. Execution and funding are disabled.">
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4"><MetricTile label="Frozen cohorts" value={String(publications.length)}/><MetricTile label="Selected instruments" value={String(publications.reduce((n:number,p:any)=>n+(p.watchlist?.length??0),0))}/><MetricTile label="Observed outcomes" value={String(outcomes.length)}/><MetricTile label="Orders / funded positions" value="Disabled"/></div>
    <p className="mt-4 text-amber-200">{d?.readiness?.status?.replaceAll('_',' ')??'Checking inputs…'}</p>
    {(d?.readiness?.blockers??[]).map((b:string)=><p key={b} className="mt-2 text-sm text-text-secondary">{b}</p>)}
    {query.isError&&<p role="alert">The shadow journal could not be read.</p>}
    <p className="mt-3 text-xs text-text-muted">Checked {formatIST(d?.checked_at)}. Retained Nomad positions and P&amp;L appear only in the separate retained-history view.</p>
  </Section><Section title="Latest frozen selection" description="Only decisions actually published inside the lane's prospective window appear here.">
    {!publications.length?<p>No prospective cohort has been frozen yet.</p>:<div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{['Session','Rank','Stock','Exact instrument','Side','Baseline'].map(s=><th className="p-2" key={s}>{s}</th>)}</tr></thead><tbody>{publications[0].watchlist.map((r:any,i:number)=><tr key={r.instrument??i} className="border-t border-bg-border"><td className="p-2">{publications[0].source_session}</td><td>{r.rank??r.side_rank??i+1}</td><td>{r.symbol}</td><td>{r.instrument}</td><td>{r.option_type??'LONG'}</td><td>{formatNumber(r.source_mark)}</td></tr>)}</tbody></table></div>}
  </Section></div>;
}
export function ShadowFollowup({lane='vanguard'}:{lane?:string}) {
  const {data}=useShadow(lane); const rows=data?.outcomes??[];
  return <Section title="Prospective follow-through" description="Observed quote-to-quote returns before costs; no simulated fills or imported track record.">
    {!rows.length?<p>No completed prospective outcomes yet.</p>:<div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{['Instrument','Horizon','Baseline','Observed quote','Change','Recorded'].map(s=><th key={s} className="p-2">{s}</th>)}</tr></thead><tbody>{rows.map((r:any,i:number)=><tr key={i} className="border-t border-bg-border"><td className="p-2">{r.instrument}</td><td>{r.horizon}</td><td>{formatNumber(r.baseline_price)}</td><td>{formatNumber(r.observed_price)}</td><td>{formatNumber(r.observed_return*100)}%</td><td>{formatIST(r.observed_at)}</td></tr>)}</tbody></table></div>}
  </Section>;
}
