"""Profit booking on extreme index moves (index options in the MACD and smart lanes)."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from macd_trader.extreme_booking import CALL_ATR, PUT_ATR, ExtremeBooker, option_root

IST = ZoneInfo("Asia/Kolkata")
PUT = "NSE:NIFTY26O1922450PE"
CALL = "NSE:NIFTY26O1922450CE"
STOCK = "NSE:SBIN26OCT960PE"


class Book:
    """The parts of ExecutionManager the booker uses."""

    def __init__(self, lane: str, positions: dict, fresh: dict):
        self.portfolio = SimpleNamespace(lane=lane, positions=positions)
        self.settings = SimpleNamespace(slippage_bps=5.0)
        self.orders: dict = {}
        self._armed_exits: set = set()
        self.exit_reasons: dict = {}
        self.fresh = fresh
        self.sold: list = []

    def _fresh_price(self, symbol):
        if symbol not in self.fresh:
            raise RuntimeError("stale")
        return self.fresh[symbol]

    def arm_exit(self, symbol, reason):
        self.exit_reasons[symbol] = reason
        self._armed_exits.add(symbol)

    async def submit(self, symbol, side, quantity):
        self.sold.append((symbol, side, quantity, self.exit_reasons[symbol]))


def _pos(avg, last, qty=75):
    return SimpleNamespace(average_price=avg, last_price=last, quantity=qty)


def _booker():
    b = ExtremeBooker(":memory:")
    b.levels["NIFTY"] = {"day": "2026-10-14", "prev_close": 22000.0, "atr": 200.0}
    return b


def test_option_root_reads_index_options_only():
    assert option_root(PUT) == ("NIFTY", -1) and option_root(CALL) == ("NIFTY", 1)
    assert option_root(STOCK) == (None, 0)


def test_put_is_booked_once_the_index_is_one_atr_down():
    b = _booker()
    book = Book("macd", {PUT: _pos(100, 160), STOCK: _pos(10, 20)}, {PUT: 160.0, STOCK: 20.0})
    now = datetime(2026, 10, 14, 11, 0, tzinfo=IST)
    assert asyncio.run(b.on_index_tick("NSE:NIFTY50-INDEX", 22000 - 0.9 * 200, [book], now)) == []
    done = asyncio.run(b.on_index_tick("NSE:NIFTY50-INDEX", 22000 - PUT_ATR * 200, [book], now))
    assert [d["symbol"] for d in done] == [PUT]
    assert book.sold == [(PUT, "SELL", 75, "EXTREME_BOOK_PUT_INDEX_1ATR_DOWN")]


def test_call_is_booked_only_at_the_end_of_an_extreme_up_day():
    b = _booker()
    book = Book("smart_50l", {CALL: _pos(100, 180)}, {CALL: 180.0})
    up = 22000 + CALL_ATR * 200
    assert asyncio.run(b.on_index_tick("NSE:NIFTY50-INDEX", up, [book], datetime(2026, 10, 14, 13, 0, tzinfo=IST))) == []
    done = asyncio.run(b.on_index_tick("NSE:NIFTY50-INDEX", up, [book], datetime(2026, 10, 14, 15, 16, tzinfo=IST)))
    assert done and book.sold[0][3] == "EXTREME_BOOK_CALL_INDEX_1.5ATR_UP_EOD"


def test_a_losing_position_is_left_to_the_lane_stops():
    b = _booker()
    book = Book("macd", {PUT: _pos(100, 90)}, {PUT: 90.0})
    asyncio.run(b.on_index_tick("NSE:NIFTY50-INDEX", 21700, [book], datetime(2026, 10, 14, 11, 0, tzinfo=IST)))
    assert book.sold == [] and not b.pending


def test_a_stale_option_quote_waits_for_the_option_s_next_print():
    b = _booker()
    book = Book("macd", {PUT: _pos(100, 150)}, {})
    now = datetime(2026, 10, 14, 11, 0, tzinfo=IST)
    assert asyncio.run(b.on_index_tick("NSE:NIFTY50-INDEX", 21750, [book], now)) == []
    assert ("macd", PUT) in b.pending and book.sold == []
    book.fresh[PUT] = 152.0
    done = asyncio.run(b.on_option_tick(PUT, [book], now + timedelta(seconds=5)))
    assert done and book.sold and not b.pending


def test_levels_come_from_the_previous_completed_sessions(tmp_path):
    path = tmp_path / "h.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER, open REAL, high REAL, low REAL, close REAL)")
    days = [date(2026, 9, 14) + timedelta(days=k) for k in range(30)]
    days = [d for d in days if d.weekday() < 5][:16]
    rows = []
    for k, d in enumerate(days):
        start = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())
        for m in range(375):
            p = 22000 + k * 10
            rows.append(("NSE:NIFTY50-INDEX", 60, start + 60 * m, p, p + 50, p - 50, p))
    db.executemany("INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?)", rows)
    db.commit(); db.close()
    b = ExtremeBooker(str(path))
    levels = b.levels_for("NIFTY", datetime(2026, 10, 8, 10, 0, tzinfo=IST))
    assert levels["prev_close"] == 22000 + 15 * 10
    assert abs(levels["atr"] - 100.0) < 1e-6           # high-low 100 dominates a 10-point drift
