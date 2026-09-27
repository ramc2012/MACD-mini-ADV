import { useEffect, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import type { AuctionContext } from "./types";

const IST_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" });
// Profiles rebuild nightly; a page left open sees the new prior day within
// this interval of the rebuild, and immediately on the IST day change.
const REFRESH_MS = 30 * 60_000;

/** Prior-day / week / naked-POC references for one spot symbol.
 *
 * Undefined while loading, on error, and when no symbol is given — App
 * passes none for an option, whose premium is a different price domain
 * from the profile levels of its underlying.
 */
export function useChartReferences(symbol: string | undefined) {
  const [references, setReferences] = useState<AuctionContext>();
  const [day, setDay] = useState(() => IST_DAY.format(new Date()));
  useEffect(() => {
    const timer = window.setInterval(() => setDay(IST_DAY.format(new Date())), 60_000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    setReferences(undefined);
    if (!symbol) return;
    const controller = new AbortController();
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const load = () => void fetch(`${API_URL}/api/auction/context/${encodeURIComponent(symbol)}`, { headers, signal: controller.signal })
      .then((response) => (response.ok ? (response.json() as Promise<AuctionContext>) : undefined))
      .then((data) => { if (data && data.symbol === symbol) setReferences(data); })
      .catch(() => undefined);
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => { controller.abort(); clearInterval(timer); };
  }, [symbol, day]);
  return references;
}
