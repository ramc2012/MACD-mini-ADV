"use client";

/**
 * Order-flow microstructure panel — shared by Auction + Fractal desks.
 * The lanes emit an identical order_flow block (CVD, imbalances, queue
 * pressure, toxicity, VWAP drift, aggressive buy/sell, fill odds).
 *
 * The panel header always states the source honesty (TICK/BOOK QUOTES vs BAR
 * PROXY) and the data timestamp. 2026-07-19: every buy/sell-attributed number
 * below (delta, cumulative delta, aggressive buy/sell, trade imbalance) is
 * INFERRED FROM QUOTES — no wired broker sends aggressor-tagged trade prints
 * (`backend/analytics/orderflow.py`), so none of these is a measured side.
 */
import { Waves } from "lucide-react";

import { LastUpdated } from "@/components/common/LastUpdated";
import { MetricTile, Section, formatNumber, formatSignedNumber, formatPct, tone } from "@/components/desk-ui";
import { OfSourceBadge } from "@/components/mpof";
import { CHART } from "./chartTheme";

export type OrderFlow = {
  spread?: number;
  mid_price?: number;
  micro_price?: number;
  top_imbalance?: number;
  depth_imbalance?: number;
  trade_imbalance?: number;
  order_flow_imbalance?: number;
  book_pressure?: number;
  aggressive_buy_volume?: number;
  aggressive_sell_volume?: number;
  delta?: number;
  cumulative_delta?: number;
  vwap?: number;
  vwap_drift?: number;
  queue_pressure?: number;
  toxicity_score?: number;
  adverse_selection_risk?: number;
  timing_confidence?: number;
  execution_aggression?: number;
  passive_fill_probability?: number;
  aggressive_fill_probability?: number;
  trade_intensity_per_minute?: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [k: string]: any;
};

function Diverging({ label, value, range = 1 }: { label: string; value?: number | null; range?: number }) {
  const v = Math.max(-range, Math.min(range, Number(value ?? 0)));
  const pct = (Math.abs(v) / range) * 50;
  return (
    <div>
      <div className="flex items-center justify-between text-[10.5px]">
        <span className="text-text-muted">{label}</span>
        <span className={`font-mono ${tone(v)}`}>{formatSignedNumber(value, 2)}</span>
      </div>
      <div className="relative mt-1 h-1.5 overflow-hidden rounded-full bg-bg-primary/40">
        <div className="absolute left-1/2 top-0 h-full w-px bg-white/25" />
        <div className="absolute top-0 h-full rounded-full" style={{ background: v >= 0 ? CHART.green : CHART.red, left: v >= 0 ? "50%" : `${50 - pct}%`, width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Gauge({ label, value, invert = false }: { label: string; value?: number | null; invert?: boolean }) {
  const v = Math.max(0, Math.min(1, Number(value ?? 0)));
  const good = invert ? 1 - v : v;
  const color = good > 0.66 ? CHART.green : good > 0.33 ? CHART.amber : CHART.red;
  return (
    <div className="rounded-lg border border-bg-border bg-bg-primary/15 px-2.5 py-2">
      <div className="flex items-center justify-between text-[10px] text-text-muted">
        <span className="uppercase tracking-[0.1em]">{label}</span>
        <span className="font-mono text-text-secondary">{formatPct(v, 0)}</span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-bg-primary/40">
        <div className="h-full rounded-full" style={{ width: `${v * 100}%`, background: color }} />
      </div>
    </div>
  );
}

export function OrderFlowPanel({
  of,
  source,
  asOf,
}: {
  of?: OrderFlow | null;
  /** Order-flow source honesty (e.g. tick_reconstruction_book / bar_inference). */
  source?: string | null;
  /** Timestamp of the snapshot feeding this block. */
  asOf?: string | null;
}) {
  const f = of || {};
  const aggBuy = Number(f.aggressive_buy_volume ?? 0);
  const aggSell = Number(f.aggressive_sell_volume ?? 0);
  const aggTot = aggBuy + aggSell || 1;

  return (
    <Section
      title="Order flow"
      icon={<Waves size={16} />}
      description="Quote-derived microstructure: delta, imbalances, queue pressure, toxicity — buy/sell sides inferred, no aggressor tape"
      rightSlot={
        <div className="flex flex-wrap items-center gap-2">
          <OfSourceBadge source={source ?? f.source ?? f.order_flow_source} />
          {asOf !== undefined ? <LastUpdated timestamp={asOf} label="data" /> : null}
        </div>
      }
    >
      <div className="grid gap-4 lg:grid-cols-3">
        {/* CVD + headline tiles */}
        <div className="grid grid-cols-2 gap-2.5">
          <MetricTile size="sm" label="Cum. delta" value={formatSignedNumber(f.cumulative_delta, 0)} color={tone(f.cumulative_delta)} />
          <MetricTile size="sm" label="Delta" value={formatSignedNumber(f.delta, 0)} color={tone(f.delta)} />
          <MetricTile size="sm" label="VWAP drift" value={formatSignedNumber(f.vwap_drift, 2)} detail={`VWAP ${formatNumber(f.vwap, 1)}`} color={tone(f.vwap_drift)} />
          <MetricTile size="sm" label="Spread" value={formatNumber(f.spread, 2)} detail={`mid ${formatNumber(f.mid_price, 1)}`} />
          <MetricTile size="sm" label="Toxicity" value={formatPct(f.toxicity_score, 0)} color={Number(f.toxicity_score ?? 0) > 0.5 ? "text-accent-red" : "text-text-primary"} />
          <MetricTile size="sm" label="Intensity" value={formatNumber(f.trade_intensity_per_minute, 0)} detail="trades/min" />
        </div>

        {/* imbalances */}
        <div className="space-y-2.5">
          <Diverging label="Top imbalance" value={f.top_imbalance} />
          <Diverging label="Depth imbalance" value={f.depth_imbalance} />
          <Diverging label="Trade imbalance" value={f.trade_imbalance} />
          <Diverging label="Order-flow imbalance" value={f.order_flow_imbalance} />
          <Diverging label="Book pressure" value={f.book_pressure} />
          <Diverging label="Queue pressure" value={f.queue_pressure} />
          {/* aggressive buy/sell split */}
          <div>
            <div className="flex justify-between text-[10.5px] text-text-muted">
              <span className="text-accent-green">Agg buy {aggBuy.toFixed(0)}</span>
              <span className="text-accent-red">Agg sell {aggSell.toFixed(0)}</span>
            </div>
            <div className="mt-1 flex h-2 overflow-hidden rounded-full">
              <div style={{ width: `${(aggBuy / aggTot) * 100}%`, background: CHART.green }} />
              <div style={{ width: `${(aggSell / aggTot) * 100}%`, background: CHART.red }} />
            </div>
          </div>
        </div>

        {/* execution-quality gauges */}
        <div className="grid grid-cols-2 gap-2.5 self-start">
          <Gauge label="Timing conf" value={f.timing_confidence} />
          <Gauge label="Exec aggr" value={f.execution_aggression} />
          <Gauge label="Passive fill" value={f.passive_fill_probability} />
          <Gauge label="Aggr fill" value={f.aggressive_fill_probability} />
          <Gauge label="Adverse sel" value={f.adverse_selection_risk} invert />
          <Gauge label="Vol burst" value={f.volatility_burst} invert />
        </div>
      </div>
    </Section>
  );
}
