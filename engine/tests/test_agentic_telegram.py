"""Telegram bridge for the agentic lane: pairing, commands from the paired chat only, decisions pushed out."""
import asyncio
import json

import httpx

from macd_trader.agentic_telegram import TelegramBridge, cycle_text
from test_agentic import NOW, PE, FakeClient, act, build, candidate_id, decision

TOKEN = "123456789:AAHfakeTokenForTestsOnly_abcdefghijk"


class FakeTelegram:
    """Records sendMessage calls and serves queued getUpdates batches."""

    def __init__(self):
        self.sent: list[dict] = []
        self.updates: list[list[dict]] = []
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/sendMessage"):
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {}})
        if request.url.path.endswith("/getUpdates"):
            batch = self.updates.pop(0) if self.updates else []
            return httpx.Response(200, json={"ok": True, "result": batch})
        return httpx.Response(404, json={"ok": False, "description": "nope"})


def message(update_id, chat_id, text, name="owner"):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "first_name": name}, "text": text}}


def setup(tmp_path, *decisions):
    lane = build(tmp_path, *decisions)
    fake = FakeTelegram()
    closed = []

    async def close_position(name, symbol, quantity):
        closed.append((name, symbol, quantity))
        await lane.execution.submit(symbol, "SELL", quantity)
        return {"ok": True}

    bridge = TelegramBridge(lane, lane.settings, close_position=close_position, transport=httpx.MockTransport(fake))
    lane.telegram = bridge
    bridge.update(bot_token=TOKEN)
    return lane, bridge, fake, closed


def test_pairing_needs_the_app_code_and_then_ignores_other_chats(tmp_path):
    lane, bridge, fake, _ = setup(tmp_path)
    status = bridge.status()
    assert status["configured"] and not status["paired"] and len(status["pairing_code"]) == 6
    assert TOKEN not in json.dumps(status) and status["token_hint"] == "…hijk"
    assert asyncio.run(bridge.handle(message(1, 42, "/status")["message"])).startswith("Not paired")
    assert asyncio.run(bridge.handle(message(2, 42, "/pair 000000")["message"])).startswith("Not paired")
    reply = asyncio.run(bridge.handle(message(3, 42, f"/pair {status['pairing_code']}")["message"]))
    assert reply.startswith("Paired") and bridge.status()["paired"] and bridge.status()["pairing_code"] is None
    assert asyncio.run(bridge.handle(message(4, 99, "/pause")["message"])) == "This bot is paired with another chat."
    assert lane.paused is False
    bridge.update(bot_token="987654321:AAHanotherFakeTokenForTests_zyxwvut")
    assert not bridge.status()["paired"]                       # a new bot pairs again


def test_commands_status_think_pause_and_close_with_confirmation(tmp_path):
    lane, bridge, fake, closed = setup(tmp_path)
    bridge._pair(42, "owner")
    lane.client = FakeClient(decision(act("open", candidate_id(lane, PE), lots=2, stop=80, target=150, reason="index put")))

    async def scenario():
        await lane.think("test", None, NOW)
        status = await bridge.handle(message(1, 42, "/status")["message"])
        positions = await bridge.handle(message(2, 42, "/positions")["message"])
        assert await bridge.handle(message(3, 42, "/pause")["message"]) and lane.paused
        await bridge.handle(message(4, 42, "/resume")["message"])
        ask = await bridge.handle(message(5, 42, "/close P01")["message"])
        done = await bridge.handle(message(6, 42, "/confirm")["message"])
        again = await bridge.handle(message(7, 42, "/confirm")["message"])
        await asyncio.sleep(0)                                   # let fire-and-forget notifications run
        return status, positions, ask, done, again

    status, positions, ask, done, again = asyncio.run(scenario())
    assert "Equity" in status and "P01 NIFTY26OCT23000PE x2" in status
    assert "stop 80.0 / target 150.0" in positions and "thesis: index put" in positions
    assert "Send /confirm" in ask and done.startswith("Closed") and again.startswith("Nothing to confirm")
    assert closed == [("agentic", PE, 150)] and not lane.held_symbols() and lane.paused is False


def test_cycle_and_exits_are_pushed_to_the_paired_chat(tmp_path):
    lane, bridge, fake, _ = setup(tmp_path)
    bridge._pair(42, "owner")
    lane.client = FakeClient(decision(act("open", candidate_id(lane, PE), lots=1, stop=80, reason="breadth weak"),
                                      view="Weak tape."))

    async def scenario():
        await lane.think("test", None, NOW)
        await lane.execution.on_tick(PE, 79.0)                     # stop hit
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(scenario())
    texts = [m["text"] for m in fake.sent]
    assert any("Weak tape." in t and "OPEN NIFTY26OCT23000PE x1" in t and "Why: breadth weak" in t for t in texts)
    assert any("Stop hit: closed NIFTY26OCT23000PE" in t for t in texts)
    assert all(m["chat_id"] == "42" for m in fake.sent) and all(TOKEN not in t for t in texts)


def test_polling_skips_the_backlog_then_answers_new_commands(tmp_path):
    lane, bridge, fake, _ = setup(tmp_path)
    bridge._pair(42, "owner")
    fake.updates = [[message(10, 42, "/pause")], [message(11, 42, "/status")]]

    async def scenario():
        await bridge._poll_once()          # backlog: /pause from before the restart is skipped
        await bridge._poll_once()

    asyncio.run(scenario())
    assert lane.paused is False and bridge._offset == 12
    assert len(fake.sent) == 1 and "Equity" in fake.sent[0]["text"]


def test_cycle_text_reads_like_a_trader_note():
    text = cycle_text({"at": "2026-10-21T10:20:00+05:30", "regime": "bearish", "confidence": 0.6, "mode": "grok",
                       "market_view": "Grind lower.", "accepted": [{"action": "set_levels", "symbol": "NSE:X", "stop_premium": 230,
                                                                     "target_premium": None, "reason": "lock gains"}],
                       "executed": [{"status": "done"}], "rejected": [{"action": "open", "rejected": "market closed"}]},
                      {"equity": 1_005_000, "day_pnl": 5000, "open_positions": 1})
    assert text.startswith("Agentic 10:20 | bearish 60%") and "SET LEVELS X (stop 230)" in text
    assert "Blocked: open (market closed)" in text and "today Rs 5,000" in text
