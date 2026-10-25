import {Section,formatIST} from '@/components/desk-ui';
import {useShadow} from '@/ShadowPanels';
import {usePaper} from '@/PaperLanePanel';
export function StrategyLiveStream(_props:Record<string,unknown>){const {data}=useShadow('cbe');const paper=usePaper('cbe').data;const rows=paper?.enabled?paper.events:data?.journal;return <Section title="CBE paper event stream" description="Recorded candidate and paper execution events.">{(rows??[]).map((row:any,i:number)=><p className="border-b border-bg-border p-2 text-xs" key={i}>{formatIST(row.at)} · {row.kind} · {row.detail?.reason??(row.detail?.blockers??[]).join(' · ')}</p>)}</Section>;}
