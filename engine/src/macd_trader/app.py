from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import re
import time
from contextlib import asynccontextmanager, suppress
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Literal
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from .alerts import AlertManager
from .brokers import FyersConnectionError, create_broker
from .config import ENVIRONMENT_ONLY, Settings, settings
from .chart_history import load_chart_history
from .candle_store import store_historical_candles
from . import desk_routes
from .api_models import OrderInput, _as_json
from .dispersion import load as load_dispersion
from .engine import TradingEngine
from .events import Frame, dumps, frame_text, json_value
from .settings_store import RuntimeSettingsStore


PERSIST_EXCLUDED = ENVIRONMENT_ONLY | {
    # Broker secrets live in credentials.json only — never mirrored into
    # settings.json, where a stale plaintext token would otherwise linger.
    "fyers_secret", "fyers_access_token", "fyers_refresh_token", "fyers_pin", "telegram_bot_token",
}

settings_store = RuntimeSettingsStore(settings.runtime_settings_path)
persisted_settings = {key: value for key, value in settings_store.load().items() if key not in ENVIRONMENT_ONLY}
credentials_store = RuntimeSettingsStore(settings.credentials_path)
saved_credentials = credentials_store.load()
active_settings = Settings(**{**settings.model_dump(), **persisted_settings, **saved_credentials, "execution_mode": "paper", "allow_live_orders": False})
engine = TradingEngine(active_settings)
# The auction desk reads India VIX for the session badge's implied daily range.
# engine.py builds MPEngine and belongs to another branch, so the setting is
# handed over here; MPEngine falls back to MACD_VIX_SYMBOL / the config default
# without it, and shows no range badge at all until a VIX tick arrives.
engine.mp.vix_symbol = active_settings.vix_symbol
alert_manager = AlertManager(engine)



@asynccontextmanager
async def lifespan(_: FastAPI):
    await engine.start()
    if os.environ.get("MACD_LOOP_WATCHDOG") == "1":
        # Exit on a wedged event loop so Docker restarts the engine (self_heal.LoopWatchdog). Container-only.
        from .self_heal import LoopWatchdog
        engine.loop_watchdog = LoopWatchdog(engine.settings.engine_role,
                                            marker_dir=os.path.dirname(os.path.abspath(engine.settings.database_path)))
        engine.loop_watchdog.start()
    persist_settings(engine.settings)
    persist_credentials(engine.settings)
    alert_manager.start()
    yield
    await alert_manager.stop()
    await engine.stop()


app = FastAPI(title="MACD Trader API", version="0.1.0", lifespan=lifespan)
CORS_ORIGINS = [
    "http://localhost:3100",
    "http://127.0.0.1:3100",
    "http://localhost:4173",
    "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def authorize(x_macd_token: str = Header(default="")) -> None:
    if engine.settings.api_token and x_macd_token != engine.settings.api_token:
        raise HTTPException(401, "Invalid API token")




class SettingsInput(BaseModel):
    feed_mode: Literal["fyers"] | None = None
    symbols: list[str] | None = None
    timeframe_seconds: int | None = Field(default=None, ge=60)
    fast_period: int | None = Field(default=None, ge=2)
    slow_period: int | None = Field(default=None, ge=3)
    signal_period: int | None = Field(default=None, ge=1)
    bb_period: int | None = Field(default=None, ge=2)
    bb_deviations: float | None = Field(default=None, gt=0, le=10)
    kama_period: int | None = Field(default=None, ge=1)
    kama_fast: int | None = Field(default=None, ge=1)
    kama_slow: int | None = Field(default=None, ge=2)
    kama_rsi_period: int | None = Field(default=None, ge=2)
    kama_rsi_min: float | None = Field(default=None, ge=0, le=100)
    kama_roc_period: int | None = Field(default=None, ge=1)
    kama_roc_min: float | None = Field(default=None, ge=-100, le=100)
    require_kama_confirmation: bool | None = None
    require_kama_rsi_confirmation: bool | None = None
    require_kama_roc_confirmation: bool | None = None
    entry_volume_ratio: float | None = Field(default=None, ge=1, le=20)
    max_trade_lots: int | None = Field(default=None, ge=1, le=4)
    max_positions: int | None = Field(default=None, ge=0, le=1000)
    min_cash_reserve: float | None = Field(default=None, ge=0)
    macd_invalidation_exit: bool | None = None
    macd_invalidation_max_mfe_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    target_position_notional: float | None = Field(default=None, ge=0, le=5_000_000)
    max_target_entry_lots: int | None = Field(default=None, ge=1, le=500)
    initial_capital: float | None = Field(default=None, ge=1)
    signal_mode: Literal["zero_cross", "signal_cross", "both"] | None = None
    auto_trade: bool | None = None
    order_quantity: int | None = Field(default=None, ge=1)
    slippage_bps: float | None = Field(default=None, ge=0, le=100)
    # The auction desk's concurrency limits. Bounds match MPSettingsInput so
    # the two write paths cannot disagree about what is acceptable.
    mp_max_positions: int | None = Field(default=None, ge=1, le=20)
    mp_max_trades_per_day: int | None = Field(default=None, ge=1, le=100)
    # Blast lane. Bounds mirror BlastSettingsInput so the two write paths
    # cannot disagree about what is acceptable.
    blast_enabled: bool | None = None
    blast_strategy: Literal["hma_kama", "macd_screen"] | None = None
    blast_auto_trade: bool | None = None
    blast_initial_capital: float | None = Field(default=None, ge=1)
    blast_max_positions: int | None = Field(default=None, ge=1, le=100)
    blast_target_notional: float | None = Field(default=None, ge=0, le=5_000_000)
    blast_max_premium_pct: float | None = Field(default=None, gt=0, le=100)
    blast_min_breadth: float | None = Field(default=None, ge=0.0, le=1.0)
    blast_min_off_high_pct: float | None = Field(default=None, ge=0.0, le=99.0)
    blast_hard_stop_pct: float | None = Field(default=None, gt=0, lt=1)
    blast_trail_activation_pct: float | None = Field(default=None, gt=0, lt=5)
    blast_trail_pct: float | None = Field(default=None, gt=0, lt=1)
    fyers_client_id: str | None = None
    fyers_secret: str | None = None
    fyers_access_token: str | None = None
    fyers_redirect_uri: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    day_loss_alert_rupees: float | None = Field(default=None, ge=0)


class AuthCodeInput(BaseModel):
    auth_code: str = Field(min_length=4)


class AccessTokenInput(BaseModel):
    access_token: str = Field(min_length=10)


class FyersCredentialsInput(BaseModel):
    client_id: str = Field(min_length=3)
    secret: str | None = None
    redirect_uri: str = Field(min_length=8)
    pin: str | None = Field(default=None, min_length=4, max_length=8)


def fast_json(content) -> Response:
    """JSON without FastAPI's jsonable_encoder pass, which alone took ~55 ms
    of the 1.7 MB snapshot's ~130 ms. Content must be orjson-serializable."""
    return Response(content=_encode(content), media_type="application/json")


def _encode(content) -> bytes:
    try:
        return dumps(content)
    except TypeError:  # an unusual type: take the slow, general path
        return dumps(json_value(content))


async def fast_json_off_loop(content) -> Response:
    """For large payloads already built as plain values: encode in a thread."""
    return Response(content=await asyncio.to_thread(_encode, content), media_type="application/json")


@app.get("/health")
async def health():
    return {"ok": engine.status == "connected", "broker": engine.broker_status(include_symbols=False)}


@app.get("/api/snapshot", dependencies=[Depends(authorize)])
async def snapshot():
    return await fast_json_off_loop(engine.snapshot())


@app.get("/api/watchlist", dependencies=[Depends(authorize)])
async def watchlist():
    return fast_json(engine.snapshot()["watchlist"])


CHART_SYMBOL = re.compile(r"(NSE|BSE):[A-Z0-9&_-]{2,40}")


@app.get("/api/chart/{symbol:path}", dependencies=[Depends(authorize)])
async def chart_history(symbol: str, timeframe_seconds: int = Query(default=1800, ge=60, le=86400)):
    # Outside the watchlist (a shadow or agentic lane's contract, an expired one) the stored minutes still chart.
    watched = symbol in engine.all_symbols
    if not watched and not CHART_SYMBOL.fullmatch(symbol):
        raise HTTPException(404, "Symbol is not in the active watchlist")
    live_rows = (
        list(engine.history.get(symbol, ()))
        if watched and timeframe_seconds == engine.settings.timeframe_seconds
        else None
    )
    try:
        chart = await asyncio.to_thread(
            load_chart_history,
            engine.settings.research_database_path,
            symbol,
            timeframe_seconds,
            engine.settings.fast_period,
            engine.settings.slow_period,
            engine.settings.signal_period,
            engine.settings.bb_period,
            engine.settings.bb_deviations,
            engine.settings.kama_period,
            engine.settings.kama_fast,
            engine.settings.kama_slow,
            engine.settings.kama_rsi_period,
            engine.settings.kama_roc_period,
            live_rows,
        )
    except sqlite3.Error as exc:
        raise HTTPException(500, f"Historical chart data is unavailable: {exc}") from exc
    if not watched and not chart.get("candles"):
        raise HTTPException(404, "No stored bars for this symbol")
    # Consumers that recompute MACD (the parallel Rust analytics) need the
    # periods these indicators were built with, not an assumed 12/26/9.
    chart["macd_periods"] = {
        "fast": engine.settings.fast_period,
        "slow": engine.settings.slow_period,
        "signal": engine.settings.signal_period,
    }
    return fast_json(chart)


@app.get("/api/ratios/{spot_symbol:path}", dependencies=[Depends(authorize)])
async def option_ratios(
    spot_symbol: str,
    timeframe_seconds: int = Query(default=300, ge=300, le=1800),
):
    """Current-expiry premium closes and CE/PE moneyness ratios."""
    if timeframe_seconds not in {300, 900, 1800}:
        raise HTTPException(422, "Ratio timeframe must be 300, 900 or 1800 seconds")
    if spot_symbol not in engine.settings.symbols:
        raise HTTPException(404, "Underlying is not in the active spot universe")
    try:
        return await engine.ratio_history(spot_symbol, timeframe_seconds)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(500, f"Ratio history is unavailable: {exc}") from exc


@app.get("/api/heatmap/atm", dependencies=[Depends(authorize)])
async def atm_heatmap(as_of: str | None = None):
    """ATM call and put premium change per underlying, period to date (1H, 4H, 1D, 1W, 1M).

    ``as_of`` (YYYY-MM-DD) replays the map at that day's close with that day's ATM contracts."""
    try:
        day = date.fromisoformat(as_of) if as_of else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="as_of must be YYYY-MM-DD") from exc
    return await engine.atm_heatmap(day)


@app.get("/api/sector-wheel", dependencies=[Depends(authorize)])
async def sector_wheel():
    """Own and NIFTY-relative returns over 1, 5, 20 and 60 sessions for indices, sectors and F&O stocks."""
    return await engine.sector_wheel()


@app.get("/api/sector-coil", dependencies=[Depends(authorize)])
async def sector_coil(base: str = "YTD", step: str = "M", measure: str = "own"):
    """Each index and sector's return from a base close (YTD, 1Y, 6M, 3M, 1M) at every checkpoint (month ends,
    fortnights, weeks, or one month ago), own or relative to NIFTY 50: the data behind the Sector coil chart."""
    from . import sector_coil as coil
    if base not in coil.BASES or step not in coil.STEPS or measure not in coil.MEASURES:
        raise HTTPException(status_code=422, detail=f"base in {sorted(coil.BASES)}, step in {sorted(coil.STEPS)}, measure in {sorted(coil.MEASURES)}")
    return await engine.sector_coil(base, step, measure)


@app.get("/api/trend-rotation", dependencies=[Depends(authorize)])
async def trend_rotation(kind: Literal["CE", "PE", "SPOT"] = "CE",
                         timeframe_seconds: int = Query(default=1800)):
    """Absolute price trend states for ATM calls, ATM puts or spots. Read-only diagnostics."""
    if timeframe_seconds not in (1800, 3600, 86400):
        raise HTTPException(422, "Use 1800, 3600 or 86400 seconds")
    return await engine.trend_rotation(kind, timeframe_seconds)


@app.get("/api/day-highs", dependencies=[Depends(authorize)])
async def day_highs():
    """Spots whose last completed 15-min bar closed at a new day high (or low). Read-only market breadth."""
    return await engine.day_highs()


@app.get("/api/whale-interest", dependencies=[Depends(authorize)])
async def whale_interest() -> dict:
    """Underlyings a whale-sized minute made interesting in the last hour (lane priority, owner 3 Nov 2026)."""
    return engine.whale_interest.status()


@app.get("/api/dispersion", dependencies=[Depends(authorize)])
async def dispersion(
    timeframe_seconds: int | None = Query(default=None, ge=60, le=86_400),
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=2_000, ge=1, le=20_000),
):
    """Stored CE/PE MACD breadth — live bars and offline reconstructions."""
    timeframe = timeframe_seconds or engine.settings.timeframe_seconds
    try:
        rows = await asyncio.to_thread(
            load_dispersion, engine.settings.research_database_path, timeframe, since, limit,
        )
    except sqlite3.Error as exc:
        raise HTTPException(500, f"Dispersion history is unavailable: {exc}") from exc
    return {"timeframe_seconds": timeframe, "points": rows}
























@app.get("/api/orders", dependencies=[Depends(authorize)])
async def orders(limit: int = Query(default=10_000, ge=1, le=100_000), collapse: bool = False):
    """The order log; ``collapse`` folds each run of rejects into one row per symbol, side and IST day."""
    if collapse:
        return await asyncio.to_thread(engine.repository.orders_collapsed, limit)
    return engine.repository.rows("orders", limit)


@app.get("/api/trades", dependencies=[Depends(authorize)])
async def trades(limit: int = Query(default=10_000, ge=1, le=100_000)):
    return engine.repository.rows("trades", limit)


@app.get("/api/risk/greeks", dependencies=[Depends(authorize)])
async def risk_greeks():
    """Net delta, theta and vega of every paper book (display only)."""
    return engine.book_greeks()


@app.get("/api/portfolio", dependencies=[Depends(authorize)])
async def portfolio():
    # The execution wrapper, not the raw Portfolio: the REST view must carry
    # the same closed_positions the websocket 'portfolio' frame carries.
    return engine.execution.portfolio_snapshot()


@app.get("/api/closed-positions", dependencies=[Depends(authorize)])
async def closed_positions(
    include_hidden: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=10_000),
):
    """MACD-lane closing slices, newest first. Default: only records still on
    the desk (until 08:00 IST after the exit); include_hidden returns the
    durable history for research."""
    if include_hidden:
        return engine.repository.closed_positions(lane=engine.portfolio.lane, limit=limit)
    return engine.execution.visible_closed_positions()[:limit]


@app.get("/api/signals", dependencies=[Depends(authorize)])
async def signals(limit: int = Query(default=200, ge=1, le=5000)):
    return engine.repository.rows("signals", limit)


@app.get("/api/signals/diagnostics", dependencies=[Depends(authorize)])
async def signal_diagnostics():
    return engine.strategy.diagnostics()


























class BlastSettingsInput(BaseModel):
    enabled: bool | None = None
    strategy: Literal["hma_kama", "macd_screen"] | None = None
    auto_trade: bool | None = None
    initial_capital: float | None = Field(default=None, ge=1)
    # Up to 1000 so the lane can be run unbounded, taking every signal the
    # screen passes, to measure the screen rather than a 10-slot sample.
    max_positions: int | None = Field(default=None, ge=1, le=1_000)
    target_notional: float | None = Field(default=None, ge=0, le=5_000_000)
    max_premium_pct: float | None = Field(default=None, gt=0, le=100)
    min_breadth: float | None = Field(default=None, ge=0.0, le=1.0)
    vix_adaptive_enabled: bool | None = None
    vix_reference: float | None = Field(default=None, ge=5.0, le=100.0)
    vix_breadth_adjustment: float | None = Field(default=None, ge=-0.25, le=0.25)
    vix_max_age_seconds: float | None = Field(default=None, ge=30.0, le=3600.0)
    rich_premium_max_multiple: float | None = Field(default=None, ge=1.0, le=4.0)
    min_off_high_pct: float | None = Field(default=None, ge=0.0, le=99.0)
    hard_stop_pct: float | None = Field(default=None, gt=0, lt=1)
    trail_activation_pct: float | None = Field(default=None, gt=0, lt=5)
    trail_pct: float | None = Field(default=None, gt=0, lt=1)


def _agentic():
    lane = getattr(engine, "agentic", None)
    if lane is None:
        raise HTTPException(404, "the agentic lane is disabled (MACD_AGENTIC_ENABLED)")
    return lane


@app.get("/api/agentic", dependencies=[Depends(authorize)])
async def agentic_snapshot():
    """The agentic lane (agentic_lane.py): book, positions with their thesis and levels, brain status."""
    return _agentic().snapshot()


@app.get("/api/agentic/matrix", dependencies=[Depends(authorize)])
async def agentic_matrix():
    """The decision matrix now: grade, score and factors per index x side, and the risk budget per grade."""
    return await _agentic().refresh_matrix()


@app.get("/api/agentic/cycles", dependencies=[Depends(authorize)])
async def agentic_cycles(day: str | None = None, limit: int = Query(default=40, ge=1, le=500)):
    """Each cycle's market view, the model's decision, what was accepted, rejected and filled."""
    return {"cycles": await asyncio.to_thread(_agentic().recent_cycles, day, limit),
            "days": await asyncio.to_thread(_agentic().journal_days)}


@app.get("/api/agentic/actions", dependencies=[Depends(authorize)])
async def agentic_actions(limit: int = Query(default=300, ge=1, le=2000)):
    return {"actions": await asyncio.to_thread(_agentic().actions, limit)}


@app.get("/api/agentic/book", dependencies=[Depends(authorize)])
async def agentic_book(limit: int = Query(default=2000, ge=1, le=5000)):
    """The agentic lane's fills and recently closed positions, for its position charts' markers."""
    lane = _agentic()
    return fast_json(await asyncio.to_thread(lambda: {"trades": lane.repository.rows("trades", limit),
                                                      "closed_positions": lane.execution.visible_closed_positions()}))


@app.get("/api/agentic/equity", dependencies=[Depends(authorize)])
async def agentic_equity():
    return {"equity": await asyncio.to_thread(_agentic().equity_curve), "capital": _agentic().capital}


class AgenticBrainInput(BaseModel):
    api_key: str | None = Field(default=None, max_length=400)
    clear_key: bool = False
    model: str | None = Field(default=None, max_length=80)
    api_base: str | None = Field(default=None, max_length=200)
    reasoning_effort: str | None = Field(default=None, max_length=10)
    universe: str | None = Field(default=None, max_length=10)
    cycle_minutes: int | None = None
    management_key: str | None = Field(default=None, max_length=400)
    clear_management_key: bool = False
    team_id: str | None = Field(default=None, max_length=80)


@app.get("/api/agentic/brain", dependencies=[Depends(authorize)])
async def agentic_brain_status():
    """Connection settings for the lane's model. The key itself is never returned, only its last 4 characters."""
    return _agentic().brain_status()


@app.put("/api/agentic/brain", dependencies=[Depends(authorize)])
async def agentic_brain_update(payload: AgenticBrainInput):
    try:
        return await asyncio.to_thread(_agentic().update_brain, api_key=payload.api_key, clear_key=payload.clear_key,
                                       model=payload.model, api_base=payload.api_base,
                                       reasoning_effort=payload.reasoning_effort, universe=payload.universe,
                                       cycle_minutes=payload.cycle_minutes, management_key=payload.management_key,
                                       clear_management_key=payload.clear_management_key, team_id=payload.team_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/agentic/usage", dependencies=[Depends(authorize)])
async def agentic_usage():
    """Grok calls, tokens and xAI's cost per IST day from the journal, and the last fetched credit balance."""
    return await asyncio.to_thread(_agentic().usage_summary)


@app.post("/api/agentic/usage/balance", dependencies=[Depends(authorize)])
async def agentic_usage_balance():
    """Fetch the prepaid credit balance from xAI's Management API (needs a management key and team id)."""
    return await _agentic().refresh_balance()


@app.post("/api/agentic/brain/test", dependencies=[Depends(authorize)])
async def agentic_brain_test():
    """List the account's models and make one tiny completion with the configured model."""
    return await _agentic().test_brain()


class AgenticTelegramInput(BaseModel):
    bot_token: str | None = Field(default=None, max_length=200)
    clear_token: bool = False
    unpair: bool = False


def _telegram():
    bridge = getattr(_agentic(), "telegram", None)
    if bridge is None:
        raise HTTPException(404, "Telegram is not available for the agentic lane")
    return bridge


@app.get("/api/agentic/telegram", dependencies=[Depends(authorize)])
async def agentic_telegram_status():
    """Bot status and, while unpaired, the code to send as /pair <code>. The token is never returned."""
    return _telegram().status()


@app.put("/api/agentic/telegram", dependencies=[Depends(authorize)])
async def agentic_telegram_update(payload: AgenticTelegramInput):
    try:
        return await asyncio.to_thread(_telegram().update, bot_token=payload.bot_token,
                                       clear_token=payload.clear_token, unpair=payload.unpair)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/agentic/telegram/test", dependencies=[Depends(authorize)])
async def agentic_telegram_test():
    bridge = _telegram()
    if not bridge.status()["paired"]:
        raise HTTPException(409, "pair the bot first: send /pair <code> to it from your Telegram")
    ok = await bridge.send("Agentic lane: test message. Commands: /help")
    return {**bridge.status(), "sent": ok}


class AgenticThinkInput(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@app.post("/api/agentic/think", dependencies=[Depends(authorize)])
async def agentic_think(payload: AgenticThinkInput):
    """Run a cycle now, optionally with an operator note the model weighs. Outside the session it only comments."""
    lane = _agentic()
    lane._manual_task = asyncio.create_task(lane.think("operator request", (payload.note or "").strip() or None))
    return {"queued": True}


@app.post("/api/agentic/pause", dependencies=[Depends(authorize)])
async def agentic_pause():
    _agentic().paused = True
    return _agentic().health()


@app.post("/api/agentic/resume", dependencies=[Depends(authorize)])
async def agentic_resume():
    _agentic().paused = False
    return _agentic().health()


class AgenticCloseInput(BaseModel):
    symbol: str = Field(min_length=3, max_length=64)
    expected_quantity: int = Field(gt=0)


@app.post("/api/agentic/close-position", dependencies=[Depends(authorize)])
async def agentic_close_position(payload: AgenticCloseInput):
    """Operator close of one agentic position (paper), priced from a fresh quote."""
    lane = _agentic()
    try:
        result = await engine.close_smart_paper_position(lane.name, payload.symbol, payload.expected_quantity)
    except (ValueError, PermissionError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    lane.note_operator_close(payload.symbol)
    return result


def _smart_lane(name: str):
    lane = engine.smart_lanes.get(name)
    spot = getattr(engine, "spot_lane", None)
    if lane is None and spot is not None and spot.name == name:
        lane = spot            # the spot MACD lane shares the smart lanes' views (spot_lane.py)
    if lane is None:
        raise HTTPException(404, f"no smart lane {name}")
    return lane


async def _shadow_lane_read(lane: str, archive: bool = False, view: str = ""):
    """Read the isolated shadow process; no execution or arbitrary upstream path can be proxied."""
    if lane not in ("vanguard", "cbe") or view not in ("", "decision"):
        raise HTTPException(404, "Unknown shadow lane")
    url = os.environ.get("MACD_SHADOW_LANES_URL", "").rstrip("/")
    if not url:
        raise HTTPException(503, "Shadow lanes are not configured")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60 if view else 15) as client:
            response = await client.get(f"{url}/api/shadow-lanes/{lane}" + ("/archive" if archive else f"/{view}" if view else ""),
                headers={"X-Macd-Token": engine.settings.api_token} if engine.settings.api_token else {})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Shadow lane service unavailable: {type(exc).__name__}") from exc
    return payload if archive or view else _trim_shadow_journal(payload)


SHADOW_DETAIL_MAX_BYTES = 20_000


def _trim_shadow_journal(payload):
    """Shrink oversized journal details for the live view; the shadow store keeps them whole.

    One CBE readiness row (27 Oct 2026 15:31 IST) carried a 3.3 MB detail, 95% of the lane's 3.5 MB snapshot,
    which every Terminal load fetched. The panels read only detail.reason and detail.blockers.
    """
    journal = payload.get("journal") if isinstance(payload, dict) else None
    if not isinstance(journal, list):
        return payload
    rows = []
    for row in journal:
        detail = row.get("detail") if isinstance(row, dict) else None
        size = len(json.dumps(detail, default=str)) if isinstance(detail, (dict, list)) else 0
        if size <= SHADOW_DETAIL_MAX_BYTES or not isinstance(detail, dict):
            rows.append(row)
            continue
        kept = {k: v for k, v in detail.items() if isinstance(v, (str, int, float, bool)) or v is None}
        if isinstance(detail.get("blockers"), list):
            kept["blockers"] = detail["blockers"][:20]
        kept["detail_truncated_bytes"] = size
        rows.append({**row, "detail": kept})
    return {**payload, "journal": rows}


@app.get("/api/shadow-lanes/{lane}", dependencies=[Depends(authorize)])
async def shadow_lane_snapshot(lane: str):
    return await _shadow_lane_read(lane)


@app.get("/api/shadow-lanes/vanguard/decision", dependencies=[Depends(authorize)])
async def shadow_lane_vanguard_decision():
    return await _shadow_lane_read("vanguard", view="decision")


@app.get("/api/shadow-lanes/{lane}/archive", dependencies=[Depends(authorize)])
async def shadow_lane_archive(lane: str):
    return await _shadow_lane_read(lane, archive=True)


@app.get('/api/local-decisions', dependencies=[Depends(authorize)])
async def local_decision_status():
    from .local_model import configuration
    import httpx
    mode,url=configuration()
    if mode=='off':return {'ready':False,'mode':'off','model':'qwen3.5:9b','recent':[]}
    try:
        async with httpx.AsyncClient(timeout=6,trust_env=False) as client:
            response=await client.get(url+'/status',headers={'X-Macd-Token':engine.settings.api_token})
            response.raise_for_status()
        return {**response.json(),'mode':mode}
    except (httpx.HTTPError,ValueError):
        return {'ready':False,'mode':mode,'model':'qwen3.5:9b','recent':[],
                'detail':'Local review service unavailable; new filtered entries wait'}


@app.get('/api/paper-lanes/{lane}',dependencies=[Depends(authorize)])
async def funded_lane_snapshot(lane:str):
    import httpx
    if lane not in ('vanguard','cbe'):raise HTTPException(404,'Unknown paper lane')
    url=os.environ.get('MACD_SHADOW_LANES_URL','').rstrip('/')
    if not url:raise HTTPException(503,'Paper service not configured')
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r=await client.get(f'{url}/api/paper-lanes/{lane}',headers={'X-Macd-Token':engine.settings.api_token})
        return Response(r.content,status_code=r.status_code,media_type='application/json')
    except httpx.HTTPError:raise HTTPException(502,'Paper account reader unavailable') from None


@app.post('/api/paper-lanes/{lane}/control',dependencies=[Depends(authorize)])
async def funded_lane_control(lane:str,body:dict=Body(...)):
    import httpx
    if lane not in ('vanguard','cbe') or set(body)!={'action'} or body['action'] not in ('pause','resume'):
        raise HTTPException(400,'Only pause/resume new paper entries is supported')
    url=os.environ.get('MACD_SHADOW_LANES_URL','').rstrip('/')
    if not url:raise HTTPException(503,'Paper service not configured')
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r=await client.post(f'{url}/api/paper-lanes/{lane}/control',json=body,headers={'X-Macd-Token':engine.settings.api_token})
        return Response(r.content,status_code=r.status_code,media_type='application/json')
    except httpx.HTTPError:raise HTTPException(502,'Paper account control unavailable') from None


@app.get('/api/nomad/{path:path}', dependencies=[Depends(authorize)])
async def retained_desk_read(path: str, request: Request):
    """Original desk views through the isolated reader. No write route or arbitrary upstream exists."""
    if path.split('/')[0] not in ('vanguard','cbe','charts','mp') or '..' in path or '://' in path:
        raise HTTPException(404,'Unknown retained desk view')
    url=os.environ.get('MACD_SHADOW_LANES_URL','').rstrip('/')
    if not url:raise HTTPException(503,'Shadow desk service is not configured')
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response=await client.get(f'{url}/api/nomad/{path}',params=list(request.query_params.multi_items()),
                headers={'X-Macd-Token':engine.settings.api_token} if engine.settings.api_token else {})
        return Response(response.content,status_code=response.status_code,media_type='application/json')
    except httpx.HTTPError:
        raise HTTPException(502,'Retained desk reader is unavailable') from None


_scoreboard_cache: dict = {"at": 0.0, "value": None}


@app.get("/api/scoreboard", dependencies=[Depends(authorize)])
async def lanes_scoreboard(refresh: bool = False):
    """Every paper book scored alike (scoreboard.py); rebuilt at most once a minute."""
    from . import scoreboard
    now = time.monotonic()
    if refresh or _scoreboard_cache["value"] is None or now - _scoreboard_cache["at"] > 60:
        _scoreboard_cache["value"] = await asyncio.to_thread(scoreboard.build, engine)
        _scoreboard_cache["at"] = now
    return _scoreboard_cache["value"]


@app.get("/api/lanes", dependencies=[Depends(authorize)])
async def smart_lanes_index():
    """The smart lanes (smart_lane.py): one paper book per configured capital, same rules."""
    return {"lanes": [await asyncio.to_thread(lane.summary) for lane in engine.smart_lanes.lanes]}


@app.get("/api/lanes/{name}/snapshot", dependencies=[Depends(authorize)])
async def smart_lane_snapshot(name: str):
    return _smart_lane(name).snapshot()


@app.get("/api/lanes/{name}/summary", dependencies=[Depends(authorize)])
async def smart_lane_summary(name: str):
    return await asyncio.to_thread(_smart_lane(name).summary)


class CloseSmartPositionInput(BaseModel):
    symbol: str = Field(min_length=3, max_length=64)
    expected_quantity: int = Field(gt=0)


@app.post("/api/lanes/{name}/close-position", dependencies=[Depends(authorize)])
async def close_smart_position(name: str, payload: CloseSmartPositionInput):
    _smart_lane(name)
    try:
        order = await engine.close_smart_paper_position(name, payload.symbol, payload.expected_quantity)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return _as_json(order)


@app.get("/api/lanes/{name}/trades", dependencies=[Depends(authorize)])
async def smart_lane_trades(name: str, limit: int = Query(default=500, ge=1, le=5000)):
    return {"trades": await asyncio.to_thread(_smart_lane(name).trades, limit)}


@app.get("/api/lanes/{name}/journal", dependencies=[Depends(authorize)])
async def smart_lane_journal(name: str, day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                             decision: str | None = Query(default=None, pattern=r"^[A-Z_]{2,40}$"),
                             limit: int = Query(default=300, ge=1, le=5000)):
    """Every entry decision -- taken, or why not -- with the score, cut and sizing it was made on."""
    lane = _smart_lane(name)
    rows = await asyncio.to_thread(lambda: lane.journal_rows(day=day, decision=decision, limit=limit))
    return {"rows": rows, "days": await asyncio.to_thread(lane.journal_days)}


@app.get("/api/lanes/{name}/book", dependencies=[Depends(authorize)])
async def smart_lane_book(name: str, limit: int = Query(default=500, ge=1, le=5000)):
    """A lane's own orders, fills, portfolio and recently closed positions, in the MACD lane's shapes (its ledger)."""
    lane = _smart_lane(name)

    def read() -> dict:
        return {"orders": lane.repository.rows("orders", limit), "trades": lane.repository.rows("trades", limit),
                "portfolio": lane.execution.portfolio_snapshot(),
                "closed_positions": lane.execution.visible_closed_positions()}
    return fast_json(await asyncio.to_thread(read))


@app.get("/api/lanes/{name}/equity", dependencies=[Depends(authorize)])
async def smart_lane_equity(name: str):
    return {"equity": await asyncio.to_thread(_smart_lane(name).equity_curve)}


@app.get("/api/blast/snapshot", dependencies=[Depends(authorize)])
async def blast_snapshot():
    """The blast lane's own book, screen parameters and journal summary."""
    return engine.blast.snapshot()


@app.get("/api/blast/journal", dependencies=[Depends(authorize)])
async def blast_journal(day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                        taken_only: bool = Query(default=False),
                        reason: str | None = Query(default=None, pattern=r"^[A-Z_]{2,40}$"),
                        limit: int = Query(default=500, ge=1, le=5000)):
    """Every candidate the screen judged, taken or rejected, newest first.

    The rejected rows are the control group: each one that cleared the premium
    gate carries the forward excursion the lane went on to observe, which is
    what makes "did this leg of the screen earn its place" an answerable
    question rather than an opinion.
    """
    return {
        "rows": engine.blast.journal_rows(day=day, taken_only=taken_only, reason=reason, limit=limit),
        "summary": engine.blast.journal_summary(day),
        "days": engine.blast.journal_days(),
    }


@app.get("/api/blast/book", dependencies=[Depends(authorize)])
async def blast_book():
    return {
        "orders": engine.blast.repository.rows("orders", 500),
        "trades": engine.blast.repository.rows("trades", 500),
        "signals": engine.blast.repository.rows("signals", 500),
        "equity": engine.blast.repository.equity_rows(5000),
        "portfolio": engine.blast.execution.portfolio_snapshot(),
        "closed_positions": engine.blast.execution.visible_closed_positions(),
    }


@app.put("/api/blast/settings", dependencies=[Depends(authorize)])
async def blast_settings(payload: BlastSettingsInput):
    updates = payload.model_dump(exclude_none=True)
    risk_keys = {"hard_stop_pct", "trail_activation_pct", "trail_pct"}
    if engine.blast.portfolio.positions and risk_keys.intersection(updates):
        raise HTTPException(409, "Close blast positions before changing active stop parameters")
    merged = {**engine.settings.model_dump(),
              **{f"blast_{key}": value for key, value in updates.items()}}
    try:
        new_settings = Settings(**merged)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    engine.settings = new_settings
    engine.blast.apply_settings(new_settings)
    persist_settings(new_settings)
    return engine.blast.snapshot()


@app.post("/api/blast/orders", dependencies=[Depends(authorize)])
async def blast_manual_order(payload: OrderInput):
    """Manual ticket against the blast lane's own book."""
    try:
        order = await engine.blast.manual_order(
            payload.symbol, payload.side, payload.lots,
            order_type=payload.order_type, limit_price=payload.limit_price,
        )
    except (ValueError, RuntimeError, PermissionError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _as_json(order)






@app.get("/api/system/health", dependencies=[Depends(authorize)])
async def system_health():
    return fast_json({**engine.health(), "alerts": alert_manager.status()})


@app.get("/api/equity-history", dependencies=[Depends(authorize)])
async def equity_history(
    period: Literal["1d", "1w", "1m", "all"] = Query(default="1d"),
    timeframe_seconds: int = Query(default=60, ge=5, le=86_400),
):
    """Equity by trading session, not wall clock: 1D is the latest session with
    samples, 1W the last 5, 1M the last 21. A 24-hour window left the curve empty
    every weekend, holiday and morning before the open."""
    sessions = {"1d": 1, "1w": 5, "1m": 21}.get(period)
    since = None
    if sessions:
        from datetime import date

        from . import market_calendar
        # Holidays and weekends carry the odd startup mark; they are not sessions.
        days = [day for day in await asyncio.to_thread(engine.repository.equity_days)
                if market_calendar.is_trading_day(date.fromisoformat(day))]
        since = days[-sessions] if len(days) >= sessions else (days[0] if days else None)
    return await asyncio.to_thread(engine.repository.equity_buckets, since, timeframe_seconds)


_RRG_TTL_SECONDS = 300
_rrg_refreshing: set[str] = set()


async def _compute_and_store_rrg(key: str, timeframe_seconds: int, window: int, tail: int) -> dict:
    from .rrg import compute_rrg

    value = await asyncio.to_thread(
        compute_rrg,
        engine.settings.research_database_path,
        # The real NSE sector and broad indices ride along (engine.context_indices), so the page can plot them
        # directly instead of only averaging member stocks.
        list(dict.fromkeys([*engine.settings.symbols, *(engine.context_indices or engine.settings.context_indices)])),
        "NSE:NIFTY50-INDEX",
        timeframe_seconds,
        window,
        tail,
    )
    await asyncio.to_thread(engine.repository.save_rrg, key, value)
    return value


@app.get("/api/rrg", dependencies=[Depends(authorize)])
async def rrg(
    timeframe_seconds: int = Query(default=1800, ge=300, le=86_400),
    window: int = Query(default=14, ge=5, le=60),
    tail: int = Query(default=8, ge=2, le=30),
):
    """Persisted rotation snapshot with stale-while-revalidate refresh.

    Snapshots live in sqlite, so they survive restarts and a client never
    waits on the ~10s full-universe computation: a stale copy is served
    immediately while one background task refreshes it.
    """
    from datetime import datetime, timezone

    key = f"{timeframe_seconds}:{window}:{tail}"
    stored = await asyncio.to_thread(engine.repository.load_rrg, key)
    if stored:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(stored["generated_at"])).total_seconds()
        except (KeyError, ValueError):
            age = _RRG_TTL_SECONDS + 1
        if age > _RRG_TTL_SECONDS and key not in _rrg_refreshing:
            _rrg_refreshing.add(key)

            async def refresh() -> None:
                try:
                    await _compute_and_store_rrg(key, timeframe_seconds, window, tail)
                finally:
                    _rrg_refreshing.discard(key)

            asyncio.create_task(refresh())
        return stored
    return await _compute_and_store_rrg(key, timeframe_seconds, window, tail)


@app.get("/api/research/report", dependencies=[Depends(authorize)])
async def research_report():
    path = Path(engine.settings.research_report_path)
    if not path.exists():
        raise HTTPException(404, "No walk-forward run has been recorded yet")
    return json.loads(path.read_text())


@app.get("/api/research/trades", dependencies=[Depends(authorize)])
async def research_trades(limit: int = Query(default=500, ge=1, le=5000), run_id: str | None = Query(default=None)):
    path = Path(engine.settings.research_database_path)
    if not path.exists():
        return []
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        selected_run = run_id
        if not selected_run:
            latest = connection.execute("SELECT run_id FROM backtest_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            selected_run = latest["run_id"] if latest else ""
        rows = connection.execute(
            "SELECT * FROM backtest_trades WHERE run_id=? ORDER BY entry_time DESC LIMIT ?", (selected_run, limit)
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        # The research database exists but no walk-forward run has written its
        # tables yet — an empty ledger, not a server error.
        return []
    finally:
        connection.close()


def public_settings(current: Settings | None = None) -> dict:
    current = current or engine.settings
    return {
        "feed_mode": current.feed_mode,
        "execution_mode": "paper",
        "symbols": current.symbols,
        "timeframe_seconds": current.timeframe_seconds,
        "fast_period": current.fast_period,
        "slow_period": current.slow_period,
        "signal_period": current.signal_period,
        "bb_period": current.bb_period,
        "bb_deviations": current.bb_deviations,
        "kama_period": current.kama_period,
        "kama_fast": current.kama_fast,
        "kama_slow": current.kama_slow,
        "kama_rsi_period": current.kama_rsi_period,
        "kama_rsi_min": current.kama_rsi_min,
        "kama_roc_period": current.kama_roc_period,
        "kama_roc_min": current.kama_roc_min,
        "require_kama_confirmation": current.require_kama_confirmation,
        "require_kama_rsi_confirmation": current.require_kama_rsi_confirmation,
        "require_kama_roc_confirmation": current.require_kama_roc_confirmation,
        "entry_volume_ratio": current.entry_volume_ratio,
        "max_trade_lots": current.max_trade_lots,
        "max_positions": current.max_positions,
        "min_cash_reserve": current.min_cash_reserve,
        "macd_invalidation_exit": current.macd_invalidation_exit,
        "macd_invalidation_max_mfe_pct": current.macd_invalidation_max_mfe_pct,
        "target_position_notional": current.target_position_notional,
        "max_target_entry_lots": current.max_target_entry_lots,
        "initial_capital": current.initial_capital,
        "signal_mode": current.signal_mode,
        "auto_trade": current.auto_trade,
        "order_quantity": current.order_quantity,
        "slippage_bps": current.slippage_bps,
        "mp_max_positions": current.mp_max_positions,
        "mp_max_trades_per_day": current.mp_max_trades_per_day,
        "hard_stop_pct": current.hard_stop_pct,
        "trailing_activation_pct": current.trailing_activation_pct,
        "trailing_stop_pct": current.trailing_stop_pct,
        "fyers_client_id": current.fyers_client_id,
        "fyers_secret_configured": bool(current.fyers_secret),
        "fyers_access_token_configured": bool(current.fyers_access_token),
        "fyers_refresh_token_configured": bool(current.fyers_refresh_token),
        "fyers_pin_configured": bool(current.fyers_pin),
        "fyers_redirect_uri": current.fyers_redirect_uri,
        "telegram_configured": bool(current.telegram_bot_token and current.telegram_chat_id),
        "telegram_chat_id": current.telegram_chat_id,
        "day_loss_alert_rupees": current.day_loss_alert_rupees,
        "broker": engine.broker_status(),
    }


@app.get("/api/settings", dependencies=[Depends(authorize)])
async def get_settings():
    return public_settings(desired_settings())


# A feed rebuild runs for minutes in the background and only then replaces
# engine.settings. Every save in that window must build on what was already
# accepted, not on the engine's outgoing settings, or it would write the old
# token back to disk and hand it to the broker once the rebuild finishes.
_pending_settings: Settings | None = None
_settings_tasks: set[asyncio.Task] = set()


def desired_settings() -> Settings:
    """The most recently accepted settings, applied or still being applied."""
    return _pending_settings or engine.settings


def schedule_settings(new_settings: Settings, *, feed: bool) -> None:
    """Apply settings behind any rebuild already running, in save order."""
    global _pending_settings
    _pending_settings = new_settings
    task = asyncio.create_task(apply_settings(new_settings, feed=feed))
    # The loop keeps only a weak reference to a task; hold it until done.
    _settings_tasks.add(task)
    task.add_done_callback(_settings_tasks.discard)


async def apply_settings(new_settings: Settings, *, feed: bool) -> None:
    """Keep an unexpected rebuild failure visible to health and the terminal."""
    global _pending_settings
    try:
        if feed:
            await engine.reconfigure(new_settings)
        else:
            await engine.reconfigure_strategy(new_settings)
    except Exception as exc:  # noqa: BLE001 - background task has no HTTP caller left
        engine.status = "error"
        engine.error = f"Settings reconfiguration failed: {exc}"
        engine.events.publish("broker", engine.broker_status())
    finally:
        if _pending_settings is new_settings:
            _pending_settings = None



@app.put("/api/settings", dependencies=[Depends(authorize)])
async def update_settings(payload: SettingsInput):
    updates = payload.model_dump(exclude_none=True)
    base = desired_settings()
    if "symbols" in updates:
        normalized_symbols = list(dict.fromkeys(symbol.strip() for symbol in updates.pop("symbols") if symbol.strip()))
        if not normalized_symbols:
            raise HTTPException(400, "At least one Fyers symbol is required")
        updates["symbols_csv"] = ",".join(normalized_symbols)
    if updates.get("fast_period", base.fast_period) >= updates.get("slow_period", base.slow_period):
        raise HTTPException(400, "MACD fast period must be below slow period")
    if updates.get("kama_fast", base.kama_fast) >= updates.get("kama_slow", base.kama_slow):
        raise HTTPException(400, "KAMA fast period must be below slow period")
    # An empty token means "leave the saved secret unchanged". It is never
    # returned to the browser after save.
    for secret_field in ("fyers_secret", "fyers_access_token", "telegram_bot_token"):
        if updates.get(secret_field) == "":
            updates.pop(secret_field)
    merged = {
        **base.model_dump(),
        **updates,
        "execution_mode": "paper",
        "allow_live_orders": False,
    }
    try:
        new_settings = Settings(**merged)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    persisted = {
        key: value
        for key, value in new_settings.model_dump().items()
        if key not in PERSIST_EXCLUDED
    }
    settings_store.save(persisted)
    persist_credentials(new_settings)
    # A changed subscription or broker identity needs a rebuilt contract
    # selection and websocket. That can take minutes for the full India
    # universe, so expose a connecting state while it runs in the background.
    feed_fields = ("symbols_csv", "feed_mode", "fyers_client_id", "fyers_secret",
                   "fyers_access_token", "fyers_redirect_uri")
    feed_changed = any(
        getattr(new_settings, field) != getattr(base, field)
        for field in feed_fields
    )
    if feed_changed:
        engine.status = "connecting"
        engine.error = None
        engine.events.publish("broker", engine.broker_status())
        schedule_settings(new_settings, feed=True)
        return {**public_settings(new_settings), "connect_state": "connecting"}
    if _pending_settings is not None:
        # A rebuild holds the engine lock; queue behind it rather than hold
        # this request open for minutes and let the proxy time it out.
        schedule_settings(new_settings, feed=False)
        return {**public_settings(new_settings), "connect_state": "connecting"}
    await engine.reconfigure_strategy(new_settings)
    return public_settings()


def persist_settings(new_settings: Settings) -> None:
    persisted = {
        key: value for key, value in new_settings.model_dump().items()
        if key not in PERSIST_EXCLUDED
    }
    settings_store.save(persisted)


def persist_credentials(new_settings: Settings) -> None:
    credentials_store.save({
        "fyers_client_id": new_settings.fyers_client_id,
        "fyers_secret": new_settings.fyers_secret,
        "fyers_access_token": new_settings.fyers_access_token,
        "fyers_refresh_token": new_settings.fyers_refresh_token,
        "fyers_pin": new_settings.fyers_pin,
        "fyers_redirect_uri": new_settings.fyers_redirect_uri,
        "telegram_bot_token": new_settings.telegram_bot_token,
        "telegram_chat_id": new_settings.telegram_chat_id,
    })


@app.get("/api/auth/fyers/url", dependencies=[Depends(authorize)])
async def fyers_auth_url():
    try:
        return {"auth_url": engine.broker.auth_url(), "redirect_uri": engine.settings.fyers_redirect_uri}
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/auth/fyers/credentials", dependencies=[Depends(authorize)])
async def fyers_credentials(payload: FyersCredentialsInput):
    updates = {
        "fyers_client_id": payload.client_id.strip(),
        "fyers_redirect_uri": payload.redirect_uri.strip(),
    }
    if payload.secret:
        updates["fyers_secret"] = payload.secret.strip()
    if payload.pin:
        updates["fyers_pin"] = payload.pin.strip()
    new_settings = Settings(**{**desired_settings().model_dump(), **updates})
    persist_settings(new_settings)
    persist_credentials(new_settings)
    if _pending_settings is not None:
        # The rebuild in flight would install settings without these
        # credentials; rebuild again behind it with them included.
        schedule_settings(new_settings, feed=True)
    else:
        engine.settings = new_settings
        engine.broker.settings = new_settings
    return {"saved": True, "redirect_uri": new_settings.fyers_redirect_uri}


async def install_fyers_token(access_token: str, described: dict | None = None, refresh_token: str | None = None) -> dict:
    if described and described.get("expires_at") and described["expires_at"] <= datetime.now(UTC):
        expired = described["expires_at"].astimezone(IST).strftime("%d %b %H:%M")
        raise HTTPException(400, f"That access token expired at {expired} IST -- Fyers tokens last until 06:00 IST "
                                 "the next morning. Log in again (step 1) for today's.")
    merged = {**desired_settings().model_dump(), "fyers_access_token": access_token,
              **({"fyers_refresh_token": refresh_token} if refresh_token else {})}
    new_settings = Settings(**merged)
    # Validate the token with a fast /profile probe BEFORE persisting, then
    # answer immediately and rebuild the watchlist in the background. The old
    # blocking flow held the HTTP response through a multi-minute contract
    # build, so the terminal's nginx proxy timed out and the browser reported
    # failure while the backend went on to connect successfully.
    probe = create_broker(new_settings)
    try:
        await probe.connect()
        accepted_expiry = probe.token_expiry()
    except FyersConnectionError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        client = new_settings.fyers_client_id or "this app"
        raise HTTPException(400, "Fyers did not accept this access token -- it is invalid, revoked, or was issued "
                                 f"for a different app than {client}. Log in again (step 1) and paste the redirect "
                                 "address in step 2.") from exc
    finally:
        await probe.close()
    persist_settings(new_settings)
    persist_credentials(new_settings)
    schedule_settings(new_settings, feed=True)
    return {
        **public_settings(),
        "connect_state": "connecting",
        "accepted_token_expires_at": accepted_expiry.isoformat() if accepted_expiry else None,
    }


@app.post("/api/auth/fyers/exchange", dependencies=[Depends(authorize)])
async def fyers_exchange(payload: AuthCodeInput):
    try:
        pasted = payload.auth_code.strip()
        if "://" in pasted:
            query = parse_qs(urlparse(pasted).query)
            if not query.get("auth_code"):
                raise HTTPException(400, "That address has no auth_code in it -- copy the full address of the page "
                                         "Fyers redirected to after you logged in.")
            pasted = str(query["auth_code"][0])
        tokens = await engine.broker.exchange_auth_code(pasted)
        return await install_fyers_token(tokens["access_token"], refresh_token=tokens.get("refresh_token"))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Fyers authorization failed: {exc}") from exc


@app.post("/api/auth/fyers/access-token", dependencies=[Depends(authorize)])
async def fyers_access_token(payload: AccessTokenInput):
    """Install a pasted access token -- or, if what was pasted is really the redirect address or an
    auth code (the usual mix-up after logging in), exchange it instead of failing with a token error."""
    from .brokers import describe_fyers_token
    pasted = describe_fyers_token(payload.access_token)
    if pasted["kind"] in ("redirect", "auth_code"):
        if not pasted["value"]:
            raise HTTPException(400, "That address has no auth_code in it -- copy the full address of the page "
                                     "Fyers redirected to after you logged in.")
        return await fyers_exchange(AuthCodeInput(auth_code=pasted["value"]))
    return await install_fyers_token(pasted["value"], described=pasted)


@app.post("/api/auth/fyers/refresh", dependencies=[Depends(authorize)])
async def fyers_refresh():
    """The daily login without a browser: refresh token + PIN -> today's access token (the platform calls this
    before the open)."""
    try:
        tokens = await engine.broker.refresh_access_token()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await install_fyers_token(tokens["access_token"])


@app.get("/api/auth/fyers/callback", response_class=HTMLResponse)
async def fyers_callback(auth_code: str | None = Query(default=None), code: str | None = Query(default=None)):
    actual_code = auth_code or code
    if not actual_code:
        return HTMLResponse("<h2>Fyers did not return an authorization code.</h2>", status_code=400)
    try:
        tokens = await engine.broker.exchange_auth_code(actual_code)
        await install_fyers_token(tokens["access_token"], refresh_token=tokens.get("refresh_token"))
    except Exception as exc:
        return HTMLResponse(f"<h2>Fyers connection failed</h2><p>{escape(str(exc))}</p>", status_code=400)
    return HTMLResponse("""
    <body style='background:#080b18;color:#00d99a;font-family:system-ui;padding:2rem'>
    <h2>Fyers token saved</h2><p>The market-data feed is reconnecting. You can close this tab.</p>
    <script>window.opener&&window.opener.postMessage({broker:'fyers',status:'connecting'},'*');setTimeout(()=>window.close(),1500)</script>
    </body>""")


@app.post("/api/orders", dependencies=[Depends(authorize)])
async def place_order(payload: OrderInput):
    try:
        return await engine.execution.submit(**payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def websocket_origin_allowed(origin: str | None) -> bool:
    """Refuse a socket opened by some other website in the owner's browser.

    CORS does not apply to WebSockets, and the socket accepts orders, so
    without this any page the owner visited could trade the paper book. A
    DNS-rebound page carries its own hostname as Origin and is refused too.
    Non-browser clients send no Origin and are governed by the API token.
    """
    if not origin:
        return True
    extra = [item.strip().rstrip("/") for item in engine.settings.allowed_origins_csv.split(",")]
    if origin.rstrip("/") in {*CORS_ORIGINS, *filter(None, extra)}:
        return True
    return (urlparse(origin).hostname or "") in LOOPBACK_HOSTS


async def websocket_authorize(websocket: WebSocket) -> bool:
    token = websocket.query_params.get("token", "")
    if engine.settings.api_token and token != engine.settings.api_token:
        await websocket.close(code=4401)
        return False
    if not websocket_origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return False
    await websocket.accept()
    return True


@app.websocket("/ws/stream")
async def stream(websocket: WebSocket):
    if not await websocket_authorize(websocket):
        return
    queue = await engine.events.subscribe()

    def enqueue_snapshot():
        # Build and enqueue without an await: the sequence is the exact cut
        # represented by this snapshot. One sender owns every socket write.
        data = engine.snapshot()
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(Frame(engine.events.current_sequence, "snapshot", data))

    enqueue_snapshot()

    async def sender():
        while True:
            frame = await queue.get()
            # Every other socket shares a frame's cached text; the snapshot is
            # built for this socket alone and is large, so encode it off-loop.
            text = (await asyncio.to_thread(frame_text, frame)) if frame.get("type") == "snapshot" else frame_text(frame)
            await websocket.send_text(text)

    async def receiver():
        while True:
            message = await websocket.receive_json()
            command = message.get("command")
            if command == "snapshot":
                enqueue_snapshot()
            elif command == "order":
                # A refused order is answered, not allowed to end the stream.
                try:
                    order = OrderInput.model_validate(message.get("data", {}))
                    await engine.execution.submit(**order.model_dump())
                except (ValidationError, ValueError, PermissionError, RuntimeError) as exc:
                    await websocket.send_json({"type": "order_error", "data": {"message": str(exc)[:500]}})
            elif command == "ping":
                await websocket.send_json({"type": "pong", "data": {}})

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task
        await engine.events.unsubscribe(queue)


# -- internal API, for the desk process ------------------------------------
# Reached only across the Compose network: the gateway refuses /api/internal/*
# from browsers. This process stays the only Fyers client and the only writer
# of settings.json; the desk asks it for contracts, broker data and saves.

class DeskHoldingsInput(BaseModel):
    holdings: dict[str, int]


class QuotesInput(BaseModel):
    symbols: list[str] = Field(max_length=200)


@app.get("/api/internal/desk-context", dependencies=[Depends(authorize)])
async def internal_desk_context():
    return fast_json(engine.desk_context())


@app.post("/api/internal/desk-holdings", dependencies=[Depends(authorize)])
async def internal_desk_holdings(payload: DeskHoldingsInput):
    return {"unsubscribed": engine.update_desk_holdings(payload.holdings)}


@app.get("/api/internal/option-chain/{symbol:path}", dependencies=[Depends(authorize)])
async def internal_option_chain(symbol: str, strikecount: int | None = Query(None, ge=1, le=50),
                                expiry: str | None = Query(None, max_length=20),
                                priority: str | None = Query(None, pattern="^(critical|high|normal|bulk)$")):
    # Only the overrides the caller sent: the default call is the one every broker implements.
    extra = {**({"strikecount": strikecount} if strikecount else {}), **({"expiry_token": expiry} if expiry else {}),
             **({"priority": priority} if priority else {})}
    try:
        chain = await engine.broker.option_chain(symbol, **extra)
    except Exception as exc:  # noqa: BLE001 - relayed: the desk backs off on a 429 in the text
        raise HTTPException(502, f"broker option chain failed: {exc}") from exc
    return fast_json(chain)


@app.get("/api/internal/open-interest/{symbol:path}", dependencies=[Depends(authorize)])
async def internal_open_interest(symbol: str):
    try:
        return {"symbol": symbol, "open_interest": await engine.broker.open_interest(symbol)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"broker open interest failed: {exc}") from exc


@app.post("/api/internal/quotes", dependencies=[Depends(authorize)])
async def internal_quotes(payload: QuotesInput):
    try:
        quotes = await engine.broker.quotes(payload.symbols)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"broker quotes failed: {exc}") from exc
    return fast_json(quotes)


IST = ZoneInfo("Asia/Kolkata")


class BackfillInput(BaseModel):
    symbol: str = Field(min_length=3, max_length=64)
    since: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


_backfill_lock = asyncio.Lock()


@app.post("/api/internal/backfill-minutes", dependencies=[Depends(authorize)])
async def internal_backfill_minutes(payload: BackfillInput):
    """Download ``symbol``'s 1-minute bars since ``since`` into the durable store.

    For the desk's past profiles (a previous month, earlier weeks): this
    process is the only Fyers client. One symbol at a time, so a page opening
    several charts cannot burst the history rate limit; Fyers serves at most
    100 days of 1-minute history per request.
    """
    start = datetime.fromisoformat(payload.since).replace(tzinfo=IST)
    start = max(start, datetime.now(IST) - timedelta(days=95))
    async with _backfill_lock:
        try:
            rows = await engine.broker.history_range(payload.symbol, 60, start, datetime.now(IST))
        except Exception as exc:  # noqa: BLE001 - relayed to the desk
            raise HTTPException(502, f"broker history failed: {exc}") from exc
        stored = await asyncio.to_thread(
            store_historical_candles, engine.settings.research_database_path, rows)
    return {"symbol": payload.symbol, "rows": stored,
            "first": rows[0].timestamp if rows else None, "last": rows[-1].timestamp if rows else None}


@app.put("/api/internal/settings", dependencies=[Depends(authorize)])
async def internal_settings(updates: dict):
    """Persist the desk's own settings (mp_*), which this process owns on disk."""
    allowed = {key: value for key, value in updates.items() if key.startswith("mp_") and key in Settings.model_fields}
    if len(allowed) != len(updates):
        raise HTTPException(400, "Only mp_* settings are saved through this route")
    try:
        engine.settings = Settings(**{**engine.settings.model_dump(), **allowed})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    persist_settings(engine.settings)
    return {"saved": sorted(allowed)}


async def persist_mp_settings_locally(config_updates: dict) -> None:
    engine.settings = engine.settings.model_copy(update=config_updates)
    persist_settings(engine.settings)


# The Market Profile / order-flow desk's routes run in this process only when
# it runs every lane. In the split layout the desk process serves them.
if active_settings.engine_role == "all":
    desk_routes.bind(engine, persist_mp_settings_locally)
    app.include_router(desk_routes.router)
