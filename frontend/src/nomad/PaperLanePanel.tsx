import {Fragment,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {ResponsiveContainer,LineChart,Line,CartesianGrid,XAxis,YAxis,Tooltip} from 'recharts';
import {api} from './lib/api';
import {API_URL,API_TOKEN} from '../runtime';
import {Section,MetricTile,formatIST,formatMoney,formatSignedMoney} from './components/desk-ui';

import {useLaneLive} from './hooks/useLaneLive';
import {paperMark,paperQuote} from '../laneMarks';
export const positionQuote=paperQuote;
type Paper={enabled:boolean;account_id:string;lane:string;mode:string;initial_capital:number;cash:number;unsettled_cash:number;equity:number;
 realized_pnl:number;unrealized_pnl:number;return_pct:number;max_drawdown_pct:number;open_count:number;closed_count:number;
 fees_paid:number;win_rate:number|null;expectancy:number|null;paused:boolean;stale_positions:number;checked_at:string;
 status:{state:string};market_error?:string;readiness?:{status?:string;blockers?:string[]};policy:Record<string,any>;
 positions:any[];closed:any[];orders:any[];fills:any[];events:any[];decisions:any[];equity_curve:any[];list_tests?:ListTest[]};
type SideTest={picks:number;filled:number;mean_returns:(number|null)[];observed:number[]};
type ListTest={cohort:string;source_session:string;picks:number;filled:number;open:number;stopped:number;realized_pnl:number;open_value_change:number;
 mean_returns:(number|null)[];observed:number[];by_side:Record<string,SideTest>;
 rows:{symbol:string;side:string;side_rank:number|null;state:string;entry:number|null;opened_at:string|null;closed_at:string|null;exit_reason:string|null;returns:(number|null)[]}[]};
const ret=(v:number|null|undefined)=>v==null?'—':`${v>=0?'+':''}${(v*100).toFixed(1)}%`;
const tone=(v:number|null|undefined)=>v==null?'':v>=0?'text-emerald-300':'text-rose-300';

// Each Vanguard list as a test (owner, 28 Oct 2026): 10 CE + 10 PE entered, held to the third session or the stop.
function ListTests({tests}:{tests:ListTest[]}){
 const [open,setOpen]=useState<string|null>(null);
 if(!tests.length)return <p className="text-xs text-text-secondary">No published list in this paper book yet.</p>;
 return <div className="space-y-3"><p className="text-xs text-text-secondary">Return per pick at the close of the 1st, 2nd and 3rd session after entry (last bid ÷ entry − 1, before costs). A stopped pick keeps its exit return from the session it stopped. The latest session's figure is live until its close.</p>
 <div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{['List (source session)','Picks / filled','Open / stopped','D1','D2','D3','CE D1 · D2 · D3','PE D1 · D2 · D3','Realized ₹','Open change ₹'].map(h=><th className="p-2" key={h}>{h}</th>)}</tr></thead>
 <tbody>{tests.map(t=><Fragment key={t.cohort}><tr className="border-t border-bg-border cursor-pointer" onClick={()=>setOpen(open===t.cohort?null:t.cohort)} title="Show the picks"><td className="p-2">{open===t.cohort?'▾':'▸'} {t.source_session}<br/><small className="text-text-muted">{t.cohort.endsWith(':owner-convert')?'owner conversion of the list':`list ${t.cohort.slice(0,12)}`}</small></td><td>{t.picks} / {t.filled}</td><td>{t.open} / {t.stopped}</td>
  {t.mean_returns.map((v,i)=><td className={tone(v)} key={i} title={`${t.observed[i]} picks observed`}>{ret(v)}<small className="text-text-muted"> n{t.observed[i]}</small></td>)}
  {['CE','PE'].map(side=><td key={side}>{t.by_side[side]?<span>{t.by_side[side].mean_returns.map((v,i)=><span className={tone(v)} key={i}>{i?' · ':''}{ret(v)}</span>)}<br/><small className="text-text-muted">{t.by_side[side].filled}/{t.by_side[side].picks} filled</small></span>:'—'}</td>)}
  <td className={tone(t.realized_pnl)}>{formatSignedMoney(t.realized_pnl)}</td><td className={tone(t.open_value_change)}>{formatSignedMoney(t.open_value_change)}</td></tr>
  {open===t.cohort&&<tr><td colSpan={10} className="p-2"><table className="w-full text-left text-xs"><thead><tr>{['Stock','Side · rank','State','Entry','Opened','D1','D2','D3','Exit'].map(h=><th className="p-1" key={h}>{h}</th>)}</tr></thead><tbody>{t.rows.map((r,i)=><tr className="border-t border-bg-border" key={i}><td className="p-1">{r.symbol}</td><td>{r.side} · {r.side_rank??'—'}</td><td>{r.state}</td><td>{r.entry==null?'—':formatMoney(r.entry)}</td><td>{r.opened_at?formatIST(r.opened_at):'—'}</td>{r.returns.map((v,j)=><td className={tone(v)} key={j}>{ret(v)}</td>)}<td>{r.closed_at?`${r.exit_reason} · ${formatIST(r.closed_at)}`:'—'}</td></tr>)}</tbody></table></td></tr>}</Fragment>)}</tbody></table></div></div>;
}

export function usePaper(lane:string){return useQuery({queryKey:['macd-paper',lane],queryFn:async()=>(await api.get(`/api/paper-lanes/${lane}`)).data as Paper,refetchInterval:5000});}

export function PaperLanePanel({lane,performance=false}:{lane:string;performance?:boolean}){
 const live=useLaneLive();
 const query=usePaper(lane),raw=query.data;
 const marks=raw?.positions.map(r=>paperMark(r,raw.policy,positionQuote(r,live)))??[];
 const equity=raw?raw.cash+raw.unsettled_cash+marks.reduce((v,m)=>v+m.netValue,0):0;
 const p=raw?{...raw,equity,unrealized_pnl:marks.reduce((v,m)=>v+m.pnl,0),return_pct:(equity/raw.initial_capital-1)*100}:undefined;const [tab,setTab]=useState(performance?'Equity':lane==='vanguard'?'List tests':'Positions');const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 const control=async()=>{if(!p)return;setBusy(true);setError('');try{const r=await fetch(`${API_URL}/api/paper-lanes/${lane}/control`,{method:'POST',headers:{'Content-Type':'application/json',...(API_TOKEN?{'X-Macd-Token':API_TOKEN}:{})},body:JSON.stringify({action:p.paused?'resume':'pause'})});if(!r.ok)throw Error('Paper entry control failed');await query.refetch();}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
 if(query.isError)return <Section title="MACD paper account"><p role="alert">The paper account is unavailable. No financial values have been substituted.</p></Section>;
 if(!p?.enabled)return <Section title="MACD paper account"><p>Loading the independent paper book…</p></Section>;
 const tabs=[...(lane==='vanguard'?['List tests']:[]),'Positions','Orders','Closed trades','Fills','Model decisions','Equity'];
 const table=(headers:string[],rows:React.ReactNode[][])=><div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{headers.map(h=><th className="p-3" key={h}>{h}</th>)}</tr></thead><tbody>{rows.length?rows.map((r,i)=><tr className="border-t border-bg-border" key={i}>{r.map((cell,j)=><td className="p-3" key={j}>{cell}</td>)}</tr>):<tr><td className="p-4 text-text-secondary" colSpan={headers.length}>No records in this new paper book.</td></tr>}</tbody></table></div>;
 return <div className="space-y-4"><Section title={`${lane==='vanguard'?'Vanguard':'CBE'} · paper account`} description={lane==='vanguard'?'Fully funded exact-contract option simulations.':'Fully funded long-only cash-equity simulations.'} rightSlot={<button className="rounded-lg border border-bg-border px-3 py-2 text-xs" disabled={busy} onClick={control}>{busy?'Saving…':p.paused?'Resume entries':'Pause entries'}</button>}>
 <div className="grid grid-cols-2 gap-3 xl:grid-cols-4"><MetricTile label="Net liquidation equity" value={formatMoney(p.equity)} detail={`Starting paper capital ${formatMoney(p.initial_capital)}`}/><MetricTile label="Available cash" value={formatMoney(p.cash)} detail={`Unsettled ${formatMoney(p.unsettled_cash)}`}/><MetricTile label="Realized / unrealized" value={formatSignedMoney(p.realized_pnl)} detail={`${formatSignedMoney(p.unrealized_pnl)} open, after estimated exit costs`}/><MetricTile label="Open / closed" value={`${p.open_count} / ${p.closed_count}`} detail={`Fees paid ${formatMoney(p.fees_paid)}`}/></div>
 <div className="mt-3 grid grid-cols-2 gap-3 xl:grid-cols-4"><MetricTile label="Return" value={`${p.return_pct.toFixed(2)}%`}/><MetricTile label="Maximum drawdown" value={`${p.max_drawdown_pct.toFixed(2)}%`}/><MetricTile label="Closed-trade strike rate" value={p.win_rate==null?'—':`${(p.win_rate*100).toFixed(1)}%`}/><MetricTile label="Net expectancy / close" value={p.expectancy==null?'—':formatSignedMoney(p.expectancy)}/></div>
 <p className="mt-4 text-sm text-accent-cyan">Paper {p.paused?'entries paused':'entries enabled'} · {p.status.state.replaceAll('_',' ')} · local intelligence · no Qwen dependency.</p>
 <p className="mt-2 text-xs text-text-secondary">New selections: {p.readiness?.status?.replaceAll('_',' ')??'awaiting inputs'}. {(p.readiness?.blockers??[]).join(' · ')}</p>
 {!!p.stale_positions&&<p className="mt-2 text-amber-200">{p.stale_positions} held marks are stale. Fresh displayed quotes are required for fills.</p>}
 {(error||p.market_error)&&<p role="alert" className="mt-2 text-amber-200">{error||p.market_error}</p>}
 <p className="mt-2 text-xs text-text-muted">Checked {formatIST(p.checked_at)}. Local review and fresh executable quotes are required; old Nomad holdings and returns are excluded.</p>
 </Section><Section title="Paper execution ledger" description={p.policy.liquidity_basis==='session_volume'?`Simulated bid/ask fills with adverse slippage and estimated fees. Entries are capped at ${(p.policy.volume_fraction*100).toFixed(0)}% of session volume; displayed size does not cap fills. Fresh quotes and risk gates apply.`:'Simulated all-or-none L1 fills, adverse slippage and estimated fees. New entries require fresh bid/ask, sufficient displayed depth and unchanged risk gates.'}>
 <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Paper ledger view">{tabs.map(t=><button className={`rounded-lg border border-bg-border px-3 py-2 text-xs ${tab===t?'text-accent-cyan bg-bg-active':'text-text-secondary'}`} key={t} onClick={()=>setTab(t)} aria-pressed={tab===t}>{t}</button>)}</div>
 {tab==='List tests'&&<ListTests tests={p.list_tests??[]}/>}
 {tab==='Positions'&&table(['Instrument','Quantity / lots','Entry','Liquidation bid','Net open P/L','Stop / target','Exit due','Opened'],p.positions.map((r,i)=>[<span>{r.symbol}<br/>{r.instrument}</span>,`${r.quantity} / ${r.quantity/r.lot_size}`,formatMoney(r.entry),<span title={`Stored quote ${formatIST(r.last_at)}`}>{formatMoney(marks[i].bid)}</span>,formatSignedMoney(marks[i].pnl),`${formatMoney(r.stop)} / ${r.target>0?formatMoney(r.target):'none'}`,formatIST(r.exit_due),formatIST(r.opened_at)]))}
 {tab==='Orders'&&table(['Stock / instrument','Rank','State','Admission reason','Entry window','Exit due'],p.orders.map(r=>[<span>{r.symbol}<br/>{r.instrument}</span>,r.rank,r.state==='PENDING_AI'?'PENDING_REVIEW':r.state,r.reason==='Awaiting entry window and fresh Qwen review'?'Awaiting entry window and local review':r.reason,<span>{formatIST(r.entry_after)}<br/>{formatIST(r.deadline)}</span>,formatIST(r.exit_due)]))}
 {tab==='Closed trades'&&table(['Stock / instrument','Quantity','Entry / exit','Net P&L','Reason','Opened','Closed'],p.closed.map(r=>[<span>{r.symbol}<br/>{r.instrument}</span>,r.quantity,`${formatMoney(r.entry)} / ${formatMoney(r.exit)}`,formatSignedMoney(r.pnl_paise/100),r.exit_reason,formatIST(r.opened_at),formatIST(r.closed_at)]))}
 {tab==='Fills'&&table(['Side','Order','Quantity','Fill','Notional','Fee','Filled / quote time'],p.fills.map(r=>[r.side,r.order_id,r.quantity,formatMoney(r.price),formatMoney(r.notional_paise/100),formatMoney(r.fee_paise/100),<span>{formatIST(r.at)}<br/>{formatIST(r.quote_at)}</span>]))}
 {tab==='Model decisions'&&table(['Recorded','Order','Verdict','Reason','Model / digest'],p.decisions.map(r=>[formatIST(r.at),r.order_id,r.verdict,r.payload.reason,`${r.payload.model} · ${r.payload.digest?.slice(0,12)}`]))}
 {tab==='Equity'&&<><ResponsiveContainer width="100%" height={260}><LineChart data={p.equity_curve.map(r=>({...r,equity:r.equity_paise/100}))}><CartesianGrid stroke="#26364a" strokeDasharray="3 3"/><XAxis dataKey="at" hide/><YAxis domain={['auto','auto']} stroke="#94a3b8" tickFormatter={v=>formatMoney(v)}/><Tooltip labelFormatter={v=>formatIST(String(v))} formatter={v=>formatMoney(Number(v))} contentStyle={{background:'#0d1726',borderColor:'#334155'}}/><Line dataKey="equity" stroke="#22d3ee" dot={p.equity_curve.length===1} isAnimationActive={false}/></LineChart></ResponsiveContainer><p className="mt-2 text-xs text-text-secondary">Prospective net liquidation equity only. No imported historical returns.</p></>}
 <p className="mt-4 text-xs text-text-muted">{p.policy.position_amount?`Fixed entry ticket ${formatMoney(p.policy.position_amount)}`:`Position share ${(p.policy.position_fraction*100).toFixed(p.policy.position_fraction<.05?2:0)}% of equity`}{p.policy.min_one_lot?' (at least one lot)':''} · up to {p.policy.max_positions} open · gross cap {(p.policy.gross_fraction*100).toFixed(0)}% · sector cap {(p.policy.sector_fraction*100).toFixed(0)}% · stop {(p.policy.hard_stop*100).toFixed(0)}% · {p.policy.target?`target ${(p.policy.target*100).toFixed(0)}%`:'no target (held to the planned exit or the stop)'}{p.policy.daily_loss_stop?` · daily loss stop ${(p.policy.daily_loss_stop*100).toFixed(0)}%`:''}. Cash-stock sale proceeds settle at the next session open. Fees are estimates; fills do not model queue position or market impact.</p>
 </Section></div>;
}
