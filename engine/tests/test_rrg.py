"""Relative rotation: an outperformer lands right of 100 and its smoothed tail moves in small steps."""
import math
import sqlite3
from datetime import UTC, datetime, timedelta

from macd_trader import rrg


def test_steady_outperformer_is_right_of_centre_with_smooth_tail(tmp_path):
    path = tmp_path / "h.sqlite3"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,
                  high REAL, low REAL, close REAL, volume INTEGER)""")
    start = datetime.now(UTC).replace(hour=4, minute=0, second=0, microsecond=0) - timedelta(days=12)
    rows = []
    for i in range(12 * 6 * 6):                      # 6 hourly-ish bars a day of 10-minute candles, 12 days
        ts = int((start + timedelta(minutes=10 * i)).timestamp())
        noise = math.sin(i * 1.7) * 0.4
        for symbol, price in (("NSE:NIFTY50-INDEX", 100 + noise), ("NSE:SBIN-EQ", 100 + 0.02 * i + noise)):
            rows.append((symbol, 60, ts, price, price, price, price, 1))
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
    db.commit(); db.close()
    out = rrg.compute_rrg(str(path), ["NSE:SBIN-EQ"], timeframe_seconds=1800, window=10, tail=6)
    row = out["symbols"][0]
    assert row["x"] > 100 and len(row["tail"]) == 6
    assert max(math.hypot(a["x"] - b["x"], a["y"] - b["y"]) for a, b in zip(row["tail"], row["tail"][1:])) < 2


def test_ema_starts_at_the_first_value_and_lags():
    assert rrg._ema([1, 1, 4], 3) == [1, 1, 2.5]
