import { clsx } from "clsx";

/**
 * Pill badge for statuses. Pass either a `tone` class (from desk-ui/tones)
 * or one of the canned variants. Replaces inline `border-accent-… bg-…
 * text-…` chains repeated in dozens of places in v1.
 */
type Variant = "neutral" | "success" | "warn" | "error" | "info";

const VARIANT: Record<Variant, string> = {
  neutral: "border-bg-border bg-bg-secondary/40 text-text-secondary",
  success: "border-accent-green/35 bg-accent-green/10 text-accent-green",
  warn:    "border-accent-amber/35 bg-accent-amber/10 text-accent-amber",
  error:   "border-accent-red/35 bg-accent-red/10 text-accent-red",
  info:    "border-accent-blue/35 bg-accent-blue/10 text-accent-blue",
};

export function StatusBadge({
  label,
  variant = "neutral",
  tone,
  icon,
  className,
}: {
  label: string;
  variant?: Variant;
  tone?: string;
  icon?: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.14em]",
        tone || VARIANT[variant],
        className,
      )}
    >
      {icon}
      {label}
    </span>
  );
}
