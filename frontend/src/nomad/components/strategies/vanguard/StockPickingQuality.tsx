"use client";

export type StockQuality = {
  model_version: string;
  side: string;
  timeframe: string;
  holding_sessions: number;
  selected_n: number;
  eligible_n: number;
  excluded_n: number;
  awaiting_refresh_n: number;
  n: number;
  sessions: number;
  pending_n: number;
  missing_n: number;
  mean_return: number | null;
  median_return: number | null;
  hit_rate: number | null;
  mean_session_return: number | null;
  benchmark_n: number;
  benchmark_sessions: number;
  mean_session_excess: number | null;
  benchmark_beat_rate: number | null;
};

const pct = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(2)}%`;

export type StockObservation = {
  entry_session: string;
  entry_mark: number | null;
  status: string;
  horizons: Record<string, { session: string; data_state: string; direction_return: number | null; excess_return: number | null }>;
};

export function StockPickDetail({ stock }: { stock?: StockObservation }) {
  if (!stock) return <p className="my-3 text-xs text-text-muted">Stock observations are awaiting the archive refresh.</p>;
  return <div className="my-4 rounded-lg border border-bg-border p-3">
    <p className="text-xs text-text-secondary">Stock entry {stock.entry_session} · ₹{stock.entry_mark?.toLocaleString("en-IN") ?? "—"} · {stock.status.replaceAll("_", " ")}</p>
    <div className="mt-3 grid gap-3 sm:grid-cols-3">{["daily", "weekly", "monthly"].map(name => {
      const point = stock.horizons[name];
      return <div key={name} className="rounded-lg bg-bg-primary/50 p-3">
        <p className="text-xs capitalize text-text-muted">{name} · {point?.session ?? "—"}</p>
        <p className="mt-1 font-mono">{pct(point?.direction_return ?? null)}</p>
        <p className="mt-1 text-xs text-text-secondary">{point?.data_state === "observed" ? `Nifty excess ${pct(point.excess_return)}` : point?.data_state ?? "awaiting refresh"}</p>
      </div>;
    })}</div>
  </div>;
}

export default function StockPickingQuality({ rows, basis, scope, onScopeChange }: {
  rows: StockQuality[];
  basis?: string;
  scope: string;
  onScopeChange: (scope: string) => void;
}) {
  return (
    <section className="rounded-xl border border-bg-border bg-bg-secondary/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold">Stock-picking skill · daily, weekly, monthly</h3>
          <p className="mt-1 text-xs text-text-secondary">Same frozen picks, three holding periods. CE tests rising stocks; PE tests falling stocks.</p>
        </div>
        <select aria-label="Stock evaluation scope" value={scope} onChange={e => onScopeChange(e.target.value)}
          className="rounded-lg border border-bg-border bg-bg-primary px-3 py-2 text-sm">
          <option value="all">All research picks</option>
          <option value="qualified">Qualified picks only</option>
        </select>
      </div>
      <p className="mt-3 text-xs text-text-secondary">{basis}</p>
      <div className="mt-4 overflow-auto">
        <table className="w-full whitespace-nowrap text-xs">
          <thead><tr>{["Frozen model / side", "Holding period", "Scored / eligible", "Source days", "Mean / median stock", "Direction hit rate", "Mean per source day", "Nifty excess per day", "Beats Nifty", "Benchmark rows / days", "Pending / missing", "Excluded / awaiting refresh"].map(label => (
            <th key={label} className="p-3 text-left">{label}</th>
          ))}</tr></thead>
          <tbody>{rows.map(row => (
            <tr key={`${row.model_version}-${row.side}-${row.timeframe}`} className="border-t border-bg-border">
              <td className="max-w-[240px] truncate p-3" title={row.model_version}>{row.model_version} · {row.side}</td>
              <td className="p-3"><span className="capitalize">{row.timeframe}</span> · {row.holding_sessions} {row.holding_sessions === 1 ? "session" : "sessions"}</td>
              <td className="p-3">{row.n} / {row.eligible_n}</td>
              <td className="p-3">{row.sessions}</td>
              <td className="p-3">{pct(row.mean_return)} / {pct(row.median_return)}</td>
              <td className="p-3">{pct(row.hit_rate)}</td>
              <td className="p-3">{pct(row.mean_session_return)}</td>
              <td className="p-3">{pct(row.mean_session_excess)}</td>
              <td className="p-3">{pct(row.benchmark_beat_rate)}</td>
              <td className="p-3">{row.benchmark_n} / {row.benchmark_sessions}</td>
              <td className="p-3">{row.pending_n} / {row.missing_n}</td>
              <td className="p-3">{row.excluded_n} / {row.awaiting_refresh_n}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {!rows.length && <p className="mt-4 text-sm text-text-muted">No stock evaluations for this scope yet. The archive writer must refresh existing observations before scores appear.</p>}
      <p className="mt-3 text-xs text-text-muted">Returns follow the predicted direction, before costs. Nifty excess measures selection beyond the market move. Source-day means weight each list date equally; benchmark means use only matched quotes. Missing prices stay missing. Retrospective or late decisions are excluded. Multiple contracts for one stock and side count once per list date.</p>
      <p className="mt-2 text-xs text-amber-200">Overlapping weeks and months are correlated. These are descriptive scores; comparing horizons does not retrain the model or change paper exits. A frozen chronological holdout is required before choosing a new strategy horizon.</p>
    </section>
  );
}
