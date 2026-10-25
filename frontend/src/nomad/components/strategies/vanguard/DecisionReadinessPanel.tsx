"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Section, formatIST } from "@/components/desk-ui";

const words = (value: unknown) => String(value ?? "awaiting evidence").replaceAll("_", " ");
const observation = (stage: any) => stage.head ?? stage.received_at ?? stage.latest_at ?? stage.latest_ts ?? (typeof stage.latest === "string" ? stage.latest : stage.latest?.decision_at);

export default function DecisionReadinessPanel() {
  const query = useQuery({ queryKey: ["vanguard", "readiness"],
    queryFn: async () => (await api.get("/api/vanguard/readiness")).data,
    refetchInterval: 30000 });
  const d = query.data;
  const stages: any[] = Array.isArray(d?.stages) ? d.stages :
    Object.entries(d?.stages ?? {}).map(([key, value]) => ({ key, ...(typeof value === "object" && value ? value : {}) }));
  return <Section title="Decision readiness" description="Current inputs and publication deadlines are checked separately from worker uptime.">
    {query.isLoading ? <p>Checking decision inputs…</p> : query.isError ?
      <p role="alert">Decision readiness could not be loaded. New entries require current evidence.</p> : <>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className={d?.selection_allowed ? "text-emerald-300" : "text-amber-200"}>{words(d?.status)}</span>
        {d?.phase && <span className="text-text-secondary">{words(d.phase)}</span>}
        {d?.observed_at && <span className="text-text-muted">Checked {formatIST(d.observed_at)}</span>}
      </div>
      {d?.detail && <p className="mt-2 text-sm text-text-secondary">{d.detail}</p>}
      {d?.artifact_stale && <p role="alert" className="mt-2 text-amber-200">Readiness reporting is stale. New decisions wait for a current check.</p>}
      <p className="mt-2 text-xs text-text-muted">New selections: {d?.selection_allowed ? "current evidence permits evaluation" : "waiting"}. New publications: {d?.publication_allowed ? "window open" : "waiting"}. New MACD paper entries need current data and Qwen approval. Paper exits run independently; retained Nomad positions remain frozen. Missed fills are not recreated.</p>
      {(d?.blockers ?? []).length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-amber-200">{d.blockers.map((reason: any, index: number) => <li key={index}>{typeof reason === "string" ? reason : reason.detail ?? reason.reason ?? words(reason.stage)}</li>)}</ul>}
      {(d?.missed_sessions ?? []).length > 0 && <p className="mt-3 text-sm text-amber-200">Missed publication: {d.missed_sessions.map((s: any) => `${s.session} (${words(s.status)})`).join(", ")}. The frozen list remains the last actual publication.</p>}
      {stages.length > 0 && <div className="mt-3 overflow-auto"><table className="w-full text-left text-xs">
        <thead><tr>{["Input", "State", "Latest observation", "Coverage"].map(label => <th className="p-2" key={label}>{label}</th>)}</tr></thead>
        <tbody>{stages.map((stage, index) => <tr className="border-t border-bg-border" key={stage.key ?? stage.name ?? index}>
          <td className="p-2">{words(stage.label ?? stage.key ?? stage.name)}</td>
          <td className="p-2">{words(stage.status)}</td>
          <td className="p-2">{observation(stage) ? formatIST(observation(stage)) : "—"}</td>
          <td className="p-2">{stage.recent_breadth ?? stage.symbols ?? stage.symbol_count ?? stage.breadth ?? stage.latest?.item_count ?? "—"}{stage.required_breadth != null ? ` / ${stage.required_breadth}` : ""}</td>
        </tr>)}</tbody>
      </table></div>}
    </>}
  </Section>;
}
