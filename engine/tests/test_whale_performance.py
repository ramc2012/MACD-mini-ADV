"""What an instrument did after it entered the whale list."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from macd_trader import whale

IST = ZoneInfo("Asia/Kolkata")


def _db(path):
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER,
        timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER)""")
    whale.ensure_schema(connection)
    return connection


def _bars(connection, symbol, day, path):
    """One bar a minute from 10:00 IST: (high, low, close) per bar."""
    start = datetime.fromisoformat(f"{day}T10:00:00").replace(tzinfo=IST)
    rows = [(symbol, 60, int((start + timedelta(minutes=i)).timestamp()), c, h, l, c, 100)
            for i, (h, l, c) in enumerate(path)]
    connection.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    return int(start.timestamp() * 1000)


def test_buy_and_sell_entries_read_the_move_their_way(tmp_path):
    connection = _db(str(tmp_path / "h.sqlite3"))
    path = [(101, 99, 100)] + [(100 + i, 99 + i, 100 + i) for i in range(1, 70)] + [(170, 90, 95)]
    start_ms = _bars(connection, "NSE:X-EQ", "2026-09-29", path)
    buy = whale.entry_performance(connection, "NSE:X-EQ", start_ms, 100.0, 1)
    assert buy["max_return_pct"] == 70.0 and buy["min_return_pct"] == -10.0
    assert buy["favourable_pct"] == 70.0 and buy["adverse_pct"] == -10.0
    assert buy["forward"]["15m"] == 15.0 and buy["forward"]["60m"] == 60.0
    sell = whale.entry_performance(connection, "NSE:X-EQ", start_ms, 100.0, -1)
    assert sell["favourable_pct"] == 10.0 and sell["adverse_pct"] == -70.0


def test_following_stops_after_five_more_sessions(tmp_path):
    connection = _db(str(tmp_path / "h.sqlite3"))
    start_ms = _bars(connection, "NSE:X-EQ", "2026-09-21", [(100, 100, 100)])
    for offset, day in enumerate(("2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28")):
        _bars(connection, "NSE:X-EQ", day, [(101 + offset, 99, 100)])
    _bars(connection, "NSE:X-EQ", "2026-09-29", [(200, 50, 100)])   # the seventh session: not followed
    perf = whale.entry_performance(connection, "NSE:X-EQ", start_ms, 100.0, 1)
    assert perf["sessions"] == 6 and perf["complete"] is True
    assert perf["max_return_pct"] == 5.0 and perf["min_return_pct"] == -1.0


def test_an_entry_is_the_first_appearance_and_is_kept(tmp_path):
    connection = _db(str(tmp_path / "h.sqlite3"))
    day = "2026-09-29"
    first = int(datetime.fromisoformat(f"{day}T10:00:00").replace(tzinfo=IST).timestamp() * 1000)
    whale.save_events(connection, day, [
        whale.WhaleEvent("NSE:X-EQ", first, "large_print", 1, 50_000, 100.0, 3.0, "50000 vs 30m median 100"),
        whale.WhaleEvent("NSE:X-EQ", first + 60_000, "large_print", -1, 90_000, 104.0, 5.0, "90000 vs 30m median 100"),
    ])
    assert whale.record_entries(connection, day) == 1
    whale.save_events(connection, day, [whale.WhaleEvent("NSE:X-EQ", first - 60_000, "large_print", -1, 1, 99.0, 1.0, "")])
    assert whale.record_entries(connection, day) == 0     # already entered: not moved
    row = connection.execute("SELECT entry_price, side, kind FROM whale_entries").fetchone()
    assert row == (100.0, 1, "large_print")
    _bars(connection, "NSE:X-EQ", day, [(101, 99, 100), (106, 100, 105)])
    report = whale.performance(connection, day, day)
    assert report["summary"]["entries"] == 1 and report["entries"][0]["max_return_pct"] == 6.0
    assert report["by_side"]["buy"]["went_their_way_pct"] == 100.0
