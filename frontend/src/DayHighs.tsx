import { useEffect, useMemo, useState } from "react";
import { SortableTable, type Column, type SortState } from "./Ledger";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";

type Kind = "SPOT" | "CE" | "PE";
type Row = {
  symbol: string; kind: Kind; label: string; group: string; index: boolean; bar_start: number; stale: boolean;
  strike?: number; expiry?: string;
  close: number; day_high: number; day_low: number; high_before: number; low_before: number;
  at_high: boolean; at_low: boolean; high_streak: number; low_streak: number;
  from_high_pct: number; from_low_pct: number; change_pct: number | null; bars: number;
};
type Payload = {
  session: string; as_of: Record<Kind, number | null>; generated_at: string; rows: Row[];
  unavailable: { symbol: string; kind: Kind; label: string; reason: string }[]; evaluated: number;
  at_high: Record<Kind, number>; at_low: Record<Kind, number>; listed: Record<Kind, number>;
  rules: Record<string, string>;
};
type Extreme = "HIGH" | "LOW";
type Scope = "ALL" | "INDEX" | "STOCK";

const KINDS: readonly (readonly [Kind, string])[] = [["SPOT", "Spot"], ["CE", "ATM CE"], ["PE", "ATM PE"]];
const clock = (seconds: number) => new Date(seconds * 1000).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
const dayLabel = (day: string) => new Date(`${day}T00:00:00+05:30`).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "short", day: "2-digit", month: "short", year: "numeric" });
const price = (value: number) => value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
const pct = (value: number | null) => value === null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
const sign = (value: number | null): "positive" | "negative" | "" => value === null || value === 0 ? "" : value > 0 ? "positive" : "negative";
const name = (row: Row) => row.kind === "SPOT" ? row.label : `${row.label} ${row.strike ?? ""} ${row.kind}`;

/** Spots and today's ATM CE / PE legs whose last completed 15-minute bar closed at a new day high (or low):
 *  owner, 4 Nov 2026. */
export function DayHighs({ onOpen }: { onOpen?: (symbol: string) => void }) {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const [kind, setKind] = useState<Kind>("SPOT");
  const [extreme, setExtreme] = useState<Extreme>("HIGH");
  const [scope, setScope] = useState<Scope>("ALL");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortState>({ key: "streak", direction: "desc" });

  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const controller = new AbortController();
    const load = () => void requestJson<Payload>(`${API_URL}/api/day-highs`, { headers, signal: controller.signal })
      .then((payload) => { setData(payload); setError(""); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unavailable"); });
    load();
    const timer = setInterval(load, 30_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);

  const high = extreme === "HIGH";
  const option = kind !== "SPOT";
  const rows = useMemo(() => (data?.rows || []).filter((row) =>
    row.kind === kind
    && (high ? row.at_high : row.at_low)
    && (scope === "ALL" || (scope === "INDEX") === row.index)
    && `${name(row)} ${row.group}`.toLowerCase().includes(query.trim().toLowerCase())), [data, kind, high, scope, query]);

  const level = (row: Row) => high ? row.high_before : row.low_before;
  const columns: Column<Row>[] = [
    { key: "label", label: option ? "Contract" : "Instrument", value: name, render: (row) => <>{name(row)}{row.stale && <small className="negative" title={`Last stored bar ${clock(row.bar_start)} IST: no trade or feed in later bars`}> · stale</small>}</> },
    ...(option ? [{ key: "expiry", label: "Expiry", value: (row: Row) => row.expiry ?? "" }] : []),
    { key: "group", label: "Sector", value: (row) => row.group },
    { key: "close", label: option ? "15-min premium close" : "15-min close", value: (row) => row.close, render: (row) => price(row.close) },
    { key: "level", label: high ? "Earlier day high" : "Earlier day low", value: level, render: (row) => price(level(row)) },
    { key: "beyond", label: high ? "Close above it" : "Close below it", value: (row) => (row.close / level(row) - 1) * 100, render: (row) => pct((row.close / level(row) - 1) * 100) },
    { key: "wick", label: high ? "From day high" : "From day low", value: (row) => high ? row.from_high_pct : row.from_low_pct, render: (row) => pct(high ? row.from_high_pct : row.from_low_pct) },
    { key: "streak", label: "Bars in a row", value: (row) => high ? row.high_streak : row.low_streak },
    { key: "change", label: "Change today", value: (row) => row.change_pct ?? -Infinity, render: (row) => pct(row.change_pct), tone: (row) => sign(row.change_pct) },
  ];

  const barStart = data?.as_of[kind];
  const asOf = barStart ? `${clock(barStart)}–${clock(barStart + 900)} IST bar` : "no completed bar yet";
  const noun = option ? `ATM ${kind} legs` : "spots";
  const count = (counts: Record<Kind, number> | undefined) => counts?.[kind] ?? "…";
  return <section className="panel atm-heatmap">
    <div className="heatmap-toolbar">
      <div className="risk-tabs" role="group" aria-label="Instrument">{KINDS.map(([key, label]) =>
        <button key={key} aria-pressed={kind === key} onClick={() => setKind(key)}>{label} <b>{data ? (high ? data.at_high : data.at_low)[key] : "…"}</b></button>)}</div>
      <div className="risk-tabs" role="group" aria-label="Extreme">
        <button aria-pressed={high} onClick={() => setExtreme("HIGH")}>At day high <b>{count(data?.at_high)}</b></button>
        <button aria-pressed={!high} onClick={() => setExtreme("LOW")}>At day low <b>{count(data?.at_low)}</b></button>
      </div>
      <div className="risk-tabs" role="group" aria-label="Universe">{(["ALL", "INDEX", "STOCK"] as const).map((key) =>
        <button key={key} aria-pressed={scope === key} onClick={() => setScope(key)}>{key === "ALL" ? "All" : key === "INDEX" ? "Indices" : "Stocks"}</button>)}</div>
      <input className="heatmap-search" placeholder="Filter instrument or sector" value={query} onChange={(event) => setQuery(event.target.value)} />
    </div>
    <div className="heatmap-meta">{data
      ? <>{dayLabel(data.session)} · {asOf} · {rows.length} of {data.listed[kind]} {noun} closed the bar {high ? "above every earlier high" : "below every earlier low"} of the day{option ? " (own premium)" : ""} · first list 09:45 IST · refreshes every 30 s{data.unavailable.filter((row) => row.kind === kind).length > 0 && <> · {data.unavailable.filter((row) => row.kind === kind).length} without bars</>}</>
      : error || "Loading…"}</div>
    <SortableTable rows={rows} columns={columns} sort={sort} empty={data ? `No ${option ? `ATM ${kind}` : "spot"} closed the last 15-min bar at a new day ${high ? "high" : "low"}` : "Loading…"}
      onSort={(key) => setSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
      rowAction={onOpen ? (row) => onOpen(row.symbol) : undefined} />
    <details className="heatmap-meta"><summary>ⓘ Rules</summary><ul>{Object.entries(data?.rules || {}).map(([key, text]) => <li key={key}>{text}</li>)}</ul></details>
  </section>;
}
