from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Iterable

from .models import ClosedPosition, Order, Position, Signal, Trade


def _encode(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError


class TradeRepository:
    def __init__(self, path: str):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(target, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        # NOT WAL. These databases live on a Docker Desktop bind mount, whose
        # virtualised filesystem does not reliably provide the shared-memory
        # and byte-range locking that WAL depends on. Running WAL here
        # corrupted mp_trader.sqlite3 ("database disk image is malformed")
        # after a series of mid-write container restarts. The rollback journal
        # with synchronous=FULL is slower per commit but survives a hard kill,
        # which matters more for a durable trade book than write throughput.
        self._connection.execute("PRAGMA journal_mode=DELETE")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS equity_points (
                    timestamp TEXT PRIMARY KEY,
                    equity REAL NOT NULL,
                    cash REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL
                );
                -- Lookup index for the per-bar duplicate check. Deliberately
                -- NOT UNIQUE: existing books already hold duplicates written by
                -- the very bug this guards against, and a unique index cannot
                -- be created over them — it would fail at startup. Uniqueness
                -- is enforced in save_signal instead.
                CREATE INDEX IF NOT EXISTS idx_signal_bar ON signals(
                    json_extract(payload, '$.symbol'),
                    json_extract(payload, '$.kind'),
                    json_extract(payload, '$.evaluated_candle_timestamp')
                );
                CREATE TABLE IF NOT EXISTS mp_session_state (
                    day TEXT PRIMARY KEY,
                    saved_at TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rrg_snapshots (
                    key TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                -- One row per closing slice. exit_time and visible_until are
                -- stored UTC-normalised so lexical ORDER/WHERE is correct; the
                -- JSON payload keeps the +05:30 form for display.
                CREATE TABLE IF NOT EXISTS closed_positions (
                    closed_id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    lane TEXT NOT NULL DEFAULT 'macd',
                    exit_time TEXT NOT NULL,
                    visible_until TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_closed_visible ON closed_positions(lane, visible_until);
                -- One row per OPEN position. Trade replay only sees fill
                -- prints, so without this a restart forgot every mark in
                -- between and silently disarmed the trailing stop.
                CREATE TABLE IF NOT EXISTS position_excursions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    max_price REAL NOT NULL,
                    min_price REAL NOT NULL,
                    max_return_pct REAL NOT NULL,
                    min_return_pct REAL NOT NULL,
                    entry_fees REAL NOT NULL DEFAULT 0,
                    entry_anchor REAL NOT NULL DEFAULT 0,
                    entry_stage INTEGER NOT NULL DEFAULT 0,
                    exit_stage INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
                CREATE INDEX IF NOT EXISTS idx_trades_order ON trades(json_extract(payload, '$.order_id'));
                """
            )
            # Existing books have the original excursion table. Defaults of
            # zero identify rows written before staged state was persisted.
            columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(position_excursions)")}
            for name, definition in (
                ("entry_anchor", "REAL NOT NULL DEFAULT 0"),
                ("entry_stage", "INTEGER NOT NULL DEFAULT 0"),
                ("exit_stage", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    self._connection.execute(f"ALTER TABLE position_excursions ADD COLUMN {name} {definition}")
            # Closed MP positions, keyed by the exit fill. Retention is a
            # column rather than a schedule so the row's lifetime survives a
            # restart and the date-keyed session reset that clears the desk's
            # in-memory state. Both stamps are written with the fixed +05:30
            # offset, which is what lets the lexical string comparisons in
            # closed_positions/purge_closed_positions be chronological.
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mp_closed_positions (
                    id TEXT PRIMARY KEY,
                    exit_time TEXT NOT NULL,
                    visible_until TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mp_closed_visible ON mp_closed_positions(visible_until);
                """
            )
            # The blast lane's decision journal: one row per evaluated
            # candidate, taken or not. Rejected rows are the control group --
            # the whole point is being able to ask afterwards whether each leg
            # of the screen earned its place, which is impossible if only the
            # trades are kept. watch_* carry the forward excursion the lane
            # tracks for the candidates it declined as well as the ones it
            # bought; resolved flips to 1 when the horizon expires.
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS blast_journal (
                    id TEXT PRIMARY KEY,
                    at TEXT NOT NULL,
                    day TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    bar_timestamp INTEGER,
                    premium REAL,
                    spot REAL,
                    premium_pct REAL,
                    breadth REAL,
                    off_high_pct REAL,
                    taken INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    order_id TEXT,
                    watch_until TEXT,
                    watch_high REAL,
                    watch_low REAL,
                    mfe_pct REAL,
                    mae_pct REAL,
                    resolved INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_blast_journal_day ON blast_journal(day, at);
                CREATE INDEX IF NOT EXISTS idx_blast_journal_open ON blast_journal(resolved, watch_until);
                -- The contract behind every blast entry, kept by the lane
                -- itself. The contract selector only knows today's band, so a
                -- held contract that rolled out of it would otherwise lose the
                -- expiry the lane has to flatten it on.
                CREATE TABLE IF NOT EXISTS blast_contracts (
                    symbol TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    def save_order(self, order: Order) -> None:
        payload = json.dumps(asdict(order), default=_encode, separators=(",", ":"))
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO orders(order_id, created_at, payload) VALUES (?, ?, ?)",
                (order.order_id, order.created_at.isoformat(), payload),
            )

    def load_open_orders(self) -> list[Order]:
        """Restore resting paper tickets and repair old partially written fills."""
        restored: list[Order] = []
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT payload FROM orders WHERE json_extract(payload, '$.status') = 'OPEN' "
                "ORDER BY created_at, order_id"
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                fill = self._connection.execute(
                    "SELECT payload FROM trades WHERE json_extract(payload, '$.order_id') = ? LIMIT 1",
                    (payload["order_id"],),
                ).fetchone()
                if fill:
                    # Earlier versions committed each fill write separately.
                    # A crash after the trade insert could leave its ticket
                    # OPEN. A trade must never be applied twice on restart.
                    payload["status"] = "FILLED"
                    payload["fill_price"] = json.loads(fill["payload"])["price"]
                    self._connection.execute(
                        "UPDATE orders SET payload = ? WHERE order_id = ?",
                        (json.dumps(payload, separators=(",", ":")), payload["order_id"]),
                    )
                    continue
                payload["created_at"] = datetime.fromisoformat(payload["created_at"])
                restored.append(Order(**payload))
        return restored

    def iter_trade_rows(self, batch_size: int = 1000):
        """Yield the complete trade log in replay order without a row cap."""
        last_timestamp, last_rowid = "", 0
        while True:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT rowid, timestamp, payload FROM trades "
                    "WHERE (timestamp, rowid) > (?, ?) ORDER BY timestamp, rowid LIMIT ?",
                    (last_timestamp, last_rowid, batch_size),
                ).fetchall()
            if not rows:
                return
            for row in rows:
                yield json.loads(row["payload"])
            last_timestamp, last_rowid = rows[-1]["timestamp"], rows[-1]["rowid"]

    def save_trade(self, trade: Trade) -> None:
        payload = json.dumps(asdict(trade), default=_encode, separators=(",", ":"))
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO trades(trade_id, timestamp, payload) VALUES (?, ?, ?)",
                (trade.trade_id, trade.timestamp.isoformat(), payload),
            )

    def save_signal(self, signal: Signal) -> bool:
        """Persist a signal. False means this bar already fired.

        The strategy's in-memory guard is rebuilt on every feed reconnect, so
        after a disconnect the same bar could fire again. The durable unique
        index below is what actually makes one-signal-per-bar hold across
        restarts and reconnects — callers must honour the return value before
        counting or executing.
        """
        payload = json.dumps(asdict(signal), default=_encode, separators=(",", ":"))
        bar = signal.evaluated_candle_timestamp
        with self._lock, self._connection:
            if bar is not None:
                existing = self._connection.execute(
                    """SELECT 1 FROM signals
                       WHERE json_extract(payload, '$.symbol') = ?
                         AND json_extract(payload, '$.kind') = ?
                         AND json_extract(payload, '$.evaluated_candle_timestamp') = ?
                       LIMIT 1""",
                    (signal.symbol, signal.kind, bar),
                ).fetchone()
                if existing:
                    return False
            self._connection.execute(
                "INSERT OR REPLACE INTO signals(signal_id, timestamp, payload) VALUES (?, ?, ?)",
                (signal.signal_id, signal.timestamp.isoformat(), payload),
            )
            return True

    def save_equity_point(self, snapshot: dict) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO equity_points(timestamp, equity, cash, realized_pnl, unrealized_pnl) VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now().astimezone().isoformat(),
                    float(snapshot.get("equity", 0)),
                    float(snapshot.get("cash", 0)),
                    float(snapshot.get("realized_pnl", 0)),
                    float(snapshot.get("unrealized_pnl", 0)),
                ),
            )

    def equity_rows(self, limit: int = 5000) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT timestamp, equity, cash, realized_pnl, unrealized_pnl FROM equity_points ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def last_equity_before(self, iso_timestamp: str) -> float | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT equity FROM equity_points WHERE timestamp < ? ORDER BY timestamp DESC LIMIT 1",
                (iso_timestamp,),
            ).fetchone()
        return float(row["equity"]) if row else None

    def first_equity_on_or_after(self, iso_timestamp: str) -> float | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT equity FROM equity_points WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT 1",
                (iso_timestamp,),
            ).fetchone()
        return float(row["equity"]) if row else None

    def save_rrg(self, key: str, payload: dict) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO rrg_snapshots(key, generated_at, payload) VALUES (?, ?, ?)",
                (key, str(payload.get("generated_at", "")), json.dumps(payload, separators=(",", ":"))),
            )

    def load_rrg(self, key: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM rrg_snapshots WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_session_state(self, day: str, payload: dict) -> int:
        """Store the desk's live aggregation, gzipped — it is mostly repeated
        price keys and compresses by roughly an order of magnitude."""
        import gzip
        # Level 3 keeps snapshots compact while avoiding level-9 CPU spikes on
        # the live service's frequent, multi-symbol state checkpoints.
        blob = gzip.compress(
            json.dumps(payload, separators=(",", ":")).encode(),
            compresslevel=3,
        )
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO mp_session_state(day, saved_at, payload) VALUES (?, ?, ?)",
                (day, datetime.now().astimezone().isoformat(), blob),
            )
        return len(blob)

    def load_session_state(self, day: str) -> dict | None:
        import gzip
        with self._lock:
            row = self._connection.execute(
                "SELECT saved_at, payload, LENGTH(payload) AS bytes FROM mp_session_state WHERE day = ?", (day,)
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(gzip.decompress(row["payload"]))
            payload["_saved_at"] = row["saved_at"]
            payload["_saved_bytes"] = int(row["bytes"] or 0)
            return payload
        except (OSError, ValueError):
            return None

    def save_closed_position(self, closed: ClosedPosition) -> None:
        payload = json.dumps(asdict(closed), default=_encode, separators=(",", ":"))
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO closed_positions"
                "(closed_id, position_id, symbol, lane, exit_time, visible_until, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    closed.closed_id, closed.position_id, closed.symbol, closed.lane,
                    closed.exit_time.astimezone(UTC).isoformat(),
                    closed.visible_until.astimezone(UTC).isoformat(), payload,
                ),
            )

    def closed_positions(self, *, lane: str | None = None, visible_after: datetime | None = None,
                         limit: int = 500) -> list[dict]:
        """Newest first. ``visible_after=now`` returns only what the desk still shows."""
        clauses: list[str] = []
        params: list = []
        if lane:
            clauses.append("lane = ?")
            params.append(lane)
        if visible_after is not None:
            clauses.append("visible_until > ?")
            params.append(visible_after.astimezone(UTC).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT payload FROM closed_positions {where} ORDER BY exit_time DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def purge_closed_positions(self, older_than: datetime) -> int:
        """Explicit retention sweep only. The desk hides by visible_until and
        keeps the rows: MFE/MAE per round trip is the exit-research data this
        lane has been missing."""
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM closed_positions WHERE visible_until <= ?",
                (older_than.astimezone(UTC).isoformat(),),
            )
            return int(cursor.rowcount or 0)

    def _write_position_excursions(self, positions: Iterable[Position]) -> None:
        """Rewrite all open position checkpoints inside the caller's transaction."""
        now = datetime.now(UTC).isoformat()
        rows = [
            (
                position.position_id, position.symbol, now, position.max_price, position.min_price,
                position.max_return_pct, position.min_return_pct, position.entry_fees,
                position.entry_anchor, position.entry_stage, position.exit_stage,
            )
            for position in positions
        ]
        if not rows:
            self._connection.execute("DELETE FROM position_excursions")
            return
        self._connection.executemany(
            "INSERT OR REPLACE INTO position_excursions"
            "(position_id, symbol, updated_at, max_price, min_price, max_return_pct, min_return_pct,"
            " entry_fees, entry_anchor, entry_stage, exit_stage)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        keep = [row[0] for row in rows]
        self._connection.execute(
            f"DELETE FROM position_excursions WHERE position_id NOT IN ({','.join('?' * len(keep))})",
            keep,
        )

    def save_position_excursions(self, positions: Iterable[Position]) -> None:
        """Checkpoint open positions in one commit, dropping closed rows."""
        with self._lock, self._connection:
            self._write_position_excursions(positions)

    def save_paper_fill(self, order: Order, trade: Trade, positions: Iterable[Position],
                        closed: ClosedPosition | None, snapshot: dict) -> None:
        """Commit every durable effect of one paper fill as a unit."""
        order_payload = json.dumps(asdict(order), default=_encode, separators=(",", ":"))
        trade_payload = json.dumps(asdict(trade), default=_encode, separators=(",", ":"))
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO orders(order_id, created_at, payload) VALUES (?, ?, ?)",
                (order.order_id, order.created_at.isoformat(), order_payload),
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO trades(trade_id, timestamp, payload) VALUES (?, ?, ?)",
                (trade.trade_id, trade.timestamp.isoformat(), trade_payload),
            )
            if closed is not None:
                closed_payload = json.dumps(asdict(closed), default=_encode, separators=(",", ":"))
                self._connection.execute(
                    "INSERT OR REPLACE INTO closed_positions"
                    "(closed_id, position_id, symbol, lane, exit_time, visible_until, payload)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        closed.closed_id, closed.position_id, closed.symbol, closed.lane,
                        closed.exit_time.astimezone(UTC).isoformat(),
                        closed.visible_until.astimezone(UTC).isoformat(), closed_payload,
                    ),
                )
            self._write_position_excursions(positions)
            self._connection.execute(
                "INSERT OR REPLACE INTO equity_points(timestamp, equity, cash, realized_pnl, unrealized_pnl)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now().astimezone().isoformat(),
                    float(snapshot.get("equity", 0)), float(snapshot.get("cash", 0)),
                    float(snapshot.get("realized_pnl", 0)), float(snapshot.get("unrealized_pnl", 0)),
                ),
            )

    def load_position_excursions(self) -> dict[str, dict]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM position_excursions").fetchall()
        return {row["position_id"]: dict(row) for row in rows}
    def save_mp_closed_position(self, record: dict) -> None:
        """Store one auction round trip.

        exit_time and visible_until are UTC-normalised on the way in, exactly
        as save_closed_position does for the other lane, because both columns
        are compared as STRINGS by the reader and the purge. This one used to
        write the record's own IST-offset stamp, so a same-date comparison
        against a UTC "now" put 02:30+00:00 below 07:36+05:30 and the desk lost
        its closed book on every restart from midnight IST onward.
        """
        payload = json.dumps(record, default=_encode, separators=(",", ":"))
        stamp = lambda value: datetime.fromisoformat(value).astimezone(UTC).isoformat()  # noqa: E731
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO mp_closed_positions(id, exit_time, visible_until, payload) "
                "VALUES (?, ?, ?, ?)",
                (record["id"], stamp(record["exit_time"]), stamp(record["visible_until"]), payload),
            )

    def mp_closed_positions(self, now_iso: str, limit: int = 200) -> list[dict]:
        """Closed MP positions still inside their retention window, newest first."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM mp_closed_positions WHERE visible_until > ? "
                "ORDER BY exit_time DESC LIMIT ?",
                (now_iso, limit),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def purge_mp_closed_positions(self, now_iso: str) -> int:
        with self._lock, self._connection:
            return self._connection.execute(
                "DELETE FROM mp_closed_positions WHERE visible_until <= ?", (now_iso,)
            ).rowcount

    def save_blast_journal(self, row: dict) -> None:
        self.save_blast_journal_many([row])

    def save_blast_journal_many(self, rows: Iterable[dict]) -> None:
        """Insert journal rows in one commit."""
        values = [
            (
                row["id"], row["at"], row["day"], row["symbol"], row.get("bar_timestamp"),
                row.get("premium"), row.get("spot"), row.get("premium_pct"), row.get("breadth"),
                row.get("off_high_pct"), int(row.get("taken", 0)), row["reason"],
                row.get("order_id"), row.get("watch_until"), row.get("premium"), row.get("premium"),
                json.dumps(row, default=_encode, separators=(",", ":")),
            )
            for row in rows
        ]
        if not values:
            return
        with self._lock, self._connection:
            self._connection.executemany(
                "INSERT OR REPLACE INTO blast_journal"
                "(id, at, day, symbol, bar_timestamp, premium, spot, premium_pct, breadth,"
                " off_high_pct, taken, reason, order_id, watch_until, watch_high, watch_low, payload)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )

    def update_blast_journal_excursions(self, updates: Iterable[tuple]) -> None:
        """``(id, high, low, mfe_pct, mae_pct, resolved)`` per row, in one commit."""
        rows = list(updates)
        if not rows:
            return
        with self._lock, self._connection:
            self._connection.executemany(
                "UPDATE blast_journal SET watch_high = ?, watch_low = ?, mfe_pct = ?, mae_pct = ?,"
                " resolved = ? WHERE id = ?",
                [(high, low, mfe, mae, resolved, key) for key, high, low, mfe, mae, resolved in rows],
            )

    def blast_journal_unresolved(self, now_iso: str, limit: int = 5_000) -> list[dict]:
        """Candidates still inside their forward-tracking window, for restart."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, symbol, premium, watch_high, watch_low, watch_until FROM blast_journal"
                " WHERE resolved = 0 AND watch_until IS NOT NULL AND watch_until > ?"
                " ORDER BY at DESC LIMIT ?",
                (now_iso, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def blast_journal(self, *, day: str | None = None, taken_only: bool = False,
                      reason: str | None = None, limit: int = 500) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if day:
            clauses.append("day = ?")
            params.append(day)
        if taken_only:
            clauses.append("taken = 1")
        if reason:
            clauses.append("reason = ?")
            params.append(reason)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT payload, mfe_pct, mae_pct, resolved FROM blast_journal {where}"
                " ORDER BY at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        out = []
        for row in rows:
            record = json.loads(row["payload"])
            record.update(mfe_pct=row["mfe_pct"], mae_pct=row["mae_pct"], resolved=bool(row["resolved"]))
            out.append(record)
        return out

    def blast_journal_days(self, limit: int = 30) -> list[str]:
        """Sessions that have journal rows, newest first."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT day FROM blast_journal ORDER BY day DESC LIMIT ?", (limit,),
            ).fetchall()
        return [row["day"] for row in rows]

    def save_blast_contract(self, symbol: str, context: dict) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO blast_contracts(symbol, updated_at, payload) VALUES (?, ?, ?)",
                (symbol, datetime.now(UTC).isoformat(), json.dumps(context, default=_encode, separators=(",", ":"))),
            )

    def load_blast_contracts(self) -> dict[str, dict]:
        with self._lock:
            rows = self._connection.execute("SELECT symbol, payload FROM blast_contracts").fetchall()
        return {row["symbol"]: json.loads(row["payload"]) for row in rows}

    def blast_journal_summary(self, day: str | None = None) -> dict:
        """Counts by verdict plus the excursion of everything tracked so far."""
        where, params = ("WHERE day = ?", [day]) if day else ("", [])
        with self._lock:
            verdicts = self._connection.execute(
                f"SELECT reason, COUNT(*) AS n, AVG(mfe_pct) AS mfe, AVG(mae_pct) AS mae,"
                f" SUM(resolved) AS resolved FROM blast_journal {where} GROUP BY reason ORDER BY n DESC",
                params,
            ).fetchall()
            totals = self._connection.execute(
                f"SELECT COUNT(*) AS n, SUM(taken) AS taken FROM blast_journal {where}", params,
            ).fetchone()
        return {
            "day": day,
            "evaluated": int(totals["n"] or 0),
            "taken": int(totals["taken"] or 0),
            "verdicts": [
                {
                    "reason": row["reason"], "count": int(row["n"] or 0),
                    "resolved": int(row["resolved"] or 0),
                    "mean_mfe_pct": row["mfe"], "mean_mae_pct": row["mae"],
                }
                for row in verdicts
            ],
        }

    def rows(self, table: str, limit: int = 200) -> list[dict]:
        if table not in {"orders", "trades", "signals"}:
            raise ValueError("invalid table")
        sort_column = "created_at" if table == "orders" else "timestamp"
        with self._lock:
            rows = self._connection.execute(
                f"SELECT payload FROM {table} ORDER BY {sort_column} DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
