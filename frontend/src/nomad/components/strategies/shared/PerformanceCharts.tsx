"use client";

/**
 * Performance visualisation block — equity curve, monthly P&L, and
 * R-multiple distribution, all derived from the closed-position list so
 * every desk gets the same charts with no bespoke endpoint.
 */
import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Activity, BarChart3, TrendingUp } from "lucide-react";

import { Section, formatSignedMoney } from "@/components/desk-ui";
import {
  type PaperPosition,
  deriveEquitySeries,
  deriveMonthly,
  deriveRHistogram,
} from "@/lib/strategy-stats";
import { CHART, pnlColor } from "./chartTheme";

function compact(v?: number | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(1)}Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(1)}L`;
  if (a >= 1e3) return `${sign}₹${(a / 1e3).toFixed(0)}k`;
  return `${sign}₹${a.toFixed(0)}`;
}

const AXIS = { stroke: CHART.axis, fontSize: 10, tickLine: false } as const;

function TipBox({ rows }: { rows: Array<{ k: string; v: string; c?: string }> }) {
  return (
    <div
      className="rounded-lg border px-3 py-2 text-[11px] shadow-lg"
      style={{ background: CHART.surface, borderColor: CHART.border }}
    >
      {rows.map((r) => (
        <div key={r.k} className="flex justify-between gap-4">
          <span className="text-text-muted">{r.k}</span>
          <span className="font-mono" style={{ color: r.c ?? "#e6edf3" }}>
            {r.v}
          </span>
        </div>
      ))}
    </div>
  );
}

export function PerformanceCharts({
  closed = [],
  initialCapital = 0,
}: {
  closed?: PaperPosition[];
  initialCapital?: number;
}) {
  const equity = useMemo(() => deriveEquitySeries(closed, initialCapital), [closed, initialCapital]);
  const monthly = useMemo(() => deriveMonthly(closed), [closed]);
  const rHist = useMemo(() => deriveRHistogram(closed), [closed]);

  const gradOffset = useMemo(() => {
    if (!equity.length) return 1;
    const max = Math.max(...equity.map((d) => d.cumPnl), 0);
    const min = Math.min(...equity.map((d) => d.cumPnl), 0);
    if (max <= 0) return 0;
    if (min >= 0) return 1;
    return max / (max - min);
  }, [equity]);

  if (!closed.length) {
    return (
      <Section title="Performance" icon={<TrendingUp size={16} />}>
        <div className="rounded-xl border border-dashed border-bg-border/60 px-4 py-10 text-center text-sm text-text-muted">
          No closed trades yet — equity curve, monthly P&L and R-distribution appear once the desk
          books its first round-trips.
        </div>
      </Section>
    );
  }

  return (
    <div className="space-y-4">
      <Section title="Equity curve" icon={<TrendingUp size={16} />} description="Cumulative realized P&L over closed trades">
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={equity} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
              <defs>
                <linearGradient id="eqSplit" x1="0" y1="0" x2="0" y2="1">
                  <stop offset={gradOffset} stopColor={CHART.green} stopOpacity={0.45} />
                  <stop offset={gradOffset} stopColor={CHART.red} stopOpacity={0.32} />
                </linearGradient>
                <linearGradient id="eqLine" x1="0" y1="0" x2="0" y2="1">
                  <stop offset={gradOffset} stopColor={CHART.green} stopOpacity={1} />
                  <stop offset={gradOffset} stopColor={CHART.red} stopOpacity={1} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="i" {...AXIS} tickFormatter={(v) => `#${v + 1}`} minTickGap={28} />
              <YAxis {...AXIS} width={52} tickFormatter={(v) => compact(v)} />
              <ReferenceLine y={0} stroke={CHART.axis} strokeDasharray="3 3" />
              <Tooltip
                cursor={{ stroke: CHART.axis, strokeWidth: 1 }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const d = payload[0].payload as (typeof equity)[number];
                  return (
                    <TipBox
                      rows={[
                        { k: "Trade", v: `#${d.i + 1} · ${d.symbol}` },
                        { k: "Trade P&L", v: formatSignedMoney(d.pnl), c: pnlColor(d.pnl) },
                        { k: "Cum P&L", v: formatSignedMoney(d.cumPnl), c: pnlColor(d.cumPnl) },
                        { k: "Equity", v: compact(d.equity) },
                        { k: "Drawdown", v: compact(d.drawdown), c: CHART.red },
                      ]}
                    />
                  );
                }}
              />
              <Area
                type="monotone"
                dataKey="cumPnl"
                stroke="url(#eqLine)"
                strokeWidth={2}
                fill="url(#eqSplit)"
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Monthly P&L" icon={<BarChart3 size={16} />}>
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={monthly} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
                <CartesianGrid stroke={CHART.grid} vertical={false} />
                <XAxis dataKey="label" {...AXIS} />
                <YAxis {...AXIS} width={52} tickFormatter={(v) => compact(v)} />
                <ReferenceLine y={0} stroke={CHART.axis} />
                <Tooltip
                  cursor={{ fill: "rgba(255,255,255,0.04)" }}
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const d = payload[0].payload as (typeof monthly)[number];
                    return (
                      <TipBox
                        rows={[
                          { k: d.label, v: formatSignedMoney(d.pnl), c: pnlColor(d.pnl) },
                          { k: "Trades", v: `${d.trades} · ${(d.winRate * 100).toFixed(0)}% win` },
                        ]}
                      />
                    );
                  }}
                />
                <Bar dataKey="pnl" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                  {monthly.map((m) => (
                    <Cell key={m.month} fill={pnlColor(m.pnl)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Section>

        <Section title="R-multiple distribution" icon={<Activity size={16} />}>
          {rHist.length === 0 ? (
            <div className="flex h-56 items-center justify-center text-sm text-text-muted">
              No R-multiples recorded for this desk.
            </div>
          ) : (
            <div className="h-56 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={rHist} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
                  <CartesianGrid stroke={CHART.grid} vertical={false} />
                  <XAxis dataKey="label" {...AXIS} />
                  <YAxis {...AXIS} width={28} allowDecimals={false} />
                  <ReferenceLine x="+0.0R" stroke={CHART.axis} />
                  <Tooltip
                    cursor={{ fill: "rgba(255,255,255,0.04)" }}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const d = payload[0].payload as (typeof rHist)[number];
                      return <TipBox rows={[{ k: d.label, v: `${d.count} trades`, c: pnlColor(d.r) }]} />;
                    }}
                  />
                  <Bar dataKey="count" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                    {rHist.map((d) => (
                      <Cell key={d.r} fill={pnlColor(d.r)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}
