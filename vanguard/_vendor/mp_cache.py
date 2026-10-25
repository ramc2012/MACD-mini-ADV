"""Versioned, content-addressed MP/OF results shared by every process.

No broker I/O. Redis is optional for offline research/tests; the deployment sets
SHARED_MP_REDIS_URL. Values are JSON, never executable serialized objects.
A lease coalesces identical work across the API, strategy worker and VANGUARD.
"""
from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from hashlib import sha256
from threading import RLock
from uuid import uuid4

_LOCAL = OrderedDict()
_LOCK = RLock()
_STATS = {"hits": 0, "shared_hits": 0, "computes": 0, "redis_errors": 0}
_CLIENT = None
_RETRY_AT = 0.0


def _default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Unsupported cache value: {type(value).__name__}")


def encode(value):
    return json.dumps(value, default=_default, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return sha256(encode(value).encode()).hexdigest()


def _redis():
    global _CLIENT, _RETRY_AT
    url = os.environ.get("SHARED_MP_REDIS_URL")
    if not url or time.monotonic() < _RETRY_AT:
        return None
    if _CLIENT is None:
        import redis
        _CLIENT = redis.Redis.from_url(url, socket_connect_timeout=0.3, socket_timeout=0.5)
    return _CLIENT


def stats():
    return {**_STATS, "entries": len(_LOCAL), "shared_configured": bool(os.environ.get("SHARED_MP_REDIS_URL")),
            "redis_degraded": time.monotonic() < _RETRY_AT}


def cached_json(namespace, inputs, compute, *, ttl=604800):
    """Return an isolated JSON value. Bump namespace when formula changes."""
    global _RETRY_AT
    key = f"mpof:{namespace}:{fingerprint(inputs)}"
    # The local lock also protects callers using asyncio.to_thread.
    with _LOCK:
        hit = _LOCAL.get(key)
        if hit and hit[0] > time.monotonic():
            _STATS["hits"] += 1
            _LOCAL.move_to_end(key)
            return json.loads(hit[1])
        client = _redis()
        token = uuid4().hex
        leased = False
        try:
            if client is not None:
                deadline = time.monotonic() + 5
                while True:
                    raw = client.get(key)
                    if raw is not None:
                        _STATS["shared_hits"] += 1
                        return json.loads(raw)
                    leased = bool(client.set(key + ":lock", token, nx=True, ex=300))
                    if leased:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Shared MP/OF calculation is still in progress")
                    time.sleep(0.02)
        except TimeoutError:
            raise
        except Exception:
            _STATS["redis_errors"] += 1
            _RETRY_AT = time.monotonic() + 30
            client = None
        try:
            value = encode(compute())
            _STATS["computes"] += 1
            if client is not None:
                try:
                    client.set(key, value, ex=ttl)
                except Exception:
                    _STATS["redis_errors"] += 1
                    _RETRY_AT = time.monotonic() + 30
            _LOCAL[key] = (time.monotonic() + min(ttl, 300), value)
            while len(_LOCAL) > 2048:
                _LOCAL.popitem(last=False)
            return json.loads(value)
        finally:
            if client is not None and leased:
                try:
                    client.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end", 1, key + ":lock", token)
                except Exception:
                    _STATS["redis_errors"] += 1
