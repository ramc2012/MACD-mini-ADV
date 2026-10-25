"use client";

import { Activity, ShieldCheck } from "lucide-react";

import { MetricTile, Section, StatusBadge, formatSignedMoney, tone } from "@/components/desk-ui";

type Gate = {
  model_version?: string;
  gate_passed?: boolean;
  status?: string;
  minimum_source_sessions?: number;
  reasons?: string[];
  policy?: string;
};

function number(value: unknown): number | null {
  const parsed = typeof value === "string" ? Number(value) : value;
  return typeof parsed === "number" && Number.isFinite(parsed) ? parsed : null;
}

function pct(value: unknown, digits = 1): string {
  const parsed = number(value);
  return parsed == null ? "—" : `${(parsed * 100).toFixed(digits)}%`;
}

export default function VanguardPerformance({
  summary,
  model,
  watchlist,
  attribution,
  followup,
}: {
  summary?: any;
  model?: any;
  watchlist?: any;
  attribution?: any;
  followup?: any;
}) {
  const book = summary?.book ?? {};
  const latest = watchlist?.latest_evaluated ?? watchlist?.latest_completed ?? {};
  const attr = attribution?.latest ?? {};
  const gate: Gate | undefined = followup?.promotion_gates?.[0];
  const h1 = followup?.quality?.find((row: any) => row.horizon === 1);
  const h2 = followup?.quality?.find((row: any) => row.horizon === 2);
  const paperPnl = number(book.realized_pnl);
  const paperTrades = number(book.closed_positions) ?? 0;
  const prospectiveReturn = number(latest.avg_return_pct);
  const perSymbol = Object.entries(attr?.report?.per_symbol_attribution ?? {}) as Array<[string, any]>;

  return (
    <div className="space-y-4">
      <Section title={`Session · ${summary?.capital?.dt || "loading"}`} description="Paper realized P&L and shadow option returns measure separate books.">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <MetricTile label="Paper realized this session" value={summary?.capital ? formatSignedMoney(number(summary.capital.realized_pnl)) : "—"} detail="Daily paper capital ledger" />
          <MetricTile label="Shadow outcome session" value={latest.track_session || "—"} detail={`Frozen on ${latest.source_session || "—"}`} />
          <MetricTile label="Closing coverage" value={watchlist ? `${latest.resolved ?? 0}/${latest.item_count ?? 0}` : "—"} detail="Exact scheduled closing candle" />
        </div>
        {watchlist && (latest.resolved ?? 0) < (latest.item_count ?? 0) && <p className="mt-3 text-xs text-amber-200">Incomplete closing coverage. Missing outcomes are excluded, never zero-filled. Late exact-session candles are reconciled automatically; an older successful session cannot stand in for this session.</p>}
      </Section>
      <Section
        title="Lane performance"
        icon={<Activity size={16} />}
        description="Paper tickets, frozen shadow rankings, and longitudinal observations are different evidence populations. They remain separate below."
        rightSlot={<StatusBadge label={gate?.status?.replaceAll("_", " ") || "loading evidence"} variant={gate?.gate_passed ? "success" : "warn"} />}
      >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
          <MetricTile label="Paper realized" value={paperPnl == null ? "—" : formatSignedMoney(paperPnl)} detail={`${paperTrades} closed · ${pct(book.hit_rate)} hit`} color={tone(paperPnl)} />
          <MetricTile label="Paper expectancy" value={number(book.avg_r) == null ? "—" : `${number(book.avg_r)! >= 0 ? "+" : ""}${number(book.avg_r)!.toFixed(3)}R`} detail="M1–M10 tickets" color={tone(number(book.avg_r))} />
          <MetricTile label="Latest shadow list" value={prospectiveReturn == null ? "—" : pct(prospectiveReturn)} detail={`${latest.winners ?? 0}/${latest.resolved ?? 0} resolved winners`} color={tone(prospectiveReturn)} />
          <MetricTile label="Follow-through +1" value={pct(h1?.mean_return)} detail={`${h1?.n ?? 0} observations · ${h1?.sessions ?? 0} days`} color={tone(number(h1?.mean_return))} />
          <MetricTile label="Follow-through +2" value={pct(h2?.mean_return)} detail={`${h2?.n ?? 0} observations · ${h2?.sessions ?? 0} days`} color={tone(number(h2?.mean_return))} />
          <MetricTile label="Conviction order" value={attr?.report?.conviction_decile_monotonic === true ? "monotonic" : "not monotonic"} detail={`${attr?.n_tickets_closed ?? 0} closed tickets`} color={attr?.report?.conviction_decile_monotonic === true ? "text-accent-green" : "text-accent-amber"} />
        </div>
      </Section>

      <Section
        title="Prospective promotion gate"
        icon={<ShieldCheck size={16} />}
        description="Historical gates can register a frozen shadow model. Promotion still requires untouched prospective evidence on its primary +1/+2-session horizons."
      >
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge label={`Neural model: ${model?.model?.status || "unknown"}`} variant={model?.model?.status === "shadow" ? "warn" : "neutral"} />
          <StatusBadge label={gate?.gate_passed ? "prospective gate passed" : "prospective gate not passed"} variant={gate?.gate_passed ? "success" : "error"} />
          <span className="text-xs text-text-muted">Minimum {gate?.minimum_source_sessions ?? 20} independent source sessions per primary horizon.</span>
        </div>
        <p className="mt-2 break-all text-xs text-text-muted">Follow-through gate cohort: {gate?.model_version || "unavailable"}. These +1/+2-session observations are separate from the next-session neural list.</p>
        <ul className="mt-3 grid gap-2 text-xs text-text-secondary md:grid-cols-2">
          {(gate?.reasons ?? ["Prospective gate is not available yet."]).map((reason) => (
            <li key={reason} className="rounded-lg border border-bg-border bg-bg-primary/20 px-3 py-2">{reason}</li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-amber-200">{gate?.policy || "No automatic promotion. Keep the model shadow-only until the frozen prospective gate is available and passes."}</p>
      </Section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Paper P&L concentration" description={`A few names can dominate a ${paperTrades}-trade sample.`}>
          <div className="space-y-2">
            {perSymbol.sort((a, b) => number(a[1]?.total_pnl_rupees)! - number(b[1]?.total_pnl_rupees)!).map(([symbol, row]) => (
              <div key={symbol} className="flex items-center justify-between rounded-lg border border-bg-border/60 px-3 py-2 text-xs">
                <span>{symbol} <span className="text-text-muted">· {row.n} trades</span></span>
                <span className={`font-mono ${tone(number(row.total_pnl_rupees))}`}>{formatSignedMoney(number(row.total_pnl_rupees))}</span>
              </div>
            ))}
          </div>
        </Section>
        <Section title="Evidence interpretation" description="What can safely change now.">
          <div className="space-y-2 text-xs text-text-secondary">
            <p className="rounded-lg border border-accent-green/20 bg-accent-green/5 p-3">Paper realized P&amp;L is {paperPnl == null ? "unavailable" : formatSignedMoney(paperPnl)} across {paperTrades} closed trades. Conviction ordering is {attr?.report?.conviction_decile_monotonic === true ? "monotonic" : "not established as monotonic"}. This small sample does not justify increasing size.</p>
            <p className="rounded-lg border border-bg-border p-3">The latest evaluated shadow list averaged {pct(prospectiveReturn)} across resolved names. This belongs to the shadow-ranking population, not the paper-ticket population; unresolved names are excluded.</p>
            <p className="rounded-lg border border-accent-blue/20 bg-accent-blue/5 p-3">The lane improvement is an explicit, machine-readable prospective gate. It turns “still shadow” into a reasoned evidence state without retraining or changing the frozen list.</p>
          </div>
        </Section>
      </div>
    </div>
  );
}
