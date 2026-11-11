"""Alert health tells the owner when Telegram cannot work, and a backing-off exit alerts once per cooldown."""
import asyncio
import httpx
from types import SimpleNamespace

from macd_trader.alerts import AlertManager


def manager(token="123456:abc-DEF", chat="123456", failures=(), runtime=None):
    health = {"status": "connected", "last_tick_age_seconds": 1, "loop_lag_ms": {}, "exit_failures": list(failures)}
    engine = SimpleNamespace(settings=SimpleNamespace(telegram_bot_token=token, telegram_chat_id=chat, day_loss_alert_rupees=0,
                                                      runtime_settings_path=runtime),
                             health=lambda: health, day_baseline_equity=lambda: None)
    return AlertManager(engine)


def test_a_chat_id_equal_to_the_bot_id_is_flagged():
    assert manager().status()["chat_is_bot"] is True
    assert manager(chat="987").status()["chat_is_bot"] is False
    assert manager(token="", chat="").status()["configured"] is False


def test_a_bot_username_as_the_chat_is_flagged():
    """11 Nov 2026: the chat id was a bot's @username; every alert got 403 "the bot can't send messages to the bot"."""
    assert manager(chat="@macd_alerts_bot").status()["chat_is_bot"] is True
    assert manager(chat="@Some_Bot").status()["chat_is_bot"] is True
    assert manager(chat="@owner_channel").status()["chat_is_bot"] is False
    assert manager(chat="-100123456").status()["chat_is_bot"] is False


def test_the_bot_token_never_leaves_in_an_error():
    alerts = manager(chat="987")
    alerts.last_error = "ConnectError for https://api.telegram.org/bot123456:abc-DEF/sendMessage"
    assert "abc-DEF" not in alerts.status()["last_error"] and "bot<redacted>" in alerts.status()["last_error"]


def test_a_backing_off_exit_alerts_once_per_book_and_symbol():
    row = {"lane": "macd", "symbol": "NSE:X26NOV1CE", "reason": "HARD_STOP_30_PCT", "count": 4, "first_at": "2026-11-04T10:00:00+05:30",
           "error": "ValueError: lot", "retry_after_seconds": 300.0}
    alerts = manager(chat="987", failures=[row])
    sent = []

    async def capture(key, text):
        sent.append((key, text))
        return key

    alerts._alert = capture
    asyncio.run(alerts.check_once())
    assert [key for key, _ in sent] == ["exit_failing:macd:NSE:X26NOV1CE"]
    assert "rejected 4×" in sent[0][1] and "300s" in sent[0][1]


def test_a_failed_send_is_remembered_across_restarts_for_that_chat_only(tmp_path, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda request: httpx.Response(403, json={"description": "Forbidden: bot can't send messages to bots"})), **kw))
    runtime = str(tmp_path / "settings.json")
    alerts = manager(chat="987", runtime=runtime)
    asyncio.run(alerts._alert("test", "hello"))
    assert "403" in alerts.status()["last_error"]
    saved = (tmp_path / "alerts_status.json").read_text()
    assert "abc-DEF" not in saved and "987" not in saved
    again = manager(chat="987", runtime=runtime)
    assert "403" in again.status()["last_error"] and again.status()["last_result_at"]
    assert manager(chat="555", runtime=runtime).status()["last_error"] is None      # a new chat id starts clean
