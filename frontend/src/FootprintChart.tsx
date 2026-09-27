import { useCallback, useEffect, useRef } from "react";
import type { CompositeProfile, MarketContext, ReplayMeta, SessionInfo } from "./types";
import { footprintColumns, priceRangesOverlap, priceViewport } from "./footprintViewport";

/* ------------------------------------------------------------------ types */

/* Everything the 2026-08-27 footprint spec adds is OPTIONAL here. An older API
   that publishes none of it renders the old chart; it never yields NaN, and a
   quantity the server did not measure is drawn as nothing, never as zero. */

export type FootprintLevel = {
  p: number;                      // price of the row
  bid: number;                    // volume traded at the bid (seller hit the bid)  INFERRED
  ask: number;                    // volume traded at the ask (buyer lifted offer)  INFERRED
  d: number;                      // ask - bid at this price                        INFERRED
  imb: "buy" | "sell" | null;     // dominant diagonal imbalance (legacy field)     INFERRED
  poc: boolean;                   // point of control for this bar                  EXACT
  /* spec_version 1 */
  k?: number;                     // integer row index on the absolute grid
  u?: number;                     // unclassified volume at this row                EXACT
  imb_buy?: boolean;              // the two diagonals are independent tests
  imb_sell?: boolean;
  imb_ratio?: number | null;      // null when the neighbour row is empty
  imb_edge?: boolean;             // neighbour row empty: the ratio is unbounded
  va?: boolean;                   // inside the bar's value area                    EXACT
  lvn?: boolean;                  // low-volume node                                EXACT
};

export type FootprintStack = {
  side: "buy" | "sell";
  k_from?: number; k_to?: number; rows?: number;
  from: number; to: number; extreme: number;
  volume?: number;
  suppressed?: boolean;
  reason?: string | null;
};

// "one_sided": the extreme traded on ONE side only, but not the side that
// would finish the auction (a high with zero bid, a low with zero ask). It is
// neither finished nor unfinished — the drawing code renders only the first
// two states, so this correctly falls through to nothing.
export type UnfinishedState = "unfinished" | "weak" | "finished" | "one_sided";

export type FootprintUnfinished = {
  high?: UnfinishedState | null;
  low?: UnfinishedState | null;
  high_price?: number | null;
  low_price?: number | null;
  floor?: number | null;          // volume the minority side had to clear
  high_minority?: number | null;  // the minority side's volume at the extreme
  low_minority?: number | null;
};

export type FootprintAbsorption = {
  k?: number;
  price: number;
  side: string;                   // "buyers_absorbed" | "sellers_absorbed"
  volume?: number;
  pressure?: number | null;
  suppressed?: boolean;
  reason?: string | null;
};

export type FootprintExhaustion = {
  end: "high" | "low";
  price: number;
  volume?: number;
  ratio?: number | null;
  volume_test?: boolean;
  auction_test?: boolean | null;
  // The marker's own verdict: volume leg AND auction leg. The object is
  // published whenever the VOLUME leg passes, so presence is not detection —
  // anything that draws must test this, not the object.
  detected?: boolean;
  suppressed?: boolean;
  reason?: string | null;
};

export type FootprintLvnZone = {
  k_from?: number; k_to?: number; rows?: number;
  from: number; to: number; volume?: number;
};

export type FootprintMethods = {
  quote?: number; mid?: number; tick?: number; zero_tick?: number;
  // The three-vote classifier's own verdicts (orderflow.classify_update):
  // "pending" is the resting-quantity rule alone, "conflict" is the quote rule
  // over a disagreeing vote. Optional so an older API renders unchanged.
  pending?: number; conflict?: number; unknown?: number;
};

/* A freeze-size or 10x-median print, published on the bar it landed in.
   "s" is a CLUSTER — the cumulative-volume delta between two updates — so a
   freeze-size hit is a cluster equal to the exchange freeze quantity, which
   is what a slicer's child order looks like once the feed has batched it. */
export type FootprintMarkedPrint = {
  t: number; p: number; s: number; side: number;
  kind: "freeze" | "large"; lots?: number; ratio?: number;
};

/* Level-1 order-flow imbalance, from the book rather than the tape. Its
   series shares the footprint's bucket starts, so the two line up column for
   column; `cum` is the running total from the first bucket captured. */
export type FootprintOfi = {
  symbol: string; cumulative: number; events: number;
  ofi_60s: number; ofi_300s: number;
  normalised_60s: number | null; normalised_300s: number | null;
  depth_scale: number | null; since: number | null;
  series?: { t: number; ofi: number; cum: number; events: number }[];
};

export type FootprintTapeSpeed = {
  window_seconds: number; updates_per_s: number; qty_per_s: number;
  updates_pct: number | null; qty_pct: number | null; samples: number;
};

export type FootprintBar = {
  t: number;                      // epoch seconds, bar open
  o: number; h: number; l: number; c: number;
  v: number;
  delta: number;
  cvd: number;
  poc: number;
  levels: FootprintLevel[];       // sorted high price first
  /* spec_version 1 */
  u?: number;                     // unclassified volume in the bar
  vah?: number | null;
  val?: number | null;
  va_share?: number | null;
  va_method?: string | null;
  imb_floor?: number | null;
  rows?: number;
  methods?: FootprintMethods | null;
  classified_share?: number | null;
  trades?: number;
  stacks?: FootprintStack[] | null;
  unfinished?: FootprintUnfinished | null;
  absorption?: FootprintAbsorption[] | null;
  exhaustion?: FootprintExhaustion | null;
  lvn?: FootprintLvnZone[] | null;
  levels_available?: boolean;     // false on a backfilled bar: no ladder exists
  /* confidence-weighted leg + tick-level whale shapes */
  wdelta?: number;                // Σ side·size·confidence over the bar        INFERRED
  wcvd?: number;                  // its session running total                  INFERRED
  // null (never 0) when no print in the bar carried a score: zero would read
  // as "every vote failed", a claim about prints nobody scored.
  confidence?: number | null;
  low_confidence?: boolean;
  marked_prints?: FootprintMarkedPrint[];
};

export type FootprintDom = {
  bid: number; ask: number; bid_qty: number; ask_qty: number;
  total_buy_qty: number; total_sell_qty: number; last: number;
  oi: number; avg_trade_price: number;
};

export type FootprintTrade = { t: number; p: number; s: number; side: number };

export type FootprintProfileLevel = { price: number; tpo: number; letters: string; volume: number };

export type FootprintProfile = {
  poc: number; vah: number; val: number; ib_high: number; ib_low: number;
  high: number; low: number; last: number; levels: FootprintProfileLevel[];
  levels_total?: number; levels_returned?: number; levels_sampled?: boolean;
  single_prints_total?: number; single_prints_sampled?: boolean;
  tick_size?: number | null;
  /* ladder extras — optional so an older API renders the plain profile */
  vpoc?: number | null;           // volume point of control, apart from the TPO one
  single_prints?: number[];
  poor_high?: boolean | null; poor_low?: boolean | null;
  tail_high?: number; tail_low?: number;
  extension?: { up: number | null; down: number | null; ratio: number | null } | null;
  // The developing value area's history; the last row is today's, and the
  // COMPLETED one belongs to the prior session (context.prior_day).
  va_by_bracket?: { bracket: number; letter: string; poc: number | null; vah: number | null; val: number | null }[];
};

export type FootprintFlow = {
  cumulative_delta: number; imbalance: number; buy_volume: number; sell_volume: number;
  // Coverage — optional so an older API simply renders no confidence stats
  // rather than "NaN%".
  // The wire sends null when a share was never measured, so the type says so.
  // It previously read `number | undefined`, which let a consumer write
  // `share.toFixed(1)` after an `undefined` check and still hit a runtime null.
  // Every read here goes through finite(), which rejects both.
  total_volume?: number; unclassified_volume?: number;
  classified_share?: number | null; quote_share?: number | null;
  depth_ticks?: number | null; depth_share?: number | null;
  trades?: number; methods?: FootprintMethods | null;
  normalised?: FootprintNormalised | null;
  absorption: { detected: boolean; side: string | null };
  divergence: { detected: boolean; kind: string | null };
  // Each print weighted by how much the three classification votes agreed.
  // Where this parts company with cumulative_delta the tape was batched or
  // contested — exactly where the raw figure deserves least trust.
  weighted_cumulative_delta?: number;
  mean_confidence?: number | null;
  tape_speed?: FootprintTapeSpeed | null;
};

export type FootprintConfidence = {
  classified_share?: number | null;
  window_classified_share?: number | null;
  grade_basis?: string | null;
  quote_share?: number | null;
  depth_share?: number | null;
  method_mix?: FootprintMethods | null;
  grade?: "high" | "fair" | "low" | null;
  avg_spread_ticks?: number | null;
};

export type FootprintDivergenceBars = {
  kind?: "bearish" | "bullish" | null;
  at_bar?: number; reference_bar?: number;
  price_extreme?: number; reference_price_extreme?: number;
  cvd_at_extreme?: number; reference_cvd_extreme?: number;
  suppressed?: boolean;
  reason?: string | null;
};

/* Per-symbol normalisation, exactly as `orderflow.snapshot()["normalised"]`
   publishes it (src/macd_trader/of_normalise.py). The server applies every
   floor — baseline sample size, baseline age, reading age, print count, and a
   0.50 classified-share gate on the DIRECTIONAL outputs — and returns null
   when nothing survives. The chart never re-derives past a floor: if this
   block is absent, or `directional_withheld` is set, no normalised number is
   drawn at all. */
export type FootprintNormBaseline = {
  as_of?: string | null; age_days?: number | null; instrument?: string | null;
  estimator?: string | null; estimator_current?: boolean;
  volume_median?: number | null; trades_median?: number | null;
  nd_median?: number | null; nd_sigma?: number | null;
  delta_mean?: number | null; delta_sd?: number | null;
  ret_sd?: number | null; spread_bps_median?: number | null;
};

export type FootprintNormalised = {
  window_seconds?: number;
  stale?: boolean;
  window_partial?: boolean;
  rvol?: number | null;
  trade_rvol?: number | null;
  nd_score?: number | null;
  delta_z?: number | null;
  combined_z?: number | null;
  flow_score?: number | null;
  flow_score_range?: number[] | null;
  baseline?: FootprintNormBaseline | null;
  confidence?: {
    baseline_bars?: number | null;
    directional_withheld?: boolean;
    grade?: string | null;
    grade_reasons?: string[] | null;
  } | null;
};

export type FootprintCoverage = {
  first_bar_t?: number | null;
  seeded_prints?: number | null;
  seed_truncated?: boolean | null;
  bars?: number | null;
};

export type FootprintSetup = { setup: string | null; reason: string };

export type FootprintData = {
  symbol: string;
  timeframe_seconds: number;
  tick_size: number;
  tick_size_source?: string | null;   // "explicit" | "observed" | "default"
  tick_size_samples?: number | null;
  imbalance_ratio: number;
  bars: FootprintBar[];
  dom?: FootprintDom | null;
  tape?: FootprintTrade[];
  profile?: FootprintProfile | null;
  flow?: FootprintFlow | null;
  setup?: FootprintSetup | null;
  watching?: string[];
  subscribed_now?: boolean;
  /* spec_version 1 */
  spec_version?: number;
  row_ticks?: number | null;
  row_size?: number | null;
  stacked_imbalance_min?: number | null;
  cvd_basis?: "session" | "watch_window" | null;
  cvd_anchor?: number | null;
  session_cumulative_delta?: number | null;
  cvd_band?: number | null;
  cvd_band_basis?: "session" | "watch_window" | null;
  divergence_bars?: FootprintDivergenceBars | null;
  single_print_rows?: number[] | null;
  single_print_window_bars?: number | null;
  confidence?: FootprintConfidence | null;
  basis?: { volume?: string[]; inferred?: string[] } | null;
  coverage?: FootprintCoverage | null;
  /* live additions (2026-09) */
  session_weighted_delta?: number | null;
  low_confidence_bar?: number | null;   // the shading threshold, set by the server
  // null off the index derivatives: "no freeze rule applies" is a different
  // statement from "the freeze quantity is zero".
  freeze_quantity?: number | null;
  lot_size?: number | null;
  ofi?: FootprintOfi | null;
  tape_speed?: FootprintTapeSpeed | null;
  /* Page-level context the chart itself does not draw but the workspace hangs
     off the same poll: the badge strip, the stored reference levels, an N-day
     composite and, in replay, where the clock is. */
  session?: SessionInfo | null;
  context?: MarketContext | null;
  composite?: CompositeProfile | null;
  replay?: ReplayMeta | null;
};

/* ------------------------------------------------------------- primitives */

const C = {
  chart: "#090d14", border: "#202a38", edge: "#263144",
  text: "#dce5f1", dim: "#8796aa", dimmer: "#687990", grid: "#141d29",
  buy: "#22c893", sell: "#f15b6c", accent: "#4d91ff", poc: "#e5c85a",
  readout: "rgba(11,16,24,0.96)",   // #0b1018 panel over the cluster
  // exact-basis markers get their own family, deliberately not gold and not
  // the buy/sell pair: nothing computed from total volume at price should be
  // mistaken for something computed from an inferred aggressor.
  va: "#6f8fbe", lvn: "#9385c4", sp: "#5f7391",
  warn: "#e0a33f",
  gradeHigh: "#3fb98a", gradeFair: "#d8b24a", gradeLow: "#f15b6c",
};

const AXIS_W = 66;   // right-hand price axis / footer gutter labels
const TIME_H = 16;   // bottom time axis
const FOOT_H = 54;   // per-column delta / volume / normalised / confidence strip
const FOOT_H_MIN = 26;
// Fallback shading threshold for a server that publishes per-bar confidence
// without naming its own (footprint.LOW_CONFIDENCE_BAR). Between the lone-tick
// verdict and the uncontested quote-rule one.
const LOW_CONF_FALLBACK = 0.55;
const CVD_H = 36;    // cumulative-delta sub-pane
const TOP_H = 18;    // header readout line
const MIN_BARS = 5;
// Nine columns leave room for two readable quantities at the workspace's
// minimum chart width. The trader can zoom out or expand the pane for context.
const DEFAULT_BARS = 9;
const NOSE_W = 7;    // unfinished-auction nose length

const SANS = 'ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
const font = (px: number, weight = 400) => `${weight} ${px}px ${SANS}`;

const istTime = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
const istStamp = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });

const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

// Indian market sizes get large fast; keep every cell label to <= 5 glyphs so a
// column never has to be measured or clipped while drawing.
const vol = (n: number): string => {
  const a = Math.abs(n);
  if (a >= 1e7) return `${(n / 1e7).toFixed(1)}C`;
  if (a >= 1e5) return `${(n / 1e5).toFixed(1)}L`;
  if (a >= 1e4) return `${Math.round(n / 1e3)}k`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(Math.round(n));
};
const signed = (n: number) => (n > 0 ? `+${vol(n)}` : n < 0 ? `-${vol(Math.abs(n))}` : "0");
const signedFixed = (n: number, dp = 1) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(dp)}`;
const pct = (n: number) => `${Math.round(n * 100)}%`;
const clockOf = (t: number) => (finite(t) ? istTime.format(new Date(t * 1000)) : "--:--");
// Did this accumulation begin at the session open, or later? OFI is not
// checkpointed, so a mid-session restart silently restarts the cumulative line
// at zero — and a curve that began at 13:00 must not pass for a session
// figure. Five minutes of slack absorbs a quiet contract's first quote.
const OPEN_GRACE_SECONDS = 300;
const startedLate = (since: unknown): string | null => {
  if (!finite(since)) return null;
  const at = new Date((since as number) * 1000);
  const parts = istTime.format(at).split(":");
  const minutes = Number(parts[0]) * 60 + Number(parts[1]);
  return minutes > 9 * 60 + 15 + OPEN_GRACE_SECONDS / 60 ? clockOf(since as number) : null;
};
const stampOf = (t: number) => (finite(t) ? `${istStamp.format(new Date(t * 1000))} IST` : "—");
const shortSymbol = (s: string) => (s ? s.split(":").pop() || s : "—");
const tfLabel = (s: number) => (s >= 3600 ? `${Math.round(s / 3600)}h` : s >= 60 ? `${Math.round(s / 60)}m` : `${Math.max(1, Math.round(s))}s`);

const rgba = (hex: string, a: number) => {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
};

type Grade = "high" | "fair" | "low" | null;

/* Grade thresholds are normative in the spec (§3.9). Prefer the server's own
   grade; derive it only from shares the server actually published; and when
   classified_share was never measured the answer is null — render nothing, not
   "low", which would be a claim about a measurement nobody took. */
const gradeFrom = (cls: number | null | undefined, quote: number | null | undefined): Grade => {
  if (!finite(cls)) return null;
  const q = finite(quote) ? quote : 0;
  if (cls >= 0.90 && q >= 0.60) return "high";
  if (cls >= 0.75 && q >= 0.35) return "fair";
  return "low";
};
const gradeColor = (g: Grade) => (g === "high" ? C.gradeHigh : g === "fair" ? C.gradeFair : g === "low" ? C.gradeLow : C.dimmer);

// Trader shorthand for a price band: 77890.00-92.50. Dropping the digits the
// two prices share keeps a five-figure index range inside the readout instead
// of getting ellipsized down to something unreadable.
const priceRange = (lo: string, hi: string) => {
  if (lo.length !== hi.length) return `${lo}\u2013${hi}`;
  let i = 0;
  while (i < hi.length - 1 && lo[i] === hi[i] && hi[i] !== ".") i += 1;
  return `${lo}\u2013${hi.slice(i)}`;
};

// widest label + a gap, so the value column starts clear of the labels
const bxLabelGap = (labelW: number) => Math.ceil(labelW) + 12;

// When the server suppressed a marker it also says why. Show its reason rather
// than a generic label — a reader who cannot see the cause cannot judge it.
const suppressNote = (suppressed: boolean, reason: string | null | undefined) =>
  (suppressed ? ` (${reason && reason.trim() ? reason.trim() : "low confidence"})` : "");

const levelVolume = (lv: FootprintLevel) => Math.max(0, num(lv.bid)) + Math.max(0, num(lv.ask)) + Math.max(0, num(lv.u));

/* ------------------------------------------------------------- component */

export function FootprintChart({ data, height = 520, requestedBars }: {
  data: FootprintData; height?: number | string; requestedBars?: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dataRef = useRef<FootprintData>(data);
  const sizeRef = useRef({ w: 0, h: 0 });
  const viewRef = useRef({ start: 0, count: 0 });
  const metaRef = useRef({ symbol: "", len: -1, rowSize: 0 });
  const priceViewRef = useRef<{ center: number | null; rows: number | null; follow: boolean }>({
    center: null, rows: null, follow: true,
  });
  const priceDrawRef = useRef({ center: 0, rows: 1, rowHeight: 18, rowSize: 0.05, fullRows: 1,
    yTop: 0, columnLeft: 0, columnRight: 0, columnWidth: 1, bars: 0 });
  const hoverRef = useRef<{ x: number; y: number } | null>(null);
  const hoverCellRef = useRef({ bar: -1, row: -1 });
  const dragRef = useRef<
    { mode: "time"; x: number; start: number } |
    { mode: "price"; y: number; center: number; rowHeight: number; rowSize: number } | null
  >(null);
  const frameRef = useRef(0);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const { w, h } = sizeRef.current;
    if (w <= 0 || h <= 0) return;

    // Back the canvas with device pixels but keep every drawing coordinate in
    // CSS pixels: the transform below is the only place dpr appears.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const bw = Math.max(1, Math.round(w * dpr));
    const bh = Math.max(1, Math.round(h * dpr));
    if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = C.chart;
    ctx.fillRect(0, 0, w, h);
    ctx.textBaseline = "alphabetic";

    const src = dataRef.current;
    const all = Array.isArray(src?.bars) ? src.bars : [];
    if (!all.length) return;                       // empty state: bare canvas, caller shows the message

    /* ---- confidence: one grade, applied consistently -------------------- */
    const conf = src.confidence ?? null;
    const flow = src.flow ?? null;
    const clsShare = finite(conf?.classified_share) ? conf!.classified_share!
      : finite(flow?.classified_share) ? flow!.classified_share! : null;
    const quoteShare = finite(conf?.quote_share) ? conf!.quote_share!
      : finite(flow?.quote_share) ? flow!.quote_share! : null;
    const depthShare = finite(conf?.depth_share) ? conf!.depth_share!
      : finite(flow?.depth_share) ? flow!.depth_share!
        : finite(flow?.depth_ticks) && finite(flow?.trades) && flow!.trades! > 0
          ? flow!.depth_ticks! / flow!.trades! : null;
    const grade: Grade = conf && conf.grade !== undefined ? (conf.grade ?? null) : gradeFrom(clsShare, quoteShare);
    // Desaturate the inferred layer when confidence is low OR UNKNOWN; never
    // hide the numbers.
    //
    // `null` means nobody measured the coverage this grade is derived from, and
    // that is not evidence the inference is sound. Rendering it at full
    // saturation — as this did — made an unmeasured bar indistinguishable from
    // a grade-`high` one, which is the single thing this surface must never do.
    // Unknown is therefore treated exactly like `low`: dimmed, compound markers
    // suppressed, cells drawn monochrome.
    const weakOrUnknown = grade === "low" || grade === null || grade === undefined;
    const inferA = weakOrUnknown ? 0.35 : 1;
    const suppressCompound = weakOrUnknown;
    const monoCells = weakOrUnknown;

    const plotLeft = 0;
    const plotW = Math.max(40, w - AXIS_W);
    const plotTop = TOP_H;
    // Panes collapse from the bottom up on a short canvas rather than
    // squeezing the ladder into illegibility.
    let footH = FOOT_H;
    let cvdH = CVD_H;
    const hasCvd = all.some((b) => finite(b.cvd));
    if (!hasCvd || h - TOP_H - TIME_H - footH - cvdH < 150) cvdH = 0;
    if (h - TOP_H - TIME_H - footH - cvdH < 120) footH = FOOT_H_MIN;
    const plotBottom = h - TIME_H - footH - cvdH;
    const plotH = plotBottom - plotTop;
    const footTop = plotBottom;
    const cvdTop = plotBottom + footH;
    if (plotH < 24 || plotW < 40) return;

    const tick = finite(src.tick_size) && src.tick_size > 0 ? src.tick_size : 0.05;
    // The ladder grid is the ROW size, which the server chooses (spec §1.3).
    // Falling back to the raw tick reproduces the old chart exactly.
    const rowSize = finite(src.row_size) && src.row_size! > 0 ? src.row_size! : tick;
    const dp = clamp(Math.ceil(-Math.log10(tick)), 0, 4);
    const price = (p: number) => p.toFixed(dp);

    const view = viewRef.current;
    const count = clamp(view.count || Math.min(all.length, DEFAULT_BARS), Math.min(MIN_BARS, all.length), all.length);
    const start = clamp(view.start, 0, Math.max(0, all.length - count));
    viewRef.current = { start, count };
    const bars = all.slice(start, start + count);
    // A newly traded contract may have only one bar. The latest cluster stays
    // beside the price scale, at the same readable width it has with history.
    const columns = footprintColumns(plotW, bars.length);
    const colW = columns.width;
    const columnLeft = columns.left;
    const columnRight = columns.right;

    /* ---- shared price axis: one row grid for the whole chart -------------- */
    let lo = Infinity;
    let hi = -Infinity;
    for (const bar of bars) {
      for (const p of [bar.h, bar.l, bar.o, bar.c]) if (finite(p)) { if (p < lo) lo = p; if (p > hi) hi = p; }
      const levels = Array.isArray(bar.levels) ? bar.levels : [];
      for (const lv of levels) if (finite(lv.p)) { if (lv.p < lo) lo = lv.p; if (lv.p > hi) hi = lv.p; }
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return;
    // A whole-session range can contain thousands of valid price rows. Keep
    // the exchange grid intact and display a readable price window; never
    // silently squeeze or rebucket bid/ask quantities into subpixel cells.
    const priceView = priceViewRef.current;
    const anchor = priceView.follow ? (finite(bars[bars.length - 1]?.c) ? bars[bars.length - 1].c : hi)
      : (priceView.center ?? hi);
    const viewport = priceViewport(lo, hi, rowSize, plotH, anchor, priceView.rows);
    const maxP = viewport.topPrice;
    const minP = viewport.bottomPrice;
    const rowCount = viewport.rows;
    const rowH = viewport.rowHeight;
    const yTop = plotTop + (plotH - rowH * rowCount) / 2;
    priceDrawRef.current = {
      center: (maxP + minP) / 2, rows: rowCount, rowHeight: rowH,
      rowSize, fullRows: viewport.fullRows, yTop, columnLeft, columnRight,
      columnWidth: colW, bars: bars.length,
    };
    const rowOf = (p: number) => Math.round((maxP - p) / rowSize);
    const yRow = (p: number) => yTop + rowOf(p) * rowH;
    const yCont = (p: number) => yTop + ((maxP - p) / rowSize) * rowH + rowH / 2;
    const inSpan = (p: number) => finite(p) && p <= maxP + rowSize && p >= minP - rowSize;

    let maxAbsDelta = 1;
    for (const bar of bars) maxAbsDelta = Math.max(maxAbsDelta, Math.abs(num(bar.delta)));

    /* ---- symbol-relative normalisation ----------------------------------- */
    // `flow.normalised` is null unless every server-side floor passed, so its
    // presence is the permission to draw a normalised number at all. The
    // per-bar figure uses nd (delta / volume), which is scale-free and needs no
    // timeframe conversion, against the published nd_median / nd_sigma.
    const norm = flow?.normalised ?? null;
    const normBase = norm?.baseline ?? null;
    const ndMedian = finite(normBase?.nd_median) ? normBase!.nd_median! : null;
    const ndSigma = finite(normBase?.nd_sigma) && normBase!.nd_sigma! > 0 ? normBase!.nd_sigma! : null;
    // The module withholds directional outputs below a 0.50 classified share;
    // honour the same gate per bar rather than quietly re-deriving past it.
    const MIN_NORM_CLASSIFIED = 0.50;
    // of_symbol_baseline stores per-minute statistics (see rvolOf).
    const BASELINE_CADENCE_SEC = 60;
    const normDirectional = !!norm && norm.confidence?.directional_withheld !== true
      && ndMedian !== null && ndSigma !== null;
    const normLegacy = normBase?.estimator_current === false;
    const ndScoreOf = (bar: FootprintBar): number | null => {
      if (!normDirectional) return null;
      const v = num(bar.v);
      if (!(v > 0)) return null;
      if (finite(bar.classified_share) && bar.classified_share! < MIN_NORM_CLASSIFIED) return null;
      // CADENCE GATE. nd is delta/volume — a ratio, so it does not scale with
      // window length the way volume does — but its DISPERSION does: a ratio
      // averaged over five minutes of prints varies less than a one-minute one.
      // The baseline's nd_sigma is measured per minute (same cadence as
      // volume_median below), so dividing a five-minute bar's deviation by it
      // yields a z on no defined scale. There is no sound rescaling to apply
      // here — the shrinkage depends on the autocorrelation of the prints — so
      // on any other cadence this answers "unknown" rather than a wrong number.
      // Lifting this needs per-cadence baselines written by of_validation.py.
      if ((num(src.timeframe_seconds) || 60) !== BASELINE_CADENCE_SEC) return null;
      return (num(bar.delta) / v - ndMedian!) / ndSigma!;
    };
    const rvolOf = (bar: FootprintBar): number | null => {
      // RVOL rests on volume, which the exchange publishes: it survives a poor
      // classified share, and it is per MINUTE, so scale the bar to its window.
      const median = finite(normBase?.volume_median) && normBase!.volume_median! > 0
        ? normBase!.volume_median! : null;
      if (!norm || median === null) return null;
      const tfSec = num(src.timeframe_seconds) || 60;
      if (!(tfSec > 0)) return null;
      return (num(bar.v) * (60 / tfSec)) / median;
    };

    /* ---- per-bar classification confidence ------------------------------- */
    // The server publishes both the figure and the threshold it shades at, so
    // the chart and the API can never disagree about which bars are weak. A
    // bar whose prints carried no score at all is not weak — it is unmeasured,
    // and shading it would be a claim about a measurement nobody took.
    const confFloor = finite(src.low_confidence_bar) && src.low_confidence_bar! > 0
      ? src.low_confidence_bar! : LOW_CONF_FALLBACK;
    const lowConfidence = (bar: FootprintBar): boolean =>
      (bar.low_confidence === true) || (bar.low_confidence === undefined
        && finite(bar.confidence) && bar.confidence! < confFloor);

    /* ---- level-1 OFI, keyed to the bar it belongs to ---------------------- */
    // The server buckets OFI on the same bucket starts the bars use, so the
    // join is by timestamp and never by index: a bar with no trades still
    // exists on the chart, and lining the two series up positionally would
    // slide the whole OFI curve across it.
    const ofiCum = new Map<number, number>();
    for (const row of src.ofi?.series ?? []) {
      if (finite(row.t) && finite(row.cum)) ofiCum.set(row.t, row.cum);
    }

    /* ---- horizontal grid + right price axis ------------------------------ */
    const labelStep = Math.max(1, Math.ceil(16 / Math.max(rowH, 0.001)));
    ctx.font = font(10, 500);
    ctx.textAlign = "right";
    for (let r = 0; r < rowCount; r += labelStep) {
      const y = Math.round(yTop + r * rowH + rowH / 2) + 0.5;
      if (y < plotTop || y > plotBottom) continue;
      ctx.strokeStyle = C.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotLeft, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.fillStyle = C.dimmer;
      ctx.fillText(price(maxP - r * rowSize), w - 6, y + 3.5);
    }
    // Make the clipped price range obvious; the hidden rows remain available
    // by dragging the price axis or scrolling it with Shift + wheel.
    ctx.font = font(8, 600);
    ctx.textAlign = "right";
    ctx.fillStyle = C.warn;
    if (viewport.hiddenAbove > 0) ctx.fillText(`▲ ${viewport.hiddenAbove}`, w - 5, plotTop + 9);
    if (viewport.hiddenBelow > 0) ctx.fillText(`▼ ${viewport.hiddenBelow}`, w - 5, plotBottom - 4);

    /* ---- footprint single prints: a row traded in exactly one bar --------- */
    const sprints = Array.isArray(src.single_print_rows) ? src.single_print_rows : [];
    if (sprints.length) {
      ctx.strokeStyle = rgba(C.sp, 0.75);
      ctx.lineWidth = 1;
      ctx.setLineDash([1, 4]);                    // deliberately unlike the LTP dash
      let labelled = false;
      for (const p of sprints) {
        if (!inSpan(p)) continue;
        const y = Math.round(yCont(p)) + 0.5;
        if (y < plotTop || y > plotBottom) continue;
        ctx.beginPath();
        ctx.moveTo(plotLeft, y);
        ctx.lineTo(plotW, y);
        ctx.stroke();
        // "traded in exactly one bar" is meaningless without the denominator:
        // one bar out of 6 is noise, one out of 60 is a genuine gap the market
        // left behind. The window was on the wire and went unrendered, so the
        // reader had no way to tell those apart. Labelled once, on the first
        // visible line, to keep the plot quiet.
        if (!labelled && finite(src.single_print_window_bars)) {
          labelled = true;
          ctx.setLineDash([]);
          ctx.fillStyle = rgba(C.sp, 0.85);
          ctx.textAlign = "left";
          ctx.fillText(`single print · 1 of ${src.single_print_window_bars} bars`,
                       plotLeft + 4, y - 3);
          ctx.textAlign = "right";
          ctx.setLineDash([1, 4]);
        }
      }
      ctx.setLineDash([]);
    }

    const cvdBasis = src.cvd_basis ?? null;
    const anchored = cvdBasis === "session";
    ctx.strokeStyle = C.edge;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(plotW + 0.5, plotTop);
    ctx.lineTo(plotW + 0.5, h - TIME_H);
    ctx.moveTo(plotLeft, plotBottom + 0.5);
    ctx.lineTo(w, plotBottom + 0.5);
    ctx.moveTo(plotLeft, h - TIME_H + 0.5);
    ctx.lineTo(w, h - TIME_H + 0.5);
    ctx.stroke();
    if (cvdH > 0) {
      // A dashed frame is the pane-level statement that this series is not
      // anchored to the session — the label alone can scroll out of a narrow
      // header, the frame cannot.
      if (!anchored) ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(plotLeft, cvdTop + 0.5);
      ctx.lineTo(w, cvdTop + 0.5);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    /* ---- shelves collected while drawing, painted over the clusters ------- */
    type Shelf = { i: number; level: number; color: string; alpha: number };
    const shelves: Shelf[] = [];
    // A projected level lives until a later bar trades through it.
    const brokenAt = (i: number, level: number) => {
      for (let j = i + 1; j < bars.length; j += 1) {
        const b = bars[j];
        if (finite(b.h) && finite(b.l) && level <= b.h && level >= b.l) return j;
      }
      return bars.length;
    };

    /* ---- columns --------------------------------------------------------- */
    const candleW = clamp(colW * 0.14, 3, 9);
    for (let i = 0; i < bars.length; i += 1) {
      const bar = bars[i];
      const x0 = columnLeft + i * colW;
      const levels = Array.isArray(bar.levels) ? bar.levels : [];
      const up = num(bar.c) >= num(bar.o);

      if (colW > 10) {
        ctx.strokeStyle = C.grid;
        ctx.beginPath();
        ctx.moveTo(Math.round(x0) + 0.5, plotTop);
        ctx.lineTo(Math.round(x0) + 0.5, plotBottom + footH);
        ctx.stroke();
      }

      // Per-bar coverage stripe: a bar captured during a depth outage is not
      // the session average, and this is how a reader spots the one column
      // they should not trade off.
      if (finite(bar.classified_share) && finite(clsShare) && bar.classified_share! < clsShare - 0.15) {
        ctx.fillStyle = rgba(C.warn, 0.045);
        ctx.fillRect(x0, plotTop, colW, plotBottom - plotTop);
      }

      // Per-bar confidence shading, a different measurement from the coverage
      // stripe above: coverage is how much of the bar got a SIDE at all, this
      // is how much the three votes AGREED on the side they gave. A bar can be
      // fully classified and still be carried entirely by the tick rule.
      if (lowConfidence(bar)) {
        ctx.fillStyle = rgba(C.warn, 0.07);
        ctx.fillRect(x0, plotTop, colW, plotBottom - plotTop);
      }

      // Bars may extend beyond the vertical price viewport. Clip their candle,
      // ladder and markers at the plot edge instead of painting over the
      // header, footer or adjacent columns.
      ctx.save();
      ctx.beginPath();
      ctx.rect(x0, plotTop, colW, plotBottom - plotTop);
      ctx.clip();

      // OHLC candle sits beside the cluster, dimmed so it never fights the numbers.
      const cx = Math.round(x0 + 2 + candleW / 2) + 0.5;
      ctx.globalAlpha = 0.38;
      ctx.strokeStyle = up ? C.buy : C.sell;
      ctx.fillStyle = up ? C.buy : C.sell;
      ctx.lineWidth = 1;
      if (finite(bar.h) && finite(bar.l)) {
        ctx.beginPath();
        ctx.moveTo(cx, yCont(bar.h));
        ctx.lineTo(cx, yCont(bar.l));
        ctx.stroke();
      }
      if (finite(bar.o) && finite(bar.c)) {
        const yo = yCont(bar.o);
        const yc = yCont(bar.c);
        const top = Math.min(yo, yc);
        const bh2 = Math.max(1, Math.abs(yc - yo));
        ctx.globalAlpha = 0.16;
        ctx.fillRect(cx - candleW / 2, top, candleW, bh2);
        ctx.globalAlpha = 0.45;
        ctx.strokeRect(Math.round(cx - candleW / 2) + 0.5, Math.round(top) + 0.5, Math.round(candleW), Math.round(bh2));
      }
      ctx.globalAlpha = 1;

      const clusterL = x0 + candleW + 5;
      const clusterR = x0 + colW - 4;
      const mid = (clusterL + clusterR) / 2;
      const halfW = mid - clusterL;
      if (halfW <= 1) { ctx.restore(); continue; }

      // A backfilled bar has OHLCV but no ladder. Say so; never synthesise rows.
      if (!levels.length && bar.levels_available === false) {
        ctx.fillStyle = rgba(C.dimmer, 0.10);
        ctx.fillRect(clusterL, plotTop, halfW * 2, plotBottom - plotTop);
        if (colW >= 30) {
          ctx.save();
          ctx.beginPath();
          ctx.rect(clusterL, plotTop, halfW * 2, plotBottom - plotTop);
          ctx.clip();
          ctx.font = font(8);
          ctx.textAlign = "center";
          ctx.fillStyle = C.dimmer;
          ctx.fillText("no ladder", mid, (plotTop + plotBottom) / 2);
          ctx.restore();
        }
      }

      // numbers need a legible row AND enough width for a 5-glyph label
      const textMode = rowH >= 15 && halfW >= 26;
      const cellFont = font(clamp(Math.min(rowH * 0.7, (halfW - 4) / 2.5), 9, 13), 600);

      let peak = 1;
      for (const lv of levels) peak = Math.max(peak, levelVolume(lv));

      /* Zone FILLS go down before the ladder so the bid/ask numbers — the data
         — always sit on top of the markers that comment on them. Only outlines
         and brackets are drawn over the text, further down. */
      const stacks = Array.isArray(bar.stacks) ? bar.stacks : [];
      for (const st of stacks) {
        if (st.suppressed === true || suppressCompound) continue;
        if (!finite(st.from) || !finite(st.to)) continue;
        const top = Math.max(st.from, st.to);
        const bot = Math.min(st.from, st.to);
        if (!priceRangesOverlap(top, bot, minP - rowSize, maxP + rowSize)) continue;
        const yA = yRow(top);
        const yB = yRow(bot) + Math.max(1, rowH);
        const buy = st.side === "buy";
        // a stack reads as different in kind, not merely as more of the same
        ctx.fillStyle = rgba(buy ? C.buy : C.sell, 0.30 * inferA);
        ctx.fillRect(buy ? mid : clusterL, yA, halfW, yB - yA);
      }
      const absorbs = Array.isArray(bar.absorption) ? bar.absorption : [];
      for (const ab of absorbs) {
        if (ab.suppressed === true || suppressCompound) continue;
        if (!finite(ab.price) || !inSpan(ab.price)) continue;
        // "buyers_absorbed" = the aggressive buyers were absorbed, so the
        // SELLER won: paint the bid half. Deliberately the opposite half from
        // the imbalance tint, because the two readings are near-opposite.
        const buyersAbsorbed = ab.side === "buyers_absorbed";
        ctx.fillStyle = rgba(buyersAbsorbed ? C.sell : C.buy, 0.38 * inferA);
        ctx.fillRect(buyersAbsorbed ? clusterL : mid, yRow(ab.price), halfW, Math.max(1, rowH - (rowH > 4 ? 1 : 0)));
      }

      for (const lv of levels) {
        if (!finite(lv.p)) continue;
        const y = yRow(lv.p);
        if (y > plotBottom || y + rowH < plotTop) continue;
        const cellH = Math.max(1, rowH - (rowH > 4 ? 1 : 0));
        const bid = Math.max(0, num(lv.bid));
        const ask = Math.max(0, num(lv.ask));
        const rowVol = levelVolume(lv);

        // Value area (EXACT — total volume at price) sits under everything and
        // keeps full saturation at every grade.
        if (lv.va === true) {
          ctx.fillStyle = rgba(C.va, 0.11);
          ctx.fillRect(clusterL, y, halfW * 2, cellH);
        }

        // volume heat behind the cell keeps the busy prices visible when zoomed
        // out. Suppressed on an LVN: an absence must read as an absence.
        const heat = rowVol / peak;
        if (heat > 0.02 && lv.lvn !== true) {
          ctx.fillStyle = `rgba(120,150,190,${(0.02 + 0.075 * heat).toFixed(3)})`;
          ctx.fillRect(clusterL, y, halfW * 2, cellH);
        }
        if (lv.lvn === true) {
          ctx.strokeStyle = rgba(C.lvn, 0.85);
          ctx.lineWidth = 1;
          const sy = Math.round(y + cellH / 2) + 0.5;
          ctx.beginPath();
          ctx.moveTo(clusterL, sy);
          ctx.lineTo(clusterR, sy);
          ctx.stroke();
        }

        // The server flags imbalances diagonally (this row's ask against the
        // row below's bid, and this row's bid against the row above's ask), so
        // a "buy" flag belongs to the ASK cell and a "sell" flag to the BID
        // cell. The two are independent tests; when both fire, tint both.
        const imbBuy = lv.imb_buy === true || (lv.imb_buy === undefined && lv.imb === "buy");
        const imbSell = lv.imb_sell === true || (lv.imb_sell === undefined && lv.imb === "sell");
        if (imbBuy) {
          ctx.fillStyle = rgba(C.buy, 0.22 * inferA);
          ctx.fillRect(mid, y, halfW, cellH);
        }
        if (imbSell) {
          ctx.fillStyle = rgba(C.sell, 0.22 * inferA);
          ctx.fillRect(clusterL, y, halfW, cellH);
        }

        if (lv.poc) {
          ctx.fillStyle = rgba(C.poc, 0.10);
          ctx.fillRect(clusterL, y, halfW * 2, cellH);
          ctx.fillStyle = C.poc;
          ctx.fillRect(clusterL - 3.5, y, 2.5, cellH);
          ctx.fillRect(clusterR + 1, y, 2.5, cellH);
        }

        // the divider doubles as a delta spine (inferred)
        const d = num(lv.d);
        ctx.globalAlpha = 0.75 * inferA;
        ctx.fillStyle = d > 0 ? C.buy : d < 0 ? C.sell : C.edge;
        ctx.fillRect(Math.round(mid), y, 1, Math.max(1, rowH));
        ctx.globalAlpha = 1;

        if (textMode) {
          ctx.font = cellFont;
          const ty = y + cellH / 2 + 3;
          // The feed can report traded volume without enough evidence to
          // assign an aggressor. A 0/0 pair alone hides that volume entirely.
          if (bid === 0 && ask === 0 && num(lv.u) > 0) {
            ctx.textAlign = "center";
            ctx.fillStyle = C.dim;
            ctx.fillText(`u ${vol(lv.u!)}`, mid, ty, halfW * 2 - 4);
            continue;
          }
          // clip each half so a wide label can never bleed across the divider
          ctx.save();
          ctx.beginPath();
          ctx.rect(clusterL, y, halfW - 1, cellH);
          ctx.clip();
          ctx.textAlign = "right";
          ctx.fillStyle = bid > 0 ? (monoCells ? C.text : C.sell) : C.dimmer;
          ctx.fillText(vol(bid), mid - 3, ty, halfW - 5);
          ctx.restore();
          ctx.save();
          ctx.beginPath();
          ctx.rect(mid + 1, y, halfW - 1, cellH);
          ctx.clip();
          ctx.textAlign = "left";
          ctx.fillStyle = ask > 0 ? (monoCells ? C.text : C.buy) : C.dimmer;
          ctx.fillText(vol(ask), mid + 3, ty, halfW - 5);
          ctx.restore();
        } else {
          const bw2 = (bid / peak) * halfW;
          const aw2 = (ask / peak) * halfW;
          ctx.globalAlpha = inferA;
          ctx.fillStyle = C.sell;
          ctx.fillRect(mid - bw2, y, bw2, cellH);
          ctx.fillStyle = C.buy;
          ctx.fillRect(mid + 1, y, aw2, cellH);
          ctx.globalAlpha = 1;
        }
        if (num(lv.u) > 0 && (bid > 0 || ask > 0)) {
          // Neutral underline marks unclassified volume mixed into a row whose
          // printed bid/ask split is only partial; exact quantity is on hover.
          ctx.fillStyle = rgba(C.dim, 0.8);
          ctx.fillRect(clusterL, y + cellH - 1, Math.max(1, (num(lv.u) / peak) * halfW * 2), 1);
        }
      }

      /* ---- value-area caps (EXACT) --------------------------------------- */
      for (const edge of [bar.vah, bar.val]) {
        if (!finite(edge) || !inSpan(edge)) continue;
        const y = Math.round(yRow(edge) + (edge === bar.vah ? 0 : Math.max(1, rowH))) + 0.5;
        ctx.strokeStyle = rgba(C.va, 0.85);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(clusterL, y);
        ctx.lineTo(clusterR, y);
        ctx.stroke();
      }

      /* ---- stacked imbalance: bracket + projected shelf (fill drew earlier) */
      for (const st of stacks) {
        if (st.suppressed === true || suppressCompound) continue;
        if (!finite(st.from) || !finite(st.to)) continue;
        const top = Math.max(st.from, st.to);
        const bot = Math.min(st.from, st.to);
        if (!priceRangesOverlap(top, bot, minP - rowSize, maxP + rowSize)) continue;
        const yA = yRow(top);
        const yB = yRow(bot) + Math.max(1, rowH);
        const buy = st.side === "buy";
        const col = buy ? C.buy : C.sell;
        ctx.strokeStyle = rgba(col, 0.9 * inferA);
        ctx.lineWidth = 2;
        const bkx = buy ? Math.round(clusterR) - 1 : Math.round(clusterL) + 1;
        ctx.beginPath();
        ctx.moveTo(bkx, yA + 0.5);
        ctx.lineTo(bkx, yB - 0.5);
        ctx.moveTo(bkx, yA + 0.5); ctx.lineTo(bkx + (buy ? -4 : 4), yA + 0.5);
        ctx.moveTo(bkx, yB - 0.5); ctx.lineTo(bkx + (buy ? -4 : 4), yB - 0.5);
        ctx.stroke();
        ctx.lineWidth = 1;
        if (finite(st.extreme) && inSpan(st.extreme)) {
          shelves.push({ i, level: st.extreme, color: col, alpha: 0.7 * inferA });
        }
      }

      /* ---- absorption: outline over the block drawn earlier --------------- */
      for (const ab of absorbs) {
        if (ab.suppressed === true || suppressCompound) continue;
        if (!finite(ab.price) || !inSpan(ab.price)) continue;
        const y = yRow(ab.price);
        const cellH = Math.max(1, rowH - (rowH > 4 ? 1 : 0));
        const buyersAbsorbed = ab.side === "buyers_absorbed";
        const abx = buyersAbsorbed ? clusterL : mid;
        ctx.strokeStyle = rgba(buyersAbsorbed ? C.sell : C.buy, 0.95 * inferA);
        ctx.lineWidth = 1;
        ctx.strokeRect(Math.round(abx) + 0.5, Math.round(y) + 0.5, Math.round(halfW) - 1, Math.max(1, Math.round(cellH) - 1));
      }

      /* ---- exhaustion: hollow caret at the bar extreme -------------------- */
      const ex = bar.exhaustion ?? null;
      // `detected` is the marker's own verdict: the volume leg AND the auction
      // leg. The payload publishes an exhaustion object whenever the volume
      // test passes, with detected=false when the auction test failed or could
      // not be run — so drawing on the object's mere presence put a caret on
      // bars the module had explicitly declined to call. The tooltip still
      // shows the partial result (vol✓ auc✗), which is where a half-answer
      // belongs.
      if (ex && ex.detected === true && ex.suppressed !== true && !suppressCompound
          && finite(ex.price) && inSpan(ex.price)) {
        const high = ex.end === "high";
        const y = yRow(ex.price) + (high ? -3 : Math.max(1, rowH) + 3);
        const cxe = mid;
        ctx.strokeStyle = rgba(high ? C.sell : C.buy, 0.9 * inferA);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(cxe - 5, y + (high ? 4 : -4));
        ctx.lineTo(cxe, y);
        ctx.lineTo(cxe + 5, y + (high ? 4 : -4));
        ctx.stroke();
        ctx.lineWidth = 1;
      }

      /* ---- unfinished auction: nose at the extreme + projected shelf ------ */
      const unf = bar.unfinished ?? null;
      if (unf) {
        const ends: { state: UnfinishedState | null | undefined; p: number | null | undefined; high: boolean }[] = [
          { state: unf.high, p: finite(unf.high_price) ? unf.high_price : bar.h, high: true },
          { state: unf.low, p: finite(unf.low_price) ? unf.low_price : bar.l, high: false },
        ];
        for (const e of ends) {
          if (e.state !== "unfinished" && e.state !== "weak") continue;
          if (!finite(e.p) || !inSpan(e.p)) continue;
          const a = (e.state === "weak" ? 0.5 : 1) * inferA;
          const col = e.high ? C.sell : C.buy;
          const y = Math.round(yRow(e.p) + Math.max(1, rowH) / 2) + 0.5;
          ctx.strokeStyle = rgba(col, 0.95 * a);
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(clusterR + 1, y);
          ctx.lineTo(Math.min(plotW, clusterR + 1 + NOSE_W), y);
          ctx.stroke();
          ctx.lineWidth = 1;
          shelves.push({ i, level: e.p, color: col, alpha: 0.55 * a });
        }
      }

      /* ---- freeze-size and large prints ---------------------------------- */
      // EXACT basis (a traded quantity), so these keep full saturation at every
      // grade — unlike the inferred markers above. The side only chooses which
      // half of the cluster the diamond hangs on; it is inferred, which is why
      // the glyph is an outline rather than a filled buy/sell block.
      for (const mp of Array.isArray(bar.marked_prints) ? bar.marked_prints : []) {
        if (!finite(mp.p) || !inSpan(mp.p)) continue;
        const gy = Math.round(yRow(mp.p) + Math.max(1, rowH) / 2) + 0.5;
        if (gy < plotTop || gy > plotBottom) continue;
        const gx = mp.side > 0 ? clusterR - 5 : clusterL + 5;
        const freeze = mp.kind === "freeze";
        ctx.strokeStyle = freeze ? C.poc : C.warn;
        ctx.lineWidth = freeze ? 1.5 : 1;
        ctx.beginPath();
        ctx.moveTo(gx, gy - 4);
        ctx.lineTo(gx + 4, gy);
        ctx.lineTo(gx, gy + 4);
        ctx.lineTo(gx - 4, gy);
        ctx.closePath();
        ctx.stroke();
        ctx.lineWidth = 1;
      }
      ctx.restore();

      /* ---- footer: delta / volume / symbol-relative reading --------------- */
      const delta = num(bar.delta);
      const fy = footTop + 1;
      const centre = x0 + colW / 2;
      ctx.textAlign = "center";
      ctx.globalAlpha = inferA;
      ctx.font = font(9, 600);
      ctx.fillStyle = delta > 0 ? C.buy : delta < 0 ? C.sell : C.dim;
      if (colW >= 26) ctx.fillText(signed(delta), centre, fy + 10);
      const barW = (Math.abs(delta) / maxAbsDelta) * (colW / 2 - 3);
      ctx.fillRect(delta >= 0 ? centre : centre - barW, fy + 14, Math.max(barW, delta === 0 ? 0 : 1), 5);
      ctx.globalAlpha = 1;
      ctx.fillStyle = C.edge;
      ctx.globalAlpha = 0.35;
      ctx.fillRect(Math.round(centre), fy + 12, 1, 9);
      ctx.globalAlpha = 1;
      if (footH >= FOOT_H) {
        if (colW >= 26) {
          ctx.font = font(9);
          ctx.fillStyle = C.dim;                          // volume is EXACT
          ctx.fillText(vol(Math.max(0, num(bar.v))), centre, fy + 30);
        }
        const dz = ndScoreOf(bar);
        if (dz !== null && colW >= 26) {
          ctx.font = font(9, 600);
          ctx.globalAlpha = inferA * (normLegacy ? 0.6 : 1);
          ctx.fillStyle = Math.abs(dz) < 0.75 ? C.dimmer : dz > 0 ? C.buy : C.sell;
          ctx.fillText(signedFixed(dz, 1), centre, fy + 40);
          ctx.globalAlpha = 1;
        }
        // The shading says WHICH bars are weak; the number says how weak, and
        // a reader comparing two shaded bars needs it to tell 0.54 from 0.31.
        if (finite(bar.confidence) && colW >= 26) {
          ctx.font = font(8);
          ctx.globalAlpha = inferA;
          ctx.fillStyle = lowConfidence(bar) ? C.warn : C.dimmer;
          ctx.fillText(`c${bar.confidence!.toFixed(2)}`, centre, fy + 50);
          ctx.globalAlpha = 1;
        }
      }
    }

    /* ---- projected shelves ------------------------------------------------ */
    for (const sh of shelves) {
      const y = Math.round(yCont(sh.level)) + 0.5;
      if (y < plotTop || y > plotBottom) continue;
      const j = brokenAt(sh.i, sh.level);
      const xa = columnLeft + sh.i * colW + colW - 3;
      const xb = j >= bars.length ? columnRight : columnLeft + j * colW + colW / 2;
      if (xb <= xa) continue;
      ctx.strokeStyle = rgba(sh.color, sh.alpha);
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(xa, y);
      ctx.lineTo(Math.min(xb, columnRight), y);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    /* ---- bar-level delta divergence: draw BOTH legs ----------------------- */
    const dvg = src.divergence_bars ?? null;
    const dvgKind = dvg?.kind ?? null;
    let dvgShown = false;
    if (dvg && dvgKind && dvg.suppressed !== true && !suppressCompound) {
      const ia = bars.findIndex((b) => b.t === dvg.at_bar);
      const ib = bars.findIndex((b) => b.t === dvg.reference_bar);
      if (ia >= 0 && ib >= 0 && finite(dvg.price_extreme) && finite(dvg.reference_price_extreme)
        && inSpan(dvg.price_extreme!) && inSpan(dvg.reference_price_extreme!)) {
        const col = dvgKind === "bearish" ? C.sell : C.buy;
        ctx.strokeStyle = rgba(col, 0.85 * inferA);
        ctx.lineWidth = 1.5;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        ctx.moveTo(columnLeft + ib * colW + colW / 2, yCont(dvg.reference_price_extreme!));
        ctx.lineTo(columnLeft + ia * colW + colW / 2, yCont(dvg.price_extreme!));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineWidth = 1;
        dvgShown = true;
      }
    }

    /* ---- CVD sub-pane ----------------------------------------------------- */
    // The chart's CVD and the header's CVD must be one series with one anchor
    // and one label. When the server has not anchored it to the session, this
    // pane says "window" and the session figure is shown separately — two
    // numbers named differently, never two numbers both named "CVD".
    const sessionCvd = finite(src.session_cumulative_delta) ? src.session_cumulative_delta!
      : finite(flow?.cumulative_delta) ? flow!.cumulative_delta : null;
    const cvdLabel = anchored ? "CVD (est.)" : "CVD (window)";
    const cvdBand = finite(src.cvd_band) ? src.cvd_band! : null;
    if (cvdH > 0) {
      const cbot = cvdTop + cvdH;
      let cLo = 0;
      let cHi = 0;
      for (const b of bars) if (finite(b.cvd)) { if (b.cvd < cLo) cLo = b.cvd; if (b.cvd > cHi) cHi = b.cvd; }
      if (cvdBand !== null) { cLo -= cvdBand; cHi += cvdBand; }
      if (cHi - cLo < 1) { cHi += 1; cLo -= 1; }
      const padT = cvdTop + 4;
      const padB = cbot - 4;
      const yC = (v: number) => padB - ((v - cLo) / (cHi - cLo)) * (padB - padT);

      ctx.save();
      ctx.beginPath();
      ctx.rect(plotLeft, cvdTop + 1, plotW, cvdH - 1);
      ctx.clip();                                   // nothing in this pane may escape it

      // The pane auto-scales to the series, so zero is usually off it. Draw the
      // zero line only when it is genuinely in range: a zero line parked at the
      // pane edge would read as a crossing that never happened.
      if (cLo <= 0 && cHi >= 0) {
        const zeroY = Math.round(yC(0)) + 0.5;
        ctx.strokeStyle = C.edge;
        ctx.lineWidth = 1;
        if (!anchored) ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(plotLeft, zeroY);
        ctx.lineTo(plotW, zeroY);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      if (cvdBand !== null) {
        ctx.fillStyle = rgba(C.accent, 0.08 * inferA);
        ctx.beginPath();
        for (let i = 0; i < bars.length; i += 1) {
          const v = num(bars[i].cvd);
          const xa = columnLeft + i * colW;
          ctx.rect(xa, yC(v + cvdBand), colW, Math.max(1, yC(v - cvdBand) - yC(v + cvdBand)));
        }
        ctx.fill();
      }

      ctx.strokeStyle = rgba(C.accent, 0.95 * inferA);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < bars.length; i += 1) {
        if (!finite(bars[i].cvd)) continue;
        const v = bars[i].cvd;
        const xa = columnLeft + i * colW;
        const y = yC(v);
        if (!started) { ctx.moveTo(xa, y); started = true; } else { ctx.lineTo(xa, y); }
        ctx.lineTo(xa + colW, y);
      }
      if (started) ctx.stroke();
      ctx.lineWidth = 1;

      if (dvgShown && dvg && finite(dvg.cvd_at_extreme) && finite(dvg.reference_cvd_extreme)) {
        const ia = bars.findIndex((b) => b.t === dvg.at_bar);
        const ib = bars.findIndex((b) => b.t === dvg.reference_bar);
        if (ia >= 0 && ib >= 0) {
          ctx.strokeStyle = rgba(dvgKind === "bearish" ? C.sell : C.buy, 0.85 * inferA);
          ctx.lineWidth = 1.5;
          ctx.setLineDash([5, 3]);
          ctx.beginPath();
          ctx.moveTo(columnLeft + ib * colW + colW / 2, yC(dvg.reference_cvd_extreme!));
          ctx.lineTo(columnLeft + ia * colW + colW / 2, yC(dvg.cvd_at_extreme!));
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.lineWidth = 1;
        }
      }

      // Level-1 OFI on its own scale, dotted. It is a different measurement
      // from CVD — the book's pressure rather than the tape's aggressor — in
      // quantity units that are not comparable with delta, so sharing the
      // pane's axis would invite reading the gap between the two as a number.
      // What the pane is for is the SHAPE: OFI turning while CVD keeps going
      // is the book giving way before the tape does.
      if (ofiCum.size) {
        const ofiValues = bars.map((b) => ofiCum.get(b.t)).filter(finite) as number[];
        if (ofiValues.length >= 2) {
          const oLo = Math.min(...ofiValues);
          const oHi = Math.max(...ofiValues);
          const oSpan = oHi - oLo || 1;
          const yO = (v: number) => (cbot - 4) - ((v - oLo) / oSpan) * ((cbot - 4) - (cvdTop + 4));
          ctx.strokeStyle = rgba(C.va, 0.9 * inferA);
          ctx.lineWidth = 1;
          ctx.setLineDash([2, 2]);
          ctx.beginPath();
          let ofiStarted = false;
          for (let i = 0; i < bars.length; i += 1) {
            const v = ofiCum.get(bars[i].t);
            if (!finite(v)) continue;
            const xa = columnLeft + i * colW;
            const y = yO(v);
            if (!ofiStarted) { ctx.moveTo(xa, y); ofiStarted = true; } else { ctx.lineTo(xa, y); }
            ctx.lineTo(xa + colW, y);
          }
          if (ofiStarted) ctx.stroke();
          ctx.setLineDash([]);
        }
      }

      ctx.restore();                                // end pane clip

      ctx.font = font(8);
      ctx.textAlign = "left";
      ctx.fillStyle = C.dimmer;
      ctx.fillText(anchored ? "CVD est" : "CVD win", plotW + 4, cvdTop + 11);
      if (cvdBand !== null) ctx.fillText(`±${vol(cvdBand)}`, plotW + 4, cvdTop + 21);
      // "L1" is load-bearing: Fyers publishes one level of depth, so this is
      // best-quote pressure, not the L5/L50 OFI the literature reports.
      if (ofiCum.size) {
        ctx.fillStyle = C.va;
        // The origin travels with the label when it is not the open — the CVD
        // leg beside it already declares its own basis ("CVD est"/"CVD win"),
        // and a cumulative curve with an undeclared start is the same claim
        // made silently.
        const ofiFrom = startedLate(src.ofi?.since);
        ctx.fillText(ofiFrom ? `OFI L1 ${ofiFrom}` : "OFI L1",
                     plotW + 4, cvdTop + (cvdBand !== null ? 31 : 21));
      }
    }

    /* ---- footer gutter labels --------------------------------------------- */
    ctx.font = font(8);
    ctx.textAlign = "left";
    ctx.fillStyle = C.dimmer;
    ctx.fillText("Δ est", plotW + 4, footTop + 11);
    if (footH >= FOOT_H) {
      ctx.fillText("vol", plotW + 4, footTop + 31);
      if (normDirectional) ctx.fillText(normLegacy ? "nd z*" : "nd z", plotW + 4, footTop + 41);
      if (bars.some((b) => finite(b.confidence))) ctx.fillText("conf", plotW + 4, footTop + 51);
    }

    /* ---- last traded price tag ------------------------------------------- */
    const last = src.dom && finite(src.dom.last) ? src.dom.last : undefined;
    if (last !== undefined && inSpan(last)) {
      const y = Math.round(yCont(last)) + 0.5;
      ctx.strokeStyle = C.accent;
      ctx.globalAlpha = 0.55;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(plotLeft, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.fillStyle = C.accent;
      ctx.fillRect(plotW + 1, y - 7, AXIS_W - 2, 14);
      ctx.fillStyle = "#04070c";
      ctx.font = font(10, 600);
      ctx.textAlign = "right";
      ctx.fillText(price(last), w - 5, y + 3);
    }

    /* ---- time axis -------------------------------------------------------- */
    ctx.font = font(10);
    ctx.textAlign = "center";
    ctx.fillStyle = C.dim;
    const timeStep = Math.max(1, Math.ceil(46 / colW));
    for (let i = 0; i < bars.length; i += timeStep) {
      const centre = columnLeft + i * colW + colW / 2;
      if (centre > plotW - 8) continue;
      ctx.fillText(clockOf(bars[i].t), centre, h - 4);
    }

    /* ---- header ----------------------------------------------------------- */
    const tail = bars[bars.length - 1];
    // Does the published coverage percentage contradict the visible ladder?
    let ladderResid = 0;
    for (const b of bars) {
      const lv = Array.isArray(b.levels) ? b.levels : [];
      if (!lv.length) continue;
      let sum = 0;
      for (const l of lv) sum += levelVolume(l);
      ladderResid += Math.max(0, num(b.v) - sum);
    }
    const clsContradicted = finite(clsShare) && clsShare >= 0.999 && ladderResid > 0;

    let headRight = plotW - 4;
    if (tail && plotW > 150) {
      const cvdVal = signed(num(tail.cvd));
      const cvdCol = num(tail.cvd) >= 0 ? C.buy : C.sell;
      ctx.font = font(9, 600);
      const vw = ctx.measureText(cvdVal).width;
      ctx.font = font(9);
      const lw = ctx.measureText(cvdLabel).width;
      let x = plotW - 4 - vw;
      ctx.textAlign = "left";
      ctx.globalAlpha = inferA;
      ctx.font = font(9, 600);
      ctx.fillStyle = cvdCol;
      ctx.fillText(cvdVal, x, 12);
      ctx.globalAlpha = 1;
      x -= 4 + lw;
      ctx.font = font(9);
      ctx.fillStyle = C.dimmer;
      ctx.fillText(cvdLabel, x, 12);

      // The session figure is a DIFFERENT quantity when the chart is not
      // session-anchored. Name it, do not let it masquerade as the same number.
      if (!anchored && sessionCvd !== null && plotW > 420) {
        const st = `sess ${signed(sessionCvd)}`;
        ctx.font = font(9);
        const sw = ctx.measureText(st).width;
        x -= 10 + sw;
        ctx.fillStyle = C.dim;
        ctx.fillText(st, x, 12);
      }

      // Confidence chip, adjacent to the number it qualifies.
      if (grade !== null && plotW > 300) {
        const parts: string[] = [grade];
        if (finite(clsShare)) parts.push(`${pct(clsShare)} cls`);
        if (finite(quoteShare) && plotW > 470) parts.push(`${pct(quoteShare)} qt`);
        // This is availability of the top-of-book quote around captured
        // prints. It is not exchange depth or a 50-level order book.
        if (finite(depthShare) && plotW > 610) parts.push(`${pct(depthShare)} L1q`);
        const chip = `${parts.join(" · ")}${clsContradicted ? " ⚠" : ""}`;
        ctx.font = font(9, 600);
        const cw = ctx.measureText(chip).width;
        x -= 10 + cw + 10;
        ctx.fillStyle = rgba(gradeColor(grade), 0.16);
        ctx.fillRect(x - 1, 2, cw + 10, TOP_H - 5);
        ctx.fillStyle = gradeColor(grade);
        ctx.fillText(chip, x + 4, 12);
      }
      headRight = Math.max(0, x - 10);
    }

    const ratio = finite(src.imbalance_ratio) ? src.imbalance_ratio : 0;
    ctx.save();                                  // narrow panels: elide, never overlap the right block
    ctx.beginPath();
    ctx.rect(0, 0, Math.max(0, headRight), TOP_H);
    ctx.clip();
    ctx.font = font(9);
    ctx.textAlign = "left";
    ctx.fillStyle = C.dimmer;
    const sym = shortSymbol(src.symbol);
    const tf = tfLabel(num(src.timeframe_seconds) || 60);
    const span = `${bars.length}/${all.length} bars`;
    // "default" means nobody could resolve this instrument's tick, so the row
    // grid resting on it is an assumption. Mark it; do not present it as read.
    const tickMark = src.tick_size_source === "default" ? "?" : "";
    const rowTxt = finite(src.row_ticks) && src.row_ticks! > 0
      ? `row ${price(rowSize)}${tickMark} (${src.row_ticks}t)` : `tick ${price(tick)}${tickMark}`;
    const covT = src.coverage && finite(src.coverage.first_bar_t) ? src.coverage.first_bar_t! : null;
    const cov = covT !== null ? ` · from ${clockOf(covT)}${src.coverage?.seed_truncated ? "…" : ""}` : "";
    ctx.fillText(
      headRight >= 400 ? `${sym} · ${tf} · ${rowTxt} · imb ${ratio.toFixed(1)}x · ${span}${cov}`
        : headRight >= 220 ? `${sym} · ${tf} · ${span}${cov}`
          : sym,
      4, 12,
    );
    ctx.restore();

    // A late-started feed can supply just one or two real bars. Name the
    // missing history in the otherwise empty plot instead of stretching a
    // single cluster or implying the earlier tape was captured.
    if (!src.replay && finite(requestedBars) && all.length < requestedBars && columnLeft > 330) {
      const first = finite(src.coverage?.first_bar_t) ? src.coverage!.first_bar_t! : all[0].t;
      ctx.fillStyle = rgba(C.va, 0.13);
      ctx.fillRect(20, plotTop + 22, Math.min(350, columnLeft - 40), 54);
      ctx.fillStyle = C.va;
      ctx.fillRect(20, plotTop + 22, 2, 54);
      ctx.textAlign = "left";
      ctx.font = font(12, 600);
      ctx.fillStyle = C.text;
      ctx.fillText(`${all.length} of ${requestedBars} requested bars available`, 32, plotTop + 44);
      ctx.font = font(11);
      ctx.fillStyle = C.dim;
      ctx.fillText(`Capture from ${clockOf(first)} IST · earlier footprint unavailable`, 32, plotTop + 65,
        Math.min(332, columnLeft - 60));
    }

    /* ---- crosshair + readout ---------------------------------------------- */
    const hover = hoverRef.current;
    if (!hover) return;
    const { x: hx, y: hy } = hover;
    if (hx < columnLeft || hx >= columnRight || hy < plotTop || hy > plotBottom) return;
    const ci = Math.floor((hx - columnLeft) / colW);
    const bar = ci >= 0 && ci < bars.length ? bars[ci] : undefined;
    if (!bar) return;
    const ri = Math.floor((hy - yTop) / rowH);
    const onRow = ri >= 0 && ri < rowCount;
    const rowPrice = maxP - ri * rowSize;

    ctx.strokeStyle = C.dimmer;
    ctx.globalAlpha = 0.7;
    ctx.setLineDash([2, 3]);
    ctx.lineWidth = 1;
    if (onRow) {
      const y = Math.round(yTop + ri * rowH + rowH / 2) + 0.5;
      ctx.beginPath();
      ctx.moveTo(plotLeft, y);
      ctx.lineTo(plotW, y);
      ctx.stroke();
    }
    if (bar) {
      const cxh = Math.round(columnLeft + ci * colW + colW / 2) + 0.5;
      ctx.beginPath();
      ctx.moveTo(cxh, plotTop);
      ctx.lineTo(cxh, plotBottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = C.accent;
      ctx.strokeRect(Math.round(columnLeft + ci * colW) + 0.5, plotTop + 0.5, Math.round(colW) - 1, plotBottom - plotTop - 1);
    }
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    if (onRow) {
      const y = Math.round(yTop + ri * rowH + rowH / 2) + 0.5;
      ctx.fillStyle = C.edge;
      ctx.fillRect(plotW + 1, y - 7, AXIS_W - 2, 14);
      ctx.fillStyle = C.text;
      ctx.font = font(9, 600);
      ctx.textAlign = "right";
      ctx.fillText(price(rowPrice), w - 5, y + 3);
    }
    if (bar) {
      const centre = columnLeft + ci * colW + colW / 2;
      const label = clockOf(bar.t);
      const tw = 34;
      ctx.fillStyle = C.edge;
      ctx.fillRect(clamp(centre - tw / 2, 0, plotW - tw), h - TIME_H + 1, tw, TIME_H - 2);
      ctx.fillStyle = C.text;
      ctx.font = font(9, 600);
      ctx.textAlign = "center";
      ctx.fillText(label, clamp(centre, tw / 2, plotW - tw / 2), h - 4);
    }

    const cell = bar && onRow
      ? (Array.isArray(bar.levels) ? bar.levels : []).find((lv) => finite(lv.p) && Math.abs(lv.p - rowPrice) < rowSize / 2)
      : undefined;
    /* Every marker must be readable as TEXT here, never as colour alone. */
    const rows: { label: string; value: string; color: string }[] = [];
    if (bar) rows.push({ label: "Time", value: stampOf(bar.t), color: C.text });
    if (onRow) rows.push({ label: "Price", value: price(rowPrice), color: C.text });
    if (cell) {
      rows.push({ label: "Bid est", value: vol(Math.max(0, num(cell.bid))), color: monoCells ? C.text : C.sell });
      rows.push({ label: "Ask est", value: vol(Math.max(0, num(cell.ask))), color: monoCells ? C.text : C.buy });
      rows.push({ label: "Δ est", value: signed(num(cell.d)), color: num(cell.d) >= 0 ? C.buy : C.sell });
      if (finite(cell.u) && cell.u! > 0) rows.push({ label: "Unclass", value: vol(cell.u!), color: C.warn });
      const ib = cell.imb_buy === true || (cell.imb_buy === undefined && cell.imb === "buy");
      const is = cell.imb_sell === true || (cell.imb_sell === undefined && cell.imb === "sell");
      if (ib || is) {
        // An empty neighbour row is the STRONGEST imbalance, not a missing
        // one — but its ratio is undefined, so name the case, never print ∞.
        // A null ratio means the neighbour row had no size on the other
        // side — the STRONGEST imbalance, not a missing one. `imb_edge`
        // separates "no such row" from "row present, zero volume". Neither
        // prints as a fabricated infinity.
        const r = finite(cell.imb_ratio) ? ` ${cell.imb_ratio!.toFixed(1)}x`
          : cell.imb_edge === true ? " edge" : " unbounded";
        rows.push({
          label: "Imb", value: `${ib && is ? "BOTH" : ib ? "BUY" : "SELL"}${r}`,
          color: ib && is ? C.warn : ib ? C.buy : C.sell,
        });
      }
      if (cell.poc) rows.push({ label: "POC", value: "bar control", color: C.poc });
      if (cell.va === true) rows.push({ label: "VA", value: "in value", color: C.va });
      if (cell.lvn === true) rows.push({ label: "LVN", value: "rejected", color: C.lvn });
    }
    if (bar) {
      const stacksAt = (Array.isArray(bar.stacks) ? bar.stacks : []).filter(
        (st) => !onRow || (finite(st.from) && finite(st.to)
          && rowPrice >= Math.min(st.from, st.to) - rowSize / 2 && rowPrice <= Math.max(st.from, st.to) + rowSize / 2),
      );
      for (const st of stacksAt.slice(0, 2)) {
        const sup = st.suppressed === true || suppressCompound;
        rows.push({
          label: "Stack",
          value: `${st.side === "buy" ? "BUY" : "SELL"} ${st.rows ?? "?"}r @${finite(st.extreme) ? price(st.extreme) : "?"}${suppressNote(sup, st.reason)}`,
          color: sup ? C.dimmer : st.side === "buy" ? C.buy : C.sell,
        });
      }
      const absAt = (Array.isArray(bar.absorption) ? bar.absorption : []).filter(
        (ab) => !onRow || (finite(ab.price) && Math.abs(ab.price - rowPrice) < rowSize / 2),
      );
      for (const ab of absAt.slice(0, 2)) {
        const sup = ab.suppressed === true || suppressCompound;
        rows.push({
          label: "Absorb",
          value: `${ab.side === "buyers_absorbed" ? "buyers" : "sellers"}${finite(ab.pressure) ? ` p${ab.pressure!.toFixed(2)}` : ""}${suppressNote(sup, ab.reason)}`,
          color: sup ? C.dimmer : ab.side === "buyers_absorbed" ? C.sell : C.buy,
        });
      }
      const exh = bar.exhaustion ?? null;
      if (exh && finite(exh.price)) {
        const sup = exh.suppressed === true || suppressCompound;
        const tests = `${exh.volume_test === false ? "vol✗" : "vol✓"} ${exh.auction_test === true ? "auc✓" : exh.auction_test === false ? "auc✗" : "auc?"}`;
        rows.push({
          label: "Exhaust", value: `${exh.end} ${tests}${suppressNote(sup, exh.reason)}`,
          color: sup ? C.dimmer : exh.end === "high" ? C.sell : C.buy,
        });
      }
      const unf2 = bar.unfinished ?? null;
      if (unf2) {
        // Show the minority-side volume behind the verdict: "weak" versus
        // "unfinished" is a floor test, and the number is what makes it checkable.
        const unfNote = (m: unknown, f: unknown) =>
          (finite(m) ? ` ${vol(m)}${finite(f) ? `/${vol(f)}` : ""}` : "");
        if (unf2.high === "unfinished" || unf2.high === "weak") {
          rows.push({ label: "Unfin hi", value: `${unf2.high}${unfNote(unf2.high_minority, unf2.floor)}`, color: unf2.high === "weak" ? C.dim : C.sell });
        }
        if (unf2.low === "unfinished" || unf2.low === "weak") {
          rows.push({ label: "Unfin lo", value: `${unf2.low}${unfNote(unf2.low_minority, unf2.floor)}`, color: unf2.low === "weak" ? C.dim : C.buy });
        }
      }
      if (!cell) {
        rows.push({ label: "Bar Δ est", value: signed(num(bar.delta)), color: num(bar.delta) >= 0 ? C.buy : C.sell });
        rows.push({ label: "Vol", value: vol(Math.max(0, num(bar.v))), color: C.dim });
      }
      // residual: never a bare inferred number without its residual in reach
      const lvs = Array.isArray(bar.levels) ? bar.levels : [];
      let sum = 0;
      for (const l of lvs) sum += levelVolume(l);
      const resid = finite(bar.u) ? bar.u! : lvs.length ? Math.max(0, num(bar.v) - sum) : 0;
      if (resid > 0) rows.push({ label: "Bar unclass", value: vol(resid), color: C.warn });
      if (finite(bar.vah) && finite(bar.val)) {
        rows.push({
          label: "Bar VA", value: `${priceRange(price(bar.val!), price(bar.vah!))}${finite(bar.va_share) ? ` ${pct(bar.va_share!)}` : ""}`,
          color: C.va,
        });
      }
      const dz = ndScoreOf(bar);
      if (dz !== null) {
        rows.push({
          label: "nd score", value: `${signedFixed(dz, 2)}σ${normLegacy ? " (old est.)" : ""}`,
          color: Math.abs(dz) < 0.75 ? C.dim : dz > 0 ? C.buy : C.sell,
        });
      }
      const rv = rvolOf(bar);
      if (rv !== null) rows.push({ label: "RVOL", value: `${rv.toFixed(2)}x`, color: C.dim });
      if (finite(bar.classified_share)) rows.push({ label: "Bar cls", value: pct(bar.classified_share!), color: finite(clsShare) && bar.classified_share! < clsShare - 0.15 ? C.warn : C.dim });
      // "cls" is how much of the bar got a side; "conf" is how much the three
      // rules agreed on the side they gave. A bar can be 100% classified and
      // still rest entirely on the tick rule, which is what this catches.
      if (finite(bar.confidence)) {
        rows.push({
          label: "Bar conf", value: `${bar.confidence!.toFixed(2)}${lowConfidence(bar) ? " (weak)" : ""}`,
          color: lowConfidence(bar) ? C.warn : C.dim,
        });
      }
      // The mark is an EXACT quantity; the aggressor half it hangs on is not.
      for (const mp of Array.isArray(bar.marked_prints) ? bar.marked_prints : []) {
        rows.push({
          label: mp.kind === "freeze" ? "Freeze cluster" : "Large cluster",
          value: mp.kind === "freeze"
            ? `${vol(mp.s)} @ ${price(mp.p)}${finite(mp.lots) ? ` · ${mp.lots} lots` : ""}`
            : `${vol(mp.s)} @ ${price(mp.p)}${finite(mp.ratio) ? ` · ${mp.ratio!.toFixed(1)}× median` : ""}`,
          color: mp.kind === "freeze" ? C.poc : C.warn,
        });
      }
      const barOfi = ofiCum.get(bar.t);
      if (finite(barOfi)) {
        const ofiFrom = startedLate(src.ofi?.since);
        rows.push({
          label: ofiFrom ? `OFI L1 cum (from ${ofiFrom})` : "OFI L1 cum",
          value: signed(barOfi), color: C.va,
        });
      }
    }
    if (dvgKind && dvg) {
      const sup = dvg.suppressed === true || suppressCompound;
      rows.push({ label: "Diverge", value: `${dvgKind}${suppressNote(sup, dvg.reason)}`, color: sup ? C.dimmer : dvgKind === "bearish" ? C.sell : C.buy });
    }
    if (finite(tail?.cvd)) {
      // The band is a hard bound (the unclassified volume), so say which scope
      // it was measured over when that is not the CVD's own scope.
      const bandScope = src.cvd_band_basis && src.cvd_band_basis !== cvdBasis ? ` ${src.cvd_band_basis === "session" ? "sess" : "win"}` : "";
      rows.push({
        label: anchored ? "CVD est" : "CVD window",
        value: `${signed(num(tail!.cvd))}${cvdBand !== null ? ` ±${vol(cvdBand)}${bandScope}` : ""}`,
        color: num(tail!.cvd) >= 0 ? C.buy : C.sell,
      });
    }
    if (norm) {
      if (finite(norm.flow_score)) {
        const hiEnd = Array.isArray(norm.flow_score_range) && finite(norm.flow_score_range[1]) ? norm.flow_score_range[1] : 100;
        rows.push({
          label: "Flow score", value: `${signedFixed(norm.flow_score, 0)} / ${hiEnd}`,
          color: Math.abs(norm.flow_score) < hiEnd * 0.25 ? C.dim : norm.flow_score > 0 ? C.buy : C.sell,
        });
      }
      if (finite(norm.rvol)) rows.push({ label: "Sym RVOL", value: `${norm.rvol.toFixed(2)}x`, color: C.dim });
      if (norm.confidence?.grade) {
        const reasons = norm.confidence.grade_reasons;
        rows.push({
          label: "Norm conf",
          value: `${norm.confidence.grade}${Array.isArray(reasons) && reasons.length ? ` · ${reasons[0]}` : ""}`,
          color: norm.confidence.grade === "high" ? C.gradeHigh : norm.confidence.grade === "medium" ? C.gradeFair : C.gradeLow,
        });
      }
      if (norm.stale === true) rows.push({ label: "Norm reading", value: "stale", color: C.warn });
    }
    if (!anchored && sessionCvd !== null) {
      rows.push({ label: "Session CVD", value: signed(sessionCvd), color: C.dim });
    }
    if (finite(src.session_weighted_delta)) {
      rows.push({ label: "CVD × conf", value: signed(src.session_weighted_delta!), color: C.dim });
    }
    // Raw OFI is a quantity and means nothing across contracts; the depth-
    // normalised figure is the comparable one, and it is null until enough
    // depth samples exist to divide by — so it is shown only when measured.
    if (finite(src.ofi?.normalised_60s)) {
      rows.push({ label: "OFI/depth 60s", value: signedFixed(src.ofi!.normalised_60s!, 2), color: C.va });
    }
    const speed = src.tape_speed ?? flow?.tape_speed ?? null;
    if (speed && finite(speed.updates_per_s)) {
      rows.push({
        label: "Tape speed",
        value: `${speed.updates_per_s.toFixed(1)}/s${finite(speed.updates_pct) ? ` · P${Math.round(speed.updates_pct!)}` : ""}`,
        color: finite(speed.updates_pct) && speed.updates_pct! >= 80 ? C.warn : C.dim,
      });
    }
    if (grade !== null) {
      rows.push({
        label: "Flow conf",
        value: `${grade}${finite(clsShare) ? ` ${pct(clsShare)}cls` : ""}${clsContradicted ? " ⚠" : ""}`,
        color: gradeColor(grade),
      });
    }
    if (finite(depthShare)) rows.push({ label: "L1 at print", value: pct(depthShare), color: C.dim });
    rows.push({ label: "Aggressor", value: "inferred", color: C.dimmer });
    if (!rows.length) return;

    const lineH = 13;
    // Drop the middle rather than let the panel run off the plot: the identity
    // rows at the top and the confidence rows at the bottom must always survive.
    const maxRows = Math.max(4, Math.floor((plotH - 12) / lineH));
    let shown = rows;
    if (rows.length > maxRows) {
      shown = [...rows.slice(0, maxRows - 3), { label: "", value: `+${rows.length - maxRows + 1} more`, color: C.dimmer }, ...rows.slice(-2)];
    }

    // Size the panel to its widest row so a long value is never sliced open.
    let labelW = 0;
    let valueW = 0;
    for (const row of shown) {
      ctx.font = font(9);
      labelW = Math.max(labelW, ctx.measureText(row.label).width);
      ctx.font = font(10, 600);
      valueW = Math.max(valueW, ctx.measureText(row.value).width);
    }
    const boxW = clamp(Math.ceil(labelW + valueW + 30), 140, Math.max(140, Math.min(300, plotW - 8)));
    const boxH = shown.length * lineH + 10;
    const valLeft = Math.min(bxLabelGap(labelW), boxW - 40);
    const bx = clamp(hx + 14 + boxW > plotW ? hx - 14 - boxW : hx + 14, 2, Math.max(2, plotW - boxW - 2));
    const by = clamp(hy + 14 + boxH > plotBottom ? hy - 14 - boxH : hy + 14, plotTop, Math.max(plotTop, plotBottom - boxH));
    ctx.fillStyle = C.readout;
    ctx.fillRect(bx, by, boxW, boxH);
    ctx.strokeStyle = C.edge;
    ctx.lineWidth = 1;
    ctx.strokeRect(Math.round(bx) + 0.5, Math.round(by) + 0.5, boxW - 1, boxH - 1);
    shown.forEach((row, idx) => {
      const ty = by + 6 + (idx + 1) * lineH - 4;
      ctx.font = font(9);
      ctx.textAlign = "left";
      ctx.fillStyle = C.dimmer;
      ctx.fillText(row.label, bx + 7, ty);
      ctx.font = font(10, 600);
      ctx.textAlign = "right";
      ctx.fillStyle = row.color;
      // Trim from the RIGHT so the leading digits — the ones that carry the
      // magnitude — survive; a left-clipped number reads as a different number.
      let text = row.value;
      const room = boxW - 12 - valLeft;
      if (ctx.measureText(text).width > room) {
        while (text.length > 1 && ctx.measureText(`${text}…`).width > room) text = text.slice(0, -1);
        text = `${text}…`;
      }
      ctx.fillText(text, bx + boxW - 7, ty);
    });
  }, [requestedBars]);

  const schedule = useCallback(() => {
    if (frameRef.current) return;
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = 0;
      draw();
    });
  }, [draw]);

  useEffect(() => {
    dataRef.current = data;
    const len = Array.isArray(data?.bars) ? data.bars.length : 0;
    const meta = metaRef.current;
    const rowSize = finite(data?.row_size) && data.row_size! > 0
      ? data.row_size! : (finite(data?.tick_size) && data.tick_size > 0 ? data.tick_size : 0.05);
    if (meta.symbol !== data.symbol || meta.rowSize !== rowSize) {
      priceViewRef.current = { center: null, rows: null, follow: true };
    }
    if (!len) {
      viewRef.current = { start: 0, count: 0 };
    } else if (meta.symbol !== data.symbol || viewRef.current.count === 0) {
      const count = clamp(Math.min(len, DEFAULT_BARS), Math.min(MIN_BARS, len), len);
      viewRef.current = { start: Math.max(0, len - count), count };
    } else {
      // new bars arriving: keep the right edge pinned if that is where we were
      const count = clamp(viewRef.current.count, Math.min(MIN_BARS, len), len);
      const pinned = meta.len > 0 && viewRef.current.start + viewRef.current.count >= meta.len;
      const next = pinned ? len - count : viewRef.current.start;
      viewRef.current = { start: clamp(next, 0, Math.max(0, len - count)), count };
    }
    metaRef.current = { symbol: data?.symbol ?? "", len, rowSize };
    schedule();
  }, [data, height, schedule]);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box) return;
      sizeRef.current = { w: Math.round(box.width), h: Math.round(box.height) };
      schedule();
    });
    observer.observe(wrap);
    sizeRef.current = { w: wrap.clientWidth, h: wrap.clientHeight };
    schedule();
    return () => observer.disconnect();
  }, [schedule]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const plotWidth = () => Math.max(40, canvas.getBoundingClientRect().width - AXIS_W);

    const onWheel = (event: WheelEvent) => {
      const len = dataRef.current?.bars?.length ?? 0;
      if (!len) return;
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const priceDraw = priceDrawRef.current;
      if (event.shiftKey) {
        const rows = Math.max(1, Math.min(20, Math.round(Math.abs(event.deltaY) / 45)));
        priceViewRef.current = {
          ...priceViewRef.current, center: priceDraw.center - Math.sign(event.deltaY) * rows * priceDraw.rowSize,
          follow: false,
        };
        schedule();
        return;
      }
      if (event.altKey || event.clientX - rect.left >= plotWidth()) {
        const zoomOut = event.deltaY > 0;
        const current = priceViewRef.current.rows ?? priceDraw.rows;
        let rows = Math.round(current * (zoomOut ? 1.2 : 1 / 1.2));
        if (rows === current) rows += zoomOut ? 1 : -1;
        rows = clamp(rows, Math.min(3, priceDraw.fullRows), priceDraw.fullRows);
        priceViewRef.current = { center: priceDraw.center, rows, follow: false };
        schedule();
        return;
      }
      const current = viewRef.current;
      const columns = footprintColumns(plotWidth(), current.count);
      const frac = clamp((event.clientX - rect.left - columns.left) / columns.usedWidth, 0, 1);
      const zoomOut = event.deltaY > 0;
      let count = Math.round(current.count * (zoomOut ? 1.2 : 1 / 1.2));
      if (count === current.count) count = current.count + (zoomOut ? 1 : -1);
      count = clamp(count, Math.min(MIN_BARS, len), len);
      const anchor = current.start + frac * current.count;
      const start = clamp(Math.round(anchor - frac * count), 0, Math.max(0, len - count));
      viewRef.current = { start, count };
      schedule();
    };

    const onDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const rect = canvas.getBoundingClientRect();
      if (event.clientX - rect.left >= plotWidth()) {
        const drawn = priceDrawRef.current;
        dragRef.current = { mode: "price", y: event.clientY, center: drawn.center,
          rowHeight: drawn.rowHeight, rowSize: drawn.rowSize };
        priceViewRef.current = { ...priceViewRef.current, center: drawn.center, follow: false };
      } else {
        dragRef.current = { mode: "time", x: event.clientX, start: viewRef.current.start };
      }
      canvas.setPointerCapture(event.pointerId);
      canvas.style.cursor = "grabbing";
    };

    const onMove = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      hoverRef.current = { x, y };
      const drag = dragRef.current;
      const len = dataRef.current?.bars?.length ?? 0;
      if (drag?.mode === "price") {
        priceViewRef.current = {
          ...priceViewRef.current,
          center: drag.center + (event.clientY - drag.y) / Math.max(1, drag.rowHeight) * drag.rowSize,
          follow: false,
        };
      } else if (drag && len) {
        const colW = footprintColumns(plotWidth(), viewRef.current.count).width;
        const shift = Math.round((event.clientX - drag.x) / colW);
        const count = viewRef.current.count;
        viewRef.current = { start: clamp(drag.start - shift, 0, Math.max(0, len - count)), count };
      }
      if (drag) {
        schedule();
      } else {
        // A full high-DPI canvas repaint for every mouse pixel is expensive.
        // The tooltip's data changes only when its price row or bar changes.
        const drawn = priceDrawRef.current;
        const bar = x >= drawn.columnLeft && x < drawn.columnRight
          ? Math.min(drawn.bars - 1, Math.floor((x - drawn.columnLeft) / drawn.columnWidth)) : -1;
        const row = bar >= 0 && y >= drawn.yTop && y < drawn.yTop + drawn.rows * drawn.rowHeight
          ? Math.floor((y - drawn.yTop) / drawn.rowHeight) : -1;
        if (bar !== hoverCellRef.current.bar || row !== hoverCellRef.current.row) {
          hoverCellRef.current = { bar, row };
          schedule();
        }
      }
    };

    const onDoubleClick = () => {
      priceViewRef.current = { center: null, rows: null, follow: true };
      schedule();
    };

    const endDrag = (event: PointerEvent) => {
      if (dragRef.current && canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      dragRef.current = null;
      canvas.style.cursor = "crosshair";
    };

    const onLeave = (event: PointerEvent) => {
      endDrag(event);
      hoverRef.current = null;
      hoverCellRef.current = { bar: -1, row: -1 };
      schedule();
    };

    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("dblclick", onDoubleClick);
    return () => {
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", endDrag);
      canvas.removeEventListener("pointercancel", endDrag);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("dblclick", onDoubleClick);
    };
  }, [schedule]);

  useEffect(() => () => {
    if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
  }, []);

  return (
    <div
      ref={wrapRef}
      className="footprint-wrap"
      style={{
        position: "relative", width: "100%", height, minHeight: 160,
        background: C.chart, border: `1px solid ${C.border}`, borderRadius: 4,
        overflow: "hidden", fontVariantNumeric: "tabular-nums",
      }}
    >
      <canvas
        ref={canvasRef}
        title="Bid and ask are inferred from quote and trade updates. Wheel over chart: time zoom; wheel over price axis or Alt+wheel: price zoom; Shift+wheel or drag price axis: price pan; double-click: follow last price."
        aria-label="Footprint chart with estimated bid and ask volume. Wheel to zoom time, drag price axis to pan price, double-click to follow last price."
        style={{ display: "block", width: "100%", height: "100%", cursor: "crosshair", touchAction: "none" }}
      />
    </div>
  );
}
