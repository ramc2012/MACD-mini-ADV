from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from .alerts import AlertManager
from .brokers import create_broker
from .config import Settings, settings
from .chart_history import load_chart_history
from . import auction_views
from .dispersion import load as load_dispersion
from .engine import TradingEngine
from .events import Frame, dumps, frame_text, json_value
from .replay import ReplaySession
from .universe import desk_underlying
from .settings_store import RuntimeSettingsStore


# Paths, the process layout and secrets come from the environment and
# credentials.json, never from settings.json: a stored tick_database_path used
# to override the one Compose set, so moving a database silently did nothing.
ENVIRONMENT_ONLY = {
    "database_path", "runtime_settings_path", "credentials_path", "research_database_path",
    "research_report_path", "contract_snapshot_path", "mp_database_path",
    "blast_database_path", "tick_database_path", "api_token", "allowed_origins_csv",
    "engine_role", "nats_url", "strategy_url", "desk_url",
}
PERSIST_EXCLUDED = ENVIRONMENT_ONLY | {
    # Broker secrets live in credentials.json only — never mirrored into
    # settings.json, where a stale plaintext token would otherwise linger.
    "fyers_secret", "fyers_access_token", "telegram_bot_token",
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


class OrderInput(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    lots: int = Field(default=1, ge=1)
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: float | None = Field(default=None, gt=0)


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


@app.get("/api/chart/{symbol:path}", dependencies=[Depends(authorize)])
async def chart_history(symbol: str, timeframe_seconds: int = Query(default=1800, ge=60, le=86400)):
    if symbol not in engine.all_symbols:
        raise HTTPException(404, "Symbol is not in the active watchlist")
    live_rows = (
        list(engine.history.get(symbol, ()))
        if timeframe_seconds == engine.settings.timeframe_seconds
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


def _ist_today() -> str:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()


@app.get("/api/auction/symbols", dependencies=[Depends(authorize)])
async def auction_symbols():
    """Underlyings with a stored session profile."""
    return await asyncio.to_thread(
        auction_views.symbols, engine.settings.research_database_path)


@app.get("/api/auction/context/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_context(symbol: str, as_of: str | None = None):
    """Prior day, week and month value areas, naked POCs and where price sits."""
    tick = engine.latest_ticks.get(symbol)
    return await asyncio.to_thread(
        auction_views.context, engine.settings.research_database_path, symbol,
        as_of or _ist_today(), tick.ltp if tick else None)


@app.get("/api/auction/sessions/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_sessions(symbol: str, limit: int = Query(default=120, ge=1, le=1000)):
    payload = await asyncio.to_thread(
        auction_views.sessions, engine.settings.research_database_path, symbol, limit)
    periods = await asyncio.to_thread(
        auction_views.periods, engine.settings.research_database_path, symbol)
    return {"symbol": symbol, "sessions": payload, "periods": periods}


@app.get("/api/auction/base-rates/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_base_rates(symbol: str, as_of: str | None = None):
    return await asyncio.to_thread(
        auction_views.base_rates, engine.settings.research_database_path, symbol, as_of)


@app.get("/api/auction/flow/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_flow(symbol: str, day: str | None = None):
    """Per-minute OFI against inferred delta for one recorded session."""
    days = await asyncio.to_thread(
        auction_views.flow_days, engine.settings.tick_database_path, symbol)
    chosen = day or (days[0] if days else _ist_today())
    payload = await asyncio.to_thread(
        auction_views.flow, engine.settings.tick_database_path, symbol, chosen)
    return {**payload, "available_days": days}


@app.get("/api/auction/setups/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_setups(symbol: str, regime_id: str | None = None):
    """The Part 7 validation journal: context / trigger / outcome rates per setup."""
    return await asyncio.to_thread(
        auction_views.setup_journal, engine.settings.research_database_path, symbol, regime_id)


@app.get("/api/auction/whale", dependencies=[Depends(authorize)])
async def auction_whale(day: str | None = None, symbol: str | None = None):
    """Layer A events, decayed aggression per symbol, and the latest chain window."""
    return await asyncio.to_thread(
        auction_views.whale, engine.settings.research_database_path, day or _ist_today(), symbol)


@app.get("/api/auction/whale/windows/{underlying}", dependencies=[Depends(authorize)])
async def auction_whale_windows(underlying: str, day: str | None = None):
    """The day's per-window series for one root: composite, option vs futures delta-notional, divergence."""
    return await asyncio.to_thread(
        auction_views.whale_windows, engine.settings.research_database_path,
        underlying.upper(), day or _ist_today())


@app.get("/api/auction/whale/alerts", dependencies=[Depends(authorize)])
async def auction_whale_alerts(day: str | None = None,
                               limit: int = Query(default=50, ge=1, le=500)):
    """Composite alerts with their 15/30/60-minute 'what happened next' records and the 20-session hit rate."""
    return await asyncio.to_thread(
        auction_views.whale_alerts, engine.settings.research_database_path,
        day or _ist_today(), limit)


@app.get("/api/auction/positional", dependencies=[Depends(authorize)])
async def auction_positional(day: str | None = None,
                             limit: int = Query(default=200, ge=1, le=2000)):
    return await asyncio.to_thread(
        auction_views.positional, engine.settings.research_database_path, day, limit)


@app.get("/api/orders", dependencies=[Depends(authorize)])
async def orders(limit: int = Query(default=10_000, ge=1, le=100_000)):
    return engine.repository.rows("orders", limit)


@app.get("/api/trades", dependencies=[Depends(authorize)])
async def trades(limit: int = Query(default=10_000, ge=1, le=100_000)):
    return engine.repository.rows("trades", limit)


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


class MPSettingsInput(BaseModel):
    enabled: bool | None = None
    auto_trade: bool | None = None
    max_positions: int | None = Field(default=None, ge=1, le=20)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=100)
    hard_stop_pct: float | None = Field(default=None, gt=0, lt=1)
    trail_activation_pct: float | None = Field(default=None, gt=0, lt=5)
    trail_pct: float | None = Field(default=None, gt=0, lt=1)
    min_imbalance: float | None = Field(default=None, ge=0, le=1)
    slippage_bps: float | None = Field(default=None, ge=0, le=500)
    brokerage_per_leg: float | None = Field(default=None, ge=0, le=10_000)
    allow_overnight_carry: bool | None = None


@app.get("/api/mp/snapshot", dependencies=[Depends(authorize)])
async def mp_snapshot(symbol: str | None = Query(default=None)):
    """Full desk state: profile, order flow, book, current setup."""
    return engine.mp.snapshot(symbol)


@app.get("/api/mp/profile/{symbol:path}", dependencies=[Depends(authorize)])
async def mp_profile(symbol: str, full: bool = Query(default=False),
                     profile_only: bool = Query(default=False)):
    profile = engine.mp.profiles.get(symbol)
    if profile is None:
        raise HTTPException(404, "No profile has formed for that symbol today")
    # The polled desk snapshot is deliberately small. A focused auction ladder
    # needs every traded price row, so fetch it explicitly while that view is
    # open instead of thinning away volume and TPO structure.
    result = {"profile": profile.snapshot(max_levels=None if full else 90)}
    if not profile_only:
        result["flow"] = engine.mp.flow.snapshot(symbol)
    return result


@app.get("/api/mp/leaderboard", dependencies=[Depends(authorize)])
async def mp_leaderboard(limit: int = Query(default=25, ge=1, le=200)):
    """Symbols ranked by how constructive their auction reads right now."""
    return engine.mp.leaderboard(limit)


@app.get("/api/mp/footprint/{symbol:path}", dependencies=[Depends(authorize)])
async def mp_footprint(symbol: str, bars: int = Query(default=60, ge=5, le=180),
                       timeframe_seconds: int | None = Query(default=None, ge=60, le=3600),
                       composite_days: int = Query(default=0, ge=0, le=60),
                       profile_full: bool = Query(default=False)):
    """Per-bar bid/ask clusters, DOM touch and tape for one contract.

    Requesting a symbol subscribes it for detailed capture, the way opening a
    chart does in a professional platform — clusters for the whole universe
    would cost hundreds of megabytes nobody is looking at. Subscribing is also
    what puts the contract in scope for level-1 OFI, which keeps ~1 MB of book
    state per symbol and is therefore kept to the contracts being read.

    ``composite_days`` merges the stored daily ladders of the underlying into
    an N-day composite profile. It is asked for explicitly because 20 days of
    ladders is thousands of rows on a poll that already runs every 3 s.
    """
    book = engine.mp.footprints
    fresh = not book.watching(symbol)
    # Seed from the flow tracker's retained classified prints so a freshly
    # opened chart is immediately populated with real clusters, and anchor both
    # CVD legs on the session figures the tracker already holds — without the
    # anchor the book's CVD starts at zero on watch() and two numbers a screen
    # apart are both labelled CVD.
    seed = engine.mp.flow.states.get(symbol)
    book.watch(symbol, seed_prints=list(seed.recent) if seed else None,
               session_delta=seed.cumulative_delta if seed else None,
               session_weighted_delta=seed.weighted_delta if seed else None)
    flow = engine.mp.flow.snapshot(symbol)
    payload = book.payload(symbol, bars, timeframe_seconds=timeframe_seconds, flow=flow)
    payload["subscribed_now"] = fresh
    profile = engine.mp.profiles.get(symbol)
    # ONE profile snapshot, handed to both readers: it is the most expensive
    # thing on this route and session_view used to take a second one.
    snapshot = profile.snapshot(max_levels=None if profile_full else 90) if profile else None
    payload["profile"] = snapshot
    payload["flow"] = flow
    setup, reason = engine.mp.live_setups.get(symbol, (None, "no tick yet"))
    payload["setup"] = {"setup": setup, "reason": reason}
    day = engine.mp.session_day or _ist_today()
    # An option contract has no stored session of its own; its prior-day value
    # is its underlying's, and so is the VIX range's reference price.
    underlying = desk_underlying(symbol) or symbol
    # Cached for 60 s inside MPEngine — the night job writes this reference
    # once and it cannot change during a session, while this route polls at 3 s.
    # In a worker thread it reads inline; called on the loop (MPEngine.snapshot)
    # it serves the cache and refreshes behind it.
    context = await asyncio.to_thread(engine.mp.session_context, underlying, day)
    payload["context"] = context
    payload["session"] = engine.mp.session_view(symbol, context=context, snapshot=snapshot)
    payload["ofi"] = engine.mp.ofi_view(symbol, timeframe_seconds or book.timeframe)
    payload["tape_speed"] = engine.mp.flow.tape_speed(symbol)
    payload["composite"] = await asyncio.to_thread(
        auction_views.composite, engine.settings.research_database_path,
        engine.settings.tick_database_path, underlying, composite_days, day,
    ) if composite_days else None
    return payload


# --- session replay ---------------------------------------------------------
# One loaded session at a time, keyed by (symbol, day, capture timeframe). A
# contract-session is ~10^5 Tick objects, so this is deliberately a cache of
# ONE: opening a second symbol evicts the first rather than doubling RSS.
_replay_lock = asyncio.Lock()
_replay_cache: dict[tuple, ReplaySession] = {}


def _replay_session(symbol: str, day: str, timeframe: int) -> ReplaySession:
    key = (symbol, day, timeframe)
    session = _replay_cache.get(key)
    if session is None:
        _replay_cache.clear()
        session = ReplaySession(symbol, day, timeframe).build(
            engine.settings.tick_database_path)
        _replay_cache[key] = session
    return session


@app.get("/api/mp/replay-days/{symbol:path}", dependencies=[Depends(authorize)])
async def mp_replay_days(symbol: str):
    """IST sessions whose RAW ticks are still on disk, newest first.

    Only these can be replayed print by print; older sessions were condensed to
    minute flow and a ladder, which carry no individual prints.
    """
    return await asyncio.to_thread(
        auction_views.raw_tick_days, engine.settings.tick_database_path, symbol)


@app.get("/api/mp/replay/{symbol:path}", dependencies=[Depends(authorize)])
async def mp_replay(symbol: str, day: str,
                    at: float = Query(default=0.0, ge=0),
                    bars: int = Query(default=60, ge=5, le=180),
                    timeframe_seconds: int | None = Query(default=None, ge=60, le=3600)):
    """The whole footprint surface as it stood at one moment of a recorded day.

    Every call runs in a worker thread. Playing forward feeds only the ticks
    between two moments, but dragging the scrubber backward rebuilds the
    session from the open — measured at 2.5 s for 120k ticks — and that must
    never be time the event loop spends not ingesting live ticks. The lock
    serialises access because ReplaySession is a single mutating walk.
    """
    async with _replay_lock:
        session = await asyncio.to_thread(_replay_session, symbol, day, 60)
        if not session.ticks:
            raise HTTPException(404, "No raw ticks are still on disk for that contract and day")
        # As of the REPLAYED day, not today: naked POCs and prior-day value are
        # what the desk would have been reading that morning.
        context = await asyncio.to_thread(
            auction_views.context, engine.settings.research_database_path,
            desk_underlying(symbol) or symbol, day)
        payload = await asyncio.to_thread(
            session.payload, at or session.session_start, bars, timeframe_seconds, context)
    payload["context"] = context
    return payload


@app.get("/api/mp/statistics", dependencies=[Depends(authorize)])
async def mp_statistics():
    return engine.mp.statistics()


@app.get("/api/mp/book", dependencies=[Depends(authorize)])
async def mp_book():
    return {
        "portfolio": engine.mp.portfolio.snapshot(),
        "orders": engine.mp.repository.rows("orders", 2000),
        "trades": engine.mp.repository.rows("trades", 2000),
        "signals": engine.mp.repository.rows("signals", 500),
        "equity": engine.mp.repository.equity_rows(5000),
        "closed_positions": engine.mp.closed_view(),
    }


@app.put("/api/mp/settings", dependencies=[Depends(authorize)])
async def mp_settings(payload: MPSettingsInput):
    updates = payload.model_dump(exclude_none=True)
    risk_keys = {"hard_stop_pct", "trail_activation_pct", "trail_pct"}
    if engine.mp.portfolio.positions and risk_keys.intersection(updates):
        raise HTTPException(409, "Close MP positions before changing active stop parameters")
    for key, value in updates.items():
        setattr(engine.mp.settings, key, value)
    config_updates = {f"mp_{key}": value for key, value in updates.items()}
    engine.settings = engine.settings.model_copy(update=config_updates)
    persist_settings(engine.settings)
    return engine.mp.settings.to_dict()


class BlastSettingsInput(BaseModel):
    enabled: bool | None = None
    auto_trade: bool | None = None
    initial_capital: float | None = Field(default=None, ge=1)
    # Up to 1000 so the lane can be run unbounded, taking every signal the
    # screen passes, to measure the screen rather than a 10-slot sample.
    max_positions: int | None = Field(default=None, ge=1, le=1_000)
    target_notional: float | None = Field(default=None, ge=0, le=5_000_000)
    max_premium_pct: float | None = Field(default=None, gt=0, le=100)
    min_breadth: float | None = Field(default=None, ge=0.0, le=1.0)
    min_off_high_pct: float | None = Field(default=None, ge=0.0, le=99.0)
    hard_stop_pct: float | None = Field(default=None, gt=0, lt=1)
    trail_activation_pct: float | None = Field(default=None, gt=0, lt=5)
    trail_pct: float | None = Field(default=None, gt=0, lt=1)


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


@app.post("/api/mp/orders", dependencies=[Depends(authorize)])
async def mp_manual_order(payload: OrderInput):
    """Manual ticket against the Market-Profile desk's own book."""
    if payload.order_type != "MARKET":
        raise HTTPException(400, "The MP paper desk currently supports MARKET orders only")
    lot = engine.mp.lot_sizes.get(payload.symbol, 0)
    if lot <= 0:
        raise HTTPException(400, "Lot size unavailable for that symbol on this desk")
    order = await engine.mp.submit(payload.symbol, payload.side, payload.lots * lot)
    if order is None:
        raise HTTPException(400, engine.mp.last_order_rejection or "Paper order rejected")
    return _as_json(order)


def _as_json(order) -> dict:
    from dataclasses import asdict
    from datetime import datetime as _dt
    row = asdict(order)
    return {k: (v.isoformat() if isinstance(v, _dt) else v) for k, v in row.items()}


@app.get("/api/system/health", dependencies=[Depends(authorize)])
async def system_health():
    return fast_json({**engine.health(), "alerts": alert_manager.status()})


@app.get("/api/equity-history", dependencies=[Depends(authorize)])
async def equity_history(
    period: Literal["1d", "1w", "1m", "all"] = Query(default="1d"),
    timeframe_seconds: int = Query(default=60, ge=5, le=86_400),
):
    from datetime import datetime, timedelta, timezone

    spans = {"1d": timedelta(days=1), "1w": timedelta(days=7), "1m": timedelta(days=31)}
    cutoff = datetime.now(timezone.utc) - spans[period] if period in spans else None
    rows = engine.repository.equity_rows(200_000)
    buckets: dict[int, dict] = {}
    for row in rows:
        moment = datetime.fromisoformat(row["timestamp"])
        if cutoff and moment < cutoff:
            continue
        bucket = int(moment.timestamp()) // timeframe_seconds * timeframe_seconds
        buckets[bucket] = {**row, "timestamp": datetime.fromtimestamp(bucket, timezone.utc).isoformat()}
    return [buckets[key] for key in sorted(buckets)]


_RRG_TTL_SECONDS = 300
_rrg_refreshing: set[str] = set()


async def _compute_and_store_rrg(key: str, timeframe_seconds: int, window: int, tail: int) -> dict:
    from .rrg import compute_rrg

    value = await asyncio.to_thread(
        compute_rrg,
        engine.settings.research_database_path,
        list(engine.settings.symbols),
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


async def install_fyers_token(access_token: str) -> dict:
    merged = {**desired_settings().model_dump(), "fyers_access_token": access_token}
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
    except Exception as exc:
        raise HTTPException(400, f"Fyers rejected the token: {exc}") from exc
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
            pasted = str((query.get("auth_code") or query.get("code") or [pasted])[0])
        tokens = await engine.broker.exchange_auth_code(pasted)
        return await install_fyers_token(tokens["access_token"])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Fyers authorization failed: {exc}") from exc


@app.post("/api/auth/fyers/access-token", dependencies=[Depends(authorize)])
async def fyers_access_token(payload: AccessTokenInput):
    return await install_fyers_token(payload.access_token)


@app.get("/api/auth/fyers/callback", response_class=HTMLResponse)
async def fyers_callback(auth_code: str | None = Query(default=None), code: str | None = Query(default=None)):
    actual_code = auth_code or code
    if not actual_code:
        return HTMLResponse("<h2>Fyers did not return an authorization code.</h2>", status_code=400)
    try:
        tokens = await engine.broker.exchange_auth_code(actual_code)
        await install_fyers_token(tokens["access_token"])
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
