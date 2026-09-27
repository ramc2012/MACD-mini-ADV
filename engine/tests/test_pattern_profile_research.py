from macd_trader.models import Candle
from macd_trader.pattern_profile_research import profile


def test_profile_value_area_terminates_when_only_upper_bucket_has_remaining_volume():
    rows = [
        Candle("TEST", 1, 100, 100, 100, 100, 40, True),
        Candle("TEST", 2, 110, 110, 110, 110, 30, True),
        Candle("TEST", 3, 111, 111, 111, 111, 30, True),
    ]

    result = profile(rows, bin_size=1.0)

    assert result is not None
    assert result["poc"] == 100.5
    assert result["val"] <= result["poc"] <= result["vah"]
