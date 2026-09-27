from datetime import UTC, datetime, timedelta

from macd_trader.candle_store import store_historical_candles
from macd_trader.contracts import OptionContract
from macd_trader.models import Candle
from macd_trader.ratio_history import build_ratio_history, ema_points


def test_ratio_history_aligns_premium_closes_and_computes_both_sides(tmp_path):
    database = str(tmp_path / "history.sqlite3")
    prices = {
        ("CE", "ITM"): 120.0, ("CE", "ATM"): 80.0, ("CE", "OTM"): 40.0,
        ("PE", "ITM"): 150.0, ("PE", "ATM"): 100.0, ("PE", "OTM"): 50.0,
    }
    start = datetime(2026, 8, 28, 3, 45, tzinfo=UTC)  # 09:15 IST
    contracts = []
    for (side, role), price in prices.items():
        symbol = f"NSE:TEST26SEP{side}{role}"
        contracts.append(OptionContract(
            underlying="TEST", spot_symbol="NSE:TEST-EQ", option_type=side,
            symbol=symbol, strike=100.0, expiry="2026-09-29", selection_price=100.0,
            moneyness=role, analysis_only=role != "ATM",
        ))
        rows = [
            Candle(symbol, int((start + timedelta(minutes=index)).timestamp()), price, price, price, price, 10, True)
            for index in range(10)
        ]
        assert store_historical_candles(database, rows, "2026-09-29") == 10

    payload = build_ratio_history(database, contracts, 300, {
        "fast_period": 3, "slow_period": 6, "signal_period": 2,
        "bb_period": 5, "bb_deviations": 2.0, "kama_period": 3,
        "kama_fast": 2, "kama_slow": 5, "kama_rsi_period": 3, "kama_roc_period": 2,
    })

    assert len(payload["contracts"]) == 6
    assert all(len(row["points"]) == 2 for row in payload["contracts"])
    # Only the ITM/OTM spread is charted, one series per side.
    assert [row["key"] for row in payload["ratios"]] == ["CE_ITM_OTM", "PE_ITM_OTM"]
    ratios = {row["key"]: row["points"][0]["value"] for row in payload["ratios"]}
    assert ratios["CE_ITM_OTM"] == 3.0
    assert ratios["PE_ITM_OTM"] == 3.0
    # A flat ratio leaves its seeded EMA on the same value, timestamp for timestamp.
    for row in payload["ratios"]:
        assert row["ema_period"] == 5
        assert [point["time"] for point in row["ema"]] == [point["time"] for point in row["points"]]
        assert [point["value"] for point in row["ema"]] == [3.0, 3.0]


def test_ratio_ema_seeds_on_the_first_ratio_and_lags_a_step_change():
    points = [{"time": index, "value": value} for index, value in enumerate([1.0, 1.0, 2.0])]

    smoothed = ema_points(points, 5)

    alpha = 2 / 6
    assert smoothed[0]["value"] == 1.0
    assert smoothed[1]["value"] == 1.0
    assert smoothed[2]["value"] == round(1.0 + alpha * 1.0, 5)
    assert smoothed[2]["value"] < 2.0
