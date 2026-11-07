"""Sector coil: each index's return from a base close, sampled at a run of checkpoints.

The view follows Barron's "The Big Reversal" chart (owner, 7 Nov 2026): a sector's line starts at zero on the base
date, runs to its year-to-date return one month ago, turns, and runs back to where it is now. Here the line turns at
every checkpoint (month ends, fortnights or weeks), so one continuous coil shows how the sector got where it is.

Returns come from ``daily_closes`` (sector_wheel.py), so the coil and the Sector wheel share one store and one
once-a-session refresh. During the session the live price stands in for today's close. "relative" is the return
against NIFTY 50 over the same span: (1 + own) / (1 + NIFTY) - 1.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import market_calendar
from .sector_wheel import BENCHMARK, BROAD, _series, label, last_completed_session

IST = ZoneInfo("Asia/Kolkata")
BASES = {"YTD", "1Y", "6M", "3M", "1M"}
STEPS = {"M", "2W", "W", "TEMPLATE"}          # month ends, every 10 sessions, every 5 sessions, one month ago only
MEASURES = {"own", "relative"}
STEP_SESSIONS = {"2W": 10, "W": 5}


def _months_back(day: date, months: int) -> date:
    month = day.month - months
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise ValueError(day)


def base_day(dates: list[str], today: date, base: str) -> str | None:
    """The last session on or before the base date: 31 Dec for YTD, else the same calendar day N months back."""
    if base == "YTD":
        cutoff = date(today.year - 1, 12, 31)
    elif base == "1Y":
        cutoff = _months_back(today, 12)
    else:
        cutoff = _months_back(today, int(base[:-1]))
    before = [d for d in dates if d <= cutoff.isoformat()]
    return before[-1] if before else None


def checkpoints(dates: list[str], start: str, today: date, step: str) -> list[str]:
    """Sessions after ``start`` where the coil turns, ending with the latest session."""
    after = [d for d in dates if d > start]
    if not after:
        return []
    if step == "M":
        ends = [d for i, d in enumerate(after) if i + 1 < len(after) and after[i + 1][:7] != d[:7]]
        points = ends + [after[-1]]
    elif step == "TEMPLATE":
        month_ago = _months_back(today, 1).isoformat()
        earlier = [d for d in after[:-1] if d <= month_ago]
        points = ([earlier[-1]] if earlier else []) + [after[-1]]
    else:
        n = STEP_SESSIONS[step]
        points = after[::-1][::n][::-1]           # count back from today so the last step is a full one
    return list(dict.fromkeys(points))


def build(database_path: str, symbols: list[str], live: dict[str, float], now: datetime | None = None,
          base: str = "YTD", step: str = "M", measure: str = "own") -> dict:
    now = (now or datetime.now(IST)).astimezone(IST)
    in_session = market_calendar.is_trading_day(now.date()) and time(9, 15) <= now.time() < time(15, 30)
    today = now.date() if in_session else last_completed_session(now)
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
        try:
            series = {s: _series(db, s, today, live.get(s) if in_session else None)
                      for s in dict.fromkeys([BENCHMARK, *symbols])}
        except sqlite3.OperationalError:          # table not created yet: first refresh still running
            series = {}
    bench = series.get(BENCHMARK) or []
    dates = [d for d, _ in bench]
    start = base_day(dates, today, base) if dates else None
    points = checkpoints(dates, start, today, step) if start else []
    empty = {"as_of": today.isoformat(), "live": in_session, "base": base, "base_day": start, "step": step,
             "measure": measure, "benchmark": BENCHMARK, "points": [], "rows": [], "missing": []}
    if not points:
        return empty

    def at(values: dict[str, float], day: str) -> float | None:
        """Close on ``day``, else the last one in the 5 sessions before it (a missing day must not break the coil)."""
        if day in values:
            return values[day]
        i = dates.index(day)
        return next((values[d] for d in reversed(dates[max(0, i - 5):i]) if d in values), None)

    days = [start, *points]
    closes = {s: dict(v) for s, v in series.items()}
    bench_close = closes[BENCHMARK]
    bench_ret = [bench_close[d] / bench_close[start] - 1 for d in days]
    rows, missing = [], []
    for symbol, c in closes.items():
        first = at(c, start)
        if first is None:
            missing.append(symbol)
            continue
        values = []
        for i, d in enumerate(days):
            price = at(c, d)
            own = None if price is None else price / first - 1
            if measure == "relative":
                value = None if own is None or bench_ret[i] is None else (1 + own) / (1 + bench_ret[i]) - 1
            else:
                value = own
            values.append(None if value is None else round(value * 100, 2))
        if values[-1] is None:                    # no close for the latest session: leave it out, say so
            missing.append(symbol)
            continue
        kind = "benchmark" if symbol == BENCHMARK else "broad" if symbol in BROAD else "sector"
        rows.append({"symbol": symbol, "label": label(symbol), "kind": kind, "values": values,
                     "price": at(c, days[-1])})
    return {**empty, "points": points, "rows": rows, "missing": missing}
