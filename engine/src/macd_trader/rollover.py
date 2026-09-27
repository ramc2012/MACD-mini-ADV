"""Which contract in a series is the one to trade today.

Two separate things expire and both were pinned to a single series:

*   The ATM option selector took whatever ``expiryData`` Fyers listed first,
    which on an expiry day is the contract expiring in a few hours. On
    25 Aug 2026 that meant 427 of 429 selected contracts died at 15:30 — a
    full day of MACD signals computed on premiums collapsing to intrinsic.
*   ``mp_symbols_csv`` held literal futures tickers (``NSE:NIFTY26AUGFUT``).
    After that series expires the symbol is simply not in the feed any more,
    so the auction desk subscribes to nothing and reports no error.

Both are answered by the same question — of the listed series, which is the
nearest one still far enough from expiry to be worth trading — so both use
:func:`first_tradable` and the same ``min_days`` threshold.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE/BSE futures tickers: root, two-digit year, three-letter month, "FUT".
_FUTURES_TICKER = re.compile(r"^(?P<root>[A-Z0-9&\-]+?)(?P<yy>\d{2})(?P<mon>[A-Z]{3})FUT$")
# The series-free form a config may use so it never needs editing again.
_FUTURES_ROOT = re.compile(r"^(?P<root>[A-Z0-9&\-]+?)-FUT$")


@dataclass(frozen=True, slots=True)
class Expiry:
    """One listed expiry of a series."""

    date: str          # ISO calendar date, e.g. "2026-09-29"
    token: str = ""    # broker-side handle: chain timestamp, or a ticker


def to_date(value: str | None) -> date | None:
    """Parse an ISO-ish expiry, or None when it cannot be trusted."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def days_to_expiry(expiry: str | None, today: date | None = None) -> int | None:
    parsed = to_date(expiry)
    if parsed is None:
        return None
    return (parsed - (today or datetime.now(IST).date())).days


def is_tradable(expiry: str | None, min_days: int = 1, today: date | None = None) -> bool:
    """Is this contract far enough from expiry to open a new position in?

    ``min_days=1`` — the default — rejects a contract expiring today and keeps
    everything else, so the only behaviour that changes is the expiry day
    itself. ``min_days=0`` restores the old "not yet expired" rule.
    """
    remaining = days_to_expiry(expiry, today)
    return remaining is not None and remaining >= min_days


def first_tradable(expiries: list[Expiry], min_days: int = 1, today: date | None = None) -> Expiry | None:
    """The nearest listed expiry that is still far enough out."""
    ordered = sorted((row for row in expiries if to_date(row.date)), key=lambda row: to_date(row.date))
    return next((row for row in ordered if is_tradable(row.date, min_days, today)), None)


def futures_root(symbol: str) -> tuple[str, str] | None:
    """Split a futures symbol into (exchange, root), series or not.

        NSE:NIFTY26AUGFUT -> ("NSE", "NIFTY")
        NSE:NIFTY-FUT     -> ("NSE", "NIFTY")
        NSE:ICICIBANK-EQ  -> None
    """
    exchange, _, body = symbol.partition(":")
    if not body:
        return None
    match = _FUTURES_TICKER.match(body) or _FUTURES_ROOT.match(body)
    return (exchange, match.group("root")) if match else None


def is_futures_symbol(symbol: str) -> bool:
    return futures_root(symbol) is not None
