import {Component,useEffect,useMemo,useState,type ReactNode} from 'react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import VanguardDesk from './nomad/components/strategies/vanguard/VanguardDesk';
import CbeDesk from './nomad/components/strategies/cbe/CbeDesk';
import {updateQuotes} from './nomad/hooks/useQuoteStore';
import {dataScope} from './nomad/lib/api';
import type {Tick} from './types';
import './nomad/nomad.generated.css';
import './lane-data.css';
import {LaneIntelligencePanel} from './LaneIntelligencePanel';
import {FeaturePipelinePanel} from './FeaturePipelinePanel';
import {InfoNote} from './nomad/components/desk-ui/InfoNote';
import {SortableTables} from './SortableTables';
import {formatIST} from './nomad/components/desk-ui';
import {useLaneLive,useLaneLiveConnection} from './nomad/hooks/useLaneLive';

class DeskBoundary extends Component<{children:ReactNode},{failed:boolean}>{state={failed:false};static getDerivedStateFromError(){return {failed:true};}render(){return this.state.failed?<div role="alert" className="rounded-xl border border-bg-border p-6">This retained view cannot be rendered from the recovered dataset. Choose another tab or return to the fresh paper book.</div>:this.props.children;}}

export function NomadLanePage({lane,ticks,connected}:{lane:'vanguard'|'cbe';ticks:Record<string,Tick>;connected:boolean}){
 const live=useLaneLive();
 const lastQuote=Math.max(0,...Object.values(live.quotes).map((q:any)=>q.exchange_ts_ms??0));
 const [scope,setScope]=useState(dataScope);const [view,setView]=useState(window.location.hash);const [notices,setNotices]=useState<string[]>([]);
 useLaneLiveConnection(scope==='shadow');
 const client=useMemo(()=>new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}}),[lane,scope]);
 useEffect(()=>{updateQuotes(ticks,connected&&scope==='shadow');},[ticks,connected,scope]);
 useEffect(()=>{const sync=()=>{setScope(dataScope());setView(window.location.hash);setNotices([]);};const failed=(e:Event)=>setNotices(v=>[...new Set([...v,(e as CustomEvent<string>).detail])].slice(-4));window.addEventListener('hashchange',sync);window.addEventListener('nomad-data-error',failed);return()=>{window.removeEventListener('hashchange',sync);window.removeEventListener('nomad-data-error',failed);};},[]);
 useEffect(()=>{setNotices([]);return()=>client.clear();},[client]);
 const choose=(next:'shadow'|'retained')=>{const p=new URLSearchParams();if(next==='retained')p.set('scope',next);window.history.replaceState(null,'',`#/${lane.toUpperCase()}${p.size?'?'+p:''}`);window.dispatchEvent(new HashChangeEvent('hashchange'));};
 return <div className="nomad-port"><div className="lane-ledger-toolbar"><label>Ledger <select aria-label="Lane ledger" value={scope} onChange={e=>choose(e.target.value as 'shadow'|'retained')}><option value="shadow">Fresh {lane==='vanguard'?'Vanguard':'CBE'}</option><option value="retained">Retained Nomad history</option></select></label><InfoNote title="Ledger information">Fresh paper cash and entries are separate from the retained Nomad book. Historical market data remains available in either view. Retained positions and returns are read only.</InfoNote>
 {scope==='shadow'&&<span role="status" className="lane-diagnostics">{live.connected?'Shared feed connected':'Shared feed reconnecting'} · Latest market quote {lastQuote?formatIST(new Date(lastQuote)):'awaiting quotes'}</span>}
 {scope==='shadow'&&<span className="lane-diagnostics">Local model &amp; data <InfoNote title="Local intelligence and data status"><LaneIntelligencePanel lane={lane}/><FeaturePipelinePanel lane={lane}/></InfoNote></span>}</div>
 {!!notices.length&&<div className="nomad-gap" role="status">Some retained datasets were outside the recovery backup. Unavailable views: {notices.map(n=>n.split('/').pop()).join(', ')}. Missing values are not new observations.</div>}
 <QueryClientProvider client={client}><DeskBoundary key={view}><div key={`${lane}-${scope}`}>{lane==='vanguard'?<SortableTables><VanguardDesk/></SortableTables>:<CbeDesk/>}</div></DeskBoundary></QueryClientProvider></div>;
}
