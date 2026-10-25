export type BrokerStatusEntry = {
  broker: string;
  connected: boolean;
  ready?: boolean;
  session_active?: boolean;
  state?: string | null;
  detail?: string | null;
  source?: string | null;
  checked_at?: string | null;
  needs_reconnect?: boolean;
  user_id?: string | null;
  name?: string | null;
  connected_at?: string | null;
};

/**
 * Broker `state` values that are explicitly NOT a live trading session, even
 * though a token is saved. Upstox's `valid_analytics_token` is a read-only
 * historical/backfill token — treating it as "ready" would falsely light the
 * broker chip green while 0 brokers can actually place an order.
 */
const NON_TRADING_STATES = new Set(["valid_analytics_token"]);

export function isBrokerReady(status?: Partial<BrokerStatusEntry> | null): boolean {
  if (status?.state && NON_TRADING_STATES.has(status.state)) return false;
  return Boolean(status?.ready ?? status?.connected);
}

export function hasBrokerSession(status?: Partial<BrokerStatusEntry> | null): boolean {
  return Boolean(status?.session_active ?? status?.connected ?? status?.ready);
}
