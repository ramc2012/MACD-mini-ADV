"""Per-bar order-flow clusters — the data behind a footprint chart.

A footprint (cluster / bid-ask) chart shows, for every price inside every bar,
how much volume traded into the bid versus into the ask. Conventions follow
the platforms traders actually use (Sierra Chart, ATAS, Jigsaw):

* **bid volume** = seller-initiated prints (hit the bid)
* **ask volume** = buyer-initiated prints (lifted the offer)
* **delta** = ask − bid, per price, per bar, and cumulative across the session
* **diagonal imbalance** — the standard read. Ask volume at price P is compared
  with bid volume at P−1 row (buyers paying up against resting sellers one
  row lower). A ratio at or above ``IMBALANCE_RATIO`` marks the level.
  Comparing the same price level to itself is the common beginner error; the
  diagonal is what the platforms flag.

Footprints are kept only for a bounded **detail set** of symbols. Holding
per-bar clusters for ~670 contracts would cost hundreds of megabytes for data
nobody is looking at; real platforms subscribe depth when you open a chart, and
so does this.

--------------------------------------------------------------------------
THE CONSTRAINT EVERY MARKER BELOW IS WRITTEN UNDER
--------------------------------------------------------------------------
NSE publishes trade price, quantity, time and the best bid/ask. It NEVER
publishes who was the aggressor. Every bid/ask split, delta, imbalance, CVD,
absorption and divergence here is an INFERENCE (Lee-Ready: quote rule, then
mid, then tick test, with zero-tick carry).

That splits the marker set in two, and the split is published structurally in
``payload["basis"]`` so a client cannot render an estimate with the authority
of a measurement:

* **basis "volume"** — computed from ``bid + ask + unclassified`` per row, i.e.
  total volume at price, which the exchange publishes exactly: bar volume, POC,
  value area, LVNs, single prints. These carry no aggressor inference at all.
* **basis "inferred"** — everything that depends on the aggressor split: delta,
  CVD, imbalance, stacks, unfinished auction, absorption, exhaustion,
  divergence. Estimates with a confidence, never measurements.

--------------------------------------------------------------------------
THE PRICE GRID
--------------------------------------------------------------------------
Levels are keyed by an INTEGER, never by a float price. Two separate bugs died
with that change:

* ``round(price - tick_size, 2)`` hard-coded two decimal places, so the
  diagonal neighbour lookup addressed a bucket that cannot exist on any
  sub-paisa instrument — and on a 0.10-tick instrument it addressed P−0.05,
  where no trade can ever occur, so the buy branch was structurally dead.
* Float arithmetic on prices drifts (the codebase already documents
  ``100.20 - 100.00 = 0.20000000000000284`` in ``orderflow.absorption``).

Storage is at a fixed fine quantum (``PRICE_QUANTUM``, 0.0001) so the ladder is
exact and instrument-independent. The DISPLAY grid — the row — is chosen at
read time as ``row_size = tick_size * row_ticks``, which is what lets one
instrument show 1 tick per row and a 78,000-point index future show 50, and
lets a tick estimate that sharpens during the session apply to bars already
captured.
"""
from __future__ import annotations

from bisect import bisect_left, insort
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from math import gcd

from .market_profile import IST, VALUE_AREA_FRACTION
from .orderflow import ABSORPTION_MIN_PRESSURE, MIN_DIVERGENCE_FRACTION
from .whale import LARGE_PRINT_MULTIPLE, MIN_LARGE_PRINT_LOTS, freeze_quantity, lot_size

SPEC_VERSION = 1

# A bar whose volume-weighted classification confidence sits below this is
# shaded on the chart. Between the lone-tick verdict (0.4) and the uncontested
# quote-rule one (0.7): a bar dominated by tick-rule and contested prints is
# flagged, one carried by quote evidence is not.
LOW_CONFIDENCE_BAR = 0.55
# Tape rows needed before "ten times the median print" means anything.
LARGE_PRINT_MIN_TAPE = 30

IMBALANCE_RATIO = 3.0        # ATAS/Jigsaw default
MIN_IMBALANCE_VOLUME = 20    # ignore imbalances built on a handful of lots
# 20 lots is sub-lot on a NIFTY future and material on a thin weekly option, so
# the absolute floor is only the lower bound — the effective floor scales with
# the bar's own median row volume and is published as bar["imb_floor"].
IMBALANCE_VOLUME_FRACTION = 0.25
STACKED_IMBALANCE_MIN = 3    # ATAS/Sierra convention
UNFINISHED_MIN_FRACTION = 0.02
ABSORB_VOLUME_MULT = 2.0
EXHAUST_VOLUME_FRACTION = 0.25
EXHAUST_MIN_RANGE_ROWS = 4
LVN_FRACTION = 0.15
LVN_MIN_ROWS = 6
DIVERGENCE_LOOKBACK = 20
DIVERGENCE_EDGE_ROWS = 2

# Production capture uses one-minute base bars and aggregates on read. A full
# NSE session is 375 minutes; retain a little headroom without unbounded growth.
MAX_BARS = 420
MAX_DETAIL_SYMBOLS = 16
TAPE_LENGTH = 200

# --- the price grid -------------------------------------------------------
# Ladder storage quantum. Every price becomes an integer count of these, so
# neighbour lookup is integer arithmetic and cannot drift.
QUANTA_PER_UNIT = 10_000
PRICE_QUANTUM = 1.0 / QUANTA_PER_UNIT
# Ticks per displayed row. No professional platform draws a footprint on the
# raw exchange tick; all of them expose ticks-per-row.
ROW_TICKS_LADDER = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)
ROW_TARGET_ROWS = 30
# Recompute the auto row size only when the bar range moves this far from the
# value the current choice was made for. A grid that re-chooses every 3s poll
# makes every stack and shelf jump.
ROW_RECHOICE_FACTOR = 2.0

# Plausible exchange ticks, for snapping an observed price granularity.
TICK_LADDER = (0.0001, 0.0005, 0.001, 0.0025, 0.005, 0.01,
               0.05, 0.10, 0.25, 0.50, 1.0, 5.0)
# Distinct prices needed before an inferred tick is worth publishing. Below
# this the greatest common divisor of a handful of prices says more about the
# sample than about the instrument.
MIN_TICK_SAMPLES = 6
MAX_TICK_SAMPLES = 4096

# Confidence gates for compound (conjunction-of-inferences) markers.
STACK_MIN_CLASSIFIED_SHARE = 0.85
STACK_MIN_QUOTE_SHARE = 0.50

# Which published fields are exact and which are inferred. Published so the
# client renders the distinction structurally instead of guessing.
BASIS = {
    "volume": ["v", "u", "poc", "vah", "val", "va_share", "lvn",
               "single_print_rows", "rows", "marked_prints"],
    "inferred": ["bid", "ask", "d", "delta", "cvd", "wdelta", "wcvd", "imb", "imb_buy",
                 "imb_sell", "imb_ratio", "stacks", "unfinished",
                 "absorption", "exhaustion", "divergence_bars",
                 "confidence", "low_confidence"],
}


# --------------------------------------------------------------------------
# grid helpers
# --------------------------------------------------------------------------

def quantise(price: float) -> int:
    """Price -> integer ladder key. Exact, and free of float neighbour drift."""
    return int(round(price * QUANTA_PER_UNIT))


def row_units_for(row_size: float) -> int:
    """Quanta per displayed row. At least one."""
    return max(1, int(round(row_size * QUANTA_PER_UNIT)))


def row_index(quantum: int, row_units: int) -> int:
    """Integer row index. Absolute (never relative to the bar), so rows line up
    across bars and across polls."""
    if quantum >= 0:
        return (quantum + row_units // 2) // row_units
    return -((-quantum + row_units // 2) // row_units)


def row_price(k: int, row_units: int) -> float:
    return round((k * row_units) / float(QUANTA_PER_UNIT), 6)


def _median(values) -> float:
    return _median_sorted(sorted(values))


def _median_sorted(ordered) -> float:
    """Median of an ALREADY sorted sequence."""
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


class TickSizeEstimator:
    """Infer an instrument's tick from the prices the process already sees.

    Nothing in this codebase resolves a tick size per instrument; three modules
    each hard-coded 0.05 independently. A wrong tick is not cosmetic: the
    diagonal imbalance compares ask at P against bid at P±1 tick, so a tick
    finer than the instrument's addresses a bucket that can never be populated
    and the marker cannot fire at all (an audit measured 0 of 165 cells flagged
    on an affected contract).

    Rather than guess from a table of instrument classes, take the greatest
    common divisor of the observed prices: the exchange's own granularity,
    measured. The estimate is monotone — it can only sharpen as more distinct
    prices arrive — and it is published with its sample count and a source
    label so a reader can see whether it was measured or defaulted.
    """

    def __init__(self, default: float = 0.05):
        self.default = default
        self._prices: dict[str, set] = {}
        self._cache: dict[str, tuple] = {}

    def observe(self, symbol: str, *prices) -> None:
        seen = self._prices.get(symbol)
        if seen is None:
            seen = self._prices[symbol] = set()
        for price in prices:
            if price is None or price <= 0:
                continue
            if len(seen) >= MAX_TICK_SAMPLES:
                break
            quantum = quantise(price)
            if quantum not in seen:
                seen.add(quantum)
                self._cache.pop(symbol, None)

    def forget(self, symbol: str) -> None:
        self._prices.pop(symbol, None)
        self._cache.pop(symbol, None)

    def clear(self) -> None:
        self._prices.clear()
        self._cache.clear()

    def estimate(self, symbol: str) -> tuple:
        """(tick_size, source, samples).

        ``source`` is "observed" when the tick was measured from prices and
        "default" when it could not be — the caller publishes that word rather
        than passing off the fallback as a measurement.
        """
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached
        seen = self._prices.get(symbol) or set()
        samples = len(seen)
        if samples < MIN_TICK_SAMPLES:
            result = (self.default, "default", samples)
            self._cache[symbol] = result
            return result
        ordered = sorted(seen)
        base = ordered[0]
        divisor = 0
        for quantum in ordered[1:]:
            divisor = gcd(divisor, quantum - base)
        if divisor <= 0:
            result = (self.default, "default", samples)
            self._cache[symbol] = result
            return result
        snapped = [tick for tick in TICK_LADDER
                   if divisor % max(1, int(round(tick * QUANTA_PER_UNIT))) == 0]
        tick = max(snapped) if snapped else divisor / float(QUANTA_PER_UNIT)
        result = (tick, "observed", samples)
        self._cache[symbol] = result
        return result


# --------------------------------------------------------------------------
# the bar
# --------------------------------------------------------------------------

@dataclass
class FootprintBar:
    """One time bucket's ladder.

    ``levels`` maps the integer ladder key (see :func:`quantise`) to
    ``[bid_volume, ask_volume, unclassified_volume]``. The third slot is the
    conservation fix: ``add`` used to credit ``self.volume`` for a sideless
    print and write nothing to the ladder, so the bar lost that volume with no
    field from which any reader could recover it (measured live: v = 1980
    against Σ(bid+ask) = 1900, the gap exactly the one side-0 print in the
    tape). Σ(bid + ask + u) == v is now an invariant, and it matters beyond
    tidiness: the value area must be computed on TOTAL volume at price.

    o/h/l/c are RAW prices, never grid prices. Once a row aggregates several
    ticks a bucketed high is wrong by up to a row, and the unfinished-auction
    test would then key off a quantised extreme.
    """
    start: int                       # bucket start, epoch seconds
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    delta: float = 0.0
    cvd: float = 0.0                 # session cumulative delta as of this bar's close
    unclassified: float = 0.0        # volume the classifier could not side
    trades: int = 0
    levels: dict = field(default_factory=dict)   # ladder key -> [bid, ask, u]
    # How the prints in this bar were classified. "unknown" counts prints that
    # reached the book without a method label; the whole dict is published as
    # None when nothing is known, never as a zeroed dict that reads as "no
    # quote-ruled prints".
    methods: dict = field(default_factory=lambda: {
        "quote": 0, "mid": 0, "tick": 0, "zero_tick": 0, "pending": 0,
        "conflict": 0, "unknown": 0})
    # Σ confidence·size and Σ size over the prints that were actually SIDED,
    # so the bar's figure is a volume-weighted mean and never counts a print
    # that predates the score as a confident one. Sideless prints are excluded
    # rather than folded in at 0.0: combine_votes returns confidence 0.0 for
    # the "unknown" verdict, so counting them made `confidence` a restatement
    # of coverage. The two are separate readings and the chart shades on both
    # -- a bar sided entirely by the uncontested quote rule (0.7, the strongest
    # single-rule verdict) published 0.35 "weak" the moment half its volume
    # could not be sided at all, which is a false statement about agreement.
    conf_sum: float = 0.0
    conf_volume: float = 0.0
    # Confidence-weighted delta and its session running total (see
    # FlowState.weighted_delta for why two CVDs are kept).
    wdelta: float = 0.0
    wcvd: float = 0.0
    # Freeze-size and large prints, published per bar so the chart can put a
    # glyph on the cell they hit.
    marked: list = field(default_factory=list)
    version: int = 0
    _cache: tuple | None = field(default=None, repr=False, compare=False)

    def add(self, price: float, size: float, side: int, method: str | None = None,
            confidence: float | None = None, mark: dict | None = None) -> None:
        if self.volume == 0.0 and self.trades == 0:
            self.open = self.high = self.low = self.close = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += size
        self.trades += 1
        self.methods[method if method in self.methods else "unknown"] += 1
        row = self.levels.setdefault(quantise(price), [0.0, 0.0, 0.0])
        if side > 0:
            row[1] += size           # lifted the ask
            self.delta += size
        elif side < 0:
            row[0] += size           # hit the bid
            self.delta -= size
        else:
            row[2] += size           # no side could be inferred — keep it
            self.unclassified += size
        if confidence is not None and side:
            self.conf_sum += confidence * size
            self.conf_volume += size
            self.wdelta += side * size * confidence
        if mark is not None:
            self.marked.append(mark)
        self.version += 1
        self._cache = None

    def merge(self, other: "FootprintBar") -> None:
        """Merge a finer bar into this read-only aggregate."""
        if self.volume == 0.0 and self.trades == 0:
            self.open = other.open
            self.high = other.high
            self.low = other.low
        else:
            self.high = max(self.high, other.high)
            self.low = min(self.low, other.low)
        self.close = other.close
        self.volume += other.volume
        self.delta += other.delta
        self.unclassified += other.unclassified
        self.trades += other.trades
        self.cvd = other.cvd
        self.conf_sum += other.conf_sum
        self.conf_volume += other.conf_volume
        self.wdelta += other.wdelta
        self.wcvd = other.wcvd
        self.marked.extend(other.marked)
        for name, count in other.methods.items():
            self.methods[name] = self.methods.get(name, 0) + count
        for key, volumes in other.levels.items():
            row = self.levels.setdefault(key, [0.0, 0.0, 0.0])
            row[0] += volumes[0]
            row[1] += volumes[1]
            row[2] += volumes[2]
        self.version += 1
        self._cache = None

    # -- ladder views ------------------------------------------------------

    def rows(self, row_units: int) -> dict:
        """Ladder aggregated onto the display grid: row index -> [bid, ask, u]."""
        grid: dict = {}
        for key, volumes in self.levels.items():
            k = row_index(key, row_units)
            row = grid.setdefault(k, [0.0, 0.0, 0.0])
            row[0] += volumes[0]
            row[1] += volumes[1]
            row[2] += volumes[2]
        return grid

    @property
    def poc(self) -> float | None:
        """Price with the most traded volume in this bar (raw ladder key)."""
        if not self.levels:
            return None
        keys = list(self.levels)
        centre = (min(keys) + max(keys)) / 2.0
        best = max(keys, key=lambda key: (sum(self.levels[key]),
                                          -abs(key - centre), key))
        return round(best / float(QUANTA_PER_UNIT), 6)

    @property
    def method_counts(self) -> dict | None:
        """None when no print in the bar carried a classification method."""
        known = sum(count for name, count in self.methods.items() if name != "unknown")
        if not known:
            return None
        return dict(self.methods)

    @property
    def classified_share(self) -> float | None:
        """Share of this bar's volume the classifier could side. Exactly
        measurable from the ladder; None (never 0.0) when there is no volume."""
        if self.volume <= 0:
            return None
        return round((self.volume - self.unclassified) / self.volume, 4)

    @property
    def confidence(self) -> float | None:
        """Volume-weighted agreement over the prints that GOT a side.

        None (never 0.0) when no sided print in the bar carried a score: zero
        would read as "every vote failed", which is a claim about prints nobody
        scored. How much of the bar could be sided at all is a different
        question and `classified_share` answers it."""
        if self.conf_volume <= 0:
            return None
        return round(self.conf_sum / self.conf_volume, 3)

    # -- the marker set ----------------------------------------------------

    def payload(self, row_units: int, row_size: float,
                grade: str | None = None,
                quote_share: float | None = None) -> dict:
        key = (row_units, round(row_size, 8), grade, quote_share, self.version)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        built = self._build(row_units, row_size, grade, quote_share)
        self._cache = (key, built)
        return built

    def _build(self, row_units: int, row_size: float,
               grade: str | None, quote_share: float | None) -> dict:
        grid = self.rows(row_units)
        ordered = sorted(grid)
        totals = {k: grid[k][0] + grid[k][1] + grid[k][2] for k in ordered}
        classified_share = self.classified_share

        poc_k = _poc_row(grid, totals, ordered)
        vah_k, val_k, va_share = _value_area(totals, ordered, poc_k)
        va_rows = set()
        if vah_k is not None and val_k is not None:
            va_rows = {k for k in ordered if val_k <= k <= vah_k}

        imb_floor = _imbalance_floor(totals)
        flags = _imbalance_flags(grid, ordered, imb_floor)
        stacks = _stacks(flags, totals, ordered, row_units)
        unfinished = _unfinished(grid, ordered, self.volume, self.high, self.low)
        lvn_rows, lvn_zones = _low_volume_nodes(totals, ordered, poc_k, row_units)
        absorption = _absorption(grid, totals, ordered, poc_k, self.close,
                                 row_size, row_units)
        exhaustion = _exhaustion(totals, ordered, poc_k, unfinished,
                                 self.open, self.close, self.high, self.low,
                                 row_units)

        stack_reason = _compound_suppression(
            grade, classified_share, quote_share, self.method_counts,
            min_classified=STACK_MIN_CLASSIFIED_SHARE,
            min_quote=STACK_MIN_QUOTE_SHARE)
        soft_reason = _compound_suppression(grade, classified_share, quote_share,
                                            self.method_counts)
        for stack in stacks:
            stack["suppressed"] = stack_reason is not None
            stack["reason"] = stack_reason
        for hit in absorption:
            hit["suppressed"] = soft_reason is not None
            hit["reason"] = soft_reason
        if exhaustion is not None:
            exhaustion["suppressed"] = soft_reason is not None
            exhaustion["reason"] = soft_reason

        levels = []
        for k in sorted(ordered, reverse=True):
            bid, ask, unclassified = grid[k]
            flag = flags[k]
            price = row_price(k, row_units)
            levels.append({
                "k": k, "p": price,
                "bid": round(bid), "ask": round(ask), "u": round(unclassified),
                "d": round(ask - bid),
                "imb": flag["imb"],
                "imb_buy": flag["imb_buy"], "imb_sell": flag["imb_sell"],
                "imb_ratio": flag["imb_ratio"],
                "imb_edge": flag["imb_edge"],
                "poc": k == poc_k,
                "va": k in va_rows,
                "lvn": k in lvn_rows,
            })

        confidence = self.confidence
        return {
            "t": self.start,
            "o": self.open, "h": self.high, "l": self.low, "c": self.close,
            "v": round(self.volume), "u": round(self.unclassified),
            "delta": round(self.delta), "cvd": round(self.cvd),
            "wdelta": round(self.wdelta), "wcvd": round(self.wcvd),
            "confidence": confidence,
            "low_confidence": confidence is not None and confidence < LOW_CONFIDENCE_BAR,
            "marked_prints": list(self.marked),
            "trades": self.trades,
            "rows": len(ordered),
            "poc": row_price(poc_k, row_units) if poc_k is not None else None,
            "vah": row_price(vah_k, row_units) if vah_k is not None else None,
            "val": row_price(val_k, row_units) if val_k is not None else None,
            "va_share": va_share,
            "va_method": "single_row_alternating",
            "imb_floor": round(imb_floor, 2),
            "stacks": stacks,
            "unfinished": unfinished,
            "absorption": absorption,
            "exhaustion": exhaustion,
            "lvn": lvn_zones,
            "methods": self.method_counts,
            "classified_share": classified_share,
            "levels": levels,
        }


# --------------------------------------------------------------------------
# marker computation — all server side; the client renders, it does not derive
# --------------------------------------------------------------------------

def _poc_row(grid: dict, totals: dict, ordered: list) -> int | None:
    """Highest-volume row, tie broken the way ``market_profile.Profile.poc``
    breaks it: nearest the bar's centre, then by price.

    ``max(self.levels, key=...)`` broke ties by dict insertion order — i.e. by
    whichever price happened to print first — so the POC could move between
    polls without a single trade. Two POCs on one screen should be computed by
    one rule.
    """
    if not ordered:
        return None
    centre = (ordered[0] + ordered[-1]) / 2.0
    return max(ordered, key=lambda k: (totals[k], -abs(k - centre), k))


def _value_area(totals: dict, ordered: list, poc_k: int | None):
    """VAH/VAL by the alternating expansion already used at session scale.

    Computed on TOTAL volume at price (bid + ask + unclassified), which the
    exchange publishes exactly — the value area carries no aggressor
    inference. ``va_share`` is the share actually enclosed, never assumed to be
    the 70% target.
    """
    if poc_k is None or len(ordered) < 2:
        return None, None, None
    total = sum(totals.values())
    if total <= 0:
        return None, None, None
    target = total * VALUE_AREA_FRACTION
    index = ordered.index(poc_k)
    low_i = high_i = index
    included = totals[poc_k]
    while included < target and (low_i > 0 or high_i < len(ordered) - 1):
        below = totals[ordered[low_i - 1]] if low_i > 0 else -1
        above = totals[ordered[high_i + 1]] if high_i < len(ordered) - 1 else -1
        if above >= below:
            high_i += 1
            included += totals[ordered[high_i]]
        else:
            low_i -= 1
            included += totals[ordered[low_i]]
    return ordered[high_i], ordered[low_i], round(included / total, 4)


def _imbalance_floor(totals: dict) -> float:
    """Effective minimum volume for an imbalance flag, published per bar.

    The absolute ``MIN_IMBALANCE_VOLUME`` is only a lower bound; the binding
    term scales with the bar's own median row so the same constant means
    something comparable on a NIFTY future and on a thin weekly option.
    """
    if not totals:
        return float(MIN_IMBALANCE_VOLUME)
    return max(float(MIN_IMBALANCE_VOLUME),
               IMBALANCE_VOLUME_FRACTION * _median(totals.values()))


def _imbalance_flags(grid: dict, ordered: list, imb_floor: float) -> dict:
    """Diagonal imbalance per row, both diagonals, independently.

    Three repairs against the previous implementation:

    * an absent or zero neighbour is the STRONGEST imbalance, not "no
      imbalance" — heavy ask over a row with no bid at all is what every
      platform flags. ``imb_ratio`` is published as None there rather than a
      fabricated infinity.
    * the two diagonals test different cells, so a row can satisfy both. The
      old ``elif`` discarded the sell finding on such a row.
    * the volume floor scales with the bar (see :func:`_imbalance_floor`).

    One deliberate qualification of the "empty neighbour fires" rule. An empty
    row INSIDE the bar's traded range is evidence — the auction went through
    there and nothing was resting. A neighbour OUTSIDE the range is not
    evidence of anything; the bar simply never reached it. Flagging the
    outward diagonal of the top and bottom rows would therefore mark almost
    every bar's own extremes, which are exactly the rows a stack must not be
    fabricated at. Those cells are published as unevaluable (``imb_edge``),
    not as a failed test.
    """
    flags = {}
    lowest, highest = ordered[0], ordered[-1]
    for k in ordered:
        bid, ask, _ = grid[k]
        below = grid.get(k - 1)
        above = grid.get(k + 1)
        buy_edge = k == lowest
        sell_edge = k == highest
        buy_ratio = sell_ratio = None
        imb_buy = imb_sell = False
        if ask >= imb_floor and not buy_edge:
            resting = below[0] if below else 0.0
            if resting <= 0:
                imb_buy = True                       # nothing resting: infinite
            else:
                buy_ratio = ask / resting
                imb_buy = buy_ratio >= IMBALANCE_RATIO
        if bid >= imb_floor and not sell_edge:
            resting = above[1] if above else 0.0
            if resting <= 0:
                imb_sell = True
            else:
                sell_ratio = bid / resting
                imb_sell = sell_ratio >= IMBALANCE_RATIO
        dominant = None
        ratio = None
        if imb_buy and imb_sell:
            # An unmeasurable (infinite) ratio outranks any finite one; a tie
            # on finite ratios goes to "buy", matching the old field's bias.
            buy_rank = float("inf") if buy_ratio is None else buy_ratio
            sell_rank = float("inf") if sell_ratio is None else sell_ratio
            dominant = "buy" if buy_rank >= sell_rank else "sell"
        elif imb_buy:
            dominant = "buy"
        elif imb_sell:
            dominant = "sell"
        if dominant == "buy":
            ratio = buy_ratio
        elif dominant == "sell":
            ratio = sell_ratio
        flags[k] = {
            "imb": dominant,
            "imb_buy": imb_buy, "imb_sell": imb_sell,
            "imb_ratio": round(ratio, 3) if ratio is not None else None,
            # True where one diagonal could not be tested because its
            # neighbour lies outside the bar's traded range.
            "imb_edge": buy_edge or sell_edge,
        }
    return flags


def _stacks(flags: dict, totals: dict, ordered: list, row_units: int) -> list:
    """Runs of N+ CONSECUTIVE same-side imbalanced rows.

    A missing row breaks the run — it is not skipped and it does not count as
    imbalanced. The far edge of the run is the level a desk marks and defends,
    so the run is published as an addressable zone rather than as loose
    per-row flags.
    """
    if not ordered:
        return []
    found = []
    for side, key in (("buy", "imb_buy"), ("sell", "imb_sell")):
        run: list = []
        for k in ordered:
            flagged = flags[k][key]
            if flagged and (not run or k == run[-1] + 1):
                run.append(k)
                continue
            if len(run) >= STACKED_IMBALANCE_MIN:
                found.append(_stack_entry(side, run, totals, row_units))
            run = [k] if flagged else []
        if len(run) >= STACKED_IMBALANCE_MIN:
            found.append(_stack_entry(side, run, totals, row_units))
    found.sort(key=lambda entry: entry["k_from"])
    return found


def _stack_entry(side: str, run: list, totals: dict, row_units: int) -> dict:
    k_from, k_to = run[0], run[-1]
    extreme_k = k_to if side == "buy" else k_from
    return {
        "side": side,
        "k_from": k_from, "k_to": k_to, "rows": len(run),
        "from": row_price(k_from, row_units), "to": row_price(k_to, row_units),
        "extreme": row_price(extreme_k, row_units),
        "volume": round(sum(totals.get(k, 0.0) for k in run)),
        "suppressed": False, "reason": None,
    }


def _unfinished(grid: dict, ordered: list, volume: float,
                high: float, low: float) -> dict:
    """Unfinished auction at the bar's extremes.

    The auction FINISHES at a high when the top row shows buyers stopped paying
    up — zero ask volume there. If both sides traded at the extreme the auction
    is unfinished and the market is expected to return to complete it.

    Three-state, not boolean: the aggressor is inferred, so one stray print at
    the extreme would otherwise flip a claim. And the extremes are exactly
    where the tick rule is weakest — price moving fastest, quotes stalest.
    """
    blank = {"high": None, "low": None,
             "high_price": high if ordered else None,
             "low_price": low if ordered else None,
             "floor": None, "high_minority": None, "low_minority": None}
    if len(ordered) < 2:
        return blank
    floor = max(1.0, UNFINISHED_MIN_FRACTION * volume)
    result = dict(blank)
    result["floor"] = round(floor, 2)
    for end, k in (("high", ordered[-1]), ("low", ordered[0])):
        bid, ask, _ = grid[k]
        if bid <= 0 and ask <= 0:
            result[end] = None       # wholly unclassified: "finished" is a claim
            continue
        if bid > 0 and ask > 0:
            # Published beside the verdict, because the floor is a fraction of
            # the WHOLE bar while the evidence is one row: on a 28-row bar an
            # extreme row's minority side clears 2% of the bar only rarely, so
            # "weak" is the common answer and a reader should be able to see
            # by how much rather than take the word on trust.
            result[end + "_minority"] = round(min(bid, ask))
            result[end] = "unfinished" if min(bid, ask) >= floor else "weak"
        else:
            # WHICH side is empty decides this, not merely that one of them is.
            # The auction finishes at a high when the OUTWARD side is empty --
            # zero ask, i.e. buyers stopped paying up. A high showing zero BID
            # and a live ask is the opposite shape: still being lifted, nobody
            # selling into it. Treating that as "finished" made _exhaustion
            # fire at highs that were being bought aggressively, inverting the
            # marker against this function's own docstring.
            result[end + "_minority"] = 0
            outward_empty = (ask <= 0) if end == "high" else (bid <= 0)
            result[end] = "finished" if outward_empty else "one_sided"
    return result


def _low_volume_nodes(totals: dict, ordered: list, poc_k: int | None,
                      row_units: int):
    """Rows the auction rejected quickly — a local minimum far below the POC.

    Exact: total volume at price only, no aggressor inference. A MISSING
    neighbour fails the local-minimum test rather than passing it by default.
    """
    if poc_k is None or len(ordered) < LVN_MIN_ROWS:
        return set(), []
    ceiling = LVN_FRACTION * totals[poc_k]
    flagged = set()
    for k in ordered:
        if k == poc_k:
            continue
        below = totals.get(k - 1)
        above = totals.get(k + 1)
        if below is None or above is None:
            continue
        if totals[k] <= ceiling and totals[k] <= below and totals[k] <= above:
            flagged.add(k)
    zones = []
    for k in sorted(flagged):
        if zones and k == zones[-1]["k_to"] + 1:
            zone = zones[-1]
            zone["k_to"] = k
            zone["rows"] += 1
            zone["to"] = row_price(k, row_units)
            zone["volume"] = round(zone["volume"] + totals[k])
        else:
            zones.append({"k_from": k, "k_to": k, "rows": 1,
                          "from": row_price(k, row_units),
                          "to": row_price(k, row_units),
                          "volume": round(totals[k])})
    return flagged, zones


def _absorption(grid: dict, totals: dict, ordered: list, poc_k: int | None,
                close: float, row_size: float, row_units: int) -> list:
    """Heavy one-sided volume that FAILED TO MOVE PRICE — the passive side won.

    The opposite of exhaustion, and deliberately rendered on the opposite side
    of the divider from the imbalance tint: an imbalance says the aggressor got
    through, absorption says it did not.

    The volume term is exact; the pressure term is inferred, so ``pressure`` is
    published beside the flag rather than folded into a boolean.
    """
    if not ordered or poc_k is None:
        return []
    threshold = ABSORB_VOLUME_MULT * _median(totals.values())
    hits = []
    for k in ordered:
        bid, ask, _ = grid[k]
        total = totals[k]
        if total <= 0 or total < threshold:
            continue
        pressure = abs(ask - bid) / total
        if pressure < ABSORPTION_MIN_PRESSURE:
            continue
        price = row_price(k, row_units)
        if ask > bid:
            if close > price + row_size:
                continue                       # buyers got through: not absorbed
            side = "buyers_absorbed"
        elif bid > ask:
            if close < price - row_size:
                continue
            side = "sellers_absorbed"
        else:
            continue
        hits.append({"k": k, "price": price, "side": side,
                     "volume": round(total), "pressure": round(pressure, 3),
                     "suppressed": False, "reason": None})
    return hits


def _exhaustion(totals: dict, ordered: list, poc_k: int | None,
                unfinished: dict, open_: float, close: float,
                high: float, low: float, row_units: int) -> dict | None:
    """Heavy volume that moved price and then simply stopped.

    Not the same thing as absorption and mutually exclusive with an unfinished
    auction at the same end: an exhausted high has NO ask at the top row
    (buyers stopped paying up), an unfinished high has both sides. The volume
    test is exact; the auction clause is one inferred row, which is why the two
    are published as separate booleans — the surface can honestly say
    "volume-exhausted, auction status unknown".
    """
    if poc_k is None or len(ordered) < EXHAUST_MIN_RANGE_ROWS:
        return None
    end = "high" if close >= open_ else "low"
    k = ordered[-1] if end == "high" else ordered[0]
    if k == poc_k:
        return None
    peak = totals[poc_k]
    if peak <= 0:
        return None
    ratio = totals[k] / peak
    if ratio > EXHAUST_VOLUME_FRACTION:
        return None
    inward = [k - 1, k - 2] if end == "high" else [k + 1, k + 2]
    neighbours = [totals.get(step) for step in inward]
    if any(value is None or value < totals[k] for value in neighbours):
        return None
    status = unfinished.get(end)
    auction_test = None if status is None else (status == "finished")
    return {
        "end": end,
        "price": high if end == "high" else low,
        "row_price": row_price(k, row_units),
        "volume": round(totals[k]),
        "ratio": round(ratio, 3),
        "volume_test": True,
        "auction_test": auction_test,
        "detected": auction_test is True,
        "suppressed": False, "reason": None,
    }


def _compound_suppression(grade: str | None, classified_share: float | None,
                          quote_share: float | None, methods: dict | None,
                          min_classified: float | None = None,
                          min_quote: float | None = None) -> str | None:
    """Why a conjunction-of-inferences marker should not be rendered.

    A stacked imbalance is N ratio tests over ~2N inferred cells; its
    false-positive rate rises superlinearly with per-print classification
    error, so it compounds that error rather than averaging it out. Suppressed
    markers are still emitted, with a reason — the API stays honest and the
    surface stays quiet.
    """
    if grade == "low":
        return "confidence grade low"
    if grade is None:
        # Unknown coverage is not a pass. `confidence_grade` returns None when
        # the classified share was never measured, and every marker downstream
        # of this is an inference over classified cells — so at a null grade
        # there is no basis for any of them. Absorption, exhaustion and
        # divergence previously published unsuppressed here, which put the
        # least-supported markers on exactly the bars with no support at all.
        return "confidence grade unmeasured"
    if methods is not None:
        known = sum(count for name, count in methods.items() if name != "unknown")
        if known and methods.get("zero_tick", 0) == known:
            return "every classified print in the bar is zero-tick carry"
    if min_classified is not None:
        if classified_share is None:
            return "classified share unmeasured"
        if classified_share < min_classified:
            return f"classified share below {min_classified}"
    if min_quote is not None:
        # Same rule as classified share directly above: a caller that sets a
        # floor is asserting the marker needs that much direct quote evidence,
        # and an unmeasured share is not evidence that the floor was cleared.
        # This previously read `quote_share is not None and quote_share <
        # min_quote`, which let every bar with no quote coverage at all through
        # the strictest gate in the file -- exactly inverted, since no coverage
        # is the worst case, not an exemption from it.
        if quote_share is None:
            return "quote share unmeasured"
        if quote_share < min_quote:
            return f"quote share below {min_quote}"
    return None


def confidence_grade(classified_share: float | None,
                     quote_share: float | None) -> tuple:
    """(grade, basis).

    ``None`` when the coverage was never measured — rendering "low" there
    would be a claim about a measurement nobody took, which is the exact
    failure the ``classified_share is None`` work exists to remove.

    When ``quote_share`` is unmeasured the grade is taken on the volume-weighted
    share alone and says so, rather than silently substituting a zero for a
    number nobody has.
    """
    if classified_share is None:
        return None, None
    if quote_share is None:
        if classified_share >= 0.90:
            grade = "high"
        elif classified_share >= 0.75:
            grade = "fair"
        else:
            grade = "low"
        return grade, "classified_share"
    if classified_share >= 0.90 and quote_share >= 0.60:
        grade = "high"
    elif classified_share >= 0.75 and quote_share >= 0.35:
        grade = "fair"
    else:
        grade = "low"
    return grade, "classified_share+quote_share"


def _divergence(series: list, row_size: float) -> dict:
    """Bar-level delta divergence: price makes a NEW extreme, CVD does not.

    Distinct from ``OrderFlowTracker.divergence``, which splits the last 120
    CVD points into halves — that asks whether the second half's extreme
    differs from the first half's, which fires whenever the extreme happens to
    fall late in the window. This one requires the extreme to be in the most
    recent bar, and floors the move in ROWS rather than as a percent of price
    (a percent-of-price floor means something different on every instrument —
    the exact problem ``ABSORPTION_MAX_SPAN_TICKS`` exists to fix).

    The reference bar is published, not just a kind: a divergence whose other
    leg you cannot see is unfalsifiable.
    """
    blank = {"kind": None, "at_bar": None, "reference_bar": None,
             "price_extreme": None, "reference_price_extreme": None,
             "cvd_at_extreme": None, "reference_cvd_extreme": None,
             "lookback_bars": 0, "suppressed": False, "reason": None}
    window = [bar for bar in series[-DIVERGENCE_LOOKBACK:] if bar.trades]
    blank["lookback_bars"] = len(window)
    if len(window) < 3:
        return blank
    last = window[-1]
    prior = window[:-1]
    highs = [bar.high for bar in window]
    lows = [bar.low for bar in window]
    cvds = [bar.cvd for bar in window]
    price_span = max(highs) - min(lows)
    cvd_span = max(cvds) - min(cvds)
    price_edge = max(DIVERGENCE_EDGE_ROWS * row_size,
                     price_span * MIN_DIVERGENCE_FRACTION)
    cvd_edge = cvd_span * MIN_DIVERGENCE_FRACTION

    prior_high = max(bar.high for bar in prior)
    prior_low = min(bar.low for bar in prior)
    result = dict(blank)
    if (last.high >= max(highs) and last.high > prior_high + price_edge
            and last.cvd < max(cvds) - cvd_edge):
        reference = max(prior, key=lambda bar: bar.high)
        result.update(kind="bearish", at_bar=last.start,
                      reference_bar=reference.start,
                      price_extreme=last.high,
                      reference_price_extreme=prior_high,
                      cvd_at_extreme=round(last.cvd),
                      reference_cvd_extreme=round(max(cvds)))
    elif (last.low <= min(lows) and last.low < prior_low - price_edge
            and last.cvd > min(cvds) + cvd_edge):
        reference = min(prior, key=lambda bar: bar.low)
        result.update(kind="bullish", at_bar=last.start,
                      reference_bar=reference.start,
                      price_extreme=last.low,
                      reference_price_extreme=prior_low,
                      cvd_at_extreme=round(last.cvd),
                      reference_cvd_extreme=round(min(cvds)))
    return result


# --------------------------------------------------------------------------
# the book
# --------------------------------------------------------------------------

class FootprintBook:
    """Time-bucketed clusters for a bounded set of symbols."""

    def __init__(self, timeframe_seconds: int = 60, tick_size: float = 0.05):
        self.timeframe = timeframe_seconds
        # Fallback only. The per-symbol tick is measured (see TickSizeEstimator)
        # or set explicitly; this is what gets published with source "default"
        # when neither is available.
        self.tick_size = tick_size
        self.tick_sizes: dict[str, float] = {}
        self.ticks = TickSizeEstimator(default=tick_size)
        self.bars: OrderedDict[str, deque] = OrderedDict()
        self.tape: OrderedDict[str, deque] = OrderedDict()
        # The tape's print sizes, kept sorted alongside it. _mark needs the
        # rolling median on every print, and re-sorting the 200-row tape there
        # made on_print 8.6x slower (647k prints/s -> 75k). Insert-and-evict
        # into a sorted list is one C-level memmove and the median is exact.
        self._tape_sizes: dict[str, list] = {}
        self.dom: dict[str, dict] = {}
        self._cvd: dict[str, float] = {}
        self._cvd_basis: dict[str, str] = {}
        self._cvd_anchor: dict[str, float] = {}
        self._session_delta: dict[str, float | None] = {}
        # The confidence-weighted running total, anchored the same way as the
        # raw one so the two lines on the chart share a session origin.
        self._wcvd: dict[str, float] = {}
        self._unclassified: dict[str, float] = {}
        self._coverage: dict[str, dict] = {}
        # Freeze quantity and lot size in force on the day the symbol was
        # watched. Zero freeze for anything that is not an index derivative,
        # so equities and their options never mark.
        self.freeze_qty: dict[str, int] = {}
        self.lot_size: dict[str, int] = {}
        self._row_choice: dict[tuple, tuple] = {}
        self._agg_cache: dict[tuple, tuple] = {}

    def watching(self, symbol: str) -> bool:
        return symbol in self.bars

    def watch(self, symbol: str, seed_prints=None, session_delta: float | None = None,
              tick_size: float | None = None,
              session_weighted_delta: float | None = None, day: str | None = None) -> None:
        """Begin detailed capture, evicting the least recently requested symbol.

        ``seed_prints`` replays already-classified prints the flow tracker
        still holds, so opening a chart shows real clusters immediately
        instead of a blank pane that fills only as new prints arrive. Seeding
        uses genuine bid/ask-classified prints — never reconstructed candles,
        which carry no aggressor side.

        ``session_delta`` anchors CVD. Without it the book's CVD started at
        zero on ``watch()`` and accumulated only what it was subsequently fed —
        the seed replay plus live prints — while the flow tracker's runs from
        session open. Measured live on one contract at one instant: 1,460
        against 16,840, two numbers a screen apart both labelled CVD. The error
        is unbounded, being a function of how long ago the chart was opened.
        Anchor so that after replaying the seed the running CVD EQUALS the
        session figure.

        ``tick_size`` sets the instrument's tick explicitly when the caller
        knows it; otherwise it is measured from observed prices.

        ``session_weighted_delta`` anchors the confidence-weighted CVD the
        same way; ``day`` (IST, ISO) selects the regime whose freeze quantity
        a print is compared against, today when omitted.
        """
        if symbol in self.bars:
            self.bars.move_to_end(symbol)
            return
        if tick_size:
            self.tick_sizes[symbol] = tick_size
        self.bars[symbol] = deque(maxlen=MAX_BARS)
        self.tape[symbol] = deque(maxlen=TAPE_LENGTH)
        self._tape_sizes[symbol] = []
        self._unclassified[symbol] = 0.0
        regime_day = day or datetime.now(IST).date().isoformat()
        self.freeze_qty[symbol] = freeze_quantity(symbol, regime_day)
        self.lot_size[symbol] = max(1, lot_size(symbol, regime_day))
        seed = list(seed_prints or ())
        seeded = sum((row.size if row.side > 0 else -row.size if row.side < 0 else 0)
                     for row in seed)
        anchor = (session_delta - seeded) if session_delta is not None else 0.0
        self._cvd[symbol] = anchor
        self._cvd_anchor[symbol] = anchor
        self._session_delta[symbol] = session_delta
        self._cvd_basis[symbol] = "session" if session_delta is not None else "watch_window"
        seeded_weighted = sum(row.side * row.size * row.confidence for row in seed
                              if getattr(row, "confidence", None) is not None)
        self._wcvd[symbol] = ((session_weighted_delta - seeded_weighted)
                              if session_weighted_delta is not None else 0.0)
        maxlen = getattr(seed_prints, "maxlen", None)
        self._coverage[symbol] = {
            "seeded_prints": len(seed),
            # Only knowable when the caller hands over the bounded container
            # itself. A list of 400 prints that was already truncated upstream
            # is indistinguishable from a list of 400 that was not, and
            # guessing would be a claim.
            "seed_truncated": (len(seed) == maxlen) if maxlen else None,
        }
        for row in seed:
            self.on_print(symbol, row.timestamp, row.price, row.size, row.side,
                          getattr(row, "method", None), getattr(row, "confidence", None))
        while len(self.bars) > MAX_DETAIL_SYMBOLS:
            evicted, _ = self.bars.popitem(last=False)
            self._forget(evicted)

    def _forget(self, symbol: str) -> None:
        self.tape.pop(symbol, None)
        self._tape_sizes.pop(symbol, None)
        self.dom.pop(symbol, None)
        self._cvd.pop(symbol, None)
        self._cvd_basis.pop(symbol, None)
        self._cvd_anchor.pop(symbol, None)
        self._session_delta.pop(symbol, None)
        self._wcvd.pop(symbol, None)
        self._unclassified.pop(symbol, None)
        self._coverage.pop(symbol, None)
        self.freeze_qty.pop(symbol, None)
        self.lot_size.pop(symbol, None)
        for key in [k for k in self._row_choice if k[0] == symbol]:
            self._row_choice.pop(key, None)
        for key in [k for k in self._agg_cache if k[0] == symbol]:
            self._agg_cache.pop(key, None)

    def reset(self) -> None:
        self.bars.clear()
        self.tape.clear()
        self._tape_sizes.clear()
        self.dom.clear()
        self._cvd.clear()
        self._cvd_basis.clear()
        self._cvd_anchor.clear()
        self._session_delta.clear()
        self._wcvd.clear()
        self._unclassified.clear()
        self._coverage.clear()
        self.freeze_qty.clear()
        self.lot_size.clear()
        self._row_choice.clear()
        self._agg_cache.clear()
        self.ticks.clear()

    # -- tick size ---------------------------------------------------------

    def tick_size_for(self, symbol: str) -> tuple:
        """(tick_size, source, samples) for one instrument.

        ``source`` is "explicit" (the caller told us), "observed" (measured
        from the prices this process has already seen) or "default" (it could
        not be determined — published as that word, not passed off as a
        measurement).
        """
        override = self.tick_sizes.get(symbol)
        if override:
            return override, "explicit", None
        return self.ticks.estimate(symbol)

    def bucket(self, price: float, symbol: str | None = None) -> float:
        """Snap a price to the instrument's tick. Retained for callers that
        want a grid price; the ladder itself is keyed by integer quanta."""
        tick = self.tick_size_for(symbol)[0] if symbol else self.tick_size
        return round(round(price / tick) * tick, 6)

    def row_ticks_for(self, symbol: str, series: list, tick: float,
                      requested_timeframe: int, override: int | None = None) -> int:
        """Ticks per displayed row.

        The raw exchange tick is unusable as a row on any wide-range
        instrument: a 78,000-point index future spanning 97 points is 1,944 tick
        rows for ~20 populated ones, which is a 0.23px row, an unreadable chart,
        and — because adjacent rows are then essentially never both populated —
        a diagonal imbalance that can never fire.

        Auto rule: smallest ladder value keeping the median bar range under
        ``ROW_TARGET_ROWS`` rows. Stability beats optimality, so the choice is
        only revisited when the median range moves by more than
        ``ROW_RECHOICE_FACTOR`` from the value it was made for — otherwise the
        grid flickers between 3-second polls and every stack and shelf jumps.
        """
        if override:
            return max(1, int(override))
        ranges = [(bar.high - bar.low) / tick for bar in series
                  if bar.trades and bar.high > bar.low]
        median_range = _median(ranges) if ranges else 0.0
        key = (symbol, requested_timeframe)
        previous = self._row_choice.get(key)
        if previous is not None:
            chosen, at_range, at_tick = previous
            if at_tick == tick and at_range > 0 and median_range > 0:
                if (median_range / at_range) <= ROW_RECHOICE_FACTOR and \
                        (at_range / median_range) <= ROW_RECHOICE_FACTOR:
                    return chosen
            elif at_tick == tick and median_range <= 0:
                return chosen
        chosen = ROW_TICKS_LADDER[-1]
        for candidate in ROW_TICKS_LADDER:
            if median_range / candidate <= ROW_TARGET_ROWS:
                chosen = candidate
                break
        self._row_choice[key] = (chosen, median_range, tick)
        return chosen

    # -- ingest ------------------------------------------------------------

    def on_print(self, symbol: str, timestamp: float, price: float, size: float,
                 side: int, method: str | None = None,
                 confidence: float | None = None) -> None:
        series = self.bars.get(symbol)
        if series is None:
            return
        self.ticks.observe(symbol, price)
        start = int(timestamp) - (int(timestamp) % self.timeframe)
        if not series or series[-1].start != start:
            series.append(FootprintBar(start))
        bar = series[-1]
        mark = self._mark(symbol, timestamp, price, size, side)
        # RAW price, never a grid price: once a row aggregates several ticks a
        # bucketed high is wrong by up to a row, and the unfinished-auction
        # test would key off a quantised extreme.
        bar.add(price, size, side, method, confidence, mark)
        self._cvd[symbol] = self._cvd.get(symbol, 0.0) + (size if side > 0 else -size if side < 0 else 0)
        bar.cvd = self._cvd[symbol]
        if confidence is not None:
            self._wcvd[symbol] = self._wcvd.get(symbol, 0.0) + side * size * confidence
        bar.wcvd = self._wcvd.get(symbol, 0.0)
        if side == 0:
            self._unclassified[symbol] = self._unclassified.get(symbol, 0.0) + size
        self._append_tape(symbol, {
            "t": int(timestamp), "p": price, "s": round(size), "side": side,
            "method": method, "conf": confidence, "mark": mark["kind"] if mark else None,
        })

    def _append_tape(self, symbol: str, row: dict) -> None:
        """Append to the bounded tape, keeping its sorted size index in step."""
        tape = self.tape[symbol]
        sizes = self._tape_sizes.setdefault(symbol, [])
        if tape.maxlen is not None and len(tape) == tape.maxlen:
            dropped = tape[0]["s"]
            index = bisect_left(sizes, dropped)
            if index < len(sizes) and sizes[index] == dropped:
                del sizes[index]
        tape.append(row)
        insort(sizes, row["s"])

    def _mark(self, symbol: str, timestamp: float, price: float, size: float,
              side: int) -> dict | None:
        """Freeze-size and large prints, the tick-level whale shapes.

        The same two tests whale.detect runs offline (A2, A1), applied as the
        print arrives so the chart can show them on the cell they hit rather
        than a day later on another page. The live "size" is a cluster -- the
        cumulative-volume delta between two updates -- so a freeze-size hit is
        a cluster that happens to equal the freeze quantity, which after
        batching is what a slicer's child order looks like; the label says
        "cluster", never "order". The rolling median is taken over the tape
        the book already keeps rather than a separate 30-minute window, and
        off its sorted size index rather than by re-sorting it per print.
        """
        freeze = self.freeze_qty.get(symbol, 0)
        lot = self.lot_size.get(symbol, 1)
        if freeze and int(size) == freeze:
            return {"t": int(timestamp), "p": price, "s": round(size), "side": side,
                    "kind": "freeze", "lots": freeze // lot}
        tape = self.tape.get(symbol)
        if tape is None or len(tape) < LARGE_PRINT_MIN_TAPE:
            return None
        median = _median_sorted(self._tape_sizes.get(symbol, ()))
        if median <= 0 or size < LARGE_PRINT_MULTIPLE * median or size < MIN_LARGE_PRINT_LOTS * lot:
            return None
        return {"t": int(timestamp), "p": price, "s": round(size), "side": side,
                "kind": "large", "ratio": round(size / median, 1)}

    def on_quote(self, symbol: str, tick) -> None:
        if symbol not in self.bars:
            return
        if tick.bid is None and tick.ask is None:
            return
        # Quotes are the densest price sample the process gets, and the tick
        # estimate only sharpens with distinct prices.
        self.ticks.observe(symbol, tick.bid, tick.ask, getattr(tick, "ltp", None))
        self.dom[symbol] = {
            "bid": tick.bid, "ask": tick.ask,
            "bid_qty": tick.bid_qty, "ask_qty": tick.ask_qty,
            "total_buy_qty": tick.total_buy_qty, "total_sell_qty": tick.total_sell_qty,
            "last": tick.ltp, "oi": tick.open_interest,
            "avg_trade_price": tick.avg_trade_price,
        }

    # -- read --------------------------------------------------------------

    def _series(self, symbol: str, bars: int, requested: int) -> list:
        source = list(self.bars.get(symbol, ()))
        if requested == self.timeframe:
            return source[-bars:]
        key = (symbol, requested)
        stamp = (len(source), source[-1].version if source else -1)
        cached = self._agg_cache.get(key)
        if cached is not None and cached[0] == stamp:
            grouped = cached[1]
        else:
            grouped: OrderedDict[int, FootprintBar] = OrderedDict()
            for bar in source:
                start = bar.start - (bar.start % requested)
                aggregate = grouped.setdefault(start, FootprintBar(start))
                aggregate.merge(bar)
            self._agg_cache[key] = (stamp, grouped)
        return list(grouped.values())[-bars:]

    def payload(self, symbol: str, bars: int = 60,
                timeframe_seconds: int | None = None,
                row_ticks: int | None = None,
                flow: dict | None = None) -> dict:
        """The whole footprint surface for one symbol.

        ``flow`` is ``OrderFlowTracker.snapshot(symbol)`` when the caller has
        it. It supplies the session-scale coverage figures (quote share, depth
        share, method mix, unclassified volume) that this book cannot measure
        by itself. Absent, those are published as None — never as zeros, which
        would read as "no quote-ruled prints" rather than "nobody measured".
        """
        requested = timeframe_seconds or self.timeframe
        if requested < self.timeframe or requested % self.timeframe:
            raise ValueError("requested timeframe must be a multiple of capture timeframe")
        series = self._series(symbol, bars, requested)

        tick, tick_source, tick_samples = self.tick_size_for(symbol)
        chosen_row_ticks = self.row_ticks_for(symbol, series, tick, requested, row_ticks)
        row_size = round(tick * chosen_row_ticks, 8)
        units = row_units_for(row_size)

        window_volume = sum(bar.volume for bar in series)
        window_unclassified = sum(bar.unclassified for bar in series)
        window_classified_share = (
            round((window_volume - window_unclassified) / window_volume, 4)
            if window_volume > 0 else None)

        flow = flow or {}
        classified_share = flow.get("classified_share", window_classified_share)
        quote_share = flow.get("quote_share")
        trades = flow.get("trades")
        depth_share = (round(flow["depth_ticks"] / trades, 4)
                       if trades and flow.get("depth_ticks") is not None else None)
        method_mix = dict(flow["methods"]) if flow.get("methods") else None
        if method_mix is None:
            merged: dict = {}
            for bar in series:
                counts = bar.method_counts
                if counts:
                    for name, count in counts.items():
                        merged[name] = merged.get(name, 0) + count
            method_mix = merged or None
        grade, grade_basis = confidence_grade(classified_share, quote_share)

        bar_rows = [bar.payload(units, row_size, grade, quote_share) for bar in series]

        # A row populated in exactly one bar of the window: the trace a fast
        # move leaves behind. Exact — total volume at price only.
        # Over a one-bar window every populated row is trivially a "single
        # print", which is a statement about the window and not about the
        # auction. Publish nothing rather than something vacuous.
        occupancy: dict = {}
        for bar in series:
            for k in bar.rows(units):
                occupancy[k] = occupancy.get(k, 0) + 1
        single_prints = sorted(row_price(k, units) for k, seen in occupancy.items()
                               if seen == 1) if len(series) > 1 else []

        divergence = _divergence(series, row_size)
        reason = _compound_suppression(grade, classified_share, quote_share, method_mix)
        divergence["suppressed"] = reason is not None
        divergence["reason"] = reason

        session_delta = self._session_delta.get(symbol)
        if flow.get("cumulative_delta") is not None:
            session_delta = flow["cumulative_delta"]
        # A hard bound, not a model: the amount by which CVD could differ if
        # every sideless print had in fact gone one way.
        if flow.get("unclassified_volume") is not None:
            cvd_band = flow["unclassified_volume"]
            band_basis = "session"
        elif symbol in self._unclassified:
            cvd_band = self._unclassified[symbol]
            band_basis = "watch_window"
        else:
            cvd_band = None
            band_basis = None

        coverage = dict(self._coverage.get(symbol, {"seeded_prints": None,
                                                    "seed_truncated": None}))
        coverage["first_bar_t"] = series[0].start if series else None
        coverage["bars"] = len(series)

        return {
            "spec_version": SPEC_VERSION,
            "symbol": symbol,
            "timeframe_seconds": requested,
            "capture_timeframe_seconds": self.timeframe,
            "tick_size": tick,
            "tick_size_source": tick_source,
            "tick_size_samples": tick_samples,
            "row_ticks": chosen_row_ticks,
            "row_size": row_size,
            "imbalance_ratio": IMBALANCE_RATIO,
            "stacked_imbalance_min": STACKED_IMBALANCE_MIN,
            "cvd_basis": self._cvd_basis.get(symbol),
            "cvd_anchor": self._cvd_anchor.get(symbol),
            "session_cumulative_delta": session_delta,
            "session_weighted_delta": flow.get("weighted_cumulative_delta"),
            "low_confidence_bar": LOW_CONFIDENCE_BAR,
            # None, not 0, off the index derivatives: "no freeze rule applies"
            # is a different statement from "the freeze quantity is zero".
            "freeze_quantity": self.freeze_qty.get(symbol) or None,
            "lot_size": self.lot_size.get(symbol),
            "cvd_band": cvd_band,
            "cvd_band_basis": band_basis,
            "divergence_bars": divergence,
            "single_print_rows": single_prints,
            "single_print_window_bars": len(series),
            "confidence": {
                "classified_share": classified_share,
                "window_classified_share": window_classified_share,
                "quote_share": quote_share,
                "depth_share": depth_share,
                "method_mix": method_mix,
                "grade": grade,
                "grade_basis": grade_basis,
                "avg_spread_ticks": None,
            },
            "basis": BASIS,
            "coverage": coverage,
            "bars": bar_rows,
            "dom": self.dom.get(symbol),
            "tape": list(self.tape.get(symbol, ()))[-TAPE_LENGTH:][::-1],
            "watching": sorted(self.bars),
        }
