from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime
from itertools import count
from typing import Any


def json_value(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


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
        event = {"seq": next(self._sequence), "type": event_type, "data": json_value(data)}
        self.current_sequence = event["seq"]
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
