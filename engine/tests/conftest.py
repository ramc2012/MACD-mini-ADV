"""Shared test clocks for paths that require a live exchange session."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


_IST = ZoneInfo("Asia/Kolkata")
_SESSION_MOMENT = datetime(2026, 9, 28, 11, 0, tzinfo=_IST)


class _TradingSessionDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        moment = _SESSION_MOMENT.astimezone(tz) if tz else _SESSION_MOMENT.replace(tzinfo=None)
        return cls.fromtimestamp(moment.timestamp(), tz) if tz else cls(
            moment.year, moment.month, moment.day,
            moment.hour, moment.minute, moment.second, moment.microsecond)


@pytest.fixture
def fixed_auction_session_clock(monkeypatch, request):
    """Run auction tick fixtures on a known NSE trading day, even on weekends."""
    from macd_trader import models, mp_engine

    monkeypatch.setattr(mp_engine, "datetime", _TradingSessionDateTime)
    monkeypatch.setattr(models, "datetime", _TradingSessionDateTime)
    monkeypatch.setattr(request.module, "datetime", _TradingSessionDateTime)


@pytest.fixture
def no_stock_t2(monkeypatch):
    """Switch off the T-2 stock-option rule (rollover.py) for tests of the expiry-DAY mechanics on stock fixtures."""
    from macd_trader import desk_app, engine, rollover
    monkeypatch.setattr(rollover, "STOCK_EXIT_SESSIONS", 0)
    for module in (engine, desk_app):
        monkeypatch.setattr(module, "set_stock_exit_sessions", lambda sessions: None)
