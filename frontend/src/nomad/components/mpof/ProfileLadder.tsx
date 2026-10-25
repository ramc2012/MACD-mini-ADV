"use client";

/**
 * ProfileLadder — compact vertical market-profile level ladder.
 *
 * One shared price axis carrying:
 *   · TPO letter-count histogram bars per price level (when tpoCounts given)
 *   · optional volume-profile overlay (right-anchored teal bars, toggleable)
 *   · value-area band (VAL→VAH, blue tint) + VAH/VAL dashed lines
 *   · POC (solid amber)
 *   · initial-balance band (IB low→high, amber tint) + IBH/IBL dashed
 *   · prior-session VAH/VAL/POC ghost lines (violet, left half)
 *   · HVN price dots (violet, left rail)
 *   · single-print ticks (red, left edge)
 *   · current spot marker (white line + arrow), colored by position vs POC
 *
 * Controls (top-right): zoom-to-value-area, expand into a modal, and — when
 * the data is present — TPO / VOL histogram toggles.
 *
 * Pure SVG — no chart deps. Field names vary per lane, so callers map their
 * payload onto these flat props.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { BarChart3, Layers, Maximize2, X, ZoomIn, ZoomOut } from "lucide-react";

import { formatNumber } from "@/components/desk-ui";

const VB_W = 250;
const PAD = { t: 12, b: 12, l: 14, r: 66 };
const GHOST_X = VB_W - PAD.r - 100; // prior ghost lines span the left portion

export type ProfileLadderProps = {
  spot?: number | null;
  vah?: number | null;
  val?: number | null;
  poc?: number | null;
  ibHigh?: number | null;
  ibLow?: number | null;
  dayHigh?: number | null;
  dayLow?: number | null;
  prior?: { vah?: number | null; val?: number | null; poc?: number | null } | null;
  hvnPrices?: Array<number | null | undefined> | null;
  singlePrints?: Array<number | null | undefined> | null;
  /** price → TPO letter count (accepts string or numeric keys straight off the payload). */
  tpoCounts?: Record<string | number, number> | null;
  /** price → TPO letters (tooltip detail). */
  tpoLetters?: Record<string | number, string> | null;
  /** per-price traded volume for the volume-profile overlay (toggleable). */
  volumeByPrice?: Array<{ price: number; volume: number }> | null;
  /**
   * Poor (unfinished) high / low — a single-TPO extreme the auction left
   * un-repaired. ADDITIVE and optional: omitted ⇒ nothing is drawn, so every
   * existing call site renders exactly as before. Pass only a level the lane
   * actually emitted; never derive one here.
   */
  poorHigh?: number | null;
  poorLow?: number | null;
  /**
   * Initial histogram mode. Defaults reproduce the shipped behaviour (TPO on,
   * volume off) so the 9 existing call sites are untouched; the Profile
   * Workbench drives them from its own mode control.
   */
  defaultShowTpo?: boolean;
  defaultShowVol?: boolean;
  height?: number;
  /** Decimal places for level labels (indices 0-1, commodities up to 2). */
  digits?: number;
  /** Hide the mini legend row underneath. */
  hideLegend?: boolean;
  /** Hide the zoom / expand / histogram controls. */
  hideControls?: boolean;
  /** Title shown in the expanded modal header. */
  expandTitle?: string;
};

const num = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(Number(v)) || Number(v) === 0 ? null : Number(v);

type Levels = {
  cur: { spot: number | null; vah: number | null; val: number | null; poc: number | null; ibHigh: number | null; ibLow: number | null; dayHigh: number | null; dayLow: number | null };
  ghost: { vah: number | null; val: number | null; poc: number | null };
  hvns: number[];
  singles: number[];
  poor: { high: number | null; low: number | null };
  tpoRows: Array<{ price: number; count: number; letters?: string }>;
  volRows: Array<{ price: number; volume: number }>;
};

export function ProfileLadder({
  spot,
  vah,
  val,
  poc,
  ibHigh,
  ibLow,
  dayHigh,
  dayLow,
  prior,
  hvnPrices,
  singlePrints,
  tpoCounts,
  tpoLetters,
  volumeByPrice,
  poorHigh,
  poorLow,
  defaultShowTpo = true,
  defaultShowVol = false,
  height = 320,
  digits = 1,
  hideLegend = false,
  hideControls = false,
  expandTitle,
}: ProfileLadderProps) {
  const [showTpo, setShowTpo] = useState(defaultShowTpo);
  const [showVol, setShowVol] = useState(defaultShowVol);
  const [zoomVA, setZoomVA] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const levels = useMemo<Levels>(() => {
    const cur = { spot: num(spot), vah: num(vah), val: num(val), poc: num(poc), ibHigh: num(ibHigh), ibLow: num(ibLow), dayHigh: num(dayHigh), dayLow: num(dayLow) };
    const ghost = { vah: num(prior?.vah), val: num(prior?.val), poc: num(prior?.poc) };
    const hvns = (hvnPrices ?? []).map(num).filter((p): p is number => p != null);
    const singles = (singlePrints ?? []).map(num).filter((p): p is number => p != null);
    const poor = { high: num(poorHigh), low: num(poorLow) };
    const tpoRows = Object.entries(tpoCounts ?? {})
      .map(([p, c]) => ({ price: Number(p), count: Number(c), letters: tpoLetters ? String(tpoLetters[p] ?? "") || undefined : undefined }))
      .filter((r) => Number.isFinite(r.price) && Number.isFinite(r.count) && r.count > 0)
      .sort((a, b) => b.price - a.price);
    const volRows = (volumeByPrice ?? [])
      .map((r) => ({ price: Number(r?.price), volume: Number(r?.volume) }))
      .filter((r) => Number.isFinite(r.price) && Number.isFinite(r.volume) && r.volume > 0)
      .sort((a, b) => b.price - a.price);
    return { cur, ghost, hvns, singles, poor, tpoRows, volRows };
  }, [spot, vah, val, poc, ibHigh, ibLow, dayHigh, dayLow, prior, hvnPrices, singlePrints, poorHigh, poorLow, tpoCounts, tpoLetters, volumeByPrice]);

  const fullDomain = useMemo(() => {
    const all = [
      ...Object.values(levels.cur),
      ...Object.values(levels.ghost),
      ...Object.values(levels.poor),
      ...levels.hvns,
      ...levels.singles,
      ...levels.tpoRows.map((r) => r.price),
    ].filter((p): p is number => p != null);
    if (all.length < 2) return null;
    const lo = Math.min(...all);
    const hi = Math.max(...all);
    const pad = (hi - lo) * 0.05 || Math.abs(hi) * 0.001 || 1;
    return { lo: lo - pad, hi: hi + pad };
  }, [levels]);

  const domain = useMemo(() => {
    if (!fullDomain) return null;
    const { cur } = levels;
    if (!zoomVA || cur.vah == null || cur.val == null) return fullDomain;
    const vaLo = Math.min(cur.val, cur.vah);
    const vaHi = Math.max(cur.val, cur.vah);
    const pad = (vaHi - vaLo) * 0.3 || Math.abs(vaHi) * 0.001 || 1;
    // keep spot in view so the marker never silently disappears
    const lo = Math.min(vaLo, cur.spot ?? vaLo) - pad;
    const hi = Math.max(vaHi, cur.spot ?? vaHi) + pad;
    return { lo, hi };
  }, [fullDomain, levels, zoomVA]);

  const closeModal = useCallback(() => setExpanded(false), []);
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeModal(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, closeModal]);

  if (!domain) {
    return (
      <div className="flex items-center justify-center rounded-xl border border-dashed border-bg-border/60 text-xs text-text-muted" style={{ height }}>
        No profile levels yet.
      </div>
    );
  }

  const canZoom = levels.cur.vah != null && levels.cur.val != null;
  const hasTpo = levels.tpoRows.length > 0;
  const hasVol = levels.volRows.length > 0;
  const controlBtn = "rounded border border-border bg-surface/80 p-1 text-text-muted hover:text-text-primary";
  const toggleBtn = (active: boolean) => `rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] ${active ? "border-accent-blue/60 bg-accent-blue/15 text-accent-blue" : "border-border bg-surface/80 text-text-muted hover:text-text-primary"}`;

  return (
    <div className="w-full">
      <div className="relative">
        {!hideControls ? (
          <div className="absolute right-0 top-0 z-10 flex items-center gap-1">
            {hasTpo ? <button type="button" onClick={() => setShowTpo((v) => !v)} className={toggleBtn(showTpo)} title="Toggle TPO letter-count histogram"><span className="inline-flex items-center gap-0.5"><Layers size={9}/>TPO</span></button> : null}
            {hasVol ? <button type="button" onClick={() => setShowVol((v) => !v)} className={toggleBtn(showVol)} title="Toggle volume-profile overlay"><span className="inline-flex items-center gap-0.5"><BarChart3 size={9}/>VOL</span></button> : null}
            {canZoom ? <button type="button" onClick={() => setZoomVA((v) => !v)} className={controlBtn} title={zoomVA ? "Zoom out to full range" : "Zoom into the value area"} aria-label={zoomVA ? "Zoom out" : "Zoom into value area"}>{zoomVA ? <ZoomOut size={11}/> : <ZoomIn size={11}/>}</button> : null}
            <button type="button" onClick={() => setExpanded(true)} className={controlBtn} title="Expand ladder" aria-label="Expand ladder"><Maximize2 size={11}/></button>
          </div>
        ) : null}
        <LadderSvg levels={levels} domain={domain} height={height} digits={digits} showTpo={showTpo} showVol={showVol} />
      </div>

      {!hideLegend ? (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] uppercase tracking-[0.1em] text-text-muted">
          <LegendSwatch color="#ffa502" label="POC" />
          <LegendSwatch color="#3b82f6" label="VA" />
          <LegendSwatch color="rgba(255,165,2,0.75)" label="IB" />
          {hasTpo && showTpo ? <LegendSwatch color="rgba(96,165,250,0.45)" label="TPO" /> : null}
          {hasVol && showVol ? <LegendSwatch color="rgba(0,212,163,0.5)" label="VOL" /> : null}
          {levels.ghost.poc != null || levels.ghost.vah != null || levels.ghost.val != null ? <LegendSwatch color="#a78bfa" label="prior" /> : null}
          {levels.hvns.length ? <LegendSwatch color="#a78bfa" label="HVN ·" round /> : null}
          {levels.poor.high != null || levels.poor.low != null ? <LegendSwatch color="#f472b6" label="poor hi/lo" /> : null}
          {levels.cur.spot != null ? <LegendSwatch color={spotColorOf(levels)} label="spot" /> : null}
        </div>
      ) : null}

      {expanded ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Expanded market profile ladder" onClick={closeModal}>
          <div className="max-h-full w-full max-w-xl overflow-y-auto rounded-xl border border-border bg-bg-primary p-4 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="text-sm font-semibold">{expandTitle ?? "Market profile ladder"}</div>
              <div className="flex items-center gap-1">
                {hasTpo ? <button type="button" onClick={() => setShowTpo((v) => !v)} className={toggleBtn(showTpo)}>TPO</button> : null}
                {hasVol ? <button type="button" onClick={() => setShowVol((v) => !v)} className={toggleBtn(showVol)}>VOL</button> : null}
                {canZoom ? <button type="button" onClick={() => setZoomVA((v) => !v)} className={controlBtn} aria-label={zoomVA ? "Zoom out" : "Zoom into value area"}>{zoomVA ? <ZoomOut size={12}/> : <ZoomIn size={12}/>}</button> : null}
                <button type="button" onClick={closeModal} className={controlBtn} aria-label="Close expanded ladder"><X size={12}/></button>
              </div>
            </div>
            <LadderSvg levels={levels} domain={domain} height={620} digits={digits} showTpo={showTpo} showVol={showVol} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function spotColorOf(levels: Levels): string {
  const { spot, poc } = levels.cur;
  if (spot == null || poc == null) return "#e6edf3";
  return spot >= poc ? "#00d4a3" : "#ff4757";
}

function LadderSvg({ levels, domain, height, digits, showTpo, showVol }: { levels: Levels; domain: { lo: number; hi: number }; height: number; digits: number; showTpo: boolean; showVol: boolean }) {
  const y = (p: number) => PAD.t + (1 - (p - domain.lo) / (domain.hi - domain.lo || 1)) * (height - PAD.t - PAD.b);
  const inDomain = (p: number | null | undefined): p is number => p != null && p >= domain.lo && p <= domain.hi;
  const clampY = (p: number) => Math.max(PAD.t, Math.min(height - PAD.b, y(p)));
  const { cur, ghost, hvns, singles, poor, tpoRows, volRows } = levels;
  const fmt = (p: number) => formatNumber(p, digits);
  const lineEnd = VB_W - PAD.r;
  const spotColor = spotColorOf(levels);

  // Row thickness for histogram bars: pixel distance of one price tick.
  const barGeometry = (rows: Array<{ price: number }>): number => {
    let tick = Infinity;
    for (let i = 1; i < rows.length; i += 1) {
      const d = Math.abs(rows[i - 1].price - rows[i].price);
      if (d > 1e-9) tick = Math.min(tick, d);
    }
    if (!Number.isFinite(tick)) return 4;
    const px = Math.abs(y(domain.lo) - y(domain.lo + tick));
    return Math.max(1.5, Math.min(11, px * 0.85));
  };

  const tpoShown = showTpo ? tpoRows.filter((r) => inDomain(r.price)) : [];
  const volShown = showVol ? volRows.filter((r) => inDomain(r.price)) : [];
  const tpoMax = tpoShown.reduce((m, r) => Math.max(m, r.count), 0) || 1;
  const volMax = volShown.reduce((m, r) => Math.max(m, r.volume), 0) || 1;
  const tpoBarH = barGeometry(tpoShown);
  const volBarH = barGeometry(volShown);
  const histSpan = lineEnd - PAD.l - 4;
  const inVa = (p: number) => cur.vah != null && cur.val != null && p >= Math.min(cur.val, cur.vah) && p <= Math.max(cur.val, cur.vah);
  const isPocRow = (p: number) => cur.poc != null && Math.abs(p - cur.poc) < 1e-9;

  return (
    <svg viewBox={`0 0 ${VB_W} ${height}`} className="w-full" style={{ height: "auto", aspectRatio: `${VB_W} / ${height}` }} role="img" aria-label="Market profile level ladder">
      {/* day-range rail */}
      {cur.dayHigh != null && cur.dayLow != null ? (
        <line x1={PAD.l - 5} x2={PAD.l - 5} y1={clampY(cur.dayHigh)} y2={clampY(cur.dayLow)} stroke="rgba(255,255,255,0.18)" strokeWidth={2} strokeLinecap="round" />
      ) : null}

      {/* value-area band */}
      {cur.vah != null && cur.val != null ? (
        <rect x={PAD.l} y={clampY(Math.max(cur.vah, cur.val))} width={lineEnd - PAD.l} height={Math.abs(clampY(cur.val) - clampY(cur.vah))} fill="rgba(59,130,246,0.10)" />
      ) : null}

      {/* IB band */}
      {cur.ibHigh != null && cur.ibLow != null ? (
        <rect x={PAD.l} y={clampY(Math.max(cur.ibHigh, cur.ibLow))} width={lineEnd - PAD.l} height={Math.abs(clampY(cur.ibLow) - clampY(cur.ibHigh))} fill="rgba(255,165,2,0.07)" />
      ) : null}

      {/* TPO letter-count histogram (left-anchored) */}
      {tpoShown.map((r) => (
        <rect
          key={`tpo-${r.price}`}
          x={PAD.l}
          y={y(r.price) - tpoBarH / 2}
          width={Math.max(1.5, (r.count / tpoMax) * histSpan)}
          height={tpoBarH}
          fill={isPocRow(r.price) ? "rgba(255,165,2,0.55)" : inVa(r.price) ? "rgba(96,165,250,0.35)" : "rgba(148,163,184,0.20)"}
          rx={0.8}
        >
          <title>{fmt(r.price)} · {r.count} TPO{r.letters ? ` · ${r.letters}` : ""}</title>
        </rect>
      ))}

      {/* volume-profile overlay (right-anchored, grows leftward) */}
      {volShown.map((r) => {
        const w = Math.max(1.5, (r.volume / volMax) * histSpan * 0.9);
        return (
          <rect key={`vol-${r.price}`} x={lineEnd - w} y={y(r.price) - volBarH / 2} width={w} height={volBarH} fill="rgba(0,212,163,0.30)" rx={0.8}>
            <title>{fmt(r.price)} · vol {formatNumber(r.volume, 0)}</title>
          </rect>
        );
      })}

      {/* prior-session ghost lines */}
      {(
        [
          { p: ghost.vah, label: "pVAH" },
          { p: ghost.poc, label: "pPOC" },
          { p: ghost.val, label: "pVAL" },
        ] as const
      )
        .filter((g) => inDomain(g.p))
        .map((g) => (
          <g key={g.label} opacity={0.75}>
            <line x1={PAD.l} x2={GHOST_X} y1={y(g.p as number)} y2={y(g.p as number)} stroke="#a78bfa" strokeWidth={0.9} strokeDasharray="2 3" />
            <text x={PAD.l + 1} y={y(g.p as number) - 2} fill="#a78bfa" fontSize={7.5}>{g.label} {fmt(g.p as number)}</text>
          </g>
        ))}

      {/* current session reference lines */}
      {(
        [
          { p: cur.ibHigh, c: "rgba(255,165,2,0.75)", label: "IBH", dash: "4 3", w: 0.8 },
          { p: cur.ibLow, c: "rgba(255,165,2,0.75)", label: "IBL", dash: "4 3", w: 0.8 },
          { p: cur.vah, c: "#3b82f6", label: "VAH", dash: "5 3", w: 1 },
          { p: cur.val, c: "#3b82f6", label: "VAL", dash: "5 3", w: 1 },
          { p: cur.poc, c: "#ffa502", label: "POC", dash: undefined, w: 1.4 },
        ] as const
      )
        .filter((l) => inDomain(l.p))
        .map((l) => (
          <g key={l.label}>
            <line x1={PAD.l} x2={lineEnd} y1={y(l.p as number)} y2={y(l.p as number)} stroke={l.c} strokeWidth={l.w} strokeDasharray={l.dash} />
            <text x={lineEnd + 4} y={y(l.p as number) + 2.6} fill={l.c} fontSize={8}>{l.label} {fmt(l.p as number)}</text>
          </g>
        ))}

      {/* HVN dots */}
      {hvns.filter((p) => inDomain(p)).map((p, i) => (
        <g key={`hvn-${i}`}>
          <circle cx={PAD.l + 7} cy={y(p)} r={2.6} fill="#a78bfa" opacity={0.9}>
            <title>HVN {fmt(p)}</title>
          </circle>
        </g>
      ))}

      {/* single-print ticks */}
      {singles.filter((p) => inDomain(p)).map((p, i) => (
        <rect key={`sp-${i}`} x={PAD.l - 2} y={y(p) - 1} width={5} height={2} fill="#ff4757" opacity={0.9}>
          <title>single print {fmt(p)}</title>
        </rect>
      ))}

      {/* poor (unfinished) high / low — drawn only when the lane emitted one */}
      {([{ p: poor.high, label: "poor high" }, { p: poor.low, label: "poor low" }] as const)
        .filter((x) => inDomain(x.p))
        .map((x) => (
          <g key={x.label}>
            <line x1={PAD.l} x2={lineEnd} y1={y(x.p as number)} y2={y(x.p as number)} stroke="#f472b6" strokeWidth={1} strokeDasharray="1 2" />
            <text x={PAD.l + 1} y={y(x.p as number) - 2} fill="#f472b6" fontSize={7}>{x.label}</text>
          </g>
        ))}

      {/* spot marker */}
      {inDomain(cur.spot) ? (
        <g>
          <line x1={PAD.l} x2={lineEnd} y1={y(cur.spot)} y2={y(cur.spot)} stroke={spotColor} strokeWidth={1.1} strokeDasharray="6 2" />
          <polygon points={`${lineEnd},${y(cur.spot)} ${lineEnd + 6},${y(cur.spot) - 3.5} ${lineEnd + 6},${y(cur.spot) + 3.5}`} fill={spotColor} />
          <text x={lineEnd + 8} y={y(cur.spot) + 2.6} fill={spotColor} fontSize={8.5} fontWeight={700}>{fmt(cur.spot)}</text>
        </g>
      ) : null}
    </svg>
  );
}

function LegendSwatch({ color, label, round = false }: { color: string; label: string; round?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={round ? "inline-block h-1.5 w-1.5 rounded-full" : "inline-block h-[3px] w-3 rounded-sm"} style={{ backgroundColor: color }} aria-hidden />
      {label}
    </span>
  );
}
