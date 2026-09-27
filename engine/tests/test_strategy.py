from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.indicators import MACDValue
from macd_trader.models import Candle, IndicatorPoint
from macd_trader.strategy import MACDStrategyManager


def test_intrabar_entry_fires_once_and_does_not_mutate_closed_state():
    settings = Settings(
        fast_period=2, slow_period=3, signal_period=2, kama_period=2, bb_period=2,
        kama_rsi_period=2, kama_rsi_min=0, kama_roc_period=1, kama_roc_min=-100,
    )
    strategy = MACDStrategyManager(settings, EventHub(), ["NSE:TEST"])
    for timestamp, close in enumerate((10, 9, 8, 7, 6), start=1):
        strategy.on_closed_candle(Candle("NSE:TEST", timestamp, close, close, close, close), warmup=True)

    committed_count = strategy.indicators["NSE:TEST"].count
    current = Candle("NSE:TEST", 6, 6, 20, 6, 20)
    signal = strategy.on_live_candle(current)

    assert signal is not None
    assert signal.kind.endswith("_INTRABAR")
    assert strategy.indicators["NSE:TEST"].count == committed_count
    assert strategy.on_live_candle(current) is None
    # The later close updates the committed indicators but never emits a
    # second order for the candle that already entered intrabar.
    assert strategy.on_closed_candle(current) is None


def test_gap_up_macd_jump_through_zero_fires_with_optional_confirmations_off():
    settings = Settings(
        fast_period=2, slow_period=3, signal_period=2, kama_period=2, bb_period=2,
    )
    strategy = MACDStrategyManager(settings, EventHub(), ["NSE:GAP"])
    candle = Candle("NSE:GAP", 1, 100, 120, 100, 120)
    value = MACDValue(
        macd=1.25, signal=0.1, histogram=1.15,
        previous_macd=-0.75, previous_signal=-0.2,
    )
    point = IndicatorPoint(
        "NSE:GAP", 1, value.macd, value.signal, value.histogram,
        kama=None, kama_rsi=None, kama_roc=None,
    )

    signal = strategy._detect(candle, value, point, old_kama=None, volume_ratio=0)

    assert signal is not None
    assert signal.kind == "PREMIUM_MACD_ZERO_CROSS_UP"
    assert strategy.evaluations["NSE:GAP"]["fired"] is True
    assert strategy.evaluations["NSE:GAP"]["required_count"] == 1


def test_enabled_confirmation_can_veto_the_same_gap_up_macd_cross():
    settings = Settings(
        fast_period=2, slow_period=3, signal_period=2, kama_period=2, bb_period=2,
        require_kama_confirmation=True,
    )
    strategy = MACDStrategyManager(settings, EventHub(), ["NSE:GAP"])
    candle = Candle("NSE:GAP", 1, 100, 120, 100, 120)
    value = MACDValue(
        macd=1.25, signal=0.1, histogram=1.15,
        previous_macd=-0.75, previous_signal=-0.2,
    )
    point = IndicatorPoint(
        "NSE:GAP", 1, value.macd, value.signal, value.histogram,
        kama=None, kama_rsi=None, kama_roc=None,
    )

    assert strategy._detect(candle, value, point, old_kama=None, volume_ratio=0) is None
    assert strategy.evaluations["NSE:GAP"]["fired"] is False
    assert strategy.evaluations["NSE:GAP"]["required_count"] == 2
