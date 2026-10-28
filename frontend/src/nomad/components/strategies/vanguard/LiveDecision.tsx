import {useMemo, useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {api} from '../../../lib/api';
import {Section, formatIST, formatNumber} from '../../desk-ui';
import {usePaper} from '../../../PaperLanePanel';
import {useShadow} from '../../../ShadowPanels';
import {AttritionRibbon} from './DecisionFlow';

// Owner, 28 Oct 2026: the decision flow shows the filtered best trade candidates for the current market, with
// logical filters that are not too restrictive. It re-runs the 14:47 list's own ranking and screen on the latest
// closed 30-minute bar (shadow_lanes/source.py rank_bar): hard filters only for tradability, evidence legs vote.
type Row = {symbol: string; option_type: 'CE' | 'PE'; strike: number; expiry: string; sector20?: string; source_mark: number;
  underlying_signal_close: number; combined_score: number; direction_score: number; atm_distance: number | null;
  traded_lots: number; premium_pct?: number | null; otm?: number | null; option_oi?: number; evidence: Record<string, number>; evidence_net: number; filter_reasons: string[];
  filtered: boolean; selected: boolean; horizon_sessions: number};
type Stage = {stage: string; surviving?: number; ce?: number; pe?: number};
type Model = {version: string; role: string; status: string; training_start?: string; training_end?: string; test_start?: string;
  test_end?: string; n_train?: number; n_test?: number; metrics?: any; created_at?: string; features?: number};
type Decision = {status: string; blockers?: string[]; decision_bar?: string; as_of?: string; stages?: Stage[]; rows?: Row[];
  filters?: {max_atm_distance: number; min_traded_lots: number; evidence_floor: number}; models?: Model[]; chain?: any;
  direction_model?: string; contract_model?: string; list_bar?: string};

const LEGS: [string, string][] = [['flow', 'Flow'], ['sector_rs', 'Sector RS'], ['own_side_oi', 'Own-side OI'], ['regime', 'Regime'], ['timing', 'Timing']];
const LEG_HELP: Record<string, string> = {
  flow: 'Prior-session flow score: +1 when it is 20+ in this side\'s direction, −1 when 20+ against',
  sector_rs: 'Sector relative strength z20: +1 at 0.5+ this way, −1 at 0.5+ the other way',
  own_side_oi: 'This side\'s own option OI: long build-up / short covering +1, long unwind / short build-up −1',
  regime: 'GEX regime: negative gamma lets moves extend (+1), positive gamma pins (−1); not directional',
  timing: 'Timing ignition (score 60+): +1 if the break is on this side of value, −1 if on the other',
};

export function useVanguardDecision(enabled = true) {
  return useQuery({queryKey: ['vanguard-decision'], enabled, refetchInterval: 60_000,
    queryFn: async () => (await api.get('/api/shadow-lanes/vanguard/decision', {timeout: 60_000})).data as Decision});
}

function vote(v: number) {
  return <span className={v > 0 ? 'text-emerald-300' : v < 0 ? 'text-rose-300' : 'text-text-muted'}>{v > 0 ? '+' : v < 0 ? '−' : '·'}</span>;
}

function SideTable({side, rows, published, showFiltered}: {side: 'CE' | 'PE'; rows: Row[]; published: Set<string>; showFiltered: boolean}) {
  const shown = rows.filter((r) => r.option_type === side && (showFiltered || !r.filtered));
  return <div className="overflow-auto"><table className="w-full whitespace-nowrap text-left text-xs">
    <thead><tr>{['#', 'Stock', 'Sector', 'ATM contract', 'Premium', 'Premium % of spot', 'Out of the money', 'Spot · distance', 'Lots traded', 'Payoff rank', 'Direction',
      ...LEGS.map(([, label]) => label), 'Net', 'Status'].map((h) => <th className="p-2" key={h} title={LEGS.find(([, l]) => l === h) ? LEG_HELP[LEGS.find(([, l]) => l === h)![0]] : undefined}>{h}</th>)}</tr></thead>
    <tbody>{shown.map((r, i) => <tr className={`border-t border-bg-border ${r.filtered ? 'opacity-60' : ''}`} key={r.symbol + side}>
      <td className="p-2">{i + 1}</td>
      <td className="p-2 font-medium">{r.symbol}</td>
      <td className="p-2">{r.sector20 ?? '—'}</td>
      <td className="p-2">{formatNumber(r.strike)} {side} · {String(r.expiry).slice(0, 10)}</td>
      <td className="p-2">₹{formatNumber(r.source_mark)}</td>
      <td className="p-2">{r.premium_pct == null ? '—' : `${(r.premium_pct * 100).toFixed(2)}%`}</td>
      <td className="p-2">{r.otm == null ? '—' : `${r.otm >= 0 ? '+' : ''}${(r.otm * 100).toFixed(2)}%`}</td>
      <td className="p-2">{formatNumber(r.underlying_signal_close)} · {r.atm_distance == null ? '—' : `${(r.atm_distance * 100).toFixed(1)}%`}</td>
      <td className="p-2">{formatNumber(r.traded_lots)}</td>
      <td className="p-2">{(r.combined_score * 100).toFixed(1)}</td>
      <td className="p-2">{r.direction_score.toFixed(2)}</td>
      {LEGS.map(([key]) => <td className="p-2 text-center" key={key}>{vote(r.evidence?.[key] ?? 0)}</td>)}
      <td className={`p-2 ${r.evidence_net > 0 ? 'text-emerald-300' : r.evidence_net < 0 ? 'text-rose-300' : ''}`}>{r.evidence_net > 0 ? '+' : ''}{r.evidence_net}</td>
      <td className="p-2 whitespace-normal min-w-[14rem]">{r.selected ? <b className="text-accent-cyan">Selected</b> : r.filtered ? r.filter_reasons.join(' · ') : 'Passes · below the top 10'}
        {published.has(`${r.symbol}:${side}`) && <small className="block text-text-muted">in today's 14:47 list</small>}</td>
    </tr>)}
    {!shown.length && <tr><td className="p-3 text-text-secondary" colSpan={20}>No {side} candidates at this bar.</td></tr>}</tbody>
  </table></div>;
}

// Short labels for the ribbon; the full stage text is in the table above it.
const RIBBON_LABELS = ['Universe', 'Spot bar', 'Fresh inputs', 'ATM listed', 'At the money', 'Liquid', 'Evidence ok', 'Pass all', 'Selected'];

function ribbonStages(stages: Stage[], side: 'ce' | 'pe') {
  const counts = stages.map((s) => s[side] ?? s.surviving ?? 0);
  return stages.map((s, i) => ({leg: `${side}-${i}`, stage: RIBBON_LABELS[i] ?? s.stage, surviving: counts[i],
    lost_here: i ? Math.max(0, counts[i - 1] - counts[i]) : 0, gate: s.stage}));
}

function DecisionMap({stages}: {stages: Stage[]}) {
  return <div className="grid gap-4 xl:grid-cols-2">
    {(['ce', 'pe'] as const).map((side) => {
      const rows = ribbonStages(stages, side);
      const worst = rows.slice(1, -1).reduce((a, b) => (b.lost_here > a.lost_here ? b : a), rows[1]);
      return <div key={side}><p className="mb-1 text-xs text-text-secondary">{side === 'ce' ? 'Calls' : 'Puts'}: {rows[0].surviving} → {rows[rows.length - 1].surviving} · most lost at “{worst?.gate}” (−{worst?.lost_here})</p>
        <AttritionRibbon stages={rows} binding={worst?.leg}/></div>;
    })}
  </div>;
}

export function LiveDecisionFlow() {
  const query = useVanguardDecision();
  const shadow = useShadow('vanguard');
  const [showFiltered, setShowFiltered] = useState(false);
  const d = query.data;
  const published = useMemo(() => {
    const today = (shadow.data?.publications ?? [])[0];
    return new Set<string>((today?.watchlist ?? []).map((r: any) => `${r.symbol}:${r.option_type}`));
  }, [shadow.data]);
  if (query.isError) return <Section title="Decision flow"><p role="alert">The live decision view is unavailable. No candidates have been substituted.</p></Section>;
  if (!d) return <Section title="Decision flow"><p>Ranking every F&O stock on the latest closed bar…</p></Section>;
  if (d.status !== 'ok') return <Section title="Decision flow"><p>Blocked: {(d.blockers ?? []).join(' · ')}</p></Section>;
  const rows = d.rows ?? [];
  const f = d.filters;
  return <div className="space-y-4">
    <Section title="Decision flow · best trade candidates now" description={`Decision bar ${formatIST(d.decision_bar)} · checked ${formatIST(d.as_of)}. The 14:47 list runs this same ranking and screen on the 14:15 bar.`}>
      <div className="overflow-auto"><table className="text-left text-xs"><thead><tr><th className="p-2">Stage</th><th className="p-2">Stocks</th><th className="p-2">CE</th><th className="p-2">PE</th></tr></thead>
        <tbody>{(d.stages ?? []).map((s) => <tr className="border-t border-bg-border" key={s.stage}><td className="p-2">{s.stage}</td><td className="p-2">{s.surviving ?? '—'}</td><td className="p-2">{s.ce ?? '—'}</td><td className="p-2">{s.pe ?? '—'}</td></tr>)}</tbody></table></div>
      <div className="mt-4"><DecisionMap stages={d.stages ?? []}/></div>
      {f && <p className="mt-3 text-xs text-text-secondary">Hard filters, tradability only: the ATM leg within {Math.round(f.max_atm_distance * 100)}% of spot and at least {f.min_traded_lots} lots traded by the bar. Evidence legs vote (+1 / −1 / 0) instead of vetoing one by one; a pick is dropped only when the net is {f.evidence_floor} or worse. The rest are ranked furthest out of the money first (still within the ATM band), then cheapest premium as a share of spot. Found on October 2026 and checked on September: it caught 16.7% / 12.9% of each day's top-10 performers over 1–3 sessions (random 6.7% / 9.2%), paper +19.6% / +31.8% a pick. Cheap options win through leverage per rupee, not low implied volatility (low IV alone was no better than random). The payoff ranker and the votes break ties; the List tests keep scoring it.</p>}
      <label className="mt-2 flex items-center gap-2 text-xs"><input type="checkbox" checked={showFiltered} onChange={(e) => setShowFiltered(e.target.checked)}/> Show filtered candidates with their reasons</label>
    </Section>
    <Section title="Calls · CE" description={`${rows.filter((r) => r.option_type === 'CE' && !r.filtered).length} pass the screen`}><SideTable side="CE" rows={rows} published={published} showFiltered={showFiltered}/></Section>
    <Section title="Puts · PE" description={`${rows.filter((r) => r.option_type === 'PE' && !r.filtered).length} pass the screen`}><SideTable side="PE" rows={rows} published={published} showFiltered={showFiltered}/></Section>
  </div>;
}

function metricLines(metrics: any): [string, string][] {
  const out: [string, string][] = [];
  const walk = (value: any, path: string) => {
    if (out.length >= 10 || value == null) return;
    if (typeof value === 'number') out.push([path, Math.abs(value) < 10 ? value.toFixed(3) : formatNumber(value)]);
    else if (typeof value === 'object' && !Array.isArray(value)) Object.entries(value).forEach(([k, v]) => walk(v, path ? `${path}.${k}` : k));
  };
  walk(metrics, '');
  return out;
}

const pct = (v: number | null | undefined) => v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;

export function LiveModels() {
  const query = useVanguardDecision();
  const paper = usePaper('vanguard').data;
  const d = query.data;
  const tests = paper?.list_tests ?? [];
  const selected = (d?.rows ?? []).filter((r) => r.selected);
  return <div className="space-y-4">
    <Section title="Models in use" description="The lane ranks with these two frozen rankers, then the decision screen and the local review admit entries. Rankers order candidates; they are not profit probabilities.">
      {!d ? <p>Loading…</p> : <div className="grid gap-3 xl:grid-cols-2">{(d.models ?? []).map((m) => <div className="rounded-lg border border-bg-border p-3 text-xs" key={m.role}>
        <b>{m.role === 'contract' ? 'Option-payoff ranker (orders contracts)' : 'Direction ranker (orders stocks per side)'}</b>
        <p className="mt-1">{m.version} · {m.status} · {m.features ?? '—'} features · built {formatIST(m.created_at)}</p>
        <p className="mt-1 text-text-secondary">Trained {m.training_start ?? '—'} → {m.training_end ?? '—'} ({formatNumber(m.n_train ?? 0)} rows) · tested {m.test_start ?? '—'} → {m.test_end ?? '—'} ({formatNumber(m.n_test ?? 0)} rows)</p>
        {metricLines(m.metrics).length > 0 && <table className="mt-2"><tbody>{metricLines(m.metrics).map(([k, v]) => <tr key={k}><td className="pr-3 text-text-muted">{k}</td><td>{v}</td></tr>)}</tbody></table>}
      </div>)}</div>}
    </Section>
    <Section title="Current market status" description={d?.decision_bar ? `Latest closed bar ${formatIST(d.decision_bar)}` : undefined}>
      {d?.status === 'ok' ? <div className="text-xs space-y-1">
        <p>{(d.stages ?? [])[2]?.surviving ?? '—'} of {(d.stages ?? [])[0]?.surviving ?? '—'} stocks have fresh inputs; {(d.stages ?? [])[7]?.ce ?? 0} calls and {(d.stages ?? [])[7]?.pe ?? 0} puts pass the screen.</p>
        <p>Top calls now: {selected.filter((r) => r.option_type === 'CE').map((r) => r.symbol).join(', ') || '—'}</p>
        <p>Top puts now: {selected.filter((r) => r.option_type === 'PE').map((r) => r.symbol).join(', ') || '—'}</p>
        <p className="text-text-secondary">Chain at {formatIST(d.chain?.chain_ts)} · {formatNumber(d.chain?.universe_rows ?? 0)} contracts ranked.</p>
      </div> : <p className="text-xs">{d ? (d.blockers ?? []).join(' · ') : 'Loading…'}</p>}
    </Section>
    <Section title="How the lists are doing" description="Each published list, entered as paper positions and held to the third session or the stop. Return per pick at each session's close, before costs.">
      {!tests.length ? <p className="text-xs">No list results yet.</p> : <div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{['List', 'Picks / filled', 'Stopped', 'D1', 'D2', 'D3', 'CE D1 · D2 · D3', 'PE D1 · D2 · D3'].map((h) => <th className="p-2" key={h}>{h}</th>)}</tr></thead>
        <tbody>{tests.map((t: any) => <tr className="border-t border-bg-border" key={t.cohort}><td className="p-2">{t.source_session}{t.cohort.endsWith(':owner-convert') ? ' (conversion)' : ''}</td><td className="p-2">{t.picks} / {t.filled}</td><td className="p-2">{t.stopped}</td>
          {t.mean_returns.map((v: number | null, i: number) => <td className="p-2" key={i}>{pct(v)}<small className="text-text-muted"> n{t.observed[i]}</small></td>)}
          {['CE', 'PE'].map((side) => <td className="p-2" key={side}>{t.by_side?.[side] ? t.by_side[side].mean_returns.map(pct).join(' · ') : '—'}</td>)}</tr>)}</tbody></table></div>}
      <p className="mt-2 text-xs text-text-muted">A handful of lists cannot prove or disprove the rankers; read these as "not proven yet" until several weeks accumulate.</p>
    </Section>
  </div>;
}
