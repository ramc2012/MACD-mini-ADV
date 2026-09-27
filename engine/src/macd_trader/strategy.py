from __future__ import annotations

import copy
from collections import defaultdict, deque

from .config import Settings
from .events import EventHub
from .indicators import IncrementalBollingerBands, IncrementalKAMA, IncrementalMACD, IncrementalROC, IncrementalRSI, MACDValue
from .models import Candle, IndicatorPoint, Signal


class MACDStrategyManager:
    def __init__(self, settings: Settings, events: EventHub, symbols: list[str] | None = None):
        self.settings = settings
        self.events = events
        self.indicators = {
            symbol: IncrementalMACD(settings.fast_period, settings.slow_period, settings.signal_period)
            for symbol in (symbols or settings.symbols)
        }
        self.bollinger = {
            symbol: IncrementalBollingerBands(settings.bb_period, settings.bb_deviations)
            for symbol in (symbols or settings.symbols)
        }
        self.kama = {
            symbol: IncrementalKAMA(settings.kama_period, settings.kama_fast, settings.kama_slow)
            for symbol in (symbols or settings.symbols)
        }
        self.kama_rsi = {symbol: IncrementalRSI(settings.kama_rsi_period) for symbol in (symbols or settings.symbols)}
        self.kama_roc = {symbol: IncrementalROC(settings.kama_roc_period) for symbol in (symbols or settings.symbols)}
        self.previous_kama: dict[str, float | None] = {}
        self.volumes: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=settings.bb_period))
        self.points: dict[str, IndicatorPoint] = {}
        self.point_history: dict[str, deque[IndicatorPoint]] = defaultdict(lambda: deque(maxlen=500))
        self.signals: list[Signal] = []
        # A forming candle is evaluated on every tick, but a qualifying move
        # must only create one entry even if it continues to trade higher.
        self.intrabar_fired: set[tuple[str, int]] = set()
        # Latest per-symbol entry-condition evaluation, kept for every closed
        # candle once indicators are mature — including warm-up, so the radar
        # has an "as of last close" answer immediately after (re)connect.
        self.evaluations: dict[str, dict] = {}

    def on_closed_candle(self, candle: Candle, *, warmup: bool = False) -> Signal | None:
        if candle.symbol not in self.indicators:
            self.indicators[candle.symbol] = IncrementalMACD(self.settings.fast_period, self.settings.slow_period, self.settings.signal_period)
            self.bollinger[candle.symbol] = IncrementalBollingerBands(self.settings.bb_period, self.settings.bb_deviations)
            self.kama[candle.symbol] = IncrementalKAMA(self.settings.kama_period, self.settings.kama_fast, self.settings.kama_slow)
            self.kama_rsi[candle.symbol] = IncrementalRSI(self.settings.kama_rsi_period)
            self.kama_roc[candle.symbol] = IncrementalROC(self.settings.kama_roc_period)
        value = self.indicators[candle.symbol].update(candle.close)
        bb = self.bollinger[candle.symbol].update(candle.close)
        old_kama = self.kama[candle.symbol].value
        kama = self.kama[candle.symbol].update(candle.close)
        kama_rsi = self.kama_rsi[candle.symbol].update(kama) if kama is not None else None
        kama_roc = self.kama_roc[candle.symbol].update(kama) if kama is not None else None
        self.previous_kama[candle.symbol] = old_kama
        previous_volumes = self.volumes[candle.symbol]
        average_volume = sum(previous_volumes) / len(previous_volumes) if previous_volumes else 0
        volume_ratio = candle.volume / average_volume if average_volume > 0 else 0
        previous_volumes.append(candle.volume)
        point = IndicatorPoint(candle.symbol, candle.timestamp, value.macd, value.signal, value.histogram,
                               bb.middle, bb.upper, bb.lower, bb.width, kama, kama_rsi, kama_roc)
        self.points[candle.symbol] = point
        self.point_history[candle.symbol].append(point)
        if not warmup:
            self.events.publish("indicator", point)
        if self.indicators[candle.symbol].count <= self.settings.slow_period:
            return None
        signal = self._detect(candle, value, point, old_kama, volume_ratio)
        if warmup:
            return None
        if signal and (candle.symbol, candle.timestamp) not in self.intrabar_fired:
            self.signals.insert(0, signal)
            self.signals = self.signals[:200]
            self.events.publish("signal", signal)
            return signal
        # The provisional values used for the live check were deliberately
        # copies, so the closed candle must still be committed above.  It is
        # now safe to discard the one-candle de-duplication marker.
        self.intrabar_fired.discard((candle.symbol, candle.timestamp))
        return None

    def on_live_candle(self, candle: Candle) -> Signal | None:
        """Emit a one-shot entry when the still-forming candle qualifies.

        Indicators remain committed only on closed candles.  Evaluating cloned
        state gives the signal the current LTP without counting every tick as a
        new EMA/KAMA observation, and prevents the next candle close from
        double-firing the same setup.
        """
        if candle.symbol not in self.indicators:
            return None
        key = (candle.symbol, candle.timestamp)
        if key in self.intrabar_fired:
            return None
        indicator = self.indicators[candle.symbol]
        value = indicator.preview(candle.close)
        if indicator.count + 1 <= self.settings.slow_period:
            return None
        previous_macd = value.previous_macd
        cross = previous_macd is not None and previous_macd <= 0 < value.macd
        kama_ok = rsi_ok = roc_ok = False
        needs_kama = any((
            self.settings.require_kama_confirmation,
            self.settings.require_kama_rsi_confirmation,
            self.settings.require_kama_roc_confirmation,
        ))
        if needs_kama:
            kama_indicator = copy.deepcopy(self.kama[candle.symbol])
            old_kama = kama_indicator.value
            kama = kama_indicator.update(candle.close)
            kama_ok = kama is not None and old_kama is not None and candle.close > kama > old_kama
            if self.settings.require_kama_rsi_confirmation:
                rsi_indicator = copy.deepcopy(self.kama_rsi[candle.symbol])
                kama_rsi = rsi_indicator.update(kama) if kama is not None else None
                rsi_ok = kama_rsi is not None and kama_rsi >= self.settings.kama_rsi_min
            if self.settings.require_kama_roc_confirmation:
                roc_indicator = copy.deepcopy(self.kama_roc[candle.symbol])
                kama_roc = roc_indicator.update(kama) if kama is not None else None
                roc_ok = kama_roc is not None and kama_roc > self.settings.kama_roc_min
        if not (cross and self._confirmations_pass(kama_ok, rsi_ok, roc_ok)):
            return None
        self.intrabar_fired.add(key)
        signal = Signal(
            candle.symbol, "BUY", self._signal_kind(intrabar=True),
            candle.close, value.macd, value.signal, value.histogram,
            evaluated_candle_timestamp=candle.timestamp,
        )
        self.signals.insert(0, signal)
        self.signals = self.signals[:200]
        self.events.publish("signal", signal)
        return signal

    def _detect(
        self, candle: Candle, value: MACDValue, point: IndicatorPoint,
        old_kama: float | None, volume_ratio: float,
    ) -> Signal | None:
        previous_macd = value.previous_macd
        previous_signal = value.previous_signal
        if previous_macd is None or previous_signal is None:
            return None
        # Direction comes from the premium contract itself. Risk exits are
        # handled by the execution manager, not by a MACD reversal.
        # Entry rule: MACD zero-cross up is mandatory. KAMA, RSI(KAMA) and
        # ROC(KAMA) are independently configurable confirmations. Bollinger
        # and volume remain radar context only.
        cross = previous_macd <= 0 < value.macd
        kama_ok = point.kama is not None and old_kama is not None and candle.close > point.kama > old_kama
        rsi_ok = point.kama_rsi is not None and point.kama_rsi >= self.settings.kama_rsi_min
        roc_ok = point.kama_roc is not None and point.kama_roc > self.settings.kama_roc_min
        fired = cross and self._confirmations_pass(kama_ok, rsi_ok, roc_ok)
        required_count = 1 + sum((
            self.settings.require_kama_confirmation,
            self.settings.require_kama_rsi_confirmation,
            self.settings.require_kama_roc_confirmation,
        ))
        passed = int(cross)
        if self.settings.require_kama_confirmation:
            passed += int(kama_ok)
        if self.settings.require_kama_rsi_confirmation:
            passed += int(rsi_ok)
        if self.settings.require_kama_roc_confirmation:
            passed += int(roc_ok)
        self.evaluations[candle.symbol] = {
            "symbol": candle.symbol,
            "timestamp": candle.timestamp,
            "close": candle.close,
            "macd": value.macd,
            "previous_macd": previous_macd,
            "cross": cross,
            "kama_ok": kama_ok,
            "kama_rsi": point.kama_rsi,
            "kama_roc": point.kama_roc,
            "rsi_ok": rsi_ok,
            "roc_ok": roc_ok,
            "kama_required": self.settings.require_kama_confirmation,
            "rsi_required": self.settings.require_kama_rsi_confirmation,
            "roc_required": self.settings.require_kama_roc_confirmation,
            "bb_ok": point.bb_upper is not None and candle.close <= point.bb_upper,
            "volume_ratio": round(volume_ratio, 3),
            "passed": passed,
            "required_count": required_count,
            "fired": fired,
        }
        if fired:
            return Signal(
                candle.symbol, "BUY", self._signal_kind(),
                candle.close, value.macd, value.signal, value.histogram,
                evaluated_candle_timestamp=candle.timestamp,
            )
        return None

    def _confirmations_pass(self, kama_ok: bool, rsi_ok: bool, roc_ok: bool) -> bool:
        return (
            (not self.settings.require_kama_confirmation or kama_ok)
            and (not self.settings.require_kama_rsi_confirmation or rsi_ok)
            and (not self.settings.require_kama_roc_confirmation or roc_ok)
        )

    def _signal_kind(self, *, intrabar: bool = False) -> str:
        parts = ["PREMIUM", "MACD", "ZERO", "CROSS", "UP"]
        if self.settings.require_kama_confirmation:
            parts.append("KAMA")
        if self.settings.require_kama_rsi_confirmation:
            parts.append("RSI")
        if self.settings.require_kama_roc_confirmation:
            parts.append("ROC")
        if intrabar:
            parts.append("INTRABAR")
        return "_".join(parts)

    def snapshot(self) -> dict:
        return {
            # The full per-symbol history is only needed for the selected chart,
            # which the UI loads via /api/chart — the snapshot carries just the
            # latest point per symbol for the watchlist columns.
            #
            # A second "indicators" key used to ship the same latest-point map
            # in a flatter shape. Nothing ever read it; it was 262 KiB of every
            # snapshot frame.
            "indicator_history": {symbol: [rows[-1]] for symbol, rows in self.point_history.items() if rows},
            "signals": self.signals,
        }

    def diagnostics(self) -> dict:
        rows = sorted(self.evaluations.values(), key=lambda row: (-row["passed"], -row["timestamp"]))
        totals = {key: sum(1 for row in rows if row[key]) for key in ("cross", "kama_ok", "rsi_ok", "roc_ok", "fired")}
        return {
            "evaluated": len(rows),
            "condition_totals": totals,
            "enabled_conditions": {
                "macd_zero_cross_up": True,
                "kama": self.settings.require_kama_confirmation,
                "kama_rsi": self.settings.require_kama_rsi_confirmation,
                "kama_roc": self.settings.require_kama_roc_confirmation,
            },
            "required_count": 1 + sum((
                self.settings.require_kama_confirmation,
                self.settings.require_kama_rsi_confirmation,
                self.settings.require_kama_roc_confirmation,
            )),
            "rows": rows[:120],
        }
