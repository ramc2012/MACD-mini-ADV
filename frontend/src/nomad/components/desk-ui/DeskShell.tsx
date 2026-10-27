"use client";

/**
 * Universal strategy-desk shell.
 *
 * Every /strategies/<name> route renders inside this so the trader sees
 * the same chrome (header, status chips, tab bar, data-freshness pill)
 * regardless of which desk they're looking at. The desk's actual content
 * (live overview, paper trading, etc.) goes into the `children` slot.
 *
 * v1 desks each rolled their own header / status row / tab bar with
 * subtly different padding, font sizes, badge styles, and tab keying.
 * One shell removes that divergence in one stroke.
 */
import { clsx } from "clsx";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { Activity } from "lucide-react";
import {InfoNote} from './InfoNote';

import { StatusBadge } from "./StatusBadge";
import { ExecutionModeBadge, SchedulerBadge } from "./SemanticBadges";
import { LastUpdated } from "@/components/common/LastUpdated";
import type { SchedulerState } from "@/lib/market-semantics";

export type DeskTab = {
  key: string;
  label: string;
  /** Any component that accepts a `size` prop — lucide-react icons fit. */
  icon?: React.ElementType;
};

export function DeskShell({
  title,
  description,
  asOf,
  asOfLabel,
  asOfStaleSeconds,
  asOfCriticalSeconds,
  isLive,
  schedulerState,
  isFetching,
  showStatus = true,
  paperMode,
  tabs,
  activeTab,
  onTabChange,
  v1Href,
  rightSlot,
  beforeTabs,
  children,
}: {
  title: string;
  description?: string;
  asOf?: string | Date | null;
  /** Freshness badge prefix — "Updated" (payload time, default) or "Fetched" (client fetch time). */
  asOfLabel?: string;
  /** Override the green→amber cutoff for lanes with a slower natural cadence (e.g. daily scans). */
  asOfStaleSeconds?: number;
  /** Override the amber→red cutoff. */
  asOfCriticalSeconds?: number;
  /**
   * DEPRECATED and deliberately misnamed-no-more: this has ALWAYS driven the
   * "armed" pill (scheduler state), never a data-liveness claim. Callers were
   * passing freshness booleans and backfill-append flags into it. Prefer
   * `schedulerState`; this remains only for callers that genuinely hold a
   * loop-armed boolean.
   */
  isLive?: boolean;
  /** Loop state from the shared contract. ARMED IS NOT LIVE. */
  schedulerState?: SchedulerState;
  isFetching?: boolean;
  /** A child chart may own its source timestamp and loading status. */
  showStatus?: boolean;
  paperMode?: boolean;
  tabs: DeskTab[];
  activeTab: string;
  onTabChange: (key: string) => void;
  /** v1 equivalent URL for quick-jump while v2 is being perfected. */
  v1Href?: string;
  rightSlot?: React.ReactNode;
  /** Optional content rendered between the header and the tab bar. */
  beforeTabs?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <header className="rounded-xl border border-bg-border bg-bg-secondary/22 px-4 py-2">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-text-primary">{title}</h1>
              {description ? <InfoNote title={`About ${title}`}>{description}</InfoNote> : null}
              {/* Paper is BLUE, live is amber. Green is reserved for
                  healthy-live / actionable-confirmed, and "we are not sending
                  real orders" is neither — it is a mode, not a health light. */}
              {paperMode != null ? (
                <ExecutionModeBadge mode={paperMode ? "paper" : "live"} />
              ) : null}
              {/* ARMED IS NOT RUNNING, so the armed pill is never green: both
                  branches go through the ONE scheduler-variant contract. */}
              {schedulerState ? (
                <SchedulerBadge state={schedulerState} />
              ) : isLive ? (
                <SchedulerBadge state="armed" />
              ) : null}
            </div>
            {showStatus && <div className="mt-2 flex flex-wrap items-center gap-3 text-[11.5px] text-text-muted">
              <span className="inline-flex items-center gap-1.5">
                <Activity size={12} className={isFetching ? "animate-pulse text-accent-blue" : undefined} />
                {isFetching ? "Refreshing" : "Idle"}
              </span>
              <LastUpdated
                timestamp={asOf ?? null}
                label={asOfLabel ?? "Updated"}
                staleAfterSeconds={asOfStaleSeconds}
                criticalAfterSeconds={asOfCriticalSeconds}
              />
            </div>}
          </div>
          <div className="flex items-center gap-2">
            {rightSlot}
            {/* v1 deprecated — the "v1 view" link is intentionally not rendered.
                The v1Href prop is kept for back-compat but no longer surfaces a
                link (v1 frontend is being retired; v2 is the primary UI). */}
          </div>
        </div>
      </header>

      {beforeTabs}

      <DeskTabBar tabs={tabs} activeTab={activeTab} onTabChange={onTabChange} />

      {children}
    </div>
  );
}

/**
 * URL-synced tab bar. Lifting the active tab into a search param means
 * /strategies/directional?tab=paper is a shareable link, and the
 * browser back button moves between tabs naturally.
 */
export function useUrlTab(defaultTab: string, param = "tab"): [string, (key: string) => void] {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const active = searchParams.get(param) || defaultTab;
  const set = useCallback(
    (key: string) => {
      const next = new URLSearchParams(searchParams.toString());
      if (key === defaultTab) next.delete(param);
      else next.set(param, key);
      router.replace(`${pathname}${next.size ? `?${next.toString()}` : ""}`, { scroll: false });
    },
    [router, pathname, searchParams, defaultTab, param],
  );
  return [active, set];
}

/**
 * A second URL-synced selector for surfaces that key on more than a tab — the
 * books pages carry `?view=` AND `?market=`, so the market switch needs the
 * same shareable-link behaviour without stomping the view.
 */
export function useUrlChoice<T extends string>(
  param: string,
  allowed: readonly T[],
  fallback: T,
): [T, (value: T) => void] {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const raw = searchParams.get(param);
  const active = (allowed as readonly string[]).includes(raw ?? "") ? (raw as T) : fallback;
  const set = useCallback(
    (value: T) => {
      const next = new URLSearchParams(searchParams.toString());
      if (value === fallback) next.delete(param);
      else next.set(param, value);
      router.replace(`${pathname}${next.size ? `?${next.toString()}` : ""}`, { scroll: false });
    },
    [router, pathname, searchParams, param, fallback],
  );
  return [active, set];
}

function DeskTabBar({
  tabs,
  activeTab,
  onTabChange,
}: {
  tabs: DeskTab[];
  activeTab: string;
  onTabChange: (key: string) => void;
}) {
  return (
    <nav className="flex flex-wrap items-center gap-1 border-b border-bg-border/40 pb-0">
      {tabs.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          type="button"
          onClick={() => onTabChange(key)}
          className={clsx(
            "inline-flex items-center gap-1.5 rounded-t-lg border-b-2 px-3 py-2 text-[12.5px] font-semibold transition-colors",
            activeTab === key
              ? "border-accent-blue text-text-primary"
              : "border-transparent text-text-muted hover:text-text-secondary",
          )}
        >
          {Icon ? <Icon size={14} /> : null}
          {label}
        </button>
      ))}
    </nav>
  );
}
