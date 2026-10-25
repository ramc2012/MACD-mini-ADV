"use client";

/**
 * One underlying, everything the lane collected about it.
 *
 * The organising idea is the DECISION TRACE at the top: for the latest bar,
 * each of M6's six legs is shown with the actual value it was given, the
 * threshold it was tested against, and the verdict. A trader should be able to
 * answer "why not this one?" without opening a database, and should be able to
 * tell a rejected signal from an absent input at a glance — the two have
 * completely different remedies and the lane has confused them before.
 *
 * Below it, one panel per feed. Each panel renders nothing rather than zeros
 * when its feed is empty: several of these genuinely stopped arriving in July
 * 2026, and a flat line at zero would read as a measurement.
 */
import { useMemo } from "react";
import {
  Area,
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
import { AlertTriangle, ArrowLeft, Ban, CalendarClock, Newspaper, Users } from "lucide-react";

import { Section, StatusBadge, formatIST, formatISTTime, formatMoney, formatNumber } from "@/components/desk-ui";
import { CHART } from "../shared/chartTheme";
import {
  GexScale,
  IvCell,
  IvsCell,
  SkewCell,
  LEG_LABELS,
  LEG_ORDER,
  LegChain,
  MwplCell,
  OiCell,
  OiStateBadge,
  PcrCell,
  PerfCell,
  RegimeChip,
  ScoreBar,
  TimingChip,
  Unmeasured,
  ValueAreaGauge,
  fmt,
  num,
} from "./vanguard-vocab";

const AXIS = { stroke: CHART.axis, fontSize: 10 };

/**
 * Dollar-gamma is a ~1e9 quantity, so a default axis renders as a column of
 * indistinguishable zeroes and the chart stops carrying information. Compact
 * SI-style ticks keep the SHAPE readable; the exact figure stays available in
 * the tooltip, where precision actually helps.
 */
function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(0)}k`;
  return value.toFixed(0);
}

function chronological<T extends Record<string, any>>(rows: T[] | undefined, key = "ts"): T[] {
  return [...(rows ?? [])].reverse().map((r) => ({ ...r, _t: r[key] }));
}

export function SymbolDetail({
  data,
  loading,
  thresholds,
  onBack,
}: {
  data?: any;
  loading?: boolean;
  thresholds?: Record<string, any>;
  onBack?: () => void;
}) {
  const latest = data?.evaluations?.[0];
  const catalog = data?.catalog ?? {};

  const price = useMemo(() => {
    const bars = chronological(data?.price_bars);
    const timingBy = new Map<string, any>((data?.timing_history ?? []).map((t: any) => [String(t.ts), t]));
    return bars.map((b) => {
      const t = timingBy.get(String(b.ts));
      return {
        t: formatISTTime(b.ts),
        close: num(b.close),
        volume: num(b.volume),
        ignition: t?.timing_state === "IGNITION" ? num(b.close) : null,
        exhaust: t?.timing_state === "EXHAUST" ? num(b.close) : null,
        state: t?.timing_state ?? null,
      };
    });
  }, [data]);

  const flow = useMemo(() => chronological(data?.flow_history).map((f) => ({
    t: String(f.ts).slice(5, 10),
    flow_score: num(f.flow_score),
    ivs_z: num(f.ivs_z),
    skew_z: num(f.skew_z),
    pcr_z: num(f.pcr_z),
    os_pctile: num(f.os_pctile) == null ? null : (num(f.os_pctile) as number) * 100,
    n_ingredients: num(f.n_ingredients),
  })), [data]);

  const gex = useMemo(() => chronological(data?.regime_history).map((g) => ({
    t: formatISTTime(g.ts),
    net_gex: num(g.net_gex),
    pct: num(g.gex_percentile) == null ? null : (num(g.gex_percentile) as number) * 100,
    regime: g.regime,
  })), [data]);

  const rs = useMemo(() => chronological(data?.sector_rs_history).map((r) => ({
    t: String(r.ts).slice(5, 10),
    z5: num(r.rs_z5), z20: num(r.rs_z20), z60: num(r.rs_z60),
  })), [data]);

  const oi = useMemo(() => chronological(data?.oi_history, "dt").map((o) => ({
    t: String(o.dt).slice(5, 10),
    total_oi: num(o.total_oi),
    d_oi_pct: num(o.d_oi_pct),
    oi_pcr: num(o.oi_pcr),
    mwpl_pct: num(o.mwpl_pct),
    close: num(o.close),
    oi_state: o.oi_state,
  })), [data]);

  // THE SNAPSHOT ANCHORS ON THE LAST SETTLED SESSION, not on the newest row.
  //
  // Today's row legitimately carries open interest (MWPL and chain OI are
  // same-day) and no close (the NSE spot feed is an overnight batch), so the
  // newest row is a real row with half its price columns correctly NULL.
  // Anchoring the panel there rendered close, 1d, 5d, 20d and 60d as dashes
  // every evening. Anchoring on the last settled session keeps every figure in
  // the panel from ONE date, and any newer OI-only row is reported separately
  // rather than blended in — mixing two dates inside one "snapshot" is exactly
  // the kind of quiet blending this desk exists to avoid.
  const history: any[] = data?.oi_history ?? [];
  const settled = history.find((row) => num(row.close) != null);
  const newestOi = history[0];
  const pendingOi =
    newestOi && settled && String(newestOi.dt) !== String(settled.dt) ? newestOi : null;

  const ivHistory = useMemo(() => chronological(data?.iv_history, "dt").map((r) => ({
    t: String(r.dt).slice(5, 10),
    atm_iv: num(r.atm_iv) == null ? null : (num(r.atm_iv) as number) * 100,
    ivs: num(r.ivs) == null ? null : (num(r.ivs) as number) * 100,
    percentile: num(r.iv_percentile) == null ? null : (num(r.iv_percentile) as number) * 100,
    n_strikes: num(r.n_strikes),
  })), [data]);
  const latestIv = data?.iv_history?.[0];

  // The volatility smile, from the individual contracts of the most recent
  // chain. Only `good` rows are drawn as points; weak ones are shown hollow so
  // the shape of the chain is visible without letting thin prints define it.
  const smile = useMemo(() => {
    const rows: any[] = data?.iv_chain ?? [];
    return rows
      .filter((r) => num(r.iv) != null)
      .map((r) => ({
        strike: num(r.strike),
        iv: (num(r.iv) as number) * 100,
        ce: r.option_type === "CE" ? (num(r.iv) as number) * 100 : null,
        pe: r.option_type === "PE" ? (num(r.iv) as number) * 100 : null,
        delta: num(r.delta),
        quality: r.quality,
      }))
      .sort((a, b) => (a.strike ?? 0) - (b.strike ?? 0));
  }, [data]);

  const delivery = useMemo(() => chronological(data?.delivery, "dt").map((d) => ({
    t: String(d.dt).slice(5, 10),
    delivery_pct: num(d.delivery_pct),
    value_cr: num(d.value) == null ? null : (num(d.value) as number) / 1e7,
  })), [data]);

  if (loading && !data) {
    return <Section title="Loading…"><p className="text-sm text-text-muted">Fetching every feed for this symbol.</p></Section>;
  }
  if (!data) return null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {onBack && (
          <button type="button" onClick={onBack}
                  className="inline-flex items-center gap-1 rounded-lg border border-bg-border px-2 py-1 text-[11px] text-text-secondary hover:bg-bg-hover/30">
            <ArrowLeft size={12} /> universe
          </button>
        )}
        <h2 className="font-mono text-lg font-semibold text-text-primary">{data.symbol}</h2>
        <span className="text-xs text-text-muted">
          {catalog.sector20 ?? catalog.sector_group ?? "unclassified"}
          {catalog.lot_size ? ` · lot ${catalog.lot_size}` : ""}
        </span>
        {data.ban && (
          <StatusBadge
            label={`F&O ban ${String(data.ban.dt).slice(0, 10)}`}
            variant="error"
            icon={<Ban size={12} />}
          />
        )}
        {!!data.results_calendar?.length && (
          <StatusBadge
            label={`results ${String(data.results_calendar[0].results_date)}`}
            variant="warn"
            icon={<CalendarClock size={12} />}
          />
        )}
      </div>

      {data.ban && (
        <div className="flex items-start gap-2 rounded-xl border border-accent-red/35 bg-accent-red/8 px-3 py-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-accent-red" />
          <p className="text-[11px] leading-relaxed text-text-secondary">{data.ban_note}</p>
        </div>
      )}

      <DecisionTrace latest={latest} thresholds={thresholds} />

      <MarketSnapshot latest={settled} pending={pendingOi} />

      <Section
        title="Price and where M5 saw ignition"
        description={
          price.length
            ? "30-minute bars on NSE's own :15/:45 grid — the same grid M5 computes on, so the markers line up with the bars they were derived from."
            : "No 30-minute bars on the exchange grid for this symbol."
        }
      >
        {!price.length ? (
          <Unmeasured why="underlying_spot_candles has no on-grid 30-minute bars for this symbol" />
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={price} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="t" {...AXIS} minTickGap={40} />
              <YAxis yAxisId="p" domain={["auto", "auto"]} {...AXIS} width={52} />
              <YAxis yAxisId="v" orientation="right" {...AXIS} width={40} hide />
              <Tooltip
                contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }}
                formatter={(v: any, n: any) => [formatNumber(v), n]}
              />
              <Bar yAxisId="v" dataKey="volume" fill={CHART.blue} fillOpacity={0.16} />
              <Line yAxisId="p" type="monotone" dataKey="close" stroke={CHART.blue} dot={false} strokeWidth={1.4} />
              <Line yAxisId="p" dataKey="ignition" stroke={CHART.green} strokeWidth={0}
                    dot={{ r: 3.2, fill: CHART.green }} isAnimationActive={false} />
              <Line yAxisId="p" dataKey="exhaust" stroke={CHART.red} strokeWidth={0}
                    dot={{ r: 3.2, fill: CHART.red }} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </Section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section
          title="M2 — options informed flow"
          description={
            flow.length
              ? `${flow.length} sessions. The composite is only as good as the ingredient count beneath it.`
              : "M2 has never written a flow row for this symbol."
          }
        >
          {!flow.length ? (
            <Unmeasured why="features_flow has no rows for this symbol — the equity IV feed stopped on 2026-07-28" />
          ) : (
            <>
              <ResponsiveContainer width="100%" height={170}>
                <ComposedChart data={flow} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={CHART.grid} vertical={false} />
                  <XAxis dataKey="t" {...AXIS} minTickGap={30} />
                  <YAxis domain={[-100, 100]} {...AXIS} width={38} />
                  <ReferenceLine y={0} stroke={CHART.axis} strokeDasharray="2 3" />
                  <ReferenceLine y={num(thresholds?.flow_min_abs) ?? 60} stroke={CHART.amber} strokeDasharray="3 3" />
                  <ReferenceLine y={-(num(thresholds?.flow_min_abs) ?? 60)} stroke={CHART.amber} strokeDasharray="3 3" />
                  <Tooltip contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }} />
                  <Area type="monotone" dataKey="flow_score" stroke={CHART.violet} fill={CHART.violet}
                        fillOpacity={0.14} strokeWidth={1.5} />
                </ComposedChart>
              </ResponsiveContainer>
              <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  ["IVS z", "ivs_z", "Call-minus-put ATM implied vol, z-scored. Cremers–Weinbaum's informed-flow proxy."],
                  ["skew z", "skew_z", "25-delta put minus call IV, z-scored. Nearest-delta row, with no tolerance band — on a thin chain this can be a very different delta."],
                  ["O/S pct", "os_pctile", "Delta-weighted option volume over share volume, as a percentile. UNSIGNED: it measures activity, not direction."],
                  ["PCR z", "pcr_z", "Session-over-session change in OI put-call ratio, z-scored."],
                ].map(([label, key, why]) => {
                  const last = flow[flow.length - 1]?.[key as keyof (typeof flow)[number]] as number | null;
                  return (
                    <div key={key} className="rounded-lg border border-bg-border bg-bg-secondary/25 px-2 py-1.5">
                      <div className="cursor-help text-[10px] uppercase tracking-[0.12em] text-text-muted" title={why as string}>
                        {label}
                      </div>
                      <div className="font-mono text-sm text-text-primary">
                        {last == null ? <span className="text-text-muted">—</span> : last.toFixed(2)}
                      </div>
                    </div>
                  );
                })}
              </div>
              <p className="mt-2 text-[11px] text-text-muted">
                Latest ingredient count:{" "}
                <span className="font-mono text-text-secondary">
                  {flow[flow.length - 1]?.n_ingredients ?? "not recorded"}
                </span>{" "}
                of 5. A ±100 built from one ingredient is not the same reading as one built from five,
                and the one that most often survives alone (O/S) carries no direction at all.
              </p>
            </>
          )}
        </Section>

        <Section
          title="M3 — dealer gamma regime"
          description={
            gex.length
              ? "net_gex is raw and unnormalised; the percentile is what M6 actually consumes."
              : "No GEX rows for this symbol."
          }
        >
          {!gex.length ? (
            <Unmeasured why="regime has no rows for this symbol — per-stock gamma coverage collapsed around 2026-06-23" />
          ) : (
            <>
              <ResponsiveContainer width="100%" height={170}>
                <ComposedChart data={gex} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={CHART.grid} vertical={false} />
                  <XAxis dataKey="t" {...AXIS} minTickGap={40} />
                  <YAxis yAxisId="g" {...AXIS} width={46} tickFormatter={compact} />
                  <YAxis yAxisId="p" orientation="right" domain={[0, 100]} {...AXIS} width={34} />
                  <ReferenceLine yAxisId="g" y={0} stroke={CHART.axis} strokeDasharray="2 3" />
                  <Tooltip
                    contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }}
                    formatter={(v: any, n: any) =>
                      [n === "net_gex" ? formatNumber(Number(v), 0) : Number(v).toFixed(1), n]}
                  />
                  <Bar yAxisId="g" dataKey="net_gex" fill={CHART.blue} fillOpacity={0.35} />
                  <Line yAxisId="p" type="monotone" dataKey="pct" stroke={CHART.amber} dot={false} strokeWidth={1.3} />
                </ComposedChart>
              </ResponsiveContainer>
              <p className="mt-2 text-[11px] text-text-muted">
                Negative net gamma amplifies moves and is what M6 permits for a momentum candidate;
                positive gamma dampens them. The regime bucket is a percentile against this symbol&apos;s
                own trailing 60 sessions, not a market-wide cut.
              </p>
            </>
          )}
        </Section>

        <Section
          title="M4 — sector relative strength"
          description={
            rs.length
              ? `${catalog.sector20 ?? "sector"} equal-weight index, z-scored over 5 / 20 / 60 SESSIONS.`
              : "No sector RS series for this symbol's bucket."
          }
        >
          {!rs.length ? (
            <Unmeasured why="sector_rs has no rows for this symbol's sector20 (it may be unclassified)" />
          ) : (
            <>
              <ResponsiveContainer width="100%" height={160}>
                <ComposedChart data={rs} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={CHART.grid} vertical={false} />
                  <XAxis dataKey="t" {...AXIS} minTickGap={30} />
                  <YAxis {...AXIS} width={38} />
                  <ReferenceLine y={0} stroke={CHART.axis} strokeDasharray="2 3" />
                  <ReferenceLine y={num(thresholds?.sector_rs_min_abs_z) ?? 1} stroke={CHART.amber} strokeDasharray="3 3" />
                  <ReferenceLine y={-(num(thresholds?.sector_rs_min_abs_z) ?? 1)} stroke={CHART.amber} strokeDasharray="3 3" />
                  <Tooltip contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }} />
                  <Line type="monotone" dataKey="z5" stroke={CHART.blue} dot={false} strokeWidth={1} />
                  <Line type="monotone" dataKey="z20" stroke={CHART.green} dot={false} strokeWidth={1.5} />
                  <Line type="monotone" dataKey="z60" stroke={CHART.violet} dot={false} strokeWidth={1} />
                </ComposedChart>
              </ResponsiveContainer>
              {!!data.leadlag?.length && (
                <p className="mt-2 text-[11px] text-text-muted">
                  Lead-lag: best correlation at lag{" "}
                  <span className="font-mono text-text-secondary">{data.leadlag[0].best_lag}</span>{" "}
                  sessions (r = {fmt(data.leadlag[0].corr)}).{" "}
                  {num(data.leadlag[0].best_lag)! > 0
                    ? "Positive — this name tends to FOLLOW its sector, which is the catch-up case M6 rewards."
                    : num(data.leadlag[0].best_lag)! < 0
                      ? "Negative — this name tends to LEAD its sector, and gets only the neutral baseline."
                      : "Synchronous with its sector."}
                </p>
              )}
            </>
          )}
        </Section>

        <Section
          title="Implied volatility — solved, not sourced"
          description={
            ivHistory.length
              ? "Black-Scholes IV from each contract's own price. The vendor's iv column stopped for equities on 2026-07-28; every number here is computed."
              : "No solved IV surface for this symbol."
          }
        >
          {!ivHistory.length ? (
            <Unmeasured why="iv_surface has no rows — run features/m_implied_vol.py then m_iv_surface.py" />
          ) : (
            <>
              <ResponsiveContainer width="100%" height={160}>
                <ComposedChart data={ivHistory} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={CHART.grid} vertical={false} />
                  <XAxis dataKey="t" {...AXIS} minTickGap={30} />
                  <YAxis yAxisId="iv" {...AXIS} width={40} unit="%" />
                  <YAxis yAxisId="p" orientation="right" domain={[0, 100]} {...AXIS} width={34} />
                  <Tooltip contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }}
                           formatter={(v: any, n: any) => [`${Number(v).toFixed(2)}${n === "percentile" ? "" : "%"}`, n]} />
                  <Line yAxisId="iv" type="monotone" dataKey="atm_iv" stroke={CHART.violet} dot={false} strokeWidth={1.5} />
                  <Line yAxisId="p" type="monotone" dataKey="percentile" stroke={CHART.amber} dot={false}
                        strokeWidth={1} strokeDasharray="3 3" />
                </ComposedChart>
              </ResponsiveContainer>
              <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                <MiniTile label="ATM IV">
                  <IvCell iv={num(latestIv?.atm_iv)} percentile={num(latestIv?.iv_percentile)}
                          change={num(latestIv?.d_atm_iv)} />
                </MiniTile>
                <MiniTile label="IVS (call−put)"><IvsCell ivs={num(latestIv?.ivs)} /></MiniTile>
                <MiniTile label="25Δ skew">
                  <SkewCell skew={num(latestIv?.skew_25d)} reason={latestIv?.skew_reason} />
                </MiniTile>
                <MiniTile label="strikes / span">
                  <span className="font-mono text-[11px] text-text-secondary">
                    {latestIv?.n_strikes ?? "—"} / {fmt(latestIv?.delta_span, 2)}
                  </span>
                </MiniTile>
              </div>
              {latestIv?.skew_reason && (
                <p className="mt-2 text-[11px] leading-relaxed text-text-muted">
                  <strong>Why there is no skew:</strong> {latestIv.skew_reason}. Substituting the
                  nearest available strike — which is what M2 does — returns a near-ATM contract and
                  measures the call-minus-put spread a second time under a different name.
                </p>
              )}
            </>
          )}
        </Section>

        <Section
          title="Volatility smile"
          description={
            smile.length
              ? "Every contract in the latest chain, at its own solved IV. Calls and puts drawn apart because they are different instruments, not one curve."
              : "No solved contracts in the latest chain."
          }
        >
          {!smile.length ? (
            <Unmeasured why="no contract-level IVs solved for this symbol's most recent session" />
          ) : (
            <ResponsiveContainer width="100%" height={170}>
              <ComposedChart data={smile} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={CHART.grid} vertical={false} />
                <XAxis dataKey="strike" type="number" domain={["dataMin", "dataMax"]} {...AXIS} />
                <YAxis {...AXIS} width={40} unit="%" />
                <Tooltip contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }}
                         formatter={(v: any, n: any) => [`${Number(v).toFixed(2)}%`, n]} />
                <Line dataKey="ce" stroke={CHART.green} strokeWidth={0}
                      dot={{ r: 3, fill: CHART.green }} isAnimationActive={false} name="call IV" connectNulls={false} />
                <Line dataKey="pe" stroke={CHART.red} strokeWidth={0}
                      dot={{ r: 3, fill: CHART.red }} isAnimationActive={false} name="put IV" connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
          )}
          <p className="mt-2 text-[11px] text-text-muted">
            {smile.length} priced contracts. A 25-delta risk reversal needs the chain to reach the
            wings; with a handful of near-money strikes it does not exist to be measured.
          </p>
        </Section>

        <Section
          title="Open interest and positioning"
          description={
            oi.length
              ? "Aggregate F&O open interest with its session change. The conjunction of ΔOI and Δprice is what the buildup state reads."
              : "No open-interest rows for this symbol."
          }
        >
          {!oi.length ? (
            <Unmeasured why="oi_positioning has no rows for this symbol — run features/m_oi_positioning.py" />
          ) : (
            <>
              <ResponsiveContainer width="100%" height={170}>
                <ComposedChart data={oi} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={CHART.grid} vertical={false} />
                  <XAxis dataKey="t" {...AXIS} minTickGap={30} />
                  <YAxis yAxisId="oi" {...AXIS} width={46} tickFormatter={compact} />
                  <YAxis yAxisId="d" orientation="right" {...AXIS} width={38} />
                  <ReferenceLine yAxisId="d" y={0} stroke={CHART.axis} strokeDasharray="2 3" />
                  <Tooltip
                    contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }}
                    formatter={(v: any, n: any) =>
                      [n === "total_oi" ? formatNumber(Number(v), 0) : `${Number(v).toFixed(2)}%`, n]}
                  />
                  <Bar yAxisId="oi" dataKey="total_oi" fill={CHART.blue} fillOpacity={0.28} />
                  <Line yAxisId="d" type="monotone" dataKey="d_oi_pct" stroke={CHART.amber}
                        dot={false} strokeWidth={1.3} />
                </ComposedChart>
              </ResponsiveContainer>
              <p className="mt-2 text-[11px] leading-relaxed text-text-muted">
                {newestOi?.oi_source === "chain_sum"
                  ? "Summed from the option contracts this lane collects — a subset that varies with collection health, not the exchange's own aggregate."
                  : "NSE's own market-wide F&O open interest, the same publication the ban list comes from."}{" "}
                m2_flow.py records that no stock-level OI source exists in this schema; it does, and
                this is it — the flow composite&apos;s fourth ingredient has been NULL for that reason
                since the lane was built.
              </p>
            </>
          )}
        </Section>

        <Section
          title="Delivery and turnover"
          description={
            delivery.length
              ? "NSE bhavcopy delivery percentage — collected by M1, and currently read by no feature module."
              : "No bhavcopy delivery rows for this symbol."
          }
        >
          {!delivery.length ? (
            <Unmeasured why="bhavcopy_delivery has no rows for this symbol" />
          ) : (
            <ResponsiveContainer width="100%" height={160}>
              <ComposedChart data={delivery} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={CHART.grid} vertical={false} />
                <XAxis dataKey="t" {...AXIS} minTickGap={30} />
                <YAxis yAxisId="d" domain={[0, 100]} {...AXIS} width={36} />
                <YAxis yAxisId="v" orientation="right" {...AXIS} width={44} tickFormatter={compact} />
                <Tooltip contentStyle={{ background: CHART.surface, border: `1px solid ${CHART.border}`, fontSize: 11 }} />
                <Bar yAxisId="v" dataKey="value_cr" fill={CHART.blue} fillOpacity={0.2} name="turnover ₹cr" />
                <Line yAxisId="d" type="monotone" dataKey="delivery_pct" stroke={CHART.green}
                      dot={false} strokeWidth={1.4} name="delivery %" />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </Section>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Bulk & block deals" icon={<Users size={16} />}
                 description="M1's NSE bulk/block feed. Collected, and not yet consumed by any feature module.">
          {!data.bulk_block?.length ? (
            <Unmeasured why="no bulk or block deals recorded for this symbol" />
          ) : (
            <div className="max-h-56 overflow-y-auto">
              <table className="w-full text-left text-[11px]">
                <tbody className="font-mono">
                  {data.bulk_block.map((d: any, i: number) => (
                    <tr key={i} className="border-t border-bg-border/50">
                      <td className="py-1 pr-2 text-text-muted">{String(d.dt).slice(0, 10)}</td>
                      <td className="max-w-[200px] truncate py-1 pr-2 text-text-secondary" title={d.client_name}>
                        {d.client_name}
                      </td>
                      <td className={"py-1 pr-2 " + (d.deal_type === "BUY" ? "text-accent-green" : "text-accent-red")}>
                        {d.deal_type}
                      </td>
                      <td className="py-1 pr-2 text-text-muted">{d.kind}</td>
                      <td className="py-1 pr-2 text-right text-text-secondary">{formatNumber(num(d.quantity))}</td>
                      <td className="py-1 text-right text-text-secondary">{fmt(d.price)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>

        <Section title="Announcements" icon={<Newspaper size={16} />}
                 description="M1's corporate-announcement feed. The results guard reads results_calendar, not this.">
          {!data.announcements?.length ? (
            <Unmeasured why="no announcements recorded for this symbol" />
          ) : (
            <div className="max-h-56 space-y-1.5 overflow-y-auto">
              {data.announcements.map((a: any, i: number) => (
                <div key={i} className="rounded-lg border border-bg-border bg-bg-secondary/25 px-2.5 py-1.5">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-[11px] text-text-primary">{a.subject}</span>
                    <span className="shrink-0 font-mono text-[10px] text-text-muted">
                      {formatIST(a.dt)}
                    </span>
                  </div>
                  {a.category && a.category !== "general" && (
                    <span className="mt-0.5 inline-block rounded bg-bg-primary/40 px-1 text-[9px] uppercase tracking-wide text-text-muted">
                      {a.category}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>

      {!!data.tickets?.length && (
        <Section title="Tickets for this symbol"
                 description="Emitted and gated alike — a gated row records the gate that stopped it.">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[11px]">
              <thead className="text-[10px] uppercase tracking-[0.14em] text-text-muted">
                <tr>
                  <th className="py-1.5 pr-3">bar</th>
                  <th className="py-1.5 pr-3">instrument</th>
                  <th className="py-1.5 pr-3">conviction</th>
                  <th className="py-1.5 pr-3">risk @ stop</th>
                  <th className="py-1.5">outcome</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {data.tickets.map((t: any) => (
                  <tr key={t.id} className="border-t border-bg-border/50">
                    <td className="py-1.5 pr-3 text-text-muted">{formatIST(t.ts)}</td>
                    <td className="py-1.5 pr-3 text-text-secondary">{t.instrument ?? "—"}</td>
                    <td className="py-1.5 pr-3">{fmt(t.conviction, 1)}</td>
                    <td className="py-1.5 pr-3 text-text-secondary">
                      {t.sizing_risk_rupees ? formatMoney(num(t.sizing_risk_rupees)) : "—"}
                    </td>
                    <td className="py-1.5">
                      {t.emitted ? <span className="text-accent-green">emitted</span>
                                 : <span className="text-text-muted">{t.gated_reason}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

/**
 * The six legs with the actual value each was given and the threshold it faced.
 *
 * This is the panel that answers "why not this one?". Note the deliberate
 * distinction in every row between a value that FAILED a test and a value that
 * was never there: they read differently because they need different fixes.
 */
function DecisionTrace({ latest, thresholds }: { latest?: any; thresholds?: Record<string, any> }) {
  if (!latest) {
    return (
      <Section title="Decision trace">
        <Unmeasured why="M6 has not evaluated this symbol since the evaluation journal was added" />
      </Section>
    );
  }
  const t = thresholds ?? {};
  const legs: Record<string, boolean | null> = {
    flow_present: latest.leg_flow_present,
    flow_fresh: latest.leg_flow_fresh,
    flow_strength: latest.leg_flow_strength,
    sector_rs: latest.leg_sector_rs,
    regime: latest.leg_regime,
    timing: latest.leg_timing,
  };
  const rows: { leg: string; value: React.ReactNode; test: string }[] = [
    {
      leg: "flow_present",
      value: latest.flow_score == null
        ? <span className="text-text-muted">no flow row at all</span>
        : <span>flow_score {fmt(latest.flow_score, 1)} from {latest.flow_ts ? formatIST(latest.flow_ts) : "?"}</span>,
      test: "M2 wrote a flow score for an earlier session",
    },
    {
      leg: "flow_fresh",
      value: (
        <span>
          {latest.flow_age_sessions == null ? "age unknown" : `${latest.flow_age_sessions} sessions old`}
          {" · "}
          {latest.flow_n_ingredients == null ? "ingredients not recorded" : `${latest.flow_n_ingredients} ingredients`}
        </span>
      ),
      test: `≤ ${t.flow_max_age_sessions ?? "?"} sessions and ≥ ${t.flow_min_ingredients ?? "?"} ingredients`,
    },
    {
      leg: "flow_strength",
      value: <span>|flow_score| = {fmt(Math.abs(num(latest.flow_score) ?? NaN), 1)}</span>,
      test: `≥ ${t.flow_min_abs ?? "?"}`,
    },
    {
      leg: "sector_rs",
      value: latest.rs_z20 == null
        ? <span className="text-text-muted">no sector RS row</span>
        : <span>rs_z20 {fmt(latest.rs_z20)} ({latest.rs_age_sessions ?? "?"}s old), direction {latest.direction ?? "—"}</span>,
      test: `|z| ≥ ${t.sector_rs_min_abs_z ?? "?"}, same sign as flow, ≤ ${t.rs_max_age_sessions ?? "?"} sessions`,
    },
    {
      leg: "regime",
      value: <span className="inline-flex items-center gap-1.5">
        <RegimeChip regime={latest.regime} ageBars={latest.regime_age_bars} maxAgeBars={t.regime_max_age_bars} />
        {latest.gex_percentile != null && <span className="text-text-muted">pct {fmt((num(latest.gex_percentile) as number) * 100, 0)}</span>}
      </span>,
      test: `${(t.regime_permits ?? []).join(" / ") || "?"}, ≤ ${t.regime_max_age_bars ?? "?"} bars old`,
    },
    {
      leg: "timing",
      value: <span className="inline-flex items-center gap-1.5">
        <TimingChip state={latest.timing_state} />
        <ScoreBar value={num(latest.timing_score)} threshold={t.timing_min_score} color="green" width={44} />
        <ValueAreaGauge position={num(latest.va_position)} width={48} />
      </span>,
      test: `IGNITION and score ≥ ${t.timing_min_score ?? "?"}`,
    },
  ];

  return (
    <Section
      title="Decision trace"
      description={`M6 at ${formatIST(latest.ts)} — each leg with the value it was actually given.`}
      rightSlot={
        <div className="flex items-center gap-2">
          <LegChain legs={legs} firstFailed={latest.survived_filter ? null : latest.first_failed_leg} />
          {latest.survived_filter ? (
            <StatusBadge label="admitted by M6" variant="success" />
          ) : (
            <StatusBadge label={`rejected at ${LEG_LABELS[latest.first_failed_leg] ?? latest.first_failed_leg}`} variant="warn" />
          )}
        </div>
      }
    >
      <div className="space-y-1">
        {LEG_ORDER.map((leg) => {
          const row = rows.find((r) => r.leg === leg)!;
          const state = legs[leg];
          const died = !latest.survived_filter && latest.first_failed_leg === leg;
          return (
            <div
              key={leg}
              className={
                "grid grid-cols-[110px_1fr_auto] items-baseline gap-3 rounded-lg border px-3 py-1.5 " +
                (died
                  ? "border-accent-red/40 bg-accent-red/8"
                  : state === true
                    ? "border-bg-border bg-bg-secondary/25"
                    : "border-dashed border-bg-border/60 bg-transparent opacity-55")
              }
            >
              <span className={"text-[11px] " + (died ? "text-accent-red" : "text-text-secondary")}>
                {LEG_LABELS[leg]}
              </span>
              <span className="font-mono text-[11px] text-text-primary">
                {state === null ? <span className="text-text-muted">never asked</span> : row.value}
              </span>
              <span className="text-right text-[10px] text-text-muted">{row.test}</span>
            </div>
          );
        })}
      </div>
      {latest.conviction != null && (
        <div className="mt-2.5 flex flex-wrap items-center gap-3 border-t border-bg-border/60 pt-2.5">
          <span className="text-[11px] text-text-muted">
            shadow conviction (computed for every symbol, survivor or not)
          </span>
          <ScoreBar value={num(latest.conviction)} threshold={t.conviction_min} color="violet" width={90} />
          {latest.component_scores && (
            <span className="font-mono text-[10px] text-text-muted">
              {Object.entries(latest.component_scores)
                .map(([k, v]) => `${k} ${Number(v).toFixed(0)}`)
                .join(" · ")}
            </span>
          )}
        </div>
      )}
    </Section>
  );
}


/**
 * The current market read for one symbol, above every chart.
 *
 * This is the block the desk was missing entirely: a trader opening a name saw
 * feature z-scores and no price, no open interest, no positioning and no
 * performance. Everything here is end-of-session — the NSE spot feed arrives as
 * an overnight batch — so the session it belongs to is stated rather than
 * implied by proximity to the word "current".
 */
function MarketSnapshot({ latest, pending }: { latest?: any; pending?: any }) {
  if (!latest) {
    return (
      <Section title="Market snapshot">
        <Unmeasured why="oi_positioning has no settled session for this symbol yet" />
      </Section>
    );
  }
  return (
    <Section
      title="Market snapshot"
      description={
        `Every figure below is from ${String(latest.dt).slice(0, 10)}, the last SETTLED session. ` +
        "The NSE spot feed arrives as an overnight batch, so the current session has no close until tomorrow."
      }
      rightSlot={
        <OiStateBadge
          state={latest.oi_state}
          dOiPct={num(latest.d_oi_pct)}
          dPricePct={num(latest.d_price_pct)}
        />
      }
    >
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        <Tile label="close">
          <span className="font-mono text-sm text-text-primary">{fmt(latest.close)}</span>
        </Tile>
        <Tile label="1 day"><PerfCell value={num(latest.d_price_pct)} scale={5} /></Tile>
        <Tile label="5 day"><PerfCell value={num(latest.ret_5d)} scale={10} /></Tile>
        <Tile label="20 day"><PerfCell value={num(latest.ret_20d)} scale={20} /></Tile>
        <Tile label="60 day"><PerfCell value={num(latest.ret_60d)} scale={40} /></Tile>
        <Tile label="open interest">
          <OiCell total={num(latest.total_oi)} dPct={num(latest.d_oi_pct)} source={latest.oi_source} />
        </Tile>
        <Tile label="MWPL"><MwplCell pct={num(latest.mwpl_pct)} /></Tile>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
        <Tile label="CE open interest">
          <OiCell total={num(latest.ce_oi)} source={latest.oi_source} />
        </Tile>
        <Tile label="PE open interest">
          <OiCell total={num(latest.pe_oi)} source={latest.oi_source} />
        </Tile>
        <Tile label="PCR (front expiry)">
          <PcrCell pcr={num(latest.oi_pcr)} dPcr={num(latest.d_oi_pcr)} />
        </Tile>
        <Tile label="ΔOI">
          <PerfCell value={num(latest.d_oi_pct)} scale={20} />
        </Tile>
      </div>
      {pending && (
        <div className="mt-2 flex flex-wrap items-center gap-3 rounded-xl border border-bg-border bg-bg-secondary/20 px-3 py-2">
          <span className="text-[10.5px] uppercase tracking-[0.16em] text-text-muted">
            newer, price still pending · {String(pending.dt).slice(0, 10)}
          </span>
          <span className="inline-flex items-center gap-1.5 text-[11px] text-text-secondary">
            OI <OiCell total={num(pending.total_oi)} dPct={num(pending.d_oi_pct)} source={pending.oi_source} />
          </span>
          <span className="inline-flex items-center gap-1.5 text-[11px] text-text-secondary">
            PCR <PcrCell pcr={num(pending.oi_pcr)} dPcr={num(pending.d_oi_pcr)} />
          </span>
          <span className="inline-flex items-center gap-1.5 text-[11px] text-text-secondary">
            MWPL <MwplCell pct={num(pending.mwpl_pct)} />
          </span>
          <span className="text-[10px] text-text-muted">
            No buildup state: the conjunction needs a close, and this session has none yet.
          </span>
        </div>
      )}
    </Section>
  );
}

function MiniTile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-bg-border bg-bg-secondary/25 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-[0.12em] text-text-muted">{label}</div>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-bg-border bg-bg-secondary/28 px-3 py-2">
      <div className="text-[10.5px] uppercase tracking-[0.16em] text-text-muted">{label}</div>
      <div className="mt-1">{children}</div>
    </div>
  );
}
