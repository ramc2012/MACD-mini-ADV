from __future__ import annotations

import asyncio
import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from itertools import count
from pathlib import Path
from typing import Any

import orjson

# Non-string dict keys (strike prices, bar numbers) become strings, as the
# JSON encoder always made them on the wire.
_ORJSON_OPTIONS = orjson.OPT_NON_STR_KEYS


def _default(value: Any) -> Any:
    # orjson encodes datetime natively but not subclasses of it.
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def dumps(value: Any) -> bytes:
    """Serialize engine state to JSON bytes.

    orjson walks dataclasses and datetimes natively. It writes NaN and
    infinity as null, where the standard encoder wrote bare NaN tokens that
    JSON.parse rejects.
    """
    return orjson.dumps(value, default=_default, option=_ORJSON_OPTIONS)


def _json_value_slow(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key if isinstance(key, str) else str(key): _json_value_slow(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value_slow(item) for item in value]
    return value


def json_value(value: Any) -> Any:
    """Engine state as plain JSON-ready Python values.

    A round trip through orjson replaced a recursive dataclasses.asdict walk
    that cost ~60% of the per-tick path. Anything orjson cannot encode falls
    back to the recursive walk.
    """
    try:
        return orjson.loads(dumps(value))
    except TypeError:
        return _json_value_slow(value)


class Frame(dict):
    """One stream frame: a plain dict for in-process readers, plus its JSON
    text, encoded once however many sockets send it."""

    __slots__ = ("_text",)

    def __init__(self, seq: int, event_type: str, data: Any, encoded: bytes | None = None):
        # `data` must already be JSON-ready (see json_value).
        super().__init__(seq=seq, type=event_type, data=data)
        self._text: str | None = None
        if encoded is not None:
            self._text = _frame_text(seq, event_type, encoded)

    def text(self) -> str:
        if self._text is None:
            self._text = _frame_text(self["seq"], self["type"], dumps(self["data"]))
        return self._text


def _frame_text(seq: int, event_type: str, encoded: bytes) -> str:
    return b"".join((
        b'{"seq":', str(seq).encode(), b',"type":', orjson.dumps(event_type), b',"data":', encoded, b"}",
    )).decode()


def frame_text(frame: dict) -> str:
    """JSON text for any queued frame, cached when it is a Frame."""
    if isinstance(frame, Frame):
        return frame.text()
    return dumps(frame).decode()


class EventHub:
    """In-process fan-out with bounded per-client queues.

    Slow browsers lose their oldest frame instead of applying back-pressure to
    the broker callback or strategy loop. Every frame carries a monotonic
    sequence so clients can detect a gap and request a fresh snapshot.
    """

    def __init__(self, queue_size: int = 512):
        self._subscribers: set[asyncio.Queue] = set()
        self._sequence = count(1)
        self.current_sequence = 0
        self._queue_size = queue_size
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    @property
    def client_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, data: Any) -> None:
        seq = next(self._sequence)
        self.current_sequence = seq
        if not self._subscribers:
            return  # nobody to encode for
        try:
            encoded = dumps(data)
            event = Frame(seq, event_type, orjson.loads(encoded), encoded)
        except TypeError:
            event = Frame(seq, event_type, _json_value_slow(data))
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
