"""Populate the session/weekly/monthly profile tiers.

Used by the engine after each close and by ``research/profile_backfill.py`` to
rebuild history. Both go through here so a backfilled row and a live one are
produced by identical code.

**Underlyings only, by default.** A positional profile compares this week's
value to last week's. An option contract that did not exist last week has
nothing to compare against, and the contract that replaced it is a different
instrument wearing a similar name, so rolling option series are excluded unless
a caller asks for them explicitly. Spots, indices and futures carry across
sessions and are what the positional lane actually reads.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .profile_history import (
    build_period, build_session, ensure_tables, period_key, save_periods, save_sessions,
    session_bounds, value_migration,
)

IST = ZoneInfo("Asia/Kolkata")
PERIODS = ("week", "month")


def is_option(symbol: str) -> bool:
    return symbol.endswith("CE") or symbol.endswith("PE")


def _connect(path: str, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    else:
        connection = sqlite3.connect(path, timeout=60)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
    return connection


def candidate_symbols(ticks: sqlite3.Connection | None, history: sqlite3.Connection | None,
                      days: list[str], include_options: bool = False) -> list[str]:
    """Symbols with data in the requested window, underlyings unless asked."""
    found: set[str] = set()
    if ticks is not None and days:
        rows = ticks.execute(
            f"""SELECT DISTINCT s.symbol FROM tick_session_ladder l
                JOIN tick_symbols s ON s.id = l.symbol_id
                WHERE l.day IN ({",".join("?" for _ in days)})""",
            days,
        )
        found.update(row[0] for row in rows)
    if history is not None and days:
        start, _ = session_bounds(min(days))
        _, end = session_bounds(max(days))
        rows = history.execute(
            """SELECT DISTINCT symbol FROM historical_candles
               WHERE timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?
                 AND asset_type <> 'option'""",
            (start, end),
        )
        found.update(row[0] for row in rows)
    if not include_options:
        found = {symbol for symbol in found if not is_option(symbol)}
    return sorted(found)


def available_days(ticks: sqlite3.Connection | None, history: sqlite3.Connection | None,
                   start: str = "", end: str = "") -> list[str]:
    days: set[str] = set()
    if ticks is not None:
        days.update(row[0] for row in ticks.execute("SELECT DISTINCT day FROM tick_session_ladder"))
    if history is not None:
        days.update(row[0] for row in history.execute(
            """SELECT DISTINCT date(timestamp,'unixepoch','+5 hours','+30 minutes')
               FROM historical_candles WHERE timeframe_seconds = 60"""))
    usable = []
    for day in sorted(d for d in days if d):
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            continue
        # Weekend rows are archive noise: the exchange was shut.
        if parsed.weekday() >= 5:
            continue
        if (start and day < start) or (end and day > end):
            continue
        usable.append(day)
    return usable


def build_sessions(ticks_path: str | None, history_path: str | None, out_path: str,
                   days: list[str], symbols: list[str] | None = None,
                   include_options: bool = False, progress=None) -> dict:
    """Build and store one session row per (symbol, day) that has data."""
    ticks = _connect(ticks_path, read_only=True) if ticks_path else None
    history = _connect(history_path, read_only=True) if history_path else None
    out = _connect(out_path)
    try:
        ensure_tables(out)
        targets = symbols or candidate_symbols(ticks, history, days, include_options)
        written = 0
        skipped = 0
        for day in days:
            profiles = []
            migrations: dict[str, str] = {}
            for symbol in targets:
                profile = build_session(ticks, history, symbol, day)
                if profile is None or profile.poc is None:
                    skipped += 1
                    continue
                prior = out.execute(
                    """SELECT vah, val FROM session_profiles
                       WHERE symbol = ? AND day < ? ORDER BY day DESC LIMIT 1""",
                    (symbol, day),
                ).fetchone()
                migrations[f"{symbol}:{day}"] = value_migration(
                    (profile.vah, profile.val), (prior[0], prior[1]) if prior else (None, None))
                profiles.append(profile)
            written += save_sessions(out, profiles, migrations)
            if progress:
                progress(day, len(profiles))
        return {"days": len(days), "symbols": len(targets), "sessions": written, "skipped": skipped}
    finally:
        for connection in (ticks, history, out):
            if connection is not None:
                connection.close()


def build_periods(ticks_path: str | None, out_path: str, days: list[str],
                  symbols: list[str] | None = None,
                  history_path: str | None = None) -> dict:
    """Refresh every weekly and monthly composite touched by ``days``."""
    ticks = _connect(ticks_path, read_only=True) if ticks_path else None
    history = _connect(history_path, read_only=True) if history_path else None
    out = _connect(out_path)
    try:
        ensure_tables(out)
        if symbols is None:
            symbols = [row[0] for row in out.execute(
                f"""SELECT DISTINCT symbol FROM session_profiles
                    WHERE day IN ({",".join("?" for _ in days)})""", days)]
        written = 0
        for period in PERIODS:
            buckets: dict[str, set[str]] = {}
            for day in days:
                buckets.setdefault(period_key(day, period), set()).add(day)
            for start in buckets:
                rows = []
                for symbol in symbols:
                    # Every stored session in the bucket, not only the days
                    # this run touched: a Wednesday rebuild must still produce
                    # the week's Monday-to-Wednesday composite.
                    member_days = [r[0] for r in out.execute(
                        """SELECT day FROM session_profiles
                           WHERE symbol = ? AND day >= ? AND day <= ? ORDER BY day""",
                        (symbol, start, _period_end(start, period)),
                    )]
                    if not member_days:
                        continue
                    composite = build_period(ticks, out, symbol, period, start,
                                             member_days, history)
                    if composite:
                        rows.append(composite)
                written += save_periods(out, rows)
        return {"periods": written}
    finally:
        for connection in (ticks, history, out):
            if connection is not None:
                connection.close()


def _period_end(start: str, period: str) -> str:
    begin = date.fromisoformat(start)
    if period == "week":
        return (begin + timedelta(days=6)).isoformat()
    following = (begin.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (following - timedelta(days=1)).isoformat()


def build_range(ticks_path: str | None, history_path: str | None, out_path: str,
                days: list[str], symbols: list[str] | None = None,
                include_options: bool = False, progress=None) -> dict:
    sessions = build_sessions(ticks_path, history_path, out_path, days, symbols,
                              include_options, progress)
    periods = build_periods(ticks_path, out_path, days, symbols, history_path)
    return {**sessions, **periods}


def build_yesterday(ticks_path: str, history_path: str, out_path: str,
                    now: datetime | None = None) -> dict:
    """The engine's nightly call: the session that just closed, plus its composites."""
    moment = (now or datetime.now(IST)).astimezone(IST)
    day = moment.date().isoformat()
    return build_range(ticks_path, history_path, out_path, [day])
