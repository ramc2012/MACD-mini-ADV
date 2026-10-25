"use client";

/**
 * The shared visual vocabulary for the Vanguard desk.
 *
 * Three ideas are repeated everywhere on this desk, and each exists because of
 * something the lane got wrong before:
 *
 *   LegChain     A candidate does not "pass" or "fail" — it dies at exactly
 *                ONE of six ordered legs, and the rest were never asked. Three
 *                states, not two. Rendering an unasked leg as a failure would
 *                have the desk blaming six gates for one death.
 *
 *   ValueAge     Every joined input is drawn WITH the age of the row it came
 *                from. A flow score of +82 means one thing computed yesterday
 *                and something else entirely computed a month ago, and until
 *                2026-08-27 the lane itself could not tell the difference —
 *                the joins had no maximum age at all. Showing the number
 *                without its age would reproduce that blindness in the UI.
 *
 *   Unmeasured   A value the backend genuinely did not compute. Never 0, never
 *                an em-dash that could pass for a real reading. "not collected"
 *                and "collected and flat" are different facts about the world.
 */
import { clsx } from "clsx";

export const LEG_LABELS: Record<string, string> = {
  flow_present: "flow",
  flow_fresh: "fresh",
  flow_strength: "strength",
  sector_rs: "sector RS",
  regime: "regime",
  timing: "timing",
};

export const LEG_ORDER = [
  "flow_present",
  "flow_fresh",
  "flow_strength",
  "sector_rs",
  "regime",
  "timing",
] as const;

/** A value the backend genuinely did not compute. Never rendered as 0. */
export function Unmeasured({ why, short }: { why: string; short?: boolean }) {
  return (
    <span className="cursor-help text-text-muted" title={why}>
      {short ? "—" : "not collected"}
    </span>
  );
}

export function num(value: unknown): number | null {
  const n = typeof value === "string" ? Number(value) : value;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

export function fmt(value: unknown, digits = 2): string {
  const n = num(value);
  return n == null ? "—" : n.toFixed(digits);
}

export function signed(value: unknown, digits = 1): string {
  const n = num(value);
  return n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(digits)}`;
}

/** Independent leg opinions, with a separate recorded rejection marker. */
export function LegChain({
  legs,
  firstFailed,
  size = "md",
}: {
  legs: Record<string, boolean | null | undefined>;
  firstFailed?: string | null;
  size?: "sm" | "md";
}) {
  const box = size === "sm" ? "h-2 w-4" : "h-2.5 w-5";
  return (
    <div className="flex items-center gap-[3px]" role="img"
         aria-label={firstFailed ? `rejected at ${LEG_LABELS[firstFailed] ?? firstFailed}` : "independent evidence verdicts"}>
      {LEG_ORDER.map((leg) => {
        const state = legs?.[leg];
        const died = firstFailed === leg;
        return (
          <span
            key={leg}
            title={
              died
                ? `rejected at: ${LEG_LABELS[leg]}`
                : state === true
                  ? `passed: ${LEG_LABELS[leg]}`
                  : state === false
                    ? `not supporting: ${LEG_LABELS[leg]}`
                    : `unavailable: ${LEG_LABELS[leg]}`
            }
            className={clsx(
              box,
              "rounded-[2px] transition-colors",
              died
                ? "bg-accent-red"
                : state === true
                  ? "bg-accent-green/80"
                  : state === false
                    ? "bg-accent-amber/70"
                    : "border border-dashed border-bg-border bg-transparent",
            )}
          />
        );
      })}
    </div>
  );
}

/**
 * A number and the age of the row it was read from, as one unit.
 *
 * `maxAge` is the threshold the selector actually applies (served by the API,
 * never hardcoded here), so the chip turns amber exactly when the lane would
 * reject the input — the UI and the filter cannot disagree about staleness.
 */
export function ValueAge({
  value,
  age,
  maxAge,
  unit = "s",
  digits = 1,
  whyMissing,
}: {
  value: unknown;
  age?: number | null;
  maxAge?: number | null;
  /** "s" = sessions, "b" = bars */
  unit?: "s" | "b";
  digits?: number;
  whyMissing: string;
}) {
  const v = num(value);
  if (v == null) return <Unmeasured why={whyMissing} />;
  const a = num(age);
  const stale = a != null && maxAge != null && a > maxAge;
  return (
    <span className="inline-flex items-baseline gap-1.5 font-mono">
      <span className={stale ? "text-text-muted line-through decoration-accent-amber/70" : "text-text-primary"}>
        {v > 0 ? "+" : ""}
        {v.toFixed(digits)}
      </span>
      {a == null ? (
        <span className="rounded px-1 text-[10px] text-text-muted" title="age unknown — the selector fails this closed">
          age?
        </span>
      ) : (
        <span
          className={clsx(
            "rounded px-1 text-[10px]",
            stale
              ? "bg-accent-amber/15 text-accent-amber"
              : a === 0
                ? "text-text-muted"
                : "bg-bg-secondary/50 text-text-secondary",
          )}
          title={
            stale
              ? `${a}${unit} old — past the ${maxAge}${unit} limit, so this input is rejected`
              : `${a}${unit} old`
          }
        >
          {a}
          {unit}
        </span>
      )}
    </span>
  );
}

/** A 0..100 score as a number plus a proportional bar. */
export function ScoreBar({
  value,
  max = 100,
  threshold,
  color = "blue",
  width = 56,
}: {
  value?: number | null;
  max?: number;
  threshold?: number | null;
  color?: "blue" | "green" | "amber" | "violet";
  width?: number;
}) {
  const v = num(value);
  const palette = {
    blue: "bg-accent-blue/80",
    green: "bg-accent-green/80",
    amber: "bg-accent-amber/80",
    violet: "bg-accent-purple/80",
  }[color];
  return (
    <span className="inline-flex items-center gap-2">
      <span className="w-9 text-right font-mono text-xs text-text-primary">
        {v == null ? "—" : v.toFixed(0)}
      </span>
      <span
        className="relative inline-block h-1.5 overflow-hidden rounded-full bg-bg-border/70"
        style={{ width }}
      >
        {v != null && (
          <span
            className={clsx("absolute inset-y-0 left-0 rounded-full", palette)}
            style={{ width: `${Math.max(0, Math.min(100, (v / max) * 100))}%` }}
          />
        )}
        {threshold != null && (
          <span
            className="absolute inset-y-0 w-px bg-text-secondary/70"
            style={{ left: `${Math.max(0, Math.min(100, (threshold / max) * 100))}%` }}
            title={`threshold ${threshold}`}
          />
        )}
      </span>
    </span>
  );
}

/**
 * Where price sits relative to its own developing value area.
 *
 * Unclipped on purpose — M5 stores the raw ratio, so 1.15 genuinely means
 * "0.15 value-area-widths above VAH". Clamping the MARKER but labelling the
 * true number keeps the extreme visible without pretending it is inside.
 */
export function ValueAreaGauge({ position, width = 62 }: { position?: number | null; width?: number }) {
  const p = num(position);
  if (p == null) return <Unmeasured why="M5 wrote no value-area position for this bar" short />;
  const clamped = Math.max(-0.25, Math.min(1.25, p));
  const left = ((clamped + 0.25) / 1.5) * 100;
  const outside = p < 0 || p > 1;
  return (
    <span className="inline-flex items-center gap-2" title={`va_position = ${p.toFixed(3)}${outside ? " (beyond the value area)" : ""}`}>
      <span className="relative inline-block h-2.5 rounded-sm bg-bg-border/50" style={{ width }}>
        {/* the value area itself: the middle two thirds of the track */}
        <span className="absolute inset-y-0 rounded-sm bg-accent-blue/20" style={{ left: "16.7%", right: "16.7%" }} />
        <span
          className={clsx("absolute top-1/2 h-2.5 w-[3px] -translate-y-1/2 rounded-sm",
            outside ? "bg-accent-amber" : "bg-text-primary")}
          style={{ left: `${left}%` }}
        />
      </span>
      <span className={clsx("font-mono text-[11px]", outside ? "text-accent-amber" : "text-text-secondary")}>
        {p.toFixed(2)}
      </span>
    </span>
  );
}

const REGIME_TONE: Record<string, string> = {
  STRONG_NEG: "border-accent-green/40 bg-accent-green/12 text-accent-green",
  NEG: "border-accent-green/25 bg-accent-green/8 text-accent-green",
  NEUTRAL: "border-bg-border bg-bg-secondary/40 text-text-secondary",
  POS: "border-accent-amber/30 bg-accent-amber/10 text-accent-amber",
  STRONG_POS: "border-accent-red/30 bg-accent-red/10 text-accent-red",
};

/**
 * Dealer-gamma regime.
 *
 * Green for NEGATIVE gamma is not a mistake: M6 treats negative/neutral dealer
 * gamma as momentum-PERMITTING, so green here means "this regime lets a
 * candidate through", not "this is bullish". The title says so, because the
 * colour alone would be read the other way.
 */
export function RegimeChip({ regime, ageBars, maxAgeBars }: {
  regime?: string | null;
  ageBars?: number | null;
  maxAgeBars?: number | null;
}) {
  if (!regime) return <Unmeasured why="M3 has no GEX regime for this symbol at this bar" short />;
  const age = num(ageBars);
  const stale = age != null && maxAgeBars != null && age > maxAgeBars;
  return (
    <span className="inline-flex items-center gap-1">
      <span
        className={clsx(
          "rounded-md border px-1.5 py-0.5 font-mono text-[10px]",
          stale ? "border-accent-amber/40 bg-accent-amber/10 text-accent-amber" : REGIME_TONE[regime] ?? REGIME_TONE.NEUTRAL,
        )}
        title={
          `dealer gamma regime ${regime}. Negative/neutral gamma amplifies moves and is what M6 ` +
          `permits for a momentum candidate — green here means "permitted", not "bullish".` +
          (stale ? ` STALE: ${age} bars old, limit ${maxAgeBars}.` : "")
        }
      >
        {regime.replace("STRONG_", "S")}
      </span>
      {stale && <span className="font-mono text-[10px] text-accent-amber">{age}b</span>}
    </span>
  );
}

const TIMING_TONE: Record<string, string> = {
  IGNITION: "border-accent-green/40 bg-accent-green/12 text-accent-green",
  EXHAUST: "border-accent-red/30 bg-accent-red/10 text-accent-red",
  COMPRESSION: "border-accent-blue/30 bg-accent-blue/10 text-accent-blue",
  BALANCED: "border-bg-border bg-bg-secondary/40 text-text-muted",
};

export function TimingChip({ state }: { state?: string | null }) {
  if (!state) return <Unmeasured why="M5 wrote no timing state for this bar" short />;
  return (
    <span
      className={clsx("rounded-md border px-1.5 py-0.5 font-mono text-[10px]",
        TIMING_TONE[state] ?? TIMING_TONE.BALANCED)}
      title={
        state === "BALANCED"
          ? "BALANCED carries no directional claim — it is also the state every bar gets when RVOL has too little history to be defined."
          : `M5 microstructure state: ${state}`
      }
    >
      {state}
    </span>
  );
}

/** Inline sparkline with no charting dependency — dozens render cheaply. */
export function Spark({
  values,
  width = 64,
  height = 16,
  color = "rgb(var(--accent-blue))",
}: {
  values?: (number | null)[] | null;
  width?: number;
  height?: number;
  color?: string;
}) {
  const series = (values || []).map(num).filter((v): v is number => v != null);
  if (series.length < 2) {
    return <span className="inline-block text-[10px] text-text-muted" style={{ width }} title="fewer than two bars — nothing to trend">·</span>;
  }
  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = max - min || 1;
  const step = width / (series.length - 1);
  const path = series
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(height - ((v - min) / span) * height).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="overflow-visible"
         aria-hidden>
      <path d={path} fill="none" stroke={color} strokeWidth={1.25} strokeLinejoin="round" />
      <circle cx={(series.length - 1) * step} cy={height - ((series[series.length - 1] - min) / span) * height}
              r={1.6} fill={color} />
    </svg>
  );
}

// ── Open interest, positioning and performance ─────────────────────────────

/**
 * The four-state OI/price conjunction, the standard Indian F&O positioning
 * read. Colour carries the DIRECTIONAL implication and the fill weight carries
 * conviction, because the four states are not two pairs:
 *
 *   long buildup    price up,   OI up    new longs   — strong bullish
 *   short covering  price up,   OI down  shorts out  — weak bullish
 *   short buildup   price down, OI up    new shorts  — strong bearish
 *   long unwinding  price down, OI down  longs out   — weak bearish
 *
 * A buildup is fresh money taking a side; an unwind/cover is old money
 * leaving. Rendering all four with equal weight would lose exactly the
 * distinction the read exists to make, so the two "weak" states are drawn
 * hollow.
 */
const OI_STATE: Record<string, { label: string; cls: string; title: string }> = {
  long_buildup: {
    label: "LONG BUILD",
    cls: "border-accent-green/50 bg-accent-green/15 text-accent-green",
    title: "Long buildup — price up, open interest up. Fresh longs: new money taking the bullish side.",
  },
  short_covering: {
    label: "SHORT COVER",
    cls: "border-accent-green/35 bg-transparent text-accent-green/85",
    title: "Short covering — price up, open interest down. Shorts closing out; a rally on positions leaving, not fresh buying.",
  },
  short_buildup: {
    label: "SHORT BUILD",
    cls: "border-accent-red/50 bg-accent-red/15 text-accent-red",
    title: "Short buildup — price down, open interest up. Fresh shorts: new money taking the bearish side.",
  },
  long_unwind: {
    label: "LONG UNWIND",
    cls: "border-accent-red/35 bg-transparent text-accent-red/85",
    title: "Long unwinding — price down, open interest down. Longs closing out; a decline on positions leaving, not fresh selling.",
  },
};

export function OiStateBadge({
  state,
  dOiPct,
  dPricePct,
}: {
  state?: string | null;
  dOiPct?: number | null;
  dPricePct?: number | null;
}) {
  if (!state) {
    return (
      <Unmeasured
        why="Either the OI change or the price change was missing or exactly flat for this session. The conjunction needs both legs and is never guessed."
        short
      />
    );
  }
  const spec = OI_STATE[state];
  if (!spec) return <span className="font-mono text-[10px] text-text-muted">{state}</span>;
  const oi = num(dOiPct);
  const px = num(dPricePct);
  return (
    <span
      className={clsx("inline-block rounded-md border px-1.5 py-0.5 font-mono text-[9.5px] tracking-tight", spec.cls)}
      title={
        spec.title +
        (oi != null && px != null
          ? `\n\nOI ${oi > 0 ? "+" : ""}${oi.toFixed(1)}%, price ${px > 0 ? "+" : ""}${px.toFixed(2)}%`
          : "")
      }
    >
      {spec.label}
    </span>
  );
}

/**
 * Dealer gamma as a colour, not just a word.
 *
 * A diverging scale on the PERCENTILE (which is what M6 consumes) rather than
 * on raw net_gex: net_gex is a ~1e9 quantity with no cross-symbol comparability,
 * whereas the percentile is each symbol ranked against its own trailing 60
 * sessions and is the only version of the number that means the same thing on
 * two different names.
 *
 * Teal = short gamma (dealers amplify moves; M6 permits momentum here).
 * Amber = long gamma (dealers dampen moves; M6 blocks momentum).
 * The bar fills from the centre so "how far from neutral" is the visual, which
 * is the question the regime bucket is actually answering.
 */
export function GexScale({
  percentile,
  regime,
  ageBars,
  maxAgeBars,
  width = 58,
}: {
  percentile?: number | null;
  regime?: string | null;
  ageBars?: number | null;
  maxAgeBars?: number | null;
  width?: number;
}) {
  const p = num(percentile);
  const age = num(ageBars);
  const stale = age != null && maxAgeBars != null && age > maxAgeBars;

  if (p == null) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <RegimeChip regime={regime} ageBars={ageBars} maxAgeBars={maxAgeBars} />
      </span>
    );
  }
  // centred: -1 (deepest short gamma) .. +1 (deepest long gamma)
  const centred = (p - 0.5) * 2;
  const magnitude = Math.min(1, Math.abs(centred));
  const shortGamma = centred < 0;
  const colour = stale
    ? "rgb(var(--text-muted))"
    : shortGamma
      ? "rgb(var(--accent-green))"
      : "rgb(var(--accent-amber))";
  return (
    <span
      className="inline-flex items-center gap-1.5"
      title={
        `GEX percentile ${(p * 100).toFixed(0)} against this symbol's own trailing 60 sessions.\n` +
        (shortGamma
          ? "Below median: dealers shorter gamma than usual — hedging amplifies moves. M6 permits momentum here."
          : "Above median: dealers longer gamma than usual — hedging dampens moves. M6 blocks momentum here.") +
        (stale ? `\nSTALE: ${age} bars old against a ${maxAgeBars}-bar limit, so this input is rejected.` : "")
      }
    >
      <span
        className="relative inline-block h-2.5 rounded-sm border border-bg-border/70 bg-bg-secondary/40"
        style={{ width }}
      >
        <span className="absolute inset-y-0 left-1/2 w-px bg-text-muted/50" />
        <span
          className="absolute inset-y-[1px] rounded-[1px]"
          style={{
            background: colour,
            opacity: stale ? 0.35 : 0.55 + magnitude * 0.45,
            left: shortGamma ? `${50 - magnitude * 50}%` : "50%",
            width: `${magnitude * 50}%`,
          }}
        />
      </span>
      <span
        className={clsx("font-mono text-[10px]", stale ? "text-accent-amber" : "text-text-secondary")}
      >
        {regime ? regime.replace("STRONG_", "S") : (p * 100).toFixed(0)}
      </span>
    </span>
  );
}

/** A percentage return, toned by sign, with a proportional bar for scanning. */
export function PerfCell({
  value,
  scale = 10,
  width = 44,
  digits = 1,
}: {
  value?: number | null;
  /** Percent move that fills the bar completely. */
  scale?: number;
  width?: number;
  digits?: number;
}) {
  const v = num(value);
  if (v == null) return <Unmeasured why="no close for this session — the NSE spot feed arrives as an overnight batch, so today has none until tomorrow" short />;
  const magnitude = Math.min(1, Math.abs(v) / scale);
  const up = v > 0;
  return (
    <span className="inline-flex items-center justify-end gap-1.5">
      <span className={clsx("font-mono text-[11px]", up ? "text-accent-green" : v < 0 ? "text-accent-red" : "text-text-muted")}>
        {up ? "+" : ""}
        {v.toFixed(digits)}
      </span>
      <span className="relative inline-block h-2 rounded-sm bg-bg-border/50" style={{ width }}>
        <span className="absolute inset-y-0 left-1/2 w-px bg-text-muted/40" />
        <span
          className={clsx("absolute inset-y-[1px] rounded-[1px]", up ? "bg-accent-green/70" : "bg-accent-red/70")}
          style={{ left: up ? "50%" : `${50 - magnitude * 50}%`, width: `${magnitude * 50}%` }}
        />
      </span>
    </span>
  );
}

/** Open interest in the units a desk actually reads it in. */
export function OiCell({
  total,
  dPct,
  source,
}: {
  total?: number | null;
  dPct?: number | null;
  source?: string | null;
}) {
  const oi = num(total);
  if (oi == null) return <Unmeasured why="no open-interest row for this symbol at or before this session" short />;
  const d = num(dPct);
  return (
    <span
      className="inline-flex items-baseline gap-1.5 font-mono text-[11px]"
      title={
        source === "chain_sum"
          ? "Summed from the option contracts this lane collects — a subset that varies with collection health, NOT the exchange's own aggregate."
          : "NSE's own market-wide F&O open interest for this symbol (the MWPL publication)."
      }
    >
      <span className="text-text-primary">{compactOi(oi)}</span>
      {d != null && (
        <span className={d > 0 ? "text-accent-green/85" : d < 0 ? "text-accent-red/85" : "text-text-muted"}>
          {d > 0 ? "+" : ""}
          {d.toFixed(1)}%
        </span>
      )}
      {source === "chain_sum" && (
        <span className="text-[9px] text-accent-amber" title="chain-summed, not exchange aggregate">~</span>
      )}
    </span>
  );
}

function compactOi(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e7) return `${(value / 1e7).toFixed(2)}cr`;
  if (abs >= 1e5) return `${(value / 1e5).toFixed(1)}L`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(0)}k`;
  return value.toFixed(0);
}

/**
 * MWPL utilisation. Past 95% NSE bans fresh F&O positions in the name.
 *
 * The badge deliberately says "BAN" rather than colouring quietly: this is the
 * one number on the desk with a hard exchange consequence, and M7 does not yet
 * veto on it, so a trader reading this row is the only control there is.
 */
export function MwplCell({ pct }: { pct?: number | null }) {
  const v = num(pct);
  if (v == null) return <Unmeasured why="no MWPL publication for this symbol" short />;
  const banned = v >= 95;
  const near = v >= 80;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 font-mono text-[11px]",
        banned ? "text-accent-red" : near ? "text-accent-amber" : "text-text-secondary",
      )}
      title={
        `Market-wide position limit utilisation ${v.toFixed(1)}%.` +
        (banned
          ? " At or past 95% — NSE bans FRESH F&O positions in this name. M7 does not enforce this; you are the control."
          : near
            ? " Approaching the 95% ban threshold."
            : "")
      }
    >
      {v.toFixed(0)}%
      {banned && (
        <span className="rounded border border-accent-red/50 bg-accent-red/15 px-1 text-[9px]">BAN</span>
      )}
    </span>
  );
}

/** PCR with its session change — a level and a direction, never just a level. */
export function PcrCell({ pcr, dPcr }: { pcr?: number | null; dPcr?: number | null }) {
  const v = num(pcr);
  if (v == null) return <Unmeasured why="no front-expiry CE/PE open interest collected for this symbol" short />;
  const d = num(dPcr);
  return (
    <span
      className="inline-flex items-baseline gap-1.5 font-mono text-[11px]"
      title={
        "Front-expiry put/call open-interest ratio, recomputed from live contract OI " +
        "(fo_option_chain_metrics' own aggregate stopped on 2026-08-03). " +
        "Above 1 = more put OI than call OI."
      }
    >
      <span className={v > 1 ? "text-accent-green/85" : "text-accent-red/85"}>{v.toFixed(2)}</span>
      {d != null && (
        <span className="text-[10px] text-text-muted">
          {d > 0 ? "+" : ""}
          {d.toFixed(2)}
        </span>
      )}
    </span>
  );
}

/** A sortable column header. Sorting is always descending-first on numerics. */
export function SortHeader({
  label,
  sortKey,
  active,
  onSort,
  align = "left",
  title,
}: {
  label: string;
  sortKey: string;
  active: string;
  onSort: (key: string) => void;
  align?: "left" | "right";
  title?: string;
}) {
  const on = active === sortKey;
  return (
    <th className={clsx("py-2 pr-3", align === "right" && "text-right")}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        title={title}
        className={clsx(
          "inline-flex items-center gap-1 uppercase tracking-[0.14em] transition-colors",
          on ? "text-text-primary" : "hover:text-text-secondary",
        )}
      >
        {label}
        <span className={clsx("text-[8px]", on ? "opacity-100" : "opacity-25")}>▼</span>
      </button>
    </th>
  );
}

// ── Implied volatility ─────────────────────────────────────────────────────

/**
 * An implied vol with where it sits in its own history.
 *
 * The level alone is not a reading — 28% means opposite things on a name that
 * usually trades 20% and one that usually trades 45%. The percentile is what
 * carries the information, so it gets the colour and the level gets the
 * number. Every IV on this desk is SOLVED by Vanguard from the option's own
 * price; the vendor's iv column stopped for equities on 2026-07-28.
 */
export function IvCell({
  iv,
  percentile,
  change,
}: {
  iv?: number | null;
  percentile?: number | null;
  change?: number | null;
}) {
  const v = num(iv);
  if (v == null) {
    return <Unmeasured why="no good-quality contract IV solved for this symbol on this session" short />;
  }
  const p = num(percentile);
  const d = num(change);
  const hot = p != null && p >= 0.8;
  const cold = p != null && p <= 0.2;
  return (
    <span
      className="inline-flex items-baseline gap-1.5 font-mono text-[11px]"
      title={
        `ATM implied volatility ${(v * 100).toFixed(1)}%, solved from the option's own ` +
        `price (Black-Scholes, European).` +
        (p != null ? `\n${(p * 100).toFixed(0)}th percentile of its own trailing 60 sessions.` : "") +
        (d != null ? `\nSession change ${d > 0 ? "+" : ""}${(d * 100).toFixed(2)} vol points.` : "")
      }
    >
      <span className={clsx(hot ? "text-accent-red" : cold ? "text-accent-blue" : "text-text-primary")}>
        {(v * 100).toFixed(1)}
      </span>
      {p != null && (
        <span
          className={clsx(
            "rounded px-1 text-[9.5px]",
            hot ? "bg-accent-red/15 text-accent-red"
              : cold ? "bg-accent-blue/15 text-accent-blue"
                : "bg-bg-secondary/50 text-text-muted",
          )}
        >
          p{(p * 100).toFixed(0)}
        </span>
      )}
      {d != null && (
        <span className={d > 0 ? "text-accent-red/80" : d < 0 ? "text-accent-green/80" : "text-text-muted"}>
          {d > 0 ? "+" : ""}
          {(d * 100).toFixed(2)}
        </span>
      )}
    </span>
  );
}

/**
 * The 25-delta risk reversal, or the reason there isn't one.
 *
 * This is NULL for about 90% of rows, and that is the correct answer rather
 * than a gap to be filled. The collected chain has never carried enough
 * strikes to hold a genuine 25-delta contract — breadth peaked at 6.6
 * contracts per symbol per day and is now 1.2 — so taking the nearest
 * available strike (which is what M2 does) returns a near-ATM contract and
 * measures the call-minus-put spread a second time under a different name.
 */
export function SkewCell({ skew, reason }: { skew?: number | null; reason?: string | null }) {
  const v = num(skew);
  if (v == null) {
    return (
      <span
        className="cursor-help text-[10px] text-text-muted"
        title={reason || "no 25-delta contract on both wings"}
      >
        no wings
      </span>
    );
  }
  return (
    <span
      className={clsx("font-mono text-[11px]", v > 0 ? "text-accent-red" : "text-accent-green")}
      title={
        `25-delta put IV minus 25-delta call IV: ${(v * 100).toFixed(2)} vol points. ` +
        (v > 0
          ? "Puts bid over calls — the usual defensive skew."
          : "Calls bid over puts — an unusual, upside-seeking skew.")
      }
    >
      {v > 0 ? "+" : ""}
      {(v * 100).toFixed(2)}
    </span>
  );
}

/**
 * Cremers-Weinbaum implied-volatility spread: near-ATM call IV minus put IV.
 * This is the actual informed-flow quantity M2 is built on, and unlike the
 * skew it IS computable from the chain the lane collects.
 */
export function IvsCell({ ivs }: { ivs?: number | null }) {
  const v = num(ivs);
  if (v == null) return <Unmeasured why="needs priced contracts on both the call and put side near the money" short />;
  return (
    <span
      className={clsx("font-mono text-[11px]", v > 0 ? "text-accent-green" : v < 0 ? "text-accent-red" : "text-text-muted")}
      title={
        `Call IV minus put IV near the money: ${(v * 100).toFixed(2)} vol points. ` +
        (v > 0 ? "Calls bid over puts — the informed-flow reading is bullish."
               : "Puts bid over calls — the informed-flow reading is bearish.")
      }
    >
      {v > 0 ? "+" : ""}
      {(v * 100).toFixed(2)}
    </span>
  );
}
