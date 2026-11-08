"""Each instrument's own continuous session, per the regime in force.

One 09:15-15:30 clock gated everything, and it was right for neither segment
after 3 Aug 2026 (regime ``2026-08-session``):

* F&O trades continuously to 15:40. On 29 Sep 2026 the ten minutes after
  15:30 carried 107k option ticks and 3-6% of the index futures' and ATM
  options' daily volume, and every bar, profile and lane dropped them.
* Cash equities trade continuously to 15:15, then run a closing call auction
  to 15:35. The auction's match arrives as ONE uncrossing print -- 9.5M
  HDFCBANK shares at 15:29:23, 23% of the day -- which the 15:30 gate let
  into the profile as its VPOC and into CVD as a single aggressive buy. An
  auction has no aggressor; it is not order flow.

Index values are published through 15:30 and carry no volume; they keep it.
Everything that asks "is this print part of the session" asks here, by the
tick's own exchange time, so a lane, a chart and the condenser agree.
"""
from __future__ import annotations

from datetime import date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo

from . import market_calendar
from .regimes import regime_for

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
INDEX_CLOSE = time(15, 30)


def segment(symbol: str) -> str:
    """"fo" (futures and options), "cash" (equities) or "index"."""
    body = symbol.split(":", 1)[-1].upper()
    if body.endswith("-INDEX"):
        return "index"
    if body.endswith("-EQ") or body.endswith("-BE"):
        return "cash"
    return "fo"


def _clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


@lru_cache(maxsize=4096)
def _close_for(kind: str, day: date) -> time:
    special = market_calendar.special_close(day)
    if special is not None:                       # a special session closes every segment at its own time
        return special
    regime = regime_for(day)
    if kind == "fo":
        return _clock(regime.session_end)
    if kind == "cash":
        return _clock(regime.cash_continuous_end)
    return INDEX_CLOSE


def session_close(symbol: str, day: date | str) -> time:
    """When ``symbol``'s continuous trading ends on ``day`` (IST)."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    return _close_for(segment(symbol), day)


def market_close(day: date | str) -> time:
    """The last close of any segment: when the whole market is done."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    return max(_close_for(kind, day) for kind in ("fo", "cash", "index"))


def in_session(symbol: str, moment: datetime) -> bool:
    """Is ``moment`` inside ``symbol``'s continuous session (trading days only)?"""
    local = moment.astimezone(IST)
    if not market_calendar.is_trading_day(local.date()):
        return False
    return session_open(local.date()) <= local.time().replace(tzinfo=None) < session_close(symbol, local.date())


def session_open(day: date | str) -> time:
    """When continuous trading starts on ``day``: 09:15, or a special session's (Muhurat) open."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    return market_calendar.session_open_time(day)


def market_open(moment: datetime | None = None) -> bool:
    """Is any segment still in its continuous session?"""
    local = (moment or datetime.now(IST)).astimezone(IST)
    if not market_calendar.is_trading_day(local.date()):
        return False
    return session_open(local.date()) <= local.time().replace(tzinfo=None) < market_close(local.date())


def closing_auction(symbol: str, day: date | str) -> tuple[time, time] | None:
    """The cash closing-auction window for ``symbol`` on ``day``, if it has one."""
    if segment(symbol) != "cash":
        return None
    day = date.fromisoformat(day) if isinstance(day, str) else day
    regime = regime_for(day)
    if not regime.closing_auction_end or market_calendar.special_session(day):
        return None
    return _clock(regime.cash_continuous_end), _clock(regime.closing_auction_end)


def in_closing_auction(symbol: str, moment: datetime) -> bool:
    local = moment.astimezone(IST)
    window = closing_auction(symbol, local.date())
    return bool(window) and window[0] <= local.time().replace(tzinfo=None) < window[1]


def session_bounds(symbol: str | None, day: str) -> tuple[int, int]:
    """Epoch seconds [open, close) of ``symbol``'s session; the whole market when None."""
    when = date.fromisoformat(day)
    close = session_close(symbol, when) if symbol else market_close(when)
    opened = datetime.combine(when, session_open(when), IST)
    return int(opened.timestamp()), int(datetime.combine(when, close, IST).timestamp())
