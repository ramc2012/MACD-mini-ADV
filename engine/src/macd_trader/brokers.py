from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import random
import time
from abc import ABC, abstractmethod
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo
from pathlib import Path

import httpx

from .config import Settings
from .models import Candle, Order, Tick
from .rollover import Expiry, futures_root


TickHandler = Callable[[Tick], Awaitable[None]]


class _ThreadsafeTickBuffer:
    """Bound SDK-thread handoff so a slow event loop cannot exhaust memory."""

    def __init__(self, loop: asyncio.AbstractEventLoop, capacity: int = 4096):
        self.loop = loop
        self.capacity = capacity
        self._items: deque[Tick] = deque()
        self._lock = Lock()
        self._wake = asyncio.Event()
        self._notification_pending = False
        self.dropped = 0

    def put(self, tick: Tick) -> None:
        notify = False
        with self._lock:
            if len(self._items) >= self.capacity:
                self._items.popleft()
                self.dropped += 1
            self._items.append(tick)
            if not self._notification_pending:
                self._notification_pending = True
                notify = True
        if notify:
            try:
                self.loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                # The application loop may already be closing.
                with self._lock:
                    self._notification_pending = False

    async def get(self) -> Tick:
        while True:
            with self._lock:
                if self._items:
                    return self._items.popleft()
                self._notification_pending = False
            self._wake.clear()
            await self._wake.wait()

    def status(self) -> dict[str, int]:
        with self._lock:
            return {
                "pending": len(self._items),
                "capacity": self.capacity,
                "dropped": self.dropped,
            }


@dataclass(slots=True)
class OptionChainEntry:
    strike: float
    option_type: str
    symbol: str
    ltp: float = 0.0
    volume: int = 0
    oi: int = 0
    # The whale tracker's Layer B reads a minute-to-minute chain. A stale ltp
    # on an illiquid strike is the norm there, so the touch is carried to
    # judge it; prev_oi is Fyers' prior-day close OI, which gives a
    # day-over-day build on the first day the collector runs. It is None, not
    # 0, when the response carries no prior close: a zero there would read as
    # a strike that opened yesterday with nothing on it, and the whole of its
    # open interest would book as one day's build.
    bid: float = 0.0
    ask: float = 0.0
    prev_oi: int | None = None
    oich: int = 0


@dataclass(slots=True)
class OptionChain:
    expiry: str
    spot_price: float
    entries: list[OptionChainEntry]
    # Every expiry Fyers lists for this underlying, nearest first. Carried so
    # a caller that must roll past the front month does not have to spend a
    # second chain request just to discover what the alternatives are.
    expiries: list[Expiry] = field(default_factory=list)
    # The underlying row's futures price. Delta solved against it with a zero
    # rate prices the option off the forward the market is actually quoting.
    fp: float = 0.0
    vix: float | None = None


@dataclass(slots=True)
class FuturesSeries:
    symbol: str
    root: str
    expiry: str
    lot_size: int


@dataclass(slots=True)
class OptionContractMetadata:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    expiry: str
    lot_size: int


class Broker(ABC):
    name = "base"

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def stream(self, symbols: list[str], handler: TickHandler) -> None: ...

    @abstractmethod
    async def history(self, symbol: str, timeframe_seconds: int, count: int) -> list[Candle]: ...

    @abstractmethod
    async def quote(self, symbol: str) -> Tick: ...

    @abstractmethod
    async def option_chain(self, symbol: str, expiry_token: str | None = None) -> OptionChain: ...

    @abstractmethod
    async def history_range(self, symbol: str, timeframe_seconds: int, start: datetime, end: datetime) -> list[Candle]: ...

    async def preopen_price(self, symbol: str) -> float | None:
        return None

    async def lot_sizes(self, symbols: list[str]) -> dict[str, int]:
        return {}

    async def option_contract_metadata(self, symbols: list[str]) -> dict[str, OptionContractMetadata]:
        return {}

    async def current_lot_sizes(self, symbols: list[str], *, refresh: bool = False) -> dict[str, int]:
        """The exchange's lot size now for these (held) contracts; ``refresh`` re-reads the broker's master."""
        return await self.lot_sizes(symbols)

    async def series_lot_sizes(self, exchanges: list[str]) -> dict[str, dict[str, int]]:
        """{"EXCHANGE:ROOT": {expiry: lot size}} for every listed option series; empty without a master."""
        return {}

    async def option_master(self, exchanges: list[str]) -> list[OptionContractMetadata]:
        """Every listed option on these exchanges; empty when the broker has no master."""
        return []

    async def futures_series(self, symbols: list[str]) -> dict[str, list["FuturesSeries"]]:
        """Every listed futures series for each requested symbol's root."""
        return {}

    async def quotes(self, symbols: list[str]) -> dict[str, Tick]:
        rows = await asyncio.gather(*(self.quote(symbol) for symbol in symbols))
        return {row.symbol: row for row in rows}

    async def open_interest(self, symbol: str) -> dict | None:
        """Open interest for one derivative: {"oi", "prev_oi", "ltp", "volume"}, or None when the broker has none."""
        return None

    async def validate_session(self) -> bool:
        """Confirm that the broker session is still authenticated.

        Simulation and test brokers only have local connection state. Live
        brokers override this with an authenticated, read-only API probe.
        """
        return bool(getattr(self, "connected", False))

    @abstractmethod
    async def place_order(self, order: Order) -> str: ...

    @abstractmethod
    async def close(self) -> None: ...


class SimulationBroker(Broker):
    name = "simulation"

    def __init__(self):
        self.connected = False
        self._prices: dict[str, float] = {}
        self._stopped = asyncio.Event()

    async def connect(self) -> None:
        self.connected = True
        self._stopped.clear()

    def _seed(self, symbol: str) -> float:
        if "BANK" in symbol:
            return 55_000.0
        if "SENSEX" in symbol:
            return 78_000.0
        return 24_000.0

    async def history(self, symbol: str, timeframe_seconds: int, count: int) -> list[Candle]:
        rng = random.Random(symbol)
        price = self._seed(symbol)
        now = int(datetime.now(UTC).timestamp())
        last_bucket = now - (now % timeframe_seconds)
        rows: list[Candle] = []
        for index in range(count, 0, -1):
            timestamp = last_bucket - index * timeframe_seconds
            drift = math.sin((count - index) / 9) * price * 0.00015
            close = max(1.0, price + drift + rng.gauss(0, price * 0.00035))
            rows.append(Candle(symbol, timestamp, price, max(price, close), min(price, close), close, rng.randint(100, 5000), True))
            price = close
        self._prices[symbol] = price
        return rows

    async def stream(self, symbols: list[str], handler: TickHandler) -> None:
        rng = random.Random(481516)
        while not self._stopped.is_set():
            for symbol in symbols:
                previous = self._prices.setdefault(symbol, self._seed(symbol))
                next_price = max(1.0, previous * (1 + rng.gauss(0, 0.00008)))
                self._prices[symbol] = next_price
                await handler(Tick(symbol, round(next_price, 2), rng.randint(100, 20000)))
            await asyncio.sleep(0.1)

    async def quote(self, symbol: str) -> Tick:
        return Tick(symbol, self._prices.get(symbol, self._seed(symbol)))

    async def option_chain(self, symbol: str) -> OptionChain:
        raise RuntimeError("Simulation option chains are disabled")

    async def history_range(self, symbol: str, timeframe_seconds: int, start: datetime, end: datetime) -> list[Candle]:
        count = max(1, int((end - start).total_seconds() // timeframe_seconds))
        return await self.history(symbol, timeframe_seconds, count)

    async def place_order(self, order: Order) -> str:
        return f"SIM-{order.order_id[:12]}"

    async def close(self) -> None:
        self._stopped.set()
        self.connected = False


# The SDK's connect and close block on internal thread joins that can never
# return -- on 8 Sep close_connection() parked forever on a message thread the
# SDK had orphaned across reconnects, and the caller abandons it on a timeout.
# An abandoned call keeps its worker for the life of the process, so these must
# not run on asyncio's DEFAULT executor: the chain collector, the whale sampler
# and the dispersion writer all reach it through asyncio.to_thread, and a few
# leaked socket closes would silently starve them of workers.
_SDK_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="fyers-sdk")


async def _in_sdk_pool(func, *args):
    return await asyncio.get_running_loop().run_in_executor(_SDK_POOL, func, *args)


def _make_resilient_symbol_conversion(original):
    """Use smaller, retried FYERS symbol-token lookups for large subscriptions.

    The SDK posts 500 symbols at once. FYERS intermittently closes those
    responses at the TLS layer, and the SDK silently returns None.
    """
    def convert(self, symbols):
        converted = {}
        invalid = []
        index_issue = False
        for offset in range(0, len(symbols), 100):
            chunk = symbols[offset:offset + 100]
            result = None
            for attempt in range(5):
                result = original(self, chunk)
                if result is not None and not result[3]:
                    break
                if attempt < 4:
                    time.sleep(0.5 * (attempt + 1))
            if result is None:
                return {}, [], False, f"FYERS symbol-token lookup failed for batch {offset // 100 + 1}"
            token_map, rejected, depth_issue, error = result
            if error:
                return {}, [], False, error
            converted.update(token_map)
            invalid.extend(rejected or [])
            index_issue = index_issue or depth_issue
        return converted, invalid, index_issue, ""
    return convert


_INDEX_DEPTH_NOTICE = "Index does not have market depth"


def _is_partial_subscription_notice(detail: object) -> bool:
    """True for SDK notices that leave the rest of a subscription live.

    ``FyersDataSocket.__symbol_conversion`` reports rejected symbols (with an
    ``invalid_symbols`` list) and depth-less indices via on_error, then still
    subscribes everything that converted. A failed lookup is different: it
    carries only a message and nothing is subscribed, so it stays fatal.
    """
    if not isinstance(detail, dict):
        return False
    return bool(detail.get("invalid_symbols")) or detail.get("message") == _INDEX_DEPTH_NOTICE


class FyersBroker(Broker):
    name = "fyers"
    data_url = "https://api-t1.fyers.in/data"
    api_url = "https://api-t1.fyers.in/api/v3"
    instrument_master_urls = {
        "NSE": "https://public.fyers.in/sym_details/NSE_FO.csv",
        "BSE": "https://public.fyers.in/sym_details/BSE_FO.csv",
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.connected = False
        self._socket = None
        self._tick_buffer: _ThreadsafeTickBuffer | None = None
        self.handler_errors = 0
        self.last_handler_error: str | None = None
        # Symbols FYERS refused at subscribe time (e.g. an expired contract
        # still held in a book). The rest of the subscription streams on.
        self.rejected_symbols: list[str] = []
        self.subscription_notices: list[str] = []
        self._lot_cache_path = Path(settings.contract_snapshot_path).with_name("fyers_lot_sizes.json")
        self._master_dir = Path(settings.contract_snapshot_path).parent
        self._master_text: dict[str, tuple[str, str]] = {}
        self._chain_keys_logged = False

    def tick_buffer_status(self) -> dict:
        status = self._tick_buffer.status() if self._tick_buffer else {
            "pending": 0, "capacity": 4096, "dropped": 0,
        }
        return {**status, "handler_errors": self.handler_errors, "last_handler_error": self.last_handler_error,
                "rejected_symbols": list(self.rejected_symbols),
                "subscription_notices": list(self.subscription_notices)}

    def auth_url(self) -> str:
        if not self.settings.fyers_client_id or not self.settings.fyers_secret:
            raise RuntimeError("Save the Fyers client ID and app secret first")
        from fyers_apiv3 import fyersModel
        session = fyersModel.SessionModel(
            client_id=self.settings.fyers_client_id,
            secret_key=self.settings.fyers_secret,
            redirect_uri=self.settings.fyers_redirect_uri,
            response_type="code",
            grant_type="authorization_code",
        )
        return session.generate_authcode()

    async def exchange_auth_code(self, auth_code: str) -> dict[str, str]:
        from fyers_apiv3 import fyersModel
        session = fyersModel.SessionModel(
            client_id=self.settings.fyers_client_id,
            secret_key=self.settings.fyers_secret,
            redirect_uri=self.settings.fyers_redirect_uri,
            response_type="code",
            grant_type="authorization_code",
        )
        session.set_token(auth_code.strip())
        response = await asyncio.to_thread(session.generate_token)
        if not response.get("access_token"):
            raise RuntimeError(str(response.get("message") or "Fyers did not return an access token"))
        refresh = response.get("refresh_token") or response.get("refreshToken")
        return {"access_token": str(response["access_token"]), **({"refresh_token": str(refresh)} if refresh else {})}

    async def refresh_access_token(self) -> dict[str, str]:
        """Today's access token from the saved refresh token and PIN (Fyers' validate-refresh-token), no browser."""
        if not (self.settings.fyers_refresh_token and self.settings.fyers_pin):
            raise RuntimeError("No refresh token and PIN saved: log in once through the browser (Fyers returns a "
                               "refresh token with it) and save the PIN")
        import hashlib
        app_hash = hashlib.sha256(f"{self.settings.fyers_client_id}:{self.settings.fyers_secret}".encode()).hexdigest()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post("https://api-t1.fyers.in/api/v3/validate-refresh-token", json={
                "grant_type": "refresh_token", "appIdHash": app_hash,
                "refresh_token": self.settings.fyers_refresh_token, "pin": self.settings.fyers_pin})
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code != 200 or not body.get("access_token"):
            raise RuntimeError(f"Fyers refused the refresh: {body.get('message') or response.text[:160]}")
        return {"access_token": str(body["access_token"])}

    def token_expiry(self) -> datetime | None:
        """Expiry of the daily access token, read from its JWT claim.

        Fyers tokens die at 06:00 IST daily. Without reading this the app only
        discovers the expiry when a request fails — which, for a websocket that
        simply goes quiet, means never.
        """
        token = self.settings.fyers_access_token
        if not token or token.count(".") != 2:
            return None
        try:
            import base64
            import json as _json
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = _json.loads(base64.urlsafe_b64decode(payload))
            return datetime.fromtimestamp(float(claims["exp"]), UTC)
        except Exception:  # noqa: BLE001 — an unreadable token is simply unknown
            return None

    def token_expired(self, now: datetime | None = None) -> bool:
        expiry = self.token_expiry()
        return expiry is not None and (now or datetime.now(UTC)) >= expiry

    @property
    def authorization(self) -> str:
        return f"{self.settings.fyers_client_id}:{self.settings.fyers_access_token}"

    async def _data_get(self, client: httpx.AsyncClient, endpoint: str, params: dict, *, priority: str,
                        ttl_ms: int | None = None) -> httpx.Response:
        """GET a Fyers data endpoint, directly or through the market-intelligence hub.

        With ``market_hub_url`` set, the call goes to the hub's /v1/request: one budget shared with every other
        caller of the same Fyers account, a short cache shared with Nomad Curie, and the hub's 429 back-off. The
        hub answers with Fyers' own status and body, so every parser below is unchanged. Empty (the default):
        straight to Fyers, as before.
        """
        if endpoint == "history" and os.environ.get("MACD_HISTORY_DOWNLOADS_ENABLED", "true").lower() in ("false", "0", "off"):
            raise RuntimeError("Historical broker downloads disabled during local-archive recovery")
        hub = (getattr(self.settings, "market_hub_url", "") or "").rstrip("/")
        if not hub:
            return await client.get(f"{self.data_url}/{endpoint}", params=params,
                                    headers={"Authorization": self.authorization})
        body = {"broker": "fyers", "endpoint": endpoint, "query": {k: str(v) for k, v in params.items()},
                "priority": priority, "caller": "macd-mini-adv"}
        if ttl_ms is not None:
            body["ttl_ms"] = ttl_ms
        return await client.post(f"{hub}/v1/request", json=body)

    @staticmethod
    def exchange_timestamp(payload: dict) -> datetime:
        """Return Fyers exchange time, never the browser/server wall clock when supplied."""
        source = next((key for key in ("last_traded_time", "exch_feed_time", "tt", "timestamp") if payload.get(key) not in (None, "")), None)
        raw = payload.get(source) if source else None
        parsed: datetime | None = None
        if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
            epoch = float(raw)
            if epoch > 10_000_000_000:
                epoch /= 1000
            parsed = datetime.fromtimestamp(epoch, UTC)
        if isinstance(raw, str):
            for pattern in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    # Broker-formatted clock strings are Indian exchange time.
                    parsed = datetime.strptime(raw, pattern).replace(tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(UTC)
                    break
                except ValueError:
                    pass
            if parsed is None:
                try:
                    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    parsed = (parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))).astimezone(UTC)
                except ValueError:
                    pass
        parsed = parsed or datetime.now(UTC)
        # After market close Fyers republishes the unchanged LTP with a fresh
        # exchange-feed timestamp and omits last_traded_time.  That is not an
        # LTP time. Clamp such snapshots to the regular-session close.
        if source == "exch_feed_time":
            # The instrument's own close, not a fixed 15:30: F&O trades to
            # 15:40 since Aug 2026 and those stamps are real.
            from .sessions import market_close, session_close
            market_time = parsed.astimezone(ZoneInfo("Asia/Kolkata"))
            symbol = payload.get("symbol")
            close = (session_close(symbol, market_time.date()) if isinstance(symbol, str) and ":" in symbol
                     else market_close(market_time.date()))
            if market_time.time().replace(tzinfo=None) > close:
                parsed = market_time.replace(hour=close.hour, minute=close.minute,
                                             second=0, microsecond=0).astimezone(UTC)
        return parsed

    async def connect(self) -> None:
        if not self.settings.fyers_client_id:
            raise RuntimeError("Fyers client ID is required")
        if self.settings.fyers_access_token and await self._token_is_valid():
            self.connected = True
            return
        raise RuntimeError("Fyers daily access token is missing or expired; paste today’s token in Fyers settings")

    async def _token_is_valid(self) -> bool:
        if not self.settings.fyers_access_token:
            return False
        try:
            return await self.validate_session()
        except (httpx.HTTPError, ValueError):
            return False

    async def validate_session(self) -> bool:
        """Use Fyers' profile endpoint as the source of truth for auth state."""
        if not self.settings.fyers_access_token:
            return False
        async with httpx.AsyncClient(timeout=6) as client:
            response = await client.get(
                f"{self.api_url}/profile",
                headers={"Authorization": self.authorization},
            )
        if response.status_code in {401, 403}:
            return False
        response.raise_for_status()
        payload = response.json()
        # Be fail-closed: an HTTP 200 without Fyers' explicit success marker
        # must not turn the terminal green.
        return isinstance(payload, dict) and payload.get("s") == "ok"

    async def history(self, symbol: str, timeframe_seconds: int, count: int) -> list[Candle]:
        resolution = str(max(1, timeframe_seconds // 60))
        end = datetime.now(UTC)
        start = end - timedelta(seconds=timeframe_seconds * (count + 20))
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": start.date().isoformat(),
            "range_to": end.date().isoformat(),
            "cont_flag": "1",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await self._data_get(client, "history", params, priority="normal")
            response.raise_for_status()
            payload = response.json()
        if payload.get("s") == "error":
            raise RuntimeError(payload.get("message", "Fyers history failed"))
        return [Candle(symbol, int(row[0]), *map(float, row[1:5]), int(row[5]), True) for row in payload.get("candles", [])[-count:]]

    async def history_range(self, symbol: str, timeframe_seconds: int, start: datetime, end: datetime) -> list[Candle]:
        # Fyers names the daily resolution "D"; minute resolutions are minute counts.
        resolution = "D" if timeframe_seconds >= 86_400 else str(max(1, timeframe_seconds // 60))
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": start.date().isoformat(),
            "range_to": end.date().isoformat(),
            "cont_flag": "1",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await self._data_get(client, "history", params, priority="bulk")
            response.raise_for_status()
            payload = response.json()
        if payload.get("s") == "error":
            raise RuntimeError(payload.get("message", f"Fyers history failed for {symbol}"))
        return [Candle(symbol, int(row[0]), *map(float, row[1:5]), int(row[5]), True) for row in payload.get("candles", [])]

    @classmethod
    def _quote_tick(cls, row: dict, fallback_symbol: str = "") -> Tick:
        value = row.get("v", {})
        ltp = float(value.get("lp", 0) or 0)
        change = float(value.get("ch", 0) or 0)
        # Fyers' /quotes carries no OI field (checked 23 Oct 2026, futures
        # included): this stays None from Fyers and open_interest() reads it.
        oi = value.get("oi")
        return Tick(
            str(row.get("n") or fallback_symbol), ltp, int(value.get("volume", 0) or 0),
            timestamp=cls.exchange_timestamp(value),
            prev_close=(ltp - change) if ltp else None, change=change,
            change_pct=float(value.get("chp", 0) or 0),
            open_interest=int(oi) if oi not in (None, "") else None,
        )

    async def quotes(self, symbols: list[str]) -> dict[str, Tick]:
        """Fetch marks in bounded batches so startup does not issue one REST call per position."""
        result: dict[str, Tick] = {}
        requested = list(dict.fromkeys(symbols))
        async with httpx.AsyncClient(timeout=15) as client:
            for offset in range(0, len(requested), 40):
                chunk = requested[offset: offset + 40]
                response = None
                for attempt in range(4):
                    # Marks: the hub's highest class, coalesced but never served from cache.
                    response = await self._data_get(client, "quotes", {"symbols": ",".join(chunk)},
                                                    priority="critical", ttl_ms=0)
                    if response.status_code != 429:
                        break
                    await asyncio.sleep(2 ** attempt)
                assert response is not None
                response.raise_for_status()
                payload = response.json()
                if payload.get("s") == "error":
                    raise RuntimeError(payload.get("message", "Fyers quotes failed"))
                for row in payload.get("d") or []:
                    tick = self._quote_tick(row)
                    if tick.symbol:
                        result[tick.symbol] = tick
        return result

    async def quote(self, symbol: str) -> Tick:
        rows = await self.quotes([symbol])
        if symbol not in rows:
            raise RuntimeError(f"No quote for {symbol}")
        return rows[symbol]

    async def open_interest(self, symbol: str) -> dict | None:
        """OI from the depth REST, the only Fyers call that carries it for a single contract: the socket never
        sends OI on this account and /quotes has no OI field at all (checked 23 Oct 2026, futures included).
        Depth takes exactly one symbol per call."""
        async with httpx.AsyncClient(timeout=15) as client:
            response = await self._data_get(client, "depth", {"symbol": symbol, "ohlcv_flag": "1"}, priority="high")
            response.raise_for_status()
            payload = response.json()
        if payload.get("s") == "error":
            raise RuntimeError(payload.get("message", f"Fyers depth failed for {symbol}"))
        row = (payload.get("d") or {}).get(symbol)
        if not row or row.get("oi") in (None, ""):
            return None
        return {"oi": int(row["oi"]), "prev_oi": int(row["pdoi"]) if row.get("pdoi") not in (None, "") else None,
                "ltp": float(row.get("ltp") or 0), "volume": int(row.get("v") or 0)}

    async def option_chain(self, symbol: str, expiry_token: str | None = None,
                           strikecount: int = 12, priority: str = "high") -> OptionChain:
        params = {"symbol": symbol, "strikecount": str(strikecount)}
        if expiry_token:
            # Fyers keys a specific expiry by the epoch it reports in
            # expiryData; without it the response is always the front expiry.
            params["timestamp"] = str(expiry_token)
        async with httpx.AsyncClient(timeout=18) as client:
            response = await self._data_get(client, "options-chain-v3", params, priority=priority)
            response.raise_for_status()
            payload = response.json()
        if payload.get("s") == "error":
            raise RuntimeError(payload.get("message", f"Option chain failed for {symbol}"))
        data = payload.get("data", {})

        def _iso(raw: str) -> str:
            try:
                return datetime.strptime(raw, "%d-%m-%Y").date().isoformat()
            except ValueError:
                return raw

        listed = [
            Expiry(_iso(str(row.get("date") or "")), str(row.get("expiry") or ""))
            for row in (data.get("expiryData") or [])
        ]
        # Fyers marks the expiry a chain response actually belongs to; fall
        # back to the first listed one only when it does not.
        selected = next((row for row in listed if str(row.token) == str(expiry_token or "")), None)
        expiry = (selected or next(iter(listed), Expiry(""))).date
        spot_price = fp = 0.0
        entries: list[OptionChainEntry] = []
        rows = data.get("optionsChain") or []
        for row in rows:
            side = str(row.get("option_type") or "").upper()
            if side not in {"CE", "PE"}:
                spot_price = max(spot_price, float(row.get("ltp", 0) or 0))
                fp = float(row.get("fp", 0) or 0) or fp
                continue
            contract_symbol = str(row.get("symbol") or "")
            if contract_symbol:
                entries.append(OptionChainEntry(
                    float(row.get("strike_price", 0) or 0), side, contract_symbol,
                    float(row.get("ltp", 0) or 0), int(row.get("volume", 0) or 0), int(row.get("oi", 0) or 0),
                    float(row.get("bid", 0) or 0), float(row.get("ask", 0) or 0),
                    int(row["prev_oi"]) if row.get("prev_oi") else None,
                    int(row.get("oich", 0) or 0),
                ))
        vix = float((data.get("indiavixData") or {}).get("ltp", 0) or 0) or None
        # The per-strike field names come from the blueprint's verified table,
        # not from code that has parsed them. A wrong key reads as silent
        # zeros in the chain snapshots, so the key set is printed once where
        # docker logs will show it.
        if rows and not self._chain_keys_logged:
            self._chain_keys_logged = True
            print("fyers chain keys:", sorted(rows[0].keys()), "top:", sorted(data.keys()))
        if not spot_price:
            spot_price = (await self.quote(symbol)).ltp
        return OptionChain(expiry, spot_price, entries, listed, fp=fp, vix=vix)

    async def lot_sizes(self, symbols: list[str]) -> dict[str, int]:
        """Resolve exchange quantities from Fyers' current derivatives master."""
        requested = set(symbols)
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        cached: dict[str, int] = {}
        try:
            payload = json.loads(self._lot_cache_path.read_text())
            if payload.get("date") == today:
                cached = {key: int(value) for key, value in payload.get("lot_sizes", {}).items()}
        except (OSError, ValueError, TypeError):
            pass
        missing = requested - cached.keys()
        if missing:
            details = await self.option_contract_metadata(list(missing))
            cached.update({symbol: row.lot_size for symbol, row in details.items() if row.lot_size > 0})
            self._lot_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._lot_cache_path.write_text(json.dumps({"date": today, "lot_sizes": cached}, indent=2))
            self._lot_cache_path.chmod(0o600)
        return {symbol: cached[symbol] for symbol in symbols if symbol in cached}

    async def current_lot_sizes(self, symbols: list[str], *, refresh: bool = False) -> dict[str, int]:
        """Held contracts' lot sizes from the master; ``refresh`` downloads it again first.

        lot_sizes() answers from the day's cache, written from the first master
        read of the day. The pre-rollover check (engine.audit_lot_sizes) wants
        the exchange's word as of the rollover, so it refreshes, then writes
        what it found back into the day's cache so both readers agree.
        """
        if not refresh:
            return await self.lot_sizes(symbols)
        requested = set(symbols)
        found: dict[str, int] = {}
        for exchange in sorted({symbol.split(":", 1)[0] for symbol in requested if ":" in symbol}):
            if exchange not in self.instrument_master_urls:
                continue
            for row in self._master_options(await self._master(exchange, refresh=True)):
                if row.symbol in requested:
                    found[row.symbol] = row.lot_size
        if found:
            today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
            cached: dict[str, int] = {}
            try:
                payload = json.loads(self._lot_cache_path.read_text())
                if payload.get("date") == today:
                    cached = {key: int(value) for key, value in payload.get("lot_sizes", {}).items()}
            except (OSError, ValueError, TypeError):
                pass
            cached.update(found)
            self._lot_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._lot_cache_path.write_text(json.dumps({"date": today, "lot_sizes": cached}, indent=2))
            self._lot_cache_path.chmod(0o600)
        return found

    async def series_lot_sizes(self, exchanges: list[str]) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, Counter]] = {}
        for exchange in dict.fromkeys(exchanges):
            if exchange not in self.instrument_master_urls:
                continue
            for row in self._master_options(await self._master(exchange)):
                counts.setdefault(f"{exchange}:{row.underlying}", {}).setdefault(row.expiry, Counter())[row.lot_size] += 1
        return {root: {expiry: sizes.most_common(1)[0][0] for expiry, sizes in series.items()}
                for root, series in counts.items()}

    async def _master(self, exchange: str, *, refresh: bool = False) -> str:
        """One exchange's FO instrument master, fetched at most once a day.

        Three readers (lot sizes, held-contract metadata, futures series) each
        downloaded the 15 MB file on every call, and contract selection now
        reads it too. The day's copy is kept in memory and beside the contract
        snapshot, so a restart re-reads the disk copy instead of the network.
        ``refresh`` forces a download: NSE adds strikes during the session,
        and a symbol missing from the morning copy may be listed now.
        """
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        held = self._master_text.get(exchange)
        if held and held[0] == today and not refresh:
            return held[1]
        path = self._master_dir / f"fyers_master_{exchange}_FO.csv"
        if not refresh:
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo("Asia/Kolkata")).date().isoformat()
                if modified == today:
                    text = path.read_text()
                    self._master_text[exchange] = (today, text)
                    return text
            except OSError:
                pass
        url = self.instrument_master_urls.get(exchange)
        if not url:
            return ""
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = None
            for attempt in range(4):
                response = await client.get(url)
                if response.status_code != 429:
                    break
                await asyncio.sleep(2 ** attempt)
            assert response is not None
            response.raise_for_status()
            text = response.text
        self._master_text[exchange] = (today, text)
        try:
            self._master_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        except OSError:
            pass  # the in-memory copy still serves this process
        return text

    @staticmethod
    def _master_options(text: str) -> list[OptionContractMetadata]:
        rows: list[OptionContractMetadata] = []
        for row in csv.reader(io.StringIO(text)):
            # Fyers FO master columns (zero based): lot=3, expiry=8,
            # ticker=9, root=13, strike=15 and option side=16.
            if len(row) <= 16:
                continue
            option_type = str(row[16]).upper()
            if option_type not in {"CE", "PE"}:
                continue
            try:
                lot_size = int(float(row[3]))
                expiry = datetime.fromtimestamp(float(row[8]), UTC).astimezone(
                    ZoneInfo("Asia/Kolkata")
                ).date().isoformat()
                strike = float(row[15])
            except (TypeError, ValueError, OSError):
                continue
            if lot_size > 0:
                rows.append(OptionContractMetadata(
                    symbol=row[9], underlying=str(row[13]), option_type=option_type,
                    strike=strike, expiry=expiry, lot_size=lot_size,
                ))
        return rows

    async def option_master(self, exchanges: list[str]) -> list[OptionContractMetadata]:
        rows: list[OptionContractMetadata] = []
        for exchange in dict.fromkeys(exchanges):
            if exchange in self.instrument_master_urls:
                rows.extend(self._master_options(await self._master(exchange)))
        return rows

    async def option_contract_metadata(self, symbols: list[str]) -> dict[str, OptionContractMetadata]:
        """Rehydrate arbitrary held contracts from the authoritative Fyers master."""
        requested = set(symbols)
        result: dict[str, OptionContractMetadata] = {}
        exchanges = {symbol.split(":", 1)[0] for symbol in requested if ":" in symbol}
        for exchange in exchanges:
            if exchange not in self.instrument_master_urls:
                continue
            for refresh in (False, True):
                for row in self._master_options(await self._master(exchange, refresh=refresh)):
                    if row.symbol in requested:
                        result[row.symbol] = row
                wanted = {symbol for symbol in requested if symbol.startswith(exchange + ":")}
                if wanted <= result.keys():
                    break
        return result

    async def futures_series(self, symbols: list[str]) -> dict[str, list[FuturesSeries]]:
        """Every listed futures series for each requested symbol's root.

        The desk's configured symbols name a root and, usually, one dead
        series. The instrument master is the only authority on which series
        are actually listed — the expiry calendar is an exchange decision that
        has changed twice in two years, so it must not be recomputed here.
        """
        wanted: dict[tuple[str, str], list[str]] = {}
        for symbol in symbols:
            parsed = futures_root(symbol)
            if parsed:
                wanted.setdefault(parsed, []).append(symbol)
        if not wanted:
            return {}
        found: dict[tuple[str, str], list[FuturesSeries]] = {key: [] for key in wanted}
        for exchange in {key[0] for key in wanted}:
            if exchange not in self.instrument_master_urls:
                continue
            for row in csv.reader(io.StringIO(await self._master(exchange))):
                # Same FO master layout as the option lookup: lot=3,
                # expiry=8, ticker=9, root=13, option side=16. Futures
                # carry side "XX" and a negative strike.
                if len(row) <= 16 or str(row[16]).upper() in {"CE", "PE"}:
                    continue
                key = futures_root(str(row[9]))
                if key is None or key not in found or key[0] != exchange:
                    continue
                try:
                    expiry = datetime.fromtimestamp(float(row[8]), UTC).astimezone(
                        ZoneInfo("Asia/Kolkata")
                    ).date().isoformat()
                    lot_size = int(float(row[3]))
                except (TypeError, ValueError, OSError):
                    continue
                found[key].append(FuturesSeries(str(row[9]), key[1], expiry, lot_size))
        return {
            symbol: sorted(found[key], key=lambda series: series.expiry)
            for key, requested in wanted.items()
            for symbol in requested
        }

    async def preopen_price(self, symbol: str) -> float | None:
        rows = await self.history(symbol, 60, 500)
        ist = ZoneInfo("Asia/Kolkata")
        candidates = []
        today = datetime.now(ist).date()
        for row in rows:
            moment = datetime.fromtimestamp(row.timestamp, UTC).astimezone(ist)
            if moment.date() == today and (moment.hour, moment.minute) <= (9, 15):
                candidates.append(row)
        return candidates[-1].close if candidates else None

    async def stream(self, symbols: list[str], handler: TickHandler) -> None:
        from fyers_apiv3.FyersWebsocket import data_ws

        # Keep SDK reconnect and subscription behavior, but avoid its fragile
        # 500-symbol token lookup. Apply only once per process.
        converter = data_ws.SymbolConversion
        if not getattr(converter, "_macd_chunked", False):
            converter.symbol_to_hsmtoken = _make_resilient_symbol_conversion(
                converter.symbol_to_hsmtoken)
            converter._macd_chunked = True

        loop = asyncio.get_running_loop()
        tick_buffer = _ThreadsafeTickBuffer(loop)
        self._tick_buffer = tick_buffer
        self.rejected_symbols = []
        self.subscription_notices = []

        def on_message(message: dict) -> None:
            if not isinstance(message, dict) or "ltp" not in message:
                return
            def _num(*keys, cast=float):
                for key in keys:
                    value = message.get(key)
                    if value not in (None, ""):
                        try:
                            return cast(value)
                        except (TypeError, ValueError):
                            continue
                return None

            # Field names verified against the SDK's own map.json:
            # SymbolUpdate full mode ("data_val") carries bid_price/ask_price/
            # bid_size/ask_size/last_traded_qty/tot_buy_qty/tot_sell_qty/OI, so
            # the quote rule needs no separate DepthUpdate subscription.
            # A DepthUpdate message instead names its levels bid_price1..5.
            best_bid = _num("bid_price", "bid_price1")
            best_ask = _num("ask_price", "ask_price1")
            tick = Tick(
                str(message.get("symbol") or message.get("n") or ""),
                float(message["ltp"]),
                int(message.get("vol_traded_today", 0) or 0),
                timestamp=self.exchange_timestamp(message),
                prev_close=float(message.get("prev_close_price", 0) or 0) or None,
                change=float(message.get("ch", message.get("change", 0)) or 0),
                change_pct=float(message.get("chp", message.get("change_percent", 0)) or 0),
                bid=best_bid,
                ask=best_ask,
                bid_qty=_num("bid_size", "bid_size1", cast=int),
                ask_qty=_num("ask_size", "ask_size1", cast=int),
                last_qty=_num("last_traded_qty", "ltq", cast=int),
                total_buy_qty=_num("tot_buy_qty", cast=int),
                total_sell_qty=_num("tot_sell_qty", cast=int),
                # The feed spells it "OI" — lowercase never matched.
                open_interest=_num("OI", "oi", cast=int),
                avg_trade_price=_num("avg_trade_price"),
            )
            # The Fyers SDK invokes this callback from its own thread.  An
            # unbounded run_coroutine_threadsafe call per message accumulated
            # pending tasks until Docker OOM-killed the API.  The bounded
            # handoff preserves order while capacity is available and drops
            # the oldest queued snapshot under sustained overload.
            tick_buffer.put(tick)

        connected = asyncio.Event()
        socket_error = asyncio.Event()
        subscribing = False
        subscription_error: str | None = None

        def on_connect() -> None:
            nonlocal subscribing, subscription_error
            subscribing = True
            subscription_error = None
            try:
                self._socket.subscribe(symbols=symbols, data_type="SymbolUpdate")
            except Exception as exc:
                subscription_error = str(exc)[:500]
                loop.call_soon_threadsafe(socket_error.set)
            finally:
                subscribing = False
            if subscription_error is None:
                loop.call_soon_threadsafe(connected.set)

        def on_error(*details: object) -> None:
            nonlocal subscription_error
            if _is_partial_subscription_notice(details[0] if details else None):
                # The SDK subscribes every valid symbol and only REPORTS the
                # invalid ones (or that an index has no depth). On 5 Oct one
                # expired SENSEX weekly still held by the blast book turned
                # this notice into a fatal "subscription failed": the stream
                # coroutine exited while 1,609 good symbols kept streaming
                # into a buffer nobody read, for the whole session.
                notice = details[0]
                self.rejected_symbols = sorted(set(self.rejected_symbols) | {
                    str(s) for s in (notice.get("invalid_symbols") or [])})
                message = str(notice.get("message") or "")[:200]
                if message and message not in self.subscription_notices:
                    self.subscription_notices = [*self.subscription_notices, message][-5:]
                return
            # The SDK reconnects internally and used to have this callback
            # discarded. Wake the coroutine so it can distinguish a transient
            # socket fault from an expired REST session.
            if subscribing:
                subscription_error = str(details[0])[:500] if details else "unknown FYERS subscription error"
            loop.call_soon_threadsafe(socket_error.set)

        self._socket = data_ws.FyersDataSocket(
            access_token=self.authorization,
            log_path="",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=on_connect,
            on_close=lambda *_: None,
            on_error=on_error,
            on_message=on_message,
        )
        await _in_sdk_pool(self._socket.connect)
        deadline = loop.time() + 20
        while not connected.is_set():
            if socket_error.is_set():
                socket_error.clear()
                if not await self.validate_session():
                    self.connected = False
                    raise RuntimeError("Fyers access token is invalid or expired")
                if subscription_error:
                    raise RuntimeError(f"Fyers subscription failed: {subscription_error}")
            if loop.time() >= deadline:
                if not await self.validate_session():
                    self.connected = False
                    raise RuntimeError("Fyers access token is invalid or expired")
                raise RuntimeError("Fyers websocket did not connect within 20 seconds")
            await asyncio.sleep(0.1)
        handled_since_yield = 0
        while self.connected:
            if socket_error.is_set():
                socket_error.clear()
                try:
                    authenticated = await self.validate_session()
                except (httpx.HTTPError, ValueError):
                    # Do not turn a transient profile/network failure into a
                    # false auth failure; the websocket SDK may still recover.
                    authenticated = True
                if not authenticated:
                    self.connected = False
                    raise RuntimeError("Fyers access token is invalid or expired")
            try:
                tick = await asyncio.wait_for(tick_buffer.get(), timeout=1)
            except TimeoutError:
                continue
            try:
                await handler(tick)
            except Exception as exc:  # noqa: BLE001 -- a handler bug must never end the market-data stream
                self.handler_errors += 1
                self.last_handler_error = f"{tick.symbol}: {type(exc).__name__}: {exc}"[:300]
            handled_since_yield += 1
            if handled_since_yield >= 64:
                handled_since_yield = 0
                await asyncio.sleep(0)

    async def place_order(self, order: Order) -> str:
        payload = {
            "symbol": order.symbol,
            "qty": order.quantity,
            "type": 2 if order.order_type == "MARKET" else 1,
            "side": 1 if order.side == "BUY" else -1,
            "productType": "INTRADAY",
            "limitPrice": order.limit_price or 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self.api_url}/orders/sync", json=payload, headers={"Authorization": self.authorization})
            response.raise_for_status()
            result = response.json()
        if result.get("s") != "ok":
            raise RuntimeError(result.get("message", "Fyers rejected order"))
        return str(result.get("id"))

    async def close(self) -> None:
        self.connected = False
        if self._socket is not None:
            for method in ("close_connection", "disconnect"):
                close = getattr(self._socket, method, None)
                if close:
                    await _in_sdk_pool(close)
                    break


def create_broker(settings: Settings) -> Broker:
    return FyersBroker(settings) if settings.feed_mode == "fyers" else SimulationBroker()


def describe_fyers_token(text: str) -> dict:
    """What a pasted value is, before Fyers is asked: a redirect address, an auth code, an access token.

    Reads the JWT claims only to classify (no signature check -- Fyers remains the judge of validity):
    ``sub`` says "auth_code" or "access_token"; ``exp`` dates it; ``app_id`` (auth codes) names the app.
    """
    from urllib.parse import parse_qs, urlparse
    value = (text or "").strip()
    out: dict = {"kind": "unknown", "value": value, "expires_at": None, "app_id": None}
    if "://" in value or "auth_code=" in value:
        query = parse_qs(urlparse(value).query if "://" in value else value.split("?", 1)[-1])
        # Only auth_code: on a Fyers redirect "code" is a status (200, -1), not the authorisation.
        code = (query.get("auth_code") or [None])[0]
        out.update(kind="redirect", value=code)
        if not code:
            return out
        value = code
    if value.count(".") != 2:
        return out
    try:
        import base64
        import json as _json
        part = value.split(".")[1]
        part += "=" * (-len(part) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(part))
    except Exception:  # noqa: BLE001 -- not a JWT we can read: let Fyers decide
        return out
    subject = str(claims.get("sub") or "").lower()
    if out["kind"] == "redirect" or subject == "auth_code" or "login" in str(claims.get("iss") or ""):
        out["kind"] = "auth_code"
    elif subject == "access_token" or claims.get("fy_id") or claims.get("at_hash"):
        out["kind"] = "access_token"
    if claims.get("exp"):
        out["expires_at"] = datetime.fromtimestamp(float(claims["exp"]), UTC)
    out["app_id"] = claims.get("app_id")
    return out
