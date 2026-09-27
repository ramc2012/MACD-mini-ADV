"""The Market Profile / order-flow desk's HTTP routes: /api/mp/* and /api/auction/*.

Mounted by the single-process app (role "all") and by the desk process (role
"desk"); the strategy process does not serve them -- the gateway routes these
paths to the desk. ``bind`` supplies the engine the routes read (a
TradingEngine or a DeskEngine; both expose the same desk surface) and how an
MP settings change is persisted.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import auction_views
from .api_models import OrderInput, _as_json
from .replay import ReplaySession
from .universe import desk_underlying

router = APIRouter()
engine = None  # bound by the app that mounts these routes
_persist_mp_settings: Callable[[dict], Awaitable[None]] | None = None


def bind(bound_engine, persist_mp_settings: Callable[[dict], Awaitable[None]]) -> None:
    global engine, _persist_mp_settings
    engine = bound_engine
    _persist_mp_settings = persist_mp_settings


def authorize(x_macd_token: str = Header(default="")) -> None:
    if engine.settings.api_token and x_macd_token != engine.settings.api_token:
        raise HTTPException(401, "Invalid API token")


def _ist_today() -> str:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()

@router.get("/api/auction/symbols", dependencies=[Depends(authorize)])
async def auction_symbols():
    """Underlyings with a stored session profile."""
    return await asyncio.to_thread(
        auction_views.symbols, engine.settings.research_database_path)

@router.get("/api/auction/context/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_context(symbol: str, as_of: str | None = None):
    """Prior day, week and month value areas, naked POCs and where price sits."""
    tick = engine.latest_ticks.get(symbol)
    return await asyncio.to_thread(
        auction_views.context, engine.settings.research_database_path, symbol,
        as_of or _ist_today(), tick.ltp if tick else None)

@router.get("/api/auction/sessions/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_sessions(symbol: str, limit: int = Query(default=120, ge=1, le=1000)):
    payload = await asyncio.to_thread(
        auction_views.sessions, engine.settings.research_database_path, symbol, limit)
    periods = await asyncio.to_thread(
        auction_views.periods, engine.settings.research_database_path, symbol)
    return {"symbol": symbol, "sessions": payload, "periods": periods}

@router.get("/api/auction/base-rates/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_base_rates(symbol: str, as_of: str | None = None):
    return await asyncio.to_thread(
        auction_views.base_rates, engine.settings.research_database_path, symbol, as_of)

@router.get("/api/auction/flow/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_flow(symbol: str, day: str | None = None):
    """Per-minute OFI against inferred delta for one recorded session."""
    days = await asyncio.to_thread(
        auction_views.flow_days, engine.settings.tick_database_path, symbol)
    chosen = day or (days[0] if days else _ist_today())
    payload = await asyncio.to_thread(
        auction_views.flow, engine.settings.tick_database_path, symbol, chosen)
    return {**payload, "available_days": days}

@router.get("/api/auction/setups/{symbol:path}", dependencies=[Depends(authorize)])
async def auction_setups(symbol: str, regime_id: str | None = None):
    """The Part 7 validation journal: context / trigger / outcome rates per setup."""
    return await asyncio.to_thread(
        auction_views.setup_journal, engine.settings.research_database_path, symbol, regime_id)

@router.get("/api/auction/whale", dependencies=[Depends(authorize)])
async def auction_whale(day: str | None = None, symbol: str | None = None):
    """Layer A events, decayed aggression per symbol, and the latest chain window."""
    return await asyncio.to_thread(
        auction_views.whale, engine.settings.research_database_path, day or _ist_today(), symbol)

@router.get("/api/auction/whale/windows/{underlying}", dependencies=[Depends(authorize)])
async def auction_whale_windows(underlying: str, day: str | None = None):
    """The day's per-window series for one root: composite, option vs futures delta-notional, divergence."""
    return await asyncio.to_thread(
        auction_views.whale_windows, engine.settings.research_database_path,
        underlying.upper(), day or _ist_today())

@router.get("/api/auction/whale/alerts", dependencies=[Depends(authorize)])
async def auction_whale_alerts(day: str | None = None,
                               limit: int = Query(default=50, ge=1, le=500)):
    """Composite alerts with their 15/30/60-minute 'what happened next' records and the 20-session hit rate."""
    return await asyncio.to_thread(
        auction_views.whale_alerts, engine.settings.research_database_path,
        day or _ist_today(), limit)

@router.get("/api/auction/positional", dependencies=[Depends(authorize)])
async def auction_positional(day: str | None = None,
                             limit: int = Query(default=200, ge=1, le=2000)):
    return await asyncio.to_thread(
        auction_views.positional, engine.settings.research_database_path, day, limit)

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

@router.get("/api/mp/snapshot", dependencies=[Depends(authorize)])
async def mp_snapshot(symbol: str | None = Query(default=None)):
    """Full desk state: profile, order flow, book, current setup."""
    return engine.mp.snapshot(symbol)

@router.get("/api/mp/profile/{symbol:path}", dependencies=[Depends(authorize)])
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

@router.get("/api/mp/leaderboard", dependencies=[Depends(authorize)])
async def mp_leaderboard(limit: int = Query(default=25, ge=1, le=200)):
    """Symbols ranked by how constructive their auction reads right now."""
    return engine.mp.leaderboard(limit)

@router.get("/api/mp/footprint/{symbol:path}", dependencies=[Depends(authorize)])
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

@router.get("/api/mp/replay-days/{symbol:path}", dependencies=[Depends(authorize)])
async def mp_replay_days(symbol: str):
    """IST sessions whose RAW ticks are still on disk, newest first.

    Only these can be replayed print by print; older sessions were condensed to
    minute flow and a ladder, which carry no individual prints.
    """
    return await asyncio.to_thread(
        auction_views.raw_tick_days, engine.settings.tick_database_path, symbol)

@router.get("/api/mp/replay/{symbol:path}", dependencies=[Depends(authorize)])
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

@router.get("/api/mp/statistics", dependencies=[Depends(authorize)])
async def mp_statistics():
    return engine.mp.statistics()

@router.get("/api/mp/book", dependencies=[Depends(authorize)])
async def mp_book():
    return {
        "portfolio": engine.mp.portfolio.snapshot(),
        "orders": engine.mp.repository.rows("orders", 2000),
        "trades": engine.mp.repository.rows("trades", 2000),
        "signals": engine.mp.repository.rows("signals", 500),
        "equity": engine.mp.repository.equity_rows(5000),
        "closed_positions": engine.mp.closed_view(),
    }

@router.put("/api/mp/settings", dependencies=[Depends(authorize)])
async def mp_settings(payload: MPSettingsInput):
    updates = payload.model_dump(exclude_none=True)
    risk_keys = {"hard_stop_pct", "trail_activation_pct", "trail_pct"}
    if engine.mp.portfolio.positions and risk_keys.intersection(updates):
        raise HTTPException(409, "Close MP positions before changing active stop parameters")
    for key, value in updates.items():
        setattr(engine.mp.settings, key, value)
    config_updates = {f"mp_{key}": value for key, value in updates.items()}
    # The strategy process owns settings.json; in the desk process this sends
    # the change there, in the single process it saves it directly.
    await _persist_mp_settings(config_updates)
    return engine.mp.settings.to_dict()

@router.post("/api/mp/orders", dependencies=[Depends(authorize)])
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
