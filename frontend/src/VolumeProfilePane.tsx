import { useEffect, useId, useMemo, useRef, useState, type CSSProperties } from "react";

/* ------------------------------------------------------------------ *
 * Session volume / market profile drawn against the price axis, the
 * way MotiveWave or Sierra Chart hang a profile beside the chart.
 * Inline SVG so the dense 9px labelling stays crisp at any DPI.
 * ------------------------------------------------------------------ */

export type VolumeProfileLevel = { price: number; tpo: number; letters: string; volume: number };

export type VolumeProfileSession = {
  poc: number | null;
  vah: number | null;
  val: number | null;
  ib_high: number | null;
  ib_low: number | null;
  high: number | null;
  low: number | null;
  last: number | null;
  levels: VolumeProfileLevel[];
  tick_size?: number | null;
  levels_total?: number;
  levels_sampled?: boolean;
  first_bracket?: number | null;
  partial_capture?: boolean;
  brackets?: number;
};

/** A reference price from another session, hung on today's axis. */
export type ProfileOverlay = {
  label: string;
  price: number;
  kind: "pd" | "week" | "month" | "naked" | "composite";
};

export type VolumeProfilePaneProps = {
  profile: VolumeProfileSession | null;
  height: number;
  showTpo: boolean;
  /* Every prop below is optional so the AuctionView caller, which has only a
     stored profile, keeps rendering exactly as it did. */
  overlays?: ProfileOverlay[];
  /* The PRIOR session's finished value area, drawn as a ghost band behind
     today's developing one. Naming matters: today's band is still moving. */
  completedVa?: { vah: number | null; val: number | null; label?: string } | null;
  vpoc?: number | null;
  singlePrints?: number[];
  poorHigh?: boolean;
  poorLow?: boolean;
  tailHigh?: number;
  tailLow?: number;
};

const BG = "#0b1018";
const CHART_BG = "#090d14";
const BORDER = "#202a38";
const BORDER_2 = "#263144";
const TEXT = "#dce5f1";
const DIM = "#8796aa";
const DIMMER = "#687990";
const GRID = "#141d29";
const ACCENT = "#4d91ff";
const AMBER = "#f5b84b";
const GOLD = "#e5c85a";
const BAR_OUT = "#3c4b60";
const SINGLE = "#5f7391";
const NAKED = "#9b8cff";
// Reference levels are coloured by WHERE they came from, not by whether price
// is above or below them: a naked POC and a prior-day VAH are different kinds
// of magnet and a reader has to tell them apart at a glance.
const OVERLAY_COLOUR: Record<ProfileOverlay["kind"], string> = {
  pd: DIM, week: ACCENT, month: GOLD, naked: NAKED, composite: BAR_OUT,
};
// Minimum vertical gap between two overlay labels before one is nudged down.
const OVERLAY_LABEL_H = 9;
// How far outside today's own range a reference may pull the price axis, in
// multiples of that range. naked_pocs walks back 40 sessions and returns up to
// twelve untested POCs at unbounded distance: measured on 2026-09-03,
// NIFTY50's 149.4-point session was stretched to a 943.5-point axis, leaving
// today's profile 16% of the pane with every histogram row floored at 2px and
// overprinting. A reference further away than this is still SHOWN — pinned to
// the edge it lies beyond, with its price — but it no longer sets the scale.
const OVERLAY_STRETCH = 1.0;

const HEADER_H = 43;
const PAD_T = 8;
const PAD_B = 8;
const PAD_L = 6;
const PAD_R = 8;
const IB_W = 15;
const PRICE_W = 42;
const ROW_TARGET = 15;
const MONO_CH = 5.42; // width of one 9px monospace glyph
const SANS_CH = 5.0; // rough width of one 9px sans glyph

const MONO = 'ui-monospace, SFMono-Regular, Menlo, "DejaVu Sans Mono", monospace';
const SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

const px = (v: number): number => Math.round(v) + 0.5;
const labelW = (s: string): number => s.length * SANS_CH + 7;

const fmtPrice = (v: number | null | undefined, dp: number): string =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toFixed(dp);

const fmtQty = (v: number): string => {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e7) return `${(v / 1e6).toFixed(0)}M`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(0)}k`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(Math.round(v));
};

/** Index of the level whose price sits closest to `target`. */
const nearestIndex = (prices: number[], target: number): number => {
  let best = 0;
  let bestD = Number.POSITIVE_INFINITY;
  for (let i = 0; i < prices.length; i++) {
    const d = Math.abs(prices[i] - target);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
};

/**
 * Thin `len` rows down to `count` evenly, but never drop a protected row
 * (POC / VAH / VAL / session high / low) — losing those misrepresents the
 * auction. A protected row displaces the nearest ordinary pick so the row
 * budget, and therefore the pane height, is still respected.
 */
const sampleRows = (len: number, count: number, keep: Set<number>): number[] => {
  if (len <= count) return Array.from({ length: len }, (_, i) => i);
  const budget = Math.max(count, keep.size);
  const denom = budget > 1 ? budget - 1 : 1;
  const picked: number[] = [];
  for (let i = 0; i < budget; i++) {
    const idx = Math.round((i * (len - 1)) / denom);
    if (!picked.includes(idx)) picked.push(idx);
  }
  keep.forEach((k) => {
    if (k < 0 || k >= len || picked.includes(k)) return;
    let slot = -1;
    let slotD = Number.POSITIVE_INFINITY;
    for (let i = 0; i < picked.length; i++) {
      if (keep.has(picked[i])) continue;
      const d = Math.abs(picked[i] - k);
      if (d < slotD) {
        slotD = d;
        slot = i;
      }
    }
    if (slot >= 0) picked[slot] = k;
    else picked.push(k);
  });
  return Array.from(new Set(picked)).sort((a, b) => a - b);
};

/**
 * Overlay lines sit where their price says, but their labels are nudged apart
 * so a cluster of references (pd_vah, week_poc, a naked POC within a few ticks
 * of each other) stays legible instead of overprinting into a smear. The LINE
 * never moves — only the text beside it.
 */
const placeOverlays = (
  overlays: ProfileOverlay[] | undefined,
  plot: Pick<Plot, "yFor" | "svgH" | "top" | "bottom" | "pMin" | "pMax">,
): (ProfileOverlay & { y: number; labelY: number; offscale: -1 | 0 | 1 })[] => {
  const rows = (overlays || [])
    .filter((o) => Number.isFinite(o.price))
    .map((o) => {
      // A reference outside the drawn span is clamped to the edge it lies
      // beyond and marked, rather than being dropped (a reader loses a level
      // that exists) or being allowed to set the scale (everyone loses the
      // profile). The label carries its real price either way.
      const offscale: -1 | 0 | 1 = o.price > plot.pMax ? 1 : o.price < plot.pMin ? -1 : 0;
      const y = offscale === 1 ? plot.top : offscale === -1 ? plot.bottom : plot.yFor(o.price);
      return { ...o, y, labelY: y - 2, offscale };
    })
    .sort((a, b) => a.y - b.y);
  let floor = Number.NEGATIVE_INFINITY;
  for (const row of rows) {
    row.labelY = Math.max(row.labelY, floor + OVERLAY_LABEL_H);
    floor = row.labelY;
  }
  return rows.filter((row) => row.labelY < plot.svgH);
};

/** The "=" mark of an extreme with no excess, at the row that made it. */
function PoorExtreme({ y, histX, width, label }: {
  y: number; histX: number; width: number; label: string;
}) {
  return (
    <g stroke={AMBER} strokeOpacity={0.9}>
      <line x1={px(histX + 2)} y1={px(y - 1.5)} x2={px(histX + 14)} y2={px(y - 1.5)} />
      <line x1={px(histX + 2)} y1={px(y + 1.5)} x2={px(histX + 14)} y2={px(y + 1.5)} />
      <line x1={px(histX + 14)} y1={px(y)} x2={px(width - PAD_R)} y2={px(y)} strokeOpacity={0.3} strokeDasharray="1 4" />
      <title>{`${label} — no excess, revisit likely`}</title>
    </g>
  );
}

/** Count of single-print rows at an extreme, labelled at that extreme. */
function TailMark({ y, x, rows, label, dy }: {
  y: number; x: number; rows: number; label: string; dy: number;
}) {
  return (
    <text x={x} y={y + dy} fontFamily={SANS} fontSize={8} fill={SINGLE} letterSpacing={0.3}>
      {`tail ${rows}`}
      <title>{label}</title>
    </text>
  );
}

type PlotRow = {
  price: number;
  letters: string;
  frac: number; // 0..1 share of the largest level
  volume: number;
  tpo: number;
  y: number;
  inVa: boolean;
  isPoc: boolean;
};

type Plot = {
  svgH: number;
  top: number;                 // y of pMax
  bottom: number;              // y of pMin
  pMin: number;
  pMax: number;
  dp: number;
  rows: PlotRow[];
  rowH: number;
  labelStep: number;
  histX: number;
  histW: number;
  barMax: number;
  tpoX: number;
  tpoW: number;
  priceRight: number;
  showVolText: boolean;
  yFor: (p: number) => number;
  vaTop: number | null;
  vaBottom: number | null;
};

export function VolumeProfilePane({
  profile, height, showTpo, overlays, completedVa, vpoc,
  singlePrints, poorHigh, poorLow, tailHigh, tailLow,
}: VolumeProfilePaneProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const partialCapture = profile?.partial_capture === true || (profile?.first_bracket ?? 0) > 0;

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      setWidth(entry ? entry.contentRect.width : el.clientWidth);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const plot = useMemo<Plot | null>(() => {
    if (!profile || profile.levels.length === 0 || width < 60) return null;

    const src = profile.levels.slice().sort((a, b) => b.price - a.price);
    const prices = src.map((l) => l.price);

    // A compact API payload may skip exchange ticks. Use the published tick
    // for price precision and value-area matching; observed row spacing is
    // only a fallback for older payloads.
    const gaps: number[] = [];
    for (let i = 1; i < prices.length; i++) {
      const d = prices[i - 1] - prices[i];
      if (d > 0) gaps.push(d);
    }
    gaps.sort((a, b) => a - b);
    const tick = profile.tick_size && profile.tick_size > 0
      ? profile.tick_size : gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0.05;
    const dp = tick >= 5 ? 0 : tick >= 0.5 ? 1 : 2;

    const svgH = Math.max(72, height - HEADER_H);
    const plotTop = PAD_T;
    const plotH = Math.max(36, svgH - PAD_T - PAD_B);

    // ---- price scale --------------------------------------------------
    let pMax = prices[0];
    let pMin = prices[prices.length - 1];
    const stretch = (v: number | null) => {
      if (v === null || !Number.isFinite(v)) return;
      if (v > pMax) pMax = v;
      if (v < pMin) pMin = v;
    };
    stretch(profile.high);
    stretch(profile.low);
    stretch(profile.ib_high);
    stretch(profile.ib_low);
    stretch(completedVa?.vah ?? null);
    stretch(completedVa?.val ?? null);
    stretch(vpoc ?? null);
    // Today's own span, fixed BEFORE any foreign reference is consulted. A
    // prior-day VAH just above today's range is exactly the level a reader
    // wants to see approaching, so it is worth axis room; a naked POC 800
    // points away is not, and letting it in collapses the session into a
    // sliver. Anything past the bound is drawn at the edge instead.
    const ownSpan = Math.max(pMax - pMin, tick);
    const ceiling = pMax + ownSpan * OVERLAY_STRETCH;
    const floorPrice = pMin - ownSpan * OVERLAY_STRETCH;
    for (const overlay of overlays || []) {
      if (!Number.isFinite(overlay.price)) continue;
      if (overlay.price <= ceiling && overlay.price >= floorPrice) stretch(overlay.price);
    }
    pMax += tick / 2;
    pMin -= tick / 2;
    const span = pMax - pMin || tick || 1;
    const yFor = (p: number): number => plotTop + ((pMax - p) / span) * plotH;

    // ---- row thinning --------------------------------------------------
    const maxRows = Math.max(3, Math.floor(plotH / ROW_TARGET));
    const keep = new Set<number>([0, src.length - 1]);
    for (const anchor of [profile.poc, profile.vah, profile.val, profile.high, profile.low]) {
      if (anchor !== null && Number.isFinite(anchor)) keep.add(nearestIndex(prices, anchor));
    }
    const picked = sampleRows(src.length, maxRows, keep);

    const totalVol = src.reduce((s, l) => s + (Number.isFinite(l.volume) ? l.volume : 0), 0);
    // This pane is labelled volume. A touched quote row with no classified
    // trade volume must stay blank; substituting a TPO count drew fake size.
    const valueOf = (l: VolumeProfileLevel): number => Math.max(0, Number.isFinite(l.volume) ? l.volume : 0);
    const maxValue = src.reduce((m, l) => Math.max(m, valueOf(l)), 0) || 1;

    const step = src.length / picked.length;
    const tickH = (tick / span) * plotH;
    const rowH = Math.max(2, Math.min(step * tickH - 1, 26));
    const spacing = plotH / picked.length;

    const vah = profile.vah;
    const val = profile.val;
    const poc = profile.poc;
    const eps = tick / 2;
    const hasVa = vah !== null && val !== null && Number.isFinite(vah) && Number.isFinite(val);

    const rows: PlotRow[] = picked.map((i) => {
      const l = src[i];
      return {
        price: l.price,
        letters: l.letters || "",
        frac: valueOf(l) / maxValue,
        volume: Number.isFinite(l.volume) ? l.volume : 0,
        tpo: Number.isFinite(l.tpo) ? l.tpo : 0,
        y: yFor(l.price),
        inVa: hasVa ? l.price <= (vah as number) + eps && l.price >= (val as number) - eps : false,
        // A sampled source can omit the POC row. Never put a POC badge on the
        // nearest different price simply because the sampling gap is wide.
        isPoc: poc !== null && Number.isFinite(poc) ? Math.abs(l.price - poc) < 1e-6 : false,
      };
    });

    // ---- horizontal layout ---------------------------------------------
    const longest = showTpo ? src.reduce((m, l) => Math.max(m, (l.letters || "").length), 0) : 0;
    const tpoW = showTpo
      ? Math.max(20, Math.min(Math.round(longest * MONO_CH) + 5, Math.floor(width * 0.42)))
      : 0;
    const tpoX = PAD_L + IB_W;
    const priceRight = tpoX + tpoW + PRICE_W;
    const histX = priceRight + 6;
    const histW = Math.max(24, width - PAD_R - histX);
    const showVolText = totalVol > 0 && histW > 130 && spacing >= 13;
    const barMax = Math.max(16, histW - (showVolText ? 46 : 4));

    return {
      svgH,
      top: plotTop,
      bottom: plotTop + plotH,
      pMin,
      pMax,
      dp,
      rows,
      rowH,
      labelStep: Math.max(1, Math.ceil(12 / spacing)),
      histX,
      histW,
      barMax,
      tpoX,
      tpoW,
      priceRight,
      showVolText,
      yFor,
      vaTop: vah !== null && Number.isFinite(vah) ? yFor(vah) - rowH / 2 : null,
      vaBottom: val !== null && Number.isFinite(val) ? yFor(val) + rowH / 2 : null,
    };
  }, [profile, height, width, showTpo, overlays, completedVa, vpoc]);

  const shell: CSSProperties = {
    height,
    background: BG,
    border: `1px solid ${BORDER}`,
    borderRadius: 2,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    fontFamily: SANS,
    fontVariantNumeric: "tabular-nums",
    boxSizing: "border-box",
  };

  if (!profile || profile.levels.length === 0) {
    return (
      <div ref={hostRef} style={shell}>
        <ProfileHeader profile={profile} dp={2} visibleRows={0} />
        <div
          style={{
            flex: 1,
            background: CHART_BG,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: DIMMER,
            fontSize: 10,
            letterSpacing: 0.4,
          }}
        >
          {profile ? "PROFILE EMPTY" : "NO SESSION PROFILE"}
        </div>
      </div>
    );
  }

  const dp = plot ? plot.dp : 2;
  const poc = profile.poc;
  const vah = profile.vah;
  const val = profile.val;
  const ibH = profile.ib_high;
  const ibL = profile.ib_low;
  const hasIb = ibH !== null && ibL !== null && Number.isFinite(ibH) && Number.isFinite(ibL);

  return (
    <div ref={hostRef} style={shell}>
      <ProfileHeader profile={profile} dp={dp} visibleRows={plot?.rows.length ?? 0} />
      <div style={{ flex: 1, background: CHART_BG, overflow: "hidden" }}>
        {plot ? (
          <svg
            width={width}
            height={plot.svgH}
            viewBox={`0 0 ${width} ${plot.svgH}`}
            style={{ display: "block", shapeRendering: "crispEdges" }}
          >
            <defs>
              <clipPath id={`vpTpo${uid}`}>
                <rect x={plot.tpoX} y={0} width={Math.max(0, plot.tpoW - 3)} height={plot.svgH} />
              </clipPath>
            </defs>

            {/* value-area band */}
            {plot.vaTop !== null && plot.vaBottom !== null ? (
              <rect
                x={PAD_L}
                y={Math.min(plot.vaTop, plot.vaBottom)}
                width={Math.max(0, width - PAD_L - PAD_R)}
                height={Math.max(1, Math.abs(plot.vaBottom - plot.vaTop))}
                fill={ACCENT}
                fillOpacity={0.07}
              />
            ) : null}

            {/* prior session's COMPLETED value area, behind today's developing
                one. Drawn as an outline, never a fill: two solid bands read as
                one wide band, and the whole point is where they do not overlap. */}
            {completedVa && Number.isFinite(completedVa.vah) && Number.isFinite(completedVa.val) ? (
              <g>
                <rect
                  x={PAD_L}
                  y={Math.min(plot.yFor(completedVa.vah as number), plot.yFor(completedVa.val as number))}
                  width={Math.max(0, width - PAD_L - PAD_R)}
                  height={Math.max(1, Math.abs(plot.yFor(completedVa.val as number) - plot.yFor(completedVa.vah as number)))}
                  fill="none"
                  stroke={DIM}
                  strokeDasharray="2 4"
                  strokeOpacity={0.6}
                >
                  <title>{`${completedVa.label || "prior session"} value area (completed) ${fmtPrice(completedVa.val, dp)}–${fmtPrice(completedVa.vah, dp)}`}</title>
                </rect>
                <text
                  x={PAD_L + 2}
                  y={Math.min(plot.yFor(completedVa.vah as number), plot.yFor(completedVa.val as number)) + 8}
                  fontFamily={SANS}
                  fontSize={8}
                  fill={DIMMER}
                  letterSpacing={0.3}
                >
                  {completedVa.label || "PD VA"}
                </text>
              </g>
            ) : null}

            {/* histogram baseline */}
            <line
              x1={px(plot.histX)}
              y1={PAD_T - 3}
              x2={px(plot.histX)}
              y2={plot.svgH - PAD_B + 3}
              stroke={BORDER_2}
            />

            {plot.rows.map((r, i) => {
              const ruled = i % plot.labelStep === 0;
              const barW = r.frac > 0 ? Math.max(1, r.frac * plot.barMax) : 0;
              return (
                <g key={`${r.price}-${i}`}>
                  {ruled ? (
                    <line
                      x1={px(plot.histX)}
                      y1={px(r.y)}
                      x2={px(plot.histX + plot.histW)}
                      y2={px(r.y)}
                      stroke={GRID}
                      strokeOpacity={0.6}
                    />
                  ) : null}
                  <rect
                    x={plot.histX + 1}
                    y={r.y - plot.rowH / 2}
                    width={barW}
                    height={Math.max(1.5, plot.rowH)}
                    fill={r.isPoc ? GOLD : r.inVa ? ACCENT : BAR_OUT}
                    fillOpacity={r.isPoc ? 0.92 : r.inVa ? 0.72 : 0.85}
                  >
                    <title>{`${r.price.toFixed(dp)}   vol ${fmtQty(r.volume)}   tpo ${r.tpo}${
                      r.isPoc ? "   POC" : ""
                    }`}</title>
                  </rect>
                  {showTpo && r.letters ? (
                    <text
                      x={plot.tpoX}
                      y={r.y + 3}
                      clipPath={`url(#vpTpo${uid})`}
                      fontFamily={MONO}
                      fontSize={9}
                      fill={r.isPoc ? GOLD : r.inVa ? DIM : DIMMER}
                      style={{ whiteSpace: "pre" }}
                    >
                      {r.letters}
                    </text>
                  ) : null}
                  {ruled || r.isPoc ? (
                    <text
                      x={plot.priceRight - 4}
                      y={r.y + 3}
                      textAnchor="end"
                      fontFamily={SANS}
                      fontSize={9}
                      fill={r.isPoc ? GOLD : r.inVa ? TEXT : DIMMER}
                      style={{ fontVariantNumeric: "tabular-nums" }}
                    >
                      {r.price.toFixed(dp)}
                    </text>
                  ) : null}
                  {plot.showVolText ? (
                    <text
                      x={plot.histX + plot.histW - 2}
                      y={r.y + 3}
                      textAnchor="end"
                      fontFamily={SANS}
                      fontSize={9}
                      fill={r.isPoc ? GOLD : DIMMER}
                      style={{ fontVariantNumeric: "tabular-nums" }}
                    >
                      {r.volume > 0 ? fmtQty(r.volume) : ""}
                    </text>
                  ) : null}
                </g>
              );
            })}

            {/* initial balance bracket on the left edge */}
            {hasIb ? (
              <g stroke={AMBER} strokeOpacity={0.85} fill="none">
                <line
                  x1={px(PAD_L + 4)}
                  y1={px(plot.yFor(ibH as number))}
                  x2={px(PAD_L + 4)}
                  y2={px(plot.yFor(ibL as number))}
                  strokeDasharray="3 3"
                />
                <line
                  x1={px(PAD_L + 4)}
                  y1={px(plot.yFor(ibH as number))}
                  x2={px(PAD_L + 11)}
                  y2={px(plot.yFor(ibH as number))}
                />
                <line
                  x1={px(PAD_L + 4)}
                  y1={px(plot.yFor(ibL as number))}
                  x2={px(PAD_L + 11)}
                  y2={px(plot.yFor(ibL as number))}
                />
                <text
                  x={PAD_L + 3}
                  y={(plot.yFor(ibH as number) + plot.yFor(ibL as number)) / 2}
                  transform={`rotate(-90 ${PAD_L + 3} ${
                    (plot.yFor(ibH as number) + plot.yFor(ibL as number)) / 2
                  })`}
                  textAnchor="middle"
                  fontFamily={SANS}
                  fontSize={9}
                  fill={AMBER}
                  stroke="none"
                  letterSpacing={0.6}
                >
                  IB
                </text>
              </g>
            ) : null}

            {/* value-area edges */}
            {vah !== null && Number.isFinite(vah) ? (
              <EdgeLine label={`VAH ${fmtPrice(vah, dp)}`} y={plot.yFor(vah)} width={width} histX={plot.histX} dy={-3} />
            ) : null}
            {val !== null && Number.isFinite(val) ? (
              <EdgeLine label={`VAL ${fmtPrice(val, dp)}`} y={plot.yFor(val)} width={width} histX={plot.histX} dy={10} />
            ) : null}

            {/* point of control */}
            {poc !== null && Number.isFinite(poc) ? (
              <g>
                <line
                  x1={px(PAD_L)}
                  y1={px(plot.yFor(poc))}
                  x2={px(width - PAD_R)}
                  y2={px(plot.yFor(poc))}
                  stroke={GOLD}
                />
                <rect
                  x={plot.histX + 3}
                  y={plot.yFor(poc) - 11}
                  width={labelW(`POC ${fmtPrice(poc, dp)}`)}
                  height={11}
                  fill={CHART_BG}
                  fillOpacity={0.88}
                />
                <text
                  x={plot.histX + 6}
                  y={plot.yFor(poc) - 3}
                  fontFamily={SANS}
                  fontSize={9}
                  fill={GOLD}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {`POC ${fmtPrice(poc, dp)}`}
                </text>
              </g>
            ) : null}

            {/* volume point of control, only when it is not the TPO POC. Where
                the two part company the session spent its TIME somewhere other
                than where the SIZE traded, which is the reading; drawing a
                second line on top of the first would just thicken it. */}
            {vpoc !== null && vpoc !== undefined && Number.isFinite(vpoc)
              && !(poc !== null && Number.isFinite(poc) && Math.abs(vpoc - (poc as number)) < 1e-9) ? (
              <g>
                <line
                  x1={px(plot.histX)}
                  y1={px(plot.yFor(vpoc))}
                  x2={px(width - PAD_R)}
                  y2={px(plot.yFor(vpoc))}
                  stroke={GOLD}
                  strokeOpacity={0.7}
                  strokeDasharray="5 3"
                />
                <text
                  x={plot.histX + 6}
                  y={plot.yFor(vpoc) + 9}
                  fontFamily={SANS}
                  fontSize={9}
                  fill={GOLD}
                  fillOpacity={0.85}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {`VPOC ${fmtPrice(vpoc, dp)}`}
                </text>
              </g>
            ) : null}

            {/* single prints: the trace of a move fast enough that one bracket
                was the only one to trade the row. Marked in the left gutter so
                the histogram bars stay readable. */}
            {!partialCapture && (profile.brackets ?? 0) >= 2 && (singlePrints || []).map((p) => (
              <line
                key={`sp${p}`}
                x1={px(PAD_L + 12)}
                y1={px(plot.yFor(p))}
                x2={px(PAD_L + 15)}
                y2={px(plot.yFor(p))}
                stroke={SINGLE}
                strokeWidth={2}
              >
                <title>{`single print ${fmtPrice(p, dp)} — one bracket only`}</title>
              </line>
            ))}

            {/* poor high / poor low: two or more TPOs on the extreme row, so
                the auction stopped there rather than being rejected. Dalton's
                reading is that an extreme with no excess gets revisited. */}
            {!partialCapture && poorHigh && profile.high !== null && Number.isFinite(profile.high) ? (
              <PoorExtreme y={plot.yFor(profile.high)} histX={plot.histX} width={width}
                label={`poor high ${fmtPrice(profile.high, dp)}`} />
            ) : null}
            {!partialCapture && poorLow && profile.low !== null && Number.isFinite(profile.low) ? (
              <PoorExtreme y={plot.yFor(profile.low)} histX={plot.histX} width={width}
                label={`poor low ${fmtPrice(profile.low, dp)}`} />
            ) : null}

            {/* the opposite shape: a run of single-print rows at an extreme,
                which is a rejection rather than a stall. Counted, because a
                two-row tail and a nine-row one are not the same event. */}
            {!partialCapture && (tailHigh || 0) >= 2 && profile.high !== null && Number.isFinite(profile.high) ? (
              <TailMark y={plot.yFor(profile.high)} x={plot.histX + 3} rows={tailHigh as number}
                label={`buying tail ${tailHigh} rows from ${fmtPrice(profile.high, dp)}`} dy={-3} />
            ) : null}
            {!partialCapture && (tailLow || 0) >= 2 && profile.low !== null && Number.isFinite(profile.low) ? (
              <TailMark y={plot.yFor(profile.low)} x={plot.histX + 3} rows={tailLow as number}
                label={`selling tail ${tailLow} rows from ${fmtPrice(profile.low, dp)}`} dy={9} />
            ) : null}

            {/* reference levels from other sessions, laid on today's axis */}
            {placeOverlays(overlays, plot).map((o) => (
              <g key={`${o.kind}-${o.label}-${o.price}`}>
                <line
                  x1={px(plot.histX)}
                  y1={px(o.y)}
                  x2={px(width - PAD_R)}
                  y2={px(o.y)}
                  stroke={OVERLAY_COLOUR[o.kind]}
                  strokeOpacity={o.offscale ? 0.3 : 0.55}
                  strokeDasharray={o.offscale ? "1 5" : "1 3"}
                />
                <text
                  x={width - PAD_R - 3}
                  y={o.labelY}
                  textAnchor="end"
                  fontFamily={SANS}
                  fontSize={8}
                  fill={OVERLAY_COLOUR[o.kind]}
                  fillOpacity={o.offscale ? 0.65 : 0.9}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {/* The arrow says the LINE is at the pane edge and the level
                      is not: without it a clamped reference reads as a level
                      sitting exactly on today's high. */}
                  {`${o.offscale === 1 ? "\u2191 " : o.offscale === -1 ? "\u2193 " : ""}${o.label} ${fmtPrice(o.price, dp)}`}
                  <title>{o.offscale
                    ? `${o.label} ${fmtPrice(o.price, dp)} — off scale, ${o.offscale === 1 ? "above" : "below"} the drawn range`
                    : `${o.label} ${fmtPrice(o.price, dp)}`}</title>
                </text>
              </g>
            ))}
          </svg>
        ) : null}
      </div>
    </div>
  );
}

/** Dashed value-area boundary with a punched-through label on the right. */
function EdgeLine({
  label,
  y,
  width,
  histX,
  dy,
}: {
  label: string;
  y: number;
  width: number;
  histX: number;
  dy: number;
}) {
  const boxW = labelW(label);
  return (
    <g>
      <line
        x1={px(histX)}
        y1={px(y)}
        x2={px(width - PAD_R)}
        y2={px(y)}
        stroke={DIM}
        strokeOpacity={0.85}
        strokeDasharray="3 3"
      />
      <rect x={width - PAD_R - boxW} y={y + dy - 8} width={boxW} height={11} fill={CHART_BG} fillOpacity={0.88} />
      <text
        x={width - PAD_R - 3}
        y={y + dy}
        textAnchor="end"
        fontFamily={SANS}
        fontSize={9}
        fill={DIM}
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {label}
      </text>
    </g>
  );
}

function ProfileHeader({ profile, dp, visibleRows }: { profile: VolumeProfileSession | null; dp: number; visibleRows: number }) {
  const cell: CSSProperties = { display: "flex", gap: 4, alignItems: "baseline", whiteSpace: "nowrap" };
  const tag: CSSProperties = { color: DIMMER, fontSize: 9, letterSpacing: 0.4 };
  const num: CSSProperties = { fontSize: 10, color: TEXT, fontVariantNumeric: "tabular-nums" };
  const sourceRows = profile?.levels.length ?? 0;
  const totalRows = profile?.levels_total ?? sourceRows;
  const sourceSampled = profile?.levels_sampled === true || totalRows > sourceRows;
  const partialCapture = profile?.partial_capture === true || (profile?.first_bracket ?? 0) > 0;
  const detail = `Overview: ${visibleRows}/${sourceRows} source price rows visible; ${sourceSampled ? `${sourceRows}/${totalRows} source rows sampled` : "full captured source"}${partialCapture ? "; partial session capture" : ""}. Open Auction for the scrollable tick ladder.`;
  return (
    <div
      style={{
        height: HEADER_H,
        minHeight: HEADER_H,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 3,
        padding: "2px 7px",
        borderBottom: `1px solid ${BORDER_2}`,
        background: BG,
        overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, overflow: "hidden" }}>
      <div style={cell}>
        <span style={tag}>{partialCapture ? "CAP POC" : "POC"}</span>
        <span style={{ ...num, color: GOLD }}>{fmtPrice(profile?.poc, dp)}</span>
      </div>
      <div style={cell}>
        <span style={tag}>VAH</span>
        <span style={num}>{fmtPrice(profile?.vah, dp)}</span>
      </div>
      <div style={cell}>
        <span style={tag}>VAL</span>
        <span style={num}>{fmtPrice(profile?.val, dp)}</span>
      </div>
      <div style={cell}>
        <span style={tag}>H</span>
        <span style={{ ...num, color: DIM }}>{fmtPrice(profile?.high, dp)}</span>
      </div>
      <div style={cell}>
        <span style={tag}>L</span>
        <span style={{ ...num, color: DIM }}>{fmtPrice(profile?.low, dp)}</span>
      </div>
      <div style={{ ...cell, marginLeft: "auto" }}>
        <span style={tag}>LAST</span>
        <span style={num}>{fmtPrice(profile?.last, dp)}</span>
      </div>
      </div>
      <div title={detail} style={{ color: sourceSampled || partialCapture ? AMBER : DIM, fontSize: 9,
        lineHeight: "12px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {`OVERVIEW · ${visibleRows}/${sourceRows} drawn · ${sourceSampled ? `source sampled ${sourceRows}/${totalRows}` : "full captured source"}${partialCapture ? " · partial session" : ""} · Auction has tick ladder`}
      </div>
    </div>
  );
}
