"""Telegram alerting for conditions that need the owner's attention.

Checks run every 30 seconds; each alert key has a cooldown so a persistent
condition nags every 30 minutes instead of every cycle. Alerts are sent only
when a bot token and chat id are configured — otherwise the manager idles.

The whale tracker's composite alerts arrive here through a queue the engine's
minute loop fills, so Telegram's round trip never sits on that loop. Telegram
carries the one-line read; the evidence stays on the dashboard.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import httpx

from . import whale
from .market_calendar import is_trading_day

CHECK_INTERVAL_SECONDS = 30.0
COOLDOWN_SECONDS = 1800.0
FEED_STALE_SECONDS = 180.0
LOOP_LAG_ALERT_MS = 500.0
IST = ZoneInfo("Asia/Kolkata")


def market_hours(now: datetime | None = None) -> bool:
    moment = (now or datetime.now(IST)).astimezone(IST)
    return is_trading_day(moment.date()) and dtime(9, 15) <= moment.time() <= dtime(15, 30)


class AlertManager:
    def __init__(self, engine):
        self.engine = engine
        self._task: asyncio.Task | None = None
        self._last_sent: dict[str, float] = {}
        self.sent_total = 0
        self.last_message: str | None = None
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        settings = self.engine.settings
        return bool(settings.telegram_bot_token and settings.telegram_chat_id)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            try:
                await self.check_once()
            except Exception as exc:  # noqa: BLE001 — alerting must never kill itself
                self.last_error = str(exc)

    async def check_once(self, now: datetime | None = None) -> list[str]:
        fired: list[str] = []
        engine = self.engine
        # Drained whether or not Telegram is configured: the loop keeps
        # filling it, and an unconfigured desk must not grow a list forever.
        queue = getattr(engine, "whale_alert_queue", None)
        pending = list(queue) if queue else []
        if queue:
            queue.clear()
        if not self.configured:
            return fired
        in_session = market_hours(now)
        health = engine.health()

        # A token that dies at 06:00 must be shouted about BEFORE the open,
        # not discovered when the session is already running.
        if getattr(self.engine, "token_expired", lambda: False)():
            fired.append(await self._alert(
                "token_expired",
                "Fyers daily access token has EXPIRED — reconnect in Settings before the open, "
                "the feed cannot authenticate.",
            ))
        if in_session and health["status"] != "connected":
            fired.append(await self._alert(
                "broker_down",
                f"Broker {health['status']} during market hours: {health.get('error') or 'no detail'}",
            ))
        age = health.get("last_tick_age_seconds")
        if in_session and health["status"] == "connected" and age is not None and age > FEED_STALE_SECONDS:
            fired.append(await self._alert(
                "feed_stale",
                f"Feed stale: last tick {age:.0f}s ago while the market is open.",
            ))
        threshold = float(getattr(engine.settings, "day_loss_alert_rupees", 0) or 0)
        baseline = engine.day_baseline_equity()
        if threshold > 0 and baseline is not None:
            day_pnl = engine.portfolio.snapshot()["equity"] - baseline
            if day_pnl <= -threshold:
                fired.append(await self._alert(
                    "day_loss",
                    f"Day P&L breached alert level: ₹{day_pnl:,.0f} (limit −₹{threshold:,.0f}). Auto-trade is {'ON' if engine.settings.auto_trade else 'off'}.",
                ))
        lag = health.get("loop_lag_ms") or {}
        if lag.get("p95") is not None and lag["p95"] > LOOP_LAG_ALERT_MS:
            fired.append(await self._alert(
                "loop_lag",
                f"Event-loop lag high: p95 {lag['p95']:.0f}ms — the process is saturating.",
            ))
        for row in pending:
            # Keyed per underlying and nothing else, so the 30-minute cooldown
            # actually applies. A per-window suffix mints a key that can never
            # collide with a later one: the cooldown then suppresses nothing
            # and every alert leaves a dictionary entry behind forever.
            key = f"whale:{row['underlying']}"
            sent = await self._alert(key, whale_alert_text(row))
            fired.append(sent)
            if sent and self.last_error is None and row.get("alert_id"):
                await asyncio.to_thread(
                    whale.mark_alert_sent, engine.settings.research_database_path, row["alert_id"])
        return [item for item in fired if item]

    async def _alert(self, key: str, text: str) -> str | None:
        now = time.monotonic()
        if now - self._last_sent.get(key, -COOLDOWN_SECONDS) < COOLDOWN_SECONDS:
            return None
        # A stamp older than the cooldown can no longer suppress anything, and
        # status() serialises this dictionary into the health payload on every
        # dashboard poll — it has to be the live cooldowns, not a session log.
        for stale in [k for k, stamp in self._last_sent.items() if now - stamp > COOLDOWN_SECONDS]:
            del self._last_sent[stale]
        self._last_sent[key] = now
        message = f"MACD mini · {text}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.engine.settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": self.engine.settings.telegram_chat_id, "text": message},
                )
            if response.status_code != 200:
                raise RuntimeError(f"Telegram HTTP {response.status_code}: {response.text[:120]}")
            self.sent_total += 1
            self.last_message = message
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        return key

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "sent_total": self.sent_total,
            "last_message": self.last_message,
            "last_error": self.last_error,
            "active_cooldowns": sorted(self._last_sent),
        }


def whale_alert_text(row: dict) -> str:
    """The composite read in one line. Lots, not units, because that is how
    the owner sizes a strike in their head; ₹ cr for the notional."""
    option = row.get("net_option_delta") or {}
    div = row.get("divergence") or {}
    lot = max(1, int(row.get("lot") or 1))
    top = ", ".join(
        f"{leg['strike']:.0f}{leg['option_type']} {'+' if leg['d_oi'] > 0 else ''}{leg['d_oi'] // lot}L"
        for leg in (row.get("strikes") or [])[:3])
    kinds = ", ".join(item["kind"].replace("_", " ") for item in (row.get("structures") or [])[:2])
    futures = "opposed" if div.get("divergence") else "aligned"
    if div.get("divergence"):
        futures += f" x{div.get('ratio')}"
    return (f"Whale {row['underlying']} composite {row.get('composite_decayed') or 0:.1f} — "
            f"option Δ-notional ₹{(option.get('dn') or 0) / 1e7:.1f} cr "
            f"{'long' if option.get('sign', 0) > 0 else 'short'}; futures {futures}; "
            f"{top or 'no strike'}; {kinds or 'no structure'}. Context, not a signal.")
