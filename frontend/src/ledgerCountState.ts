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
  return {
    orders: savedOrders.length, booked: filled.length, rejected: rejected.length,
    other: savedOrders.length-filled.length-rejected.length, trades: fills.length,
    buys: fills.filter((r) => r.side === "BUY").length, sells: fills.filter((r) => r.side === "SELL").length,
    unlinked: fills.filter((r) => !r.order_id || !byOrder.has(r.order_id)).length,
    notBooked: fills.filter((r) => r.order_id && byOrder.has(r.order_id) && byOrder.get(r.order_id)!.status.toUpperCase() !== "FILLED").length,
    withoutFill: filled.filter((r) => !linkedIds.has(r.order_id)).length,
  };
}
