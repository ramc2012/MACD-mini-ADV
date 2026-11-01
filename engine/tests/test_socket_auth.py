"""30 Oct 2026: the data socket refused the token (11001) for 2 h 40 min while REST accepted it, and nobody was told."""
import asyncio
import threading

from fyers_apiv3.FyersWebsocket import data_ws

from macd_trader.brokers import FyersBroker, SOCKET_AUTH_MESSAGE, _is_socket_auth_failure
from macd_trader.config import Settings

REFUSED = {"type": "cn", "code": 11001, "message": "Authentication failed", "s": "error"}
ACCEPTED = {"type": "cn", "code": 200, "message": "Authentication done", "s": "ok"}


def settings(tmp_path):
    return Settings(fyers_client_id="APP-100", fyers_access_token="x.y.z",
                    contract_snapshot_path=str(tmp_path / "contracts.json"))


def fake_socket(monkeypatch, script):
    """A FyersDataSocket whose connect() plays `script` (on_error / on_message payloads) from the SDK's thread."""
    class Socket:
        def __init__(self, **kwargs):
            self.kw = kwargs

        def connect(self):
            def play():
                for kind, payload in script:
                    self.kw["on_error" if kind == "error" else "on_message"](payload)
            threading.Thread(target=play).start()

        def subscribe(self, **_):
            pass

        def close_connection(self):
            pass

    monkeypatch.setattr(data_ws, "FyersDataSocket", Socket)


def test_the_sdk_refusal_is_recognised():
    assert _is_socket_auth_failure(REFUSED)
    assert not _is_socket_auth_failure(ACCEPTED)
    assert not _is_socket_auth_failure({"code": -300, "message": "Please provide a valid symbol"})
    assert not _is_socket_auth_failure("socket closed")


def test_repeated_refusals_with_rest_still_valid_end_the_stream_with_the_reason(tmp_path, monkeypatch):
    broker = FyersBroker(settings(tmp_path))
    broker.connected = True

    async def rest_ok():
        return True

    monkeypatch.setattr(broker, "validate_session", rest_ok)
    fake_socket(monkeypatch, [("error", REFUSED)] * 3)

    async def handler(_tick):
        pass

    try:
        asyncio.run(asyncio.wait_for(broker.stream(["NSE:SBIN-EQ"], handler), 10))
    except RuntimeError as exc:
        assert str(exc) == SOCKET_AUTH_MESSAGE
    else:
        raise AssertionError("the stream should give up on a token the socket refuses")
    assert broker.socket_auth_rejected()
    assert broker.tick_buffer_status()["socket_auth_failures"] == 3
    assert broker.connected is False


def test_one_refusal_is_not_decisive_and_a_good_socket_login_clears_it(tmp_path):
    broker = FyersBroker(settings(tmp_path))
    for _ in range(3):
        broker._note_socket_auth_failure()
    assert broker.socket_auth_rejected()
    broker._note_tick()                     # what a tick or {"type": "cn", "s": "ok"} does
    assert not broker.socket_auth_rejected()
    broker._note_socket_auth_failure()
    assert not broker.socket_auth_rejected()


def test_engine_reports_and_alerts_a_refused_socket(tmp_path):
    from macd_trader.alerts import AlertManager
    from macd_trader.engine import TradingEngine

    engine = TradingEngine(Settings(
        symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False, telegram_bot_token="1:x", telegram_chat_id="2",
        database_path=str(tmp_path / "book.sqlite3"), mp_database_path=str(tmp_path / "mp.sqlite3"),
        research_database_path=str(tmp_path / "history.sqlite3"), tick_database_path=str(tmp_path / "ticks.sqlite3"),
        contract_snapshot_path=str(tmp_path / "contracts.json")))
    engine.status = "connected"
    engine.broker.socket_auth_rejected = lambda: True
    assert engine.broker_status()["status"] == "socket_auth_failed"
    assert engine.health()["socket_auth_failed"] is True
    assert engine.feed_alive() is False

    alerts = AlertManager(engine)
    sent = []

    async def capture(key, text):
        sent.append(key)
        return key

    alerts._alert = capture
    asyncio.run(alerts.check_once())
    assert "socket_auth_failed" in sent

    engine.broker.token_expired = lambda: True   # an expired token is reported as that, not as a socket refusal
    assert engine.socket_auth_rejected() is False
