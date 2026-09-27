"""Order-flow imbalance from the book (Cont, Kukanov & Stoikov 2014).

The desk's existing flow signal is inferred trade delta: it guesses which side
was the aggressor from price against the quote, then sums signed volume. That
inference is the weak link — Fyers sends state snapshots, not trade prints, and
several trades routinely arrive batched into one update, so the aggressor is
never actually observed.

OFI does not need the aggressor. It reads the *book*, which the feed transmits
exactly: best bid and ask prices with their sizes. Every change in those four
numbers is decomposed into how much buying or selling pressure it represents.
Cont, Kukanov & Stoikov found this explains short-horizon price moves with
R² around 0.65, against roughly 0.32 for trade imbalance. That is the argument
for computing it: it is both the more informative signal and the one built
from data that is not a guess.

The event contribution for consecutive level-1 states n-1 and n is

    e_n =  1{Pb(n) >= Pb(n-1)}·qb(n) - 1{Pb(n) <= Pb(n-1)}·qb(n-1)
         - 1{Pa(n) <= Pa(n-1)}·qa(n) + 1{Pa(n) >= Pa(n-1)}·qa(n-1)

Read it a term at a time. A bid that moves up, or holds and grows, adds its new
size as buying pressure; a bid that moves down, or holds and shrinks, removes
the size that left. The ask terms mirror it with the opposite sign. When a
price is unchanged both indicators fire and the term collapses to the size
*difference*, which is why the rule handles "someone added 200 lots at the same
bid" and "the bid was lifted entirely" with one expression.

Raw OFI is in units of quantity, so it is not comparable between a symbol
quoting in 30-lot clips and one quoting in 1,800, nor between a quiet morning
and a violent afternoon. Every reading is therefore also published normalised
by the average best-quote depth over a trailing window, per the paper's own
advice.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

# Long enough to span a lull without letting the open's depth define the close.
DEPTH_WINDOW_SECONDS = 1800.0
MIN_DEPTH_SAMPLES = 20


@dataclass(slots=True)
class BookState:
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float


def event_contribution(previous: BookState, current: BookState) -> float:
    """One e_n term. Positive means net buying pressure entered the book."""
    contribution = 0.0
    if current.bid >= previous.bid:
        contribution += current.bid_qty
    if current.bid <= previous.bid:
        contribution -= previous.bid_qty
    if current.ask <= previous.ask:
        contribution -= current.ask_qty
    if current.ask >= previous.ask:
        contribution += previous.ask_qty
    return contribution


@dataclass
class OFIState:
    """Running OFI for one symbol, fed one level-1 snapshot at a time."""
    symbol: str
    cumulative: float = 0.0
    events: int = 0
    last: BookState | None = None
    # (timestamp, e_n) so a window can be summed without keeping every update.
    recent: deque = field(default_factory=lambda: deque(maxlen=4000))
    # Trailing best-quote depth, the scale OFI is reported against.
    depth_samples: deque = field(default_factory=lambda: deque(maxlen=4000))
    # minute_start -> [ofi_sum, events]. `recent` is bounded and only has to
    # serve a five-minute window; a cumulative line drawn beside footprint
    # bars needs every bucket of the session, and a minute is the finest bar
    # the book captures, so anything coarser groups from here.
    minutes: dict = field(default_factory=dict)

    def reset(self) -> None:
        """Start of session. NSE has no overnight book, so cumulative OFI
        carried across a close would be describing two different auctions."""
        self.cumulative = 0.0
        self.events = 0
        self.last = None
        self.recent.clear()
        self.depth_samples.clear()
        self.minutes.clear()

    def update(self, timestamp: float, bid: float | None, ask: float | None,
               bid_qty: float | None, ask_qty: float | None) -> float | None:
        """Fold in one quote. Returns this update's e_n, or None if unusable.

        A crossed or empty book is skipped rather than clamped: those frames
        are feed artefacts, and letting one through injects a spike of pure
        noise into a cumulative series that never forgets it.
        """
        if bid is None or ask is None or bid_qty is None or ask_qty is None:
            return None
        if bid <= 0 or ask <= 0 or ask < bid or bid_qty < 0 or ask_qty < 0:
            return None
        current = BookState(float(bid), float(ask), float(bid_qty), float(ask_qty))
        self.depth_samples.append((timestamp, (current.bid_qty + current.ask_qty) / 2))
        previous, self.last = self.last, current
        if previous is None:
            return None
        contribution = event_contribution(previous, current)
        self.cumulative += contribution
        self.events += 1
        self.recent.append((timestamp, contribution))
        bucket = int(timestamp) - (int(timestamp) % 60)
        minute = self.minutes.get(bucket)
        if minute is None:
            self.minutes[bucket] = [contribution, 1]
        else:
            minute[0] += contribution
            minute[1] += 1
        return contribution

    def series(self, timeframe_seconds: int = 60) -> list[dict]:
        """Per-bar OFI and its running total, keyed to the same bucket starts
        the footprint uses so the two line up column for column."""
        grouped: dict[int, list] = {}
        for minute, (total, count) in sorted(self.minutes.items()):
            start = minute - (minute % timeframe_seconds)
            row = grouped.setdefault(start, [0.0, 0])
            row[0] += total
            row[1] += count
        running = 0.0
        out = []
        for start, (total, count) in grouped.items():
            running += total
            out.append({"t": start, "ofi": round(total, 2), "cum": round(running, 2),
                        "events": count})
        return out

    def window(self, timestamp: float, seconds: float) -> float:
        """Summed OFI over the trailing window ending at ``timestamp``."""
        cutoff = timestamp - seconds
        return sum(value for stamp, value in self.recent if stamp >= cutoff)

    def depth_scale(self, timestamp: float,
                    seconds: float = DEPTH_WINDOW_SECONDS) -> float | None:
        cutoff = timestamp - seconds
        sizes = [size for stamp, size in self.depth_samples if stamp >= cutoff]
        if len(sizes) < MIN_DEPTH_SAMPLES:
            return None
        average = sum(sizes) / len(sizes)
        return average if average > 0 else None

    def normalised(self, timestamp: float, seconds: float = 60.0) -> float | None:
        """Window OFI in units of average best-quote depth.

        None until the depth window has enough samples to mean anything —
        an un-normalised number wearing a normalised label is worse than a gap.
        """
        scale = self.depth_scale(timestamp)
        if scale is None:
            return None
        return self.window(timestamp, seconds) / scale

    def snapshot(self, timestamp: float, timeframe_seconds: int | None = None) -> dict:
        payload = {
            "symbol": self.symbol,
            "cumulative": round(self.cumulative, 2),
            "events": self.events,
            "ofi_60s": round(self.window(timestamp, 60.0), 2),
            "ofi_300s": round(self.window(timestamp, 300.0), 2),
            "normalised_60s": _round(self.normalised(timestamp, 60.0)),
            "normalised_300s": _round(self.normalised(timestamp, 300.0)),
            "depth_scale": _round(self.depth_scale(timestamp)),
            # When the accumulation began. A chart opened at 11:00 shows a
            # line that starts there, and the label must say so rather than
            # let it pass for a session figure.
            "since": min(self.minutes) if self.minutes else None,
        }
        if timeframe_seconds:
            payload["series"] = self.series(timeframe_seconds)
        return payload


def _round(value: float | None, places: int = 4) -> float | None:
    return None if value is None else round(value, places)


class OFITracker:
    """Per-symbol OFI, driven off the same tick stream as everything else."""

    def __init__(self) -> None:
        self.states: dict[str, OFIState] = {}

    def state(self, symbol: str) -> OFIState:
        state = self.states.get(symbol)
        if state is None:
            state = OFIState(symbol=symbol)
            self.states[symbol] = state
        return state

    def on_tick(self, tick) -> float | None:
        timestamp = getattr(tick, "timestamp", None)
        stamp = timestamp.timestamp() if hasattr(timestamp, "timestamp") else 0.0
        return self.state(tick.symbol).update(
            stamp, getattr(tick, "bid", None), getattr(tick, "ask", None),
            getattr(tick, "bid_qty", None), getattr(tick, "ask_qty", None))

    def reset(self, symbol: str | None = None) -> None:
        """Session roll. Clearing the dict, not resetting states in place:
        expired strikes and rolled futures series never trade again, and a
        state kept for each of them is a leak that only grows -- the same
        reason OrderFlowTracker.reset() clears rather than zeroes.
        """
        if symbol is None:
            self.states.clear()
        else:
            self.states.pop(symbol, None)

    def prune(self, keep) -> int:
        """Drop states for symbols nobody is reading. Returns how many went."""
        keep = set(keep)
        dropped = [symbol for symbol in self.states if symbol not in keep]
        for symbol in dropped:
            del self.states[symbol]
        return len(dropped)

    def snapshot(self, symbol: str, timestamp: float,
                 timeframe_seconds: int | None = None) -> dict | None:
        state = self.states.get(symbol)
        if state is None or state.last is None:
            return None
        return state.snapshot(timestamp, timeframe_seconds)


def replay(rows) -> OFIState:
    """Rebuild OFI for one symbol from stored quotes.

    ``rows`` are ``(ts_seconds, bid, ask, bid_qty, ask_qty)`` in time order —
    exactly the columns the tick archive keeps, so any recorded session can be
    re-measured without the live feed.
    """
    state = OFIState(symbol="replay")
    for stamp, bid, ask, bid_qty, ask_qty in rows:
        state.update(float(stamp), bid, ask, bid_qty, ask_qty)
    return state
