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
    assert parsed == datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
