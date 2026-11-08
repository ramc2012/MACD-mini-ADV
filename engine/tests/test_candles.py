from datetime import UTC, datetime

from macd_trader.candles import CandleAggregator
from macd_trader.models import Tick


def _session(hh, mm, ss=0):
    """A timestamp inside today's trading session — the aggregator ignores
    ticks outside 09:15-15:30 IST, so fixtures must sit within it."""
    from datetime import date
    from macd_trader.candles import IST
    today = date(2026, 11, 9)       # a regular session (today's date could be a special session with other hours)
    return datetime(today.year, today.month, today.day, hh, mm, ss, tzinfo=IST).astimezone(UTC)


def test_aggregator_closes_previous_bucket():
    aggregator = CandleAggregator(60)
    first = Tick("NSE:TEST", 100, 10, _session(10, 2, 0))
    second = Tick("NSE:TEST", 102, 20, _session(10, 3, 1))
    _, closed = aggregator.on_tick(first)
    assert closed is None
    current, closed = aggregator.on_tick(second)
    assert closed is not None
    assert closed.close == 100
    assert closed.closed is True
    assert current.open == 102

