"""MCX option chains for the MCX lane's ATM straddle & OI view (owner, 18 Nov 2026: "In commodity lane also provide
straddle, OI charts as in market breadth").

Every CHAIN_REFRESH_SECONDS of the MCX session, one options-chain-v3 call per commodity the lane trades options on
(its futures symbol, the expiry of the lane's own option contracts, ATM +-CHAIN_STRIKES strikes) is stored in
``mcx_chains.sqlite3`` beside the lane's bar database, in the same chain_snapshots / chain_days tables as the NSE
chains. straddle_views then builds the same straddle, anchored VWAPs and OI band from it. Fyers answers MCX chains
with per-strike OI (checked 18 Nov 2026: CRUDEOIL Dec and GOLD Nov). Read-only: nothing here prices or trades.

The spot of a snapshot is the lane's own live futures price when fresh (the future the options exercise into), else
the chain's underlying price. History starts on the first session the recorder runs; nothing is downloaded.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import whale

IST = ZoneInfo("Asia/Kolkata")
CHAIN_REFRESH_SECONDS = 120      # one chain per commodity every two minutes (about 2-3 Fyers calls a minute)
CHAIN_STRIKES = 8                # strikes either side of the money (17 strikes: the OI band reads opening ATM +-3)
CHAIN_PACING_SECONDS = 1.0
SPOT_FRESH_SECONDS = 120
RETENTION_DAYS = 60
SCHEMA = """
CREATE TABLE IF NOT EXISTS chain_snapshots (
  ts INTEGER NOT NULL, underlying TEXT NOT NULL, expiry TEXT NOT NULL, strike REAL NOT NULL,
  option_type TEXT NOT NULL, symbol TEXT NOT NULL, ltp REAL, volume INTEGER, oi INTEGER, spot REAL,
  bid REAL, ask REAL, prev_oi INTEGER, fp REAL, vix REAL, PRIMARY KEY (ts, symbol)
);
CREATE INDEX IF NOT EXISTS idx_chain_underlying_ts ON chain_snapshots(underlying, ts);
-- One contract's marks (chain_warmup.chain_minutes): without it each read scanned the whole table, ~0.65 s a
-- contract, 161 s of the 19 Nov 2026 morning connect.
CREATE INDEX IF NOT EXISTS idx_chain_symbol_ts ON chain_snapshots(symbol, ts);
CREATE TABLE IF NOT EXISTS chain_days (day TEXT NOT NULL, underlying TEXT NOT NULL, PRIMARY KEY (day, underlying));
"""


def chain_path(store_path: str) -> str | None:
    """``mcx_chains.sqlite3`` beside the bar database; None for an in-memory store (tests)."""
    if not store_path or store_path == ":memory:":
        return None
    return str(Path(store_path).with_name("mcx_chains.sqlite3"))


def save(path: str, ts: int, root: str, expiry: str, spot: float, entries, fp: float = 0.0) -> int:
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        rows = [(ts, root, expiry, float(e.strike), e.option_type, e.symbol, float(e.ltp or 0), int(e.volume or 0),
                 int(e.oi or 0), float(spot), float(getattr(e, "bid", 0) or 0), float(getattr(e, "ask", 0) or 0),
                 whale._prev_oi(e), float(fp or 0), None) for e in entries if e.option_type in ("CE", "PE")]
        if not rows:
            return 0
        with connection:
            connection.executemany(
                """INSERT OR REPLACE INTO chain_snapshots (ts, underlying, expiry, strike, option_type, symbol, ltp,
                   volume, oi, spot, bid, ask, prev_oi, fp, vix) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
            connection.execute("INSERT OR IGNORE INTO chain_days (day, underlying) VALUES (?, ?)",
                               (datetime.fromtimestamp(ts, IST).date().isoformat(), root))
        return len(rows)
    finally:
        connection.close()


def prune(path: str, keep_days: int = RETENTION_DAYS, today=None) -> int:
    today = today or datetime.now(IST).date()
    cutoff = today - timedelta(days=keep_days)
    start = int(datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=IST).timestamp())
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.executescript(SCHEMA)
        with connection:
            removed = connection.execute("DELETE FROM chain_snapshots WHERE ts < ?", (start,)).rowcount
            connection.execute("DELETE FROM chain_days WHERE day < ?", (cutoff.isoformat(),))
        return removed
    finally:
        connection.close()


class McxChainRecorder:
    """Paced chain collection for one MCX lane; never raises into the lane."""

    def __init__(self, lane, path: str | None):
        self.lane = lane
        self.path = path
        self._task: asyncio.Task | None = None
        self._last_at = 0.0
        self._pruned_day: str | None = None
        self.report: dict = {"path": path, "last_at": None, "snapshots": 0, "rows": 0, "failed": {}}

    def series(self) -> list[tuple[str, str, str]]:
        """(root, futures symbol, option expiry) for each traded root: the lane's own nearest option series."""
        chosen: dict[str, tuple[str, str]] = {}
        for contract in self.lane.contracts.values():
            if contract.option_type not in ("CE", "PE") or contract.retained or not contract.futures_symbol:
                continue
            if contract.root not in self.lane.roots:
                continue
            current = chosen.get(contract.root)
            if current is None or contract.expiry < current[1]:
                chosen[contract.root] = (contract.futures_symbol, contract.expiry)
        return [(root, fut, expiry) for root, (fut, expiry) in sorted(chosen.items())]

    def spot(self, futures_symbol: str, chain, now: datetime) -> float:
        tick = self.lane.latest.get(futures_symbol)
        if tick is not None and tick.ltp and tick.ltp > 0:
            stamp = tick.timestamp if tick.timestamp.tzinfo else tick.timestamp.replace(tzinfo=UTC)
            if (now - stamp).total_seconds() <= SPOT_FRESH_SECONDS:
                return float(tick.ltp)
        return float(chain.fp or chain.spot_price or 0)

    def schedule(self, now: datetime) -> None:
        reader = getattr(self.lane.broker, "option_chain", None)
        if (self.path is None or not callable(reader) or now.timestamp() - self._last_at < CHAIN_REFRESH_SECONDS
                or (self._task is not None and not self._task.done())):
            return
        series = self.series()
        if not series:
            return
        self._last_at = now.timestamp()
        self._task = asyncio.create_task(self.collect(series, reader))

    async def collect(self, series, reader) -> None:
        day = datetime.now(IST).date().isoformat()
        if self._pruned_day != day:
            self._pruned_day = day
            try:
                await asyncio.to_thread(prune, self.path)
            except Exception as exc:  # noqa: BLE001
                self.report["failed"]["prune"] = f"{type(exc).__name__}: {exc}"[:200]
        for root, futures_symbol, expiry in series:
            try:
                chain = await reader(futures_symbol, strikecount=CHAIN_STRIKES, priority="normal")
                if chain.expiry != expiry:
                    token = next((e.token for e in chain.expiries if e.date == expiry and e.token), None)
                    if token is None:
                        raise RuntimeError(f"expiry {expiry} not listed in the {futures_symbol} chain")
                    chain = await reader(futures_symbol, token, strikecount=CHAIN_STRIKES, priority="normal")
                now = datetime.now(UTC)
                ts = int(now.timestamp())
                rows = await asyncio.to_thread(save, self.path, ts, root, expiry, self.spot(futures_symbol, chain, now),
                                               chain.entries, chain.fp)
                self.report["snapshots"] += 1
                self.report["rows"] += rows
                self.report["last_at"] = now.isoformat()
                self.report["failed"].pop(root, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- a missing chain must not touch prices or orders
                self.report["failed"][root] = f"{type(exc).__name__}: {exc}"[:200]
            await asyncio.sleep(CHAIN_PACING_SECONDS)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
