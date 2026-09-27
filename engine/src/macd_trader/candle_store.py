"""Durable writer for live one-minute candles.

The research downloader populates ``historical_candles`` in bulk; this writer
keeps the same table current from the live tick stream, so charts and warm-up
no longer depend on broker refetches after a restart. Rows are buffered in
memory and flushed off the event loop in batches. ``INSERT OR REPLACE`` on the
(symbol, timeframe, timestamp) key means an official broker download later
simply overwrites the tick-built row.
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from .models import Candle

FLUSH_INTERVAL_SECONDS = 10.0
MAX_BUFFER_ROWS = 50_000


def _asset_type(symbol: str) -> str:
    if symbol.endswith("-INDEX"):
        return "index"
    if symbol.endswith("-EQ"):
        return "stock"
    return "option"


class LiveCandleWriter:
    def __init__(self, database_path: str):
        self._path = database_path
        self._lock = RLock()
        self._buffer: list[tuple] = []
        self._cumulative_volume: dict[str, int] = {}
        self._task: asyncio.Task | None = None
        self.rows_written = 0
        self.last_flush_at: datetime | None = None
        self.last_error: str | None = None

    def add(self, candle: Candle, expiry: str | None = None) -> None:
        # Tick volume from Fyers is the cumulative session volume, but the
        # research table stores per-bar traded volume — store the delta.
        previous = self._cumulative_volume.get(candle.symbol, 0)
        cumulative = max(0, candle.volume)
        bar_volume = cumulative - previous if cumulative >= previous else cumulative
        self._cumulative_volume[candle.symbol] = cumulative
        with self._lock:
            if len(self._buffer) >= MAX_BUFFER_ROWS:
                self._buffer.pop(0)
            self._buffer.append((
                candle.symbol, 60, candle.timestamp,
                candle.open, candle.high, candle.low, candle.close,
                int(bar_volume), _asset_type(candle.symbol), expiry,
                datetime.now(UTC).isoformat(),
            ))

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await asyncio.to_thread(self.flush_sync)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            await asyncio.to_thread(self.flush_sync)

    def flush_sync(self) -> int:
        with self._lock:
            rows, self._buffer = self._buffer, []
        if not rows:
            return 0
        try:
            connection = sqlite3.connect(self._path, timeout=30)
            try:
                # NOT WAL. This file lives on a Docker Desktop bind mount,
                # whose virtualised filesystem does not reliably provide the
                # shared-memory and byte-range locking WAL requires. The trade
                # book was corrupted this way; historical.sqlite3 is the last
                # writer still using it. (Corruption here predates the pragma —
                # a 4GB file under constant writes on this mount is fragile
                # regardless — but WAL removes none of that risk and adds some.)
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS historical_candles (
                      symbol TEXT NOT NULL, timeframe_seconds INTEGER NOT NULL, timestamp INTEGER NOT NULL,
                      open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                      volume INTEGER NOT NULL, asset_type TEXT NOT NULL, expiry TEXT,
                      downloaded_at TEXT NOT NULL,
                      PRIMARY KEY(symbol, timeframe_seconds, timestamp)
                    )
                """)
                connection.executemany(
                    """INSERT OR REPLACE INTO historical_candles
                       (symbol, timeframe_seconds, timestamp, open, high, low, close, volume, asset_type, expiry, downloaded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                connection.commit()
            finally:
                connection.close()
            self.rows_written += len(rows)
            self.last_flush_at = datetime.now(UTC)
            self.last_error = None
            return len(rows)
        except sqlite3.Error as exc:
            self.last_error = str(exc)
            # Re-buffer so a transient lock does not drop bars.
            with self._lock:
                self._buffer = rows + self._buffer
            return 0

    def status(self) -> dict:
        with self._lock:
            pending = len(self._buffer)
        return {
            "rows_written": self.rows_written,
            "pending_rows": pending,
            "last_flush_at": self.last_flush_at.isoformat() if self.last_flush_at else None,
            "last_error": self.last_error,
        }


def resolve_expiry(symbol: str, contracts: dict) -> str | None:
    contract = contracts.get(symbol)
    return getattr(contract, "expiry", None) if contract else None


def prior_session_candle_count(database_path: str, symbol: str, session_start: int) -> int:
    """Minute rows stored before ``session_start`` — genuine prior-day history.

    A plain row count cannot answer the coverage question the lazy ratio
    backfill asks. LiveCandleWriter starts storing bars the moment a leg is
    subscribed, so a strike selected this morning always has rows and always
    looked downloaded; its 90-day history was then skipped forever.
    """
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM historical_candles "
                "WHERE symbol=? AND timeframe_seconds=60 AND timestamp<?",
                (symbol, session_start),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return 0
            raise
        return int(row[0] or 0)
    finally:
        connection.close()


def store_historical_candles(
    database_path: str, rows: list[Candle], expiry: str | None = None,
    expiry_by_symbol: dict[str, str] | None = None,
) -> int:
    """Persist broker minute history in the same durable table as live bars."""
    if not rows:
        return 0
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_candles (
              symbol TEXT NOT NULL, timeframe_seconds INTEGER NOT NULL, timestamp INTEGER NOT NULL,
              open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
              volume INTEGER NOT NULL, asset_type TEXT NOT NULL, expiry TEXT,
              downloaded_at TEXT NOT NULL,
              PRIMARY KEY(symbol, timeframe_seconds, timestamp)
            )
        """)
        downloaded_at = datetime.now(UTC).isoformat()
        connection.executemany(
            """INSERT OR REPLACE INTO historical_candles
               (symbol, timeframe_seconds, timestamp, open, high, low, close, volume, asset_type, expiry, downloaded_at)
               VALUES (?, 60, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    row.symbol, row.timestamp, row.open, row.high, row.low, row.close,
                    int(row.volume), _asset_type(row.symbol),
                    (expiry_by_symbol or {}).get(row.symbol, expiry), downloaded_at,
                )
                for row in rows
            ],
        )
        connection.commit()
        return len(rows)
    finally:
        connection.close()


# Deleting fewer rows than this leaves the free pages for SQLite to reuse;
# VACUUM rewrites the whole file and is only worth it for a real reclaim.
PRUNE_VACUUM_MIN_ROWS = 200_000


def prune_history(
    database_path: str, *, now: datetime, keep_days: int, expired_keep_days: int,
) -> dict[str, int]:
    """Bound the minute-bar cache, which otherwise grows ~190 MB a trading day.

    Two rules, both measured in days:
    - an option contract's bars go ``expired_keep_days`` after its expiry;
    - any bar older than ``keep_days`` goes, contract or spot.
    Cached indicators follow their bars. A value of 0 disables that rule.
    Run outside market hours: the delete and any VACUUM hold the write lock.
    """
    if not Path(database_path).exists():
        return {"candles": 0, "indicators": 0, "vacuumed": 0}
    connection = sqlite3.connect(database_path, timeout=60)
    deleted = {"candles": 0, "indicators": 0, "vacuumed": 0}
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "historical_candles" not in tables:
            return deleted
        has_indicators = "historical_indicators" in tables
        with connection:
            if expired_keep_days > 0:
                expired_before = (now.date().toordinal() - expired_keep_days)
                cutoff = datetime.fromordinal(expired_before).date().isoformat()
                symbols = [row[0] for row in connection.execute(
                    "SELECT DISTINCT symbol FROM historical_candles WHERE expiry IS NOT NULL AND expiry < ?",
                    (cutoff,))]
                for offset in range(0, len(symbols), 500):
                    chunk = symbols[offset:offset + 500]
                    marks = ",".join("?" * len(chunk))
                    deleted["candles"] += connection.execute(
                        f"DELETE FROM historical_candles WHERE symbol IN ({marks})", chunk).rowcount
                    if has_indicators:
                        deleted["indicators"] += connection.execute(
                            f"DELETE FROM historical_indicators WHERE symbol IN ({marks})", chunk).rowcount
            if keep_days > 0:
                oldest = int(now.timestamp()) - keep_days * 86_400
                deleted["candles"] += connection.execute(
                    "DELETE FROM historical_candles WHERE timestamp < ?", (oldest,)).rowcount
                if has_indicators:
                    deleted["indicators"] += connection.execute(
                        "DELETE FROM historical_indicators WHERE timestamp < ?", (oldest,)).rowcount
        if deleted["candles"] + deleted["indicators"] >= PRUNE_VACUUM_MIN_ROWS:
            connection.execute("VACUUM")
            deleted["vacuumed"] = 1
        return deleted
    finally:
        connection.close()
