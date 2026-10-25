"use client";

/**
 * GateChips — a gates dict rendered as compact pass/fail chips.
 *
 * Green check = gate passed, red cross = gate blocked. Blocked reasons (the
 * lane's own explanation of why it isn't trading) get a prominent red banner —
 * a desk should never have to guess why a signal is FLAT.
 *
 * HONESTY (2026-07-19): chips are rendered from the RAW gate keys the backend
 * emits, and some of those keys are historical names that overstate the
 * evidence. `real_tick_cvd` is the standing example: it asserts nothing about
 * trade prints — it only requires CVD built from the observed QUOTE-tick
 * stream (`cvd_source == "market_ticks"`) instead of from bar shape. No wired
 * broker sends an aggressor-tagged tape, so the buy/sell sides are inferred
 * either way (`backend/analytics/orderflow.py`). DISPLAY ONLY: the gate key,
 * its condition and the payload contract are untouched — the raw key is still
 * shown verbatim in the chip's tooltip so nothing is laundered.
 */
import { Check, X } from "lucide-react";

/** Display overrides for gate keys whose name claims more than the gate does. */
const GATE_LABEL_OVERRIDE: Record<string, { label: string; note: string }> = {
  real_tick_cvd: {
    label: "cvd from quote ticks",
    note:
      "CVD is built from the observed quote-tick stream rather than from bar shape. It is NOT a trade-print claim — the feed carries no aggressor tape, so the buy/sell sides are inferred either way.",
  },
};

export function GateChips({
  gates,
  blockedReasons,
  title,
  className,
}: {
  gates?: Record<string, boolean> | null;
  blockedReasons?: string[] | null;
  title?: string;
  className?: string;
}) {
  const entries = Object.entries(gates ?? {});
  const failed = entries.filter(([, pass]) => !pass).length;

  return (
    <div className={className}>
      {title ? (
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted">{title}</span>
          <span className={`font-mono text-[10px] ${failed ? "text-accent-red" : "text-accent-green"}`}>
            {entries.length - failed}/{entries.length || 0} pass
          </span>
        </div>
      ) : null}

      {entries.length ? (
        <div className="flex flex-wrap gap-1.5">
          {entries.map(([key, pass]) => {
            const override = GATE_LABEL_OVERRIDE[key];
            return (
              <span
                key={key}
                className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${
                  pass
                    ? "border-accent-green/30 bg-accent-green/10 text-accent-green"
                    : "border-accent-red/40 bg-accent-red/10 text-accent-red"
                }`}
                title={`${key}: ${pass ? "PASS" : "BLOCK"}${override ? ` — ${override.note}` : ""}`}
              >
                {pass ? <Check size={10} strokeWidth={3} /> : <X size={10} strokeWidth={3} />}
                {override?.label ?? key.replace(/_/g, " ")}
              </span>
            );
          })}
        </div>
      ) : (
        <div className="text-[11px] text-text-muted">No gate evaluation in this snapshot.</div>
      )}

      {blockedReasons?.length ? (
        <div className="mt-2 rounded-lg border border-accent-red/40 bg-accent-red/10 px-2.5 py-1.5 text-[11px] font-medium text-accent-red">
          {/* Blocked reasons ARE gate keys (engine.py emits the failing keys
              verbatim), so they get the same display override. */}
          Blocked: {blockedReasons.map((r) => GATE_LABEL_OVERRIDE[r]?.label ?? r.replace(/_/g, " ")).join(" · ")}
        </div>
      ) : null}
    </div>
  );
}
