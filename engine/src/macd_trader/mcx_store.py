"""The MCX lane's own bar database.

One-minute bars for every contract the lane watches: a contract's history is downloaded once, the first time the
lane sees it, and from then on its bars come from the live ticks. The warm-up reads this file, not the broker.

What is kept beside the bars:
* ``symbols``: when a contract was first watched, the start of its one download (``backfilled_from``; empty until
  it has had one), and the time up to which every feed gap has been filled for it (``checked_to``).
* ``feed``: the last time the lane was receiving MCX ticks (``alive_to``).
* ``gaps``: stretches of the MCX session during which the lane received nothing (the engine was down or the socket
  was). A contract is downloaded a second time only to fill one of these.

SQLite in WAL mode. The lane writes from the event loop (a few hundred rows a minute) and from worker threads
(a download); one lock serialises them.
"""

from __future__ import annotations

import math
import sqlite3
import threading
from pathlib import Path

from .models import Candle

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL, ts INTEGER NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL,
    PRIMARY KEY (symbol, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS symbols (
    symbol TEXT PRIMARY KEY, first_seen INTEGER NOT NULL, backfilled_from INTEGER, checked_to INTEGER
);
CREATE TABLE IF NOT EXISTS feed (id INTEGER PRIMARY KEY CHECK (id = 1), alive_to INTEGER);
CREATE TABLE IF NOT EXISTS gaps (from_ts INTEGER NOT NULL, to_ts INTEGER NOT NULL, PRIMARY KEY (from_ts, to_ts));
"""


def valid_minute(bar: Candle) -> bool:
    stamp, o, h, l, c = bar.timestamp, bar.open, bar.high, bar.low, bar.close
    return (stamp % 60 == 0 and all(math.isfinite(v) and v > 0 for v in (o, h, l, c))
            and l <= min(o, c) <= max(o, c) <= h and (bar.volume or 0) >= 0)


class McxBarStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ------------------------------------------------------------------ bars

    def write(self, bars: list[Candle], *, preserve_existing: bool = False) -> int:
        """Persist valid minutes; a history fill can preserve already captured bars."""
        rows = [(bar.symbol, int(bar.timestamp), float(bar.open), float(bar.high), float(bar.low), float(bar.close),
                 int(bar.volume or 0)) for bar in bars if valid_minute(bar)]
        if not rows:
            return 0
        with self._lock:
            verb = "IGNORE" if preserve_existing else "REPLACE"
            result = self._db.executemany(f"INSERT OR {verb} INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            self._db.commit()
        return result.rowcount

    def minutes(self, symbol: str, since_ts: int) -> list[Candle]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol = ? AND ts >= ? ORDER BY ts",
                (symbol, int(since_ts))).fetchall()
        bars = (Candle(symbol, int(ts), o, h, l, c, int(v), True) for ts, o, h, l, c, v in rows)
        return [bar for bar in bars if valid_minute(bar)]

    def prune(self, before_ts: int) -> int:
        """Drop bars older than ``before_ts`` and the records of contracts that have none left."""
        with self._lock:
            removed = self._db.execute("DELETE FROM bars WHERE ts < ?", (int(before_ts),)).rowcount
            self._db.execute("DELETE FROM gaps WHERE to_ts < ?", (int(before_ts),))
            self._db.execute("DELETE FROM symbols WHERE first_seen < ? AND symbol NOT IN (SELECT DISTINCT symbol FROM bars)",
                             (int(before_ts),))
            self._db.commit()
        return int(removed or 0)

    # --------------------------------------------------------------- symbols

    def seen(self, symbols: list[str], now_ts: int) -> None:
        with self._lock:
            self._db.executemany("INSERT OR IGNORE INTO symbols (symbol, first_seen) VALUES (?, ?)",
                                 [(symbol, int(now_ts)) for symbol in symbols])
            self._db.commit()

    def symbol(self, symbol: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT first_seen, backfilled_from, checked_to FROM symbols WHERE symbol = ?",
                                   (symbol,)).fetchone()
        return None if row is None else {"first_seen": row[0], "backfilled_from": row[1], "checked_to": row[2]}

    def mark_filled(self, symbol: str, now_ts: int, *, backfilled_from: int | None = None) -> None:
        """Every gap up to now is filled for this contract; ``backfilled_from`` records its one full download."""
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO symbols (symbol, first_seen) VALUES (?, ?)", (symbol, int(now_ts)))
            if backfilled_from is not None:
                self._db.execute("UPDATE symbols SET backfilled_from = ?, checked_to = ? WHERE symbol = ?",
                                 (int(backfilled_from), int(now_ts), symbol))
            else:
                self._db.execute("UPDATE symbols SET checked_to = ? WHERE symbol = ?", (int(now_ts), symbol))
            self._db.commit()

    def never_downloaded(self, symbols: list[str]) -> list[str]:
        if not symbols:
            return []
        with self._lock:
            done = {row[0] for row in self._db.execute(
                "SELECT symbol FROM symbols WHERE backfilled_from IS NOT NULL")}
        return [symbol for symbol in symbols if symbol not in done]

    # ------------------------------------------------------------- feed gaps

    def alive_to(self) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT alive_to FROM feed WHERE id = 1").fetchone()
        return None if row is None else row[0]

    def heartbeat(self, now_ts: int) -> None:
        with self._lock:
            self._db.execute("INSERT INTO feed (id, alive_to) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET alive_to = excluded.alive_to",
                             (int(now_ts),))
            self._db.commit()

    def add_gap(self, from_ts: int, to_ts: int) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO gaps VALUES (?, ?)", (int(from_ts), int(to_ts)))
            self._db.commit()

    def gaps_ending_after(self, ts: int) -> list[tuple[int, int]]:
        with self._lock:
            return [(int(a), int(b)) for a, b in self._db.execute(
                "SELECT from_ts, to_ts FROM gaps WHERE to_ts > ? ORDER BY from_ts", (int(ts),))]

    # ---------------------------------------------------------------- report

    def stats(self) -> dict:
        with self._lock:
            bars, first, last = self._db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars").fetchone()
            symbols, downloaded = self._db.execute(
                "SELECT COUNT(*), COUNT(backfilled_from) FROM symbols").fetchone()
            gaps = self._db.execute("SELECT COUNT(*) FROM gaps").fetchone()[0]
        size = 0
        if self.path != ":memory:":
            for suffix in ("", "-wal"):
                try:
                    size += Path(self.path + suffix).stat().st_size
                except OSError:
                    pass
        return {"contracts": int(symbols or 0), "downloaded_once": int(downloaded or 0), "bars": int(bars or 0),
                "first_bar": first, "last_bar": last, "feed_gaps": int(gaps or 0), "megabytes": round(size / 1_048_576, 1)}

    def close(self) -> None:
        with self._lock:
            try:
                self._db.commit()
                self._db.close()
            except sqlite3.Error:
                pass
