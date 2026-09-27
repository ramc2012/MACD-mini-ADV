"""Durable capture of the raw tick stream, condensed after a retention window.

Fyers has no trade-by-trade history endpoint — the finest the History API
offers is 5-second OHLCV, and only for 30 trading days (the API itself lists
``5S, 10S, 15S, 30S, 45S, 1, 2, ...`` when handed an invalid resolution). So
unlike minute candles, ticks CANNOT be backfilled: whatever the socket delivers
is the only copy that will ever exist. That is what this module preserves.

Raw ticks are kept for ``retention_days`` (default 2) and then condensed. The
condensation deliberately keeps only what a candle cannot reproduce:

  * aggressor-classified buy/sell volume and delta, via the quote rule
  * the per-session volume-at-price ladder (the volume profile / footprint)
  * classification quality, so a later reader knows how much to trust it
  * spread statistics

OHLC is reproducible from the research store and is kept only because it is
nearly free and makes the condensed rows self-contained.

Sizing: ~250 ticks/sec over a 6.25h session is ~5.6M rows/day. Symbols are
interned into an id table so the symbol text is not repeated on every row.
Condensation collapses that to roughly one row per symbol-minute plus one
ladder row per symbol-price, which measured about a 15-20x reduction.
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import RLock
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
FLUSH_INTERVAL_SECONDS = 5.0
MAX_BUFFER_ROWS = 400_000
DEFAULT_RETENTION_DAYS = 5
# Raw ticks are the only tier that is expensive per unit of insight: ~680 MB a
# session against ~2 MB for the minute rows they condense into. They exist to
# be replayed while a question is fresh, then to become flow. The derived tiers
# are cheap enough to keep for a research year; the session parameters that
# feed the positional lane are never discarded.
DEFAULT_FLOW_RETENTION_DAYS = 365

SCHEMA = """
CREATE TABLE IF NOT EXISTS tick_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS ticks (
    symbol_id INTEGER NOT NULL,
    ts_ms INTEGER NOT NULL,
    ltp REAL NOT NULL,
    cum_volume INTEGER,
    last_qty INTEGER,
    bid REAL,
    ask REAL,
    bid_qty INTEGER,
    ask_qty INTEGER,
    oi INTEGER,
    -- Total pending buy/sell quantity across the book. The strongest aggressor
    -- vote available on this feed reads which of these two fell by the traded
    -- quantity, and without them a recorded session can only ever be
    -- reclassified by the weaker quote and tick rules.
    tbq INTEGER,
    tsq INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ticks_ts ON ticks(ts_ms);
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol_id, ts_ms);

-- One row per symbol-minute. Everything here is derived from ticks and is not
-- recoverable from OHLCV once the raw rows are gone.
CREATE TABLE IF NOT EXISTS tick_minute_flow (
    symbol_id INTEGER NOT NULL,
    minute_ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER NOT NULL DEFAULT 0,
    buy_volume INTEGER NOT NULL DEFAULT 0,
    sell_volume INTEGER NOT NULL DEFAULT 0,
    delta INTEGER NOT NULL DEFAULT 0,
    trades INTEGER NOT NULL DEFAULT 0,
    ticks INTEGER NOT NULL DEFAULT 0,
    vwap REAL,
    quote_n INTEGER NOT NULL DEFAULT 0,
    mid_n INTEGER NOT NULL DEFAULT 0,
    tick_n INTEGER NOT NULL DEFAULT 0,
    zero_tick_n INTEGER NOT NULL DEFAULT 0,
    -- The pending-quantity vote and the votes that contradicted each other get
    -- their own counters. Folding them into mid_n would have made the stored
    -- classification health describe a rule that never ran.
    pending_n INTEGER NOT NULL DEFAULT 0,
    conflict_n INTEGER NOT NULL DEFAULT 0,
    unclassified INTEGER NOT NULL DEFAULT 0,
    avg_spread REAL,
    -- Book-based order-flow imbalance over the minute, and how many level-1
    -- changes contributed. OFI needs no aggressor inference, so unlike delta
    -- it is a measurement rather than an estimate.
    ofi REAL,
    ofi_events INTEGER NOT NULL DEFAULT 0,
    -- Volume-weighted confidence of the aggressor classification. A delta
    -- divergence computed on a low-confidence minute is not a signal.
    avg_confidence REAL,
    PRIMARY KEY (symbol_id, minute_ts)
);
-- The session volume profile: volume traded at each price, split by aggressor.
CREATE TABLE IF NOT EXISTS tick_session_ladder (
    symbol_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    price REAL NOT NULL,
    buy_volume INTEGER NOT NULL DEFAULT 0,
    sell_volume INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol_id, day, price)
);
CREATE TABLE IF NOT EXISTS tick_condensed_days (
    day TEXT PRIMARY KEY,
    condensed_at TEXT NOT NULL,
    raw_ticks INTEGER NOT NULL,
    minute_rows INTEGER NOT NULL,
    ladder_rows INTEGER NOT NULL
);
"""


def _connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=60)
    # NOT WAL: this file lives on a Docker Desktop bind mount, whose
    # virtualised filesystem does not reliably provide the shared-memory and
    # byte-range locking WAL needs. It corrupted the trade book once already.
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(SCHEMA)
    _migrate(connection)
    return connection


# CREATE TABLE IF NOT EXISTS never widens a table that already exists, so a
# column added to SCHEMA reaches new databases only. Every such column needs a
# line here or the running desk keeps the old shape.
FLOW_COLUMNS = (
    ("zero_tick_n", "INTEGER NOT NULL DEFAULT 0"),
    ("pending_n", "INTEGER NOT NULL DEFAULT 0"),
    ("conflict_n", "INTEGER NOT NULL DEFAULT 0"),
    ("ofi", "REAL"),
    ("ofi_events", "INTEGER NOT NULL DEFAULT 0"),
    ("avg_confidence", "REAL"),
)
TICK_COLUMNS = (("tbq", "INTEGER"), ("tsq", "INTEGER"))


def _migrate(connection: sqlite3.Connection) -> None:
    for table, columns in (("tick_minute_flow", FLOW_COLUMNS), ("ticks", TICK_COLUMNS)):
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns:
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    connection.commit()


# Imported, not re-implemented. This module used to carry its own copy that
# claimed in its docstring to "mirror orderflow.classify" and did not: it had
# NO MID_TOLERANCE deadband, so it gave a side to any print off the mid where
# the live path requires 25% of the half-spread, and it accepted a non-positive
# bid. Reconstructing a session with this copy therefore disagreed with the live
# footprint for reasons that had nothing to do with the quote-vs-tick question
# the reconstruction was meant to measure. One function, one rule.
from .ofi import OFIState
from .orderflow import classify, classify_update  # noqa: F401  (classify re-exported)

# The three-vote classifier names its verdicts after how they were decided; the
# minute table's counters predate it and are named after the winning rule.
_METHOD_COLUMN = {
    "quote+pending": "quote", "quote": "quote", "conflict": "conflict",
    "pending": "pending", "tick": "tick", "zero_tick": "zero_tick",
    "unknown": None,
}


class TickStore:
    """Buffers ticks in memory and flushes them off the event loop."""

    def __init__(self, database_path: str, retention_days: int = DEFAULT_RETENTION_DAYS,
                 flow_retention_days: int = DEFAULT_FLOW_RETENTION_DAYS):
        self._path = database_path
        self.retention_days = max(1, int(retention_days))
        self.flow_retention_days = max(self.retention_days, int(flow_retention_days))
        self._lock = RLock()
        self._buffer: list[tuple] = []
        self._symbol_ids: dict[str, int] = {}
        self._task: asyncio.Task | None = None
        self.rows_written = 0
        self.rows_dropped = 0
        self.last_flush_at: datetime | None = None
        self.last_error: str | None = None
        self.last_condense: dict | None = None
        self.last_prune: dict | None = None

    # -- capture -------------------------------------------------------------

    def add(self, tick) -> None:
        """Buffer one tick. Never raises — capture must not break the feed."""
        try:
            ts_ms = int(tick.timestamp.timestamp() * 1000)
        except Exception:  # noqa: BLE001 — a malformed stamp is not worth a crash
            ts_ms = int(datetime.now(UTC).timestamp() * 1000)
        row = (
            tick.symbol, ts_ms, float(tick.ltp),
            _int_or_none(getattr(tick, "volume", None)),
            _int_or_none(getattr(tick, "last_qty", None)),
            _float_or_none(getattr(tick, "bid", None)),
            _float_or_none(getattr(tick, "ask", None)),
            _int_or_none(getattr(tick, "bid_qty", None)),
            _int_or_none(getattr(tick, "ask_qty", None)),
            _int_or_none(getattr(tick, "open_interest", None)),
            _int_or_none(getattr(tick, "total_buy_qty", None)),
            _int_or_none(getattr(tick, "total_sell_qty", None)),
        )
        with self._lock:
            if len(self._buffer) >= MAX_BUFFER_ROWS:
                # Shed the oldest rather than grow without bound. Losing the
                # tail of a stalled flush is better than an OOM mid-session.
                self._buffer.pop(0)
                self.rows_dropped += 1
            self._buffer.append(row)

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

    def _symbol_id(self, connection: sqlite3.Connection, symbol: str) -> int:
        cached = self._symbol_ids.get(symbol)
        if cached is not None:
            return cached
        connection.execute("INSERT OR IGNORE INTO tick_symbols(symbol) VALUES (?)", (symbol,))
        row = connection.execute("SELECT id FROM tick_symbols WHERE symbol = ?", (symbol,)).fetchone()
        self._symbol_ids[symbol] = row[0]
        return row[0]

    def flush_sync(self) -> int:
        with self._lock:
            rows, self._buffer = self._buffer, []
        if not rows:
            return 0
        try:
            connection = _connect(self._path)
            try:
                payload = [(self._symbol_id(connection, r[0]), *r[1:]) for r in rows]
                connection.executemany(
                    """INSERT INTO ticks
                       (symbol_id, ts_ms, ltp, cum_volume, last_qty, bid, ask, bid_qty,
                        ask_qty, oi, tbq, tsq)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    payload,
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
            with self._lock:  # re-buffer so a transient lock does not lose ticks
                self._buffer = rows + self._buffer
            return 0

    # -- condensation --------------------------------------------------------

    def days_pending_condensation(self, now: datetime | None = None) -> list[str]:
        """IST days whose raw ticks are older than the retention window."""
        moment = (now or datetime.now(IST)).astimezone(IST)
        cutoff = (moment - timedelta(days=self.retention_days)).date()
        connection = _connect(self._path)
        try:
            done = {r[0] for r in connection.execute("SELECT day FROM tick_condensed_days")}
            days = set()
            for (day,) in connection.execute(
                "SELECT DISTINCT date(ts_ms / 1000, 'unixepoch', '+5 hours', '+30 minutes') FROM ticks"
            ):
                if day and day not in done and datetime.fromisoformat(day).date() <= cutoff:
                    days.add(day)
            return sorted(days)
        finally:
            connection.close()

    def condense_day(self, day: str) -> dict:
        """Roll one IST day of raw ticks into flow rows, then delete the raw."""
        start = int(datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=IST).timestamp() * 1000)
        end = start + 86_400_000
        connection = _connect(self._path)
        try:
            minute: dict[tuple[int, int], dict] = {}
            ladder: dict[tuple[int, float], list[int]] = {}
            # Last non-zero side per symbol, for the zero-tick rule — the same
            # carry-forward the live tracker keeps on FlowState.last_side.
            previous_side: dict[int, int] = {}
            previous_price: dict[int, float] = {}
            previous_volume: dict[int, int] = {}
            # The book, and the pending totals, as they stood BEFORE the update
            # being classified -- the ones its trades hit.
            previous_book: dict[int, tuple] = {}
            previous_pending: dict[int, tuple] = {}
            ofi_states: dict[int, OFIState] = {}
            raw = 0

            cursor = connection.execute(
                """SELECT symbol_id, ts_ms, ltp, cum_volume, last_qty, bid, ask,
                          bid_qty, ask_qty, tbq, tsq
                   FROM ticks WHERE ts_ms >= ? AND ts_ms < ? ORDER BY symbol_id, ts_ms""",
                (start, end),
            )
            for (symbol_id, ts_ms, ltp, cum_volume, last_qty, bid, ask,
                 bid_qty, ask_qty, tbq, tsq) in cursor:
                raw += 1
                minute_ts = (ts_ms // 60_000) * 60
                bucket = minute.setdefault((symbol_id, minute_ts), _empty_minute())
                bucket["ticks"] += 1
                if bucket["open"] is None:
                    bucket["open"] = ltp
                bucket["close"] = ltp
                bucket["high"] = ltp if bucket["high"] is None else max(bucket["high"], ltp)
                bucket["low"] = ltp if bucket["low"] is None else min(bucket["low"], ltp)
                if bid is not None and ask is not None and ask >= bid:
                    bucket["spread_sum"] += ask - bid
                    bucket["spread_n"] += 1

                # Book pressure, measured not inferred. Folded in before the
                # trade branch because a quote-only update carries no trade but
                # is still a change in the book.
                ofi_state = ofi_states.get(symbol_id)
                if ofi_state is None:
                    ofi_state = ofi_states[symbol_id] = OFIState(symbol=str(symbol_id))
                contribution = ofi_state.update(ts_ms / 1000.0, bid, ask, bid_qty, ask_qty)
                if contribution is not None:
                    bucket["ofi"] += contribution
                    bucket["ofi_events"] += 1

                prior_book = previous_book.get(symbol_id)
                prior_pending = previous_pending.get(symbol_id)
                previous_book[symbol_id] = (bid, ask)
                previous_pending[symbol_id] = (tbq, tsq)

                # Traded size: prefer last_qty, else the cumulative-volume delta.
                size = 0
                if last_qty:
                    size = max(0, int(last_qty))
                elif cum_volume is not None:
                    prior = previous_volume.get(symbol_id)
                    if prior is not None:
                        size = max(0, int(cum_volume) - prior)
                if cum_volume is not None:
                    previous_volume[symbol_id] = int(cum_volume)

                if size <= 0:
                    # Not a trade (quote/OI update). It must NOT advance the
                    # tick-rule reference, or the next real print is compared
                    # against its own echo and lands on the equality branch —
                    # the same defect the live path had.
                    continue
                # Against the PRIOR book: classifying a lift at the ask against
                # the book it just moved reads it as a hit on the bid.
                prior_bid, prior_ask = prior_book if prior_book else (None, None)
                buy_change = sell_change = None
                if prior_pending is not None:
                    prior_tbq, prior_tsq = prior_pending
                    if tbq is not None and prior_tbq is not None:
                        buy_change = tbq - prior_tbq
                    if tsq is not None and prior_tsq is not None:
                        sell_change = tsq - prior_tsq
                verdict = classify_update(
                    price=ltp, traded=size, last_qty=last_qty,
                    prior_bid=prior_bid, prior_ask=prior_ask,
                    prior_price=previous_price.get(symbol_id),
                    last_side=previous_side.get(symbol_id, 0),
                    buy_pending_change=buy_change, sell_pending_change=sell_change)
                side, method = verdict.side, _METHOD_COLUMN[verdict.method]
                bucket["conf_sum"] += verdict.confidence * size
                bucket["conf_volume"] += size
                previous_price[symbol_id] = ltp
                if side:
                    previous_side[symbol_id] = side

                bucket["trades"] += 1
                bucket["volume"] += size
                bucket["notional"] += size * ltp
                # A sideless verdict has no method counter of its own: the
                # side check below books it as unclassified, once.
                if method is not None:
                    bucket[f"{method}_n"] += 1
                if side > 0:
                    bucket["buy_volume"] += size
                elif side < 0:
                    bucket["sell_volume"] += size
                else:
                    bucket["unclassified"] += 1
                key = (symbol_id, round(ltp, 2))
                cell = ladder.setdefault(key, [0, 0])
                # Classified size only. This was `side >= 0`, which booked every
                # UNCLASSIFIED print as a BUY — inventing one-sided pressure in
                # exactly the zero-tick zone where classification is weakest.
                # The ladder's two columns are buy/sell by schema, so an
                # unclassified print belongs in neither; its size is still
                # counted in the minute row's own volume, and the print in
                # `unclassified`, so the residual stays visible.
                if side > 0:
                    cell[0] += size
                elif side < 0:
                    cell[1] += size

            minute_rows = [
                (sid, ts, b["open"], b["high"], b["low"], b["close"], b["volume"],
                 b["buy_volume"], b["sell_volume"], b["buy_volume"] - b["sell_volume"],
                 b["trades"], b["ticks"],
                 round(b["notional"] / b["volume"], 4) if b["volume"] else None,
                 b["quote_n"], b["mid_n"], b["tick_n"], b["zero_tick_n"],
                 b["pending_n"], b["conflict_n"], b["unclassified"],
                 round(b["spread_sum"] / b["spread_n"], 4) if b["spread_n"] else None,
                 round(b["ofi"], 2), b["ofi_events"],
                 round(b["conf_sum"] / b["conf_volume"], 4) if b["conf_volume"] else None)
                for (sid, ts), b in minute.items()
            ]
            ladder_rows = [(sid, day, price, v[0], v[1]) for (sid, price), v in ladder.items()]

            connection.executemany(
                """INSERT OR REPLACE INTO tick_minute_flow
                   (symbol_id, minute_ts, open, high, low, close, volume, buy_volume,
                    sell_volume, delta, trades, ticks, vwap, quote_n, mid_n, tick_n,
                    zero_tick_n, pending_n, conflict_n, unclassified, avg_spread,
                    ofi, ofi_events, avg_confidence)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                minute_rows,
            )
            connection.executemany(
                """INSERT OR REPLACE INTO tick_session_ladder
                   (symbol_id, day, price, buy_volume, sell_volume) VALUES (?,?,?,?,?)""",
                ladder_rows,
            )
            connection.execute("DELETE FROM ticks WHERE ts_ms >= ? AND ts_ms < ?", (start, end))
            connection.execute(
                """INSERT OR REPLACE INTO tick_condensed_days
                   (day, condensed_at, raw_ticks, minute_rows, ladder_rows) VALUES (?,?,?,?,?)""",
                (day, datetime.now(UTC).isoformat(), raw, len(minute_rows), len(ladder_rows)),
            )
            connection.commit()
            result = {"day": day, "raw_ticks": raw, "minute_rows": len(minute_rows),
                      "ladder_rows": len(ladder_rows)}
            self.last_condense = result
            return result
        finally:
            connection.close()

    def prune_flow(self, now: datetime | None = None) -> dict:
        """Drop derived flow past its own, much longer, retention window.

        Raw ticks and flow age out on different clocks on purpose: the raw tier
        is bulk, the derived tier is the research record. Session and period
        profiles are never pruned here — they are the positional lane's memory.
        """
        moment = (now or datetime.now(IST)).astimezone(IST)
        cutoff = (moment - timedelta(days=self.flow_retention_days)).date().isoformat()
        connection = _connect(self._path)
        try:
            minute_cutoff = int(datetime.fromisoformat(f"{cutoff}T00:00:00").replace(tzinfo=IST).timestamp())
            minutes = connection.execute(
                "DELETE FROM tick_minute_flow WHERE minute_ts < ?", (minute_cutoff,)).rowcount
            ladders = connection.execute(
                "DELETE FROM tick_session_ladder WHERE day < ?", (cutoff,)).rowcount
            connection.commit()
            self.last_prune = {"cutoff": cutoff, "minute_rows": minutes, "ladder_rows": ladders}
            return self.last_prune
        finally:
            connection.close()

    def condense_pending(self, now: datetime | None = None) -> list[dict]:
        return [self.condense_day(day) for day in self.days_pending_condensation(now)]

    def vacuum(self) -> None:
        """Reclaim the file space freed by deleting condensed raw ticks."""
        connection = sqlite3.connect(self._path, timeout=120)
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()

    # -- reporting -----------------------------------------------------------

    def status(self, *, include_database_counts: bool = True) -> dict:
        """Return writer health, optionally including expensive archive counts.

        Counting every row in a multi-gigabyte tick archive can take tens of
        seconds. The live system-health endpoint needs writer freshness and
        errors, not an exact archive inventory, so it deliberately skips those
        scans. Explicit diagnostics retain the exact-count default.
        """
        with self._lock:
            pending = len(self._buffer)
        info = {"pending_rows": pending, "rows_written": self.rows_written,
                "rows_dropped": self.rows_dropped, "retention_days": self.retention_days,
                "flow_retention_days": self.flow_retention_days,
                "last_flush_at": self.last_flush_at.isoformat() if self.last_flush_at else None,
                "last_error": self.last_error, "last_condense": self.last_condense,
                "last_prune": self.last_prune}
        if not include_database_counts:
            return info
        try:
            connection = _connect(self._path)
            try:
                info["raw_ticks"] = connection.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
                info["symbols"] = connection.execute("SELECT COUNT(*) FROM tick_symbols").fetchone()[0]
                info["condensed_days"] = connection.execute(
                    "SELECT COUNT(*) FROM tick_condensed_days").fetchone()[0]
                info["minute_rows"] = connection.execute(
                    "SELECT COUNT(*) FROM tick_minute_flow").fetchone()[0]
            finally:
                connection.close()
        except sqlite3.Error as exc:
            info["last_error"] = str(exc)
        return info


def _empty_minute() -> dict:
    # A key per method classify() can return. A missing one is not a default of
    # zero, it is a KeyError inside condense_day that aborts the whole day --
    # which is exactly how "zero_tick" silently froze every derived tier for a
    # week after it was added to the classifier.
    return {"open": None, "high": None, "low": None, "close": None, "volume": 0,
            "buy_volume": 0, "sell_volume": 0, "trades": 0, "ticks": 0, "notional": 0.0,
            "quote_n": 0, "mid_n": 0, "tick_n": 0, "zero_tick_n": 0,
            "pending_n": 0, "conflict_n": 0, "unclassified": 0,
            "spread_sum": 0.0, "spread_n": 0,
            "ofi": 0.0, "ofi_events": 0, "conf_sum": 0.0, "conf_volume": 0.0}


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
