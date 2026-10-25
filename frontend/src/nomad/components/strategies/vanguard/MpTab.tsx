"use client";

/**
 * MP structure — the Market-Profile layer of the Vanguard lane, full page.
 *
 * WHAT THIS TAB IS FOR. features_mp is written nightly (cycle daemon EOD):
 * one row per name per session — the session's TPO profile, its comparatives,
 * order-flow proxies where volume exists, and the TWO validated signal flags.
 * The mp_paper_trades book trades exactly those flags inside the researched
 * universe. This tab shows all of it, and — because this project has burned
 * itself repeatedly on decorative metrics — it renders the RESEARCH VERDICT
 * next to the numbers: what each metric was measured to mean, and which
 * celebrated MP concepts tested as false on this data.
 *
 * HONESTY RULES, same spirit as the rest of the desk:
 *   · A flag on a name OUTSIDE the researched universe renders greyed with an
 *     "observed, not traded" chip — the paper book must match the study.
 *   · of_available=false renders as "no volume series", never as zeros — the
 *     indices genuinely carry no volume in this store.
 *   · The falsified list is rendered, not linked: the fastest way to stop a
 *     future session re-testing the 80% rule is to show its failure here.
 */
import { useMemo, useState } from "react";
import {
  Activity,
  BookOpen,
  Layers,
  Moon,
  ShieldAlert,
  Waves,
} from "lucide-react";

import { MetricTile, Section, StatusBadge } from "@/components/desk-ui";

/* eslint-disable @typescript-eslint/no-explicit-any */

const DAY_TYPE_LABEL: Record<string, string> = {
  trend: "trend",
  normal: "normal (balanced)",
  normal_variation: "normal variation",
  neutral: "neutral",
  neutral_extreme: "neutral extreme",
  double_distribution: "double distribution",
};

/** Postgres NUMERIC arrives as a string through the JSON layer; coerce before
 * formatting or every .toFixed call throws on the first real payload. */
function n2(v: any, digits = 2): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : "\u2014";
}

function pct(v: any, digits = 2): string {
  const n = Number(v);
  return Number.isFinite(n) ? `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%` : "—";
}

function SignalChip({ kind }: { kind: "gap" | "oversold" }) {
  return kind === "gap" ? (
    <span className="inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-semibold text-amber-500">
      <Moon className="h-3 w-3" /> strong close
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-semibold text-sky-400">
      <Waves className="h-3 w-3" /> oversold MTF
    </span>
  );
}

const TH = "px-2 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground";
const TD = "px-2 py-1.5 text-xs tabular-nums";

export function MpTab({ data, verdicts }: { data?: any; verdicts?: any }) {
  const [showAll, setShowAll] = useState(false);

  const features: any[] = data?.features ?? [];
  const signals: any[] = data?.signals ?? [];
  const trades: any[] = data?.trades ?? [];
  const tradeSummary: any[] = data?.trade_summary ?? [];
  const summary = data?.summary;

  const verdictRows = useMemo(() => {
    const v = verdicts?.verdicts ?? {};
    return Object.entries(v).map(([key, val]: [string, any]) => ({
      key,
      status: String(val?.status ?? ""),
      meaning: String(val?.meaning ?? ""),
    }));
  }, [verdicts]);

  if (data && data.available === false) {
    return (
      <Section title="MP structure" icon={<Layers size={16} />}>
        <p className="text-sm text-muted-foreground">{data.note}</p>
      </Section>
    );
  }

  const openTrades = trades.filter((t) => t.status === "open");
  const closedTrades = trades.filter((t) => t.status !== "open");
  const shown = showAll ? features : features.slice(0, 40);

  return (
    <div className="space-y-4">
      {/* ── headline ── */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <MetricTile
          label="session"
          value={data?.as_of_dt ?? "—"}
          detail="latest features_mp write (cycle daemon EOD)"
        />
        <MetricTile
          label="names profiled"
          value={n2(summary?.names, 0)}
          detail="one TPO profile per name per session"
        />
        <MetricTile
          label="strong-close flags"
          value={n2(summary?.flagged_strong_close, 0)}
          detail="close above value area, close_pos 0.70–0.90 (acceptance)"
        />
        <MetricTile
          label="oversold-MTF flags"
          value={n2(summary?.flagged_oversold_mtf, 0)}
          detail="below day AND prior-week AND prior-month value"
        />
        <MetricTile
          label="volume coverage"
          value={
            summary?.names
              ? `${Math.round(((summary?.of_available ?? 0) / summary.names) * 100)}%`
              : "—"
          }
          detail="names with a real volume series (indices have none here)"
        />
      </div>

      {/* ── tonight's signals ── */}
      <Section
        title="Signals at this close"
        icon={<Moon size={16} />}
        description={data?.universe_note}
      >
        {signals.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No name met either validated condition at this close. A quiet night
            is the filter working, not a broken feed — the acceptance band
            refuses spikes at the high, and multi-timeframe oversold needs all
            three value areas broken.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]">
              <thead>
                <tr className="border-b border-border">
                  <th className={TH}>name</th>
                  <th className={TH}>signal</th>
                  <th className={TH}>traded?</th>
                  <th className={TH}>day type</th>
                  <th className={TH}>close pos</th>
                  <th className={TH}>value shift</th>
                  <th className={TH}>exp. range</th>
                  <th className={TH}>Δ-share</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((r) => (
                  <tr
                    key={r.underlying}
                    className={`border-b border-border/50 ${r.researched ? "" : "opacity-50"}`}
                  >
                    <td className={`${TD} font-semibold`}>{r.underlying}</td>
                    <td className={TD}>
                      <span className="flex gap-1">
                        {r.sig_strong_close && <SignalChip kind="gap" />}
                        {r.sig_oversold_mtf && <SignalChip kind="oversold" />}
                      </span>
                    </td>
                    <td className={TD}>
                      {r.researched ? (
                        <StatusBadge tone="positive" label="in universe" />
                      ) : (
                        <StatusBadge tone="neutral" label="observed, not traded" />
                      )}
                    </td>
                    <td className={TD}>{DAY_TYPE_LABEL[r.day_type] ?? r.day_type ?? "—"}</td>
                    <td className={TD}>{n2(r.close_pos, 2)}</td>
                    <td className={TD}>{r.value_shift ?? "—"}</td>
                    <td className={TD}>{pct(r.exp_range_pct)}</td>
                    <td className={TD}>
                      {r.of_available ? n2(r.of_delta_share, 2) : "no volume series"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* ── paper book ── */}
      <Section
        title="MP-edge paper book"
        icon={<BookOpen size={16} />}
        description="gap_overnight: enter close → exit next 09:15 open (the edge is the gap; nothing is held past it). oversold_mtf: 4-session hold, NO stop — a tight stop halves this edge by measurement. Costs recorded per trade: 4bp futures, 5bp stock proxy."
      >
        {tradeSummary.length > 0 && (
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            {tradeSummary.map((s) => (
              <MetricTile
                key={`${s.strategy}-${s.status}`}
                label={`${s.strategy} · ${s.status}`}
                value={`${s.n} trade${Number(s.n) === 1 ? "" : "s"}`}
                detail={
                  s.status === "closed"
                    ? `avg net ${pct(s.avg_net_pct, 3)} · win ${n2(s.win_pct, 0)}% · ₹${n2(s.pnl_rs, 0)}`
                    : "awaiting exit data"
                }
              />
            ))}
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead>
              <tr className="border-b border-border">
                <th className={TH}>strategy</th>
                <th className={TH}>name</th>
                <th className={TH}>signal date</th>
                <th className={TH}>entry</th>
                <th className={TH}>src</th>
                <th className={TH}>exit</th>
                <th className={TH}>net</th>
                <th className={TH}>status</th>
              </tr>
            </thead>
            <tbody>
              {[...openTrades, ...closedTrades].map((t) => (
                <tr key={t.id} className="border-b border-border/50">
                  <td className={TD}>{t.strategy}</td>
                  <td className={`${TD} font-semibold`}>{t.underlying}</td>
                  <td className={TD}>{t.signal_dt}</td>
                  <td className={TD}>{n2(t.entry_px, 2)}</td>
                  <td className={TD}>{t.entry_src}</td>
                  <td className={TD}>
                    {t.exit_px != null
                      ? `${n2(t.exit_px, 2)} (${t.exit_reason})`
                      : "—"}
                  </td>
                  <td className={TD}>
                    {t.net_ret_pct != null ? (
                      <span className={Number(t.net_ret_pct) >= 0 ? "text-emerald-500" : "text-red-500"}>
                        {pct(t.net_ret_pct, 3)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className={TD}>
                    <StatusBadge
                      tone={t.status === "open" ? "warning" : "neutral"}
                      label={t.status}
                    />
                  </td>
                </tr>
              ))}
              {trades.length === 0 && (
                <tr>
                  <td className={TD} colSpan={8}>
                    <span className="text-muted-foreground">
                      No paper trades yet — entries open at the first EOD pass
                      that finds a flag inside the researched universe.
                    </span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ── full structure table ── */}
      <Section
        title="Session structure — every name profiled"
        icon={<Activity size={16} />}
        description="Profile metrics are CONTEXT: they predict RANGE, not direction (that is the measured verdict, below). Sorted flags-first, then by expected range."
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px]">
            <thead>
              <tr className="border-b border-border">
                <th className={TH}>name</th>
                <th className={TH}>day type</th>
                <th className={TH}>close pos</th>
                <th className={TH}>IB width</th>
                <th className={TH}>VA width</th>
                <th className={TH}>range/IB</th>
                <th className={TH}>value shift</th>
                <th className={TH}>POC migr.</th>
                <th className={TH}>wk/mo loc</th>
                <th className={TH}>poor H/L</th>
                <th className={TH}>rvol20</th>
                <th className={TH}>flags</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.underlying} className="border-b border-border/50">
                  <td className={`${TD} font-semibold`}>{r.underlying}</td>
                  <td className={TD}>{DAY_TYPE_LABEL[r.day_type] ?? r.day_type ?? "—"}</td>
                  <td className={TD}>{n2(r.close_pos, 2)}</td>
                  <td className={TD}>{pct(r.ib_width_pct)}</td>
                  <td className={TD}>{pct(r.va_width_pct)}</td>
                  <td className={TD}>{n2(r.range_over_ib, 2)}</td>
                  <td className={TD}>{r.value_shift ?? "—"}</td>
                  <td className={TD}>{pct(r.poc_migration_pct)}</td>
                  <td className={TD}>
                    {(r.w_loc ?? "—")}/{(r.m_loc ?? "—")}
                  </td>
                  <td className={TD}>
                    {r.poor_high ? "H" : ""}
                    {r.poor_low ? "L" : ""}
                    {!r.poor_high && !r.poor_low ? "—" : ""}
                  </td>
                  <td className={TD}>
                    {r.of_available ? n2(r.of_rvol20, 2) : "—"}
                  </td>
                  <td className={TD}>
                    <span className="flex gap-1">
                      {r.sig_strong_close && <SignalChip kind="gap" />}
                      {r.sig_oversold_mtf && <SignalChip kind="oversold" />}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {features.length > 40 && (
          <button
            type="button"
            className="mt-2 text-xs text-muted-foreground underline"
            onClick={() => setShowAll((v) => !v)}
          >
            {showAll ? "show top 40" : `show all ${features.length} names`}
          </button>
        )}
      </Section>

      {/* ── the verdicts ── */}
      <Section
        title="MP intelligence — what each metric is entitled to mean"
        icon={<ShieldAlert size={16} />}
        description="Measured 2026-08-28 over ~5 years of 30-minute data with walk-forward and adversarial review. Served by /api/mp/unified/verdicts so every surface reads the same statements."
      >
        <div className="grid gap-2 md:grid-cols-2">
          {verdictRows.map((v) => (
            <div key={v.key} className="rounded border border-border/60 p-2">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-xs font-semibold">{v.key}</span>
                <StatusBadge
                  tone={
                    v.status === "validated"
                      ? "positive"
                      : v.status === "falsified"
                        ? "negative"
                        : "neutral"
                  }
                  label={v.status}
                />
              </div>
              <p className="text-xs text-muted-foreground">{v.meaning}</p>
            </div>
          ))}
          {verdictRows.length === 0 && (
            <p className="text-sm text-muted-foreground">
              verdicts unavailable — /api/mp/unified/verdicts did not respond.
            </p>
          )}
        </div>
      </Section>
    </div>
  );
}
