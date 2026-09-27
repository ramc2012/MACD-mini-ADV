"""Relative Rotation Graph data vs a benchmark (NIFTY 50).

Computes public-domain approximations of the JdK RS-Ratio / RS-Momentum pair:
    rs        = 100 * close(symbol) / close(benchmark)
    rs_ratio  = 100 + z-score(rs, window)
    rs_momo   = 100 + z-score(one-bar change of rs_ratio, window)
Both series are read from the locally stored 1-minute candles aggregated to
the requested timeframe, so no broker calls are made.
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import UTC, datetime, timedelta

from .chart_history import aggregate_session_candles
from .models import Candle
from .sectors import sector_of

LOOKBACK_DAYS = 45


def _closes(connection: sqlite3.Connection, symbol: str, timeframe_seconds: int, since: int) -> dict[int, float]:
    try:
        rows = connection.execute(
            """SELECT timestamp,open,high,low,close,volume FROM historical_candles
               WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? ORDER BY timestamp""",
            (symbol, since),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    source = [
        Candle(symbol, int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), int(row[5]), True)
        for row in rows
    ]
    return {candle.timestamp: candle.close for candle in aggregate_session_candles(source, timeframe_seconds)}


def _z_series(values: list[float], window: int) -> list[float]:
    """100-centred rolling z-score; flat windows pin to 100."""
    out: list[float] = []
    for index in range(window - 1, len(values)):
        chunk = values[index - window + 1: index + 1]
        spread = statistics.pstdev(chunk)
        out.append(100.0 if spread == 0 else 100.0 + (values[index] - statistics.fmean(chunk)) / spread)
    return out


def _quadrant(x: float, y: float) -> str:
    if x >= 100 and y >= 100:
        return "leading"
    if x >= 100:
        return "weakening"
    if y >= 100:
        return "improving"
    return "lagging"


def compute_rrg(
    database_path: str,
    symbols: list[str],
    benchmark: str = "NSE:NIFTY50-INDEX",
    timeframe_seconds: int = 1800,
    window: int = 14,
    tail: int = 8,
) -> dict:
    since = int((datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).timestamp())
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        bench = _closes(connection, benchmark, timeframe_seconds, since)
        bench_ts = sorted(bench)
        rows: list[dict] = []
        unmapped: list[str] = []
        need = window * 2 + tail
        for symbol in symbols:
            if symbol == benchmark:
                continue
            closes = _closes(connection, symbol, timeframe_seconds, since)
            timestamps = [ts for ts in bench_ts if ts in closes]
            if len(timestamps) < need:
                continue
            rs = [100.0 * closes[ts] / bench[ts] for ts in timestamps]
            rs_ratio = _z_series(rs, window)
            changes = [rs_ratio[i] - rs_ratio[i - 1] for i in range(1, len(rs_ratio))]
            rs_momo = _z_series(changes, window)
            count = min(len(rs_ratio) - 1, len(rs_momo), tail)
            if count < 2:
                continue
            points = [
                {"x": round(rs_ratio[len(rs_ratio) - count + i], 3), "y": round(rs_momo[len(rs_momo) - count + i], 3)}
                for i in range(count)
            ]
            sector = sector_of(symbol)
            if sector == "Other":
                unmapped.append(symbol)
            head = points[-1]
            rows.append({
                "symbol": symbol,
                "sector": sector,
                "tail": points,
                "x": head["x"],
                "y": head["y"],
                "quadrant": _quadrant(head["x"], head["y"]),
            })
    finally:
        connection.close()

    sectors: dict[str, list[dict]] = {}
    for row in rows:
        if row["sector"] != "Indices":
            sectors.setdefault(row["sector"], []).append(row)
    sector_rows = []
    for name, members in sorted(sectors.items()):
        length = min(len(member["tail"]) for member in members)
        points = [
            {
                "x": round(statistics.fmean(member["tail"][len(member["tail"]) - length + i]["x"] for member in members), 3),
                "y": round(statistics.fmean(member["tail"][len(member["tail"]) - length + i]["y"] for member in members), 3),
            }
            for i in range(length)
        ]
        head = points[-1]
        sector_rows.append({
            "sector": name,
            "members": len(members),
            "tail": points,
            "x": head["x"],
            "y": head["y"],
            "quadrant": _quadrant(head["x"], head["y"]),
        })

    return {
        "benchmark": benchmark,
        "timeframe_seconds": timeframe_seconds,
        "window": window,
        "tail": tail,
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluated": len(rows),
        "unmapped": unmapped,
        "sectors": sector_rows,
        "symbols": rows,
    }
