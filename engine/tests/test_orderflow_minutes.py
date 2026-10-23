"""Minute-scale order flow (research/orderflow_detectors_20261023): bursts are not chased, divergence reads minutes."""
import tempfile
from datetime import UTC, datetime, timedelta

from macd_trader.models import Tick
from macd_trader.orderflow import FlowState, OrderFlowTracker

from test_directional_desk import _engine, _seeded

START = datetime(2026, 10, 23, 4, 0, tzinfo=UTC)          # 09:30 IST


def minutes(tracker, symbol, rows):
    """rows: [(price, buy_size, sell_size)] one per minute -> ticks lifting the ask / hitting the bid."""
    volume = 1_000
    tracker.on_tick(Tick(symbol, rows[0][0], volume=volume, bid=rows[0][0] - 0.05, ask=rows[0][0] + 0.05, timestamp=START))
    for k, (price, buy, sell) in enumerate(rows):
        at = START + timedelta(minutes=k, seconds=10)
        if buy:
            volume += buy
            tracker.on_tick(Tick(symbol, price + 0.05, volume=volume, bid=price - 0.05, ask=price + 0.05, last_qty=buy, timestamp=at))
        if sell:
            volume += sell
            tracker.on_tick(Tick(symbol, price - 0.05, volume=volume, bid=price - 0.05, ask=price + 0.05, last_qty=sell,
                                 timestamp=at + timedelta(seconds=20)))


def test_a_one_sided_heavy_minute_is_a_burst_and_ages_out():
    tracker = OrderFlowTracker()
    rows = [(100.0, 50, 50)] * 25 + [(100.5, 900, 50)]
    minutes(tracker, "NSE:SBIN-EQ", rows)
    burst = tracker.burst("NSE:SBIN-EQ")
    assert burst["detected"] and burst["side"] == "buy_burst" and burst["direction"] == 1 and burst["minutes_ago"] == 0
    later = (START + timedelta(minutes=len(rows) + 4)).timestamp()
    assert not tracker.burst("NSE:SBIN-EQ", now=later)["detected"]
    calm = OrderFlowTracker()
    minutes(calm, "NSE:SBIN-EQ", [(100.0, 50, 50)] * 26)
    assert not calm.burst("NSE:SBIN-EQ")["detected"]


def test_minute_divergence_classic_and_hidden():
    tracker = OrderFlowTracker()
    # a sell-off to 99.4 on heavy selling, a bounce on buying, then a LOWER low on light selling: CVD stays far above
    # its earlier low while price breaks it -- classic bullish
    rows = ([(100.0 - 0.02 * k, 20, 80) for k in range(30)] + [(99.4 + 0.02 * k, 120, 40) for k in range(20)]
            + [(99.75 - 0.03 * k, 40, 60) for k in range(15)])
    minutes(tracker, "NSE:SBIN-EQ", rows)
    div = tracker.minute_divergence("NSE:SBIN-EQ")
    assert div["classic"] == "bullish" and div["detected"] and div["kind"] == "bullish"
    held = OrderFlowTracker()
    # price holds above its low while sellers keep hitting: hidden bullish (sellers absorbed)
    rows = [(100.0 - 0.02 * k, 45, 55) for k in range(50)] + [(99.5, 10, 120) for _ in range(15)]
    minutes(held, "NSE:SBIN-EQ", rows)
    assert held.minute_divergence("NSE:SBIN-EQ")["hidden"] == "bullish"
    assert not OrderFlowTracker().minute_divergence("X")["detected"]


def test_the_desk_does_not_chase_a_breakout_on_a_same_direction_burst():
    with tempfile.TemporaryDirectory() as folder:
        engine = _engine(folder)
        underlying = "NSE:SBIN-EQ"
        profile = _seeded(engine, underlying, buy=900, sell=100)
        state = engine.flow.states[underlying]
        base = int(START.timestamp() // 60 * 60)
        for k in range(25):
            state.minutes.append([base + 60 * k, 100.5, 100.0, 50.0, 50.0, 0.0])
        state.minutes.append([base + 60 * 25, 101.5, 900.0, 850.0, 50.0, 800.0])     # a buy burst this minute
        bias, reason = engine.evaluate(underlying, profile.ib_high + 1, 610, commit=True)
        assert bias is None and "vetoed: chasing a buy burst" in reason


def test_minute_bars_survive_the_desk_checkpoint():
    with tempfile.TemporaryDirectory() as folder:
        engine = _engine(folder)
        minutes(engine.flow, "NSE:SBIN-EQ", [(100.0, 50, 50)] * 5)
        bars = [list(b) for b in engine.flow.states["NSE:SBIN-EQ"].minutes]
        engine.session_day = "2026-10-23"
        assert engine.save_state()
        restored = _engine(folder)
        restored.restore_state("2026-10-23")
        assert [list(b) for b in restored.flow.states["NSE:SBIN-EQ"].minutes] == bars
