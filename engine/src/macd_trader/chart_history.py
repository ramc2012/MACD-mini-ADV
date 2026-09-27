from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .indicators import IncrementalBollingerBands, IncrementalKAMA, IncrementalMACD, IncrementalROC, IncrementalRSI
from .models import Candle, IndicatorPoint

IST = ZoneInfo("Asia/Kolkata")


def load_chart_history(
    database_path: str,
    symbol: str,
    timeframe_seconds: int,
    fast_period: int,
    slow_period: int,
    signal_period: int,
    bb_period: int = 20,
    bb_deviations: float = 2.0,
    kama_period: int = 10,
    kama_fast: int = 2,
    kama_slow: int = 30,
    kama_rsi_period: int = 14,
    kama_roc_period: int = 5,
    extra_candles: list[Candle] | None = None,
    source_row_limit: int | None = None,
) -> dict:
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_indicators (
              symbol TEXT NOT NULL, timeframe_seconds INTEGER NOT NULL, timestamp INTEGER NOT NULL,
              macd REAL NOT NULL, signal REAL NOT NULL, histogram REAL NOT NULL,
              bb_middle REAL, bb_upper REAL, bb_lower REAL, bb_width REAL, kama REAL,
              kama_rsi REAL, kama_roc REAL,
              PRIMARY KEY(symbol, timeframe_seconds,timestamp)
            )
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(historical_indicators)")}
        for name in ("kama_rsi", "kama_roc"):
            if name not in columns:
                connection.execute(f"ALTER TABLE historical_indicators ADD COLUMN {name} REAL")
        try:
            if source_row_limit is None:
                rows = connection.execute(
                    """SELECT timestamp,open,high,low,close,volume FROM historical_candles
                       WHERE symbol=? AND timeframe_seconds=60 ORDER BY timestamp""",
                    (symbol,),
                ).fetchall()
            else:
                # The live engine only retains the latest 500 aggregated bars.
                # Loading an entire year of raw minutes per instrument on every
                # restart delays the websocket connection by several minutes.
                rows = connection.execute(
                    """SELECT timestamp,open,high,low,close,volume FROM (
                         SELECT timestamp,open,high,low,close,volume FROM historical_candles
                         WHERE symbol=? AND timeframe_seconds=60
                         ORDER BY timestamp DESC LIMIT ?
                       ) ORDER BY timestamp""",
                    (symbol, source_row_limit),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            rows = []
        source = [
            Candle(symbol, int(row["timestamp"]), float(row["open"]), float(row["high"]),
                   float(row["low"]), float(row["close"]), int(row["volume"]), True)
            for row in rows
        ]
        candles = aggregate_session_candles(source, timeframe_seconds)
        if extra_candles:
            # Live candles from the engine's in-memory history — the durable
            # store only reaches the last research download, so without this
            # merge today's bars never reach the chart.
            by_time = {candle.timestamp: candle for candle in candles}
            for candle in extra_candles:
                by_time[candle.timestamp] = candle
            candles = [by_time[key] for key in sorted(by_time)]
        macd = IncrementalMACD(fast_period, slow_period, signal_period)
        bollinger = IncrementalBollingerBands(bb_period, bb_deviations)
        kama = IncrementalKAMA(kama_period, kama_fast, kama_slow)
        kama_rsi = IncrementalRSI(kama_rsi_period)
        kama_roc = IncrementalROC(kama_roc_period)
        indicators: list[IndicatorPoint] = []
        for candle in candles:
            macd_value = macd.update(candle.close)
            bb = bollinger.update(candle.close)
            kama_value = kama.update(candle.close)
            rsi_value = kama_rsi.update(kama_value) if kama_value is not None else None
            roc_value = kama_roc.update(kama_value) if kama_value is not None else None
            indicators.append(IndicatorPoint(
                symbol, candle.timestamp, macd_value.macd, macd_value.signal, macd_value.histogram,
                bb.middle, bb.upper, bb.lower, bb.width, kama_value, rsi_value, roc_value,
            ))
        connection.executemany(
            """INSERT OR REPLACE INTO historical_indicators
               (symbol,timeframe_seconds,timestamp,macd,signal,histogram,bb_middle,bb_upper,bb_lower,bb_width,kama,kama_rsi,kama_roc)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (point.symbol, timeframe_seconds, point.timestamp, point.macd, point.signal, point.histogram,
                 point.bb_middle, point.bb_upper, point.bb_lower, point.bb_width, point.kama, point.kama_rsi, point.kama_roc)
                for point in indicators
            ],
        )
        connection.commit()
        return {"symbol": symbol, "timeframe_seconds": timeframe_seconds, "candles": candles, "indicators": indicators}
    finally:
        connection.close()


def aggregate_session_candles(rows: list[Candle], timeframe_seconds: int) -> list[Candle]:
    # Fyers can include post-close snapshots in an intraday history response.
    # They are not tradable regular-session bars and must not seed indicators,
    # affect a walk-forward exit, or extend the chart beyond 15:30 IST.
    session_rows = [
        row for row in rows
        if time(9, 15) <= datetime.fromtimestamp(row.timestamp, UTC).astimezone(IST).time() < time(15, 30)
    ]
    if timeframe_seconds <= 60:
        return session_rows
    buckets: dict[int, Candle] = {}
    for row in session_rows:
        moment = datetime.fromtimestamp(row.timestamp, UTC).astimezone(IST)
        session_open = datetime.combine(moment.date(), time(9, 15), IST)
        elapsed = int((moment - session_open).total_seconds())
        if elapsed < 0:
            continue
        bucket_time = session_open + timedelta(seconds=(elapsed // timeframe_seconds) * timeframe_seconds)
        bucket = int(bucket_time.timestamp())
        existing = buckets.get(bucket)
        if existing is None:
            buckets[bucket] = Candle(row.symbol, bucket, row.open, row.high, row.low, row.close, row.volume, True)
        else:
            existing.high = max(existing.high, row.high)
            existing.low = min(existing.low, row.low)
            existing.close = row.close
            existing.volume += row.volume
    return list(buckets.values())
