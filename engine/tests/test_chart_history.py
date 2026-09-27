from datetime import datetime
from zoneinfo import ZoneInfo

from macd_trader.chart_history import aggregate_session_candles
from macd_trader.models import Candle


IST = ZoneInfo("Asia/Kolkata")


def timestamp(hour: int, minute: int) -> int:
    return int(datetime(2026, 8, 11, hour, minute, tzinfo=IST).timestamp())


def test_history_aggregation_is_aligned_to_0915_ist_session():
    rows = [
        Candle("NSE:TEST", timestamp(9, 15), 100, 102, 99, 101, 10, True),
        Candle("NSE:TEST", timestamp(9, 44), 101, 105, 100, 104, 20, True),
        Candle("NSE:TEST", timestamp(9, 45), 104, 106, 103, 105, 30, True),
    ]
    result = aggregate_session_candles(rows, 1800)
    assert [row.timestamp for row in result] == [timestamp(9, 15), timestamp(9, 45)]
    assert (result[0].open, result[0].high, result[0].low, result[0].close, result[0].volume) == (100, 105, 99, 104, 30)
