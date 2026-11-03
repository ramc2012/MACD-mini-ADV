import type { Order, Trade } from "./types";

const unique = <T,>(rows: T[], key: (row: T) => string): T[] => [...new Map(rows.map((row) => [key(row), row])).values()];

/** Counts of stored attempts and fills, with order linkage rather than assuming one fill per order. */
export function ledgerCounts(orders: Order[], trades: Trade[]) {
  const savedOrders = unique(orders, (r) => r.order_id);
  const fills = unique(trades, (r) => r.trade_id);
  const byOrder = new Map(savedOrders.map((r) => [r.order_id, r]));
  const filled = savedOrders.filter((r) => r.status.toUpperCase() === "FILLED");
  const rejected = savedOrders.filter((r) => r.status.toUpperCase() === "REJECTED");
  const linkedIds = new Set(fills.map((r) => r.order_id).filter(Boolean));
  const rejectedAttempts = attempts(rejected);
  return {
    orders: savedOrders.length - rejected.length + rejectedAttempts, booked: filled.length, rejected: rejectedAttempts,
    other: savedOrders.length-filled.length-rejected.length, trades: fills.length,
    buys: fills.filter((r) => r.side === "BUY").length, sells: fills.filter((r) => r.side === "SELL").length,
    unlinked: fills.filter((r) => !r.order_id || !byOrder.has(r.order_id)).length,
    notBooked: fills.filter((r) => r.order_id && byOrder.has(r.order_id) && byOrder.get(r.order_id)!.status.toUpperCase() !== "FILLED").length,
    withoutFill: filled.filter((r) => !linkedIds.has(r.order_id)).length,
  };
}

/** Order attempts a row stands for: a folded run of rejects carries repeat_count. */
export function attempts(orders: Order[]) {
  return orders.reduce((sum, r) => sum + Math.max(1, Number(r.repeat_count) || 1), 0);
}

export type RejectionGroup = { reason: string; attempts: number; symbols: string[]; last: string };
/** Rejected attempts grouped by reason, most first. Orders from before 4 Nov 2026 carry no reason. */
export function rejectionsByReason(orders: Order[]): RejectionGroup[] {
  const groups = new Map<string, RejectionGroup>();
  for (const r of orders) {
    if (r.status.toUpperCase() !== "REJECTED") continue;
    const reasons = r.reject_reasons?.length ? r.reject_reasons : r.reject_reason ? [r.reject_reason] : [];
    const reason = reasons.length ? reasons.map((x) => x.replace(/\b[A-Z]+:[A-Z0-9&-]+\b/g, "…")).join(" | ") : "reason not recorded (before 4 Nov 2026)";
    const g = groups.get(reason) ?? { reason, attempts: 0, symbols: [], last: "" };
    g.attempts += Math.max(1, Number(r.repeat_count) || 1);
    if (!g.symbols.includes(r.symbol)) g.symbols.push(r.symbol);
    if (r.created_at > g.last) g.last = r.created_at;
    groups.set(reason, g);
  }
  return [...groups.values()].sort((a, b) => b.attempts - a.attempts);
}
