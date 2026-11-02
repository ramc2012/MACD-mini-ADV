"""IB30 / IS30 on the order-flow charts (owner, 2 Nov 2026): 30-minute periods from the 09:15 open marked when market
orders on one side dominate, with the open interest added or cut over the period where OI is stored."""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from macd_trader import auction_views
from macd_trader.footprint import INITIATIVE_SECONDS, INITIATIVE_SHARE, FootprintBook, period_start

IST = ZoneInfo("Asia/Kolkata")


def at(hour: int, minute: int, second: int = 0) -> int:
    return int(datetime(2026, 11, 2, hour, minute, second, tzinfo=IST).timestamp())


def test_periods_longer_than_15_minutes_count_from_the_open():
    assert period_start(at(9, 20), 1800) == at(9, 15)          # epoch alignment put this in 09:00-09:30
    assert period_start(at(9, 50), 1800) == at(9, 45)
    assert period_start(at(10, 20), 3600) == at(10, 15)
    assert period_start(at(9, 22), 300) == at(9, 20)            # short frames are unchanged
    assert period_start(at(9, 22), 900) == at(9, 15)


def test_thirty_minute_bars_start_at_the_open():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("NSE:NIFTY26NOVFUT")
    for minute in (15, 29, 44, 46):
        book.on_print("NSE:NIFTY26NOVFUT", at(9, minute), 23_000.0, 65, 1)
    bars = book.payload("NSE:NIFTY26NOVFUT", timeframe_seconds=1800)["bars"]
    assert [b["t"] for b in bars] == [at(9, 15), at(9, 45)]
    assert [b["v"] for b in bars] == [195, 65]


def test_initiative_marks_one_sided_periods_and_the_one_still_forming():
    book = FootprintBook(timeframe_seconds=60)
    symbol = "NSE:NIFTY26NOVFUT"
    book.watch(symbol)
    book.on_print(symbol, at(9, 16), 23_000.0, 700, 1)          # 09:15: 70% lifted the offer
    book.on_print(symbol, at(9, 40), 23_010.0, 300, -1)
    book.on_print(symbol, at(9, 50), 23_000.0, 550, 1)          # 09:45: +10%, under the line
    book.on_print(symbol, at(10, 5), 22_990.0, 450, -1)
    book.on_print(symbol, at(10, 20), 22_980.0, 900, -1)        # 10:15: all sold, still forming
    periods = book.payload(symbol)["initiative_30m"]
    assert [p["t"] for p in periods] == [at(9, 15), at(9, 45), at(10, 15)]
    assert [p["label"] for p in periods] == ["IB30", None, "IS30"]
    assert periods[0]["share"] == 0.4 and periods[0]["end"] == at(9, 15) + INITIATIVE_SECONDS
    assert abs(periods[1]["share"]) < INITIATIVE_SHARE
    assert [p["complete"] for p in periods] == [True, True, False]


def _oi_store(path: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE oi_minutes (ts INTEGER, symbol TEXT, oi INTEGER, prev_oi INTEGER, ltp REAL, "
                       "volume INTEGER, PRIMARY KEY (symbol, ts))")
    connection.execute("CREATE TABLE chain_snapshots (ts INTEGER, underlying TEXT, expiry TEXT, strike REAL, "
                       "option_type TEXT, symbol TEXT, ltp REAL, volume INTEGER, oi INTEGER)")
    previous_close = int(datetime(2026, 10, 30, 15, 29, tzinfo=IST).timestamp())
    with connection:
        connection.executemany("INSERT INTO oi_minutes (ts, symbol, oi) VALUES (?, 'NSE:NIFTY26NOVFUT', ?)",
                               [(previous_close, 1_000_000), (at(9, 30), 940_000), (at(9, 44), 950_000),
                                (at(10, 0), 990_000)])
        connection.executemany("INSERT INTO chain_snapshots (ts, underlying, symbol, oi) VALUES (?, 'RELIANCE', ?, ?)",
                               [(at(9, 20), "NSE:RELIANCE26NOV1300CE", 100_000),
                                (at(9, 50), "NSE:RELIANCE26NOV1300CE", 160_000)])


def test_oi_change_per_period_carries_the_overnight_change_into_the_first(tmp_path):
    path = str(tmp_path / "history.sqlite3")
    _oi_store(path)
    periods = [{"t": at(9, 15), "end": at(9, 45), "oi_change": None},
               {"t": at(9, 45), "end": at(10, 15), "oi_change": None},
               {"t": at(10, 15), "end": at(10, 45), "oi_change": None}]
    filled = auction_views.initiative_oi(path, "NSE:NIFTY26NOVFUT", [dict(p) for p in periods])
    assert [p["oi_change"] for p in filled] == [-50_000, 40_000, None]   # 1.00 M -> 0.95 M -> 0.99 M, then nothing
    option = auction_views.initiative_oi(path, "NSE:RELIANCE26NOV1300CE", [dict(p) for p in periods])
    assert [p["oi_change"] for p in option] == [None, 60_000, None]      # first reading has no earlier one
    cash = auction_views.initiative_oi(path, "NSE:RELIANCE-EQ", [dict(p) for p in periods])
    assert all(p["oi_change"] is None for p in cash)
