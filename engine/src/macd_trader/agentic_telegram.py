"""Telegram for the agentic lane: its decisions pushed to the owner's chat, and the owner's commands back.

Pairing: the bot answers commands only from one chat. While unpaired the app shows a 6-digit code; sending
``/pair <code>`` to the bot from the owner's Telegram binds that chat (saved in runtime/agentic_brain.json).
Anyone else who finds the bot gets nothing but a refusal.

Out: each cycle's market view and actions with reasons, stop/target/floor exits, operator closes, a day-end
summary after 15:35, and model failures (at most one per 30 minutes).
In:  /status /positions /journal /think [note] /pause /resume /close P01 (then /confirm within 60 s) /help

The bot token comes from the lane's panel (agentic_brain.json, owner-only file) or, failing that, the app's
Telegram setting. It is never logged or returned. One long-poll consumer: nothing else reads this bot's updates.
"""
from __future__ import annotations

import asyncio
import secrets
import time as clock
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import httpx

from . import agentic_brain as brain
from . import market_calendar

IST = ZoneInfo("Asia/Kolkata")
API = "https://api.telegram.org"
MAX_TEXT = 3900
ERROR_GAP_SECONDS = 1800
CONFIRM_SECONDS = 60
DAY_END_AT = time(15, 36)
HELP = ("Agentic lane commands:\n"
        "/status - book, P&L, brain, next cycle\n"
        "/positions - open positions with stop, target and thesis\n"
        "/journal - the latest decision\n"
        "/think [note] - run a cycle now, with an optional note to the model\n"
        "/pause, /resume - stop or restart the cycles\n"
        "/close P01 - close a position (then /confirm within 60 s)")


def _rs(value) -> str:
    try:
        return f"Rs {float(value):,.0f}"
    except (TypeError, ValueError):
        return "-"


def _short(symbol: str) -> str:
    return str(symbol).split(":")[-1]


def cycle_text(cycle: dict, book: dict) -> str:
    """One Telegram message for one decision cycle."""
    at = str(cycle.get("at", ""))[11:16]
    conf = cycle.get("confidence")
    head = f"Agentic {at} | {cycle.get('regime') or '-'}" + (f" {round(conf * 100)}%" if isinstance(conf, (int, float)) else "")
    if cycle.get("mode") != "grok":
        head += " | rules-only"
    lines = [head, "", str(cycle.get("market_view") or "").strip()]
    executed = cycle.get("executed") or []
    for i, action in enumerate(cycle.get("accepted") or []):
        done = executed[i] if i < len(executed) else {}
        what = action.get("action", "").upper().replace("_", " ")
        detail = f"{what} {_short(action.get('symbol', ''))}"
        if action.get("lots"):
            detail += f" x{action['lots']}"
        if done.get("price"):
            detail += f" @ {float(done['price']):.2f}"
        levels = [f"stop {action['stop_premium']}" if action.get("stop_premium") else "",
                  f"target {action['target_premium']}" if action.get("target_premium") else ""]
        levels = ", ".join(x for x in levels if x)
        if levels:
            detail += f" ({levels})"
        if action.get("action") == "arm" and action.get("trigger_index"):
            detail += f" when {action['trigger_index']} {action.get('trigger_op')} {action.get('trigger_level')}"
            if action.get("confirm_index"):
                detail += f" and {action['confirm_index']} {action.get('confirm_op')} {action.get('confirm_level')}"
            detail += f" until {action.get('valid_until')}"
        if done.get("status") == "failed":
            detail += f" - FAILED: {done.get('error', '')}"
        lines += ["", detail, f"Why: {str(action.get('reason') or '')[:600]}"]
    if not cycle.get("accepted"):
        lines += ["", "No action."]
    blocked = cycle.get("rejected") or []
    if blocked:
        lines += ["", "Blocked: " + "; ".join(f"{b.get('action')} ({b.get('rejected')})" for b in blocked[:4])]
    if cycle.get("error"):
        lines += ["", f"Model call failed: {str(cycle['error'])[:200]}"]
    lines += ["", f"Equity {_rs(book.get('equity'))} | today {_rs(book.get('day_pnl'))} | open {book.get('open_positions', 0)}"]
    return "\n".join(lines)[:MAX_TEXT]


class TelegramBridge:
    def __init__(self, lane, settings, close_position=None, transport: httpx.AsyncBaseTransport | None = None):
        self.lane = lane
        self.settings = settings
        self.close_position = close_position          # async (lane_name, symbol, quantity) -> dict, from the engine
        self._transport = transport
        self.pairing_code = f"{secrets.randbelow(900000) + 100000}"
        self.last_error: str | None = None
        self.last_sent_at: str | None = None
        self._offset: int | None = None
        self._pending_close: tuple[str, int, float] | None = None
        self._last_error_sent = 0.0
        self._day_end_sent: str | None = None
        self._task: asyncio.Task | None = None
        self._send_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ configuration

    def _config(self) -> dict:
        return brain.load_config(self.lane.runtime_dir)

    @property
    def token(self) -> str:
        return str(self._config().get("telegram_bot_token") or getattr(self.lane.settings, "telegram_bot_token", "") or "").strip()

    @property
    def chat_id(self) -> str:
        return str(self._config().get("telegram_chat_id") or "").strip()

    def status(self) -> dict:
        token = self.token
        return {"configured": bool(token), "token_hint": f"…{token[-4:]}" if len(token) >= 8 else None,
                "token_source": "panel" if self._config().get("telegram_bot_token") else ("settings" if token else "none"),
                "paired": bool(self.chat_id), "chat_name": self._config().get("telegram_chat_name"),
                "pairing_code": None if self.chat_id or not token else self.pairing_code,
                "polling": self._task is not None and not self._task.done(), "last_error": self.last_error,
                "last_sent_at": self.last_sent_at}

    def update(self, *, bot_token: str | None = None, clear_token: bool = False, unpair: bool = False) -> dict:
        store = brain.brain_store(self.lane.runtime_dir)
        config = store.load()
        if clear_token:
            config.pop("telegram_bot_token", None)
        elif bot_token is not None and bot_token.strip():
            token = bot_token.strip()
            if ":" not in token or len(token) < 30:
                raise ValueError("that does not look like a Telegram bot token (expected 123456:ABC...)")
            if token != config.get("telegram_bot_token"):
                config["telegram_bot_token"] = token
                config.pop("telegram_chat_id", None)          # a new bot must be paired again
                config.pop("telegram_chat_name", None)
        if unpair:
            config.pop("telegram_chat_id", None)
            config.pop("telegram_chat_name", None)
        store.save(config)
        self.pairing_code = f"{secrets.randbelow(900000) + 100000}"
        self._offset = None
        return self.status()

    def _pair(self, chat_id: int, name: str) -> None:
        store = brain.brain_store(self.lane.runtime_dir)
        config = store.load()
        config["telegram_chat_id"] = str(chat_id)
        config["telegram_chat_name"] = name[:80]
        store.save(config)

    # ------------------------------------------------------------------ Telegram API

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def _call(self, method: str, payload: dict, timeout: float = 20.0) -> dict:
        token = self.token
        if not token:
            raise RuntimeError("no bot token")
        async with self._client(timeout) as client:
            response = await client.post(f"{API}/bot{token}/{method}", json=payload)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code != 200 or not body.get("ok"):
            # the token is in the URL only; the description never echoes it
            raise RuntimeError(f"Telegram {method} HTTP {response.status_code}: {str(body.get('description', ''))[:160]}")
        return body

    async def send(self, text: str, chat_id: str | None = None) -> bool:
        chat = chat_id or self.chat_id
        if not (self.token and chat):
            return False
        try:
            await self._call("sendMessage", {"chat_id": chat, "text": text[:MAX_TEXT], "disable_web_page_preview": True})
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            self.last_error = str(exc)[:200]
            return False
        self.last_sent_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.last_error = None
        return True

    def notify(self, text: str) -> None:
        """Fire and forget from the lane; never blocks or raises into trading code."""
        if not (self.token and self.chat_id):
            return
        try:
            task = asyncio.get_running_loop().create_task(self.send(text))
        except RuntimeError:
            return
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)

    # ------------------------------------------------------------------ lane events

    def on_cycle(self, cycle: dict, book: dict) -> None:
        """Every decision is sent; a cycle that only repeats a model failure is sent at most every 30 minutes."""
        if cycle.get("error") and not cycle.get("accepted"):
            if clock.monotonic() - self._last_error_sent < ERROR_GAP_SECONDS:
                return
            self._last_error_sent = clock.monotonic()
        self.notify(cycle_text(cycle, book))

    def on_exit(self, symbol: str, reason: str, price: float | None) -> None:
        label = {"AGENT_STOP": "Stop hit", "AGENT_TARGET": "Target hit", "EXPIRY_DAY_FLATTEN": "Expiry-day exit"}.get(
            reason, reason.replace("_", " ").title())
        self.notify(f"Agentic | {label}: closed {_short(symbol)}" + (f" @ {price:.2f}" if price else ""))

    # ------------------------------------------------------------------ commands

    def _book_lines(self) -> list[str]:
        snap = self.lane.portfolio.snapshot()
        day = self.lane._day_pnl(snap)
        health = self.lane.health()
        return [f"Equity {_rs(snap['equity'])} | today {_rs(day)} | cash {_rs(snap['cash'])}",
                f"Brain {health['brain']} ({health['model']}) | {'PAUSED' if self.lane.paused else 'running'} | "
                f"every {health['cycle_minutes']} min | next {str(health.get('next_cycle_at') or '-')[11:16]}"]

    def _positions(self) -> list[tuple[str, str, object]]:
        out = []
        for i, (symbol, p) in enumerate(((s, p) for s, p in self.lane.portfolio.positions.items() if p.quantity > 0), 1):
            out.append((f"P{i:02d}", symbol, p))
        return out

    def _positions_text(self, detail: bool) -> str:
        rows = self._positions()
        if not rows:
            return "No open positions."
        lines = []
        for pid, symbol, p in rows:
            meta = self.lane.meta.get(symbol, {})
            price = self.lane._price(symbol) or p.last_price
            ret = (price / p.average_price - 1) * 100 if p.average_price else 0.0
            lines.append(f"{pid} {_short(symbol)} x{p.lots} | {p.average_price:.2f} -> {price:.2f} ({ret:+.1f}%, "
                         f"{_rs((price - p.average_price) * p.quantity)})")
            if detail:
                lines.append(f"   stop {meta.get('stop_premium') or '-'} / target {meta.get('target_premium') or '-'}")
                if meta.get("thesis"):
                    lines.append(f"   thesis: {str(meta['thesis'])[:300]}")
        return "\n".join(lines)

    async def handle(self, message: dict) -> str | None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = str(message.get("text") or "").strip()
        if not text or chat_id is None:
            return None
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()
        if not self.chat_id:
            if command == "/pair" and rest.strip() == self.pairing_code:
                name = chat.get("username") or chat.get("first_name") or str(chat_id)
                self._pair(chat_id, str(name))
                self.pairing_code = f"{secrets.randbelow(900000) + 100000}"
                return "Paired. This chat now receives the agentic lane's decisions and can command it.\n\n" + HELP
            return "Not paired. Open the app's Agentic lane > Telegram panel and send /pair <code> shown there."
        if str(chat_id) != self.chat_id:
            return "This bot is paired with another chat."
        if command in ("/start", "/help"):
            return HELP
        if command == "/status":
            return "\n".join([*self._book_lines(), "", self._positions_text(False)])
        if command == "/positions":
            return self._positions_text(True)
        if command == "/journal":
            last = self.lane.recent_cycles(limit=1)
            if not last:
                return "No cycles yet."
            return cycle_text(last[0], {"equity": self.lane.portfolio.snapshot()["equity"], "day_pnl": None,
                                        "open_positions": len(self.lane.held_symbols())})
        if command == "/think":
            self.lane._manual_task = asyncio.get_running_loop().create_task(
                self.lane.think("telegram request", rest.strip()[:1000] or None))
            return "Thinking... the decision follows here in about a minute."
        if command == "/pause":
            self.lane.paused = True
            return "Paused: no scheduled cycles. Stops and targets still act. /resume to restart."
        if command == "/resume":
            self.lane.paused = False
            return "Resumed."
        if command == "/close":
            pid = rest.strip().upper()
            match = next(((s, p) for i, s, p in self._positions() if i == pid), None)
            if not match:
                return "Usage: /close P01 (see /positions)."
            symbol, position = match
            self._pending_close = (symbol, position.quantity, clock.monotonic())
            return f"Close {_short(symbol)} x{position.lots} at market (paper)? Send /confirm within {CONFIRM_SECONDS} s."
        if command == "/confirm":
            pending, self._pending_close = self._pending_close, None
            if not pending or clock.monotonic() - pending[2] > CONFIRM_SECONDS:
                return "Nothing to confirm (or it expired)."
            if self.close_position is None:
                return "Closing from Telegram is not available."
            symbol, quantity, _ = pending
            try:
                await self.close_position(self.lane.name, symbol, quantity)
            except (ValueError, RuntimeError, PermissionError) as exc:
                return f"Close failed: {exc}"
            self.lane.note_operator_close(symbol)
            return f"Closed {_short(symbol)}."
        return "Unknown command.\n\n" + HELP

    # ------------------------------------------------------------------ loops

    async def _poll_once(self) -> None:
        if self._offset is None:
            # Start after anything already waiting: old commands must not run on a restart.
            body = await self._call("getUpdates", {"offset": -1, "timeout": 0})
            results = body.get("result") or []
            self._offset = (results[-1]["update_id"] + 1) if results else 0
            return
        body = await self._call("getUpdates", {"offset": self._offset, "timeout": 25,
                                               "allowed_updates": ["message"]}, timeout=40.0)
        for update in body.get("result") or []:
            self._offset = update["update_id"] + 1
            message = update.get("message") or {}
            reply = await self.handle(message)
            if reply:
                await self.send(reply, str((message.get("chat") or {}).get("id")))

    def _day_end(self, now: datetime) -> None:
        day = now.date().isoformat()
        if (self._day_end_sent == day or not market_calendar.is_trading_day(now.date())
                or now.time() < DAY_END_AT or not self.chat_id):
            return
        self._day_end_sent = day
        cycles = self.lane.recent_cycles(day=day, limit=100)
        actions = [a for a in self.lane.actions(200) if str(a.get("at", ""))[:10] == day]
        self.notify("\n".join([f"Agentic | day end {day}", *self._book_lines(),
                               f"{len(cycles)} cycles, {sum(1 for a in actions if a['action'] == 'open')} entries, "
                               f"{sum(1 for a in actions if a['action'] in ('close', 'reduce'))} exits", "",
                               self._positions_text(False)]))

    async def run(self) -> None:
        backoff = 5.0
        while True:
            try:
                self._day_end(datetime.now(IST))
                if not self.token:
                    await asyncio.sleep(10)
                    continue
                await self._poll_once()
                backoff = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- the bridge must never take the lane down
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300.0)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
