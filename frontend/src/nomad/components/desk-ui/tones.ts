/**
 * Centralised tone helpers. v1 hardcoded `text-accent-green|amber|red`
 * strings inline in 8+ workspaces (SectorInteraction alone has it in
 * 8 places, MacroResearch in 6). One source of truth here.
 */

/** P&L / numeric magnitude tone — green positive, rose negative. */
export function tone(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return "text-text-muted";
  if (value > 0) return "text-accent-green";
  if (value < 0) return "text-accent-red";
  return "text-text-secondary";
}

/** Direction tone (option CE/PE). */
export function directionTone(direction?: string | null): string {
  if (direction === "CE") return "text-accent-green";
  if (direction === "PE") return "text-accent-red";
  return "text-text-muted";
}

/**
 * Regime label tone — colour-codes regime chips so a trader can see
 * tape state at a glance. Centralised so every desk uses the same
 * palette.
 */
export function regimeTone(label?: string | null): string {
  switch (label) {
    case "breakout":
    case "trend":
      return "border-accent-green/30 bg-accent-green/10 text-accent-green";
    case "micro_trend":
    case "exploration":
      return "border-accent-blue/30 bg-accent-blue/10 text-accent-blue";
    case "chop":
      return "border-accent-amber/30 bg-accent-amber/10 text-accent-amber";
    case "risk_off":
      return "border-accent-red/30 bg-accent-red/10 text-accent-red";
    default:
      return "border-bg-border bg-bg-primary/15 text-text-muted";
  }
}

/** Service state tone — used by /system page and broker bar. */
export function serviceStateTone(status?: string | null): string {
  const s = String(status || "").toLowerCase();
  if (s === "healthy" || s === "active" || s === "ready") {
    return "border-accent-green/30 bg-accent-green/10 text-accent-green";
  }
  if (s === "degraded" || s === "warning" || s === "stale") {
    return "border-accent-amber/30 bg-accent-amber/10 text-accent-amber";
  }
  if (s === "critical" || s === "error") {
    return "border-accent-red/30 bg-accent-red/10 text-accent-red";
  }
  return "border-bg-border bg-bg-secondary/28 text-text-secondary";
}

/** Generic ACT/SKIP / approved/rejected tone. */
export function decisionTone(approved?: boolean | null): string {
  if (approved === true) return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (approved === false) return "border-accent-amber/40 bg-accent-amber/10 text-accent-amber";
  return "border-bg-border bg-bg-primary/15 text-text-muted";
}
