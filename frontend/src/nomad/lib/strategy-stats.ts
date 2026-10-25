/**
 * Shared trade-book / performance derivations.
 *
 * Every strategy desk exposes the same paper endpoints:
 *   /api/<lane>/paper-summary    → PaperSummary
 *   /api/<lane>/paper-positions  → { open_positions, closed_positions, summary }
 *
 * Rather than require a bespoke equity-history endpoint per lane, we derive
 * the equity curve, monthly P&L, R-distribution and trade statistics directly
 * from the closed-position list. That gives every desk a full performance
 * surface (curve + stats + trade book) for free, from one canonical shape.
 */

export type PaperPosition = {
  position_id?: string;
  trading_symbol?: string;
  underlying?: string;
  direction?: string | null;
  option_type?: string | null;
  strike?: number | null;
  expiry?: string | null;
  expiry_kind?: string | null;
  regime?: string | null;
  confidence?: number | null;
  status?: string;
  opened_at?: string | null;
  closed_at?: string | null;
  entry_spot?: number | null;
  exit_spot?: number | null;
  entry_premium?: number | null;
  exit_premium?: number | null;
  latest_premium?: number | null;
  quantity_lots?: number | null;
  quantity_units?: number | null;
  realized_pnl?: number | null;
  unrealized_pnl?: number | null;
  transaction_cost?: number | null;
  close_reason?: string | null;
  selection_reason?: string | null;
  policy_r_multiple?: number | null;
  mark_time?: string | null;
  side?: string | null;
  qty?: number | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [k: string]: any;
};

export type PaperSummary = {
  open_positions?: number;
  closed_positions?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  total_pnl?: number;
  initial_capital?: number;
  available_capital?: number;
  reserved_margin?: number;
  total_equity?: number;
  total_return_pct?: number;
  max_drawdown?: number;
  sharpe_ratio?: number;
  total_trades?: number;
  win_rate?: number;
};

export type PositionsPayload = {
  open_positions?: PaperPosition[];
  closed_positions?: PaperPosition[];
  summary?: PaperSummary;
};

/** Realized P&L of a position, tolerant of differing field names across lanes. */
export function pnlOf(p: PaperPosition): number {
  const v = p.realized_pnl ?? p.pnl ?? p.realised_pnl ?? p.net_pnl;
  return Number(v ?? 0);
}

export function rOf(p: PaperPosition): number | null {
  const v = p.policy_r_multiple ?? p.r_multiple ?? p.r;
  return v == null ? null : Number(v);
}

/** Open-position mark-to-market P&L. */
export function unrealizedOf(p: PaperPosition): number {
  return Number(p.unrealized_pnl ?? p.mtm_pnl ?? 0);
}

export function directionOf(p: PaperPosition): string {
  return String(p.direction ?? p.option_type ?? p.side ?? "").toUpperCase();
}

export function symbolOf(p: PaperPosition): string {
  return String(p.trading_symbol ?? p.symbol ?? p.underlying ?? "—");
}

export type TradeStats = {
  count: number;
  wins: number;
  losses: number;
  winRate: number;
  grossWin: number;
  grossLoss: number;
  profitFactor: number;
  avgWin: number;
  avgLoss: number;
  expectancy: number;
  net: number;
  bestPnl: number;
  worstPnl: number;
  avgR: number | null;
  avgHoldHours: number | null;
};

export function deriveTradeStats(closed: PaperPosition[]): TradeStats {
  const n = closed.length;
  const wins = closed.filter((p) => pnlOf(p) > 0);
  const losses = closed.filter((p) => pnlOf(p) < 0);
  const grossWin = wins.reduce((s, p) => s + pnlOf(p), 0);
  const grossLoss = Math.abs(losses.reduce((s, p) => s + pnlOf(p), 0));
  const net = closed.reduce((s, p) => s + pnlOf(p), 0);
  const rVals = closed.map(rOf).filter((r): r is number => r != null);
  const holds = closed
    .map((p) => holdHours(p))
    .filter((h): h is number => h != null);
  return {
    count: n,
    wins: wins.length,
    losses: losses.length,
    winRate: n ? wins.length / n : 0,
    grossWin,
    grossLoss,
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
    avgWin: wins.length ? grossWin / wins.length : 0,
    avgLoss: losses.length ? -grossLoss / losses.length : 0,
    expectancy: n ? net / n : 0,
    net,
    bestPnl: n ? Math.max(...closed.map(pnlOf)) : 0,
    worstPnl: n ? Math.min(...closed.map(pnlOf)) : 0,
    avgR: rVals.length ? rVals.reduce((s, r) => s + r, 0) / rVals.length : null,
    avgHoldHours: holds.length ? holds.reduce((s, h) => s + h, 0) / holds.length : null,
  };
}

export function holdHours(p: PaperPosition): number | null {
  if (!p.opened_at || !p.closed_at) return null;
  const a = Date.parse(p.opened_at);
  const b = Date.parse(p.closed_at);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return (b - a) / 3_600_000;
}

export type EquityPoint = {
  i: number;
  t: string;
  equity: number;
  cumPnl: number;
  pnl: number;
  drawdown: number;
  symbol: string;
};

/** Cumulative-equity series from closed trades, sorted by close time. */
export function deriveEquitySeries(
  closed: PaperPosition[],
  initialCapital = 0,
): EquityPoint[] {
  const sorted = [...closed]
    .filter((p) => p.closed_at)
    .sort((a, b) => (a.closed_at! < b.closed_at! ? -1 : 1));
  let cum = initialCapital;
  let peak = initialCapital;
  return sorted.map((p, i) => {
    const pnl = pnlOf(p);
    cum += pnl;
    peak = Math.max(peak, cum);
    return {
      i,
      t: p.closed_at!,
      equity: cum,
      cumPnl: cum - initialCapital,
      pnl,
      drawdown: cum - peak,
      symbol: symbolOf(p),
    };
  });
}

export type MonthlyPoint = {
  month: string;
  label: string;
  pnl: number;
  trades: number;
  wins: number;
  winRate: number;
};

export function deriveMonthly(closed: PaperPosition[]): MonthlyPoint[] {
  const m = new Map<string, MonthlyPoint>();
  for (const p of closed) {
    if (!p.closed_at) continue;
    const key = p.closed_at.slice(0, 7); // YYYY-MM
    const e =
      m.get(key) ||
      { month: key, label: monthLabel(key), pnl: 0, trades: 0, wins: 0, winRate: 0 };
    e.pnl += pnlOf(p);
    e.trades += 1;
    if (pnlOf(p) > 0) e.wins += 1;
    m.set(key, e);
  }
  return Array.from(m.values())
    .map((e) => ({ ...e, winRate: e.trades ? e.wins / e.trades : 0 }))
    .sort((a, b) => (a.month < b.month ? -1 : 1));
}

function monthLabel(yyyymm: string): string {
  const [y, mo] = yyyymm.split("-");
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const idx = Number(mo) - 1;
  return `${names[idx] ?? mo} ${String(y).slice(2)}`;
}

/** Histogram of R-multiples for a distribution chart. */
export function deriveRHistogram(closed: PaperPosition[], bin = 0.5) {
  const rs = closed.map(rOf).filter((r): r is number => r != null);
  if (!rs.length) return [];
  const buckets = new Map<number, number>();
  for (const r of rs) {
    const b = Math.round(r / bin) * bin;
    buckets.set(b, (buckets.get(b) ?? 0) + 1);
  }
  return Array.from(buckets.entries())
    .map(([r, count]) => ({ r, label: `${r > 0 ? "+" : ""}${r.toFixed(1)}R`, count }))
    .sort((a, b) => a.r - b.r);
}
