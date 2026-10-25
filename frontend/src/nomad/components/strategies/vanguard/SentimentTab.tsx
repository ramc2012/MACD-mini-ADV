"use client";

/**
 * Market-wide sentiment: participant positioning, PCR, volatility, breadth.
 *
 * MARKET-WIDE, AND THE PANEL SAYS SO. NSE publishes participant-wise open
 * interest as an aggregate by instrument class — FII / DII / Pro / Client
 * across index futures, stock futures and the option legs — with no per-symbol
 * dimension whatsoever. A desk that renders "FII positioning" next to a symbol
 * would be inventing detail the exchange does not publish, so this lives on
 * its own tab rather than as a column.
 *
 * The headline score is deliberately hard to read without its components. It
 * is a blend of five families and it is NULL whenever fewer than three of them
 * were available — renormalising over one family makes that family the score,
 * which on the first run produced a +100 reading from a single input. When it
 * is suppressed the panel shows the families that DID report, because those
 * are real measurements even when the blend is not.
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
import { Gauge, Info, TrendingUp, Users } from "lucide-react";

import { MetricTile, Section, StatusBadge, formatNumber } from "@/components/desk-ui";
import { CHART } from "../shared/chartTheme";
import { Unmeasured, fmt, num } from "./vanguard-vocab";

const AXIS = { stroke: CHART.axis, fontSize: 10 };

const FAMILY_LABEL: Record<string, string> = {
  positioning: "FII positioning",
  options: "Put/call ratio",
  breadth: "Breadth",
  volatility: "Volatility",
  oi_conjunction: "OI buildup",
};

export function SentimentTab({ data }: { data?: any }) {
  const history: any[] = data?.history ?? [];
  const latest = data?.latest;
  const scored = data?.latest_scored;

  const series = useMemo(
    () =>
      history.map((row) => ({
        t: String(row.dt).slice(5, 10),
        score: num(row.sentiment_score),
        pcr: num(row.market_oi_pcr),
        fii: num(row.fii_fut_index_net),
        iv: num(row.index_atm_iv) == null ? null : (num(row.index_atm_iv) as number) * 100,
        adv: num(row.advances),
        dec: num(row.declines) == null ? null : -(num(row.declines) as number),
      })),
    [history],
  );

  if (data?.unavailable) {
    return (
      <Section title="Sentiment unavailable" icon={<Gauge size={16} />}>
        <p className="text-sm text-text-secondary">{data.unavailable}</p>
      </Section>
    );
  }
  if (!latest) return <Section title="Sentiment"><Unmeasured why="no sessions computed" /></Section>;

  const parts = latest.sentiment_components?.parts ?? {};
  const suppressed = latest.sentiment_score == null;
  const score = num(latest.sentiment_score) ?? num(scored?.sentiment_score);

  return (
    <div className="space-y-4">
      <Section
        title="Market sentiment"
        icon={<Gauge size={16} />}
        description={
          suppressed
            ? `Session ${String(latest.dt).slice(0, 10)} has only ${latest.sentiment_components?.n_families ?? 0} of 5 families, below the minimum of ${latest.sentiment_components?.min_families ?? 3} — no composite is formed. The families that did report are shown below, and the headline falls back to the last scored session.`
            : `Session ${String(latest.dt).slice(0, 10)}, blended from ${latest.sentiment_components?.n_families ?? 0} of 5 families.`
        }
        rightSlot={
          <StatusBadge
            label={data?.scope === "market-wide" ? "market-wide, never per symbol" : "market-wide"}
            variant="neutral"
            icon={<Info size={12} />}
          />
        }
      >
        <div className="flex flex-wrap items-center gap-4">
          <ScoreDial value={score} stale={suppressed} asOf={suppressed ? scored?.dt : latest.dt} />
          <div className="min-w-[260px] flex-1 space-y-1.5">
            {Object.keys(FAMILY_LABEL).map((key) => {
              const v = num(parts[key]);
              return (
                <div key={key} className="flex items-center gap-2">
                  <span className="w-28 shrink-0 text-[11px] text-text-secondary">
                    {FAMILY_LABEL[key]}
                  </span>
                  {v == null ? (
                    <span className="text-[10px] text-text-muted">not reporting this session</span>
                  ) : (
                    <>
                      <span className="relative inline-block h-2 flex-1 rounded-sm bg-bg-border/50">
                        <span className="absolute inset-y-0 left-1/2 w-px bg-text-muted/50" />
                        <span
                          className={
                            "absolute inset-y-[1px] rounded-[1px] " +
                            (v > 0 ? "bg-accent-green/70" : "bg-accent-red/70")
                          }
                          style={{
                            left: v > 0 ? "50%" : `${50 - (Math.min(100, Math.abs(v)) / 2)}%`,
                            width: `${Math.min(100, Math.abs(v)) / 2}%`,
                          }}
                        />
                      </span>
                      <span
                        className={
                          "w-12 shrink-0 text-right font-mono text-[11px] " +
                          (v > 0 ? "text-accent-green" : v < 0 ? "text-accent-red" : "text-text-muted")
                        }
                      >
                        {v > 0 ? "+" : ""}
                        {v.toFixed(0)}
                      </span>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-text-muted">
          This composite is a summary for reading, <strong>not a validated signal</strong>. No
          cross-sectional IC study stands behind it, the participant series began in August 2026,
          and several families are contemporaneous rather than predictive.
        </p>
      </Section>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-6">
        <MetricTile label="FII index futures" value={formatNumber(num(latest.fii_fut_index_net) ?? num(scored?.fii_fut_index_net), 0)}
                    detail="net contracts (long − short)"
                    color={(num(latest.fii_fut_index_net) ?? 0) < 0 ? "text-accent-red" : "text-accent-green"} />
        <MetricTile
          label="FII long ratio"
          value={fmt(latest.fii_index_long_ratio ?? scored?.fii_index_long_ratio, 3)}
          detail={(() => {
            const r = num(latest.fii_index_long_ratio ?? scored?.fii_index_long_ratio);
            return r == null ? "index futures" : `${((1 - r) * 100).toFixed(0)}% of the book is short`;
          })()}
          color={(num(latest.fii_index_long_ratio ?? scored?.fii_index_long_ratio) ?? 0.5) < 0.4
            ? "text-accent-red" : undefined}
        />
        <MetricTile label="Client index fut" value={formatNumber(num(latest.client_fut_index_net) ?? num(scored?.client_fut_index_net), 0)}
                    detail="retail, the usual other side" />
        <MetricTile label="market OI PCR" value={fmt(latest.market_oi_pcr, 3)} detail="put OI / call OI" />
        <MetricTile label="NIFTY ATM IV" value={num(latest.index_atm_iv) == null ? "—" : `${((num(latest.index_atm_iv) as number) * 100).toFixed(2)}%`}
                    detail="solved, not vendor-supplied" />
        <MetricTile label="advance / decline" value={fmt(latest.advance_decline_ratio ?? scored?.advance_decline_ratio, 2)}
                    detail={`${latest.advances ?? scored?.advances ?? "—"} up / ${latest.declines ?? scored?.declines ?? "—"} down`} />
      </div>

      <Section
        title="Sentiment through time"
        icon={<TrendingUp size={16} />}
        description="Gaps are sessions where fewer than three families reported — suppressed, not zero."
      >
        <ResponsiveContainer width="100%" height={190}>
          <ComposedChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={CHART.grid} vertical={false} />
            <XAxis dataKey="t" {...AXIS} minTickGap={30} />
            <YAxis yAxisId="s" domain={[-100, 100]} {...AXIS} width={38} />
            <YAxis yAxisId="p" orientation="right" {...AXIS} width={40} />
            <ReferenceLine yAxisId="s" y={0} stroke={CHART.axis} strokeDasharray="2 3" />
            <Tooltip contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }} />
            <Bar yAxisId="s" dataKey="score" name="sentiment">
              {series.map((row, i) => (
                <Cell key={i} fill={(row.score ?? 0) >= 0 ? CHART.green : CHART.red} fillOpacity={0.6} />
              ))}
            </Bar>
            <Line yAxisId="p" type="monotone" dataKey="pcr" stroke={CHART.amber} dot={false} strokeWidth={1.2} name="OI PCR" />
          </ComposedChart>
        </ResponsiveContainer>
      </Section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Participant positioning" icon={<Users size={16} />}
                 description="FII net index futures. Aggregate across the market — this file has no per-symbol dimension.">
          <ResponsiveContainer width="100%" height={170}>
            <ComposedChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="t" {...AXIS} minTickGap={30} />
              <YAxis {...AXIS} width={54} tickFormatter={(v: number) => `${(v / 1000).toFixed(0)}k`} />
              <ReferenceLine y={0} stroke={CHART.axis} strokeDasharray="2 3" />
              <Tooltip contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }}
                       formatter={(v: any) => formatNumber(Number(v), 0)} />
              <Line type="monotone" dataKey="fii" stroke={CHART.violet} dot={false} strokeWidth={1.5} name="FII net index fut" />
            </ComposedChart>
          </ResponsiveContainer>
        </Section>

        <Section title="Breadth" description="Advances above the line, declines below. Sentiment out of prices rather than positioning.">
          <ResponsiveContainer width="100%" height={170}>
            <ComposedChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: 0 }} stackOffset="sign">
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="t" {...AXIS} minTickGap={30} />
              <YAxis {...AXIS} width={40} />
              <ReferenceLine y={0} stroke={CHART.axis} />
              <Tooltip contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }} />
              <Bar dataKey="adv" stackId="b" fill={CHART.green} fillOpacity={0.55} name="advances" />
              <Bar dataKey="dec" stackId="b" fill={CHART.red} fillOpacity={0.55} name="declines" />
            </ComposedChart>
          </ResponsiveContainer>
        </Section>
      </div>

      <Section title="Positioning conjunction across the universe"
               description="How many names showed each OI/price state this session.">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {[
            ["long buildup", "long_buildup_count", "text-accent-green"],
            ["short covering", "short_covering_count", "text-accent-green/80"],
            ["short buildup", "short_buildup_count", "text-accent-red"],
            ["long unwinding", "long_unwind_count", "text-accent-red/80"],
          ].map(([label, key, colour]) => (
            <MetricTile
              key={key as string}
              label={label as string}
              value={String((latest[key as string] ?? scored?.[key as string]) ?? "—")}
              color={colour as string}
              detail="names"
            />
          ))}
        </div>
      </Section>
    </div>
  );
}

function ScoreDial({ value, stale, asOf }: { value?: number | null; stale?: boolean; asOf?: string }) {
  const v = num(value);
  const angle = v == null ? 0 : (Math.max(-100, Math.min(100, v)) / 100) * 90;
  const colour = v == null ? "rgb(var(--text-muted))"
    : v > 15 ? "rgb(var(--accent-green))"
      : v < -15 ? "rgb(var(--accent-red))"
        : "rgb(var(--accent-amber))";
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 120 68" width={168} height={95} aria-label={`sentiment ${v ?? "unavailable"}`}>
        <path d="M10,60 A50,50 0 0,1 110,60" fill="none" stroke="rgb(var(--bg-border))" strokeWidth={9} strokeLinecap="round" />
        <path d="M10,60 A50,50 0 0,1 45,14" fill="none" stroke="rgb(var(--accent-red))" strokeWidth={9} strokeOpacity={0.28} strokeLinecap="round" />
        <path d="M75,14 A50,50 0 0,1 110,60" fill="none" stroke="rgb(var(--accent-green))" strokeWidth={9} strokeOpacity={0.28} strokeLinecap="round" />
        <g transform={`rotate(${angle} 60 60)`}>
          <line x1="60" y1="60" x2="60" y2="20" stroke={colour} strokeWidth={3} strokeLinecap="round" />
        </g>
        <circle cx="60" cy="60" r="4" fill={colour} />
        <text x="60" y="52" textAnchor="middle" style={{ fill: colour }} className="text-[17px] font-semibold">
          {v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(0)}`}
        </text>
      </svg>
      <span className="text-[10px] text-text-muted">
        {stale && asOf ? `last scored ${String(asOf).slice(0, 10)}` : "bearish · neutral · bullish"}
      </span>
    </div>
  );
}
