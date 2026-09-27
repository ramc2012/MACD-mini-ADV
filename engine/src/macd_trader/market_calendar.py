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
Special sessions on non-trading days (Muhurat trading, 8 Nov 2026) are not
modelled; the desk simply does not trade them.
"""

from __future__ import annotations

from datetime import date, timedelta

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

_extra: dict[date, str] = {}


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
    return day.weekday() < 5 and day not in holidays()


def next_trading_day(day: date) -> date:
    """The first trading day strictly after ``day``."""
    candidate = day + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def previous_trading_day(day: date) -> date:
    """The last trading day strictly before ``day``."""
    candidate = day - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def covers_year(year: int) -> bool:
    return any(day.year == year for day in holidays())


def holiday_list() -> list[str]:
    """ISO dates, for the terminal's own closed-book arithmetic."""
    return sorted(day.isoformat() for day in holidays())


def status(today: date) -> dict:
    return {
        "trading_day": is_trading_day(today),
        "holiday": holiday_name(today),
        "next_trading_day": next_trading_day(today).isoformat(),
        "calendar_covers_year": covers_year(today.year),
        "configured_extra_holidays": sorted(day.isoformat() for day in _extra),
    }
