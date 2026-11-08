"""The NSE trading calendar: which weekdays are not sessions.

Every session gate in this app used to be ``weekday() < 5``. On an exchange
holiday that made the engine believe the market was open: on 14 Sep 2026
(Ganesh Chaturthi) the feed watchdog tried to recover a socket that had
nothing to deliver, the option-chain collector ran all day and wrote whale
windows dated to the holiday from Friday's last snapshot, the closed book
rolled off at 08:00 as though a session were starting, and the next morning
the warm-up read the holiday as a MISSED session and was about to request
broker history for every symbol to repair a gap that never existed.

The built-in list is the exchange's published 2026 trading-holiday list for
the equity and equity-derivatives segments. It must be extended every year:
``market_holidays_csv`` adds dates without a release, and health reports
``calendar_covers_year: false`` once the current year has no entries, so an
unmaintained calendar is visible rather than silently weekday-only again.
Special sessions (Muhurat trading) are trading days with their own hours:
SPECIAL_SESSIONS gives each one's continuous-session open and its last close
(F&O to 15:40; SPECIAL_SEGMENT_CLOSES holds cash 15:30 and index 15:30), and
sessions.py / candles.py gate prints by them, so every lane trades them.

For everything that COUNTS sessions a special session is an extension of the
regular session before it (owner, 8 Nov 2026): next_trading_day /
previous_trading_day step over it, so Monday's previous session is Friday,
holding periods count Friday + Muhurat once, and a Muhurat trade settles with
Friday's (settlement_day, T+1 on the regular calendar). logical_day maps the
special date onto that Friday. is_trading_day stays True for it, because that
gates the feed, bars and entries.
"""

from __future__ import annotations

from datetime import date, time, timedelta

# NSE trading holidays, Equity and Equity Derivatives segments, calendar 2026.
NSE_TRADING_HOLIDAYS: dict[date, str] = {
    date(2026, 1, 15): "Municipal Corporation Election - Maharashtra",
    date(2026, 1, 26): "Republic Day",
    date(2026, 3, 3): "Holi",
    date(2026, 3, 26): "Shri Ram Navami",
    date(2026, 3, 31): "Shri Mahavir Jayanti",
    date(2026, 4, 3): "Good Friday",
    date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
    date(2026, 5, 1): "Maharashtra Day",
    date(2026, 5, 28): "Bakri Id",
    date(2026, 6, 26): "Muharram",
    date(2026, 9, 14): "Ganesh Chaturthi",
    date(2026, 10, 2): "Mahatma Gandhi Jayanti",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 10): "Diwali Balipratipada",
    date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev",
    date(2026, 12, 25): "Christmas",
}

# Special sessions on otherwise closed days: (continuous open, last segment close, name), IST.
# 8 Nov 2026: NSE circular NSE/CMTR/76941 (19 Oct 2026) -- pre-open 14:05, equity normal market 14:20-15:30,
# equity derivatives 14:20-15:40.
SPECIAL_SESSIONS: dict[date, tuple[time, time, str]] = {
    date(2026, 11, 8): (time(14, 20), time(15, 40), "Muhurat trading"),
}
SPECIAL_SEGMENT_CLOSES: dict[date, dict[str, time]] = {
    date(2026, 11, 8): {"fo": time(15, 40), "cash": time(15, 30), "index": time(15, 30)},
}
REGULAR_OPEN = time(9, 15)

_extra: dict[date, str] = {}


def special_session(day: date) -> tuple[time, time, str] | None:
    return SPECIAL_SESSIONS.get(day)


def session_open_time(day: date) -> time:
    """The continuous session's open on ``day``: 09:15, or a special session's own open."""
    special = SPECIAL_SESSIONS.get(day)
    return special[0] if special else REGULAR_OPEN


def special_close(day: date, kind: str | None = None) -> time | None:
    """A special session's close for one segment ("fo", "cash", "index"), or its last close when ``kind`` is None."""
    special = SPECIAL_SESSIONS.get(day)
    if not special:
        return None
    if kind is None:
        return special[1]
    return SPECIAL_SEGMENT_CLOSES.get(day, {}).get(kind, special[1])


def configure(extra_csv: str | None) -> list[str]:
    """Add holidays from ``YYYY-MM-DD`` tokens. Returns the tokens it could not read."""
    _extra.clear()
    rejected: list[str] = []
    for token in (extra_csv or "").replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            _extra[date.fromisoformat(token)] = "configured holiday"
        except ValueError:
            rejected.append(token)
    return rejected


def holidays() -> dict[date, str]:
    return {**NSE_TRADING_HOLIDAYS, **_extra}


def holiday_name(day: date) -> str | None:
    return holidays().get(day)


def is_trading_day(day: date) -> bool:
    if day in SPECIAL_SESSIONS:
        return True
    return day.weekday() < 5 and day not in holidays()


def is_regular_trading_day(day: date) -> bool:
    """A regular session: the unit every session count, hold and settlement uses (special sessions extend one)."""
    return day.weekday() < 5 and day not in holidays()


def logical_day(day: date) -> date:
    """The regular session ``day`` belongs to: a special session counts as part of the regular session before it."""
    if day in SPECIAL_SESSIONS and not is_regular_trading_day(day):
        return previous_trading_day(day)
    return day


def next_trading_day(day: date) -> date:
    """The first regular trading day strictly after ``day`` (special sessions are not counted)."""
    candidate = day + timedelta(days=1)
    while not is_regular_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def previous_trading_day(day: date) -> date:
    """The last regular trading day strictly before ``day`` (special sessions are not counted)."""
    candidate = day - timedelta(days=1)
    while not is_regular_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def settlement_day(trade_day: date) -> date:
    """T+1 settlement on the regular calendar; a special session settles with the session it extends."""
    return next_trading_day(logical_day(trade_day))


def covers_year(year: int) -> bool:
    return any(day.year == year for day in holidays())


def holiday_list() -> list[str]:
    """ISO dates, for the terminal's own closed-book arithmetic."""
    return sorted(day.isoformat() for day in holidays())


def status(today: date) -> dict:
    return {
        "trading_day": is_trading_day(today),
        "holiday": None if today in SPECIAL_SESSIONS else holiday_name(today),
        "special_session": ({"name": SPECIAL_SESSIONS[today][2], "open": SPECIAL_SESSIONS[today][0].strftime("%H:%M"),
                             "close": SPECIAL_SESSIONS[today][1].strftime("%H:%M"),
                             "closes": {k: v.strftime("%H:%M") for k, v in SPECIAL_SEGMENT_CLOSES.get(today, {}).items()},
                             "extends": logical_day(today).isoformat()} if today in SPECIAL_SESSIONS else None),
        "logical_day": logical_day(today).isoformat(),
        "settlement_day": settlement_day(today).isoformat(),
        "next_trading_day": next_trading_day(today).isoformat(),
        "calendar_covers_year": covers_year(today.year),
        "configured_extra_holidays": sorted(day.isoformat() for day in _extra),
    }
