import { clsx } from "clsx";

import { ProvenanceChip } from "./SemanticBadges";
import type { Provenance } from "@/lib/market-semantics";

/**
 * Standard section wrapper. The v1 frontend used a mix of
 * `rounded-2xl`, `rounded-[26px]`, `rounded-[28px]` with inconsistent
 * border / background. One Section here, used everywhere.
 *
 * Optional `provenance` renders the shared caption affordance under the
 * section body: source · grade · aggregation · completeness · age · data mode.
 * Existing callers are unaffected (the prop is opt-in).
 */
export function Section({
  title,
  icon,
  description,
  rightSlot,
  provenance,
  padded = true,
  className,
  children,
}: {
  title?: React.ReactNode;
  icon?: React.ReactNode;
  description?: React.ReactNode;
  rightSlot?: React.ReactNode;
  /** Panel-level data provenance — rendered as a compact caption. */
  provenance?: Provenance | null;
  padded?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className={clsx(
        "rounded-2xl border border-bg-border bg-bg-secondary/24",
        padded && "p-5",
        className,
      )}
    >
      {title ? (
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-text-primary">
              {icon}
              {title}
            </div>
            {description ? (
              <div className="mt-1 text-xs text-text-muted">{description}</div>
            ) : null}
          </div>
          {rightSlot}
        </div>
      ) : null}
      {children}
      {provenance ? <ProvenanceChip provenance={provenance} density="caption" /> : null}
    </section>
  );
}
