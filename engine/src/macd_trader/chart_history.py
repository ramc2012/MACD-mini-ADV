from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .indicators import IncrementalBollingerBands, IncrementalKAMA, IncrementalMACD, IncrementalROC, IncrementalRSI
from .models import Candle, IndicatorPoint
from . import market_calendar
from .sessions import in_session, session_close  # noqa: F401  (in_session re-exported)

IST = ZoneInfo("Asia/Kolkata")
IST_OFFSET_SECONDS = 19_800
EPOCH_DAY = date(1970, 1, 1)


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
        # Indicators are computed per request and returned, never stored: the historical_indicators table they
        # were once written to (3.3 GB by Oct 2026) had no reader, and every chart load rewrote thousands of rows.
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
        return {"symbol": symbol, "timeframe_seconds": timeframe_seconds, "candles": candles, "indicators": indicators}
    finally:
        connection.close()


def aggregate_session_candles(rows: list[Candle], timeframe_seconds: int) -> list[Candle]:
    # Fyers can include post-close snapshots in an intraday history response.
    # They are not tradable regular-session bars and must not seed indicators,
    # affect a walk-forward exit, or extend the chart beyond the instrument's
    # own close (sessions.session_close: F&O 15:40, cash 15:15 since Aug 2026).
    #
    # Each day's session window is resolved once and rows are compared as integers: the per-row datetime
    # checks were ~0.12 s a symbol and most of a 670-symbol warm-up (154 s on 16 Oct 2026). Same rule as
    # sessions.in_session: a trading day, 09:15 <= t < the symbol's own close.
    windows: dict[tuple[str, int], tuple[int, int] | None] = {}

    def window(symbol: str, timestamp: int) -> tuple[int, int] | None:
        key = (symbol, (timestamp + IST_OFFSET_SECONDS) // 86400)
        if key not in windows:
            day = EPOCH_DAY + timedelta(days=key[1])
            windows[key] = (
                (int(datetime.combine(day, time(9, 15), IST).timestamp()),
                 int(datetime.combine(day, session_close(symbol, day), IST).timestamp()))
                if market_calendar.is_trading_day(day) else None)
        return windows[key]

    session_rows = []
    for row in rows:
        span = window(row.symbol, int(row.timestamp))
        if span is not None and span[0] <= row.timestamp < span[1]:
            session_rows.append(row)
    if timeframe_seconds <= 60:
        return session_rows
    buckets: dict[int, Candle] = {}
    for row in session_rows:
        session_open = window(row.symbol, int(row.timestamp))[0]
        elapsed = int(row.timestamp) - session_open
        if elapsed < 0:
            continue
        bucket = session_open + (elapsed // timeframe_seconds) * timeframe_seconds
        existing = buckets.get(bucket)
        if existing is None:
            buckets[bucket] = Candle(row.symbol, bucket, row.open, row.high, row.low, row.close, row.volume, True)
        else:
            existing.high = max(existing.high, row.high)
            existing.low = min(existing.low, row.low)
            existing.close = row.close
            existing.volume += row.volume
    return list(buckets.values())
