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
from .desk import AGENT_JOB_LOCK as _agent_lock
from .profile_scale import cached_scale, profile_scale
from .replay import ReplaySession, merge_seed_prints, session_prints
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

@router.get("/api/auction/whale/names", dependencies=[Depends(authorize)])
async def auction_whale_names(day: str | None = None,
                              kind: str | None = Query(default=None, pattern="^(cash|future|option)$"),
                              limit: int = Query(default=100, ge=1, le=500)):
    """Every instrument with a Layer A pattern today, ranked, with its parameters."""
    return await asyncio.to_thread(
        auction_views.whale_names, engine.settings.research_database_path,
        engine.settings.tick_database_path if engine.settings.tick_capture_enabled else None,
        day or _ist_today(), lots=dict(engine.mp.lot_sizes), kind=kind, limit=limit)

# --- whale tuning: per-instrument thresholds and the calibration loop --------

def _history(read_only: bool = True):
    import sqlite3
    path = engine.settings.research_database_path
    return (sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) if read_only
            else sqlite3.connect(path, timeout=60))


def _with_history(fn, read_only: bool = True):
    connection = _history(read_only)
    try:
        return fn(connection)
    finally:
        connection.close()


@router.get("/api/auction/whale/tuning", dependencies=[Depends(authorize)])
async def whale_tuning_overview(runs: int = Query(default=20, ge=1, le=200)):
    """The latest calibration (feature weights, hit rates), run history and parameter population."""
    from . import whale_tuning
    return await asyncio.to_thread(_with_history, lambda h: whale_tuning.overview(h, runs))


@router.get("/api/auction/whale/tuning/params", dependencies=[Depends(authorize)])
async def whale_tuning_params(q: str | None = Query(default=None, max_length=40),
                              limit: int = Query(default=300, ge=1, le=2000)):
    """Every instrument's fitted thresholds, sensitivity, baseline and override."""
    from . import whale_tuning
    return await asyncio.to_thread(_with_history, lambda h: whale_tuning.params_table(h, limit, q))


@router.get("/api/auction/whale/tuning/symbol/{symbol:path}", dependencies=[Depends(authorize)])
async def whale_tuning_symbol(symbol: str):
    """One instrument: fingerprints, baseline, params, override and its labelled events."""
    from . import whale_tuning
    return await asyncio.to_thread(_with_history, lambda h: whale_tuning.symbol_view(h, symbol, _ist_today()))


@router.put("/api/auction/whale/tuning/symbol/{symbol:path}", dependencies=[Depends(authorize)])
async def whale_tuning_override(symbol: str, override: dict | None = None):
    """Pin thresholds for one instrument (keys: whale_tuning.OVERRIDE_KEYS); null clears."""
    from . import whale_tuning
    if override:
        unknown = set(override) - whale_tuning.OVERRIDE_KEYS
        if unknown:
            raise HTTPException(400, f"Unknown override keys: {', '.join(sorted(unknown))}")
    lots = dict(engine.mp.lot_sizes)
    return await asyncio.to_thread(
        _with_history, lambda h: whale_tuning.set_override(h, symbol, override, _ist_today(), lots), False)


_tuning_lock = asyncio.Lock()


@router.get("/api/auction/whale/study", dependencies=[Depends(authorize)])
async def whale_study_latest(limit: int = Query(default=10, ge=1, le=100)):
    """The latest walk-forward edge study (minute-level whale spikes) and its predecessors."""
    from . import whale_study
    return await asyncio.to_thread(_with_history, lambda h: whale_study.latest(h, limit))


@router.post("/api/auction/whale/study/run", dependencies=[Depends(authorize)])
async def whale_study_run(since: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                          until: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                          force: bool = False):
    """Re-run the edge study (default: the last 60 days) -- minutes of CPU, so after the close."""
    from datetime import date, timedelta
    from . import whale_study
    from .sessions import market_open
    if market_open() and not force:
        raise HTTPException(409, "The study reads every instrument's minute history; run it after the close")
    if _tuning_lock.locked():
        raise HTTPException(409, "A tuning or study run is already in progress")
    end = until or _ist_today()
    start = since or (date.fromisoformat(end) - timedelta(days=60)).isoformat()
    async with _tuning_lock:
        return await asyncio.to_thread(
            _with_history, lambda h: whale_study.run_study(h, start, end), False)


@router.post("/api/auction/whale/tuning/run", dependencies=[Depends(authorize)])
async def whale_tuning_run(day: str | None = None, force: bool = False):
    """Run the loop now: rescan the day with tuned params, fingerprint, label, calibrate, refit."""
    import sqlite3
    from . import whale_tuning
    from .sessions import market_open
    if market_open() and not force:
        raise HTTPException(409, "The tuning run rescans the whole session and replaces its events; "
                                 "it runs after the close (pass force=true to run anyway)")
    if _tuning_lock.locked():
        raise HTTPException(409, "A tuning run is already in progress")
    chosen = day or engine.mp.session_day or _ist_today()
    lots = dict(engine.mp.lot_sizes)

    def job() -> dict:
        ticks = sqlite3.connect(f"file:{engine.settings.tick_database_path}?mode=ro", uri=True, timeout=30)
        history = _history(read_only=False)
        try:
            return whale_tuning.run(ticks, history, chosen, lots=lots,
                                    whale_symbols=engine.whale_symbols() if hasattr(engine, "whale_symbols") else None)
        finally:
            ticks.close()
            history.close()

    async with _tuning_lock:
        return await asyncio.to_thread(job)


# --- option agent: 14:45 CE/PE picks for the next 1-3 sessions, and its loop ---

def _agent_lots() -> dict[str, int]:
    from .agent_equity import known_lots
    return known_lots(dict(engine.mp.lot_sizes), getattr(engine.settings, "contract_snapshot_path", None))


@router.get("/api/agent/picks", dependencies=[Depends(authorize)])
async def agent_picks(day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                      run_id: int | None = Query(default=None, ge=1)):
    """One emission (``run_id``, else ``day``'s latest, else the newest): calls, puts, index trades, outcomes."""
    from . import option_agent
    lots = _agent_lots()
    return await asyncio.to_thread(_with_history, lambda h: option_agent.picks_view(h, day, lots, run_id), False)


@router.get("/api/agent/runs", dependencies=[Depends(authorize)])
async def agent_runs(days: int = Query(default=30, ge=1, le=365)):
    """Every emission: day, model, how far into its 1-3 session hold, and how its picks have done."""
    from . import option_agent
    return await asyncio.to_thread(_with_history, lambda h: option_agent.runs_view(h, days), False)


@router.get("/api/agent/compare", dependencies=[Depends(authorize)])
async def agent_compare(run_id: int | None = Query(default=None, ge=1),
                        horizon: str = Query(default="y", pattern="^(y|r1|r2|r3|live)$")):
    """One list against the actual top 10 performers among the same day's candidates (per side, and index trades)."""
    from . import option_agent
    return await asyncio.to_thread(_with_history, lambda h: option_agent.compare_view(h, run_id, horizon), False)


@router.get("/api/agent/compare/summary", dependencies=[Depends(authorize)])
async def agent_compare_summary(days: int = Query(default=60, ge=1, le=365),
                                horizon: str = Query(default="y", pattern="^(y|r1|r2|r3|live)$")):
    """Every list's overlap with the actual top 10, the picks' percentile and capture, and their averages."""
    from . import option_agent
    return await asyncio.to_thread(_with_history, lambda h: option_agent.compare_summary(h, days, horizon), False)


@router.get("/api/agent/active", dependencies=[Depends(authorize)])
async def agent_active():
    """Every pick still inside its hold, across the lists that are still open."""
    from . import option_agent
    lots = _agent_lots()
    return await asyncio.to_thread(_with_history, lambda h: option_agent.active_view(h, lots), False)


@router.get("/api/agent/performance", dependencies=[Depends(authorize)])
async def agent_performance(days: int = Query(default=30, ge=1, le=365)):
    """Every emission's picks against their day's full candidate set, and the basket curve."""
    from . import option_agent
    return await asyncio.to_thread(_with_history, lambda h: option_agent.performance(h, days), False)


@router.get("/api/agent/equity", dependencies=[Depends(authorize)])
async def agent_equity(days: int = Query(default=45, ge=1, le=365),
                       notional: float = Query(default=10_000, ge=1_000, le=10_000_000)):
    """Rupee equity curves for every book (top-10 CE/PE, index trades) against their benchmarks."""
    from datetime import date, timedelta
    from . import agent_equity as equity
    since = (date.fromisoformat(_ist_today()) - timedelta(days=days)).isoformat()
    lots = _agent_lots()
    return await asyncio.to_thread(_with_history, lambda h: equity.books(h, since, lots, notional), False)


@router.get("/api/agent/models", dependencies=[Depends(authorize)])
async def agent_models(limit: int = Query(default=30, ge=1, le=200)):
    """Model versions (champion, challengers, retired) with walk-forward metrics and weights."""
    from . import option_agent
    return await asyncio.to_thread(_with_history, lambda h: option_agent.models_view(h, limit), False)


@router.get("/api/agent/status", dependencies=[Depends(authorize)])
async def agent_status():
    status = getattr(engine, "agent_status", None) or {}
    from . import option_agent
    from .desk import AGENT_EMIT_AT, AGENT_PREPARE_AT
    return {**status, "emit_at": ":".join(f"{part:02d}" for part in AGENT_EMIT_AT),
            "prepare_at": ":".join(f"{part:02d}" for part in AGENT_PREPARE_AT[:2]),
            "as_of": option_agent.AS_OF.strftime("%H:%M"),
            "busy": _agent_lock.locked()}


@router.post("/api/agent/run", dependencies=[Depends(authorize)])
async def agent_run(day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Emit with the champion from complete, fresh bars at the 14:45 feature cutoff."""
    from . import option_agent
    lots = _agent_lots()
    if _agent_lock.locked():
        raise HTTPException(409, "The agent is busy")
    async with _agent_lock:
        result = await asyncio.to_thread(
            _with_history, lambda h: option_agent.emit(h, day, source="manual", lots=lots), False)
        if result.get("status") == "cutoff_not_reached":
            raise HTTPException(409, f"The 14:45 IST feature cutoff has not closed for {result['day']}. Emit after that cutoff.")
        return result


@router.post("/api/agent/train", dependencies=[Depends(authorize)])
async def agent_train(force: bool = False):
    """Label what has closed and re-judge every configuration walk-forward."""
    from . import option_agent
    from .sessions import market_open
    if market_open() and not force:
        raise HTTPException(409, "Training reads weeks of option bars; it runs after the close (force=true to run anyway)")
    if _agent_lock.locked():
        raise HTTPException(409, "The agent is busy")
    async with _agent_lock:
        return await asyncio.to_thread(_with_history, option_agent.learn, False)


@router.post("/api/agent/bootstrap", dependencies=[Depends(authorize)])
async def agent_bootstrap(since: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
                          until: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                          force: bool = False):
    """Rebuild past candidates and labels from stored minute bars, then train."""
    from . import option_agent
    from .sessions import market_open
    if market_open() and not force:
        raise HTTPException(409, "The bootstrap reads every option bar in the range; run it after the close")
    if _agent_lock.locked():
        raise HTTPException(409, "The agent is busy")

    def job(history):
        built = option_agent.bootstrap(history, since, until or _ist_today())
        return {"bootstrap": built, "train": option_agent.train(history, note="bootstrap")}

    async with _agent_lock:
        return await asyncio.to_thread(_with_history, job, False)


@router.get("/api/auction/whale/performance", dependencies=[Depends(authorize)])
async def auction_whale_performance(days: int = Query(default=10, ge=1, le=60),
                                    kind: str | None = Query(default=None, pattern="^(cash|future|option)$")):
    """Every whale-list entry in the last ``days`` calendar days with its max/min return since entry."""
    from datetime import date, timedelta
    until = _ist_today()
    since = (date.fromisoformat(until) - timedelta(days=days)).isoformat()
    return await asyncio.to_thread(
        auction_views.whale_performance, engine.settings.research_database_path, since, until, kind)

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

async def _scale_for(symbol: str, profile) -> dict:
    """The ATR row scale for a live profile; SQLite only on the first read of the day."""
    path = engine.settings.research_database_path
    cached = cached_scale(path, symbol, profile.day, profile.tick_size)
    if cached is not None:
        return cached
    span = (profile.high - profile.low) if profile.high is not None and profile.low is not None else None
    return await asyncio.to_thread(profile_scale, path, symbol, profile.day, profile.tick_size, span)


async def _profile_snapshot(symbol: str, profile, max_levels: int | None) -> dict:
    scale = await _scale_for(symbol, profile)
    snapshot = profile.snapshot(max_levels=max_levels, row_size=scale["row_size"], scale=scale)
    # A cash equity's closing-auction match, kept out of the profile's volume
    # and the flow because it has no aggressor; shown beside them instead.
    snapshot["closing_auction"] = engine.mp.closing_auctions.get(symbol)
    return snapshot


@router.get("/api/mp/snapshot", dependencies=[Depends(authorize)])
async def mp_snapshot(symbol: str | None = Query(default=None)):
    """Full desk state: profile, order flow, book, current setup."""
    result = engine.mp.snapshot(symbol)
    focus = result.get("focus")
    profile = engine.mp.profiles.get(focus) if focus else None
    if profile is not None and (result.get("profile") or {}).get("row_scale") is None:
        # First read of this symbol today: compute its ATR rows off the loop,
        # then publish the same rows as the auction ladder and the footprint.
        result["profile"] = await _profile_snapshot(focus, profile, 90)
    return result

_annotation_cache: dict = {}


@router.get("/api/mp/annotations/{symbol:path}", dependencies=[Depends(authorize)])
async def mp_annotations_view(symbol: str):
    """Evidence-labelled profile notes and the current period vs its baseline (mp_annotations.py)."""
    from . import mp_annotations
    state = engine.mp.flow.states.get(symbol)
    flow_minutes = [list(m) for m in getattr(state, "minutes", [])] if state is not None else None
    return await asyncio.to_thread(mp_annotations.annotate, engine.mp.history_database_path, symbol, None,
                                   flow_minutes, _annotation_cache)


@router.get("/api/mp/profile/{symbol:path}", dependencies=[Depends(authorize)])
async def mp_profile(symbol: str, full: bool = Query(default=False),
                     profile_only: bool = Query(default=False)):
    profile = engine.mp.profiles.get(symbol)
    if profile is None:
        raise HTTPException(404, "No profile has formed for that symbol today")
    # The polled desk snapshot is deliberately small. A focused auction ladder
    # needs every traded price row, so fetch it explicitly while that view is
    # open instead of thinning away volume and TPO structure.
    result = {"profile": await _profile_snapshot(symbol, profile, None if full else 90)}
    if not profile_only:
        result["flow"] = engine.mp.flow.snapshot(symbol)
    return result

_backfill_tried: dict[tuple[str, str], str] = {}


async def _backfill_if_short(symbol: str, day: str, series: dict) -> bool:
    """Download minute bars when the past profiles lack data; once per symbol a day.

    Short means no stored session in the previous month or fewer than five
    before today. The engine (the only Fyers client) fetches and stores them;
    the caller rebuilds. A failure is reported in the series notes, not raised.
    """
    from datetime import date, timedelta
    from .profile_series import DAY_COLUMNS

    today = date.fromisoformat(day)
    previous_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    have_month = any(column["kind"] == "month" for column in series["columns"])
    if have_month and series.get("stored_sessions", 0) >= DAY_COLUMNS:
        return False
    key = (symbol, day)
    if key in _backfill_tried:
        if _backfill_tried[key]:
            series.setdefault("notes", []).append(_backfill_tried[key])
        return False
    try:
        strategy = getattr(engine, "strategy", None)
        if strategy is not None:            # desk process: ask the engine
            outcome = await strategy.backfill_minutes(symbol, previous_month.isoformat())
            stored = outcome.get("rows", 0)
        else:                               # single process: this is the engine
            from datetime import datetime
            from zoneinfo import ZoneInfo
            from .candle_store import store_historical_candles
            ist = ZoneInfo("Asia/Kolkata")
            rows = await engine.broker.history_range(
                symbol, 60, datetime.combine(previous_month, datetime.min.time(), ist), datetime.now(ist))
            stored = await asyncio.to_thread(
                store_historical_candles, engine.settings.research_database_path, rows)
        _backfill_tried[key] = "" if stored else "Fyers returned no older minute history for this contract"
        return bool(stored)
    except Exception as exc:  # noqa: BLE001 -- the series still renders what it has
        _backfill_tried[key] = f"history backfill failed: {str(exc)[:160]}"
        series.setdefault("notes", []).append(_backfill_tried[key])
        return False


@router.get("/api/mp/profile-series/{symbol:path}", dependencies=[Depends(authorize)])
async def mp_profile_series(symbol: str, days: int = Query(default=5, ge=1, le=10)):
    """Previous month, earlier weeks, this week, the last sessions and today on one row grid."""
    import sqlite3
    from .profile_series import build_series

    profile = engine.mp.profiles.get(symbol)
    day = engine.mp.session_day or _ist_today()
    if profile is not None:
        scale = await _scale_for(symbol, profile)
        live = profile.snapshot(max_levels=None, row_size=scale["row_size"], scale=scale)
    else:
        scale = await asyncio.to_thread(
            profile_scale, engine.settings.research_database_path, symbol, day, 0.05)
        live = None

    def build() -> dict:
        history = sqlite3.connect(f"file:{engine.settings.research_database_path}?mode=ro", uri=True, timeout=30)
        ticks = None
        try:
            if engine.settings.tick_capture_enabled:
                try:
                    ticks = sqlite3.connect(f"file:{engine.settings.tick_database_path}?mode=ro", uri=True, timeout=30)
                except sqlite3.OperationalError:
                    ticks = None
            return build_series(ticks, history, symbol, day, scale["row_size"], live, days)
        finally:
            history.close()
            if ticks is not None:
                ticks.close()

    result = await asyncio.to_thread(build)
    if await _backfill_if_short(symbol, day, result):
        result = await asyncio.to_thread(build)
    result["row_scale"] = scale
    result["last"] = profile.last_price if profile is not None else None
    result["closing_auction"] = engine.mp.closing_auctions.get(symbol)
    return result

@router.get("/api/mp/leaderboard", dependencies=[Depends(authorize)])
async def mp_leaderboard(limit: int = Query(default=25, ge=1, le=200)):
    """Symbols ranked by how constructive their auction reads right now."""
    return engine.mp.leaderboard(limit)

_seed_locks: dict[str, asyncio.Lock] = {}


async def _watch_from_open(symbol: str) -> None:
    """Start detailed capture seeded with the whole session so far.

    The book only captures symbols a chart has asked for, and it used to seed
    from the flow tracker's last 400 prints: a liquid contract opened at 15:29
    began at 15:05. The session's raw ticks are on disk, so classify them off
    the loop with the desk's own tracker and replay those prints instead,
    joined to the newest ones the store has not flushed yet.
    Both CVD legs are still anchored on the tracker's session figures.
    """
    book = engine.mp.footprints
    lock = _seed_locks.setdefault(symbol, asyncio.Lock())
    async with lock:
        if book.watching(symbol):  # a concurrent poll seeded it first
            return
        day = engine.mp.session_day or _ist_today()
        try:
            stored = await asyncio.to_thread(
                session_prints, engine.settings.tick_database_path, symbol, day)
        except Exception:  # noqa: BLE001 -- fall back to the tracker's prints
            stored = []
        seed = engine.mp.flow.states.get(symbol)
        # Only the instrument's own session: a tracker restored from a
        # checkpoint can still hold a cash closing-auction uncross.
        from datetime import UTC, datetime as _dt
        from .sessions import in_session
        recent = [row for row in (seed.recent if seed else [])
                  if in_session(symbol, _dt.fromtimestamp(row.timestamp, UTC))]
        prints = merge_seed_prints(stored, recent)
        book.watch(symbol, seed_prints=prints or None,
                   session_delta=seed.cumulative_delta if seed else None,
                   session_weighted_delta=seed.weighted_delta if seed else None)


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
    if fresh:
        await _watch_from_open(symbol)
    else:
        book.watch(symbol)  # refresh its place in the LRU
    flow = engine.mp.flow.snapshot(symbol)
    payload = book.payload(symbol, bars, timeframe_seconds=timeframe_seconds, flow=flow)
    payload["subscribed_now"] = fresh
    profile = engine.mp.profiles.get(symbol)
    # ONE profile snapshot, handed to both readers: it is the most expensive
    # thing on this route and session_view used to take a second one.
    snapshot = await _profile_snapshot(symbol, profile, None if profile_full else 90) if profile else None
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
        scale = await asyncio.to_thread(
            profile_scale, engine.settings.research_database_path, symbol, day, 0.05)
        payload = await asyncio.to_thread(
            session.payload, at or session.session_start, bars, timeframe_seconds, context, scale)
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
