"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Section, formatIST } from "@/components/desk-ui";

const pct = (n: unknown) => typeof n === "number" && Number.isFinite(n) ? `${(100*n).toFixed(2)}%` : "—";
const label = (n: unknown) => String(n ?? "awaiting report").replaceAll("_", " ");

export default function EquityChallenger() {
  const query = useQuery({ queryKey: ["vanguard", "stock-challenger"],
    queryFn: async () => (await api.get("/api/vanguard/stock-challenger")).data,
    refetchInterval: 60000 });
  const d = query.data;
  const horizons: any[] = Array.isArray(d?.horizons) ? d.horizons :
    Object.entries(d?.horizons ?? {}).map(([key,value]) => ({ horizon_sessions: Number(key), ...(typeof value === "object" && value ? value : {}) }));
  return <div className="space-y-5">
    <Section title="Stock challenger" description="Separate equity-return research for 1, 5 and 21 sessions. It does not place trades or replace the frozen option ranking.">
      {query.isLoading ? <p>Loading the research report…</p> : query.isError ? <p role="alert">The stock research report could not be loaded.</p> : <>
        <p className="text-sm text-amber-200">{label(d?.status)} · shadow research · promotion disabled</p>
        {d?.detail && <p className="mt-2 text-sm text-text-secondary">{d.detail}</p>}
        {(d?.generated_at_utc || d?.generated_at || d?.as_of) && <p className="mt-2 text-xs text-text-muted">Report {formatIST(d.generated_at_utc ?? d.generated_at ?? d.as_of)}{d.prospective_start ? ` · Prospective evaluation begins ${d.prospective_start}` : ""}</p>}
        <p className="mt-3 text-sm text-text-secondary">Historical reconstructed prices are diagnostics. Fresh snapshots, completed holding periods, realistic execution costs and independent validation are required before a model can be considered for paper admission.</p>
        {d?.costscenarios && <p className="mt-2 text-xs text-text-muted">Cost assumptions: {Object.entries(d.costscenarios).map(([key,value]: [string,any]) => `${label(key)} ${value.round_trip_bps} bps round trip, ${value.short_borrow_annual_pct}% annual short borrowing`).join("; ")}. These are scenarios, not verified execution or borrow quotes.</p>}
        {(d?.blockers ?? []).length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-amber-200">{d.blockers.map((text: any, i: number) => <li key={i}>{typeof text === "string" ? text : text.detail ?? text.reason}</li>)}</ul>}
      </>}
    </Section>
    {horizons.length > 0 && <Section title="Horizon comparisons" description="Compare each stock target with the same-date benchmarks; overlapping holding periods are correlated.">
      <p className="mb-3 text-xs text-text-muted">Mean net return is per source-session cohort after modeled costs, not a compounded account return. The model target is stock excess return versus Nifty.</p>
      <div className="overflow-auto"><table className="w-full whitespace-nowrap text-left text-xs"><thead><tr>{["Hold", "Diagnostic dates", "Complete sessions / blocks", "Stock observations", "Mean net return", "Assessment"].map(text => <th className="p-3" key={text}>{text}</th>)}</tr></thead>
        <tbody>{horizons.map((h,i) => <tr key={h.horizon_sessions ?? i} className="border-t border-bg-border">
          <td className="p-3">{h.horizon_sessions ?? h.horizon ?? "—"}</td>
          <td className="p-3">{h.counts?.diagnostic?.test?.start ?? "—"} to {h.counts?.diagnostic?.test?.end ?? "—"}</td>
          <td className="p-3">{h.metrics?.complete_source_sessions ?? "—"} / {h.metrics?.nonoverlapping_blocks ?? "—"}</td>
          <td className="p-3">{h.metrics?.selected_stock_observations ?? "—"}</td>
          <td className="p-3">{pct(h.metrics?.mean_net_return)}</td>
          <td className="p-3">{label(h.status ?? h.assessment ?? "diagnostic only")}</td>
        </tr>)}</tbody>
      </table></div>
      {horizons.map((h,i) => <details key={h.horizon_sessions ?? i} className="mt-3 border-t border-bg-border pt-3"><summary className="cursor-pointer text-sm text-text-secondary">{h.horizon_sessions} session baselines and availability</summary><p className="mt-2 text-xs text-amber-200">Features recorded by the historical decision: {h.counts?.features_recorded_by_historical_decision ?? "unavailable"}. Recorded-availability training examples: {h.counts?.recorded_availability_audit?.train?.examples ?? "unavailable"}. Retrospective diagnostics do not establish point-in-time availability.</p><div className="mt-2 overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{["Baseline", "Complete sessions", "Mean net return"].map(t=><th className="p-2" key={t}>{t}</th>)}</tr></thead><tbody>{Object.entries(h.baselines ?? {}).map(([key,b]: [string,any])=><tr className="border-t border-bg-border" key={key}><td className="p-2">{label(key)}</td><td className="p-2">{b.complete_source_sessions ?? "—"}</td><td className="p-2">{pct(b.mean_net_return)}</td></tr>)}</tbody></table></div></details>)}
      {(d?.limitations ?? []).length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-text-muted">{d.limitations.map((text: string, i: number) => <li key={i}>{text}</li>)}</ul>}
    </Section>}
  </div>;
}
