"""The tick bus: every tick the strategy process accepts, published to NATS.

This is the market-data contract between processes. The strategy process
(the only Fyers client) publishes after its own delayed- and future-print
filters, so every consumer sees the same accepted stream in the same order:
the desk process rebuilds exactly the Tick the engine had, and the Rust
analytics service reads the same message. A Go ingester using the Fyers Go
SDK could later publish this message instead, and no consumer would change.

Message: JSON on subject ``md.tick.<symbol>`` carrying every Tick field, plus
``v`` (contract version), ``seq`` (per-publisher, contiguous: a consumer sees a
gap as loss), ``analysis_only``, ``exchange_ts_ms`` and ``received_ts_ms`` (the
engine's receipt time; ``gateway_ts_ms`` repeats it for older readers).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import fields
from datetime import datetime
from itertools import count
from typing import Awaitable, Callable

import orjson

from .events import dumps
from .models import Tick

SUBJECT_PREFIX = "md.tick."
SUBJECT_ALL = "md.tick.>"
CONTRACT_VERSION = 1
_TICK_FIELDS = tuple(field.name for field in fields(Tick))
log = logging.getLogger(__name__)


def tick_subject(symbol: str) -> str:
    """NATS subject tokens may not contain separators or wildcards."""
    return SUBJECT_PREFIX + "".join("_" if ch in ".*> \t\r\n" else ch for ch in symbol)


def encode_tick(tick: Tick, *, seq: int, analysis_only: bool, received_at: datetime) -> bytes:
    stamp = tick.timestamp
    received_ms = int(received_at.timestamp() * 1000)
    message = {name: getattr(tick, name) for name in _TICK_FIELDS}
    message.update({
        "v": CONTRACT_VERSION, "seq": seq, "analysis_only": analysis_only,
        "exchange_ts_ms": int(stamp.timestamp() * 1000),
        "received_ts_ms": received_ms, "gateway_ts_ms": received_ms,
    })
    return dumps(message)


def decode_tick(payload: bytes) -> tuple[Tick, bool, int]:
    """(tick, analysis_only, seq) from a bus message."""
    message = orjson.loads(payload)
    values = {name: message.get(name) for name in _TICK_FIELDS if name in message}
    values["timestamp"] = datetime.fromisoformat(message["timestamp"])
    values["volume"] = int(values.get("volume") or 0)
    return Tick(**values), bool(message.get("analysis_only")), int(message.get("seq") or 0)


class TickBusPublisher:
    """Publishes accepted ticks without ever holding up the tick path.

    It connects in the background and keeps reconnecting; while NATS is away
    ticks are counted as dropped, not queued without bound.
    """

    def __init__(self, url: str):
        self.url = url
        self._connection = None
        self._sequence = count(1)
        self._task: asyncio.Task | None = None
        self.published = 0
        self.dropped = 0
        self.last_error: str | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._connect())

    async def _connect(self) -> None:
        while self._connection is None:
            try:
                import nats  # inside the try: a missing client must show, not kill the task

                self._connection = await nats.connect(
                    servers=[self.url], name="macd-strategy", allow_reconnect=True,
                    max_reconnect_attempts=-1, reconnect_time_wait=1,
                    # Ticks buffered while reconnecting; beyond this, publish raises.
                    pending_size=16 * 1024 * 1024,
                    error_cb=self._on_error,
                )
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 - keep trying; never block the feed
                self.last_error = f"connect: {exc}"[:300]
                await asyncio.sleep(2)

    async def _on_error(self, exc: Exception) -> None:
        self.last_error = str(exc)[:300]

    @property
    def connected(self) -> bool:
        return bool(self._connection is not None and self._connection.is_connected)

    async def publish(self, tick: Tick, *, analysis_only: bool, received_at: datetime) -> None:
        connection = self._connection
        if connection is None or connection.is_closed:
            self.dropped += 1
            return
        seq = next(self._sequence)
        try:
            await connection.publish(tick_subject(tick.symbol),
                                     encode_tick(tick, seq=seq, analysis_only=analysis_only,
                                                 received_at=received_at))
            self.published += 1
        except Exception as exc:  # noqa: BLE001 - a full buffer drops the tick, never the feed
            self.dropped += 1
            self.last_error = str(exc)[:300]
            # The consumer sees this as a sequence gap, which is the truth.

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._connection is not None:
            try:
                await self._connection.drain()
            except Exception:  # noqa: BLE001
                pass
            self._connection = None

    def status(self) -> dict:
        return {"url": self.url, "connected": self.connected, "published": self.published,
                "dropped": self.dropped, "last_error": self.last_error}


class TickBusSubscriber:
    """Delivers bus ticks to ``handler`` in publish order, counting any loss."""

    def __init__(self, url: str, handler: Callable[[Tick, bool], Awaitable[None]]):
        self.url = url
        self._handler = handler
        self._connection = None
        self.received = 0
        self.lost = 0
        self.undecodable = 0
        self.publisher_restarts = 0
        self.last_seq = 0
        self.last_error: str | None = None

    async def run(self) -> None:
        while True:
            try:
                import nats  # inside the try: a missing client must show, not kill the task

                self._connection = await nats.connect(
                    servers=[self.url], name="macd-desk", allow_reconnect=True,
                    max_reconnect_attempts=-1, reconnect_time_wait=1, error_cb=self._on_error)
                await self._connection.subscribe(
                    SUBJECT_ALL, cb=self._on_message,
                    # A full session's burst stays in memory rather than being
                    # dropped as a slow consumer; the desk drains it in order.
                    pending_msgs_limit=2_000_000, pending_bytes_limit=1024 * 1024 * 1024)
                self.last_error = None
                return
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"connect: {exc}"[:300]
                await asyncio.sleep(2)

    async def _on_error(self, exc: Exception) -> None:
        self.last_error = str(exc)[:300]

    async def _on_message(self, message) -> None:
        try:
            tick, analysis_only, seq = decode_tick(message.data)
        except Exception:  # noqa: BLE001
            self.undecodable += 1
            return
        self.observe_seq(seq)
        self.received += 1
        await self._handler(tick, analysis_only)

    def observe_seq(self, seq: int) -> None:
        if seq <= 0:
            return
        if self.last_seq and seq <= self.last_seq:
            self.publisher_restarts += 1  # the strategy process restarted
        elif self.last_seq and seq > self.last_seq + 1:
            self.lost += seq - self.last_seq - 1
        self.last_seq = seq

    @property
    def connected(self) -> bool:
        return bool(self._connection is not None and self._connection.is_connected)

    async def stop(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.drain()
            except Exception:  # noqa: BLE001
                pass
            self._connection = None

    def status(self) -> dict:
        return {"url": self.url, "connected": self.connected, "received": self.received,
                "lost": self.lost, "undecodable": self.undecodable,
                "publisher_restarts": self.publisher_restarts, "last_error": self.last_error}
