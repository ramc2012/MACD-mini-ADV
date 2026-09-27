"""Session gates follow the NSE trading calendar, not the weekday.

14 Sep 2026 (Ganesh Chaturthi, a Monday) is the case that exposed it: the
engine treated the holiday as an open session, and the next morning treated
it as a missed one.
"""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from macd_trader import market_calendar
from macd_trader.alerts import market_hours
from macd_trader.config import Settings
from macd_trader.engine import TradingEngine, fo_session_open, preopen_window, regular_session_open
from macd_trader.models import Candle
from macd_trader.portfolio import closed_visible_until

IST = ZoneInfo("Asia/Kolkata")
FRIDAY, HOLIDAY, TUESDAY = date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 15)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


@pytest.fixture(autouse=True)
def clean_calendar():
    market_calendar.configure("")
    yield
    market_calendar.configure("")


class TestCalendar:
    def test_the_published_2026_list_has_sixteen_holidays(self):
        assert len(market_calendar.NSE_TRADING_HOLIDAYS) == 16
        assert all(day.year == 2026 and day.weekday() < 5 for day in market_calendar.NSE_TRADING_HOLIDAYS)

    def test_the_holidays_the_stored_candles_confirm(self):
        # The archive has no bars on exactly these weekdays since it began.
        for day in (date(2026, 5, 28), date(2026, 6, 26), HOLIDAY):
            assert not market_calendar.is_trading_day(day)

    def test_weekends_holidays_and_ordinary_days(self):
        assert market_calendar.is_trading_day(FRIDAY)
        assert not market_calendar.is_trading_day(date(2026, 9, 12))
        assert not market_calendar.is_trading_day(HOLIDAY)
        assert market_calendar.is_trading_day(TUESDAY)
        assert market_calendar.holiday_name(HOLIDAY) == "Ganesh Chaturthi"

    def test_next_and_previous_skip_the_holiday(self):
        assert market_calendar.next_trading_day(FRIDAY) == TUESDAY
        assert market_calendar.previous_trading_day(TUESDAY) == FRIDAY

    def test_extra_holidays_are_configurable_and_bad_tokens_reported(self):
        rejected = market_calendar.configure("2027-01-26, not-a-date ;2026-09-16")
        assert rejected == ["not-a-date"]
        assert not market_calendar.is_trading_day(date(2026, 9, 16))
        assert market_calendar.covers_year(2027)

    def test_an_unmaintained_year_is_visible(self):
        assert market_calendar.covers_year(2026)
        assert not market_calendar.covers_year(2027)
        assert market_calendar.status(date(2027, 1, 4))["calendar_covers_year"] is False


class TestSessionGates:
    def test_no_session_on_the_holiday(self):
        assert not regular_session_open(at(HOLIDAY, 10))
        assert not fo_session_open(at(HOLIDAY, 15, 35))
        assert not preopen_window(at(HOLIDAY, 9, 5))
        assert not market_hours(at(HOLIDAY, 10))

    def test_a_session_the_next_morning(self):
        assert regular_session_open(at(TUESDAY, 10))
        assert fo_session_open(at(TUESDAY, 15, 35))
        assert preopen_window(at(TUESDAY, 9, 5))
        assert market_hours(at(TUESDAY, 10))

    def test_the_auction_desk_does_not_see_a_session_either(self):
        from macd_trader import mp_engine
        assert not mp_engine.is_trading_day(HOLIDAY)


class TestHistoryStaleness:
    def _engine(self):
        engine = object.__new__(TradingEngine)
        return engine

    @staticmethod
    def bar(day: date) -> Candle:
        moment = at(day, 15, 29).astimezone(UTC)
        return Candle("NSE:SBIN-EQ", int(moment.timestamp()), 1, 1, 1, 1, 1, True)

    def test_a_holiday_is_not_a_missed_session(self):
        assert TradingEngine.stored_history_is_stale(self._engine(), [self.bar(FRIDAY)], today=TUESDAY) is False

    def test_a_real_missed_session_still_is(self):
        assert TradingEngine.stored_history_is_stale(self._engine(), [self.bar(date(2026, 9, 10))], today=TUESDAY) is True

    def test_the_morning_after_a_weekend_is_current(self):
        assert TradingEngine.stored_history_is_stale(self._engine(), [self.bar(FRIDAY)], today=date(2026, 9, 14) + timedelta(days=0)) is False


class TestClosedBook:
    def test_a_friday_exit_survives_a_monday_holiday(self):
        until = closed_visible_until(at(FRIDAY, 15, 20))
        assert until == datetime(2026, 9, 15, 8, 0, tzinfo=IST)

    def test_an_ordinary_friday_still_rolls_on_monday(self):
        until = closed_visible_until(at(date(2026, 9, 4), 15, 20))
        assert until == datetime(2026, 9, 7, 8, 0, tzinfo=IST)


def test_health_and_snapshot_expose_the_calendar(tmp_path, monkeypatch):
    settings = Settings(
        feed_mode="simulation", symbols_csv="NSE:SBIN-EQ", tick_capture_enabled=False,
        database_path=str(tmp_path / "macd.sqlite3"), mp_database_path=str(tmp_path / "mp.sqlite3"),
        blast_database_path=str(tmp_path / "blast.sqlite3"), tick_database_path=str(tmp_path / "ticks.sqlite3"),
        contract_snapshot_path=str(tmp_path / "contracts.json"), market_holidays_csv="2026-12-31,bogus",
    )
    engine = TradingEngine(settings)
    assert engine.calendar_rejected == ["bogus"]
    assert "2026-09-14" in engine.snapshot()["config"]["market_holidays"]
    assert "2026-12-31" in engine.snapshot()["config"]["market_holidays"]
    import asyncio
    asyncio.run(engine.blast.stop())
