import {Component,useEffect,useMemo,useState,type ReactNode} from 'react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import VanguardDesk from './nomad/components/strategies/vanguard/VanguardDesk';
import CbeDesk from './nomad/components/strategies/cbe/CbeDesk';
import {updateQuotes} from './nomad/hooks/useQuoteStore';
import {dataScope} from './nomad/lib/api';
import type {Tick} from './types';
import './nomad/nomad.generated.css';
import {LocalModelPanel} from './LocalModelPanel';
import {FeaturePipelinePanel} from './FeaturePipelinePanel';

class DeskBoundary extends Component<{children:ReactNode},{failed:boolean}>{state={failed:false};static getDerivedStateFromError(){return {failed:true};}render(){return this.state.failed?<div role="alert" className="rounded-xl border border-bg-border p-6">This retained view cannot be rendered from the recovered dataset. Choose another tab or return to the fresh paper book.</div>:this.props.children;}}

export function NomadLanePage({lane,ticks,connected}:{lane:'vanguard'|'cbe';ticks:Record<string,Tick>;connected:boolean}){
 const [scope,setScope]=useState(dataScope);const [view,setView]=useState(window.location.hash);const [notices,setNotices]=useState<string[]>([]);
 const client=useMemo(()=>new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}}),[lane,scope]);
 useEffect(()=>{updateQuotes(ticks,connected&&scope==='shadow');},[ticks,connected,scope]);
 useEffect(()=>{const sync=()=>{setScope(dataScope());setView(window.location.hash);setNotices([]);};const failed=(e:Event)=>setNotices(v=>[...new Set([...v,(e as CustomEvent<string>).detail])].slice(-4));window.addEventListener('hashchange',sync);window.addEventListener('nomad-data-error',failed);return()=>{window.removeEventListener('hashchange',sync);window.removeEventListener('nomad-data-error',failed);};},[]);
 useEffect(()=>{setNotices([]);return()=>client.clear();},[client]);
 const choose=(next:'shadow'|'retained')=>{const p=new URLSearchParams();if(next==='retained')p.set('scope',next);window.history.replaceState(null,'',`#/${lane.toUpperCase()}${p.size?'?'+p:''}`);window.dispatchEvent(new HashChangeEvent('hashchange'));};
 return <div className="nomad-port"><div className="nomad-scope-bar"><div><b>{scope==='shadow'?'Fresh MACD paper lane':'Retained Nomad history · read only'}</b><p>{scope==='shadow'?'Independent paper cash, Qwen-reviewed entries and managed exits. Research and legacy panels use retained Nomad data.':'Original frozen lists, positions and recorded returns. Retired workers do not manage these accounts.'}</p></div><div role="group" aria-label="Lane data scope"><button onClick={()=>choose('shadow')} aria-pressed={scope==='shadow'}>MACD paper</button><button onClick={()=>choose('retained')} aria-pressed={scope==='retained'}>Nomad history</button></div></div>
 {!!notices.length&&<div className="nomad-gap" role="status">Some retained datasets were outside the recovery backup. Unavailable views: {notices.map(n=>n.split('/').pop()).join(', ')}. Missing values are not new observations.</div>}
 {scope==='shadow'&&<><LocalModelPanel/><FeaturePipelinePanel lane={lane}/></>}
 <QueryClientProvider client={client}><DeskBoundary key={view}><div key={`${lane}-${scope}`}>{lane==='vanguard'?<VanguardDesk/>:<CbeDesk/>}</div></DeskBoundary></QueryClientProvider></div>;
}
