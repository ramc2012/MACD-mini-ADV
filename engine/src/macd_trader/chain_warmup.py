"""Warm-up bars for a newly selected option from the stored option-chain snapshots.

A contract chosen around today's price (the daily ladder or an intraday refresh) often has no stored minute bars,
and broker history downloads may be switched off. The chain store (oi_capture.py) keeps every index strike within
4% of spot each minute and every F&O stock's ATM +-4 strikes each 15-minute sweep, front series, so the marks of
the strike the selector picked are usually already on disk. They become one-minute candles (open = high = low =
close = the snapshot's last price, volume = traded quantity since the previous snapshot that day) and are then
aggregated exactly like stored minutes. Sparse stock marks give coarse bars; they only seed the indicators and
are never written back to the candle store.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .chart_history import aggregate_session_candles
from .models import Candle

IST = ZoneInfo("Asia/Kolkata")


def chain_minutes(database_path: str, symbol: str, limit: int = 20_000) -> list[Candle]:
    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error:
        return []
    try:
        rows = connection.execute(
            """SELECT ts, ltp, volume FROM (SELECT ts, ltp, volume FROM chain_snapshots
                 WHERE symbol = ? AND ltp > 0 ORDER BY ts DESC LIMIT ?) ORDER BY ts""", (symbol, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        connection.close()
    out: list[Candle] = []
    previous: tuple | None = None          # (IST day, cumulative volume)
    for ts, ltp, volume in rows:
        minute = int(ts) // 60 * 60
        day = datetime.fromtimestamp(minute, IST).date()
        traded = 0
        if volume is not None:
            traded = max(0, int(volume) - previous[1]) if previous and previous[0] == day else 0
            previous = (day, int(volume))
        if out and out[-1].timestamp == minute:
            last = out[-1]
            out[-1] = Candle(symbol, minute, last.open, max(last.high, ltp), min(last.low, ltp), float(ltp),
                             last.volume + traded, True)
        else:
            out.append(Candle(symbol, minute, float(ltp), float(ltp), float(ltp), float(ltp), traded, True))
    return out


def chain_candles(database_path: str, symbol: str, timeframe_seconds: int) -> list[Candle]:
    """Session-aligned bars of ``timeframe_seconds`` built from the chain marks of ``symbol``."""
    minutes = chain_minutes(database_path, symbol)
    return aggregate_session_candles(minutes, timeframe_seconds) if minutes else []
