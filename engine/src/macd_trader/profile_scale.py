"""One ATR-scaled row size for every drawing of a symbol's profile.

The profile was published at the raw exchange tick: HDFCBANK spanned 191
rows of Rs 0.05 on 29 Sep 2026, most of them empty, and the polled desk
snapshot then kept every Nth of them to fit 90 -- dropping the skipped rows'
volume and TPO letters. The auction ladder (full) and the order-flow
profile (sampled) drew two different shapes of one session, and neither
shared the footprint's row grid.

Rows are sized from the symbol's own volatility instead: the daily ATR over
prior sessions, divided into ``PROFILE_ROWS_PER_ATR`` rows and snapped up to
the footprint's tick ladder, so an ordinary day fills a readable ladder and
every view aggregates the same way. Prior sessions only, so the row does not
move during the day. This is display scaling: POC, value area, IB and single
prints -- the desk's setup inputs -- stay computed at tick precision.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .footprint import ROW_TICKS_LADDER
from .sessions import in_session
from .universe import INDEX_SPOTS, STOCK_SPOTS, desk_underlying

IST = ZoneInfo("Asia/Kolkata")

ATR_PERIOD = 14
# Fewer prior sessions than this is not an ATR worth scaling by.
ATR_MIN_SESSIONS = 3
# Rows per daily ATR. Snapping UP the tick ladder can nearly double a row
# (HDFCBANK: 14.67 / 40 = 0.37 -> 0.50), and a normal day spans under one
# ATR, so 40 left 21 rows; 60 keeps an ordinary session near 30-50 rows.
PROFILE_ROWS_PER_ATR = 60
# Minute bars read per symbol: ATR_PERIOD + 1 sessions with room for holidays.
_LOOKBACK_DAYS = 30

_cache: dict[tuple[str, str, str, float], dict] = {}
# Symbols already found to have too little history today: not re-queried.
_no_history: set[tuple[str, str, str, float]] = set()


def row_ticks_for_range(span: float, tick: float, rows: int = PROFILE_ROWS_PER_ATR) -> int:
    """Smallest tick-ladder step that fits ``span`` into at most ``rows`` rows."""
    if tick <= 0 or span <= 0:
        return 1
    for candidate in ROW_TICKS_LADDER:
        if span / (candidate * tick) <= rows:
            return candidate
    return ROW_TICKS_LADDER[-1]


def daily_ranges(connection: sqlite3.Connection, symbol: str, before_day: str) -> list[tuple[float, float, float]]:
    """(high, low, close) of each regular session before ``before_day``, oldest first."""
    end = datetime.combine(datetime.fromisoformat(before_day).date(), time(0, 0), IST)
    start = end - timedelta(days=_LOOKBACK_DAYS)
    rows = connection.execute(
        """SELECT timestamp, high, low, close FROM historical_candles
           WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?
           ORDER BY timestamp""",
        (symbol, int(start.timestamp()), int(end.timestamp())),
    ).fetchall()
    days: dict[str, list[float]] = {}
    for stamp, high, low, close in rows:
        moment = datetime.fromtimestamp(int(stamp), IST)
        if not in_session(symbol, moment):
            continue
        day = days.get(moment.date().isoformat())
        if day is None:
            days[moment.date().isoformat()] = [float(high), float(low), float(close)]
        else:
            day[0] = max(day[0], float(high))
            day[1] = min(day[1], float(low))
            day[2] = float(close)
    return [tuple(days[key]) for key in sorted(days)]


def average_true_range(sessions: list[tuple[float, float, float]], period: int = ATR_PERIOD) -> float | None:
    """Mean true range of the last ``period`` sessions (gaps count, as in ATR)."""
    ranges = []
    for index, (high, low, _close) in enumerate(sessions):
        if index == 0:
            ranges.append(high - low)
            continue
        prior_close = sessions[index - 1][2]
        ranges.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
    recent = [value for value in ranges[-period:] if value > 0]
    return sum(recent) / len(recent) if recent else None


def _fallback_symbol(symbol: str) -> str | None:
    """The spot behind a futures contract: the same point scale, longer history."""
    if not symbol.upper().endswith("FUT"):
        return None
    name = desk_underlying(symbol)
    return (INDEX_SPOTS.get(name) or STOCK_SPOTS.get(name)) if name else None


def profile_scale(history_path: str | None, symbol: str, day: str, tick: float,
                  session_range: float | None = None) -> dict:
    """Row size for ``symbol``'s profile on ``day``, and how it was chosen.

    Cached per (symbol, day) once an ATR is found. Reads SQLite, so call it
    off the event loop the first time; :func:`cached_scale` serves the loop.
    """
    key = (history_path or "", symbol, day, tick)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    atr = None
    sessions = 0
    source = symbol
    if history_path and key not in _no_history:
        try:
            connection = sqlite3.connect(f"file:{history_path}?mode=ro", uri=True, timeout=5)
        except sqlite3.OperationalError:
            connection = None
        if connection is not None:
            try:
                for candidate in (symbol, _fallback_symbol(symbol)):
                    if not candidate:
                        continue
                    ranges = daily_ranges(connection, candidate, day)
                    if len(ranges) >= ATR_MIN_SESSIONS:
                        atr = average_true_range(ranges)
                        sessions = min(len(ranges), ATR_PERIOD)
                        source = candidate
                        break
                if atr is None:
                    _no_history.add(key)
            except sqlite3.OperationalError:
                atr = None
            finally:
                connection.close()
    if atr:
        row_ticks = row_ticks_for_range(atr, tick)
        scale = {"row_ticks": row_ticks, "row_size": round(row_ticks * tick, 8),
                 "basis": "daily_atr", "atr": round(atr, 4), "atr_sessions": sessions,
                 "atr_symbol": source, "rows_per_atr": PROFILE_ROWS_PER_ATR}
        _cache[key] = scale
        return scale
    # No history (a newly listed contract): scale by today's own range so far.
    # Not cached -- the range grows through the session.
    row_ticks = row_ticks_for_range(session_range or 0.0, tick)
    return {"row_ticks": row_ticks, "row_size": round(row_ticks * tick, 8),
            "basis": "session_range" if session_range else "tick", "atr": None,
            "atr_sessions": 0, "atr_symbol": None, "rows_per_atr": PROFILE_ROWS_PER_ATR}


def cached_scale(history_path: str | None, symbol: str, day: str, tick: float) -> dict | None:
    """The ATR scale if already computed -- never touches the database."""
    return _cache.get((history_path or "", symbol, day, tick))
