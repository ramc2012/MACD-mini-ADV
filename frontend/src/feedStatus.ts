/**
 * Broker events describe state changes, but an old "stale" event is not
 * replayed as "connected" when ticks resume. The polled health response is
 * the current observation; use it only while it is fresh, and preserve
 * explicit errors and token-expiry states from the broker event.
 */
export function observedBrokerStatus(
  reported: string | undefined,
  health: { status: string; feed_alive?: boolean } | undefined,
  healthUpdated: string | undefined,
  now = Date.now(),
): string {
  if (reported !== "connected" && reported !== "stale") return reported || "unknown";
  const observedAt = healthUpdated ? Date.parse(healthUpdated) : NaN;
  if (!health || !Number.isFinite(observedAt) || now < observedAt || now - observedAt > 25_000) return reported;
  if (health.status !== "connected") return health.status;
  if (health.feed_alive === false) return "stale";
  if (health.feed_alive === true) return "connected";
  return reported;
}
