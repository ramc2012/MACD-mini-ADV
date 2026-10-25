import {Section,formatIST} from '@/components/desk-ui';
import {useShadow} from '@/ShadowPanels';
export function StrategyLiveStream(_props:Record<string,unknown>){const {data}=useShadow('cbe');return <Section title="CBE observation stream" description="Recorded shadow events; market quotes use MACD's existing live feed.">{(data?.journal??[]).map((row:any,i:number)=><p className="border-b border-bg-border p-2 text-xs" key={i}>{formatIST(row.at)} · {row.kind} · {(row.detail?.blockers??[]).join(' · ')}</p>)}</Section>;}
