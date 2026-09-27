"""Textbook directional setups for the auction desk.

The desk shipped with three setups and all three were bullish: responsive
buying, an upside initial-balance extension, and a failed low. Half of every
session was therefore unreachable, and the flow tracker had been computing the
bearish divergence all along with nothing reading it.

Direction is expressed in the OPTION, never by shorting: a bullish read buys
the ATM call, a bearish read buys the ATM put. That keeps the desk's book
long-only and its paper cash non-negative, while still taking a two-sided view.

Each rule is EDGE-triggered through the caller's ``marks`` dict. A standing
condition ("price is above the value area") is near-tautological once true and
would re-fire on every tick; what a desk trades is the transition.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

# The opening range is the first half hour. Market profile's initial balance
# is the first HOUR (two brackets); both are classic and they are not the same
# level, so the desk watches each separately.
OPENING_RANGE_MINUTES = 30
SESSION_OPEN_MINUTE = 9 * 60 + 15
DONCHIAN_MINUTES = 20
TREND_FAST_MINUTES = 9
TREND_SLOW_MINUTES = 21
# A breakout has to clear the level by more than the noise around it, or every
# tick that kisses the high counts as a break.
BREAKOUT_BUFFER_PCT = 0.0005


@dataclass(slots=True)
class Bias:
    setup: str
    direction: str          # "bullish" | "bearish"
    reason: str

    @property
    def option_type(self) -> str:
        return "CE" if self.direction == "bullish" else "PE"


@dataclass
class SessionTape:
    """Per-symbol intraday series the classic setups need.

    The market profile carries structure (value area, initial balance, day
    type) but not a running VWAP, an opening range, or a rolling channel, so
    those are tracked here off the same tick stream.
    """
    day: str
    open_price: float = 0.0
    or_high: float | None = None
    or_low: float | None = None
    vwap_value: float = 0.0
    vwap_volume: float = 0.0
    closes: deque[float] = field(default_factory=lambda: deque(maxlen=DONCHIAN_MINUTES + 1))
    highs: deque[float] = field(default_factory=lambda: deque(maxlen=DONCHIAN_MINUTES + 1))
    lows: deque[float] = field(default_factory=lambda: deque(maxlen=DONCHIAN_MINUTES + 1))
    fast_ema: float | None = None
    slow_ema: float | None = None
    fast_ema_base: float | None = None
    slow_ema_base: float | None = None
    last_minute: int = -1
    minutes_seen: int = 0

    def on_price(self, price: float, size: float, minute: int) -> None:
        if price <= 0:
            return
        if not self.open_price:
            self.open_price = price
        if minute - SESSION_OPEN_MINUTE < OPENING_RANGE_MINUTES:
            self.or_high = price if self.or_high is None else max(self.or_high, price)
            self.or_low = price if self.or_low is None else min(self.or_low, price)
        if size > 0:
            self.vwap_value += price * size
            self.vwap_volume += size
        if minute != self.last_minute:
            self.last_minute = minute
            self.minutes_seen += 1
            self.closes.append(price)
            self.highs.append(price)
            self.lows.append(price)
            self.fast_ema_base = self.fast_ema
            self.slow_ema_base = self.slow_ema
        else:
            self.closes[-1] = price
            self.highs[-1] = max(self.highs[-1], price)
            self.lows[-1] = min(self.lows[-1], price)
        self._update_current_ema(price)

    def _update_current_ema(self, price: float) -> None:
        for span, name, base_name in (
            (TREND_FAST_MINUTES, "fast_ema", "fast_ema_base"),
            (TREND_SLOW_MINUTES, "slow_ema", "slow_ema_base"),
        ):
            previous = getattr(self, base_name)
            alpha = 2 / (span + 1)
            setattr(self, name, price if previous is None else previous + alpha * (price - previous))

    def on_bar(self, open_price: float, high: float, low: float, close: float,
               volume: float, minute: int) -> None:
        """Restore one completed minute from durable OHLCV history."""
        if min(open_price, high, low, close) <= 0:
            return
        self.on_price(open_price, 0.0, minute)
        self.highs[-1] = max(self.highs[-1], high)
        self.lows[-1] = min(self.lows[-1], low)
        self.closes[-1] = close
        if minute - SESSION_OPEN_MINUTE < OPENING_RANGE_MINUTES:
            self.or_high = high if self.or_high is None else max(self.or_high, high)
            self.or_low = low if self.or_low is None else min(self.or_low, low)
        if volume > 0:
            typical = (high + low + close) / 3
            self.vwap_value += typical * volume
            self.vwap_volume += volume
        self._update_current_ema(close)

    @property
    def opening_range_complete(self) -> bool:
        return (self.or_high is not None and self.or_low is not None
                and self.last_minute - SESSION_OPEN_MINUTE >= OPENING_RANGE_MINUTES)

    @property
    def vwap(self) -> float | None:
        return (self.vwap_value / self.vwap_volume) if self.vwap_volume > 0 else None

    def channel(self) -> tuple[float, float] | None:
        """Donchian high/low EXCLUDING the current minute, so a break is a break."""
        if len(self.highs) < DONCHIAN_MINUTES + 1:
            return None
        return max(list(self.highs)[:-1]), min(list(self.lows)[:-1])

    @property
    def trend(self) -> str | None:
        if self.fast_ema is None or self.slow_ema is None or self.minutes_seen < TREND_SLOW_MINUTES:
            return None
        if self.fast_ema > self.slow_ema:
            return "up"
        return "down" if self.fast_ema < self.slow_ema else None


class TapeBook:
    def __init__(self) -> None:
        self.tapes: dict[str, SessionTape] = {}

    def on_price(self, symbol: str, day: str, price: float, size: float, minute: int) -> SessionTape:
        tape = self.tapes.get(symbol)
        if tape is None or tape.day != day:
            tape = SessionTape(day=day)
            self.tapes[symbol] = tape
        tape.on_price(price, size, minute)
        return tape

    def on_bar(self, symbol: str, day: str, open_price: float, high: float,
               low: float, close: float, volume: float, minute: int) -> SessionTape:
        tape = self.tapes.get(symbol)
        if tape is None or tape.day != day:
            tape = SessionTape(day=day)
            self.tapes[symbol] = tape
        tape.on_bar(open_price, high, low, close, volume, minute)
        return tape

    def get(self, symbol: str) -> SessionTape | None:
        return self.tapes.get(symbol)


def _crossed(marks: dict, key: str, now: bool) -> bool:
    """True only on the tick the condition turns on."""
    if key not in marks:
        marks[key] = now
        return False
    was = marks.get(key, False)
    marks[key] = now
    return now and not was


def evaluate(
    *,
    price: float,
    marks: dict,
    tape: SessionTape,
    profile,
    flow_state,
    absorption: dict,
    divergence: dict,
    min_imbalance: float,
    probe_window_open: dict,
) -> Bias | None:
    """First matching textbook setup, or None.

    Order is deliberate: the responsive (fade) setups are tested before the
    initiative (breakout) ones, because a probe that reverses back into value
    is the same tick as a level being lost, and the reversal is the higher
    quality read of the two.
    """
    vah, val = profile.value_area()
    buffer_amount = price * BREAKOUT_BUFFER_PCT
    imbalance = flow_state.imbalance if flow_state else 0.0
    delta = flow_state.cumulative_delta if flow_state else 0.0
    absorbed = absorption.get("side")

    # 1. Responsive buying / selling: a recent probe OUTSIDE value that is now
    #    rejected back into it. The classic auction fade.
    for side, direction, level, comparison in (
        ("below", "bullish", val, "reclaim"),
        ("above", "bearish", vah, "reject"),
    ):
        probe_at = probe_window_open.get(side)
        if probe_at is None or level is None:
            continue
        back_inside = price >= level if direction == "bullish" else price <= level
        confirmed = ((absorbed == "sellers_absorbed" or delta > 0) if direction == "bullish"
                     else (absorbed == "buyers_absorbed" or delta < 0))
        if back_inside and confirmed:
            extreme = probe_window_open.pop(f"{side}_extreme", price)
            probe_window_open.pop(side, None)
            return Bias("value_area_reclaim", direction,
                        f"probed to {extreme:.2f} {side} value and {comparison}ed it, "
                        f"CVD {delta:+,.0f}")

    # 2. Initial balance range extension, both ways.
    if profile.ib_high is not None and _crossed(marks, "above_ib", price > profile.ib_high + buffer_amount):
        if imbalance >= min_imbalance:
            return Bias("ib_range_extension", "bullish",
                        f"range extension THROUGH IB high {profile.ib_high:.2f}, "
                        f"imbalance {imbalance:+.2f}, day reading {profile.day_type()}")
    if profile.ib_low is not None and _crossed(marks, "below_ib", price < profile.ib_low - buffer_amount):
        if imbalance <= -min_imbalance:
            return Bias("ib_range_extension", "bearish",
                        f"range extension THROUGH IB low {profile.ib_low:.2f}, "
                        f"imbalance {imbalance:+.2f}, day reading {profile.day_type()}")

    # 3. Value area breakout: acceptance OUTSIDE value, the initiative move.
    if vah is not None and _crossed(marks, "above_vah", price > vah + buffer_amount):
        if imbalance >= min_imbalance:
            return Bias("value_area_breakout", "bullish",
                        f"accepted above VAH {vah:.2f} with buy imbalance {imbalance:+.2f}")
    if val is not None and _crossed(marks, "below_val", price < val - buffer_amount):
        if imbalance <= -min_imbalance:
            return Bias("value_area_breakout", "bearish",
                        f"accepted below VAL {val:.2f} with sell imbalance {imbalance:+.2f}")

    # 4. Opening range breakout — the first half hour, not the initial balance.
    if tape.opening_range_complete:
        if _crossed(marks, "above_or", price > tape.or_high + buffer_amount) and delta > 0:
            return Bias("opening_range_breakout", "bullish",
                        f"broke the {OPENING_RANGE_MINUTES}m opening range high "
                        f"{tape.or_high:.2f}, CVD {delta:+,.0f}")
        if _crossed(marks, "below_or", price < tape.or_low - buffer_amount) and delta < 0:
            return Bias("opening_range_breakout", "bearish",
                        f"broke the {OPENING_RANGE_MINUTES}m opening range low "
                        f"{tape.or_low:.2f}, CVD {delta:+,.0f}")

    # 5. Donchian channel break, confirmed by the EMA trend it is breaking with.
    channel = tape.channel()
    if channel:
        channel_high, channel_low = channel
        if _crossed(marks, "above_channel", price > channel_high + buffer_amount) and tape.trend == "up":
            return Bias("donchian_breakout", "bullish",
                        f"cleared the {DONCHIAN_MINUTES}-minute high {channel_high:.2f} "
                        f"with EMA{TREND_FAST_MINUTES} above EMA{TREND_SLOW_MINUTES}")
        if _crossed(marks, "below_channel", price < channel_low - buffer_amount) and tape.trend == "down":
            return Bias("donchian_breakout", "bearish",
                        f"lost the {DONCHIAN_MINUTES}-minute low {channel_low:.2f} "
                        f"with EMA{TREND_FAST_MINUTES} below EMA{TREND_SLOW_MINUTES}")

    # 6. VWAP reclaim / loss with delta agreeing.
    vwap = tape.vwap
    if vwap:
        if _crossed(marks, "above_vwap", price > vwap + buffer_amount) and delta > 0:
            return Bias("vwap_reclaim", "bullish",
                        f"reclaimed session VWAP {vwap:.2f} on positive delta {delta:+,.0f}")
        if _crossed(marks, "below_vwap", price < vwap - buffer_amount) and delta < 0:
            return Bias("vwap_reclaim", "bearish",
                        f"lost session VWAP {vwap:.2f} on negative delta {delta:+,.0f}")

    # 7. Failed extreme: price makes a new extreme, cumulative delta does not.
    kind = divergence.get("kind")
    if kind == "bullish" and profile.position() != "above_value":
        if _crossed(marks, "divergence_bull", True):
            marks["divergence_bear"] = False
            return Bias("failed_extreme_divergence", "bullish",
                        "price made a lower low while cumulative delta made a higher low — "
                        "selling without conviction")
    elif kind == "bearish" and profile.position() != "below_value":
        if _crossed(marks, "divergence_bear", True):
            marks["divergence_bull"] = False
            return Bias("failed_extreme_divergence", "bearish",
                        "price made a higher high while cumulative delta made a lower high — "
                        "buying without conviction")
    else:
        marks["divergence_bull"] = marks["divergence_bear"] = False

    # 8. Trend-day continuation: the profile has already declared a trend day,
    #    so join it on a pullback to VWAP rather than at the extreme.
    day_type = profile.day_type()
    upward = day_type in {"trend_day_up", "normal_variation_up"}
    downward = day_type in {"trend_day_down", "normal_variation_down"}
    if vwap and (upward or downward):
        if upward and _crossed(marks, "trend_pullback_up", price <= vwap) and delta > 0:
            return Bias("trend_day_continuation", "bullish",
                        f"{day_type} pulled back to VWAP {vwap:.2f} with delta still {delta:+,.0f}")
        if downward and _crossed(marks, "trend_pullback_down", price >= vwap) and delta < 0:
            return Bias("trend_day_continuation", "bearish",
                        f"{day_type} rallied to VWAP {vwap:.2f} with delta still {delta:+,.0f}")
    return None
