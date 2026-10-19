"""A continuous, left-to-right series of profiles on one price grid.

The auction ladder drew today alone. Reading today's auction needs the
structure it trades against, so the series lays out, oldest on the left:

* the previous calendar month, whole -- the reference a new month opens into;
* each earlier week of the current month that lies beyond the last five
  sessions;
* the current week, developing (today included);
* the last five completed sessions, one column each, with TPO letters;
* today, live.

Every column is built from the same per-session distribution the stored
composites use (profile_history.session_levels: the condensed tick ladder
where it exists, the durable minute bars otherwise) and bucketed onto one ATR
row size, so a POC in a monthly column and one in a day column sit on the same
row grid and can be compared by eye. Composite columns carry TPO counts, not
letters -- a letter names a 30-minute bracket of ONE session.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .profile_history import period_key, session_levels, value_area_from_levels
from .sessions import SESSION_OPEN, session_close

IST = ZoneInfo("Asia/Kolkata")
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
DAY_COLUMNS = 5
# Past columns do not change; keyed (symbol, as_of, row_size, day or period).
_cache: dict[tuple, dict] = {}


def stored_days(history: sqlite3.Connection, symbol: str, before: str, lookback_days: int = 70) -> list[str]:
    """IST sessions with durable minute bars for ``symbol`` before ``before``."""
    end = datetime.combine(date.fromisoformat(before), datetime.min.time(), IST)
    start = end - timedelta(days=lookback_days)
    rows = history.execute(
        """SELECT DISTINCT date(timestamp, 'unixepoch', '+5 hours', '+30 minutes') FROM historical_candles
           WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?""",
        (symbol, int(start.timestamp()), int(end.timestamp()))).fetchall()
    return sorted(row[0] for row in rows if row[0])


def _row(price: float, row_size: float) -> float:
    return round(math.floor(price / row_size + 1e-9) * row_size, 8)


def _session_column(ticks, history, symbol: str, day: str, row_size: float) -> dict | None:
    """One session: volume and TPO brackets per row, and its tick-precise levels."""
    levels, source, extras = session_levels(ticks, history, symbol, day)
    if not levels:
        return None
    volume: dict[float, float] = {}
    for price, size in levels.items():
        key = _row(price, row_size)
        volume[key] = volume.get(key, 0.0) + size
    brackets: dict[float, set] = {}
    opened = datetime.combine(date.fromisoformat(day), SESSION_OPEN, IST).timestamp()
    close = session_close(symbol, day)
    closed = datetime.combine(date.fromisoformat(day), close, IST).timestamp()
    high = low = first = last = None
    for ts, open_price, bar_high, bar_low, bar_close, _vol in extras.get("minutes", []):
        if bar_high is None or bar_low is None or not (opened <= int(ts) < closed):
            continue
        bracket = int((int(ts) - opened) // 1800)
        top, bottom = _row(float(bar_high), row_size), _row(float(bar_low), row_size)
        steps = int(round((top - bottom) / row_size))
        for step in range(steps + 1):
            brackets.setdefault(round(bottom + step * row_size, 8), set()).add(bracket)
        high = float(bar_high) if high is None else max(high, float(bar_high))
        low = float(bar_low) if low is None else min(low, float(bar_low))
        first = first if first is not None else (float(open_price) if open_price else None)
        last = float(bar_close) if bar_close else last
    poc, vah, val = value_area_from_levels(levels)
    rows = sorted(set(volume) | set(brackets), reverse=True)
    return {
        "kind": "day", "key": day, "label": date.fromisoformat(day).strftime("%a %d %b"),
        "from": day, "to": day, "sessions": 1, "source": source,
        "open": first, "close": last, "high": high if high is not None else max(levels),
        "low": low if low is not None else min(levels), "poc": poc, "vah": vah, "val": val,
        "volume": round(sum(levels.values())),
        "rows": [{"price": price, "volume": round(volume.get(price, 0.0)),
                  "tpo": len(brackets.get(price, ())),
                  "letters": "".join(LETTERS[b] for b in sorted(brackets.get(price, ())) if 0 <= b < len(LETTERS))}
                 for price in rows],
    }


def _composite(columns: list[dict], kind: str, key: str, label: str, developing: bool = False) -> dict | None:
    """Merge session columns: volume and TPO counts summed per row."""
    columns = [column for column in columns if column]
    if not columns:
        return None
    volume: dict[float, float] = {}
    tpo: dict[float, int] = {}
    for column in columns:
        for row in column["rows"]:
            volume[row["price"]] = volume.get(row["price"], 0.0) + row["volume"]
            tpo[row["price"]] = tpo.get(row["price"], 0) + row["tpo"]
    # Value area over the merged distribution -- the composite's own, not an
    # average of its days' (TPO counts where the instrument has no volume).
    basis = volume if any(volume.values()) else {price: float(count) for price, count in tpo.items()}
    poc, vah, val = value_area_from_levels(basis)
    return {
        "kind": kind, "key": key, "label": label, "developing": developing,
        "from": columns[0]["from"], "to": columns[-1]["to"], "sessions": len(columns),
        "source": "composite", "open": columns[0]["open"], "close": columns[-1]["close"],
        "high": max(c["high"] for c in columns if c["high"] is not None),
        "low": min(c["low"] for c in columns if c["low"] is not None),
        "poc": poc, "vah": vah, "val": val, "volume": round(sum(volume.values())),
        "rows": [{"price": price, "volume": round(volume.get(price, 0.0)), "tpo": tpo.get(price, 0), "letters": ""}
                 for price in sorted(set(volume) | set(tpo), reverse=True)],
    }


def _cached_session(ticks, history, symbol: str, day: str, row_size: float) -> dict | None:
    key = (symbol, day, row_size)
    if key not in _cache:
        _cache[key] = _session_column(ticks, history, symbol, day, row_size)
    return _cache[key]


def build_series(ticks: sqlite3.Connection | None, history: sqlite3.Connection, symbol: str,
                 today: str, row_size: float, live_today: dict | None = None,
                 day_columns: int = DAY_COLUMNS) -> dict:
    """The column list for ``symbol`` as of ``today`` (see the module docstring).

    ``live_today`` is the desk's own developing profile, already on the row
    grid (Profile.snapshot(row_size=...)); without it today is rebuilt from
    the minute bars stored so far.
    """
    days = stored_days(history, symbol, today)
    sessions = {day: _cached_session(ticks, history, symbol, day, row_size) for day in days}
    recent = [day for day in days if sessions.get(day)][-day_columns:]
    today_date = date.fromisoformat(today)
    month_start = today_date.replace(day=1)
    previous_month = (month_start - timedelta(days=1)).replace(day=1)
    this_week = period_key(today, "week")

    columns: list[dict] = []
    notes: list[str] = []
    # Previous month, whole -- even the days it shares with the five-day window.
    prev_days = [day for day in days if previous_month.isoformat() <= day < month_start.isoformat()]
    monthly = _composite([sessions[d] for d in prev_days], "month", previous_month.isoformat()[:7],
                         previous_month.strftime("%b %Y"))
    if monthly:
        columns.append(monthly)
    else:
        notes.append(f"{previous_month.strftime('%b %Y')}: no stored minute bars for a monthly profile")
    # Earlier weeks of this month that reach beyond the five-day window.
    oldest_recent = recent[0] if recent else today
    weeks: dict[str, list[str]] = {}
    for day in days:
        if day >= month_start.isoformat() and period_key(day, "week") != this_week:
            weeks.setdefault(period_key(day, "week"), []).append(day)
    for week, week_days in sorted(weeks.items()):
        if week_days[0] < oldest_recent:
            column = _composite([sessions[d] for d in week_days], "week", week,
                                f"Week of {date.fromisoformat(week).strftime('%d %b')}")
            if column:
                columns.append(column)
    # The current week so far, today included.
    week_days = [sessions[d] for d in days if period_key(d, "week") == this_week]
    today_column = None
    if live_today and live_today.get("levels"):
        today_column = {
            "kind": "today", "key": today, "label": "Today", "developing": True,
            "from": today, "to": today, "sessions": 1, "source": "live",
            "open": live_today.get("open"), "close": live_today.get("last"),
            "high": live_today.get("high"), "low": live_today.get("low"),
            "poc": live_today.get("poc"), "vah": live_today.get("vah"), "val": live_today.get("val"),
            "volume": sum(level["volume"] for level in live_today["levels"]),
            "rows": [{"price": level["price"], "volume": level["volume"], "tpo": level["tpo"],
                      "letters": level["letters"]} for level in live_today["levels"]],
        }
    else:
        rebuilt = _session_column(ticks, history, symbol, today, row_size)
        if rebuilt:
            today_column = {**rebuilt, "kind": "today", "label": "Today", "developing": True}
    weekly = _composite([*week_days, today_column], "week", this_week,
                        f"This week (from {date.fromisoformat(this_week).strftime('%d %b')})", developing=True)
    if weekly:
        columns.append(weekly)
    columns.extend(sessions[day] for day in recent)
    if today_column:
        columns.append(today_column)
    return {"symbol": symbol, "as_of": today, "row_size": row_size, "columns": columns,
            "stored_sessions": len(days), "notes": notes}
