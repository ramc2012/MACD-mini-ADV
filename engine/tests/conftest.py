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
