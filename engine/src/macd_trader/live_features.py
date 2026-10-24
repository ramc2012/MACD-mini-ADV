"""Live features, published as they form: phase 1 of the shared platform (research/architecture_20261024/
REALTIME_PLATFORM.md).

Order flow is computed per tick by the desk's OrderFlowTracker; until now its minute rows lived only in process memory,
so no other process, app or a restarted desk could read the session's flow without re-deriving it from raw ticks.
This publishes, from that same tracker and profile book:

  ft.flow.1m.<symbol>     each CLOSED minute: close, volume, buy, sell, delta, CVD at close, session totals, burst
  ft.profile.1m.<symbol>  each minute: POC, value area, IB, high/low, open type, day type (desk universe)
  KV ft_latest            the latest flow snapshot per symbol ("flow.<symbol>"), for late joiners
  fts.flow.<symbol>       the same snapshot on a plain subject, every 2 s for symbols that traded (the engine reads it)

The two ft.* families go into a JetStream stream (FT, 3 days, de-duplicated by message id), so a consumer that
restarts replays the session; closed minutes are also written to live_flow_minutes in the tick store as they close.
Feature contract version FEATURE_VERSION; every message carries computed_at_ms so the latency meter can time it.

The latency meter (LatencyMeter) times each hop of the live path per minute -- exchange -> engine receipt ("feed"),
engine receipt -> desk delivery ("bus"), desk processing of one tick ("desk"), minute close -> publish ("feature") --
and keeps p50/p95/p99/max per minute in memory and in latency_minutes.
"""
from __future__ import annotations

import asyncio
import random
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import orjson

IST = ZoneInfo("Asia/Kolkata")
FEATURE_VERSION = 1
STREAM = "FT"
STREAM_SUBJECTS = ["ft.flow.1m.>", "ft.profile.1m.>"]
KV_BUCKET = "ft_latest"
SNAPSHOT_PREFIX = "fts.flow."
PUBLISH_EVERY_SECONDS = 2.0
PROFILE_EVERY_SECONDS = 60.0
RESERVOIR = 4096
HOPS = ("feed", "bus", "desk", "feature")

LIVE_FLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_flow_minutes (
  symbol TEXT NOT NULL, minute_ts INTEGER NOT NULL, close REAL, volume REAL, buy_volume REAL, sell_volume REAL,
  delta REAL, cvd REAL, published_at_ms INTEGER,
  PRIMARY KEY (symbol, minute_ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS latency_minutes (
  minute_ts INTEGER NOT NULL, hop TEXT NOT NULL, n INTEGER, p50 REAL, p95 REAL, p99 REAL, max REAL,
  PRIMARY KEY (minute_ts, hop)
) WITHOUT ROWID;
"""


def subject_token(symbol: str) -> str:
    return "".join("_" if ch in ".*> \t\r\n" else ch for ch in symbol)


def _quantiles(samples: list[float]) -> dict:
    if not samples:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(samples)
    pick = lambda q: round(ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))], 2)  # noqa: E731
    return {"n": len(ordered), "p50": pick(0.5), "p95": pick(0.95), "p99": pick(0.99), "max": round(ordered[-1], 2)}


class LatencyMeter:
    """Per-hop latency samples, summarised per minute (reservoir sampled, so a busy minute costs bounded memory)."""

    def __init__(self):
        self._minute = int(time.time() // 60 * 60)
        self._samples: dict[str, list[float]] = {hop: [] for hop in HOPS}
        self._seen: dict[str, int] = {hop: 0 for hop in HOPS}
        self._session: dict[str, list[float]] = {hop: [] for hop in HOPS}
        self._session_seen: dict[str, int] = {hop: 0 for hop in HOPS}
        self._session_day: str | None = None
        self.closed: list[tuple] = []          # (minute_ts, hop, n, p50, p95, p99, max) awaiting the store
        self.last_minute: dict = {}

    def _keep(self, bucket: list[float], seen: int, value: float) -> None:
        if len(bucket) < RESERVOIR:
            bucket.append(value)
        else:
            slot = random.randrange(seen)
            if slot < RESERVOIR:
                bucket[slot] = value

    def add(self, hop: str, millis: float, now: float | None = None) -> None:
        now = time.time() if now is None else now
        minute = int(now // 60 * 60)
        if minute != self._minute:
            self.roll(minute)
        self._seen[hop] += 1
        self._keep(self._samples[hop], self._seen[hop], millis)
        self._session_seen[hop] += 1
        self._keep(self._session[hop], self._session_seen[hop], millis)

    def roll(self, minute: int) -> None:
        summary = {}
        for hop in HOPS:
            q = _quantiles(self._samples[hop])
            summary[hop] = q
            if q["n"]:
                self.closed.append((self._minute, hop, q["n"], q["p50"], q["p95"], q["p99"], q["max"]))
            self._samples[hop], self._seen[hop] = [], 0
        self.last_minute = {"minute_ts": self._minute, **summary}
        self._minute = minute
        day = datetime.fromtimestamp(minute, IST).date().isoformat()
        if day != self._session_day:
            self._session_day = day
            self._session = {hop: [] for hop in HOPS}
            self._session_seen = {hop: 0 for hop in HOPS}

    def status(self) -> dict:
        return {"last_minute": self.last_minute, "session_day": self._session_day,
                "session": {hop: _quantiles(self._session[hop]) for hop in HOPS}}


class LiveFeatures:
    def __init__(self, nats_url: str, tick_db_path: str | None, flow_tracker, profiles=None, universe=None):
        self.nats_url = nats_url
        self.tick_db_path = tick_db_path
        self.flow = flow_tracker
        self.profiles = profiles
        self.universe = universe or (lambda: ())
        self.latency = LatencyMeter()
        self._published: dict[str, int] = {}      # symbol -> last closed minute published
        self._snap_trades: dict[str, int] = {}
        self._pending_rows: list[tuple] = []
        self._last_profile = 0.0
        self._connection = None
        self._js = None
        self._kv = None
        self._task: asyncio.Task | None = None
        self.published_minutes = 0
        self.published_profiles = 0
        self.published_snapshots = 0
        self.errors = 0
        self.last_error: str | None = None

    # ---------------------------------------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._connection is not None:
            try:
                await self._connection.drain()
            except Exception:  # noqa: BLE001
                pass
            self._connection = None

    async def _connect(self) -> None:
        import nats
        from nats.js.api import KeyValueConfig, StorageType, StreamConfig
        self._connection = await nats.connect(servers=[self.nats_url], name="macd-desk-features", allow_reconnect=True,
                                              max_reconnect_attempts=-1, reconnect_time_wait=1)
        self._js = self._connection.jetstream()
        config = StreamConfig(name=STREAM, subjects=STREAM_SUBJECTS, storage=StorageType.FILE,
                              max_age=3 * 86_400, max_bytes=2 * 1024 ** 3, duplicate_window=6 * 3600)
        try:
            await self._js.stream_info(STREAM)
            await self._js.update_stream(config)
        except Exception:  # noqa: BLE001 — not there yet
            await self._js.add_stream(config)
        try:
            self._kv = await self._js.key_value(KV_BUCKET)
        except Exception:  # noqa: BLE001
            self._kv = await self._js.create_key_value(KeyValueConfig(bucket=KV_BUCKET, history=1,
                                                                      storage=StorageType.FILE))

    async def _run(self) -> None:
        while True:
            try:
                if self._connection is None or not self._connection.is_connected:
                    await self._connect()
                    self.last_error = None
                await self.publish_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — features must never take the desk down
                self.errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                self._connection = None
                await asyncio.sleep(5)
            await asyncio.sleep(PUBLISH_EVERY_SECONDS)

    # --------------------------------------------------------------------------------------------- publishing

    def closed_minutes(self, now: float | None = None) -> list[tuple[str, list, object]]:
        """(symbol, minute row, state) for every minute that has closed and was not published yet."""
        now = time.time() if now is None else now
        current = int(now // 60 * 60)
        out = []
        for symbol, state in list(self.flow.states.items()):
            minutes = state.minutes
            if not minutes:
                continue
            last = self._published.get(symbol, 0)
            if minutes[-1][0] <= last:
                continue
            for row in list(minutes):
                if last < row[0] < current:
                    out.append((symbol, list(row), state))
        return out

    def flow_message(self, symbol: str, row: list, state, computed_ms: int) -> dict:
        minute, close, volume, buy, sell, cvd = row[:6]
        try:
            burst = self.flow.burst(symbol)
        except Exception:  # noqa: BLE001
            burst = None
        return {"v": FEATURE_VERSION, "kind": "flow.1m", "symbol": symbol, "minute_ts": minute, "close": close,
                "volume": volume, "buy_volume": buy, "sell_volume": sell, "delta": buy - sell, "cvd": cvd,
                "session_cvd": state.cumulative_delta, "session_volume": state.total_volume,
                "session_unclassified": state.unclassified_volume, "session_trades": state.trades,
                "burst": burst, "computed_at_ms": computed_ms}

    def snapshot(self, symbol: str, state, computed_ms: int) -> dict:
        classified = state.buy_volume + state.sell_volume
        return {"v": FEATURE_VERSION, "kind": "flow.snapshot", "symbol": symbol, "session_cvd": state.cumulative_delta,
                "session_classified_volume": classified, "session_total_volume": state.total_volume,
                "weighted_delta": state.weighted_delta, "trades": state.trades, "last_price": state.last_price,
                "minute": list(state.minutes[-1]) if state.minutes else None, "computed_at_ms": computed_ms,
                "day": datetime.fromtimestamp(computed_ms / 1000, IST).date().isoformat()}

    def profile_message(self, symbol: str, profile, computed_ms: int) -> dict | None:
        try:
            vah_val = profile.value_area()
            return {"v": FEATURE_VERSION, "kind": "profile.1m", "symbol": symbol, "day": profile.day,
                    "poc": profile.poc(), "vah": vah_val[0], "val": vah_val[1], "ib_high": profile.ib_high,
                    "ib_low": profile.ib_low, "high": profile.high, "low": profile.low, "open": profile.open_price,
                    "last": profile.last_price, "open_type": profile.open_type(), "day_type": profile.day_type(),
                    "brackets": len(profile.brackets_seen), "computed_at_ms": computed_ms}
        except Exception:  # noqa: BLE001 — a profile that cannot summarise yet is skipped, not fatal
            return None

    async def publish_once(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        computed_ms = int(now * 1000)
        sent = {"minutes": 0, "snapshots": 0, "profiles": 0}
        for symbol, row, state in self.closed_minutes(now):
            message = self.flow_message(symbol, row, state, computed_ms)
            # A plain publish: the FT stream captures its subjects without a per-message ack round trip, and
            # the message id still de-duplicates a re-publish after a restart.
            await self._connection.publish(f"ft.flow.1m.{subject_token(symbol)}", orjson.dumps(message),
                                           headers={"Nats-Msg-Id": f"flow:{symbol}:{row[0]}"})
            self._published[symbol] = max(self._published.get(symbol, 0), row[0])
            self._pending_rows.append((symbol, row[0], row[1], row[2], row[3], row[4], row[3] - row[4], row[5],
                                       computed_ms))
            self.latency.add("feature", max(0.0, (now - (row[0] + 60)) * 1000), now)
            sent["minutes"] += 1
        for symbol, state in list(self.flow.states.items()):
            if state.trades == self._snap_trades.get(symbol):
                continue
            self._snap_trades[symbol] = state.trades
            payload = orjson.dumps(self.snapshot(symbol, state, computed_ms))
            await self._connection.publish(SNAPSHOT_PREFIX + subject_token(symbol), payload)
            # The KV bucket's own subject: a put without waiting for its ack.
            await self._connection.publish(f"$KV.{KV_BUCKET}.flow.{subject_token(symbol)}", payload)
            sent["snapshots"] += 1
        if self.profiles is not None and now - self._last_profile >= PROFILE_EVERY_SECONDS:
            self._last_profile = now
            minute = int(now // 60 * 60)
            for symbol in list(self.universe()):
                profile = self.profiles.get(symbol)
                if profile is None:
                    continue
                message = self.profile_message(symbol, profile, computed_ms)
                if message is None:
                    continue
                await self._connection.publish(f"ft.profile.1m.{subject_token(symbol)}", orjson.dumps(message),
                                               headers={"Nats-Msg-Id": f"profile:{symbol}:{minute}"})
                sent["profiles"] += 1
        self.published_minutes += sent["minutes"]
        self.published_snapshots += sent["snapshots"]
        self.published_profiles += sent["profiles"]
        if self._pending_rows or self.latency.closed:
            rows, self._pending_rows = self._pending_rows, []
            latency, self.latency.closed = self.latency.closed, []
            await asyncio.to_thread(self.store, rows, latency)
        return sent

    def store(self, rows: list[tuple], latency: list[tuple]) -> None:
        if not self.tick_db_path:
            return
        connection = sqlite3.connect(self.tick_db_path, timeout=30)
        try:
            connection.executescript(LIVE_FLOW_SCHEMA)
            with connection:
                connection.executemany("INSERT OR REPLACE INTO live_flow_minutes VALUES (?,?,?,?,?,?,?,?,?)", rows)
                connection.executemany("INSERT OR REPLACE INTO latency_minutes VALUES (?,?,?,?,?,?,?)", latency)
        finally:
            connection.close()

    def status(self) -> dict:
        return {"connected": bool(self._connection is not None and self._connection.is_connected),
                "published_minutes": self.published_minutes, "published_profiles": self.published_profiles,
                "published_snapshots": self.published_snapshots, "errors": self.errors, "last_error": self.last_error,
                "latency": self.latency.status()}


def decode_snapshot(payload: bytes) -> dict | None:
    try:
        return orjson.loads(payload)
    except Exception:  # noqa: BLE001
        return None


class FlowSnapshots:
    """The desk's latest flow snapshot per symbol (fts.flow.*), held for synchronous reads in another process.

    The engine's entry context reads the desk's tracker through this instead of running a second one; its own
    tracker stays as the fallback until the two are shown to agree on live sessions (phase 1 gate).
    """

    FRESH_SECONDS = 120

    def __init__(self, nats_url: str):
        self.nats_url = nats_url
        self.latest: dict[str, dict] = {}
        self.received = 0
        self.last_error: str | None = None
        self._connection = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            try:
                import nats
                self._connection = await nats.connect(servers=[self.nats_url], name="macd-engine-flow",
                                                      allow_reconnect=True, max_reconnect_attempts=-1,
                                                      reconnect_time_wait=1)
                await self._connection.subscribe(SNAPSHOT_PREFIX + ">", cb=self._on_message)
                self.last_error = None
                return
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"connect: {exc}"[:200]
                await asyncio.sleep(5)

    async def _on_message(self, message) -> None:
        snapshot = decode_snapshot(message.data)
        if snapshot and snapshot.get("symbol"):
            self.latest[snapshot["symbol"]] = snapshot
            self.received += 1

    def get(self, symbol: str, now_ms: float | None = None) -> dict | None:
        snapshot = self.latest.get(symbol)
        now_ms = time.time() * 1000 if now_ms is None else now_ms
        if snapshot is None or now_ms - (snapshot.get("computed_at_ms") or 0) > self.FRESH_SECONDS * 1000:
            return None
        return snapshot

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._connection is not None:
            try:
                await self._connection.drain()
            except Exception:  # noqa: BLE001
                pass

    def status(self) -> dict:
        return {"symbols": len(self.latest), "received": self.received, "last_error": self.last_error}
