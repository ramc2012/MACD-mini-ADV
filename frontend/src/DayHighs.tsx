import { useEffect, useMemo, useState } from "react";
import { SortableTable, type Column, type SortState } from "./Ledger";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";

type Row = {
  symbol: string; label: string; group: string; index: boolean; bar_start: number; stale: boolean;
  close: number; day_high: number; day_low: number; high_before: number; low_before: number;
  at_high: boolean; at_low: boolean; high_streak: number; low_streak: number;
  from_high_pct: number; from_low_pct: number; change_pct: number | null; bars: number;
};
type Payload = {
  session: string; as_of: number | null; as_of_end: number | null; generated_at: string; rows: Row[];
  unavailable: { symbol: string; label: string; reason: string }[]; evaluated: number; at_high: number; at_low: number;
  rules: Record<string, string>;
};
type Extreme = "HIGH" | "LOW";
type Scope = "ALL" | "INDEX" | "STOCK";

const clock = (seconds: number) => new Date(seconds * 1000).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
const dayLabel = (day: string) => new Date(`${day}T00:00:00+05:30`).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "short", day: "2-digit", month: "short", year: "numeric" });
const price = (value: number) => value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
const pct = (value: number | null) => value === null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
const sign = (value: number | null): "positive" | "negative" | "" => value === null || value === 0 ? "" : value > 0 ? "positive" : "negative";

/** Spots whose last completed 15-minute bar closed at a new day high (or low): owner, 4 Nov 2026. */
export function DayHighs({ onOpen }: { onOpen?: (symbol: string) => void }) {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
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
  const rows = useMemo(() => (data?.rows || []).filter((row) =>
    (high ? row.at_high : row.at_low)
    && (scope === "ALL" || (scope === "INDEX") === row.index)
    && `${row.label} ${row.group}`.toLowerCase().includes(query.trim().toLowerCase())), [data, high, scope, query]);

  const columns: Column<Row>[] = [
    { key: "label", label: "Instrument", value: (row) => row.label, render: (row) => <>{row.label}{row.stale && <small className="negative" title={`Last stored bar ${clock(row.bar_start)} IST: the feed missed later bars`}> · stale</small>}</> },
    { key: "group", label: "Sector", value: (row) => row.group },
    { key: "close", label: "15-min close", value: (row) => row.close, render: (row) => price(row.close) },
    { key: "level", label: high ? "Earlier day high" : "Earlier day low", value: (row) => high ? row.high_before : row.low_before, render: (row) => price(high ? row.high_before : row.low_before) },
    { key: "beyond", label: high ? "Close above it" : "Close below it", value: (row) => (row.close / (high ? row.high_before : row.low_before) - 1) * 100,
      render: (row) => pct((row.close / (high ? row.high_before : row.low_before) - 1) * 100) },
    { key: "wick", label: high ? "From day high" : "From day low", value: (row) => high ? row.from_high_pct : row.from_low_pct, render: (row) => pct(high ? row.from_high_pct : row.from_low_pct) },
    { key: "streak", label: "Bars in a row", value: (row) => high ? row.high_streak : row.low_streak },
    { key: "change", label: "Change today", value: (row) => row.change_pct ?? -Infinity, render: (row) => pct(row.change_pct), tone: (row) => sign(row.change_pct) },
  ];

  const asOf = data?.as_of ? `${clock(data.as_of)}–${clock(data.as_of_end ?? data.as_of + 900)} IST bar` : "no completed bar yet";
  return <section className="panel atm-heatmap">
    <div className="heatmap-toolbar">
      <div className="risk-tabs" role="group" aria-label="Extreme">
        <button aria-pressed={high} onClick={() => setExtreme("HIGH")}>At day high <b>{data?.at_high ?? "…"}</b></button>
        <button aria-pressed={!high} onClick={() => setExtreme("LOW")}>At day low <b>{data?.at_low ?? "…"}</b></button>
      </div>
      <div className="risk-tabs" role="group" aria-label="Universe">{(["ALL", "INDEX", "STOCK"] as const).map((key) =>
        <button key={key} aria-pressed={scope === key} onClick={() => setScope(key)}>{key === "ALL" ? "All" : key === "INDEX" ? "Indices" : "Stocks"}</button>)}</div>
      <input className="heatmap-search" placeholder="Filter instrument or sector" value={query} onChange={(event) => setQuery(event.target.value)} />
    </div>
    <div className="heatmap-meta">{data
      ? <>{dayLabel(data.session)} · {asOf} · {rows.length} of {data.rows.length} spots closed the bar {high ? "above every earlier high" : "below every earlier low"} of the day · first list 09:45 IST · refreshes every 30 s{data.unavailable.length > 0 && <> · {data.unavailable.length} without bars</>}</>
      : error || "Loading…"}</div>
    <SortableTable rows={rows} columns={columns} sort={sort} empty={data ? `No spot closed the last 15-min bar at a new day ${high ? "high" : "low"}` : "Loading…"}
      onSort={(key) => setSort((old) => ({ key, direction: old.key === key && old.direction === "desc" ? "asc" : "desc" }))}
      rowAction={onOpen ? (row) => onOpen(row.symbol) : undefined} />
    <details className="heatmap-meta"><summary>ⓘ Rules</summary><ul>{Object.entries(data?.rules || {}).map(([key, text]) => <li key={key}>{text}</li>)}</ul></details>
  </section>;
}
