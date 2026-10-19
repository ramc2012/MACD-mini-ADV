from datetime import UTC, datetime

from macd_trader.brokers import FyersBroker


def test_exchange_epoch_is_used_for_tick_time():
    expected = datetime(2026, 8, 11, 9, 45, tzinfo=UTC)
    parsed = FyersBroker.exchange_timestamp({"last_traded_time": int(expected.timestamp())})
    assert parsed == expected


def test_exchange_indian_clock_is_converted_to_utc():
    parsed = FyersBroker.exchange_timestamp({"tt": "11-08-2026 15:15:00"})
    assert parsed == datetime(2026, 8, 11, 9, 45, tzinfo=UTC)


def test_after_hours_feed_snapshot_is_clamped_to_market_close():
    after_close = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    parsed = FyersBroker.exchange_timestamp({"exch_feed_time": int(after_close.timestamp())})
    # No symbol: the market's last close, 15:40 IST since the Aug 2026 regime.
    assert parsed == datetime(2026, 8, 11, 10, 10, tzinfo=UTC)


def test_after_hours_clamp_uses_the_instruments_own_close():
    after_close = int(datetime(2026, 8, 11, 12, 0, tzinfo=UTC).timestamp())
    future = FyersBroker.exchange_timestamp({"symbol": "NSE:NIFTY26AUGFUT", "exch_feed_time": after_close})
    equity = FyersBroker.exchange_timestamp({"symbol": "NSE:SBIN-EQ", "exch_feed_time": after_close})
    assert future == datetime(2026, 8, 11, 10, 10, tzinfo=UTC)   # 15:40 IST
    assert equity == datetime(2026, 8, 11, 9, 45, tzinfo=UTC)    # 15:15 IST
    # A real F&O stamp inside 15:30-15:40 is left alone.
    live = int(datetime(2026, 8, 11, 10, 5, tzinfo=UTC).timestamp())
    assert FyersBroker.exchange_timestamp({"symbol": "NSE:NIFTY26AUGFUT", "exch_feed_time": live}) == \
        datetime(2026, 8, 11, 10, 5, tzinfo=UTC)
