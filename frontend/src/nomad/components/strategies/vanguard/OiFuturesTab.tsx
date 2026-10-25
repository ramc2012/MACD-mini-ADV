"use client";

/**
 * Futures OI tab — true per-contract stock/index futures open interest with
 * baselines (vanguard futures_oi_baselines). Complements the aggregate
 * participant-OI Sentiment tab and Market's MWPL leg: this is the first
 * per-symbol FUTURES OI in the schema.
 *
 * Cross-section table of buildup states + z-scores with a per-symbol
 * drill-down chart (price line + OI bars + ΔOI z-score).
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ArrowLeft, Flame, Layers } from "lucide-react";

import { MetricTile, Section, Sparkline, formatNumber } from "@/components/desk-ui";
import { getVanguardOiFuturesSymbol } from "@/lib/api";
import { CHART } from "@/components/strategies/shared/chartTheme";
import { OiStateBadge } from "./vanguard-vocab";

type Row = {
  symbol: string;
  ts: string;
  expiry: string;
  close?: number | null;
  d_price_pct?: number | null;
  oi?: number | null;
  d_oi?: number | null;
  d_oi_pct?: number | null;
  d_oi_pct_z?: number | null;
  oi_z?: number | null;
  volume_z?: number | null;
  oi_pctile?: number | null;
  oi_state?: string | null;
  activity_surge?: boolean;
  is_rollover?: boolean;
  lookback_sessions?: number | null;
  sector?: string | null;
};

const STATE_FILTERS = [
  { key: "long_buildup", label: "Long buildup" },
  { key: "short_buildup", label: "Short buildup" },
  { key: "short_covering", label: "Short covering" },
  { key: "long_unwind", label: "Long unwind" },
] as const;

const num = (v: unknown): number | null => {
  if (v == null || v === "" || typeof v === "boolean") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const fmtZ = (v?: number | null) =>
  v == null || !Number.isFinite(v) ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2);

const fmtCompact = (v?: number | null) => {
  const n = num(v);
  if (n == null) return "—";
  if (Math.abs(n) >= 1e7) return (n / 1e7).toFixed(2) + " Cr";
  if (Math.abs(n) >= 1e5) return (n / 1e5).toFixed(1) + " L";
  return formatNumber(n, 0);
};

const zTone = (v?: number | null) =>
  v == null ? "text-text-muted" : v >= 1.5 ? "text-accent-green" : v <= -1.5 ? "text-accent-red" : "text-text-primary";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export default function OiFuturesTab({ data }: { data?: any }) {
  const [stateFilter, setStateFilter] = useState<string | null>(null);
  const [surgeOnly, setSurgeOnly] = useState(false);
  const [sortKey, setSortKey] = useState<string>("d_oi_pct_z");
  const [sortDir, setSortDir] = useState<1 | -1>(-1);
  const [symbol, setSymbol] = useState<string | null>(null);

  const rows: Row[] = useMemo(() => data?.rows ?? [], [data]);
  const summary = data?.summary;

  const filtered = useMemo(() => {
    let out = rows;
    if (stateFilter) out = out.filter((r) => r.oi_state === stateFilter);
    if (surgeOnly) out = out.filter((r) => r.activity_surge);
    const dir = sortDir;
    return [...out].sort((a, b) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const av = num((a as any)[sortKey]);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const bv = num((b as any)[sortKey]);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * -dir;
    });
  }, [rows, stateFilter, surgeOnly, sortKey, sortDir]);

  const detail = useQuery({
    queryKey: ["vanguard", "oi-futures", symbol],
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    queryFn: (): Promise<any> => getVanguardOiFuturesSymbol(symbol as string).then((r) => r.data),
    enabled: Boolean(symbol),
  });

  if (data && data.available === false) {
    return (
      <Section title="Futures OI" icon={<Layers size={16} />}>
        <p className="text-sm text-text-muted">{data.note}</p>
      </Section>
    );
  }

  const header = (key: string, label: string, align = "text-right") => (
    <th
      className={`cursor-pointer select-none whitespace-nowrap px-2 py-1.5 ${align} text-[10px] uppercase tracking-[0.08em] text-text-muted hover:text-text-primary`}
      onClick={() => {
        if (sortKey === key) setSortDir((d) => (d === 1 ? -1 : 1));
        else {
          setSortKey(key);
          setSortDir(-1);
        }
      }}
    >
      {label}
      {sortKey === key ? (sortDir === -1 ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <div className="space-y-4">
      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricTile label="Names" value={String(summary?.names ?? "—")} detail={`session ${data?.latest_session ?? "—"}`} />
        <MetricTile label="Long buildup" value={String(summary?.states?.long_buildup ?? 0)} color="text-accent-green" />
        <MetricTile label="Short buildup" value={String(summary?.states?.short_buildup ?? 0)} color="text-accent-red" />
        <MetricTile label="Short covering" value={String(summary?.states?.short_covering ?? 0)} />
        <MetricTile label="Long unwind" value={String(summary?.states?.long_unwind ?? 0)} />
        <MetricTile label="Activity surges" value={summary?.baseline_ready ? String(summary?.surges ?? 0) : "—"} detail={`${summary?.baseline_ready ?? 0}/${summary?.names ?? 0} baselines ready · ΔOI z & vol z ≥ 1.5`} color={(summary?.surges ?? 0) > 0 ? "text-accent-amber" : undefined} />
      </section>

      {symbol ? (
        <Section
          title={`${symbol} — futures OI history`}
          icon={<Layers size={16} />}
          description="Front-contract stitched daily series. Deltas are never taken across a roll."
          rightSlot={
            <button
              type="button"
              onClick={() => setSymbol(null)}
              className="inline-flex items-center gap-1 rounded-lg border border-bg-border/70 px-2 py-1 text-[11px] text-text-muted hover:text-text-primary"
            >
              <ArrowLeft size={12} /> back to grid
            </button>
          }
        >
          {detail.isLoading ? (
            <p className="py-10 text-center text-sm text-text-muted">Loading history…</p>
          ) : (
            <SymbolOiChart rows={detail.data?.rows ?? []} />
          )}
        </Section>
      ) : (
        <Section
          title="Futures OI cross-section"
          icon={<Layers size={16} />}
          description={`Latest scored session per symbol (as of ${data?.latest_session ?? "—"}). Intraday rows are today's running OI scored against settled baselines; baselines use up to 60 prior sessions. ${summary?.warming_up ?? 0} names are warming up; unavailable measurements are shown as —.`}
        >
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {STATE_FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={() => setStateFilter(stateFilter === f.key ? null : f.key)}
                className={`rounded-full border px-2.5 py-0.5 text-[10.5px] ${
                  stateFilter === f.key
                    ? "border-accent-blue/60 bg-accent-blue/10 text-accent-blue"
                    : "border-bg-border/70 text-text-muted hover:text-text-primary"
                }`}
              >
                {f.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setSurgeOnly((v) => !v)}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[10.5px] ${
                surgeOnly
                  ? "border-accent-amber/60 bg-accent-amber/10 text-accent-amber"
                  : "border-bg-border/70 text-text-muted hover:text-text-primary"
              }`}
            >
              <Flame size={11} /> surge only
            </button>
            <span className="ml-auto text-[10.5px] text-text-muted">{filtered.length} names</span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] border-separate border-spacing-0 text-[11.5px]">
              <thead>
                <tr>
                  {header("symbol", "Symbol", "text-left")}
                  <th className="px-2 py-1.5 text-left text-[10px] uppercase tracking-[0.08em] text-text-muted">Sector</th>
                  {header("close", "Close")}
                  {header("d_price_pct", "Δpx%")}
                  {header("oi", "OI")}
                  {header("d_oi_pct", "ΔOI%")}
                  {header("d_oi_pct_z", "ΔOI z")}
                  {header("volume_z", "Vol z")}
                  {header("oi_pctile", "OI pctile")}
                  <th className="px-2 py-1.5 text-left text-[10px] uppercase tracking-[0.08em] text-text-muted">State</th>
                  <th className="px-2 py-1.5 text-center text-[10px] uppercase tracking-[0.08em] text-text-muted">Surge</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr
                    key={r.symbol}
                    className="cursor-pointer border-b border-bg-border/40 hover:bg-bg-border/20"
                    onClick={() => setSymbol(r.symbol)}
                  >
                    <td className="px-2 py-1.5 font-mono font-semibold text-text-primary">
                      {r.symbol}
                      {r.is_rollover ? <span className="ml-1 text-[9px] text-text-muted" title="Front contract rolled; deltas suppressed this session">roll</span> : null}
                    </td>
                    <td className="max-w-[110px] truncate px-2 py-1.5 text-text-muted">{r.sector ?? "—"}</td>
                    <td className="px-2 py-1.5 text-right font-mono">{formatNumber(num(r.close), 1)}</td>
                    <td className={`px-2 py-1.5 text-right font-mono ${num(r.d_price_pct) == null ? "text-text-muted" : (num(r.d_price_pct) as number) >= 0 ? "text-accent-green" : "text-accent-red"}`}>
                      {num(r.d_price_pct) == null ? "—" : `${(num(r.d_price_pct) as number) >= 0 ? "+" : ""}${(num(r.d_price_pct) as number).toFixed(2)}%`}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono">{fmtCompact(r.oi)}</td>
                    <td className="px-2 py-1.5 text-right font-mono">{num(r.d_oi_pct) == null ? "—" : `${(num(r.d_oi_pct) as number) >= 0 ? "+" : ""}${(num(r.d_oi_pct) as number).toFixed(2)}%`}</td>
                    <td className={`px-2 py-1.5 text-right font-mono ${zTone(num(r.d_oi_pct_z))}`}>{fmtZ(num(r.d_oi_pct_z))}</td>
                    <td className={`px-2 py-1.5 text-right font-mono ${zTone(num(r.volume_z))}`}>{fmtZ(num(r.volume_z))}</td>
                    <td className="px-2 py-1.5 text-right font-mono">{num(r.oi_pctile) == null ? "—" : `${Math.round((num(r.oi_pctile) as number) * 100)}%`}</td>
                    <td className="px-2 py-1.5">
                      <OiStateBadge state={r.oi_state} dOiPct={num(r.d_oi_pct)} dPricePct={num(r.d_price_pct)} />
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      {r.activity_surge ? <Flame size={12} className="inline text-accent-amber" /> : <span className="text-text-muted">·</span>}
                    </td>
                  </tr>
                ))}
                {!filtered.length ? (
                  <tr>
                    <td colSpan={11} className="px-2 py-8 text-center text-text-muted">
                      {rows.length ? "No names match the active filters." : "No baseline rows yet — the futures OI ingest has not populated this session."}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

function SymbolOiChart({ rows }: { rows: Row[] }) {
  const chartData = useMemo(
    () =>
      rows.map((r) => ({
        ts: r.ts?.slice(5),
        close: num(r.close),
        oi: num(r.oi),
        z: num(r.d_oi_pct_z),
        surge: r.activity_surge,
        state: r.oi_state,
      })),
    [rows],
  );
  const oiSpark = useMemo(() => rows.map((r) => num(r.oi) ?? 0), [rows]);
  const latest = rows.length ? rows[rows.length - 1] : null;

  if (!rows.length) {
    return <p className="py-10 text-center text-sm text-text-muted">No history for this symbol yet.</p>;
  }
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-5">
        <MetricTile size="sm" label="Latest OI" value={fmtCompact(latest?.oi)} detail={latest?.ts} />
        <MetricTile size="sm" label="ΔOI z" value={fmtZ(num(latest?.d_oi_pct_z))} color={zTone(num(latest?.d_oi_pct_z))} />
        <MetricTile size="sm" label="Vol z" value={fmtZ(num(latest?.volume_z))} color={zTone(num(latest?.volume_z))} />
        <MetricTile size="sm" label="OI pctile" value={num(latest?.oi_pctile) == null ? "—" : `${Math.round((num(latest?.oi_pctile) as number) * 100)}%`} />
        <div className="flex flex-col justify-center rounded-xl border border-bg-border/60 px-3 py-1.5">
          <span className="text-[9.5px] uppercase tracking-[0.1em] text-text-muted">OI trend</span>
          <Sparkline values={oiSpark} width={120} height={26} color={CHART.blue} />
        </div>
      </div>

      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={chartData} margin={{ top: 6, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          <XAxis dataKey="ts" tick={{ fill: CHART.axis, fontSize: 9.5 }} minTickGap={28} axisLine={false} tickLine={false} />
          <YAxis yAxisId="oi" orientation="left" tick={{ fill: CHART.axis, fontSize: 9.5 }} tickFormatter={(v: number) => fmtCompact(v)} axisLine={false} tickLine={false} width={52} />
          <YAxis yAxisId="px" orientation="right" tick={{ fill: CHART.axis, fontSize: 9.5 }} domain={["auto", "auto"]} axisLine={false} tickLine={false} width={48} />
          <Tooltip
            contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, borderRadius: 8, fontSize: 11 }}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter={(value: any, name: any) =>
              name === "OI" ? [fmtCompact(Number(value)), name] : [formatNumber(Number(value), 2), name]
            }
          />
          <Bar yAxisId="oi" dataKey="oi" name="OI" fill={CHART.blueSoft} isAnimationActive={false} />
          <Line yAxisId="px" dataKey="close" name="Close" stroke={CHART.amber} dot={false} strokeWidth={1.6} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>

      <ResponsiveContainer width="100%" height={120}>
        <ComposedChart data={chartData} margin={{ top: 2, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          <XAxis dataKey="ts" tick={{ fill: CHART.axis, fontSize: 9 }} minTickGap={28} axisLine={false} tickLine={false} />
          <YAxis tick={{ fill: CHART.axis, fontSize: 9.5 }} axisLine={false} tickLine={false} width={52} />
          <ReferenceLine y={1.5} stroke={CHART.amber} strokeDasharray="3 3" label={{ value: "surge", fill: CHART.amber, fontSize: 9, position: "insideTopRight" }} />
          <ReferenceLine y={0} stroke={CHART.axis} />
          <ReferenceLine y={-1.5} stroke={CHART.axis} strokeDasharray="3 3" />
          <Tooltip contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, borderRadius: 8, fontSize: 11 }} />
          <Bar
            dataKey="z"
            name="ΔOI z"
            isAnimationActive={false}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            fill={CHART.violet}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="text-[10.5px] text-text-muted">
        Top: front-contract OI (bars, left) vs close (line, right). Bottom: ΔOI% z-score vs the trailing baseline — bars beyond ±1.5 mark unusual OI activity; paired with a volume z ≥ 1.5 they set the surge flag.
      </p>
    </div>
  );
}
