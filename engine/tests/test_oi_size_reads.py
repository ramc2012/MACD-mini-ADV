"""Size reads added 23 Oct 2026: first-hour futures volume vs usual (matrix, zero points) and near-money OI vs its
hour average (positioning). Both tell the size of the coming move, never its direction."""
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

from macd_trader import agentic_features as feat
from macd_trader import agentic_matrix as mx

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 10, 23)


def _db():
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL,
                  high REAL, low REAL, close REAL, volume INTEGER)""")
    db.execute("""CREATE TABLE chain_snapshots (ts INTEGER, underlying TEXT, expiry TEXT, strike REAL, option_type TEXT,
                  symbol TEXT, ltp REAL, volume INTEGER, oi INTEGER, spot REAL, bid REAL, ask REAL, prev_oi INTEGER,
                  fp REAL, vix REAL)""")
    return db


def _first_hour(db, day, per_minute, symbol="NSE:NIFTY26OCTFUT"):
    lo, _ = feat._bounds(day)
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)",
                   [(symbol, 60, lo + 60 * i, 1, 1, 1, 1, per_minute) for i in range(60)])


def test_first_hour_rvol_compares_today_with_the_prior_sessions_busiest_series():
    feat._FIRST_HOUR_BASE.clear()
    db = _db()
    day = DAY
    for _ in range(20):
        day = feat.previous_session(day)
        _first_hour(db, day, 100)
        _first_hour(db, day, 10, symbol="NSE:NIFTY26NOVFUT")    # the thinner series is ignored
    _first_hour(db, DAY, 200)
    assert feat.first_hour_rvol(db, "NSE:NIFTY50-INDEX", DAY) == 2.0
    assert feat.first_hour_rvol(db, "NSE:NIFTYIT-INDEX", DAY) is None
    feat._FIRST_HOUR_BASE.clear()
    assert feat.first_hour_rvol(_db(), "NSE:NIFTY50-INDEX", DAY) is None      # no history, no read


def test_first_hour_volume_is_a_zero_point_size_note_in_the_matrix():
    base = {"open_type": "OD", "open_dir": 1, "ib_high": 23100.0, "ib_low": 23000.0, "ib_atr": 0.5}
    plain = mx.evaluate(base, "CE", "11:00", 23120.0)
    heavy = mx.evaluate({**base, "ib_rvol": 1.8}, "CE", "11:00", 23120.0)
    light = mx.evaluate({**base, "ib_rvol": 0.6}, "CE", "11:00", 23120.0)
    assert heavy["score"] == light["score"] == plain["score"] and heavy["grade"] == plain["grade"]
    assert any("1.8x usual: expect a wider rest of day" in why for _, why in heavy["factors"])
    assert any("light" in why for _, why in light["factors"])
    assert not any("first-hour volume" in why for _, why in plain["factors"])


def test_near_money_oi_against_its_hour_average():
    db = _db()
    lo, _ = feat._bounds(DAY)
    now = lo + 3 * 3600
    rows = []
    for k in range(60):
        ts = now - 60 * (59 - k)
        oi = 1000 if k < 59 else 1300                 # the last snapshot jumps
        for strike, spot_oi in ((23000.0, oi), (25000.0, 50_000)):    # 25000 is >2% away: not near the money
            rows.append((ts, "NIFTY", "2026-10-27", strike, "CE", f"S{strike}", 1, 1, spot_oi, 23050.0, 0, 0, 900, 0, None))
            rows.append((ts, "NIFTY", "2026-10-27", strike, "PE", f"P{strike}", 1, 1, spot_oi, 23050.0, 0, 0, 900, 0, None))
    db.executemany("INSERT INTO chain_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    value = feat.near_money_oi_vs_hour(db, "NIFTY", now, "2026-10-27", lo)
    assert value == round((2600 / ((59 * 2000 + 2600) / 60) - 1) * 100, 1)
    out = feat.positioning(db, "NIFTY", datetime.fromtimestamp(now + 30, IST))
    assert out["near_money_oi_vs_60min_avg_pct"] == value and "size, not direction" in out["near_money_oi_read"]
    assert feat.near_money_oi_vs_hour(db, "NIFTY", lo + 600, "2026-10-27", lo) is None    # too few snapshots
