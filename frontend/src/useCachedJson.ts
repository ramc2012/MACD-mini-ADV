import { useCallback, useEffect, useRef, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";

// Stale-while-revalidate for page data (owner, 18 Nov 2026): returning to a page shows the last
// answer at once and refreshes behind it, instead of a blank "Loading…" on every visit.
// The cache lives for the browser tab only (module memory), keyed by the request path.

type Entry = { data: unknown; at: number };
const cache = new Map<string, Entry>();
const MAX_ENTRIES = 80;

export function cachedValue<T>(path: string): T | undefined {
  return cache.get(path)?.data as T | undefined;
}

export function remember(path: string, data: unknown): void {
  cache.delete(path);
  cache.set(path, { data, at: Date.now() });
  while (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value as string);
}

/** GET `path` (relative to the API) with the last answer shown at once; `everyMs` re-polls while mounted. */
export function useCachedJson<T>(path: string | null, everyMs = 0): { data: T | undefined; error: string; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | undefined>(() => (path ? cachedValue<T>(path) : undefined));
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const current = useRef(path);
  current.current = path;
  useEffect(() => {
    if (!path) { setData(undefined); return; }
    setData(cachedValue<T>(path));
    setError("");
    const controller = new AbortController();
    let timer = 0;
    const load = async () => {
      setLoading(true);
      try {
        const response = await fetch(`${API_URL}${path}`, { headers: API_TOKEN ? { "x-macd-token": API_TOKEN } : {}, signal: controller.signal });
        if (!response.ok) {
          let detail = "";
          try { detail = (await response.json()).detail || ""; } catch { /* not JSON */ }
          throw new Error(`${path.split("?")[0]} failed (HTTP ${response.status})${detail ? `: ${detail}` : ""}`);
        }
        const body = (await response.json()) as T;
        remember(path, body);
        if (current.current === path) { setData(body); setError(""); }
      } catch (exc) {
        if (controller.signal.aborted) return;
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
      if (everyMs > 0 && !controller.signal.aborted) timer = window.setTimeout(() => void load(), everyMs);
    };
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [path, everyMs, nonce]);
  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}
