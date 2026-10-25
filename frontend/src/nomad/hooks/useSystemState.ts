"use client";

/**
 * useSystemState — the ONE truth strip source.
 *
 * The old TopBar "LIVE" badge collapsed five DISTINCT facts into one green
 * light, and it lit up merely because a status endpoint answered. This hook
 * keeps them separate, because a trader must never confuse them:
 *
 *   1. API up        — transport: the backend answered            (≠ session)
 *   2. NSE / MCX     — market session open/closed from the clock  (≠ feed)
 *   3. Feed          — data freshness: WS connected + fresh ticks  (≠ brokers)
 *   4. Brokers X/4   — execution readiness (trading sessions)      (≠ strategy)
 *   5. Auto-run      — strategy loop armed/paused                  (≠ execution)
 *   + Paper/Live mode, Kill switch
 *
 * Everything is derived from endpoints that already exist:
 *   /api/system/health        — services[], broker readiness meta, market_data feed
 *   /api/auth/broker-status   — per-broker trading session (isBrokerReady)
 *   /api/trading/mode         — paper vs live
 *   /api/trading/kill-switch  — auto-run loop + kill switch
 *
 * Market session comes from the pure IST clock (lib/market-hours), cross-checked
 * against health's next_market_open_ist — no backend dependency for open/closed.
 */
import { useQuery } from "@tanstack/react-query";

import { REFRESH_MS } from "@/components/desk-ui";
import { api } from "@/lib/api";
import { isBrokerReady, type BrokerStatusEntry } from "@/lib/broker-status";
import { marketSessions } from "@/lib/market-hours";
import { schedulerStateVariant } from "@/lib/market-semantics";

type ServiceStatus = "healthy" | "degraded" | "critical" | "idle" | string;

type HealthService = {
  key: string;
  label?: string;
  status?: ServiceStatus;
  detail?: string | null;
  meta?: Record<string, unknown> | null;
};

type HealthPayload = {
  generated_at?: string;
  summary?: { status?: string } | null;
  services?: HealthService[] | null;
};

type KillSwitch = {
  kill_switch_active?: boolean;
  auto_run_enabled?: boolean;
  loop_active?: boolean;
};

type ModePayload = {
  mode?: string;
  paper_trading?: boolean;
  paper_only?: boolean;
  live?: boolean;
};

/** One separated system fact, ready to render as a chip. */
export type SystemFlag = {
  label: string;
  variant: "success" | "warn" | "error" | "info" | "neutral";
  title?: string;
};

export type SystemState = {
  apiUp: boolean;
  nseOpen: boolean;
  mcxOpen: boolean;
  feedOnline: boolean;
  lastTickAgeSeconds: number | null;
  brokersReady: number;
  brokersTotal: number;
  autoRunArmed: boolean;
  killActive: boolean;
  isLive: boolean;
  modeKnown: boolean;
  nextMarketOpenIst: string | null;
  /** Convenience: the five (+mode/kill) chips, already styled. */
  flags: SystemFlag[];
};

function svc(health: HealthPayload | undefined, key: string): HealthService | undefined {
  return (health?.services ?? []).find((s) => s.key === key);
}

function num(v: unknown): number | null {
  // `Number(null)` is 0, so the naive form turns a MISSING tick age into a
  // 0-second-old tick and lights the feed green with no data behind it. Missing
  // must stay missing — the feed check below fails closed on null.
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function useSystemState(): SystemState {
  const health = useQuery({
    queryKey: ["system", "health-truthstrip"],
    queryFn: async () => (await api.get("/api/system/health")).data as HealthPayload,
    refetchInterval: REFRESH_MS.snapshot,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const brokers = useQuery({
    queryKey: ["system", "brokers-truthstrip"],
    queryFn: async () => (await api.get("/api/auth/broker-status")).data as BrokerStatusEntry[],
    refetchInterval: REFRESH_MS.snapshot,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const ks = useQuery({
    queryKey: ["system", "killswitch-truthstrip"],
    queryFn: async () => (await api.get("/api/trading/kill-switch")).data as KillSwitch,
    refetchInterval: REFRESH_MS.snapshot,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const mode = useQuery({
    queryKey: ["system", "mode-truthstrip"],
    queryFn: async () => (await api.get("/api/trading/mode")).data as ModePayload,
    refetchInterval: REFRESH_MS.summary,
    refetchOnWindowFocus: false,
    retry: false,
  });

  // 1. API up — transport only: backend answered AND reports healthy.
  const backend = svc(health.data, "backend");
  const apiUp =
    !health.isError &&
    health.isFetched &&
    (backend ? backend.status === "healthy" : true);

  // 2. Market session — pure IST clock (no backend). next_market_open_ist from
  //    health is surfaced for the tooltip (holiday authority lives server-side).
  const sessions = marketSessions();
  const brokerMeta = (svc(health.data, "brokers")?.meta ?? {}) as Record<string, unknown>;
  const nextMarketOpenIst =
    (brokerMeta.next_market_open_ist as string | undefined) ?? null;

  // 3. Feed — data freshness from the market-data router.
  const feedMeta = (svc(health.data, "market_data")?.meta ?? {}) as Record<string, unknown>;
  const wsConnected = Boolean(feedMeta.ws_connected);
  const lastTickAgeSeconds = num(feedMeta.last_tick_age_seconds);
  // Online only when the socket is up AND ticks are recent (< 90s). Missing age
  // with an up socket is treated as not-yet-fresh, never falsely online.
  const feedOnline =
    wsConnected && lastTickAgeSeconds != null && lastTickAgeSeconds < 90;

  // 4. Brokers — execution readiness (real trading sessions only).
  const list = Array.isArray(brokers.data) ? brokers.data : [];
  const brokersTotal = list.length;
  const brokersReady = list.filter((b) => isBrokerReady(b)).length;

  // 5. Auto-run — strategy loop.
  const autoRunArmed = !!ks.data?.auto_run_enabled && ks.data?.loop_active !== false;
  const killActive = !!ks.data?.kill_switch_active;

  // Mode.
  const modeKnown = mode.isFetched && !mode.isError;
  const isLive = mode.data?.mode
    ? mode.data.mode === "live"
    : mode.data?.live ?? mode.data?.paper_trading === false;

  const brokerTitle =
    list.length > 0
      ? list.map((b) => `${b.broker}: ${b.state || (isBrokerReady(b) ? "ready" : "—")}`).join("\n")
      : "No broker sessions";

  const flags: SystemFlag[] = [
    {
      label: apiUp ? "API up" : "API down",
      variant: apiUp ? "success" : "error",
      title: "Backend transport — does NOT mean the market is open or the feed is live.",
    },
    {
      label: sessions.nseOpen ? "NSE open" : "NSE closed",
      variant: sessions.nseOpen ? "success" : "neutral",
      title: nextMarketOpenIst
        ? `NSE 09:15–15:30 IST. Next open: ${nextMarketOpenIst}`
        : "NSE 09:15–15:30 IST",
    },
    {
      label: sessions.mcxOpen ? "MCX open" : "MCX closed",
      variant: sessions.mcxOpen ? "success" : "neutral",
      title: "MCX 09:00–23:30 IST",
    },
    {
      label: feedOnline ? "feed online" : "feed offline",
      variant: feedOnline ? "success" : "warn",
      title:
        lastTickAgeSeconds != null
          ? `WS ${wsConnected ? "connected" : "down"} · last tick ${Math.round(lastTickAgeSeconds)}s ago`
          : `WS ${wsConnected ? "connected" : "down"} · no ticks`,
    },
    {
      label: `${brokersReady}/${brokersTotal || 0} brokers`,
      variant: brokersReady > 0 ? "success" : brokersTotal ? "error" : "neutral",
      title: brokerTitle,
    },
    ...(modeKnown
      ? [
          {
            label: isLive ? "live" : "paper",
            variant: (isLive ? "warn" : "info") as SystemFlag["variant"],
            title: isLive ? "LIVE execution mode" : "Paper trading",
          },
        ]
      : []),
    {
      // ARMED IS NOT RUNNING and therefore NOT GREEN: the variant comes from
      // the ONE scheduler contract (lib/status-variants), so the truth strip,
      // the desk headers and the lane inventory cannot disagree about it.
      label: autoRunArmed ? "NSE auto-run armed" : "NSE auto-run paused",
      variant: autoRunArmed ? schedulerStateVariant("armed") : "warn",
      title: "NSE S1 strategy loop only. Other paper lanes have independent controls.",
    },
    ...(killActive
      ? [
          {
            label: "NSE kill armed",
            variant: "error" as SystemFlag["variant"],
            title: "NSE S1 kill switch is armed. MCX and research lanes are controlled separately.",
          },
        ]
      : []),
  ];

  return {
    apiUp,
    nseOpen: sessions.nseOpen,
    mcxOpen: sessions.mcxOpen,
    feedOnline,
    lastTickAgeSeconds,
    brokersReady,
    brokersTotal,
    autoRunArmed,
    killActive,
    isLive,
    modeKnown,
    nextMarketOpenIst,
    flags,
  };
}
