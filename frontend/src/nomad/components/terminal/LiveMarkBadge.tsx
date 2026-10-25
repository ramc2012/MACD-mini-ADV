"use client";

/**
 * Honesty layer for the terminal: shows whether the live tape is connected and,
 * per-symbol, how fresh the last value is — so a coalesced/stale frame is never
 * silently presented as a raw real-time print. green=live · amber=stale · red=offline.
 *
 * Transport-connected is NOT the same as "live tape". A websocket can be up
 * while the market session is closed, the global feed is offline, or every
 * quote cell is empty. These badges separate FOUR facts — market session (pure
 * clock), feed health (global), transport (socket) and per-quote freshness —
 * and only render green when the session is open, the feed is online AND a
 * fresh quote exists. A replay/offline/empty/closed state never reads LIVE.
 */
import { useEffect, useState } from "react";

import { StatusBadge } from "@/components/desk-ui";
import { useQuote, useQuotesConnection } from "@/hooks/useQuoteStore";
import { useSystemState } from "@/hooks/useSystemState";
import { marketSessions } from "@/lib/market-hours";

/** MCX commodity roots — used to pick the right session window for a symbol. */
const MCX_ROOTS = [
  "CRUDEOIL", "NATURALGAS", "GOLD", "GOLDM", "SILVER", "SILVERM", "COPPER",
  "ZINC", "ALUMINIUM", "LEAD", "NICKEL", "MENTHAOIL", "COTTON",
];

/** True when a symbol trades on MCX (evening session) rather than NSE. */
function isMcxSymbol(symbol: string): boolean {
  const upper = String(symbol || "").toUpperCase();
  if (upper.includes("MCX")) return true;
  return MCX_ROOTS.some((root) => upper.includes(root));
}

/** Whether the relevant exchange session is open right now for `symbol`. */
function sessionOpenFor(symbol: string): boolean {
  const s = marketSessions();
  return isMcxSymbol(symbol) ? s.mcxOpen : s.nseOpen;
}

/**
 * Global connection state of the shared /ws/quotes socket, cross-checked
 * against the market session and the global feed health. "● live tape" only
 * shows when the session is open, the feed is online AND the socket is up —
 * transport-connected alone never claims a live tape.
 */
export function QuoteConnectionBadge() {
  const connected = useQuotesConnection();
  const { feedOnline, nseOpen, mcxOpen } = useSystemState();
  const sessionOpen = nseOpen || mcxOpen;

  if (!sessionOpen) return <StatusBadge label="market closed" variant="neutral" />;
  if (!feedOnline) return <StatusBadge label="feed offline" variant="warn" />;
  if (!connected) return <StatusBadge label="tape offline" variant="warn" />;
  return <StatusBadge label="● live tape" variant="success" />;
}

/**
 * Per-symbol freshness. Precedence (honest states first): market-session closed
 * → neutral; transport down → offline; no quote → em-dash; otherwise the age
 * verdict (stale after `staleMs`). Uses only the pure IST clock + the shared
 * quote store, so it stays cheap even when rendered per cell.
 */
export function LiveMarkBadge({ symbol, staleMs = 4000 }: { symbol: string; staleMs?: number }) {
  const quote = useQuote(symbol);
  const connected = useQuotesConnection();
  // Re-tick once a second so the age verdict updates even without new ticks.
  const [, force] = useState(0);
  useEffect(() => {
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  if (!sessionOpenFor(symbol)) return <StatusBadge label="closed" variant="neutral" />;
  if (!connected) return <StatusBadge label="offline" variant="warn" />;
  if (!quote || quote.rxAt == null) return <StatusBadge label="—" variant="info" />;

  const ageS = Math.max(0, (Date.now() - quote.rxAt) / 1000);
  const stale = ageS * 1000 > staleMs;
  const label = `${stale ? "stale " : ""}${ageS < 1 ? "<1" : Math.round(ageS)}s${quote.coalesced ? " ·c" : ""}`;
  return <StatusBadge label={label} variant={stale ? "warn" : "success"} />;
}
