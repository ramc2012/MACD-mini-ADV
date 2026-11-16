import asyncio
import httpx
import pytest
from types import SimpleNamespace

from macd_trader.brokers import FyersBroker, FyersConnectionError, _ThreadsafeTickBuffer
from macd_trader.config import Settings
from macd_trader.engine import TradingEngine
from macd_trader.events import EventHub
from macd_trader.models import Tick


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("unexpected raise_for_status call")

    def json(self):
        return self._payload


class FakeClient:
    response = FakeResponse(200, {"s": "ok"})

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        return self.response


def broker():
    return FyersBroker(Settings(
        feed_mode="fyers",
        fyers_client_id="CLIENT-100",
        fyers_access_token="token-value",
    ))


def test_fyers_tick_buffer_is_bounded_and_reports_oldest_drop():
    async def exercise():
        buffer = _ThreadsafeTickBuffer(asyncio.get_running_loop(), capacity=2)
        buffer.put(Tick("NSE:ONE", 1))
        buffer.put(Tick("NSE:TWO", 2))
        buffer.put(Tick("NSE:THREE", 3))

        assert buffer.status() == {"pending": 2, "capacity": 2, "dropped": 1}
        assert (await buffer.get()).symbol == "NSE:TWO"
        assert (await buffer.get()).symbol == "NSE:THREE"
        assert buffer.status() == {"pending": 0, "capacity": 2, "dropped": 1}

    asyncio.run(exercise())


def test_fyers_tick_buffer_wakes_after_becoming_empty():
    async def exercise():
        buffer = _ThreadsafeTickBuffer(asyncio.get_running_loop(), capacity=2)

        async def publish():
            await asyncio.sleep(0)
            buffer.put(Tick("NSE:LATE", 4))

        task = asyncio.create_task(publish())
        assert (await asyncio.wait_for(buffer.get(), timeout=1)).symbol == "NSE:LATE"
        await task

    asyncio.run(exercise())


def test_fyers_auth_requires_explicit_success_marker(monkeypatch):
    monkeypatch.setattr("macd_trader.brokers.httpx.AsyncClient", FakeClient)
    FakeClient.response = FakeResponse(200, {"code": -8, "message": "expired"})
    assert asyncio.run(broker().validate_session()) is False


def test_fyers_auth_rejects_unauthorized_response(monkeypatch):
    monkeypatch.setattr("macd_trader.brokers.httpx.AsyncClient", FakeClient)
    FakeClient.response = FakeResponse(401, {"s": "error", "code": -8})
    assert asyncio.run(broker().validate_session()) is False


def test_fyers_auth_accepts_explicit_ok(monkeypatch):
    monkeypatch.setattr("macd_trader.brokers.httpx.AsyncClient", FakeClient)
    FakeClient.response = FakeResponse(200, {"s": "ok", "data": {}})
    assert asyncio.run(broker().validate_session()) is True


def test_connect_reports_dns_failure_without_blaming_the_saved_token(monkeypatch):
    class UnreachableProfile(FakeClient):
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("[Errno -2] Name or service not known")

    monkeypatch.setattr("macd_trader.brokers.httpx.AsyncClient", UnreachableProfile)
    client = broker()
    client.connected = True
    with pytest.raises(FyersConnectionError, match="could not be resolved"):
        asyncio.run(client.connect())
    assert client.connected is False
    assert client.token_expired() is False


def test_profile_outage_is_not_an_auth_rejection(monkeypatch):
    class UnavailableProfile(FakeClient):
        async def get(self, *args, **kwargs):
            return httpx.Response(503, request=httpx.Request("GET", "https://broker.example/profile"))

    monkeypatch.setattr("macd_trader.brokers.httpx.AsyncClient", UnavailableProfile)
    with pytest.raises(FyersConnectionError, match="HTTP 503"):
        asyncio.run(broker().validate_session())


def test_watchdog_reports_failed_recovery_then_retries(monkeypatch):
    from macd_trader import engine as module

    async def run():
        engine = object.__new__(TradingEngine)
        engine.broker = broker()
        engine.status = "connected"
        engine.error = None
        engine._reconfigure_lock = asyncio.Lock()
        engine._last_feed_recovery = float("-inf")
        engine.feed_recoveries = 0
        engine.all_symbols = []
        engine.option_symbols = []
        engine.settings = SimpleNamespace(symbols=[])
        engine.events = EventHub()
        engine.seconds_since_last_tick = lambda: 600.0
        monkeypatch.setattr(module, "regular_session_open", lambda: True)
        monkeypatch.setattr(module, "FEED_RECOVERY_COOLDOWN", 0)
        assert engine.observed_feed_status() == "stale"

        async def preopen():
            return False

        async def failed():
            raise FyersConnectionError("FYERS DNS unavailable")

        engine._preopen_refresh = preopen
        engine.restart_stream = failed
        await engine._watchdog_sweep()
        assert engine.broker_status()["status"] == "error"
        assert "DNS" in engine.broker_status()["error"]
        assert not engine.broker_status()["token_expired"]

        async def recovered():
            engine.status = "connected"
            engine.error = None
            engine.seconds_since_last_tick = lambda: 0.0

        engine.restart_stream = recovered
        await engine._watchdog_sweep()
        assert engine.broker_status()["status"] == "connected"
        assert engine.feed_recoveries == 2

    asyncio.run(run())


def test_engine_clears_connected_state_when_session_expires():
    class ExpiredBroker:
        name = "fyers"
        connected = True

        async def validate_session(self):
            return False

        async def close(self):
            self.connected = False

    engine = object.__new__(TradingEngine)
    engine.broker = ExpiredBroker()
    engine.status = "connected"
    engine.error = None
    engine._stream_task = None
    engine._reconfigure_lock = asyncio.Lock()
    engine._auth_validated_date = "2026-08-18"
    engine.events = EventHub()
    engine.all_symbols = []
    engine.option_symbols = []
    engine.settings = SimpleNamespace(symbols=[])

    assert asyncio.run(engine._validate_broker_session("2026-08-19")) is False
    assert engine.status == "error"
    assert engine.broker.connected is False
    assert "expired" in engine.error


def test_disconnect_feed_ignores_completed_stream_error():
    class ExpiredBroker:
        closed = False

        async def close(self):
            self.closed = True

    async def exercise():
        async def failed_stream():
            raise RuntimeError("Fyers access token is invalid or expired")

        engine = object.__new__(TradingEngine)
        engine.broker = ExpiredBroker()
        engine._stream_task = asyncio.create_task(failed_stream())
        await asyncio.sleep(0)

        await engine.disconnect_feed()

        assert engine.broker.closed is True
        assert engine._stream_task is None

    asyncio.run(exercise())


def test_disconnect_feed_does_not_wait_forever_for_sdk_close(monkeypatch):
    class HungBroker:
        async def close(self):
            await asyncio.Event().wait()

    async def exercise():
        engine = object.__new__(TradingEngine)
        engine.broker = HungBroker()
        engine._stream_task = None
        monkeypatch.setattr("macd_trader.engine.FEED_STEP_TIMEOUT", 0.01)

        await asyncio.wait_for(engine.disconnect_feed(), timeout=0.1)

    asyncio.run(exercise())
