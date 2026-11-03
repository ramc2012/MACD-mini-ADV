"""Alert health tells the owner when Telegram cannot work, and a backing-off exit alerts once per cooldown."""
import asyncio
from types import SimpleNamespace

from macd_trader.alerts import AlertManager


def manager(token="123456:abc-DEF", chat="123456", failures=()):
    health = {"status": "connected", "last_tick_age_seconds": 1, "loop_lag_ms": {}, "exit_failures": list(failures)}
    engine = SimpleNamespace(settings=SimpleNamespace(telegram_bot_token=token, telegram_chat_id=chat, day_loss_alert_rupees=0),
                             health=lambda: health, day_baseline_equity=lambda: None)
    return AlertManager(engine)


def test_a_chat_id_equal_to_the_bot_id_is_flagged():
    assert manager().status()["chat_is_bot"] is True
    assert manager(chat="987").status()["chat_is_bot"] is False
    assert manager(token="", chat="").status()["configured"] is False


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
