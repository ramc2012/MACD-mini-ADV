"use client";

/**
 * Canonical trade book — open + closed paper positions for any desk.
 * Rich columns (regime, confidence, entry→exit, R-multiple, exit reason,
 * hold time) + an inline P&L magnitude bar + a derived stats footer.
 */
import { useMemo, useState } from "react";
import { BookOpen, Layers } from "lucide-react";

import {
  Section,
  StatusBadge,
  formatNumber,
  formatPct,
  formatIST,
  formatSignedMoney,
  regimeTone,
  tone,
} from "@/components/desk-ui";
import {
  type PaperPosition,
  deriveTradeStats,
  directionOf,
  holdHours,
  pnlOf,
  rOf,
  symbolOf,
  unrealizedOf,
} from "@/lib/strategy-stats";
import { CHART } from "./chartTheme";
import { LastUpdated, newestTimestamp } from "@/components/common/LastUpdated";
import { LiveMarkCell } from "@/components/terminal/LiveMarkCell";
import { legTapeSymbol } from "@/lib/marketSymbols";

function dirBadge(dir: string) {
  if (dir === "CE" || dir === "LONG" || dir === "BUY")
    return <StatusBadge label={dir} variant="success" />;
  if (dir === "PE" || dir === "SHORT" || dir === "SELL")
    return <StatusBadge label={dir} variant="error" />;
  return <StatusBadge label={dir || "—"} variant="neutral" />;
}

function PnlBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (Math.abs(value) / max) * 100) : 0;
  return (
    <div className="flex items-center justify-end gap-2">
      <span className={`font-mono text-[11.5px] ${tone(value)}`}>{formatSignedMoney(value)}</span>
      <div className="relative h-1.5 w-14 overflow-hidden rounded-full bg-bg-primary/40">
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${pct}%`, background: value >= 0 ? CHART.green : CHART.red }}
        />
      </div>
    </div>
  );
}

function holdLabel(p: PaperPosition): string {
  const h = holdHours(p);
  if (h == null) return "—";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 24) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

const TH = "px-2.5 py-2 text-left text-[10px] uppercase tracking-[0.12em] text-text-muted font-semibold";
const TD = "px-2.5 py-2 text-[12px] text-text-secondary whitespace-nowrap";

export function TradeBook({
  open = [],
  closed = [],
  title = "Trade book",
  initialView = "closed",
  maxRows = 200,
}: {
  open?: PaperPosition[];
  closed?: PaperPosition[];
  title?: string;
  initialView?: "open" | "closed";
  maxRows?: number;
}) {
  const [view, setView] = useState<"open" | "closed">(initialView);
  const stats = useMemo(() => deriveTradeStats(closed), [closed]);
  const maxAbsPnl = useMemo(
    () => Math.max(1, ...closed.map((p) => Math.abs(pnlOf(p))), ...open.map((p) => Math.abs(unrealizedOf(p)))),
    [closed, open],
  );
  const sortedClosed = useMemo(
    () => [...closed].sort((a, b) => String(b.closed_at).localeCompare(String(a.closed_at))).slice(0, maxRows),
    [closed, maxRows],
  );

  const tabBtn = (key: "open" | "closed", label: string, count: number) => (
    <button
      type="button"
      onClick={() => setView(key)}
      className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11.5px] font-semibold transition-colors ${
        view === key
          ? "bg-accent-blue/15 text-text-primary"
          : "text-text-muted hover:text-text-secondary"
      }`}
    >
      {label}
      <span className="rounded-full bg-bg-primary/50 px-1.5 text-[10px] text-text-muted">{count}</span>
    </button>
  );

  return (
    <Section
      title={title}
      icon={<BookOpen size={16} />}
      rightSlot={
        <div className="flex flex-wrap items-center justify-end gap-2">
          {view === "open" ? (
            <LastUpdated timestamp={newestTimestamp(open.map((p) => p.mark_time ?? p.opened_at))} label="Marked" />
          ) : (
            <LastUpdated
              timestamp={newestTimestamp(closed.map((p) => p.closed_at ?? p.mark_time ?? p.opened_at))}
              label="Last close"
              staleAfterSeconds={24 * 3600}
              criticalAfterSeconds={5 * 24 * 3600}
            />
          )}
          <div className="flex items-center gap-1">
            {tabBtn("open", "Open", open.length)}
            {tabBtn("closed", "Closed", closed.length)}
          </div>
        </div>
      }
    >
      <div className="-mx-2 overflow-x-auto">
        {view === "open" ? (
          open.length === 0 ? (
            <Empty label="No open positions" />
          ) : (
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-bg-border/60">
                  <th className={TH}>Symbol</th>
                  <th className={TH}>Dir</th>
                  <th className={`${TH} text-right`}>Strike</th>
                  <th className={TH}>Regime</th>
                  <th className={`${TH} text-right`}>Conf</th>
                  <th className={`${TH} text-right`}>Entry → Mark</th>
                  <th className={`${TH} text-right`}>uPnL</th>
                  <th className={`${TH} text-right`}>Opened</th>
                </tr>
              </thead>
              <tbody>
                {open.map((p, i) => (
                  <tr key={p.position_id ?? i} className="border-b border-bg-border/25 hover:bg-bg-primary/20">
                    <td className={`${TD} font-medium text-text-primary`}>{symbolOf(p)}</td>
                    <td className={TD}>{dirBadge(directionOf(p))}</td>
                    <td className={`${TD} text-right font-mono`}>{p.strike ?? "—"}</td>
                    <td className={TD}>
                      {p.regime ? <StatusBadge label={p.regime} tone={regimeTone(p.regime)} /> : "—"}
                    </td>
                    <td className={`${TD} text-right font-mono`}>{formatNumber(p.confidence, 2)}</td>
                    <td className={`${TD} text-right font-mono`}>
                      {formatNumber(p.entry_premium, 2)} →{" "}
                      <LiveMarkCell
                        symbol={legTapeSymbol(p)}
                        fallback={p.latest_premium ?? p.exit_premium}
                        fallbackAt={p.updated_at ?? p.marked_at ?? p.opened_at}
                        decimals={2}
                      />
                    </td>
                    <td className={`${TD} text-right`}>
                      <PnlBar value={unrealizedOf(p)} max={maxAbsPnl} />
                    </td>
                    <td className={`${TD} text-right text-text-muted`}>{formatIST(p.opened_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : sortedClosed.length === 0 ? (
          <Empty label="No closed trades yet" />
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-bg-border/60">
                <th className={TH}>Symbol</th>
                <th className={TH}>Dir</th>
                <th className={TH}>Regime</th>
                <th className={`${TH} text-right`}>Conf</th>
                <th className={`${TH} text-right`}>Entry → Exit</th>
                <th className={`${TH} text-right`}>R</th>
                <th className={TH}>Exit</th>
                <th className={`${TH} text-right`}>Hold</th>
                <th className={`${TH} text-right`}>P&L</th>
                <th className={`${TH} text-right`}>Closed</th>
              </tr>
            </thead>
            <tbody>
              {sortedClosed.map((p, i) => {
                const r = rOf(p);
                return (
                  <tr key={p.position_id ?? i} className="border-b border-bg-border/25 hover:bg-bg-primary/20">
                    <td className={`${TD} font-medium text-text-primary`}>{symbolOf(p)}</td>
                    <td className={TD}>{dirBadge(directionOf(p))}</td>
                    <td className={TD}>
                      {p.regime ? <StatusBadge label={p.regime} tone={regimeTone(p.regime)} /> : "—"}
                    </td>
                    <td className={`${TD} text-right font-mono`}>{formatNumber(p.confidence, 2)}</td>
                    <td className={`${TD} text-right font-mono`}>
                      {formatNumber(p.entry_premium, 1)} → {formatNumber(p.exit_premium, 1)}
                    </td>
                    <td className={`${TD} text-right font-mono ${tone(r)}`}>
                      {r != null ? `${r > 0 ? "+" : ""}${r.toFixed(2)}` : "—"}
                    </td>
                    <td className={`${TD} text-text-muted`}>{p.close_reason ?? "—"}</td>
                    <td className={`${TD} text-right text-text-muted`}>{holdLabel(p)}</td>
                    <td className={`${TD} text-right`}>
                      <PnlBar value={pnlOf(p)} max={maxAbsPnl} />
                    </td>
                    <td className={`${TD} text-right text-text-muted`}>{formatIST(p.closed_at)}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="border-t border-bg-border/60 text-[11.5px]">
                <td className={`${TD} font-semibold text-text-primary`} colSpan={4}>
                  {stats.count} trades · {formatPct(stats.winRate)} win
                </td>
                <td className={`${TD} text-right text-text-muted`} colSpan={2}>
                  PF {stats.profitFactor === Infinity ? "∞" : formatNumber(stats.profitFactor, 2)}
                </td>
                <td className={`${TD} text-text-muted`} colSpan={2}>
                  Exp {formatSignedMoney(stats.expectancy)}
                </td>
                <td className={`${TD} text-right font-mono ${tone(stats.net)}`} colSpan={2}>
                  {formatSignedMoney(stats.net)}
                </td>
              </tr>
            </tfoot>
          </table>
        )}
      </div>
    </Section>
  );
}

function Empty({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-2 rounded-xl border border-dashed border-bg-border/60 px-4 py-8 text-sm text-text-muted">
      <Layers size={15} />
      {label}
    </div>
  );
}
