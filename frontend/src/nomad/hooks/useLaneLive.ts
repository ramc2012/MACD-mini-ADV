import {useEffect,useSyncExternalStore} from 'react';
import {API_URL,API_TOKEN} from '../../runtime';
import {updateSharedQuotes} from './useQuoteStore';
type Live={connected:boolean;market_open:boolean;cash_open:boolean;received_ticks:number;reused_features:number;last_message_at?:string;error?:string;flow:Record<string,any>;quotes:Record<string,any>};
let current:Live={connected:false,market_open:false,cash_open:false,received_ticks:0,reused_features:0,flow:{},quotes:{}};
const listeners=new Set<()=>void>();
const subscribe=(cb:()=>void)=>{listeners.add(cb);return()=>{listeners.delete(cb);};};
function publish(data:Live){current=data;updateSharedQuotes(data.quotes,data.connected);listeners.forEach(cb=>cb());}
export function useLaneLive(){return useSyncExternalStore(subscribe,()=>current,()=>current);}
// One primitive out of each frame, so a cell re-renders only when its own value changes rather than on every frame.
export function useLaneLiveValue<T extends string|number|boolean|null|undefined>(select:(live:Live)=>T){return useSyncExternalStore(subscribe,()=>select(current),()=>select(current));}
export function useLaneLiveConnection(enabled:boolean){useEffect(()=>{if(!enabled)return;const controller=new AbortController();let retry:ReturnType<typeof setTimeout>;const connect=async()=>{try{const r=await fetch(`${API_URL}/api/lane-live/stream`,{headers:API_TOKEN?{'X-Macd-Token':API_TOKEN}:{},signal:controller.signal});if(!r.ok||!r.body)throw Error();const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const part=await reader.read();if(part.done)throw Error();buffer+=decoder.decode(part.value,{stream:true});let end;while((end=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,end);buffer=buffer.slice(end+2);if(frame.startsWith('data: '))publish(JSON.parse(frame.slice(6)));}}}catch{if(controller.signal.aborted)return;publish({...current,connected:false});retry=setTimeout(connect,2000);}};void connect();return()=>{controller.abort();clearTimeout(retry);updateSharedQuotes({},false);};},[enabled]);}
