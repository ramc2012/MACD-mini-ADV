"use client";

/**
 * The cross-sectional IC study, and M7's sizing coherence.
 *
 * Both panels exist to render a number HONESTLY rather than impressively.
 *
 * The IC table's most important column is `sessions`, not `IC`. n in every
 * t-statistic is the number of SESSIONS, because same-session names share a
 * market-wide shock and a per-observation SE runs several times too small —
 * this programme measured exactly that error at 1.6x to 4.7x. So an IC with
 * too few sessions behind it is rendered greyed and labelled under-powered
 * instead of coloured by significance it does not have.
 *
 * The risk panel renders M7's configured limits AND whether they can bind. At
 * M6's 15% stop they cannot: the three numbers are mutually unsatisfiable and
 * the premium cap always wins. A risk panel that showed "-2% daily stand-down"
 * without saying it is unreachable would be asserting a control that is not
 * there.
 */
import { Activity, ShieldAlert, Sigma } from "lucide-react";

import { MetricTile, Section, StatusBadge, formatMoney } from "@/components/desk-ui";
import { ScoreBar, Unmeasured, fmt, num } from "./vanguard-vocab";

const COMPONENT_LABEL: Record<string, string> = {
  signed_flow: "M2 flow (signed)",
  signed_rs: "M4 sector RS (signed)",
  signed_timing: "M5 timing (signed by VA side)",
  signed_regime: "M3 gamma percentile (centred)",
  conviction: "conviction (vs |return|)",
};

export function ResearchTab({ crossSection, risk }: { crossSection?: any; risk?: any }) {
  return (
    <div className="space-y-4">
      <CrossSectionPanel data={crossSection} />
      <RiskPanel data={risk} />
    </div>
  );
}

function CrossSectionPanel({ data }: { data?: any }) {
  const runs: any[] = data?.runs ?? [];

  if (data?.unavailable) {
    return (
      <Section title="Cross-sectional IC" icon={<Sigma size={16} />}>
        <p className="text-sm text-text-secondary">{data.unavailable}</p>
        <p className="mt-2 text-[11px] text-text-muted">
          This study scores every symbol at every bar, not just the ones that passed M6&apos;s filter.
          Correlating a component inside its own acceptance region truncates its range to almost
          nothing, which is why the ticket-level IC in Attribution can never falsify anything.
        </p>
      </Section>
    );
  }

  const horizons = Array.from(new Set(runs.map((r) => r.horizon_bars))).sort((a, b) => a - b);

  return (
    <Section
      title="Cross-sectional information coefficient"
      icon={<Sigma size={16} />}
      description={
        data?.as_of
          ? `As of ${String(data.as_of)}. Spearman rank IC per bar, averaged per session; the standard error is taken ACROSS sessions.`
          : "No study has been run yet."
      }
      rightSlot={
        runs.length ? (
          <span className="text-[10px] text-text-muted">
            window {String(runs[0].window_start)} → {String(runs[0].window_end)}
          </span>
        ) : null
      }
    >
      {!runs.length ? (
        <Unmeasured why="cross_section_ic has no rows — run `make ic` or wait for the EOD cycle" />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="text-[10px] uppercase tracking-[0.14em] text-text-muted">
                <tr>
                  <th className="py-1.5 pr-3">component</th>
                  <th className="py-1.5 pr-3">h</th>
                  <th className="py-1.5 pr-3 text-right">obs</th>
                  <th className="py-1.5 pr-3 text-right" title="n in the t-statistic. Not the observation count.">sessions</th>
                  <th className="py-1.5 pr-3 text-right">mean IC</th>
                  <th className="py-1.5 pr-3 text-right">SE (clustered)</th>
                  <th className="py-1.5 pr-3 text-right">t</th>
                  <th className="py-1.5">95% CI</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {horizons.map((h) =>
                  runs
                    .filter((r) => r.horizon_bars === h)
                    .map((r) => {
                      const report = r.report ?? {};
                      const adequate = report.sample_adequate;
                      const ic = num(r.mean_ic);
                      const ci = [num(r.ci_low), num(r.ci_high)];
                      const excludesZero =
                        ci[0] != null && ci[1] != null && (ci[0] > 0 || ci[1] < 0);
                      return (
                        <tr key={`${r.component}-${h}`} className="border-t border-bg-border/50">
                          <td className="py-1.5 pr-3 font-sans text-text-primary">
                            {COMPONENT_LABEL[r.component] ?? r.component}
                          </td>
                          <td className="py-1.5 pr-3 text-text-muted">{h}</td>
                          <td className="py-1.5 pr-3 text-right text-text-secondary">
                            {Number(r.n_obs).toLocaleString()}
                          </td>
                          <td className={"py-1.5 pr-3 text-right " + (adequate ? "text-text-secondary" : "text-accent-amber")}>
                            {r.n_sessions}
                          </td>
                          <td
                            className={
                              "py-1.5 pr-3 text-right " +
                              (!adequate
                                ? "text-text-muted"
                                : excludesZero
                                  ? (ic ?? 0) > 0 ? "text-accent-green" : "text-accent-red"
                                  : "text-text-secondary")
                            }
                          >
                            {ic == null ? "—" : `${ic > 0 ? "+" : ""}${ic.toFixed(4)}`}
                          </td>
                          <td className="py-1.5 pr-3 text-right text-text-muted">
                            {fmt(r.ic_se_clustered, 4)}
                          </td>
                          <td className="py-1.5 pr-3 text-right text-text-secondary">
                            {num(r.t_stat) == null ? "—" : `${(num(r.t_stat) as number) > 0 ? "+" : ""}${(num(r.t_stat) as number).toFixed(2)}`}
                          </td>
                          <td className="py-1.5 text-text-muted">
                            {ci[0] == null ? (
                              <span title="one session has no spread — a t-statistic from it would be an invented certainty">
                                needs ≥ 2 sessions
                              </span>
                            ) : (
                              `[${ci[0].toFixed(4)}, ${(ci[1] as number).toFixed(4)}]`
                            )}
                            {!adequate && (
                              <span className="ml-1 text-accent-amber" title="too few sessions for this estimate to mean anything">
                                under-powered
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    }),
                )}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-text-muted">{data?.note}</p>
        </>
      )}
    </Section>
  );
}

function RiskPanel({ data }: { data?: any }) {
  const limits = data?.limits ?? {};
  const coherence = data?.coherence ?? {};
  const incoherent = coherence.coherent === false;

  return (
    <Section
      title="M7 — risk limits, and whether they can bind"
      icon={<ShieldAlert size={16} />}
      description="Sizing is denominated in risk AT STOP; premium paid is capped separately as the gap-to-zero exposure."
      rightSlot={
        incoherent ? (
          <StatusBadge label="limits cannot bind as configured" variant="warn" />
        ) : (
          <StatusBadge label="limits coherent" variant="success" />
        )
      }
    >
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MetricTile label="risk / trade" value={`${fmt(limits.risk_per_trade_pct)}%`} detail="intended, at stop" />
        <MetricTile
          label="effective risk"
          value={`${fmt(coherence.effective_risk_pct, 3)}%`}
          detail={`binding cap: ${coherence.binding_cap ?? "—"}`}
          color={incoherent ? "text-accent-amber" : undefined}
        />
        <MetricTile label="premium cap" value={`${fmt(limits.max_premium_per_trade_pct)}%`} detail="gap-to-zero exposure" />
        <MetricTile label="portfolio heat" value={`${fmt(limits.max_portfolio_heat_pct)}%`}
                    detail={`max ${limits.max_concurrent_positions ?? "?"} positions`} />
        <MetricTile label="daily stop" value={`${fmt(limits.daily_loss_stop_pct)}%`}
                    detail={`${fmt(coherence.stopouts_to_daily_standdown, 1)} stop-outs away`}
                    color={coherence.daily_standdown_reachable_in_one_session ? undefined : "text-accent-amber"} />
        <MetricTile label="weekly stop" value={`${fmt(limits.weekly_loss_stop_pct)}%`} detail="stand-down + review" />
        <MetricTile label="M6 stop" value={`${fmt((num(limits.stop_pct) ?? 0) * 100, 0)}%`} detail="of premium" />
        <MetricTile
          label="current heat"
          value={data?.portfolio_heat_pct == null ? "—" : `${fmt(data.portfolio_heat_pct)}%`}
          detail={`${data?.open_positions?.length ?? 0} open`}
        />
      </div>

      {incoherent && (
        <div className="mt-3 rounded-xl border border-accent-amber/35 bg-accent-amber/8 px-3 py-2">
          <p className="text-[11px] leading-relaxed text-text-secondary">{coherence.explanation}</p>
          <p className="mt-1.5 text-[11px] leading-relaxed text-text-muted">
            Three coherent resolutions exist and picking one is an owner decision, not a refactor:
            raise the premium cap to {fmt(coherence.premium_needed_for_intended_risk_pct)}%;
            restate risk-per-trade as the achievable {fmt(coherence.effective_risk_pct, 3)}% and scale
            the loss stops to match; or widen the stop until the two agree.
          </p>
        </div>
      )}

      {!!data?.open_positions?.length && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead className="text-[10px] uppercase tracking-[0.14em] text-text-muted">
              <tr>
                <th className="py-1.5 pr-3">symbol</th>
                <th className="py-1.5 pr-3">instrument</th>
                <th className="py-1.5 pr-3">lots</th>
                <th className="py-1.5 pr-3">risk @ stop</th>
                <th className="py-1.5 pr-3">premium</th>
                <th className="py-1.5">basis</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {data.open_positions.map((p: any, i: number) => (
                <tr key={i} className="border-t border-bg-border/50">
                  <td className="py-1.5 pr-3 text-text-primary">{p.symbol}</td>
                  <td className="py-1.5 pr-3 text-text-secondary">{p.instrument}</td>
                  <td className="py-1.5 pr-3 text-text-secondary">{p.sizing_lots}</td>
                  <td className="py-1.5 pr-3 text-text-secondary">{formatMoney(num(p.sizing_risk_rupees))}</td>
                  <td className="py-1.5 pr-3 text-text-muted">
                    {p.sizing_premium_rupees ? formatMoney(num(p.sizing_premium_rupees)) : "—"}
                  </td>
                  <td className="py-1.5">
                    <span
                      className={p.sizing_risk_basis === "full_premium" ? "text-accent-amber" : "text-text-muted"}
                      title={
                        p.sizing_risk_basis === "full_premium"
                          ? "Sized before migration 006 — its risk column holds the FULL PREMIUM, a different quantity from every row after it."
                          : "risk at stop"
                      }
                    >
                      {p.sizing_risk_basis ?? "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
