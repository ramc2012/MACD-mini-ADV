"use client";

/**
 * OrderFlowPulse — signed "aggressive" volume per 3-minute bucket.
 *
 * HONESTY (2026-07-19): `aggressor_side` on a FlowTrade is a BACKEND-INFERRED
 * label, not a broker field. No wired Indian retail broker pushes public
 * aggressor-tagged trade prints (`backend/analytics/orderflow.py`), and
 * `market_ticks` carries no per-trade size or side at all. So initiative,
 * absorption and net Δ here are all inferred from quotes — every label says so.
 */

import { useMemo } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { LastUpdated } from "@/components/common/LastUpdated";
import { ProvenanceChip, StatusBadge, formatIST, formatISTTime, formatNumber } from "@/components/desk-ui";
import { provenanceOf, type DataMode } from "@/lib/market-semantics";

import type { FootprintBar } from "./FootprintGrid";
import { OfSourceBadge } from "./OfSourceBadge";

export type FlowTrade = {
  timestamp: string;
  quantity?: number | null;
  /** INFERRED side — the broker never sends one. See the module docstring. */
  aggressor_side?: string | null;
};

type PulseRow = {
  time: string;
  buy: number;
  sell: number;
  sellSigned: number;
  delta: number;
  deltaPct: number;
  cumulativeDelta: number;
  absorption: boolean;
  initiative: boolean;
};

const bucketTime = (value: string): string => {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return value;
  timestamp.setUTCSeconds(0, 0);
  timestamp.setUTCMinutes(timestamp.getUTCMinutes() - (timestamp.getUTCMinutes() % 3));
  return timestamp.toISOString();
};

function rowsFromTrades(trades: FlowTrade[]): Array<Omit<PulseRow, "absorption" | "initiative">> {
  const buckets = new Map<string, { buy: number; sell: number }>();
  for (const trade of trades) {
    const time = bucketTime(trade.timestamp);
    const quantity = Math.max(0, Number(trade.quantity ?? 0));
    if (!time || !quantity) continue;
    const row = buckets.get(time) ?? { buy: 0, sell: 0 };
    if (String(trade.aggressor_side ?? "").toLowerCase().includes("sell")) row.sell += quantity;
    else row.buy += quantity;
    buckets.set(time, row);
  }
  let cumulativeDelta = 0;
  return Array.from(buckets.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([time, volume]) => {
      const delta = volume.buy - volume.sell;
      const total = volume.buy + volume.sell;
      cumulativeDelta += delta;
      return {
        time,
        buy: volume.buy,
        sell: volume.sell,
        sellSigned: -volume.sell,
        delta,
        deltaPct: total > 0 ? delta / total : 0,
        cumulativeDelta,
      };
    });
}

function rowsFromFootprints(bars: FootprintBar[]): Array<Omit<PulseRow, "absorption" | "initiative">> {
  return bars.map((bar) => {
    const buy = (bar.levels ?? []).reduce((sum, level) => sum + Math.max(0, Number(level.buy) || 0), 0);
    const sell = (bar.levels ?? []).reduce((sum, level) => sum + Math.max(0, Number(level.sell) || 0), 0);
    const total = buy + sell;
    const delta = Number.isFinite(Number(bar.delta)) ? Number(bar.delta) : buy - sell;
    return {
      time: bar.time,
      buy,
      sell,
      sellSigned: -sell,
      delta,
      deltaPct: total > 0 ? delta / total : 0,
      cumulativeDelta: Number(bar.cumulative_delta ?? delta),
    };
  });
}

export function OrderFlowPulse({
  bars,
  trades,
  source,
  asOf,
  timeframe = "3m",
  dataMode,
  height = 260,
}: {
  bars?: FootprintBar[] | null;
  trades?: FlowTrade[] | null;
  source?: string | null;
  asOf?: string | null;
  /** Aggregation window, surfaced in the provenance caption. */
  timeframe?: string | null;
  /** Declared by the owning desk; without it the caption can only say "unknown". */
  dataMode?: DataMode;
  height?: number;
}) {
  const rows = useMemo<PulseRow[]>(() => {
    const raw = bars?.length ? rowsFromFootprints(bars) : rowsFromTrades(trades ?? []);
    const totals = raw.map((row) => row.buy + row.sell).sort((a, b) => a - b);
    const medianVolume = totals.length ? totals[Math.floor(totals.length / 2)] : 0;
    return raw.map((row) => {
      const total = row.buy + row.sell;
      const pressure = total > 0 ? Math.abs(row.delta) / total : 0;
      return {
        ...row,
        absorption: total >= medianVolume && pressure <= 0.15,
        initiative: pressure >= 0.60,
      };
    });
  }, [bars, trades]);

  const absorptionCount = rows.filter((row) => row.absorption).length;
  const initiativeCount = rows.filter((row) => row.initiative).length;
  const netDelta = rows.reduce((sum, row) => sum + row.delta, 0);
  const lastTime = asOf ?? rows.at(-1)?.time ?? null;

  return (
    <div className="w-full">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <OfSourceBadge source={source} />
          <span className="text-[10px] uppercase tracking-[0.12em] text-text-muted" title="Buy/sell sides, initiative and absorption are inferred from the quote stream — there is no aggressor-tagged trade tape on any wired broker.">
            sides inferred from quotes
          </span>
          <StatusBadge label={`net Δ ${netDelta >= 0 ? "+" : ""}${formatNumber(netDelta, 0)}`} variant={netDelta > 0 ? "success" : netDelta < 0 ? "error" : "neutral"} />
          <StatusBadge label={`${initiativeCount} initiative`} variant="info" />
          <StatusBadge label={`${absorptionCount} absorption`} variant={absorptionCount ? "warn" : "neutral"} />
        </div>
        <LastUpdated timestamp={lastTime} label="last pulse" />
      </div>
      <ProvenanceChip
        density="caption"
        provenance={provenanceOf({
          source,
          feature: "flow_attribution",
          asOf: lastTime,
          timeframe,
          dataMode,
          have: rows.length,
          completenessLabel: `${rows.length} bars${bars?.length ? "" : " (bucketed from flow rows)"}`,
        })}
      />

      {rows.length ? (
        <>
          <div style={{ height }}>
            <ResponsiveContainer>
              <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="time" tickFormatter={(value) => formatISTTime(value)} minTickGap={28} tick={{ fontSize: 10, fill: "rgba(255,255,255,0.4)" }} stroke="rgba(255,255,255,0.12)" />
                <YAxis yAxisId="volume" width={58} tick={{ fontSize: 10, fill: "rgba(255,255,255,0.4)" }} stroke="rgba(255,255,255,0.12)" tickFormatter={(value: number) => Math.abs(value) >= 1000 ? `${(value / 1000).toFixed(0)}k` : String(value)} />
                <YAxis yAxisId="pressure" orientation="right" domain={[-1, 1]} width={42} tick={{ fontSize: 10, fill: "#a78bfa" }} tickFormatter={(value: number) => `${Math.round(value * 100)}%`} stroke="rgba(255,255,255,0.12)" />
                <Tooltip
                  contentStyle={{ background: "#0d1117", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 8, fontSize: 11 }}
                  labelFormatter={(value) => `${formatIST(value)} IST`}
                  formatter={(value: number, name: string) => [
                    name === "Pressure" ? `${formatNumber(Number(value) * 100, 1)}%` : formatNumber(Math.abs(Number(value)), 0),
                    name === "Sell" ? "Sell side (inferred)" : name === "Buy" ? "Buy side (inferred)" : name,
                  ]}
                />
                <ReferenceLine yAxisId="volume" y={0} stroke="rgba(255,255,255,0.25)" />
                <ReferenceLine yAxisId="pressure" y={0.6} stroke="#00d4a3" strokeDasharray="3 4" strokeOpacity={0.35} />
                <ReferenceLine yAxisId="pressure" y={-0.6} stroke="#ff4757" strokeDasharray="3 4" strokeOpacity={0.35} />
                <Bar yAxisId="volume" dataKey="buy" name="Buy" maxBarSize={16} isAnimationActive={false}>
                  {rows.map((row, index) => <Cell key={`buy-${row.time}-${index}`} fill={row.initiative && row.delta > 0 ? "#00d4a3" : "#10b981"} fillOpacity={row.absorption ? 0.35 : 0.75} />)}
                </Bar>
                <Bar yAxisId="volume" dataKey="sellSigned" name="Sell" maxBarSize={16} isAnimationActive={false}>
                  {rows.map((row, index) => <Cell key={`sell-${row.time}-${index}`} fill={row.initiative && row.delta < 0 ? "#ff4757" : "#ef4444"} fillOpacity={row.absorption ? 0.35 : 0.75} />)}
                </Bar>
                <Line yAxisId="pressure" dataKey="deltaPct" name="Pressure" stroke="#a78bfa" strokeWidth={1.7} dot={(props) => {
                  const row = rows[Number(props.index)];
                  if (!row?.absorption) return <circle cx={0} cy={0} r={0} />;
                  return <circle cx={Number(props.cx)} cy={Number(props.cy)} r={4} fill="#f59e0b" stroke="#0d1117" strokeWidth={1.5} />;
                }} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-text-muted">
            <span><span className="text-accent-green">Above zero</span> buy-side volume (inferred)</span>
            <span><span className="text-accent-red">Below zero</span> sell-side volume (inferred)</span>
            <span><span className="text-violet-300">Line</span> signed delta / volume</span>
            <span><span className="text-accent-amber">Amber dot</span> high-volume absorption</span>
          </div>
        </>
      ) : (
        <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-bg-border/60 text-xs text-text-muted">
          No flow rows bucketed in this snapshot.
        </div>
      )}
    </div>
  );
}
