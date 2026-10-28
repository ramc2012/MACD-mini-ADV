import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {ResponsiveContainer,LineChart,Line,XAxis,YAxis,Tooltip,CartesianGrid} from 'recharts';
import {api} from './lib/api';
import {Section,formatIST} from './components/desk-ui';
import {LiveMarkCell} from './components/terminal/LiveMarkCell';
import {useLaneLive} from './hooks/useLaneLive';

import {useQuote} from './hooks/useQuoteStore';
import {priceMove} from '../laneMarks';
function DayChange({symbol,close}:{symbol:string;close:number}){const q=useQuote(symbol);const m=q?priceMove(q.ltp,q.prevClose??close):null;return <span className={m&&m.change>0?'text-accent-green':m&&m.change<0?'text-accent-red':''}>{m?`${m.change>=0?'+':''}${m.change.toFixed(2)} (${m.percent>=0?'+':''}${m.percent.toFixed(2)}%)`:'—'}</span>;}
const n=(v:unknown,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-IN',{maximumFractionDigits:d});
const pct=(v:unknown)=>v==null?'—':`${n(Number(v)*100)}%`;
const groups={daily:'Daily indicators',profile:'Market profile',flow:'Flow & timing',positioning:'Positioning',surface:'Volatility',futures:'Futures OI',chain:'Option structure',delivery:'Delivery & limits'};
export function MarketContextPanel({lane}:{lane:'vanguard'|'cbe'}){
 const [session,setSession]=useState(''),[search,setSearch]=useState(''),[symbol,setSymbol]=useState(''),[lens,setLens]=useState<keyof typeof groups>('daily');
 const q=useQuery({queryKey:['market-context',lane,session],queryFn:async()=>(await api.get(`/api/${lane}/market-context`,{params:session?{session}:{},timeout:40000})).data,refetchInterval:60000});
 const latestFeatures=useQuery({queryKey:['lane-current-features',lane],queryFn:async()=>(await api.get(`/api/${lane}/market-features`)).data,refetchInterval:15000,enabled:!session});
 const live=useLaneLive();
 const data=q.data,rows=(data?.results??[]).filter((r:any)=>r.symbol.includes(search.toUpperCase()));
 const selected=(data?.results??[]).find((r:any)=>r.symbol===symbol);
 const columns=lens==='daily'?['Close','EMA20','EMA50','MACD','Signal','Histogram','RSI14','5D','20D','Relative 20D','Trend']:
 lens==='profile'?['POC','VAH','VAL','VA width %','IB width %','Close location','Value shift','Day type','MP session']:
 lens==='flow'?['Flow','Ingredients','Flow source','Timing','Score','RVOL','VA position','Regime','GEX percentile','Timing source']:
 lens==='positioning'?['OI change %','PCR','MWPL %','OI state','5D','20D','60D','Source']:
 lens==='futures'?['Price change %','OI change %','OI state','OI z','Volume z','Activity surge','Rollover','Baseline sessions','Source']:
 lens==='chain'?['Straddle / spot','Normalized straddle','ATM put/call','Premium PCR','Wing valid','Strikes','Contracts','Source']:
 lens==='delivery'?['Delivery %','Volume','Deliverable qty','Delivery session','MWPL %','MWPL session']:
 ['ATM IV','IV percentile','IV rank','Skew','IV change','Strikes','Source'];
 const visible=columns.map((name,i)=>({name,i})).filter(c=>session||!/source|session/i.test(c.name));
 const cells=(r:any)=>{const f=(!session?latestFeatures.data?.symbols?.[r.symbol]:null)??r.features??{},p=f.profile??r.profile??{};
 if(lens==='daily')return[n(r.latest_close),n(r.ema20),n(r.ema50),n(r.macd_line),n(r.macd_signal),n(r.macd_hist),n(r.rsi_14),pct(r.momentum_fast),pct(r.momentum_slow),pct(r.relative_momentum),r.direction??'Unavailable'];
 if(lens==='profile')return[n(p.poc),n(p.vah),n(p.val),n(p.va_width_pct),n(p.ib_width_pct),n(p.close_pos),p.value_shift==null?'—':String(p.value_shift),p.day_type??'—',p.source??p.dt??'—'];
 if(lens==='flow')return[n(f.flow?.flow_score),n(f.flow?.n_ingredients,0),f.flow?.source?formatIST(f.flow.source):'—',f.timing?.timing_state??'—',n(f.timing?.timing_score),n(f.timing?.rvol),n(f.timing?.va_position),f.regime?.regime??'—',n(f.regime?.gex_percentile),f.timing?.source?formatIST(f.timing.source):'—'];
 if(lens==='positioning'){const x=f.positioning??{};return[n(x.d_oi_pct),n(x.oi_pcr),n(x.mwpl_pct),x.oi_state??'—',n(x.ret_5d),n(x.ret_20d),n(x.ret_60d),x.source??'—'];}
 if(lens==='futures'){const x=f.futures??{};return[n(x.d_price_pct),n(x.d_oi_pct),x.oi_state??'—',n(x.oi_z),n(x.volume_z),x.activity_surge==null?'—':String(x.activity_surge),x.is_rollover==null?'—':String(x.is_rollover),n(x.lookback_sessions,0),x.source?formatIST(x.source):'—'];}
 if(lens==='chain'){const x=f.chain??{};return[n(x.straddle_to_spot,4),n(x.normalized_straddle,4),n(x.atm_put_call_premium_ratio),n(x.premium_pcr),x.wing_valid==null?'—':String(x.wing_valid),n(x.n_strikes,0),n(x.n_contracts,0),x.source?formatIST(x.source):'—'];}
 if(lens==='delivery'){const x=f.delivery??{},m=f.mwpl??{};return[n(x.delivery_pct),n(x.volume,0),n(x.deliverable_qty,0),x.source??'—',n(m.utilisation_pct),m.source??'—'];}
 const x=f.surface??{};return[n(x.atm_iv),n(x.iv_percentile),n(x.iv_rank),n(x.skew_25d),n(x.d_atm_iv),n(x.n_strikes,0),x.source??'—'];};
 return <Section title={lane==='vanguard'?'Vanguard option underlyings':'F&O stocks · daily cash market'} description={data?.provenance??'Local completed-session market data; no broker download.'} rightSlot={<span>{data?.current_complete??'—'} / {data?.expected??'—'} daily · {data?.mp_complete??'—'} MP</span>}>
 <div className="market-context-controls"><label>Session <select aria-label="Market session" value={session} onChange={e=>{setSession(e.target.value);setSymbol('');}}><option value="">Latest · {data?.source_session??'loading'}</option>{(data?.sessions??[]).map((s:string)=><option value={s} key={s}>{s}</option>)}</select></label><input aria-label="Find stock" placeholder="Find stock…" value={search} onChange={e=>setSearch(e.target.value)}/><span>Completed session {data?.source_session??'—'}</span></div>
 {!session&&<div className="market-context-foot" role="status">{!(lane==='cbe'?live.cash_open:live.market_open)?'Market closed · recorded quotes':live.connected?'MI tick stream connected':'Tick stream reconnecting'} · {Object.keys(live.quotes).length} quotes · shared flow {Object.keys(live.flow).length} symbols</div>}
 <div className="market-context-tabs" role="tablist" aria-label="Market data views">{Object.entries(groups).map(([key,label])=><button key={key} role="tab" aria-selected={lens===key} onClick={()=>setLens(key as keyof typeof groups)}>{label}</button>)}</div>
 {q.isLoading&&<p>Reading local market history…</p>}{q.isError&&<p role="alert">Market data could not be loaded. Retry this tab.</p>}
 {selected&&<div className="market-context-chart"><div><b>{selected.symbol} · daily close</b><button onClick={()=>setSymbol('')} aria-label="Close stock chart">×</button></div><ResponsiveContainer width="100%" height={180}><LineChart data={selected.history}><CartesianGrid stroke="#284557"/><XAxis dataKey="session" stroke="#94a3b8" minTickGap={80}/><YAxis domain={['auto','auto']} stroke="#94a3b8" width={80}/><Tooltip contentStyle={{background:'#101c28'}}/><Line dataKey="close" stroke="#68d5c1" dot={false} isAnimationActive={false}/></LineChart></ResponsiveContainer></div>}
 <div className="market-context-grid"><table><thead><tr><th>Stock</th><th>Sector</th>{!session&&<><th>Latest quote</th><th>Day change ₹ / %</th><th>Shared tick CVD</th></>}{visible.map(c=><th key={c.name}>{c.name}</th>)}{session&&<th>Price session</th>}</tr></thead><tbody>{rows.map((r:any)=><tr key={r.symbol} onClick={()=>setSymbol(r.symbol)}><td><button onClick={()=>setSymbol(r.symbol)}>{r.symbol}</button></td><td>{r.sector}</td>{!session&&<><td><LiveMarkCell symbol={`NSE:${r.symbol}-EQ`} fallback={r.latest_close} fallbackAt={`${r.price_session}T15:30:00+05:30`}/></td><td><DayChange symbol={`NSE:${r.symbol}-EQ`} close={r.latest_close}/></td><td title={`${live.flow[`NSE:${r.symbol}-EQ`]?.window??'No shared feature'} · ${live.flow[`NSE:${r.symbol}-EQ`]?.computed_at_ms?formatIST(new Date(live.flow[`NSE:${r.symbol}-EQ`].computed_at_ms)):'No feature time'}`}>{n(live.flow[`NSE:${r.symbol}-EQ`]?.session_cvd,0)}</td></>}{visible.map(c=><td key={c.i} title={`Completed basis ${r.price_session??'Missing'} · ${cells(r).filter((_,i)=>/source|session/i.test(columns[i])).join(' · ')}`}>{cells(r)[c.i]}</td>)}{session&&<td>{r.price_session??'Missing'}</td>}</tr>)}</tbody></table></div>
 {!q.isLoading&&!q.isError&&!rows.length&&<p>No matching stocks.</p>}
 <div className="market-context-foot">{rows.length} stocks · observed {data?.observed_at?formatIST(data.observed_at):'—'} · click a stock for history</div>
 </Section>;
}
