"""Order-flow reconstruction from the Fyers tick stream.

No Indian broker publishes an aggressor-tagged trade tape, so the buy/sell
side of each print must be inferred. The live tracker votes three rules per
print (``classify_update`` below): the **quote rule** (Lee-Ready) against the
book that was in force BEFORE the update, the **pending-quantity rule** (which
side of the resting book lost the traded quantity), and the **tick rule** as a
fallback — and reports how much they agreed as a confidence. That is a step
beyond the single-rule ``classify`` still kept for callers, but it is still an
inference, and every number here inherits that caveat.

Volume handling: Fyers sends ``vol_traded_today`` (cumulative for the
session), so per-print size is the positive delta between consecutive ticks.
When the feed also supplies ``last_traded_qty`` that value is preferred.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field, replace

# A print must move this fraction of the spread past the mid to be called
# aggressive when only a mid is available.
MID_TOLERANCE = 0.25
# A divergence must move price and delta by this share of their own range
# before it counts, otherwise tick noise reads as a signal.
MIN_DIVERGENCE_FRACTION = 0.25
# Deepest consumer is the 240-point UI curve; divergence looks back 120. 512
# leaves headroom without retaining more than twice the data any consumer can
# use for every symbol in the 600+ contract universe.
CVD_CURVE_POINTS = 512
# Speed of tape is read over this window and ranked against the day's own
# closed windows. Ten seconds is short enough to catch a burst and long enough
# that a single batched update does not register as one.
SPEED_WINDOW_SECONDS = 10.0
# Two minutes of closed windows before a percentile is published; ranking a
# reading against five samples says more about the sample than the tape.
SPEED_MIN_SAMPLES = 12
# Stamps are pruned to the window on every update, so this bound only ever
# binds on a feed replaying a burst with one timestamp; it keeps a 670-symbol
# universe from holding a session of stamps per contract.
SPEED_STAMPS = 5_000
# The pool a reading is ranked against: the trailing hour of closed windows,
# not the whole session. Bounded because every one of the 670 subscribed
# contracts accumulates one -- a full session each would be 2,250 samples per
# symbol -- and because "fast right now" means fast against the recent tape,
# not against an opening auction six hours ago.
SPEED_SAMPLES = 360


@dataclass(slots=True)
class Print:
    """One classified trade print."""
    timestamp: float
    price: float
    size: int
    side: int          # +1 buyer-initiated, -1 seller-initiated, 0 unknown
    method: str        # quote | pending | conflict | tick | zero_tick
    # How much the three votes agreed (classify_update). None on a print that
    # predates the score -- a restored checkpoint, say -- so a bar's mean is
    # taken only over prints that actually carried one.
    confidence: float | None = None


@dataclass
class FlowState:
    """Rolling order-flow state for one symbol for one session."""
    symbol: str
    cumulative_delta: float = 0.0
    trades: int = 0
    # Total size seen, whatever the classifier decided. buy+sell alone silently
    # under-reports the tape by exactly the unclassified share, and there was no
    # field from which that share could be recovered — the size was added to no
    # counter at all. A footprint can never tie out to exchange volume (the
    # aggressor side is inferred, not reported), but the RESIDUAL should be a
    # number the desk can show, not one it destroys.
    total_volume: float = 0.0
    unclassified_volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    last_price: float | None = None
    # The price of the last actual PRINT, kept apart from last_price. Fyers
    # SymbolUpdate also fires on quote and OI changes, and the old code advanced
    # last_price on every one of them — so by the time a real print arrived the
    # "previous trade price" was usually the same LTP a quote tick had just
    # written, the tick rule hit its equality branch, and the print went
    # unclassified. The tick test was being fed its own echo.
    last_trade_price: float | None = None
    # Last NON-ZERO aggressor side, for the zero-tick rule below.
    last_side: int = 0
    last_cum_volume: int | None = None
    # (timestamp, cumulative_delta, price) sampled per print, bounded.
    # 5,000 per symbol x ~670 symbols held 3.3M tuples (~0.5GB) to serve the
    # last 240 and analyse the last 120. The window only has to outlast the
    # deepest consumer, so it is sized to that with headroom.
    cvd_curve: deque = field(default_factory=lambda: deque(maxlen=CVD_CURVE_POINTS))
    # price -> [buy_volume, sell_volume] for footprint / absorption work.
    volume_at_price: dict[float, list[float]] = field(default_factory=dict)
    recent: deque = field(default_factory=lambda: deque(maxlen=400))
    # How each print was classified. If "quote" stays at zero the broker is
    # not delivering depth and the flow is a tick-rule approximation — the
    # difference matters enough to measure rather than assume. "mid" no longer
    # fires (the three-vote classifier has no mid rule) but stays a key so the
    # desk-wide health totals keep the same shape across the switch.
    methods: dict = field(default_factory=lambda: {
        "quote": 0, "mid": 0, "tick": 0, "zero_tick": 0, "pending": 0, "conflict": 0})
    unclassified: int = 0
    quotes_seen: int = 0
    # The book and the pending totals as they stood BEFORE the update being
    # classified -- the ones its trades actually hit. Classifying a lift at the
    # ask against the book it just moved reads it as a hit on the bid.
    prior_bid: float | None = None
    prior_ask: float | None = None
    prior_tbq: int | None = None
    prior_tsq: int | None = None
    # Σ side·size·confidence and Σ size·confidence over the prints that were
    # actually SIDED. A delta that weights each print by how much the votes
    # agreed is the blueprint's CVD; the raw one is kept beside it because the
    # two diverge exactly where the tape is batched. Sideless prints are left
    # out of both: the "unknown" verdict carries confidence 0.0, so averaging
    # over total volume turned the agreement figure into a second, worse
    # statement of coverage -- which unclassified_volume already measures.
    weighted_delta: float = 0.0
    conf_volume: float = 0.0
    # Speed of tape: the trailing window of updates (quotes included) and of
    # print sizes, plus the closed SPEED_WINDOW buckets a current reading is
    # ranked against.
    tick_stamps: deque = field(default_factory=lambda: deque(maxlen=SPEED_STAMPS))
    print_stamps: deque = field(default_factory=lambda: deque(maxlen=SPEED_STAMPS))
    speed_samples: deque = field(default_factory=lambda: deque(maxlen=SPEED_SAMPLES))
    speed_bucket: int = -1
    bucket_updates: int = 0
    bucket_qty: float = 0.0

    @property
    def delta(self) -> float:
        return self.buy_volume - self.sell_volume

    @property
    def imbalance(self) -> float:
        """Signed share of aggressive volume, -1..+1."""
        total = self.buy_volume + self.sell_volume
        return (self.buy_volume - self.sell_volume) / total if total else 0.0


# Absorption is heavy one-sided volume that FAILS TO MOVE PRICE. The failure
# to move must be measured in ticks, because a percent-of-price threshold means
# something different on every instrument. Over 179,534 minute bars on
# 2026-08-20 the previous 0.6%-of-price gate passed 99.9% of EQUITY bars -- it
# was always true, so absorption silently degraded to "one-sided flow" with no
# failed-to-move requirement at all -- while passing only 18.4% of OPTION bars,
# where a single Rs 0.05 tick on a Rs 1 premium is already a 5% move.
#
# Four ticks is the quietest ~23% of equity minutes and ~53% of option minutes.
# The real window is 60 prints, which for a liquid contract is far shorter than
# a minute, so in practice the gate binds harder than those figures suggest.
ABSORPTION_MAX_SPAN_TICKS = 4.0
ABSORPTION_MIN_PRESSURE = 0.35


def classify(price: float, bid: float | None, ask: float | None, previous: float | None,
             last_side: int = 0) -> tuple[int, str]:
    """Aggressor side for one print: (+1 buy, -1 sell, 0 unknown), method.

    The tick test is the full three-branch rule, not just up/down. Lee-Ready's
    tick test carries the PREVIOUS classification through an unchanged price
    (the zero-uptick / zero-downtick case); returning 0 there instead threw away
    exactly the prints that cluster where price is not moving — which is where
    volume accumulates, and where live-vs-reconstructed footprints disagree
    most. `last_side` is the last non-zero side for this symbol.

    This remains an INFERENCE. NSE publishes price, quantity, time and the best
    bid/ask — never the aggressor — so every side here is estimated. The point
    of the zero-tick branch is to stop discarding evidence, not to pretend the
    estimate is exact; `method` is returned so callers can weight it.
    """
    if bid is not None and ask is not None and ask > bid > 0:
        if price >= ask:
            return 1, "quote"
        if price <= bid:
            return -1, "quote"
        mid = (bid + ask) / 2
        half = (ask - bid) / 2
        if half > 0 and abs(price - mid) >= half * MID_TOLERANCE:
            return (1, "mid") if price > mid else (-1, "mid")
    if previous is not None:
        if price > previous:
            return 1, "tick"
        if price < previous:
            return -1, "tick"
        # Zero tick: price unchanged. Continue the prior aggressor.
        if last_side:
            return last_side, "zero_tick"
    return 0, "tick"


# The three-vote verdicts folded onto the method counters the health metric
# and the readiness blocker were written against: agreement with the quote
# rule is still quote-rule evidence, and a sideless verdict lands under "tick"
# exactly as the single-rule classifier booked it.
VOTE_METHOD = {
    "quote+pending": "quote", "quote": "quote", "conflict": "conflict",
    "pending": "pending", "tick": "tick", "zero_tick": "zero_tick", "unknown": "tick",
}


class OrderFlowTracker:
    """Per-symbol order-flow accumulation driven by the live tick stream."""

    def __init__(self, tick_size: float = 0.05):
        self.tick_size = tick_size
        self.states: dict[str, FlowState] = {}

    def reset(self, symbol: str | None = None) -> None:
        if symbol is None:
            self.states.clear()
        else:
            self.states.pop(symbol, None)

    def bucket(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, 2)

    def on_tick(self, tick) -> Print | None:
        """Fold one tick into the symbol's flow state. Returns the print, if any."""
        state = self.states.get(tick.symbol)
        if state is None:
            state = FlowState(tick.symbol)
            self.states[tick.symbol] = state
        stamp = tick.timestamp.timestamp()
        self._note_update(state, stamp)

        # Capture the prior book before this tick overwrites it. The memory
        # advances on EVERY update, quote-only and depth-less ones included:
        # the book a print hits is whatever was quoted last, whether or not
        # that quote traded -- and if the last update carried no depth, the
        # honest answer is that there is no book to judge against.
        #
        # Holding the last quote ever seen through a depth-less run gave the
        # quote rule (0.7, or 0.9 with pending agreement) verdicts against an
        # arbitrarily stale book, where tick_store's offline reconstruction of
        # the same ticks -- which clears its memory unconditionally -- booked
        # the tick rule (0.4). That inflated quote_share, which feeds the
        # confidence grade, the stacked-imbalance gate and the readiness
        # blocker, so the desk looked more ready on staler evidence and live
        # and replayed classifications of one session disagreed.
        prior_bid, prior_ask = state.prior_bid, state.prior_ask
        prior_tbq, prior_tsq = state.prior_tbq, state.prior_tsq
        bid, ask = getattr(tick, "bid", None), getattr(tick, "ask", None)
        tbq, tsq = getattr(tick, "total_buy_qty", None), getattr(tick, "total_sell_qty", None)
        state.prior_bid, state.prior_ask = bid, ask
        state.prior_tbq, state.prior_tsq = tbq, tsq

        size = 0
        if tick.volume:
            if state.last_cum_volume is None:
                # First tick of the session only establishes the baseline; its
                # cumulative total is not a single print.
                state.last_cum_volume = int(tick.volume)
                state.last_price = tick.ltp
                return None
            delta = int(tick.volume) - state.last_cum_volume
            # A reconnect can replay a lower session total. Re-baseline it;
            # treating the broker's reset as a trade would corrupt CVD.
            size = max(0, delta)
        elif tick.last_qty:
            # Some instruments omit cumulative volume. LTQ is only a fallback:
            # SymbolUpdate also fires for quote/OI changes and repeats LTQ, so
            # preferring it when a cumulative total exists double-counts trades.
            size = max(0, int(tick.last_qty))
        if tick.volume:
            state.last_cum_volume = int(tick.volume)
        if size <= 0:
            state.last_price = tick.ltp
            return None

        if (prior_bid is None or prior_ask is None) and not state.trades:
            # No book has EVER been seen: the tick's own quote is the only one
            # there is. A current-book read beats no read on the first print
            # after a baseline, which is otherwise permanently sideless.
            #
            # Only on that first print. Once the memory advances on every
            # update, a None prior later in the session means the last update
            # carried no depth -- and reaching for this tick's own book there
            # would classify a lift at the ask against the book it just moved,
            # which is the error this whole prior-book machinery exists to
            # avoid. The tick rule is the honest fallback.
            prior_bid, prior_ask = bid, ask
        buy_change = (tbq - prior_tbq) if (tbq is not None and prior_tbq is not None) else None
        sell_change = (tsq - prior_tsq) if (tsq is not None and prior_tsq is not None) else None
        verdict = classify_update(
            price=tick.ltp, traded=size, last_qty=getattr(tick, "last_qty", None),
            prior_bid=prior_bid, prior_ask=prior_ask,
            prior_price=state.last_trade_price, last_side=state.last_side,
            buy_pending_change=buy_change, sell_pending_change=sell_change)
        side, method, confidence = verdict.side, VOTE_METHOD[verdict.method], verdict.confidence
        # last_trade_price advances ONLY here, on a real print — never on the
        # quote/OI ticks that also arrive as SymbolUpdate.
        state.last_trade_price = tick.ltp
        state.last_price = tick.ltp
        state.trades += 1
        # Conserve the tape: every print's size lands in total_volume whatever
        # the classifier concluded, so buy + sell + unclassified == total and
        # the desk can report its own coverage instead of quietly shedding it.
        state.total_volume += size
        if side:
            state.last_side = side
        else:
            state.unclassified_volume += size
        state.methods[method] = state.methods.get(method, 0) + 1
        if side == 0:
            state.unclassified += 1
        if bid is not None and ask is not None:
            state.quotes_seen += 1
        if side > 0:
            state.buy_volume += size
        elif side < 0:
            state.sell_volume += size
        state.cumulative_delta = state.buy_volume - state.sell_volume
        if side:
            state.weighted_delta += side * size * confidence
            state.conf_volume += size * confidence

        level = state.volume_at_price.setdefault(self.bucket(tick.ltp), [0.0, 0.0])
        if side > 0:
            level[0] += size
        elif side < 0:
            level[1] += size

        state.print_stamps.append((stamp, size))
        state.bucket_qty += size
        state.cvd_curve.append((stamp, state.cumulative_delta, tick.ltp, state.weighted_delta))
        print_row = Print(stamp, tick.ltp, size, side, method, confidence)
        state.recent.append(print_row)
        return print_row

    @staticmethod
    def _note_update(state: FlowState, stamp: float) -> None:
        """Count this update toward the speed-of-tape window, closing the
        previous bucket into the day's sample when the clock has moved on."""
        state.tick_stamps.append(stamp)
        cutoff = stamp - SPEED_WINDOW_SECONDS
        while state.tick_stamps and state.tick_stamps[0] < cutoff:
            state.tick_stamps.popleft()
        while state.print_stamps and state.print_stamps[0][0] < cutoff:
            state.print_stamps.popleft()
        bucket = int(stamp // SPEED_WINDOW_SECONDS)
        if state.speed_bucket >= 0 and bucket != state.speed_bucket:
            state.speed_samples.append((state.bucket_updates, state.bucket_qty))
            state.bucket_updates = 0
            state.bucket_qty = 0.0
        state.speed_bucket = bucket
        state.bucket_updates += 1

    def tape_speed(self, symbol: str, at: float | None = None) -> dict | None:
        """Updates and quantity per second over the trailing window, ranked
        against the closed windows of the last hour for THIS symbol.

        A global updates-per-second figure says nothing about one contract:
        30 updates a second is a dead NIFTY future and a frantic weekly option.
        The percentile is withheld until enough windows have closed to rank
        against, rather than published as a number nobody could stand behind.
        """
        state = self.states.get(symbol)
        if not state or not state.tick_stamps:
            return None
        # Read as of the last update, never the wall clock: between polls the
        # window would otherwise drain to zero on a contract that is merely
        # quiet for a few seconds, and read as a dead tape.
        at = at if at is not None else state.tick_stamps[-1]
        cutoff = at - SPEED_WINDOW_SECONDS
        updates = sum(1 for stamp in state.tick_stamps if stamp >= cutoff)
        qty = sum(size for stamp, size in state.print_stamps if stamp >= cutoff)

        def percentile(value: float, index: int) -> float | None:
            pool = sorted(sample[index] for sample in state.speed_samples)
            if len(pool) < SPEED_MIN_SAMPLES:
                return None
            return round(100.0 * bisect_left(pool, value) / len(pool), 1)

        return {
            "window_seconds": SPEED_WINDOW_SECONDS,
            "updates_per_s": round(updates / SPEED_WINDOW_SECONDS, 2),
            "qty_per_s": round(qty / SPEED_WINDOW_SECONDS, 1),
            "updates_pct": percentile(updates, 0),
            "qty_pct": percentile(qty, 1),
            "samples": len(state.speed_samples),
        }

    # -- read models ---------------------------------------------------------

    def absorption(self, symbol: str, lookback: int = 60) -> dict:
        """Heavy one-sided volume that fails to move price = absorption.

        Buyers being absorbed at a high is distribution; sellers absorbed at a
        low is accumulation and the setup this module trades.
        """
        state = self.states.get(symbol)
        if not state or len(state.recent) < 10:
            return {"detected": False}
        window = list(state.recent)[-lookback:]
        volume = sum(row.size for row in window)
        if volume <= 0:
            return {"detected": False}
        signed = sum(row.size * row.side for row in window)
        prices = [row.price for row in window]
        span = max(prices) - min(prices)
        reference = sum(prices) / len(prices)
        span_pct = (span / reference * 100) if reference else 0.0
        # Exchange prices are tick-quantised, so this ratio is a near-integer;
        # round before comparing or binary float error rejects a span of
        # exactly N ticks (100.20 - 100.00 = 0.20000000000000284 -> 4.000000000000057).
        span_ticks = round(span / self.tick_size, 6) if self.tick_size else 0.0
        pressure = signed / volume
        # Strong directional pressure with almost no price progress. "Almost no
        # progress" is measured in the exchange's own price quantum, not as a
        # percent of price -- see ABSORPTION_MAX_SPAN_TICKS.
        detected = abs(pressure) >= ABSORPTION_MIN_PRESSURE and span_ticks <= ABSORPTION_MAX_SPAN_TICKS
        return {
            "detected": detected,
            "side": "sellers_absorbed" if detected and pressure < 0 else ("buyers_absorbed" if detected else None),
            "pressure": round(pressure, 3),
            "range_pct": round(span_pct, 3),
            "span_ticks": round(span_ticks, 1),
            "volume": volume,
        }

    def divergence(self, symbol: str, lookback: int = 120) -> dict:
        """Price/CVD divergence — price makes a new extreme, delta does not."""
        state = self.states.get(symbol)
        if not state or len(state.cvd_curve) < 60:
            return {"detected": False}
        window = list(state.cvd_curve)[-lookback:]
        half = len(window) // 2
        first, second = window[:half], window[half:]
        if not first or not second:
            return {"detected": False}
        price_low_1 = min(row[2] for row in first)
        price_low_2 = min(row[2] for row in second)
        cvd_low_1 = min(row[1] for row in first)
        cvd_low_2 = min(row[1] for row in second)
        # Require a MEANINGFUL divergence, not merely a lower tick. On noisy
        # tick-level CVD any two halves differ, which fired the signal on ~38%
        # of the universe — a signal that common is not a signal.
        price_span = max(row[2] for row in window) - min(row[2] for row in window)
        cvd_span = max(row[1] for row in window) - min(row[1] for row in window)
        price_edge = max(price_span * MIN_DIVERGENCE_FRACTION, price_low_1 * 0.002)
        cvd_edge = cvd_span * MIN_DIVERGENCE_FRACTION
        bullish = (price_low_2 < price_low_1 - price_edge) and (cvd_low_2 > cvd_low_1 + cvd_edge)
        price_high_1 = max(row[2] for row in first)
        price_high_2 = max(row[2] for row in second)
        cvd_high_1 = max(row[1] for row in first)
        cvd_high_2 = max(row[1] for row in second)
        bearish = (price_high_2 > price_high_1 + price_edge) and (cvd_high_2 < cvd_high_1 - cvd_edge)
        return {
            "detected": bool(bullish or bearish),
            "kind": "bullish" if bullish else ("bearish" if bearish else None),
        }

    def snapshot(self, symbol: str, top_levels: int = 12) -> dict:
        state = self.states.get(symbol)
        if not state:
            return {}
        levels = sorted(
            ((price, row[0], row[1]) for price, row in state.volume_at_price.items()),
            key=lambda row: -(row[1] + row[2]),
        )[:top_levels]
        quote_share = (state.methods.get("quote", 0) / state.trades) if state.trades else 0.0
        # buy + sell IS the sided volume: both legs accumulate only when the
        # classifier returned a side, and the residual is unclassified_volume.
        sided_volume = state.buy_volume + state.sell_volume
        # Symbol-relative reading: RVOL, an nd score and a bounded flow score
        # measured against THIS symbol's own history, so a delta on a Rs 2
        # option is comparable with one on NIFTY futures. Imported here rather
        # than at module scope so a desk with no tick database pays nothing for
        # it, and returns None whenever the symbol has no baseline or the
        # reading cannot support one -- see of_normalise for the floors.
        #
        # list() over a deque is one C-level call, so the live socket thread
        # cannot mutate `recent` mid-iteration underneath us.
        try:
            from .of_normalise import normalised_flow
            normalised = normalised_flow(symbol, list(state.recent))
        except Exception:
            # A normalisation layer must never be able to take down the flow
            # snapshot the desk actually trades from. Absent beats broken.
            normalised = None
        return {
            "symbol": symbol,
            "trades": state.trades,
            "methods": dict(state.methods),
            "quote_share": round(quote_share, 3),
            "depth_ticks": state.quotes_seen,
            "unclassified": state.unclassified,
            # Coverage: what share of the tape this desk could actually assign a
            # side to. buy+sell is NOT the traded volume — the aggressor is
            # inferred, never reported by NSE — so publishing the residual is
            # what lets a reader weight the delta/imbalance below instead of
            # taking them as measurements.
            "total_volume": state.total_volume,
            "unclassified_volume": state.unclassified_volume,
            # None (not 0.0) when there is no measurement yet: 0.0 would render
            # as "0% classified", which is a claim. None renders as nothing.
            "classified_share": round(
                (state.total_volume - state.unclassified_volume) / state.total_volume, 3
            ) if state.total_volume else None,
            "buy_volume": state.buy_volume,
            "sell_volume": state.sell_volume,
            "delta": state.delta,
            "cumulative_delta": state.cumulative_delta,
            # Each print weighted by how much the three votes agreed. Where the
            # two CVDs part company the tape was batched or contested, which is
            # exactly where the raw figure is least trustworthy.
            "weighted_cumulative_delta": round(state.weighted_delta, 1),
            # Averaged over the SIDED volume, not the whole tape: how much of
            # the tape got a side is `classified_share` above, and dividing by
            # total volume published the two as one number.
            "mean_confidence": (round(state.conf_volume / sided_volume, 3)
                                if sided_volume else None),
            "imbalance": round(state.imbalance, 3),
            # Per-symbol normalisation. None (absent) when unavailable: a
            # default here would read as "this symbol is behaving normally",
            # which is a claim nobody measured.
            "normalised": normalised,
            "absorption": self.absorption(symbol),
            "divergence": self.divergence(symbol),
            "footprint": [
                {"price": price, "buy": buy, "sell": sell, "delta": buy - sell}
                for price, buy, sell in sorted(levels, key=lambda row: -row[0])
            ],
            "cvd_curve": [
                {"t": int(row[0]), "cvd": round(row[1], 1), "price": row[2],
                 # A curve restored from an older checkpoint has no weighted
                 # leg; null there, never the raw value wearing its label.
                 "wcvd": round(row[3], 1) if len(row) > 3 else None}
                for row in list(state.cvd_curve)[-240:]
            ],
            "tape_speed": self.tape_speed(symbol),
        }


# ---------------------------------------------------------------------------
# Three-vote classification with a confidence score
# ---------------------------------------------------------------------------
# The single-rule `classify` above answers "which side?" and nothing else. On a
# feed that batches several trades into one state update, that question does
# not always have an answer, and a footprint that reports a confident delta on
# a batched update is lying quietly.
#
# This path votes three independent rules and reports how much they agreed:
#
#   1. the quote rule, against the book that was in force BEFORE the update --
#      the book those trades actually executed against, not the one left behind;
#   2. the pending-quantity rule, which is available on Indian feeds and rarely
#      used: a market buy that lifts offers removes exactly its filled quantity
#      from total pending sell quantity, so a fall in tot_sell_qty matching the
#      traded quantity is direct evidence of a buyer. It is contaminated only by
#      simultaneous cancellations, which the tolerances absorb;
#   3. the tick rule, as a fallback only.
#
# Weights follow the blueprint's initial values and are meant to be replaced by
# measured agreement rates once enough sessions are recorded.
BOTH_AGREE_CONFIDENCE = 0.9
QUOTE_ONLY_CONFIDENCE = 0.7
PENDING_ONLY_CONFIDENCE = 0.6
TICK_ONLY_CONFIDENCE = 0.4
# The votes contradict each other. The quote rule wins on the grounds that it
# reads the price actually paid, but a contested read is worth less than an
# uncontested weak one, so it sits below PENDING_ONLY.
CONFLICT_CONFIDENCE = 0.5
# A batched update may contain trades on both sides; whatever side wins, some
# of the quantity was probably the other one.
BATCHED_CONFIDENCE_FACTOR = 0.8
# A market order consuming one side should remove ~dv from that side's pending
# total. The tolerances leave room for cancellations and new orders arriving in
# the same snapshot window.
PENDING_CONSUMED_FRACTION = 0.8
PENDING_OPPOSITE_FRACTION = 0.2


@dataclass(frozen=True)
class Classification:
    side: int
    method: str
    confidence: float
    quote_vote: int = 0
    pending_vote: int = 0
    tick_vote: int = 0
    single: bool = True

    @property
    def agreed(self) -> bool:
        return self.quote_vote != 0 and self.quote_vote == self.pending_vote


def quote_vote(price: float, bid: float | None, ask: float | None) -> int:
    """Vote 1. Zero when the print landed inside the prior spread."""
    if bid is None or ask is None or not (ask > bid > 0):
        return 0
    if price >= ask:
        return 1
    if price <= bid:
        return -1
    return 0


def pending_vote(traded: float, buy_pending_change: float | None,
                 sell_pending_change: float | None) -> int:
    """Vote 2. Which side of the resting book lost the quantity that traded?"""
    if traded <= 0 or buy_pending_change is None or sell_pending_change is None:
        return 0
    consumed = -PENDING_CONSUMED_FRACTION * traded
    untouched = -PENDING_OPPOSITE_FRACTION * traded
    if sell_pending_change <= consumed and buy_pending_change > untouched:
        return 1
    if buy_pending_change <= consumed and sell_pending_change > untouched:
        return -1
    return 0


def tick_vote(price: float, previous: float | None, last_side: int = 0) -> int:
    """Vote 3. Lee-Ready's full three-branch tick test, zero tick included."""
    if previous is None:
        return 0
    if price > previous:
        return 1
    if price < previous:
        return -1
    return last_side


def combine_votes(quote: int, pending: int, tick: int, single: bool = True) -> Classification:
    """Fold the three votes into a side, a name for how it was decided, and a
    confidence that callers are expected to weight by rather than ignore."""
    if quote and quote == pending:
        side, method, confidence = quote, "quote+pending", BOTH_AGREE_CONFIDENCE
    elif quote and pending and quote != pending:
        side, method, confidence = quote, "conflict", CONFLICT_CONFIDENCE
    elif quote:
        side, method, confidence = quote, "quote", QUOTE_ONLY_CONFIDENCE
    elif pending:
        side, method, confidence = pending, "pending", PENDING_ONLY_CONFIDENCE
    elif tick:
        side, method, confidence = tick, "tick", TICK_ONLY_CONFIDENCE
    else:
        side, method, confidence = 0, "unknown", 0.0
    if not single and side:
        confidence *= BATCHED_CONFIDENCE_FACTOR
    return Classification(side=side, method=method, confidence=round(confidence, 4),
                          quote_vote=quote, pending_vote=pending, tick_vote=tick,
                          single=single)


def classify_update(*, price: float, traded: float, last_qty: float | None,
                    prior_bid: float | None, prior_ask: float | None,
                    prior_price: float | None, last_side: int = 0,
                    buy_pending_change: float | None = None,
                    sell_pending_change: float | None = None) -> Classification:
    """Classify one volume-bearing update against the book that preceded it.

    ``traded`` is the cumulative-volume delta -- the quantity that changed hands
    since the previous update, which is the only trade quantity this feed
    actually reports. ``last_qty`` is the exchange's last-traded quantity; when
    it equals ``traded`` the update carried exactly one trade and the side is
    unambiguous, otherwise several trades were merged and confidence is cut.
    """
    single = last_qty is not None and traded > 0 and abs(traded - last_qty) < 1e-9
    result = combine_votes(
        quote_vote(price, prior_bid, prior_ask),
        pending_vote(traded, buy_pending_change, sell_pending_change),
        tick_vote(price, prior_price, last_side),
        single=single,
    )
    # A tick verdict on an unchanged price is a carried side, not an observed
    # one. The desk separates the two on purpose: zero-tick prints cluster
    # exactly where price is not moving, which is where volume accumulates and
    # where a reconstruction most needs to show its working.
    if result.method == "tick" and prior_price is not None and price == prior_price:
        return replace(result, method="zero_tick")
    return result
