"""The desk process (MACD_ENGINE_ROLE=desk): the Market Profile / order-flow lane.

It runs the same desk code as the single-process engine (DeskMixin) with
three differences, all at the edges:
- ticks arrive from the bus, published by the strategy process after its own
  filters, instead of from an in-process call;
- today's contracts, the futures roll and the desk's settings come from the
  strategy process (/api/internal/desk-context), polled;
- broker calls (option chain, futures quotes) and settings saves go through
  the strategy process, which remains the only Fyers client and the only
  writer of settings.json.
The desk owns its book (mp_trader.sqlite3) and the tick capture
(ticks.sqlite3), and shares historical.sqlite3 with the strategy process on a
local volume, where SQLite's cross-process locking holds.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from fastapi import Depends, FastAPI

from . import desk_routes, market_calendar, whale
from .brokers import OptionChain, OptionChainEntry
from .bus import TickBusSubscriber
from .config import ENVIRONMENT_ONLY, Settings, settings as environment_settings
from .contracts import contract_from_row
from .desk import IST, DeskMixin, desk_settings
from .events import EventHub, dumps, json_value
from .models import Tick
from .mp_engine import MPEngine
from .rollover import Expiry
from .settings_store import RuntimeSettingsStore
from .tick_store import TickStore

CONTEXT_POLL_SECONDS = 15.0
HOLDINGS_REPORT_SECONDS = 10.0
HOLDINGS_REFRESH_SECONDS = 60.0


class StrategyClient:
    """The strategy process, as the desk sees it: context, broker data, saves."""

    def __init__(self, base_url: str, token: str):
        headers = {"X-Macd-Token": token} if token else {}
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=20.0)

    async def context(self) -> dict:
        response = await self._client.get("/api/internal/desk-context")
        response.raise_for_status()
        return response.json()

    async def report_holdings(self, holdings: dict[str, int]) -> dict:
        response = await self._client.post("/api/internal/desk-holdings", json={"holdings": holdings})
        response.raise_for_status()
        return response.json()

    async def save_settings(self, updates: dict) -> None:
        response = await self._client.put("/api/internal/settings", json=updates)
        if response.status_code >= 400:
            raise RuntimeError(f"strategy refused the settings: {response.text[:300]}")

    async def _broker(self, method: str, path: str, **kwargs):
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            # The text carries the broker's own error, e.g. a 429, which the
            # whale collector backs off on.
            raise RuntimeError(response.json().get("detail", response.text)[:500])
        return response.json()

    # The two broker calls the desk's collectors make, same shapes as a Broker.
    async def option_chain(self, symbol: str) -> OptionChain:
        raw = await self._broker("GET", f"/api/internal/option-chain/{symbol}")
        return OptionChain(
            expiry=raw["expiry"], spot_price=raw["spot_price"],
            entries=[OptionChainEntry(**row) for row in raw.get("entries", [])],
            expiries=[Expiry(**row) for row in raw.get("expiries", [])],
            fp=raw.get("fp", 0.0), vix=raw.get("vix"),
        )

    async def quotes(self, symbols: list[str]) -> dict[str, Tick]:
        raw = await self._broker("POST", "/api/internal/quotes", json={"symbols": symbols})
        return {symbol: Tick(**{**row, "timestamp": datetime.fromisoformat(row["timestamp"])})
                for symbol, row in raw.items()}

    async def close(self) -> None:
        await self._client.aclose()


class DeskEngine(DeskMixin):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.events = EventHub()
        self.init_desk_state()
        self.mp = MPEngine(
            settings.mp_database_path,
            settings=desk_settings(settings, enabled=settings.mp_enabled),
            events=self.events,
            history_database_path=settings.research_database_path,
        )
        self.mp.vix_symbol = settings.vix_symbol
        self.tick_store = TickStore(settings.tick_database_path, settings.tick_retention_days,
                                    settings.flow_retention_days)
        self.whale_live = whale.LiveFlow()
        self.latest_ticks: dict[str, Tick] = {}
        # The surface DeskMixin reads, filled from the strategy's context.
        self.contract_selector = SimpleNamespace(contracts={})
        self.futures_rollover: dict[str, str] = {}
        self.all_symbols: list[str] = []
        self.analysis_option_symbols: set[str] = set()
        self.rollover_errors: dict[str, str] = {}
        self.error: str | None = None
        self.strategy = StrategyClient(settings.strategy_url, settings.api_token)
        self.broker = self.strategy
        self.bus = TickBusSubscriber(settings.nats_url, self._on_bus_tick)
        self.context: dict = {"loaded": False, "at": None, "error": None, "selection_date": None}
        self._configured_key: tuple | None = None
        self._applied_settings: dict = {}
        self._reported_holdings: dict[str, int] | None = None
        self._reported_at = 0.0
        self._flushed_session: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._lane_tasks: list[asyncio.Task] = []
        # Idle until the strategy process confirms it runs as "strategy". If it
        # runs as "all" it has its own desk, and this one must not trade.
        self.active = False
        self.started_at = datetime.now(UTC)

    # -- ticks ---------------------------------------------------------------

    async def _on_bus_tick(self, tick: Tick, analysis_only: bool) -> None:
        if not self.active:
            return
        self.latest_ticks[tick.symbol] = tick
        await self._desk_on_tick(tick, analysis_only)

    # -- context from the strategy process ------------------------------------

    def apply_context(self, context: dict) -> None:
        self.context["strategy_role"] = context.get("role")
        if context.get("role") != "strategy":
            self.context.update({"loaded": False, "at": datetime.now(UTC).isoformat(),
                                 "error": f"the engine runs as {context.get('role')!r}, with its own desk; "
                                          "this desk process stays idle"})
            return
        wanted = context.get("settings") or {}
        # Only values that changed since the last context: the desk's own
        # settings route may have moved a value locally a moment ago.
        changed = {key: value for key, value in wanted.items()
                   if key not in ENVIRONMENT_ONLY and self._applied_settings.get(key) != value}
        if changed:
            self.settings = Settings(**{**self.settings.model_dump(), **changed})
            self._applied_settings.update(changed)
            self.mp.settings.max_positions = self.settings.mp_max_positions
            self.mp.settings.max_trades_per_day = self.settings.mp_max_trades_per_day
            self.mp.vix_symbol = self.settings.vix_symbol
        self.all_symbols = list(context.get("all_symbols") or [])
        self.analysis_option_symbols = set(context.get("analysis_symbols") or [])
        self.futures_rollover = dict(context.get("futures_rollover") or {})
        self.contract_selector.contracts = {
            row["symbol"]: contract_from_row(row) for row in context.get("contracts") or []}
        lot_sizes = {symbol: int(size) for symbol, size in (context.get("lot_sizes") or {}).items()}
        key = (context.get("selection_date"), datetime.now(IST).date().isoformat(),
               tuple(sorted(self.contract_selector.contracts)), tuple(sorted(lot_sizes.items())),
               tuple(sorted(self.futures_rollover.items())), self.settings.mp_symbols_csv)
        if key != self._configured_key:
            self.configure_desk(lot_sizes)
            self._configured_key = key
        self.context.update({"loaded": True, "at": datetime.now(UTC).isoformat(), "error": None,
                             "selection_date": context.get("selection_date"),
                             "strategy_status": context.get("status"),
                             "contracts": len(self.contract_selector.contracts)})

    async def _context_loop(self) -> None:
        while True:
            try:
                self.apply_context(await self.strategy.context())
            except Exception as exc:  # noqa: BLE001 - keep the last good context
                self.context["error"] = str(exc)[:300]
            await self._set_active(self.context.get("strategy_role") == "strategy" and self.context["loaded"])
            await asyncio.sleep(CONTEXT_POLL_SECONDS if self.context["loaded"] else 2.0)

    async def _set_active(self, active: bool) -> None:
        """Run the lane's clocks only while this process is the desk."""
        if active == self.active:
            return
        self.active = active
        if active:
            loops = [self._holdings_loop(), self._expiry_flatten_loop(), self._session_close_loop(),
                     self._nightly_loop(), self._chain_loop()]
            if self.settings.tick_capture_enabled:
                loops.append(self._tick_maintenance_loop())
            self._lane_tasks = [asyncio.create_task(loop) for loop in loops]
        else:
            for task in self._lane_tasks:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            self._lane_tasks = []

    # -- holdings, so the strategy keeps them subscribed ----------------------

    def holdings(self) -> dict[str, int]:
        return {symbol: position.lot_size for symbol, position in self.mp.portfolio.positions.items()}

    async def _holdings_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            current = self.holdings()
            due = loop.time() - self._reported_at >= HOLDINGS_REFRESH_SECONDS
            if current != self._reported_holdings or due:
                try:
                    await self.strategy.report_holdings(current)
                    self._reported_holdings = current
                    self._reported_at = loop.time()
                except Exception as exc:  # noqa: BLE001
                    self.error = f"holdings report failed: {exc}"[:300]
            await asyncio.sleep(HOLDINGS_REPORT_SECONDS)

    # -- the desk's own clocks ------------------------------------------------

    async def _expiry_flatten_loop(self) -> None:
        """The desk's share of the engine's expiry sweep, on the same cutoff."""
        while True:
            await asyncio.sleep(20)
            cutoff = self._expiry_flatten_time()
            now = datetime.now(IST)
            if cutoff is None or not market_calendar.is_trading_day(now.date()) or (now.hour, now.minute) < cutoff:
                continue
            symbols = self.expiring_desk_positions()
            if symbols:
                await self._flatten_expiring_desk(symbols)

    async def _session_close_loop(self) -> None:
        """At 15:30 the engine closes the desk's intraday positions."""
        while True:
            await asyncio.sleep(20)
            now = datetime.now(IST)
            today = now.date().isoformat()
            if not market_calendar.is_trading_day(now.date()) or self._flushed_session == today:
                continue
            if (now.hour, now.minute) < (15, 30):
                continue
            self._flushed_session = today
            await self.mp.close_positions()

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        if self.settings.tick_capture_enabled:
            self.tick_store.start()
        self._tasks = [asyncio.create_task(self._context_loop()), asyncio.create_task(self.bus.run())]

    async def stop(self) -> None:
        await self._set_active(False)
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        await self.bus.stop()
        await self.tick_store.stop()
        await self.mp.stop()
        await self.strategy.close()

    def status(self, *, drain_alerts: bool = False) -> dict:
        alerts = list(self.whale_alert_queue) if drain_alerts else []
        if drain_alerts:
            self.whale_alert_queue.clear()
        return json_value({
            "role": "desk",
            "active": self.active,
            "uptime_seconds": round((datetime.now(UTC) - self.started_at).total_seconds()),
            "mp": self.mp.health(),
            "chain": self.chain_status,
            "nightly": self.nightly_report,
            "tick_store": self.tick_store.status(include_database_counts=False)
            if self.settings.tick_capture_enabled else None,
            "bus": self.bus.status(),
            "context": self.context,
            "holdings": len(self.holdings()),
            "error": self.error,
            "whale_alerts": alerts,
        })


def _desk_settings() -> Settings:
    persisted = RuntimeSettingsStore(environment_settings.runtime_settings_path).load()
    # Read-only here: the strategy process owns settings.json.
    persisted = {key: value for key, value in persisted.items() if key not in ENVIRONMENT_ONLY}
    return Settings(**{**environment_settings.model_dump(), **persisted,
                       "execution_mode": "paper", "allow_live_orders": False})


engine = DeskEngine(_desk_settings())


async def persist_mp_settings_remotely(config_updates: dict) -> None:
    await engine.strategy.save_settings(config_updates)
    engine.settings = engine.settings.model_copy(update=config_updates)
    engine._applied_settings.update(config_updates)


desk_routes.bind(engine, persist_mp_settings_remotely)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(title="MACD Trader desk", version="0.1.0", lifespan=lifespan)
app.include_router(desk_routes.router)


@app.get("/health")
async def health():
    return {"ok": engine.active and engine.bus.connected, "active": engine.active, "role": "desk",
            "bus": engine.bus.status(), "context": engine.context}


@app.get("/api/internal/desk/status", dependencies=[Depends(desk_routes.authorize)])
async def desk_status():
    # Polled by the strategy process, which relays whale alerts to Telegram.
    from fastapi.responses import Response
    return Response(content=dumps(engine.status(drain_alerts=True)), media_type="application/json")
