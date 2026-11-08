"""Special sessions (Muhurat trading, 8 Nov 2026): a trading day with its own hours."""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from macd_trader import market_calendar, sessions
from macd_trader.candles import session_bucket

IST = ZoneInfo("Asia/Kolkata")
MUHURAT = date(2026, 11, 8)


def at(h, m):
    return datetime(2026, 11, 8, h, m, tzinfo=IST)


def test_muhurat_is_a_trading_day_with_its_own_hours():
    assert market_calendar.is_trading_day(MUHURAT)
    special = market_calendar.status(MUHURAT)["special_session"]
    assert (special["name"], special["open"], special["close"]) == ("Muhurat trading", "14:20", "15:40")
    assert special["closes"] == {"fo": "15:40", "cash": "15:30", "index": "15:30"}
    assert market_calendar.next_trading_day(MUHURAT) == date(2026, 11, 9)
    assert not market_calendar.is_trading_day(date(2026, 11, 10))      # Diwali Balipratipada stays a holiday


def test_muhurat_extends_fridays_session_for_counting_and_settlement():
    friday, monday = date(2026, 11, 6), date(2026, 11, 9)
    assert market_calendar.logical_day(MUHURAT) == friday
    assert market_calendar.next_trading_day(friday) == monday           # Muhurat is not a session of its own
    assert market_calendar.previous_trading_day(monday) == friday
    assert market_calendar.settlement_day(MUHURAT) == market_calendar.settlement_day(friday) == monday
    assert market_calendar.logical_day(monday) == monday
    from macd_trader.agentic_lane import sessions_held
    assert sessions_held("2026-11-06T10:00:00+05:30", at(15, 0)) == 1
    assert sessions_held("2026-11-08T14:30:00+05:30", datetime(2026, 11, 9, 10, 0, tzinfo=IST)) == 2
    from macd_trader.smart_lane import hold_until
    assert hold_until(at(14, 30), 1).date() == monday
    assert hold_until(datetime(2026, 11, 6, 10, 0, tzinfo=IST), 1).date() == monday


def test_sessions_gate_prints_by_the_special_hours():
    for symbol in ("NSE:NIFTY26N1024750CE", "NSE:SBIN-EQ", "NSE:NIFTY50-INDEX"):
        assert not sessions.in_session(symbol, at(13, 0))
        assert not sessions.in_session(symbol, at(14, 19))
        assert sessions.in_session(symbol, at(14, 20))
        assert sessions.in_session(symbol, at(15, 29))
    for symbol in ("NSE:SBIN-EQ", "NSE:NIFTY50-INDEX"):
        assert not sessions.in_session(symbol, at(15, 30))
    assert sessions.in_session("NSE:NIFTY26N1024750CE", at(15, 39))       # equity derivatives to 15:40
    assert not sessions.in_session("NSE:NIFTY26N1024750CE", at(15, 40))
    assert sessions.market_close(MUHURAT) == time(15, 40)
    assert not sessions.market_open(at(10, 0)) and sessions.market_open(at(14, 30)) and sessions.market_open(at(15, 35))
    assert sessions.closing_auction("NSE:SBIN-EQ", MUHURAT) is None


def test_candles_open_at_the_special_open_and_keep_the_0915_grid():
    assert session_bucket(int(at(14, 10).timestamp()), 60) is None
    assert session_bucket(int(at(14, 21).timestamp()), 60) == int(at(14, 21).timestamp())
    assert session_bucket(int(at(14, 21).timestamp()), 1800) == int(at(14, 15).timestamp())


def test_regular_days_unchanged():
    monday = datetime(2026, 11, 9, 9, 15, tzinfo=IST)
    assert sessions.in_session("NSE:NIFTY26N1024750CE", monday)
    assert sessions.session_close("NSE:NIFTY26N1024750CE", date(2026, 11, 9)) == time(15, 40)
