from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from math import sqrt


@dataclass(slots=True)
class MACDValue:
    macd: float
    signal: float
    histogram: float
    previous_macd: float | None
    previous_signal: float | None


class IncrementalMACD:
    """O(1) EMA/MACD update suitable for the live candle-close hot path."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        if not 0 < fast < slow or signal < 1:
            raise ValueError("MACD requires 0 < fast < slow and signal >= 1")
        self.fast_alpha = 2.0 / (fast + 1)
        self.slow_alpha = 2.0 / (slow + 1)
        self.signal_alpha = 2.0 / (signal + 1)
        self.fast_ema: float | None = None
        self.slow_ema: float | None = None
        self.signal_ema: float | None = None
        self.count = 0

    def update(self, price: float) -> MACDValue:
        previous_macd = None
        previous_signal = self.signal_ema
        if self.fast_ema is not None and self.slow_ema is not None:
            previous_macd = self.fast_ema - self.slow_ema
        self.fast_ema = price if self.fast_ema is None else self.fast_ema + self.fast_alpha * (price - self.fast_ema)
        self.slow_ema = price if self.slow_ema is None else self.slow_ema + self.slow_alpha * (price - self.slow_ema)
        macd = self.fast_ema - self.slow_ema
        self.signal_ema = macd if self.signal_ema is None else self.signal_ema + self.signal_alpha * (macd - self.signal_ema)
        self.count += 1
        return MACDValue(macd, self.signal_ema, macd - self.signal_ema, previous_macd, previous_signal)

    def preview(self, price: float) -> MACDValue:
        """Project one update without mutating the committed candle state.

        Forming-candle checks run for every market tick. Copying the complete
        indicator object for each check is needlessly expensive because MACD
        needs only four scalar calculations to project the next value.
        """
        previous_macd = None
        if self.fast_ema is not None and self.slow_ema is not None:
            previous_macd = self.fast_ema - self.slow_ema
        fast_ema = price if self.fast_ema is None else self.fast_ema + self.fast_alpha * (price - self.fast_ema)
        slow_ema = price if self.slow_ema is None else self.slow_ema + self.slow_alpha * (price - self.slow_ema)
        macd = fast_ema - slow_ema
        signal_ema = macd if self.signal_ema is None else self.signal_ema + self.signal_alpha * (macd - self.signal_ema)
        return MACDValue(macd, signal_ema, macd - signal_ema, previous_macd, self.signal_ema)


@dataclass(slots=True)
class BollingerValue:
    middle: float | None
    upper: float | None
    lower: float | None
    width: float | None


class IncrementalBollingerBands:
    """Rolling Bollinger Bands with population deviation and O(1) updates."""

    def __init__(self, period: int = 20, deviations: float = 2.0):
        if period < 2 or deviations <= 0:
            raise ValueError("Bollinger Bands require period >= 2 and positive deviations")
        self.period = period
        self.deviations = deviations
        self.values: deque[float] = deque()
        self.total = 0.0
        self.total_squared = 0.0

    def update(self, price: float) -> BollingerValue:
        self.values.append(price)
        self.total += price
        self.total_squared += price * price
        if len(self.values) > self.period:
            removed = self.values.popleft()
            self.total -= removed
            self.total_squared -= removed * removed
        if len(self.values) < self.period:
            return BollingerValue(None, None, None, None)
        middle = self.total / self.period
        variance = max(0.0, self.total_squared / self.period - middle * middle)
        spread = self.deviations * sqrt(variance)
        upper, lower = middle + spread, middle - spread
        width = (upper - lower) / middle if middle else None
        return BollingerValue(middle, upper, lower, width)


class IncrementalKAMA:
    """Kaufman adaptive moving average using the standard 10/2/30 defaults."""

    def __init__(self, period: int = 10, fast: int = 2, slow: int = 30):
        if period < 1 or not 0 < fast < slow:
            raise ValueError("KAMA requires period >= 1 and 0 < fast < slow")
        self.period = period
        self.fast_sc = 2.0 / (fast + 1)
        self.slow_sc = 2.0 / (slow + 1)
        self.prices: deque[float] = deque(maxlen=period + 1)
        self.value: float | None = None

    def update(self, price: float) -> float | None:
        self.prices.append(price)
        if len(self.prices) < self.period + 1:
            if self.value is None:
                self.value = price
            return None
        values = list(self.prices)
        change = abs(values[-1] - values[0])
        volatility = sum(abs(values[index] - values[index - 1]) for index in range(1, len(values)))
        efficiency = change / volatility if volatility else 0.0
        smoothing = (efficiency * (self.fast_sc - self.slow_sc) + self.slow_sc) ** 2
        self.value = price if self.value is None else self.value + smoothing * (price - self.value)
        return self.value


class IncrementalRSI:
    """Wilder RSI calculated from a supplied series, here the KAMA values."""

    def __init__(self, period: int = 14):
        if period < 2:
            raise ValueError("RSI period must be at least 2")
        self.period = period
        self.previous: float | None = None
        self.seed_gains: deque[float] = deque(maxlen=period)
        self.seed_losses: deque[float] = deque(maxlen=period)
        self.average_gain: float | None = None
        self.average_loss: float | None = None

    def update(self, value: float) -> float | None:
        if self.previous is None:
            self.previous = value
            return None
        change = value - self.previous
        self.previous = value
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if self.average_gain is None or self.average_loss is None:
            self.seed_gains.append(gain)
            self.seed_losses.append(loss)
            if len(self.seed_gains) < self.period:
                return None
            self.average_gain = sum(self.seed_gains) / self.period
            self.average_loss = sum(self.seed_losses) / self.period
        else:
            self.average_gain = (self.average_gain * (self.period - 1) + gain) / self.period
            self.average_loss = (self.average_loss * (self.period - 1) + loss) / self.period
        if self.average_loss == 0:
            return 100.0
        relative_strength = self.average_gain / self.average_loss
        return 100 - 100 / (1 + relative_strength)


class IncrementalROC:
    """Rate of change percentage calculated from a supplied series."""

    def __init__(self, period: int = 5):
        if period < 1:
            raise ValueError("ROC period must be positive")
        self.period = period
        self.values: deque[float] = deque(maxlen=period + 1)

    def update(self, value: float) -> float | None:
        self.values.append(value)
        if len(self.values) <= self.period:
            return None
        base = self.values[0]
        return (value / base - 1) * 100 if base else None
