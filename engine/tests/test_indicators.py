from macd_trader.indicators import IncrementalBollingerBands, IncrementalKAMA, IncrementalMACD, IncrementalROC, IncrementalRSI


def test_incremental_macd_moves_positive_for_rising_prices():
    indicator = IncrementalMACD(12, 26, 9)
    point = None
    for price in range(100, 160):
        point = indicator.update(float(price))
    assert point is not None
    assert point.macd > 0
    assert point.signal > 0


def test_incremental_macd_rejects_invalid_periods():
    try:
        IncrementalMACD(26, 12, 9)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid periods were accepted")


def test_macd_preview_matches_update_without_mutating_state():
    indicator = IncrementalMACD(3, 5, 2)
    for price in (100, 101, 99, 102, 103):
        indicator.update(price)
    before = (
        indicator.fast_ema,
        indicator.slow_ema,
        indicator.signal_ema,
        indicator.count,
    )

    projected = indicator.preview(104)

    assert (
        indicator.fast_ema,
        indicator.slow_ema,
        indicator.signal_ema,
        indicator.count,
    ) == before
    committed = indicator.update(104)
    assert projected == committed


def test_bollinger_bands_and_kama_warm_up():
    bands = IncrementalBollingerBands(period=3, deviations=2)
    assert bands.update(10).middle is None
    assert bands.update(11).middle is None
    value = bands.update(12)
    assert value.middle == 11
    assert value.lower < value.middle < value.upper

    kama = IncrementalKAMA(period=3, fast=2, slow=30)
    assert kama.update(10) is None
    assert kama.update(11) is None
    assert kama.update(12) is None
    assert kama.update(13) is not None


def test_rsi_and_roc_warm_up_then_confirm_rising_kama_series():
    rsi = IncrementalRSI(period=3)
    roc = IncrementalROC(period=2)
    assert rsi.update(10) is None
    assert roc.update(10) is None
    assert rsi.update(11) is None
    assert roc.update(11) is None
    assert rsi.update(12) is None
    assert round(roc.update(12) or 0, 6) == 20
    assert rsi.update(13) == 100
    assert round(roc.update(13) or 0, 6) == 18.181818
