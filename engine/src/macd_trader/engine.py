from __future__ import annotations

import asyncio
import copy
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import suppress
import json
import math
from dataclasses import asdict
from pathlib import Path
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .rollover import set_stock_exit_sessions
from .brokers import Broker, create_broker
from .candle_store import (
    LiveCandleWriter, prior_session_candle_count, prune_history, resolve_expiry, store_historical_candles,
)
from .tick_store import TickStore
from .regimes import regime_for
from .rollover import days_to_expiry, first_tradable, is_futures_symbol, Expiry, is_stock_option, stock_exit_due, stock_last_day
from .sessions import in_session, market_close, market_open, segment
from .chain_warmup import chain_candles
from .candles import CandleAggregator
from .chart_history import aggregate_session_candles, load_chart_history
from .config import Settings
from .contracts import ContractSelector
from .events import EventHub, json_value
from .greeks import contract_gex
from .execution import ExecutionManager, ExitRetryWait
from .models import Candle, Tick, Trade
from . import market_calendar
from .blast_engine import BlastEngine
from .smart_lane import SmartLanes
from .agentic_lane import AgenticLane
from .spot_lane import SpotLane
from .agentic_telegram import TelegramBridge
from .extreme_booking import ExtremeBooker, SPOT_TO_ROOT as EXTREME_SPOTS
from .option_greeks import book_greeks
from .entry_context import EntryFlow, is_underlying
from .entry_grade import EntryGrader
from . import self_heal
from .desk import (  # noqa: F401 - re-exported; tests and callers read them here
    NIGHTLY_MAX_ATTEMPTS, QUOTES_BACKOFF_MAX_SECONDS, QUOTES_BACKOFF_SECONDS,
    TICK_MAINTENANCE_INTERVAL_SECONDS, DeskMixin, desk_settings,
)
from .mp_engine import MPEngine
from .portfolio import Portfolio, breakeven_exit_price
from .dispersion import LIVE as DISPERSION_LIVE, latest_breadth, record as record_dispersion
from . import whale
from .bus import TickBusPublisher
from .ratio_history import build_ratio_history
from .repository import TradeRepository
from .strategy import MACDStrategyManager
from .sectors import sector_of


# 20,000 one-minute rows cover roughly 50 trading days and leave ample
# indicator warm-up for the 500 aggregated bars retained by the live engine.
LIVE_WARMUP_SOURCE_ROWS = 20_000
# Fyers permits bounded minute-history requests.  Ninety calendar days is
# enough to supply the retained live-bar window for a newly listed contract.
NEW_CONTRACT_HISTORY_DAYS = 90
# Strategy bars a stale or empty symbol is refilled with at connect.
WARMUP_HISTORY_BARS = 150
# Pause between background history requests for new contracts (see
# Engine._backfill_contract_history): well under the per-minute limit.
HISTORY_BACKFILL_PACE_SECONDS = 1.0
# The ratio backfill runs off a browser poll, so a leg whose download fails
# would re-request on every refresh. Fyers rate-limits per endpoint — the desk
# collected 429s on /data/quotes on 4 Sep — so a failed attempt stands that leg
# down. A strike listed today cannot grow before-session history until
# tomorrow, and it shows up either as an empty range or — more often — as a
# successful download that carries only today's bars; both wait far longer
# than a transient failure does.
RATIO_BACKFILL_RETRY_SECONDS = 300.0
RATIO_BACKFILL_EMPTY_RETRY_SECONDS = 3_600.0
# Warm-up concurrency is bounded inside warm() by two lanes: stored-history loads and broker downloads.
# What rate-limited a restart into 138 dead contracts was the RETRY policy:
# three attempts 0.5s apart all land inside the same per-minute window.
WARMUP_ATTEMPTS = 5
# Stored-history loads are ~0.02 s of SQLite and aggregation; broker downloads are seconds each. Separate lanes
# so cheap loads never queue behind downloads. Loads run ONE at a time: measured on 16 Oct 2026, 666 loads took
# 18 s serially and 88 s six at a time (SQLite releasing the GIL while the aggregation competes for it -- a
# convoy). The download lane is network-bound, sized for the 2026-10-16 data-API limits and paced, with the
# existing 429 back-off on top.
WARMUP_LOAD_CONCURRENCY = 1
WARMUP_DOWNLOAD_CONCURRENCY = 4
WARMUP_DOWNLOAD_PACING_SECONDS = 0.2
# Seconds to wait after a 429, per attempt. Fyers' history window is per-minute,
# so the tail has to clear a minute to be worth anything.
WARMUP_RATE_LIMIT_BACKOFF = (4.0, 12.0, 30.0, 65.0)
# No tick for this long during the session means the socket is gone, however
# healthy the last connect() looked.
# The ITM/OTM ratio ladder is ~850 of the 1302 subscribed contracts and, on
# 2026-09-01, 6.5M of 15.6M inbound ticks. Every one was serialised and fanned
# out to every browser at 376 bytes a frame — about 2.5 GB per client per day.
# The ratio chart reads those legs from stored minute bars, never from live
# ticks; only the CE/PE watch tabs show their LTP, and a once-a-second LTP is
# indistinguishable there.
ANALYSIS_TICK_PUBLISH_SECONDS = 1.0
FEED_STALE_SECONDS = 120
GREEKS_CACHE_SECONDS = 5.0
# Once a day, outside the session and the pre-open, after this IST hour.
HISTORY_PRUNE_AFTER_HOUR = 16
# A print stamped this far past our own clock is treated as unusable: it would
# otherwise become the "newest" quote and veto every real one behind it. A few
# seconds of NTP error is normal; a Docker VM that slept can lag by minutes, and
# that must be reported rather than silently starve the paper book of quotes.
FUTURE_TICK_TOLERANCE_SECONDS = 30
CLOCK_SKEW_REPORT_SECONDS = 60
FEED_RECOVERY_COOLDOWN = 180
# Every step of a socket restart has to be bounded, because two of them cannot
# be interrupted: the SDK's connect and close both run under asyncio.to_thread,
# which ignores cancellation. On 8 Sep the 09:05 pre-open refresh cancelled the
# stream, called close() on a socket whose reconnect thread was mid-flight, and
# never came back -- the last tick was 09:05:01, the market opened ten minutes
# later on a dead feed, and the desk took no trade all day. A step that overruns
# leaks its thread, which is a far smaller cost than wedging the desk.
FEED_STEP_TIMEOUT = 25.0
FEED_WATCHDOG_INTERVAL = 30.0
# The socket is opened whenever the process starts — often two hours before
# the bell — and then sits idle. The Fyers SDK burns its five reconnects
# silently during that idle window, so a socket that still reports "connected"
# can be dead on arrival at 09:15. On 19 Aug that cost the first 29 minutes of
# the session: 546 of 667 contracts recorded their first bar at 09:44. Force a
# fresh socket shortly before the open, and start watching from the pre-open.
PREOPEN_WATCH_START = (9, 0)
PREOPEN_REFRESH_AT = (9, 5)
# A signal is only actionable if the bar it was computed on is still the
# current one. This MUST scale with the timeframe: the strategy evaluates the
# FORMING bar and fires once, at whatever moment inside that bar the condition
# first becomes true, so a legitimate signal is up to one full period "old" by
# construction. A flat 180s constant silently restricted signal generation to
# the first three minutes of every 30-minute bar — a ~90% suppression window —
# and the 19 Aug signals it was written for turned out to be normal intrabar
# evaluations of 1800s bars, not stale state. Only a bar from a PREVIOUS period
# (a real outage) is stale.
SIGNAL_STALENESS_GRACE_SECONDS = 120
IST = ZoneInfo("Asia/Kolkata")


def _drop_expired(symbols: list[str], known_expiries: dict[str, str], today) -> list[str]:
    """Remove option contracts whose expiry has passed from a subscription.

    FYERS rejects an expired contract at subscribe time. On 5 Oct a SENSEX
    weekly that expired on 1 Oct, still held by the blast book, was the one bad
    symbol in 1,610. Futures, spots and anything unparseable are kept.
    """
    from .option_agent import parse_option

    kept = []
    for symbol in symbols:
        expiry = known_expiries.get(symbol)
        if expiry is None and symbol.endswith(("CE", "PE")):
            parsed = parse_option(symbol)
            expiry = parsed["expiry"] if parsed else None
        if expiry is not None and str(expiry)[:10] < today.isoformat():
            continue
        kept.append(symbol)
    return kept


def minute_closed_moment(candle) -> datetime:
    """The IST wall-clock moment a closed candle belongs to."""
    return datetime.fromtimestamp(candle.timestamp, UTC).astimezone(IST)


def regular_session_open(moment: datetime | None = None) -> bool:
    """True while any segment is in its continuous session on a trading day.

    The market-wide question (maintenance, feed watchdog, health). Whether one
    instrument's print belongs to the session is sessions.in_session: F&O runs
    to 15:40 and cash stops at 15:15 for its closing auction since Aug 2026.
    """
    return market_open(moment)


def fo_session_open(moment: datetime | None = None) -> bool:
    """True through the derivatives session as the regime defines it.

    The cash gate above stops at 15:30, but since 3 Aug 2026 F&O trades to
    15:40, and those ten minutes are where expiry-day unwinds and closing-
    auction hedges land. A chain snapshotted on the cash clock would record a
    "close" OI that is ten minutes stale.
    """
    local = (moment or datetime.now(IST)).astimezone(IST)
    hour, minute = (int(part) for part in regime_for(local.date()).session_end.split(":"))
    return market_calendar.is_trading_day(local.date()) and (9, 15) <= (local.hour, local.minute) <= (hour, minute)


def session_open_epoch(moment: datetime | None = None) -> int:
    """Epoch seconds of today's 09:15 IST open.

    Rows older than this instant can only have come from a broker download or
    an earlier session; everything at or after it is what the live writer has
    stored today.
    """
    local = (moment or datetime.now(IST)).astimezone(IST)
    return int(local.replace(hour=9, minute=15, second=0, microsecond=0).timestamp())


def preopen_window(moment: datetime | None = None) -> bool:
    """True during the NSE pre-open, when feed faults must surface early."""
    local = (moment or datetime.now(IST)).astimezone(IST)
    return market_calendar.is_trading_day(local.date()) and PREOPEN_WATCH_START <= (local.hour, local.minute) < (9, 15)



def _smart_lanes(engine) -> list:
    """The engine's smart lanes, or none -- tests drive engine methods on bare stand-ins."""
    lanes = getattr(engine, "smart_lanes", None)
    agentic = getattr(engine, "agentic", None)
    spot = getattr(engine, "spot_lane", None)
    # The agentic lane (agentic_lane.py) and the spot MACD lane (spot_lane.py) wear the same surface, so ticks,
    # subscriptions, settlement and sweeps reach them through every loop below; the /api/smart_lanes routes list
    # smart_lanes.lanes only.
    return [*(lanes.lanes if lanes is not None else []), *([agentic] if agentic is not None else []),
            *([spot] if spot is not None else [])]


def _held_contract_lots(engine) -> dict[str, int]:
    """Retention covers every book without expanding its entry universe."""
    held = {s: p.lot_size for s, p in engine.portfolio.positions.items() if p.quantity > 0}
    held.update(engine.desk_holdings())
    blast = getattr(engine, "blast", None)
    if blast is not None:
        held.update({s: p.lot_size for s, p in blast.portfolio.positions.items() if p.quantity > 0})
    for lane in _smart_lanes(engine):
        if getattr(lane, "kind", None) == "spot":
            continue           # shares, not contracts: nothing for the option master or the selector to retain
        held.update({s: p.lot_size for s, p in lane.portfolio.positions.items() if p.quantity > 0})
    return held

class TradingEngine(DeskMixin):
    def __init__(self, settings: Settings):
        set_stock_exit_sessions(settings.stock_option_exit_sessions)   # T-2 for stock options (rollover.py)
        self.settings = settings
        self.calendar_rejected = market_calendar.configure(settings.market_holidays_csv)
        self.events = EventHub()
        self.broker: Broker = create_broker(settings)
        self.repository = TradeRepository(settings.database_path)
        self.portfolio = Portfolio(settings.initial_capital)
        self.execution = ExecutionManager(settings, self.broker, self.portfolio, self.repository, self.events)
        self.strategy = MACDStrategyManager(settings, self.events)
        self.candles = CandleAggregator(settings.timeframe_seconds)
        self.history: dict[str, deque[Candle]] = defaultdict(lambda: deque(maxlen=500))
        self.history_errors: dict[str, str] = {}
        self.warmup_report: dict = {}
        self.connect_steps: dict[str, float] = {}
        self.history_backfill: dict = {}
        self._history_backfill_task: asyncio.Task | None = None
        self._warming_symbols: set[str] = set()
        self._warm_live_candles: dict[str, list[Candle]] = defaultdict(list)
        self.position_mark_errors: dict[str, str] = {}
        self.latest_ticks: dict[str, Tick] = {}
        self.future_ticks_ignored = 0
        self._last_future_tick: tuple[float, float] | None = None  # (monotonic, skew seconds)
        self.status = "stopped"
        self.error: str | None = None
        self._stream_task: asyncio.Task | None = None
        self._startup_task: asyncio.Task | None = None
        self._tick_maintenance_task: asyncio.Task | None = None
        self._history_prune_task: asyncio.Task | None = None
        self.history_prune: dict = {"day": None, "result": None, "error": None}
        # symbol -> (monotonic time, inputs, result): the snapshot recomputed
        # every contract's implied vol (~25 us each, ~32 ms for the ladder).
        self._greeks_cache: dict[str, tuple[float, tuple, dict]] = {}
        self._rollover_task: asyncio.Task | None = None
        self._session_close_task: asyncio.Task | None = None
        self._flushed_session: str | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._expiry_flatten_task: asyncio.Task | None = None
        self._dispersion_task: asyncio.Task | None = None
        self._nightly_task: asyncio.Task | None = None
        self._chain_task: asyncio.Task | None = None
        self._stock_chain_task: asyncio.Task | None = None
        self._nightly_done: str | None = None
        self._nightly_attempts = 0
        self.nightly_report: dict | None = None
        self.chain_status: dict = {"day": None, "snapshots": 0, "last_at": None, "error": None,
                                   "windows": 0, "alerts_today": 0, "history_days": 0,
                                   "composite": {}, "whale_error": None}
        # The futures leg of the whale tracker: a live book-OFI per desk
        # future (nothing else in the process keeps one) sampled once a minute
        # beside the desk's own classified flow.
        self.whale_live = whale.LiveFlow()
        # Composite alerts the minute loop raised, drained by the AlertManager
        # so Telegram delivery never sits on the chain loop.
        self.whale_alert_queue: list[dict] = []
        self._dispersion_bar: tuple[int, int, int] | None = None
        self._dispersion_error: str | None = None
        self._analysis_published: dict[str, float] = {}
        self._quotes_blocked_until = 0.0
        self._quotes_backoff = QUOTES_BACKOFF_SECONDS
        # Never recovered, not "recovered at monotonic zero": monotonic time counts
        # from boot, so 0.0 put the first FEED_RECOVERY_COOLDOWN seconds after a
        # reboot inside a cooldown, and a dead feed then went unrecovered.
        self._last_feed_recovery = float("-inf")
        self.feed_recoveries = 0
        self.lane_errors: dict[str, dict] = {}
        from .entry_scorer import EntryGate
        self.entry_gate = EntryGate(settings, settings.research_database_path)
        # Whale interest (owner, 3 Nov 2026): a whale-sized minute on an underlying gives its signals priority.
        from .whale_interest import WhaleInterest
        self.whale_interest = WhaleInterest()
        self._whale_interest_loading = False
        self._whale_interest_exported: int | None = None
        self._whale_tasks: set[asyncio.Task] = set()
        self.entry_gate.whale = self.whale_interest.reading
        self._preopen_refresh_day: str | None = None
        self.preopen_refreshes = 0
        self.signals_dropped_stale = 0
        self.last_stale_drop: dict | None = None
        self._selection_date: str | None = None
        self._contracts_live_since: datetime | None = None
        self.chain_seeded: dict[str, int] = {}
        self._last_contract_refresh: datetime | None = None
        self.contract_refresh_status: dict = {}
        self._auth_validated_date: str | None = None
        self._reconfigure_lock = asyncio.Lock()
        self.contract_selector = ContractSelector(
            self.broker, settings.contract_snapshot_path, settings.min_days_to_expiry)
        # Configured desk symbol -> the series actually listed today.
        self.futures_rollover: dict[str, str] = {}
        self.rollover_errors: dict[str, str] = {}
        self._flattened_expiry_day: str | None = None
        self.option_symbols: list[str] = []
        self.analysis_option_symbols: set[str] = set()
        self._ratio_locks: dict[str, asyncio.Lock] = {}
        self._ratio_errors: dict[str, str] = {}
        self._ratio_backfill_retry: dict[str, float] = {}
        # symbol -> last durable session before the hole warm() had to refill.
        self.history_gaps: dict[str, str] = {}
        self.all_symbols: list[str] = list(settings.symbols)
        self.started_at = datetime.now(UTC)
        self.ticks_total = 0
        self.candles_closed = 0
        self.signals_emitted = 0
        self.feed_connects = 0
        self.last_tick_at: datetime | None = None
        self._tick_times: deque[float] = deque(maxlen=20_000)
        self._last_equity_save = 0.0
        self._day_baseline_date: str | None = None
        self._day_baseline_equity: float | None = None
        # Fixed 60s aggregation feeding the durable candle store, independent
        # of the strategy timeframe.
        self.minute_candles = CandleAggregator(60)
        # Independent Market-Profile / Order-Flow desk: shares the tick stream,
        # keeps its own book in a separate database.
        # Role "all" runs the desk here. Role "strategy" leaves it to the desk
        # process: this MPEngine is an inert stand-in with an in-memory book, so
        # the strategy process never opens (or trades) the desk's database.
        self.desk_local = settings.engine_role == "all"
        # Strategy role: what the desk process last reported holding. Kept on
        # disk so a restart subscribes (and restores) those contracts even
        # before the desk reports again.
        self._desk_holdings_path = Path(settings.runtime_settings_path).with_name("desk_holdings.json")
        self.remote_desk_holdings: dict[str, int] = self._load_desk_holdings()
        self.desk_status: dict = {"reachable": False, "checked_at": None, "error": None}
        self._desk_status_task: asyncio.Task | None = None
        # Every accepted tick onto the bus (the desk and Rust analytics read it).
        self.bus = TickBusPublisher(settings.nats_url) if settings.nats_url else None
        self.mp = MPEngine(
            settings.mp_database_path if self.desk_local else ":memory:",
            settings=desk_settings(settings, enabled=settings.mp_enabled and self.desk_local),
            events=self.events,
            history_database_path=settings.research_database_path if self.desk_local else None,
        )
        # Third book: the blast lane. Shares the tick stream and the MACD
        # lane's indicator state, keeps its own database, and enters on the
        # signal-line cross the MACD lane deliberately ignores.
        self.blast = BlastEngine(settings.blast_database_path, settings, self.broker, self.events)
        self._blast_task: asyncio.Task | None = None
        # Fourth and fifth books: the smart lanes (smart_lane.py), the research's
        # best rules at two capitals. Fed MACD-lane signals and blast-screen passes.
        self.smart_lanes = SmartLanes(settings, self.broker, self.events)
        # Sixth book: the agentic lane, a Grok-run paper options book with written reasoning.
        self.agentic = (AgenticLane(settings, self.broker, self.events,
                                    str(Path(settings.database_path).parent / "agentic_trader.sqlite3"), engine=self)
                        if settings.agentic_enabled else None)
        if self.agentic is not None:
            self.agentic.telegram = TelegramBridge(self.agentic, settings, close_position=self.close_smart_paper_position)
        # Seventh book: the spot MACD lane, stock futures long and short on each stock's own 30-min MACD zero cross.
        self.spot_lane = (SpotLane(settings, self.broker, self.events,
                                   str(Path(settings.database_path).parent / "spot_macd.sqlite3"))
                          if settings.spot_lane_enabled else None)
        self.extreme_booker = ExtremeBooker(settings.research_database_path)
        # One book of latest quotes for every lane: paper fills cross the bid/ask carried by the tick that
        # priced them, and the blast journal reads the spread and IV inputs at each candidate.
        self.outage_repair: dict | None = None
        # Pre-rollover lot-size check (audit_lot_sizes): the last report, and when the master was last re-read.
        self.lot_audit: dict | None = None
        self._lot_master_read_at: datetime | None = None
        self.context_indices: list[str] = []
        self.context_indices_status: dict = {}
        self.expired_settlements: list[dict] = []
        self._self_heal_task: asyncio.Task | None = None
        from .live_features import FlowSnapshots
        self.flow_snapshots = FlowSnapshots(settings.nats_url) if settings.nats_url else None
        self.entry_flow = EntryFlow(self.flow_snapshots)
        self.entry_grader = EntryGrader(settings.research_database_path)
        quote = lambda symbol: self.latest_ticks.get(symbol)  # noqa: E731
        for execution in (self.execution, self.blast.execution, *[lane.execution for lane in _smart_lanes(self)]):
            execution.quote_source = quote
            execution.entry_context = self._entry_context
        self.blast.quote_source = quote
        self.blast.history_path = settings.research_database_path
        self.blast.candidate_listeners.append(self._offer_blast_candidate)
        self.candle_writer = LiveCandleWriter(settings.research_database_path)
        self.tick_store = TickStore(settings.tick_database_path, settings.tick_retention_days,
                                    settings.flow_retention_days)
        self._lag_task: asyncio.Task | None = None
        self._lag_samples: deque[float] = deque(maxlen=600)

    async def start(self) -> None:
        self._restore_portfolio()
        self.candle_writer.start()
        if self.desk_local and self.settings.tick_capture_enabled:
            self.tick_store.start()
        self._lag_task = asyncio.create_task(self._lag_probe_loop())
        if self.bus is not None:
            self.bus.start()
        if self.flow_snapshots is not None and not self.desk_local:
            self.flow_snapshots.start()
        if not self.desk_local and self.settings.desk_url:
            self._desk_status_task = asyncio.create_task(self._desk_status_loop())
        self.status = "connecting"
        self._startup_task = asyncio.create_task(self._finish_startup())

    async def _finish_startup(self) -> None:
        # History requests can take minutes. Serve the durable book and health
        # while they run, without letting rollover/recovery race initial setup.
        async with self._reconfigure_lock:
            await self.connect_feed()
        self.events.publish("snapshot_required", {"reason": "startup_complete"})
        await self._settle_expired()
        self._self_heal_task = asyncio.create_task(self._self_heal_loop())
        self._rollover_task = asyncio.create_task(self._rollover_loop())
        self._session_close_task = asyncio.create_task(self._session_close_loop())
        self._watchdog_task = asyncio.create_task(self._feed_watchdog_loop())
        self._expiry_flatten_task = asyncio.create_task(self._expiry_flatten_loop())
        self._dispersion_task = asyncio.create_task(self._dispersion_loop())
        self._blast_task = asyncio.create_task(self._blast_maintenance_loop())
        self.smart_lanes.start()
        if self.agentic is not None:
            self.agentic.start()
            self.agentic.telegram.start()
        if self.spot_lane is not None:
            self.spot_lane.start()
        self._entry_gate_task = asyncio.create_task(self.entry_gate.run(
            lambda: self.execution, lambda: self.portfolio, self._queued_macd_entry_rejection,
        ))
        if self.desk_local:
            self._nightly_task = asyncio.create_task(self._nightly_loop())
            self._chain_task = asyncio.create_task(self._chain_loop())
            self._stock_chain_task = asyncio.create_task(self._stock_chain_loop())
            if self.settings.tick_capture_enabled:
                self._tick_maintenance_task = asyncio.create_task(self._tick_maintenance_loop())
        self._history_prune_task = asyncio.create_task(self._history_prune_loop())

    async def _lag_probe_loop(self) -> None:
        """Sample event-loop scheduling lag so single-process saturation is a
        measured number in /api/system/health, not a guess."""
        interval = 0.5
        while True:
            started = time.monotonic()
            await asyncio.sleep(interval)
            self._lag_samples.append(max(0.0, (time.monotonic() - started - interval) * 1000))

    async def _resolve_futures_series(self) -> None:
        """Point every configured futures symbol at the active series."""
        configured = [item for item in self.mp_configured_symbols() if is_futures_symbol(item)]
        if not configured:
            self.futures_rollover = {}
            return
        try:
            listed = await self.broker.futures_series(configured)
        except Exception as exc:  # noqa: BLE001 — keep yesterday's mapping
            self.error = self.error or f"futures series lookup failed: {exc}"
            return
        resolved: dict[str, str] = {}
        for symbol in configured:
            series = listed.get(symbol) or []
            active = first_tradable(
                [Expiry(row.expiry, row.symbol) for row in series],
                self.settings.min_days_to_expiry,
            )
            if active and active.token and active.token != symbol:
                resolved[symbol] = active.token
            elif not active and series:
                self.rollover_errors[symbol] = "no listed futures series is far enough from expiry"
            else:
                self.rollover_errors.pop(symbol, None)
        self.futures_rollover = resolved

    def stored_history_is_stale(self, candles, today=None) -> bool:
        """True when a symbol's durable bars stop before a session it should have.

        warm() only reached for Fyers when a symbol was NEW or had NO stored
        bars at all. A symbol with years of history and a HOLE in it took
        neither branch, so the hole silently entered the indicator state. On
        8 Sep the feed died at 09:05 and wrote no candles all day; the next
        morning 215 equities, indices and futures warmed with MACD state that
        jumped straight from 7 Sep 15:15 to today, letting the 12/26 EMAs
        absorb a whole missing session in one bar -- and /api/system/health
        still reported warmup 693/693 with history_errors 0.

        Staleness is counted in completed TRADING sessions, not hours, so an
        ordinary overnight, weekend or exchange-holiday gap reads as current.
        Counting weekdays instead made the morning after 14 Sep 2026 (Ganesh
        Chaturthi) look like a missed session for every symbol, which would
        have spent a broker history request per symbol repairing a gap that
        did not exist and logged it as a repaired hole.
        """
        if not candles:
            return False
        newest = datetime.fromtimestamp(candles[-1].timestamp, UTC).astimezone(IST).date()
        today = today or datetime.now(IST).date()
        sessions = 0
        day = newest + timedelta(days=1)
        while day < today:
            if market_calendar.is_trading_day(day):
                sessions += 1
                if sessions >= 1:
                    return True
            day += timedelta(days=1)
        return False

    def _last_completed_session(self, today: date | None = None) -> date:
        day = (today or datetime.now(IST).date()) - timedelta(days=1)
        while not market_calendar.is_trading_day(day):
            day -= timedelta(days=1)
        return day

    def _history_checked_path(self) -> Path:
        return Path(self.settings.research_database_path).with_name("history_checked.json")

    def _history_checked(self) -> dict[str, str]:
        if getattr(self, "_history_checked_cache", None) is None:
            try:
                self._history_checked_cache = json.loads(self._history_checked_path().read_text())
            except (OSError, ValueError):
                self._history_checked_cache = {}
        return self._history_checked_cache

    def _history_checked_current(self, symbol: str) -> bool:
        """The broker was already asked for this symbol after the last completed session."""
        seen = self._history_checked().get(symbol)
        return bool(seen) and seen >= self._last_completed_session().isoformat()

    def _mark_history_checked(self, symbol: str) -> None:
        checked = self._history_checked()
        checked[symbol] = self._last_completed_session().isoformat()
        try:
            self._history_checked_path().write_text(json.dumps(checked))
        except OSError:
            pass

    def new_contract_needs_history(self, candles) -> bool:
        """Must a contract new to the watchlist download before it warms?

        Only when its stored bars fall short or stop before a session. One
        that already streamed -- as an ITM/OTM or NEXT-series leg, or an agent
        pick -- replays from the store (~25 ms) with no request. And a download
        is only the warm-up window; the rest of the 90 days follows in the
        background (:meth:`_backfill_contract_history`). Fetching all 90 days
        inline took 1 Oct's warm-up to nine minutes, almost all of it 225 new
        strikes' downloads. A newly listed option can legitimately have fewer
        bars than needed; it stays non-mature until real bars accrue.
        """
        return len(candles) < self.minimum_warmup_bars() or self.stored_history_is_stale(candles)

    def minimum_warmup_bars(self) -> int:
        """Bars required before this contract's indicators are trustworthy.

        The longest chain is the MACD slow EMA feeding the signal EMA, plus the
        widest standalone window (Bollinger / KAMA-RSI), plus a small buffer.
        """
        longest = max(
            self.settings.slow_period + self.settings.signal_period,
            self.settings.bb_period,
            self.settings.kama_period,
            self.settings.kama_rsi_period,
            self.settings.kama_roc_period,
        )
        return longest + 25

    def ratio_contracts(self, spot_symbol: str) -> list:
        """The complete current-expiry six-leg ladder for one underlying."""
        groups: dict[str, list] = defaultdict(list)
        for contract in self.contract_selector.contracts.values():
            if contract.spot_symbol == spot_symbol and contract.moneyness in {"ITM", "ATM", "OTM"}:
                groups[contract.expiry].append(contract)
        required = {(side, role) for side in ("CE", "PE") for role in ("ITM", "ATM", "OTM")}
        complete = [
            rows for rows in groups.values()
            if required.issubset({(row.option_type, row.moneyness) for row in rows})
        ]
        if not complete:
            return []
        # A complete group is today's selected ladder; old held expiries have
        # only one or two rows and therefore cannot win this choice.
        chosen = sorted(complete, key=lambda rows: rows[0].expiry)[0]
        ladder = []
        for side in ("CE", "PE"):
            for role in ("ITM", "ATM", "OTM"):
                candidates = [row for row in chosen if row.option_type == side and row.moneyness == role]
                if not candidates:
                    continue
                # Today's selected rows are normally non-retained. If the
                # selected ATM is itself held, liquidity breaks a same-expiry
                # tie against an older carried strike.
                ladder.append(max(
                    candidates,
                    key=lambda row: (
                        not row.retained,
                        max(row.volume, row.oi / 100),
                        -abs(row.strike - row.selection_price),
                    ),
                ))
        return ladder

    async def ratio_history(self, spot_symbol: str, timeframe_seconds: int) -> dict:
        contracts = self.ratio_contracts(spot_symbol)
        if not contracts:
            raise ValueError("No complete current-expiry ITM/ATM/OTM ladder is available")
        lock = self._ratio_locks.setdefault(spot_symbol, asyncio.Lock())
        session_start = session_open_epoch()
        async with lock:
            for contract in contracts:
                # "Does this leg have any rows" was the wrong question. The live
                # writer stores a bar a minute for every subscribed contract, so
                # an analysis-only ITM/OTM leg chosen this morning was always
                # non-empty and never downloaded. build_ratio_history intersects
                # the legs' timestamps, so one such leg collapsed the whole
                # ratio to today — which is exactly what the desk saw.
                prior = await asyncio.to_thread(
                    prior_session_candle_count,
                    self.settings.research_database_path, contract.symbol, session_start,
                )
                if prior:
                    self._ratio_errors.pop(contract.symbol, None)
                    self._ratio_backfill_retry.pop(contract.symbol, None)
                    continue
                if time.monotonic() < self._ratio_backfill_retry.get(contract.symbol, 0.0):
                    continue
                try:
                    rows = await self.broker.history_range(
                        contract.symbol,
                        60,
                        datetime.now(UTC) - timedelta(days=NEW_CONTRACT_HISTORY_DAYS),
                        datetime.now(UTC),
                    )
                except Exception as exc:
                    self._ratio_errors[contract.symbol] = str(exc)
                    self._ratio_backfill_retry[contract.symbol] = (
                        time.monotonic() + RATIO_BACKFILL_RETRY_SECONDS)
                    continue
                if not rows:
                    self._ratio_errors[contract.symbol] = (
                        "Fyers returned no minute candles for the current expiry")
                    self._ratio_backfill_retry[contract.symbol] = (
                        time.monotonic() + RATIO_BACKFILL_EMPTY_RETRY_SECONDS)
                    continue
                try:
                    await asyncio.to_thread(
                        store_historical_candles,
                        self.settings.research_database_path,
                        rows,
                        contract.expiry,
                    )
                except Exception as exc:
                    self._ratio_errors[contract.symbol] = str(exc)
                    self._ratio_backfill_retry[contract.symbol] = (
                        time.monotonic() + RATIO_BACKFILL_RETRY_SECONDS)
                    continue
                # A successful store is not the same as coverage. Fyers'
                # 90-day window for a strike listed this morning returns
                # today's bars — non-empty, so the branch above never fires —
                # and every stored row is still on or after session_start.
                # Popping the retry entry there would re-download the leg on
                # every 60s browser poll, so re-ask the question the guard
                # asked and stand the leg down if the answer has not moved.
                if await asyncio.to_thread(
                    prior_session_candle_count,
                    self.settings.research_database_path, contract.symbol, session_start,
                ):
                    self._ratio_errors.pop(contract.symbol, None)
                    self._ratio_backfill_retry.pop(contract.symbol, None)
                else:
                    self._ratio_errors[contract.symbol] = (
                        "Fyers minute history for this contract starts today; "
                        "before-session bars cannot exist until tomorrow")
                    self._ratio_backfill_retry[contract.symbol] = (
                        time.monotonic() + RATIO_BACKFILL_EMPTY_RETRY_SECONDS)
                await asyncio.sleep(0.25)

        live = {
            contract.symbol: list(self.history.get(contract.symbol, ()))
            for contract in contracts
            if timeframe_seconds == self.settings.timeframe_seconds
        }
        payload = await asyncio.to_thread(
            build_ratio_history,
            self.settings.research_database_path,
            contracts,
            timeframe_seconds,
            {
                "fast_period": self.settings.fast_period,
                "slow_period": self.settings.slow_period,
                "signal_period": self.settings.signal_period,
                "bb_period": self.settings.bb_period,
                "bb_deviations": self.settings.bb_deviations,
                "kama_period": self.settings.kama_period,
                "kama_fast": self.settings.kama_fast,
                "kama_slow": self.settings.kama_slow,
                "kama_rsi_period": self.settings.kama_rsi_period,
                "kama_roc_period": self.settings.kama_roc_period,
            },
            live,
        )
        payload["download_errors"] = {
            symbol: error for symbol, error in self._ratio_errors.items()
            if symbol in {row.symbol for row in contracts}
        }
        return payload

    def loop_lag_ms(self) -> dict:
        samples = sorted(self._lag_samples)
        if not samples:
            return {"p50": None, "p95": None, "max": None}
        pick = lambda q: round(samples[min(len(samples) - 1, int(q * len(samples)))], 1)  # noqa: E731
        return {"p50": pick(0.50), "p95": pick(0.95), "max": round(samples[-1], 1)}

    def _restore_portfolio(self) -> None:
        """Rebuild the paper book by replaying the durable trade log.

        The portfolio object is in-memory, so without this a restart silently
        reset equity to initial capital and dropped open positions. Replay is
        exact for cash/realized P&L. Replay only sees fill prints, so the
        excursion each open position travelled between fills is merged back
        from position_excursions; the peak and trailing stop are re-armed
        from that MFE, because re-arming from the average price used to
        disarm a live trail on every restart until a new high printed.
        """
        for row in self.repository.iter_trade_rows():
            try:
                # Replay discards the closed record on purpose: the durable
                # copy was written at fill time and is loaded by the
                # ExecutionManager, not re-created here.
                self.portfolio.apply_trade(Trade(
                    order_id=row["order_id"],
                    symbol=row["symbol"],
                    side=row["side"],
                    quantity=int(row["quantity"]),
                    price=float(row["price"]),
                    lots=int(row.get("lots", 1)),
                    lot_size=int(row.get("lot_size", 1)),
                    fees=float(row.get("fees", 0.0)),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    trade_id=row["trade_id"],
                ))
            except (KeyError, ValueError, TypeError):
                # One malformed or lot-size-shifted historical row must not
                # keep the terminal from booting.
                continue
        stored = self.repository.load_position_excursions()
        for position in self.portfolio.positions.values():
            row = stored.get(position.position_id)
            if row:
                position.max_price = max(position.max_price, float(row["max_price"]))
                position.min_price = min(position.min_price or float(row["min_price"]), float(row["min_price"]))
                position.max_return_pct = max(position.max_return_pct, float(row["max_return_pct"]))
                position.min_return_pct = min(position.min_return_pct, float(row["min_return_pct"]))
                # The trade replay is fee-exact; the excursion row only fills in
                # for a position that predates fee memory.
                if not position.entry_fees:
                    position.entry_fees = float(row["entry_fees"])
                # A replay of fills cannot infer which scale-out thresholds
                # already fired. This state belongs to the open position and
                # is checkpointed alongside its excursion data.
                position.entry_anchor = float(row["entry_anchor"] or 0)
                position.entry_stage = int(row["entry_stage"] or 0)
                position.exit_stage = int(row["exit_stage"] or 0)
            position.peak_price = max(position.peak_price, position.average_price, position.max_price)
            position.hard_stop = round(position.average_price * (1 - self.settings.hard_stop_pct), 4)
            position.entry_anchor = position.entry_anchor or position.average_price
            position.entry_stage = max(position.entry_stage, position.lots)
            if position.quantity > 0 and position.peak_price >= position.average_price * (1 + self.settings.trailing_activation_pct):
                floor = breakeven_exit_price(
                    position.average_price, position.quantity, self.settings.slippage_bps,
                )
                trail = position.peak_price * (1 - self.settings.trailing_stop_pct)
                position.trailing_stop = round(max(trail, floor), 4)

    async def audit_lot_sizes(self, *, reason: str = "", refresh: bool | None = None) -> dict:
        """Check every book's open contracts against the exchange's lot sizes and correct them; report the next series.

        NSE revises F&O lot sizes. On 29 Oct 2026 Fyers' master listed NSE:OFSS26NOV10000CE at 50 while the MACD
        book held it at the 100 it was bought in on 27 Oct, and the position could not be exited. Run at every
        contract rollover (connect_feed: boot, the 08:45 daily roll, a ladder refresh). The first run of a session
        from 08:30 IST re-downloads the master, so the 08:45 roll sees any revision Fyers published overnight.
        Quantities never change; positions are re-expressed in the new lot (Portfolio.relot). ``next_series``
        lists held and selected underlyings whose next expiry trades a different lot size than the current one,
        so a change is known before the books roll into it.
        """
        from .option_agent import parse_option

        now = datetime.now(IST)
        if refresh is None:
            last = self._lot_master_read_at
            refresh = (now.hour, now.minute) >= (8, 30) and (
                last is None or last.date() != now.date() or (last.hour, last.minute) < (8, 30))
        held = _held_contract_lots(self)
        report: dict = {"at": now.isoformat(timespec="seconds"), "reason": reason, "master_refreshed": refresh,
                        "held": len(held), "corrected": [], "unresolved": [], "next_series": [], "error": None}
        try:
            current = await self.broker.current_lot_sizes(sorted(held), refresh=refresh) if held else {}
            if refresh:
                self._lot_master_read_at = now
        except Exception as exc:  # noqa: BLE001 -- a failed check keeps the books as they are
            report["error"] = f"{type(exc).__name__}: {exc}"[:300]
            self.lot_audit = report
            return report
        report["unresolved"] = sorted(set(held) - current.keys())[:50]
        books = [self.execution, *([self.blast.execution] if getattr(self, "blast", None) else []),
                 *[lane.execution for lane in _smart_lanes(self)]]
        for execution in books:
            report["corrected"].extend(execution.correct_lot_sizes(current))
        # Next series: the same root's following expiry, for held contracts and today's selection.
        fronts: dict[str, tuple[str, int]] = {}
        for row in self.contract_selector.contracts.values():
            exchange = row.symbol.split(":", 1)[0]
            key = f"{exchange}:{row.underlying}"
            if row.lot_size > 0 and (key not in fronts or str(row.expiry) < fronts[key][0]):
                fronts[key] = (str(row.expiry)[:10], int(row.lot_size))
        for symbol in held:
            parsed = parse_option(symbol)
            if parsed and current.get(symbol):
                key = f"{symbol.split(':', 1)[0]}:{parsed['root']}"
                if key not in fronts or str(parsed["expiry"]) < fronts[key][0]:
                    fronts[key] = (str(parsed["expiry"])[:10], int(current[symbol]))
        try:
            series = await self.broker.series_lot_sizes(sorted({key.split(":", 1)[0] for key in fronts}))
        except Exception as exc:  # noqa: BLE001
            series = {}
            report["error"] = f"series: {type(exc).__name__}: {exc}"[:300]
        for key, (expiry, size) in sorted(fronts.items()):
            later = sorted(e for e in series.get(key, {}) if e > expiry)
            if later and series[key][later[0]] != size:
                report["next_series"].append({"underlying": key, "expiry": expiry, "lot_size": size,
                                              "next_expiry": later[0], "next_lot_size": series[key][later[0]]})
        self.lot_audit = report
        return report

    async def connect_feed(self) -> None:
        self.status = "connecting"
        # A new connection attempt must not carry yesterday's rejection into
        # the visible in-progress state after a replacement token was accepted.
        self.error = None
        self.feed_connects += 1
        self.events.publish("broker", self.broker_status())
        # Seconds from the start of this connect to each step: everything before "stream" is time the feed is down
        # (a ladder refresh calls this with the socket closed).
        connect_started = time.monotonic()
        connect_steps: dict[str, float] = {}
        self.connect_steps = connect_steps

        def step(name: str) -> None:
            connect_steps[name] = round(time.monotonic() - connect_started, 1)
        try:
            await self.broker.connect()
            self._auth_validated_date = datetime.now(IST).date().isoformat()
            strategy_held = set(self.portfolio.positions) | set(self.desk_holdings())
            held_positions = _held_contract_lots(self)
            held = await self.contract_selector.restore_held_contracts(held_positions)
            # Metadata recovered only for another book's carried holding is
            # management-only; it must not become a fresh MACD entry candidate.
            for row in held:
                if row.symbol not in strategy_held and row.selection_price == 0:
                    row.analysis_only = True
            # During an in-process rollover the selector already holds the
            # contract metadata. Prefer it, but fall back to the durable
            # snapshot after an API restart.
            held_by_symbol = {row.symbol: row for row in held}
            held_by_symbol.update({
                symbol: contract for symbol, contract in self.contract_selector.contracts.items()
                if symbol in held_positions
            })
            for contract in held_by_symbol.values():
                contract.retained = True
            contracts = await self.contract_selector.build(self.settings.symbols, list(held_by_symbol.values()))
            self.contract_selector.contracts = {row.symbol: row for row in contracts}
            missing_held = set(held_positions) - self.contract_selector.contracts.keys()
            if missing_held:
                raise RuntimeError(
                    "Held contracts could not be restored; feed subscription blocked: "
                    + ", ".join(sorted(missing_held))
                )
            self._selection_date = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
            step("selection")
            self._contracts_live_since = datetime.now(UTC)
            # ITM/OTM legs are market-context inputs only.  They are live-feed
            # subscribers and chart sources, never additions to either paper
            # strategy's entry universe. Held contracts remain manageable.
            self.option_symbols = [
                row.symbol for row in contracts
                if not row.analysis_only or row.symbol in strategy_held
            ]
            self.analysis_option_symbols = {
                row.symbol for row in contracts if row.symbol not in self.option_symbols
            }
            # The option agent's recent picks stay on the feed for their 1-3 day
            # hold, analysis-only: live marks on its page, and the minute bars its
            # outcome labels are measured from, after the strike leaves the ladder.
            try:
                from .option_agent import watch_symbols
                agent_watch = await asyncio.to_thread(watch_symbols, self.settings.research_database_path)
                self.analysis_option_symbols |= agent_watch - set(self.option_symbols)
            except Exception:  # noqa: BLE001 -- a research read must never block the feed
                pass
            # The entry gate's reference data (previous-session volume, 5-session-back close):
            # read in the background so contract selection never waits on it.
            asyncio.create_task(self.entry_gate.refresh_reference(
                [*self.option_symbols, *{row.spot_symbol for row in contracts}]))
            await self._resolve_futures_series()
            # The spot lane trades each stock's future: today's series are chosen before the subscription is built.
            if getattr(self, "spot_lane", None) is not None:
                await self.spot_lane.resolve_futures(self.broker)
            self.context_indices = await self._validated_context_indices()
            # The desk's own instruments must be subscribed too: it watches
            # index futures, which are in neither the spot universe nor the
            # option list.
            self.all_symbols = _drop_expired(list(dict.fromkeys(
                [*self.settings.symbols, *self.option_symbols, *self.analysis_option_symbols, *self.mp_spot_symbols(),
                 *self.context_indices,
                 # Keep the shared reference subscribed even if adaptation is
                 # toggled off, so enabling it does not require a feed restart.
                 *([self.settings.vix_symbol] if self.settings.vix_symbol else []),
                 # A position opened in the outgoing series still has to be
                 # marked and exited, so it stays subscribed after the roll.
                 *self.portfolio.positions, *self.desk_holdings(),
                 # Stream subscription only, so a blast holding keeps its stop
                 # and trail live. Subscribing is not a fetch: it rides the
                 # websocket the engine already holds open.
                 *self.blast.portfolio.positions, *[s for smart in _smart_lanes(self) for s in smart.held_symbols()],
                 # Every stock's chosen future: a spot-lane entry fills on a fresh futures quote.
                 *(self.spot_lane.feed_symbols() if getattr(self, "spot_lane", None) is not None else [])])),
                {row.symbol: row.expiry for row in contracts}, datetime.now(IST).date())
            self.strategy = MACDStrategyManager(self.settings, self.events, self.option_symbols)
            tradable = {row.symbol: row.lot_size for row in contracts if row.symbol in self.option_symbols}
            self.execution.set_tradable_contracts(tradable)
            self.execution.set_contract_expiries({row.symbol: row.expiry for row in contracts})
            self.blast.set_tradable_contracts(tradable)
            self.blast.set_contract_context({
                row.symbol: {
                    "spot_symbol": row.spot_symbol, "option_type": row.option_type,
                    "strike": row.strike, "expiry": row.expiry,
                    # Selection-time liquidity, the same reading the ladder's
                    # ATM leg was chosen on. Dropping it here left the blast
                    # screen unable to tell a contract that trades from one
                    # that does not, and it sized both at the full ticket.
                    "volume": row.volume, "oi": row.oi,
                }
                for row in contracts if row.symbol in self.option_symbols
            })
            self.blast.set_resolvers(spot_price=self.spot_price,
                                     vix_quote=lambda: self.latest_ticks.get(self.settings.vix_symbol))
            lane_context = {
                row.symbol: {"spot_symbol": row.spot_symbol, "option_type": row.option_type, "strike": row.strike,
                             "expiry": row.expiry, "volume": row.volume, "oi": row.oi}
                for row in contracts if row.symbol in self.option_symbols
            }
            for lane in _smart_lanes(self):
                lane.set_tradable_contracts(tradable)
                lane.set_contract_context(lane_context)
                lane.set_resolvers(macd_confirm=self._smart_macd_confirm, dispersion=self.dispersion_point,
                                   entry_context=self._smart_entry_context, whale=self.whale_interest.reading)
            if getattr(self, "agentic", None) is not None:
                # The agentic lane may pick any selected index strike or roll to the next expiry.
                self.agentic.set_index_universe({
                    row.symbol: {"spot_symbol": row.spot_symbol, "option_type": row.option_type, "strike": row.strike,
                                 "expiry": row.expiry, "volume": row.volume, "oi": row.oi, "lot_size": row.lot_size,
                                 "moneyness": row.moneyness}
                    for row in contracts if row.underlying in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")})
            self.configure_desk(tradable)
            # Before trading the new selection: every held contract's lot size against the exchange's
            # current one (owner, 30 Oct 2026). Never blocks the feed.
            try:
                await self.audit_lot_sizes(reason="contract rollover")
            except Exception as exc:  # noqa: BLE001
                self.lot_audit = {"at": datetime.now(IST).isoformat(timespec="seconds"),
                                  "error": f"{type(exc).__name__}: {exc}"[:300]}
            step("lot_audit")
            await self._refresh_position_marks()
            step("marks")

            # Self-heal: download the session minutes the app did not capture (host down, feed dead) BEFORE
            # warm-up, so no indicator steps across a hole. On 15 Oct 2026 this took a manual backfill.
            try:
                self.outage_repair = await self_heal.repair_outage(
                    self.broker, self.settings.research_database_path, list(self.all_symbols))
            except Exception as exc:  # noqa: BLE001 -- a repair must never block the feed
                self.outage_repair = {"error": f"{type(exc).__name__}: {exc}"[:300]}

            # Two lanes (boot, 16 Oct 2026): stored-history loads are ~0.1 s each and must not queue behind
            # broker downloads, which take seconds each and are retried with back-off when Fyers is flaky.
            load_slots = asyncio.Semaphore(WARMUP_LOAD_CONCURRENCY)
            download_slots = asyncio.Semaphore(WARMUP_DOWNLOAD_CONCURRENCY)
            new_contracts = set(self.contract_selector.new_symbols)
            expiry_by_symbol = {row.symbol: row.expiry for row in contracts}
            history_backfill: list[str] = []
            downloaded: set[str] = set()
            self.chain_seeded = {}

            async def warm(symbol: str) -> None:
                partial: list[Candle] = []
                for attempt in range(WARMUP_ATTEMPTS):
                    try:
                        async with load_slots:
                            stored = await asyncio.to_thread(
                                load_chart_history,
                                self.settings.research_database_path,
                                symbol,
                                self.settings.timeframe_seconds,
                                self.settings.fast_period,
                                self.settings.slow_period,
                                self.settings.signal_period,
                                self.settings.bb_period,
                                self.settings.bb_deviations,
                                self.settings.kama_period,
                                self.settings.kama_fast,
                                self.settings.kama_slow,
                                self.settings.kama_rsi_period,
                                self.settings.kama_roc_period,
                                source_row_limit=LIVE_WARMUP_SOURCE_ROWS,
                            )
                        broker_rows = []
                        minute_rows = []
                        needed = self.minimum_warmup_bars()
                        if len(stored["candles"]) < needed:
                            # Too few stored bars (a strike chosen around today's price): seed from the option-chain
                            # marks already on disk before asking the broker, whose history may be switched off.
                            seeded = await asyncio.to_thread(chain_candles, self.settings.research_database_path,
                                                             symbol, self.settings.timeframe_seconds)
                            if seeded:
                                by_time = {row.timestamp: row for row in seeded}
                                by_time.update({row.timestamp: row for row in stored["candles"]})
                                stored["candles"] = [by_time[key] for key in sorted(by_time)]
                                self.chain_seeded[symbol] = len(seeded)
                        partial = stored["candles"]
                        if symbol in new_contracts:
                            # The rest of its 90 days follows in the
                            # background once the feed is warm.
                            history_backfill.append(symbol)
                        if symbol in new_contracts and self.new_contract_needs_history(stored["candles"]):
                            try:
                                async with download_slots:
                                    broker_rows, minute_rows = await self._download_warmup_history(symbol)
                                    await asyncio.sleep(WARMUP_DOWNLOAD_PACING_SECONDS)
                            except Exception:
                                if len(stored["candles"]) < needed:
                                    raise
                        elif symbol not in new_contracts and (
                                not stored["candles"] or (self.stored_history_is_stale(stored["candles"])
                                                          and not self._history_checked_current(symbol))):
                            if stored["candles"]:
                                self.history_gaps[symbol] = datetime.fromtimestamp(
                                    stored["candles"][-1].timestamp, UTC).astimezone(IST).date().isoformat()
                            try:
                                async with download_slots:
                                    broker_rows, minute_rows = await self._download_warmup_history(symbol)
                                    await asyncio.sleep(WARMUP_DOWNLOAD_PACING_SECONDS)
                                # A quiet contract can have nothing newer to give: remember that it was
                                # asked, or every boot downloads it again (35 of 71 downloads on 16 Oct).
                                self._mark_history_checked(symbol)
                            except Exception:
                                # A restart warms hundreds of contracts at once.  If
                                # Fyers rate-limits a supplemental request, retain the
                                # already durable research history rather than dropping
                                # the whole symbol from the live strategy state.
                                if not stored["candles"]:
                                    raise
                        if minute_rows:
                            downloaded.add(symbol)
                            # Persist per symbol: a 90-day minute download for
                            # every rolled contract is millions of rows, too
                            # many to hold until the whole warm-up finishes.
                            try:
                                await asyncio.to_thread(
                                    store_historical_candles,
                                    self.settings.research_database_path,
                                    minute_rows,
                                    None,
                                    expiry_by_symbol,
                                )
                            except Exception as exc:  # noqa: BLE001 - cache failure must not kill the feed
                                self.candle_writer.last_error = f"history cache failed: {exc}"
                        rows_by_time = {row.timestamp: row for row in stored["candles"]}
                        rows_by_time.update({row.timestamp: row for row in broker_rows})
                        rows = [rows_by_time[key] for key in sorted(rows_by_time)]
                        if not rows:
                            raise RuntimeError("Fyers returned no historical candles")
                        self._finish_symbol_warmup(symbol, rows)
                        self.history_errors.pop(symbol, None)
                        return
                    except Exception as exc:
                        self.history_errors[symbol] = str(exc)
                        # A 429 is a RATE limit, not a concurrency limit: retrying
                        # 0.5s later just spends another request inside the same
                        # window. Back off past the window instead — giving up
                        # after 1.5s left 138 contracts with no indicator state
                        # and no scheduled retry to recover them.
                        if attempt < WARMUP_ATTEMPTS - 1:
                            rate_limited = "429" in str(exc) or "Too Many Requests" in str(exc)
                            delay = WARMUP_RATE_LIMIT_BACKOFF[attempt] if rate_limited else 0.5 * (attempt + 1)
                            await asyncio.sleep(delay)
                # No broker history was available. Release the symbol with the
                # stored / chain-seeded bars it has (short of the full warm-up,
                # so its indicators mature with each live bar) plus whatever
                # closed live bars arrived while FYERS was retried.
                self._finish_symbol_warmup(symbol, partial)

            # The blast lane runs on one-minute bars, so it seeds from the
            # stored minute bars rather than this strategy-timeframe replay.
            try:
                await self.blast.warm_from_store(
                    self.settings.research_database_path,
                    [*self.option_symbols, *self.blast.portfolio.positions],
                )
            except Exception as exc:  # noqa: BLE001 - the lane must not block the feed
                self.blast.error = f"minute-bar warm-up failed: {exc}"
            step("blast_warm")
            # Subscribe before the hundreds of FYERS history requests. Each
            # symbol buffers closed live bars until its historical replay is
            # complete, so signals cannot run on a partial indicator state.
            # Analysis-only ratio legs remain lazy and have no MACD state.
            warm_symbols = self.warmup_symbols()
            self._warming_symbols = set(warm_symbols)
            self._warm_live_candles.clear()
            self.status = "connected"
            self.error = None
            self._stream_task = asyncio.create_task(self._run_broker_stream())
            step("stream")
            warm_started = time.monotonic()
            await asyncio.gather(*(warm(symbol) for symbol in warm_symbols))
            self.warmup_report = {
                "finished_at": datetime.now(IST).isoformat(timespec="seconds"),
                "seconds": round(time.monotonic() - warm_started, 1),
                "symbols": len(warm_symbols), "new_contracts": len(new_contracts & set(warm_symbols)),
                "downloaded": len(downloaded), "chain_seeded": len(self.chain_seeded),
                "selection": dict(self.contract_selector.selection_source),
                "connect_steps": connect_steps,
            }
            if downloaded:
                # The blast lane seeded from the store before these strikes'
                # bars landed; fold them in now (it reads only newer bars).
                try:
                    await self.blast.warm_from_store(self.settings.research_database_path, sorted(downloaded))
                except Exception as exc:  # noqa: BLE001
                    self.blast.error = f"minute-bar warm-up failed: {exc}"
            if history_backfill:
                backfill = getattr(self, "_history_backfill_task", None)
                if backfill and not backfill.done():
                    backfill.cancel()
                self._history_backfill_task = asyncio.create_task(
                    self._backfill_contract_history(list(dict.fromkeys(history_backfill)), expiry_by_symbol))
            if self.context_indices:
                self._context_backfill_task = asyncio.create_task(self._backfill_context_indices())
        except Exception as exc:
            self.status = "error"
            self.error = str(exc)
        finally:
            self.events.publish("broker", self.broker_status())

    async def _backfill_contract_history(self, symbols: list[str], expiry_by_symbol: dict[str, str]) -> None:
        """The older part of each new contract's 90 days, one request at a time, after warm-up.

        Charts, ratio history and research read these bars; no indicator
        needs them, so they no longer hold up the morning warm-up. Paced well
        under the history rate limit, and a 429 waits past the minute window.
        """
        report = self.history_backfill = {
            "pending": len(symbols), "stored": 0, "rows": 0, "errors": {},
            "started_at": datetime.now(IST).isoformat(timespec="seconds"), "finished_at": None,
        }
        now = datetime.now(UTC)
        end = now - timedelta(days=max(0, self._warmup_window_days() - 1))
        start = now - timedelta(days=NEW_CONTRACT_HISTORY_DAYS)
        for symbol in symbols:
            for attempt in range(3):
                try:
                    minutes = await self.broker.history_range(symbol, 60, start, end)
                    if minutes:
                        await asyncio.to_thread(store_historical_candles, self.settings.research_database_path,
                                                minutes, None, expiry_by_symbol)
                        report["rows"] += len(minutes)
                    report["stored"] += 1
                    report["errors"].pop(symbol, None)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - background fill; the feed is unaffected
                    report["errors"][symbol] = str(exc)[:160]
                    await asyncio.sleep(65.0 if "429" in str(exc) else 5.0)
            report["pending"] -= 1
            await asyncio.sleep(HISTORY_BACKFILL_PACE_SECONDS)
        report["finished_at"] = datetime.now(IST).isoformat(timespec="seconds")

    async def _download_warmup_history(
        self, symbol: str, days: int | None = None,
    ) -> tuple[list[Candle], list[Candle]]:
        """Fetch history as (strategy-timeframe bars, minute bars to persist).

        The durable store holds only one-minute bars, and a download used to be
        persisted only when the strategy itself ran on one minute. On the
        30-minute timeframe every new contract's 90-day history lived in
        memory alone: the blast lane, ratio charts and chart history found no
        bars for the freshly rolled series, and each restart downloaded it
        again. Download minutes instead and build the strategy bars from them
        with the same session-aligned buckets the stored-history path uses.

        ``days`` requests a fixed window (a newly listed contract); otherwise
        the window covers ``WARMUP_HISTORY_BARS`` strategy bars.
        """
        timeframe = self.settings.timeframe_seconds
        end = datetime.now(UTC)
        if timeframe <= 60:
            if days is None:
                minutes = await self.broker.history(symbol, timeframe, WARMUP_HISTORY_BARS)
            else:
                minutes = await self.broker.history_range(symbol, timeframe, end - timedelta(days=days), end)
            return minutes, minutes
        if days is None:
            days = self._warmup_window_days()
        minutes = await self.broker.history_range(symbol, 60, end - timedelta(days=days), end)
        return aggregate_session_candles(minutes, timeframe), minutes

    def _warmup_window_days(self) -> int:
        """Calendar days holding ``WARMUP_HISTORY_BARS`` strategy bars, with room for holidays."""
        timeframe = self.settings.timeframe_seconds
        if timeframe <= 60:
            return 1
        sessions = -(-WARMUP_HISTORY_BARS * timeframe // (375 * 60))
        return min(NEW_CONTRACT_HISTORY_DAYS, sessions * 7 // 5 + 5)

    def _finish_symbol_warmup(self, symbol: str, historical: list[Candle]) -> None:
        """Replay history then bars received during download, exactly once."""
        live = self._warm_live_candles.pop(symbol, [])
        rows_by_time = {row.timestamp: row for row in historical}
        rows_by_time.update({row.timestamp: row for row in live})
        rows = [rows_by_time[key] for key in sorted(rows_by_time)]
        self.history[symbol].clear()
        self.history[symbol].extend(rows[-500:])
        for candle in rows:
            self.strategy.on_closed_candle(candle, warmup=True)
        self._warming_symbols.discard(symbol)

    async def _run_broker_stream(self) -> None:
        """Make websocket termination visible instead of leaving stale green state."""
        try:
            await self.broker.stream(self.all_symbols, self.on_tick)
            if self.status == "connected":
                raise RuntimeError("Broker websocket stopped unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Always keep the reason. A failure that arrived while the status
            # was already "error" used to be swallowed, so a stream dying the
            # same way on every watchdog restart showed "error" with no text.
            self.error = str(exc) or type(exc).__name__
            if self.status == "connected":
                self.status = "error"
                with suppress(Exception):
                    await asyncio.wait_for(self.broker.close(), FEED_STEP_TIMEOUT)
            self.events.publish("broker", self.broker_status())

    async def _refresh_position_marks(self) -> None:
        """Apply read-only REST marks without running paper exit logic.

        A restart can happen outside market hours, when no websocket tick will
        arrive.  These marks update portfolio valuation only; they deliberately
        bypass ``ExecutionManager.on_tick`` so a stale close cannot fire exits.
        """
        mp = getattr(self, "mp", None)
        mp_positions = mp.portfolio.positions if mp is not None else {}
        # The blast lane's holdings are deliberately NOT in this request. That
        # lane never causes a broker call; it is valued below from whatever
        # this call and the stream have already put in memory.
        smart_lanes = _smart_lanes(self)
        symbols = list(dict.fromkeys([*self.portfolio.positions, *mp_positions,
                                     *[s for lane in smart_lanes for s in lane.portfolio.positions]]))
        blast = getattr(self, "blast", None)
        if not symbols:
            if blast is not None and blast.mark_from(self.latest_ticks):
                self.events.publish("snapshot_required", {"reason": "position_marks_refreshed"})
            return
        try:
            quotes = await self.broker.quotes(symbols)
        except Exception as exc:
            for symbol in symbols:
                self.position_mark_errors[symbol] = str(exc)
            return
        for symbol in symbols:
            tick = quotes.get(symbol)
            if tick is None or tick.ltp <= 0:
                self.position_mark_errors[symbol] = "Fyers returned no positive startup mark"
                continue
            self.portfolio.mark(symbol, tick.ltp)
            for lane in smart_lanes:
                lane.portfolio.mark(symbol, tick.ltp)
            if mp is not None:
                mp.portfolio.mark(symbol, tick.ltp)
                mp.last_prices[symbol] = tick.ltp
                mp.last_price_at[symbol] = datetime.now(UTC)
            self.latest_ticks[symbol] = tick
            self.position_mark_errors.pop(symbol, None)
        if blast is not None:
            blast.mark_from(self.latest_ticks)
        self.events.publish("snapshot_required", {"reason": "position_marks_refreshed"})

    async def restart_stream(self) -> None:
        """Re-establish just the market-data socket.

        Cheaper than connect_feed(): the contract set and warmed indicators are
        already correct, only the websocket is gone. Rebuilding the universe
        here would cost minutes of a live session for no benefit.
        """
        if self._stream_task:
            self._stream_task.cancel()
            # Cancelling does not free a task parked in asyncio.to_thread, so
            # awaiting it is not bounded by the cancel. Abandon it on timeout.
            with suppress(asyncio.CancelledError, TimeoutError, Exception):
                await asyncio.wait_for(self._stream_task, FEED_STEP_TIMEOUT)
            self._stream_task = None
        with suppress(Exception):
            await asyncio.wait_for(self.broker.close(), FEED_STEP_TIMEOUT)
        # revalidates the daily token
        await asyncio.wait_for(self.broker.connect(), FEED_STEP_TIMEOUT)
        # A restart that crosses an expiry must not resubscribe the dead series.
        self.all_symbols = _drop_expired(self.all_symbols, {}, datetime.now(IST).date())
        # The replacement socket is what the status now describes. Leaving the
        # old "error" in place meant _run_broker_stream ignored the next failure
        # and a recovered feed stayed red.
        self.status = "connected"
        self.error = None
        # Via _run_broker_stream, not broker.stream directly: the wrapper is
        # what turns a socket that stops into status="error" and a published
        # broker event. Restarting past it meant a replacement socket could
        # die as silently as the one it replaced.
        self._stream_task = asyncio.create_task(self._run_broker_stream())

    async def _feed_watchdog_loop(self) -> None:
        """Recover a feed that has died without saying so.

        The Fyers SDK retries its socket five times and then logs
        "Max reconnect attempts reached. Connection abandoned." — it never
        raises, so the engine kept reporting "connected" while no tick arrived
        for hours, straight through a market open. Liveness has to be measured
        from tick arrivals, not from the result of the initial connect.
        """
        while True:
            await asyncio.sleep(FEED_WATCHDOG_INTERVAL)
            # NOTHING in this loop may end it. It is the only thing that
            # notices a feed that has stopped without saying so, and on 8 Sep
            # it was itself the casualty: the pre-open refresh it awaits hung
            # inside an uncancellable close(), so the loop never reached the
            # staleness check below and the desk sat out the whole session
            # with feed_recoveries at 0 and not one line in the log.
            try:
                await self._watchdog_sweep()
            except Exception as exc:  # noqa: BLE001 — survive to the next sweep
                self.error = f"feed watchdog sweep failed: {exc}"

    async def _watchdog_sweep(self) -> None:
        try:
            refreshed = await asyncio.wait_for(
                self._preopen_refresh(), FEED_STEP_TIMEOUT * 2)
        except TimeoutError:
            # The refresh marks the day done before it acts, so it will not be
            # retried; recovery below takes over once the session opens.
            self.error = "pre-open feed refresh timed out"
            return
        if refreshed:
            return
        if not (regular_session_open() or preopen_window()):
            return
        age = self.seconds_since_last_tick()
        if age is not None and age <= FEED_STALE_SECONDS:
            return
        now = time.monotonic()
        if now - self._last_feed_recovery < FEED_RECOVERY_COOLDOWN:
            return
        self._last_feed_recovery = now
        self.feed_recoveries += 1
        try:
            async with asyncio.timeout(FEED_STEP_TIMEOUT * 4):
                async with self._reconfigure_lock:
                    await self.restart_stream()
        except Exception as exc:  # noqa: BLE001 — retry on the next sweep
            self.error = f"feed recovery failed: {exc}"
        finally:
            self.events.publish("broker", self.broker_status())

    def _archive(self):
        """The market-data archive, when its folder is mounted; None means the hot stores keep everything."""
        from .archive import Archive
        archive = Archive(self.settings.archive_dir or "/nonexistent")
        return archive if archive.available() else None

    async def _history_prune_loop(self) -> None:
        """Bound historical.sqlite3 once a day, after the close.

        The minute-bar cache had no retention and grew by up to ~190 MB a
        trading day. The engine is the file's writer, so it prunes it; no
        other process may, since the file is written without WAL.
        """
        while True:
            await asyncio.sleep(600)
            now = datetime.now(IST)
            today = now.date().isoformat()
            if (self.history_prune["day"] == today or regular_session_open() or preopen_window()
                    or now.hour < HISTORY_PRUNE_AFTER_HOUR):
                continue
            self.history_prune["day"] = today
            try:
                self.history_prune["result"] = await asyncio.to_thread(
                    prune_history,
                    self.settings.research_database_path,
                    now=now,
                    keep_days=self.settings.history_retention_days,
                    expired_keep_days=self.settings.expired_contract_retention_days,
                    archive=self._archive(),
                )
                self.history_prune["error"] = None
            except Exception as exc:  # noqa: BLE001 - retry tomorrow; never stop the feed
                self.history_prune["error"] = str(exc)[:300]

    async def _preopen_refresh(self) -> bool:
        """Reconnect once, just before the bell, on a socket that has idled.

        Returns True when a refresh was attempted, so the caller skips the
        staleness check this sweep (the socket is deliberately new and has no
        ticks yet). Pre-open silence is normal, so this cannot be driven off
        tick age — it has to be unconditional and time-triggered.
        """
        moment = datetime.now(IST)
        if not market_calendar.is_trading_day(moment.date()):
            return False
        if not (PREOPEN_REFRESH_AT <= (moment.hour, moment.minute) < (9, 15)):
            return False
        today = moment.date().isoformat()
        if self._preopen_refresh_day == today:
            return False
        # Only recycle an IDLE socket. This exists because the SDK burns its
        # five reconnects silently during the long pre-open idle, so a socket
        # still claiming "connected" can be dead at 09:15 -- but a socket
        # delivering ticks right now is not that socket. On 8 Sep the refresh
        # tore down a feed that had just carried 1,323 ticks, and the teardown
        # is what hung. If it falls quiet later the watchdog's staleness path
        # covers the rest of the pre-open, so leave the day unmarked.
        age = self.seconds_since_last_tick()
        if age is not None and age <= FEED_STALE_SECONDS:
            return False
        self._preopen_refresh_day = today
        self.preopen_refreshes += 1
        try:
            async with self._reconfigure_lock:
                await self.restart_stream()
            self.error = None
        except Exception as exc:  # noqa: BLE001 — the watchdog retries from 09:15
            self.error = f"pre-open feed refresh failed: {exc}"
        finally:
            self.events.publish("broker", self.broker_status())
        return True

    def signal_staleness_limit(self) -> float:
        """One full bar period, plus grace for the close-and-evaluate boundary."""
        return float(self.settings.timeframe_seconds) + SIGNAL_STALENESS_GRACE_SECONDS

    def _signal_bar_is_current(self, signal) -> bool:
        """Reject a signal whose bar belongs to an earlier period than now.

        Staleness is measured from the bar's START, so an intrabar signal is
        naturally 0..timeframe seconds old and a closed-bar signal is exactly
        one timeframe old. Anything beyond that means the feed skipped a whole
        period and the cross being acted on is from a bar that has already gone.
        """
        bar = getattr(signal, "evaluated_candle_timestamp", None)
        if bar is None:
            return True
        staleness = datetime.now(UTC).timestamp() - bar
        if staleness <= self.signal_staleness_limit():
            return True
        self.signals_dropped_stale += 1
        self.last_stale_drop = {
            "symbol": getattr(signal, "symbol", None),
            # Bar timestamps are integer seconds while wall time is fractional;
            # floor for stable, non-flaky operational reporting.
            "staleness_seconds": int(staleness),
            "limit_seconds": self.signal_staleness_limit(),
            "at": datetime.now(UTC).isoformat(),
        }
        return False

    async def disconnect_feed(self) -> None:
        backfill = getattr(self, "_history_backfill_task", None)
        if backfill and not backfill.done():
            backfill.cancel()
        if self._stream_task:
            self._stream_task.cancel()
            # A dead websocket task can already hold the auth error that
            # triggered this disconnect.  Cleanup must not replay that stale
            # exception: token installation calls reconfigure(), and aborting
            # here would leave the newly validated token on disk but the old
            # expired broker in memory.
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._stream_task, FEED_STEP_TIMEOUT)
            self._stream_task = None
        # The Fyers SDK closes in a worker thread and can hang after exhausting
        # its internal reconnect loop.  Never let that hold _reconfigure_lock:
        # a freshly verified daily token must be able to replace the dead
        # broker instead of waiting behind yesterday's socket forever.
        with suppress(asyncio.CancelledError, TimeoutError, Exception):
            await asyncio.wait_for(self.broker.close(), FEED_STEP_TIMEOUT)

    def _apply_capital_change(self, initial_capital: float) -> None:
        """Change only the paper account's funding base, keeping every position and
        realized result. A later restart reaches the same state by replaying the
        durable trades from the new initial capital. The change is logged so day
        P&L does not count new funding as profit (or a withdrawal as a loss)."""
        capital_delta = initial_capital - self.settings.initial_capital
        if not capital_delta:
            return
        self.portfolio.initial_capital = initial_capital
        self.portfolio.cash += capital_delta
        self.repository.record_capital_change(capital_delta, datetime.now().astimezone().isoformat())
        if self._day_baseline_equity is not None:
            self._day_baseline_equity += capital_delta

    async def reconfigure(self, new_settings: Settings) -> None:
        async with self._reconfigure_lock:
            await self.disconnect_feed()
            self._apply_capital_change(new_settings.initial_capital)
            self.settings = new_settings
            self.calendar_rejected = market_calendar.configure(new_settings.market_holidays_csv)
            self.broker = create_broker(new_settings)
            self.execution.settings = new_settings
            self.execution.broker = self.broker
            self.blast.rebind_broker(self.broker)
            self.blast.apply_settings(new_settings)
            for smart in _smart_lanes(self):
                smart.broker.rebind(self.broker)
                smart.apply_settings(new_settings)
            self.mp.settings.max_positions = new_settings.mp_max_positions
            self.mp.settings.max_trades_per_day = new_settings.mp_max_trades_per_day
            self.mp.vix_symbol = new_settings.vix_symbol
            self.strategy = MACDStrategyManager(new_settings, self.events)
            self.candles = CandleAggregator(new_settings.timeframe_seconds)
            self.history.clear()
            self.history_errors.clear()
            self._warming_symbols.clear()
            self._warm_live_candles.clear()
            self.latest_ticks.clear()
            self.contract_selector = ContractSelector(
                self.broker, new_settings.contract_snapshot_path, new_settings.min_days_to_expiry)
            self.option_symbols = []
            self.analysis_option_symbols = set()
            self.all_symbols = list(new_settings.symbols)
            await self.connect_feed()
            self.events.publish("snapshot_required", {
                "reason": "settings_changed" if self.status == "connected" else "settings_reconfigure_failed"
            })

    async def reconfigure_strategy(self, new_settings: Settings) -> None:
        """Apply paper-strategy settings without reconnecting the live broker feed."""
        async with self._reconfigure_lock:
            timeframe_changed = new_settings.timeframe_seconds != self.settings.timeframe_seconds
            self._apply_capital_change(new_settings.initial_capital)
            self.settings = new_settings
            self.calendar_rejected = market_calendar.configure(new_settings.market_holidays_csv)
            self.execution.settings = new_settings
            # The broker object and its authenticated websocket remain untouched.
            # Only its immutable settings view is refreshed for subsequent REST calls.
            if hasattr(self.broker, "settings"):
                self.broker.settings = new_settings
            self.strategy = MACDStrategyManager(new_settings, self.events, self.option_symbols)
            self.candles = CandleAggregator(new_settings.timeframe_seconds)
            # The auction desk holds its own settings object, built once at
            # construction. Without this its concurrency limits would only pick
            # up an edit at the next restart, and the owner would think the
            # saved value had been ignored.
            self.mp.settings.max_positions = new_settings.mp_max_positions
            self.mp.settings.max_trades_per_day = new_settings.mp_max_trades_per_day
            self.blast.apply_settings(new_settings)
            for smart in _smart_lanes(self):
                smart.apply_settings(new_settings)

            if timeframe_changed:
                self.history.clear()
                self.history_errors.clear()
                for symbol in self.all_symbols:
                    try:
                        stored = await asyncio.to_thread(
                            load_chart_history,
                            new_settings.research_database_path,
                            symbol,
                            new_settings.timeframe_seconds,
                            new_settings.fast_period,
                            new_settings.slow_period,
                            new_settings.signal_period,
                            new_settings.bb_period,
                            new_settings.bb_deviations,
                            new_settings.kama_period,
                            new_settings.kama_fast,
                            new_settings.kama_slow,
                            new_settings.kama_rsi_period,
                            new_settings.kama_roc_period,
                            source_row_limit=LIVE_WARMUP_SOURCE_ROWS,
                        )
                        self.history[symbol].extend(stored["candles"][-500:])
                    except Exception as exc:
                        self.history_errors[symbol] = str(exc)

            # Every symbol, not just the option legs. Rebuilding the manager
            # above empties strategy.points, and re-warming only the options
            # left all 212 spots, indices and futures with no indicator state
            # until their next closed bar -- which pre-open is a long wait.
            # Saving any setting at 07:50 dropped health to 478/690 and blanked
            # the watchlist MACD column for the rest of the morning. The bars
            # are already in self.history, so this is an in-memory replay and
            # costs no broker request.
            for symbol in self.all_symbols:
                for candle in self.history.get(symbol, ()):
                    self.strategy.on_closed_candle(candle, warmup=True)
            self.events.publish("snapshot_required", {"reason": "strategy_settings_changed"})

    async def stop(self) -> None:
        for name in ("_startup_task", "_expiry_flatten_task", "_tick_maintenance_task",
                     "_blast_task", "_history_prune_task", "_desk_status_task", "_self_heal_task"):
            task = getattr(self, name, None)
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                setattr(self, name, None)
        if self._rollover_task:
            self._rollover_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._rollover_task
            self._rollover_task = None
        if self._watchdog_task:
            self._watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._watchdog_task
            self._watchdog_task = None
        if self._session_close_task:
            self._session_close_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._session_close_task
            self._session_close_task = None
        for name in ("_nightly_task", "_chain_task", "_stock_chain_task"):
            task = getattr(self, name)
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                setattr(self, name, None)
        if self._dispersion_task:
            self._dispersion_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._dispersion_task
            self._dispersion_task = None
        if self._lag_task:
            self._lag_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._lag_task
            self._lag_task = None
        await self.candle_writer.stop()
        await self.tick_store.stop()
        await self.disconnect_feed()
        await self.mp.stop()
        await self.blast.stop()
        if getattr(self, "smart_lanes", None) is not None:
            await self.smart_lanes.stop()
        if getattr(self, "agentic", None) is not None:
            await self.agentic.stop()
            if self.agentic.telegram is not None:
                await self.agentic.telegram.stop()
        if getattr(self, "spot_lane", None) is not None:
            await self.spot_lane.stop()
        if self.bus is not None:
            await self.bus.stop()
            if self.flow_snapshots is not None:
                await self.flow_snapshots.stop()
        with suppress(Exception):
            self.repository.save_position_excursions(self.portfolio.positions.values())
        self.repository.close()
        self.status = "stopped"
        self_heal.mark_clean_shutdown(self.settings.database_path, self.settings.engine_role)

    async def _manage_analysis_holdings(self, tick: Tick, exchange_at: datetime) -> None:
        """Analysis legs cannot enter, but holdings still receive risk handling."""
        managers = []
        if tick.symbol in self.portfolio.positions:
            managers.append(("macd", self.execution))
        if tick.symbol in self.blast.portfolio.positions:
            managers.append(("blast", self.blast.execution))
        managers.extend((lane.name, lane.execution) for lane in _smart_lanes(self)
                        if tick.symbol in lane.portfolio.positions)
        current_print = (exchange_at.astimezone(IST).date() == self.last_tick_at.astimezone(IST).date()
                         and in_session(tick.symbol, exchange_at)
                         and in_session(tick.symbol, self.last_tick_at))
        for name, manager in managers:
            try:
                if current_print:
                    await manager.on_tick(tick.symbol, tick.ltp, self.last_tick_at)
                else:
                    manager.portfolio.mark(tick.symbol, tick.ltp)
            except ExitRetryWait:
                pass
            except Exception as exc:
                self.lane_errors[name] = {"count": self.lane_errors.get(name, {}).get("count", 0) + 1,
                                          "last": f"{tick.symbol}: {type(exc).__name__}: {exc}"[:300],
                                          "at": datetime.now(UTC).isoformat()}
        if managers and current_print:
            try:
                await self._exit_expiring_position(tick.symbol)
            except Exception as exc:
                self.lane_errors["expiry"] = {"count": self.lane_errors.get("expiry", {}).get("count", 0) + 1,
                                              "last": f"{tick.symbol}: {type(exc).__name__}: {exc}"[:300],
                                              "at": datetime.now(UTC).isoformat()}

    async def close_smart_paper_position(self, name: str, symbol: str, expected_quantity: int):
        """Explicit user close, priced from a new quote request, with a durable SELL."""
        lane = next((lane for lane in _smart_lanes(self) if lane.name == name), None)
        if lane is None:
            raise ValueError("Unknown smart lane")
        if self.settings.execution_mode != "paper" or lane.execution.settings.execution_mode != "paper":
            raise PermissionError("This close action is paper-only")
        position = lane.portfolio.positions.get(symbol)
        # A spot-lane short holds a negative quantity and is closed by a BUY.
        if position is None or position.quantity != expected_quantity or expected_quantity == 0:
            raise ValueError("Holding quantity changed; refresh the position before closing")
        quotes = await self.broker.quotes([symbol])
        quote = quotes.get(symbol)
        if quote is None or not math.isfinite(quote.ltp) or quote.ltp <= 0:
            raise RuntimeError("No valid broker quote available for the requested close")
        position = lane.portfolio.positions.get(symbol)
        if position is None or position.quantity != expected_quantity:
            raise ValueError("Holding quantity changed during quote refresh")
        lane.execution.set_quote(symbol, quote.ltp, datetime.now(UTC))
        lane.execution._exit_blocked_until.pop(symbol, None)     # the owner's click is never held by a back-off
        lane.execution.arm_exit(symbol, "MANUAL_USER_CLOSE")
        return await lane.execution.submit(symbol, "SELL" if expected_quantity > 0 else "BUY", abs(expected_quantity))

    async def on_tick(self, tick: Tick) -> None:
        if not tick.symbol or tick.ltp <= 0:
            return
        self.ticks_total += 1
        # Arrival time, not the tick's exchange timestamp — Fyers stamps can
        # trail wall clock by minutes, which made a live feed read as stale.
        self.last_tick_at = datetime.now(UTC)
        now = time.monotonic()
        self._tick_times.append(now)
        exchange_at = tick.timestamp if tick.timestamp.tzinfo else tick.timestamp.replace(tzinfo=UTC)
        previous = self.latest_ticks.get(tick.symbol)
        skew = (exchange_at - self.last_tick_at).total_seconds()
        if skew > FUTURE_TICK_TOLERANCE_SECONDS:
            self.future_ticks_ignored += 1
            first_report = self.clock_skew_seconds() is None
            self._last_future_tick = (now, skew)
            if first_report:
                self.events.publish("broker", self.broker_status())
            return
        self.entry_gate.days.observe(tick.symbol, tick.ltp, exchange_at)
        if previous is not None:
            previous_at = previous.timestamp if previous.timestamp.tzinfo else previous.timestamp.replace(tzinfo=UTC)
            if exchange_at < previous_at:
                # A delayed print must not replace a newer quote or trigger a
                # resting limit, stop, scale-out, or expiry liquidation.
                return
        self.latest_ticks[tick.symbol] = tick
        analysis_only = tick.symbol in getattr(self, "analysis_option_symbols", set())
        if self.desk_local:
            await self._desk_on_tick(tick, analysis_only)
        minute_closed = None
        if exchange_at.astimezone(IST).date() >= self.last_tick_at.astimezone(IST).date():
            # A print stamped on an earlier day is the morning snapshot or a pre-open quote carrying the last
            # session's trade time. Bucketed by that stamp it rewrote the previous close: on 2 Nov 2026 the Fri
            # 15:39 bar of 962 contracts took Monday's pre-open price and the whole day's volume (RELIANCE
            # 1160 PE closed 23.55, stored 7.5).
            _, minute_closed = self.minute_candles.on_tick(tick)
        if minute_closed and in_session(minute_closed.symbol, minute_closed_moment(minute_closed)):
            self.candle_writer.add(minute_closed, resolve_expiry(minute_closed.symbol, self.contract_selector.contracts))
            self._whale_interest_bar(minute_closed)
            if not analysis_only or minute_closed.symbol in self.blast.portfolio.positions:
                # The blast lane screens one-minute bars whatever the strategy
                # timeframe is; that is the resolution its screen was measured at.
                # A holding that left today's ladder still needs its bars: its
                # HMA-below-KAMA exit is judged on them.
                await self.blast.on_minute_bar(minute_closed, tick.timestamp)
        if analysis_only:
            await self._manage_analysis_holdings(tick, exchange_at)
            # latest_ticks was updated above, so the next snapshot still carries
            # this leg's true price — only the per-tick frame is rationed.
            if now - self._analysis_published.get(tick.symbol, 0.0) >= ANALYSIS_TICK_PUBLISH_SECONDS:
                self._analysis_published[tick.symbol] = now
                await self._publish_tick(tick, analysis_only=True)
            # Minute bars have already been captured above. Ratio legs do not
            # enter the strategy-timeframe candle engine, which is the hard
            # boundary preventing an analysis contract from firing an order.
            return
        await self._publish_tick(tick, analysis_only=False)
        current_session_print = (
            exchange_at.astimezone(IST).date() == self.last_tick_at.astimezone(IST).date()
            and in_session(tick.symbol, exchange_at)
        )
        if in_session(tick.symbol, self.last_tick_at) and current_session_print:
            # EntryFlow checks each symbol's current-session snapshot, rolls its day even while using the desk,
            # and resumes a labelled local interval when that symbol's snapshot becomes stale.
            if is_underlying(tick.symbol):
                try:
                    self.entry_flow.on_tick(tick, now=self.last_tick_at,
                                            use_snapshots=getattr(self.settings, "entry_flow_source", "desk") == "desk")
                except Exception as exc:  # noqa: BLE001 -- measurement must not cost the feed
                    self.entry_flow.error = f"{type(exc).__name__}: {exc}"[:200]
            # Measure executable quote freshness by receipt, consistently
            # with feed health; Fyers exchange stamps can lag on valid quotes.
            steps = [("macd", lambda: self.execution.on_tick(tick.symbol, tick.ltp, self.last_tick_at)),
                     ("blast", lambda: self.blast.on_tick(tick.symbol, tick.ltp, self.last_tick_at, session_open=True)),
                     ("expiry", lambda: self._exit_expiring_position(tick.symbol))]
            steps += [(smart.name, (lambda smart=smart: smart.execution.on_tick(tick.symbol, tick.ltp, self.last_tick_at)))
                      for smart in _smart_lanes(self)]
            for lane, step in steps:
                try:
                    await step()
                except ExitRetryWait:
                    pass           # a rejected exit backing off (execution.EXIT_RETRY_SECONDS); counted in health
                except Exception as exc:  # noqa: BLE001 -- one lane must not stop the feed or the other lanes
                    self.lane_errors[lane] = {"count": self.lane_errors.get(lane, {}).get("count", 0) + 1,
                                              "last": f"{tick.symbol}: {type(exc).__name__}: {exc}"[:300],
                                              "at": datetime.now(UTC).isoformat()}
            if self.settings.extreme_booking_enabled and (tick.symbol in EXTREME_SPOTS or self.extreme_booker.pending):
                try:
                    books = [self.execution, *[smart.execution for smart in _smart_lanes(self)]]
                    if tick.symbol in EXTREME_SPOTS:
                        await self.extreme_booker.on_index_tick(tick.symbol, tick.ltp, books)
                    else:
                        await self.extreme_booker.on_option_tick(tick.symbol, books)
                except Exception as exc:  # noqa: BLE001 -- booking must not stop the feed
                    self.lane_errors["extreme_booking"] = {"count": self.lane_errors.get("extreme_booking", {}).get("count", 0) + 1,
                                                           "last": f"{tick.symbol}: {type(exc).__name__}: {exc}"[:300],
                                                           "at": datetime.now(UTC).isoformat()}
        else:
            # Prior-session and off-session prints may value a holding, but
            # must not refresh the executable quote or trigger paper risk.
            self.portfolio.mark(tick.symbol, tick.ltp)
            self.blast.portfolio.mark(tick.symbol, tick.ltp)
            for smart in _smart_lanes(self):
                smart.portfolio.mark(tick.symbol, tick.ltp)
        if tick.symbol in self.portfolio.positions:
            self.contract_selector.retain(tick.symbol)
        # Candle building and SIGNAL GENERATION are both session-gated. Gating
        # only execution still let pre-open re-broadcasts of yesterday's close
        # create bars and fire signals — an INDIGO alert was evaluated on a
        # candle stamped 15:30 the previous day, and a NIFTY signal was stored
        # at 09:00 before the session existed.
        current, closed = self.candles.on_tick(tick)
        if current is None:
            return
        self.events.publish("candle", current)
        if now - self._last_equity_save >= 5:
            self._last_equity_save = now
            self.repository.save_equity_point(self.portfolio.snapshot())
            self.repository.save_position_excursions(self.portfolio.positions.values())
        if closed:
            self.candles_closed += 1
            if closed.symbol in self._warming_symbols:
                self._warm_live_candles[closed.symbol].append(closed)
                return
            self.history[closed.symbol].append(closed)
            signal = self.strategy.on_closed_candle(closed)
            # Invalidation is judged on the bar that just closed, using the
            # same MACD the entry was taken from, before any new signal acts.
            point = self.strategy.points.get(closed.symbol)
            if point is not None:
                await self.execution.on_closed_bar(closed.symbol, point.macd)
                for smart in _smart_lanes(self):
                    try:
                        await smart.execution.on_closed_bar(closed.symbol, point.macd)
                    except Exception as exc:  # noqa: BLE001
                        smart.error = f"invalidation {closed.symbol}: {type(exc).__name__}: {exc}"[:300]
            if point is not None and segment(closed.symbol) == "cash":
                history = self.strategy.point_history.get(closed.symbol)
                indicator = self.strategy.indicators.get(closed.symbol)
                if history and len(history) >= 2 and indicator is not None and indicator.count > self.settings.slow_period:
                    self._spot_cross(closed.symbol, history[-2].macd, point.macd, closed.close, closed.timestamp,
                                     intrabar=False)
            if signal and self._signal_bar_is_current(signal) and self.repository.save_signal(signal):
                self.signals_emitted += 1
                await self._route_signal(signal)
        # Commit the prior bar before previewing the new one. Otherwise the
        # boundary tick skips a close and can duplicate the prior zero-cross.
        signal = (None if current.symbol in self._warming_symbols
                  else self.strategy.on_live_candle(current))
        if signal and self._signal_bar_is_current(signal) and self.repository.save_signal(signal):
            self.signals_emitted += 1
            await self._route_signal(signal)
        if (getattr(self, "spot_lane", None) is not None and segment(current.symbol) == "cash"
                and current.symbol not in self._warming_symbols):
            indicator = self.strategy.indicators.get(current.symbol)
            if indicator is not None and indicator.count + 1 > self.settings.slow_period:
                value = indicator.preview(current.close)
                self._spot_cross(current.symbol, value.previous_macd, value.macd, current.close, current.timestamp,
                                 intrabar=True)

    def _spot_cross(self, symbol: str, previous: float | None, macd: float, price: float, bucket: int, *,
                    intrabar: bool) -> None:
        """The stock's MACD crossing zero, up or down, is the spot lane's entry (its future, long or short)."""
        spot = getattr(self, "spot_lane", None)
        if spot is None:
            return
        try:
            signal = spot.on_macd(symbol, previous, macd, price, bucket, intrabar=intrabar)
            if signal is not None and self._signal_bar_is_current(signal):
                spot.admit(signal)
        except Exception as exc:  # noqa: BLE001 -- a lane fault must never stop the feed
            spot.error = f"intake {symbol}: {type(exc).__name__}: {exc}"[:300]

    async def _route_signal(self, signal) -> None:
        """Option BUY signals go through the entry gate; everything else straight to execution.

        Every tradable option BUY is also offered to the smart lanes, whatever
        the MACD lane does with it -- each lane keeps its own book. Features are
        computed once: building them counts the signal into the crowd tally.
        """
        gate = self.entry_gate
        gate.settings = self.settings
        contract = self.contract_selector.contracts.get(signal.symbol)
        features = None
        feature_error = None
        option_buy = (signal.side == "BUY" and contract is not None
                      and signal.symbol in self.execution.tradable_symbols)
        if option_buy:
            smart_features = None
            try:
                features = self._entry_features(signal, contract)
                smart_features = {**features, 'underlying': contract.spot_symbol,
                                  'strike': float(contract.strike), 'expiry': str(contract.expiry),
                                  'features_available_at': datetime.now(UTC).isoformat()} if features else features
            except Exception as exc:  # noqa: BLE001 -- a scoring fault must not cost the entry
                feature_error = f"features {signal.symbol}: {type(exc).__name__}: {exc}"[:300]
            for smart in _smart_lanes(self):
                try:
                    smart.on_macd_signal(signal, contract.option_type, smart_features)
                except Exception as exc:  # noqa: BLE001
                    smart.error = f"intake {signal.symbol}: {type(exc).__name__}: {exc}"[:300]
        if (not gate.enabled or not option_buy or not self.settings.auto_trade
                or signal.symbol in self.portfolio.positions):
            await self.execution.on_signal(signal)
            return
        if features is None:
            gate.error = feature_error
            await self.execution.on_signal(signal)
            return
        gate.admit(signal, features)

    def _whale_interest_bar(self, candle) -> None:
        """Score a closed minute for whale interest; reload the baseline on a new day; export once a minute."""
        interest = self.whale_interest
        today = datetime.now(IST).date().isoformat()
        if interest.day != today and not self._whale_interest_loading:
            self._whale_interest_loading = True
            task = asyncio.create_task(self._load_whale_interest(today))
            self._whale_tasks.add(task)
            task.add_done_callback(self._whale_tasks.discard)
        try:
            interest.on_minute_bar(candle)
        except Exception as exc:  # noqa: BLE001 -- a context feature must not cost the feed
            interest.error = f"{candle.symbol}: {type(exc).__name__}: {exc}"[:200]
        minute = int(candle.timestamp) // 60
        if interest.day == today and self._whale_interest_exported != minute:
            self._whale_interest_exported = minute
            from .whale_interest import write_export
            path = Path(self.settings.runtime_settings_path).parent / "shadow_lanes" / "whale_interest.json"
            task = asyncio.create_task(asyncio.to_thread(write_export, path, interest.export_payload()))
            self._whale_tasks.add(task)
            task.add_done_callback(self._whale_tasks.discard)
            task.add_done_callback(lambda done: done.cancelled() or done.exception() is None
                                   or setattr(interest, "error", f"export: {done.exception()}"[:200]))

    async def _load_whale_interest(self, today: str) -> None:
        try:
            await asyncio.to_thread(self.whale_interest.load, self.settings.research_database_path, today)
            self.whale_interest.error = None
        except Exception as exc:  # noqa: BLE001
            self.whale_interest.error = f"baseline: {type(exc).__name__}: {exc}"[:200]
            self.whale_interest.day = today      # retried tomorrow, not on every bar
        finally:
            self._whale_interest_loading = False

    def _offer_blast_candidate(self, row: dict) -> None:
        for smart in _smart_lanes(self):
            smart.on_blast_candidate(row)

    def _queued_macd_entry_rejection(self, signal) -> str | None:
        """Recheck a ranked BUY against the current bar and executable quote.

        Ranking delays an entry. A cross that faded during that wait, or whose
        originating bar has been superseded, is no longer an entry candidate.
        This check is read-only and does not consume the strategy's one-shot
        marker; risk exits keep their own execution path.
        """
        symbol = signal.symbol
        bucket = getattr(signal, "evaluated_candle_timestamp", None)
        if bucket is None or symbol in self._warming_symbols:
            return "NO_CURRENT_MACD"
        if not self._signal_bar_is_current(signal):
            return "BAR_STALE"
        indicator = self.strategy.indicators.get(symbol)
        current = self.candles.current.get(symbol)
        point = self.strategy.points.get(symbol)
        if indicator is None or current is None or indicator.count + 1 <= self.settings.slow_period:
            return "NO_CURRENT_MACD"
        if current.timestamp != bucket and (point is None or point.timestamp != bucket):
            return "BAR_STALE"
        try:
            price = self.execution._fresh_price(symbol)
        except RuntimeError:
            return "STALE_PRICE"
        preview = indicator.preview(price)
        if preview.macd <= 0:
            return "MACD_FADED"
        if current.timestamp == bucket:
            if preview.previous_macd is None or preview.previous_macd > 0:
                return "MACD_FADED"
        elif point.macd <= 0:
            return "MACD_FADED"
        if any((self.settings.require_kama_confirmation,
                self.settings.require_kama_rsi_confirmation,
                self.settings.require_kama_roc_confirmation)):
            kama_indicator = copy.deepcopy(self.strategy.kama[symbol])
            old_kama = kama_indicator.value
            kama = kama_indicator.update(price)
            kama_ok = kama is not None and old_kama is not None and price > kama > old_kama
            rsi_ok = roc_ok = False
            if self.settings.require_kama_rsi_confirmation:
                rsi = copy.deepcopy(self.strategy.kama_rsi[symbol]).update(kama) if kama is not None else None
                rsi_ok = rsi is not None and rsi >= self.settings.kama_rsi_min
            if self.settings.require_kama_roc_confirmation:
                roc = copy.deepcopy(self.strategy.kama_roc[symbol]).update(kama) if kama is not None else None
                roc_ok = roc is not None and roc > self.settings.kama_roc_min
            if not self.strategy._confirmations_pass(kama_ok, rsi_ok, roc_ok):
                return "CONFIRMATION_FADED"
        return None

    def _entry_context(self, symbol: str) -> dict | None:
        """What every lane logs at a new entry: the underlying's session order flow and the decision matrix's grades."""
        flow = self.entry_flow.context(symbol, now=datetime.now(UTC),
                                       use_snapshots=getattr(self.settings, "entry_flow_source", "desk") == "desk")
        grade = self.entry_grader.grade(symbol)
        if flow is None and grade is None:
            return None
        return {**(flow or {}), "matrix": grade}

    def _smart_macd_confirm(self, symbol: str, bucket: int | None, price: float) -> bool | None:
        """Is the cross still standing? The committed bar once the signal bar has closed, else the intrabar preview."""
        point = self.strategy.points.get(symbol)
        if bucket is not None and point is not None and point.timestamp >= bucket:
            return point.macd > 0
        indicator = self.strategy.indicators.get(symbol)
        if indicator is None or not price or price <= 0:
            return None
        return indicator.preview(price).macd > 0

    def _smart_entry_context(self, item, now: datetime) -> dict:
        """Read existing quotes/indicators/flow; no broker request or signal/crowd recomputation."""
        from .entry_scorer import NIFTY
        contract = self.contract_selector.contracts.get(item.symbol)
        live, sources = {}, {}
        def tick(symbol):
            row = self.latest_ticks.get(symbol)
            age = (now - row.timestamp).total_seconds() if row else None
            if row and age is not None and 0 <= age <= self.settings.order_quote_max_age_seconds:
                sources[symbol] = row.timestamp.isoformat()
                return row
            return None
        def ret(a, b):
            return a / b - 1 if a and b and a > 0 and b > 0 else None
        option = tick(item.symbol)
        indicator = self.strategy.indicators.get(item.symbol)
        if option and indicator:
            point = indicator.preview(option.ltp)
            live['macd_pct'] = point.macd / option.ltp
        if contract:
            side = 1 if contract.option_type == 'CE' else -1
            spot, nifty = tick(contract.spot_symbol), tick(NIFTY)
            opened, _, _ = self.entry_gate.days.get(contract.spot_symbol)
            for key, value in (
                ('u_ret_open', ret(spot.ltp, opened) if spot else None),
                ('u_vwap', ret(spot.ltp, spot.avg_trade_price) if spot else None),
                ('nifty_ret_prev', ret(nifty.ltp, nifty.prev_close) if nifty else None)):
                live[key] = side * value if value is not None else None
        flow = self.entry_flow.context(item.symbol, now=now,
            use_snapshots=getattr(self.settings, 'entry_flow_source', 'desk') == 'desk')
        return {'live': live, 'quote_sources': sources, 'observed_at': now.isoformat(),
                'flow': flow, 'flow_use': 'diagnostic_only; not an entry veto or direction predictor'}

    def _entry_features(self, signal, contract) -> dict:
        from .entry_scorer import NIFTY, build_features
        gate = self.entry_gate
        symbol = signal.symbol
        spot_symbol = contract.spot_symbol
        spot_tick = self.latest_ticks.get(spot_symbol)
        nifty_tick = self.latest_ticks.get(NIFTY)
        option_tick = self.latest_ticks.get(symbol)
        spot_open, _, _ = gate.days.get(spot_symbol)
        day_open, day_high, day_low = gate.days.get(symbol)
        bucket = signal.evaluated_candle_timestamp or int(datetime.now(UTC).timestamp())
        same, other = gate.crowd(int(bucket), contract.option_type)
        # A closed-bar signal has already appended its own positive point.
        # The model's dip and slope features describe the history BEFORE the
        # signal bar, consistently with the intrabar path and training data.
        history = [point.macd for point in (self.strategy.point_history.get(symbol) or ())
                   if point.timestamp < bucket]
        return build_features(
            price=self.execution.last_prices.get(symbol) or signal.price,
            side=1 if contract.option_type == "CE" else -1, strike=float(contract.strike), expiry=str(contract.expiry),
            now=datetime.now(UTC), is_index=spot_symbol.endswith("-INDEX"),
            spot=spot_tick.ltp if spot_tick else None, spot_prev_close=spot_tick.prev_close if spot_tick else None,
            spot_open=spot_open, spot_close_5d=gate.close_5d.get(spot_symbol),
            spot_vwap=spot_tick.avg_trade_price if spot_tick else None,
            nifty=nifty_tick.ltp if nifty_tick else None, nifty_prev_close=nifty_tick.prev_close if nifty_tick else None,
            day_open=day_open, day_high=day_high, day_low=day_low,
            option_prev_close=option_tick.prev_close if option_tick else None,
            volume_today=float(option_tick.volume) if option_tick else 0.0,
            prior_day_volume=gate.prior_volume.get(symbol, 0.0),
            macd_history=history, macd_preview=float(signal.macd), crowd_same=same, crowd_other=other)

    async def _validated_context_indices(self) -> list[str]:
        """Context-only sector/broad indices the broker actually prices (config.context_indices_csv).

        One quotes request validates them all, once per day; a name the broker does not know is dropped rather
        than handed to the websocket subscription. If the check itself fails, nothing extra is subscribed.
        """
        today = datetime.now(IST).date().isoformat()
        cached = getattr(self, "_context_indices_checked", None)
        if cached and cached[0] == today:
            return cached[1]
        candidates = self.settings.context_indices
        if not candidates:
            return []
        try:
            quotes = await asyncio.wait_for(self.broker.quotes(candidates), FEED_STEP_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 -- context only; never block the feed
            self.context_indices_status = {"checked": today, "error": f"{type(exc).__name__}: {exc}"[:200]}
            return []
        valid = [s for s in candidates if (tick := quotes.get(s)) is not None and tick.ltp and tick.ltp > 0]
        self.context_indices_status = {"checked": today, "valid": valid, "rejected": [s for s in candidates if s not in valid]}
        self._context_indices_checked = (today, valid)
        return valid

    async def _backfill_context_indices(self) -> None:
        """About 100 days of minute history for context indices that have less, so 1W/1M moves and a daily RRG exist at once."""
        from .self_heal import last_captured
        vix = [self.settings.vix_symbol] if self.settings.vix_symbol else []
        for symbol in list(dict.fromkeys([*getattr(self, "context_indices", []), *vix])):
            try:
                last = await asyncio.to_thread(last_captured, self.settings.research_database_path, symbol)
                count = await asyncio.to_thread(self._stored_minutes, symbol)
                if count >= 375 * 55 and last and (datetime.now(UTC) - last) < timedelta(days=4):
                    continue
                end = datetime.now(IST)
                rows = await self.broker.history_range(symbol, 60, end - timedelta(days=99), end)   # Fyers caps 1-minute ranges at 100 days
                await asyncio.to_thread(store_historical_candles, self.settings.research_database_path, rows)
                await asyncio.sleep(0.3)
            except Exception as exc:  # noqa: BLE001
                self.context_indices_status = {**getattr(self, "context_indices_status", {}),
                                               "backfill_error": f"{symbol}: {type(exc).__name__}: {exc}"[:200]}

    def _stored_minutes(self, symbol: str) -> int:
        with sqlite3.connect(f"file:{self.settings.research_database_path}?mode=ro", uri=True, timeout=30) as db:
            return db.execute("SELECT COUNT(*) FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60",
                              (symbol,)).fetchone()[0]

    async def atm_heatmap(self, as_of: date | None = None) -> dict:
        """ATM CE/PE premium changes over 1H..1M per underlying (atm_heatmap.py); cached for 30 s.

        ``as_of`` replays the map at that day's close with the ATM contracts of that day (a past day never
        changes, so its answer is kept)."""
        from . import atm_heatmap
        context = list(dict.fromkeys([*(self.context_indices or self.settings.context_indices),
                                      *([self.settings.vix_symbol] if self.settings.vix_symbol else [])]))
        if as_of is not None:
            as_of = atm_heatmap._first_session_on_or_after(as_of)   # a holiday or weekend means the next session
        if as_of is not None and as_of < datetime.now(IST).date():
            history = self.__dict__.setdefault("_atm_heatmap_history", {})
            if as_of not in history:
                path = self.settings.research_database_path
                contracts = await asyncio.to_thread(atm_heatmap.historical_contracts, path, as_of)
                close = datetime.combine(as_of, atm_heatmap.CLOSE, IST)
                result = await asyncio.to_thread(atm_heatmap.build, path, contracts, {}, close, context)
                if not result["rows"]:
                    return result
                history[as_of] = result
            return history[as_of]
        cached = getattr(self, "_atm_heatmap_cache", None)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        contracts = [
            {"underlying": c.underlying, "spot_symbol": c.spot_symbol, "option_type": c.option_type,
             "symbol": c.symbol, "strike": c.strike, "expiry": c.expiry}
            for c in self.contract_selector.contracts.values()
            if getattr(c, "moneyness", "ATM") == "ATM" and not getattr(c, "analysis_only", False)
        ]
        if not contracts:
            # No live selection yet (still connecting, or the day's broker token is not in): use the last
            # saved selection, so the map still shows the most recent ATM contracts off-hours.
            try:
                saved = json.loads(Path(self.settings.contract_snapshot_path).read_text())
                contracts = [{key: row.get(key) for key in ("underlying", "spot_symbol", "option_type", "symbol", "strike", "expiry")}
                             for row in saved.get("contracts", [])
                             if str(row.get("moneyness") or "ATM").upper() == "ATM" and not row.get("analysis_only")]
            except (OSError, ValueError):
                contracts = []
        live = {symbol: tick.ltp for symbol, tick in self.latest_ticks.items() if tick.ltp and tick.ltp > 0}
        result = await asyncio.to_thread(atm_heatmap.build, self.settings.research_database_path, contracts, live, None, context)
        if result["rows"]:                       # never cache the empty answer of a still-selecting engine
            self._atm_heatmap_cache = (time.monotonic(), result)
        return result

    def _wheel_symbols(self) -> list[str]:
        broad = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "BSE:SENSEX-INDEX", "NSE:MIDCPNIFTY-INDEX"]
        return list(dict.fromkeys([*broad, *(self.context_indices or self.settings.context_indices),
                                   *[s for s in self.settings.symbols if s.endswith("-EQ")]]))

    async def sector_wheel(self) -> dict:
        """Own and NIFTY-relative 1/5/20/60-session returns (sector_wheel.py); cached 60 s. Daily closes are
        refreshed in the background once per completed session."""
        from . import sector_wheel
        task = getattr(self, "_wheel_refresh_task", None)
        if (task is None or task.done()) and getattr(self, "_wheel_refreshed_for", None) != sector_wheel.last_completed_session(datetime.now(IST)):
            async def run() -> None:
                report = await sector_wheel.refresh(self.broker, self.settings.research_database_path, self._wheel_symbols())
                self.wheel_refresh_report = {**report, "at": datetime.now(UTC).isoformat()}
                if not report["errors"] and not await asyncio.to_thread(
                        sector_wheel.stale_symbols, self.settings.research_database_path, self._wheel_symbols(), datetime.now(IST)):
                    self._wheel_refreshed_for = sector_wheel.last_completed_session(datetime.now(IST))
                self._sector_wheel_cache = None
            self._wheel_refresh_task = asyncio.create_task(run())
        cached = getattr(self, "_sector_wheel_cache", None)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        live = {symbol: tick.ltp for symbol, tick in self.latest_ticks.items() if tick.ltp and tick.ltp > 0}
        result = await asyncio.to_thread(sector_wheel.build, self.settings.research_database_path, self._wheel_symbols(), live)
        result["refreshing"] = not self._wheel_refresh_task.done()
        if result["rows"]:
            self._sector_wheel_cache = (time.monotonic(), result)
        return result

    async def trend_rotation(self, kind: str, timeframe: int) -> dict:
        from . import trend_rotation
        key = (kind, timeframe)
        cache = self.__dict__.setdefault("_trend_rotation_cache", {})
        tasks = self.__dict__.setdefault("_trend_rotation_tasks", {})
        if key in cache and time.monotonic() - cache[key][0] < 60:
            return cache[key][1]
        if key not in tasks or tasks[key].done():
            async def compute():
                if kind == "SPOT":
                    instruments = [{"symbol": s, "spot_symbol": s} for s in dict.fromkeys(
                        [*self.settings.symbols, *(self.context_indices or self.settings.context_indices)])
                        if s.endswith(("-EQ", "-INDEX"))]
                else:
                    # Reuse the current ATM identities, including saved selection during connection warm-up.
                    heatmap = await self.atm_heatmap()
                    instruments = [{"symbol": row[kind.lower()]["symbol"], "underlying": row["underlying"],
                                    "spot_symbol": row["spot_symbol"], "strike": row[kind.lower()]["strike"],
                                    "expiry": row["expiry"]}
                                   for row in heatmap["rows"] if kind.lower() in row]
                result = await asyncio.to_thread(trend_rotation.build, self.settings.research_database_path,
                                                 instruments, kind, timeframe)
                if instruments:
                    cache[key] = (time.monotonic(), result)
                return result
            tasks[key] = asyncio.create_task(compute())
        return await asyncio.shield(tasks[key])

    def warmup_status(self) -> dict:
        wanted = set(self.settings.symbols) | set(self.option_symbols)
        return {"required": len(wanted), "with_indicators": sum(symbol in self.strategy.points for symbol in wanted)}

    def _books(self) -> list[tuple[str, object]]:
        return [("macd", self.execution), ("blast", self.blast.execution),
                *[(lane.name, lane.execution) for lane in _smart_lanes(self)]]

    def exit_failures(self) -> list[dict]:
        """Automated exits rejected and backing off, every book (execution.EXIT_RETRY_SECONDS), newest first."""
        rows = [{**row, "lane": name} for name, execution in self._books()
                for row in getattr(execution, "exit_failures", {}).values()]
        return sorted(rows, key=lambda row: row.get("last_at") or "", reverse=True)

    async def _settle_expired(self) -> None:
        """Close holdings whose contract expired while the app was down (self_heal.settle_expired_books)."""
        try:
            settled = await self_heal.settle_expired_books(self._books(), self.settings.research_database_path)
        except Exception as exc:  # noqa: BLE001 -- recovery must not stop startup
            self.lane_errors["self_heal"] = {"at": datetime.now(UTC).isoformat(), "error": f"{type(exc).__name__}: {exc}"[:300]}
            return
        unresolved = [row for row in settled if row.get("error")]
        if unresolved:
            self.lane_errors["self_heal"] = {"at": datetime.now(UTC).isoformat(),
                "error": "expired settlement unresolved", "positions": unresolved}
        else:
            self.lane_errors.pop("self_heal", None)
        if settled:
            self.expired_settlements = [*settled, *self.expired_settlements][:50]
            self.events.publish("snapshot_required", {"reason": "expired_positions_settled"})

    async def _self_heal_loop(self) -> None:
        """After each session's close, settle any expiry the 15:20 exit could not sell (no fresh quote, app restarting)."""
        done: str | None = None
        while True:
            await asyncio.sleep(60)
            now = datetime.now(IST)
            if market_calendar.is_trading_day(now.date()) and (now.hour, now.minute) >= (15, 35) and done != now.date().isoformat():
                done = now.date().isoformat()
                await self._settle_expired()

    async def _exit_expiring_position(self, symbol: str) -> None:
        # The blast lane judges expiry from its own recorded contract, so a
        # holding that rolled out of the selector's band is still flattened.
        if symbol in self.blast.portfolio.positions:
            await self.blast.exit_if_expiring(symbol, datetime.now(ZoneInfo("Asia/Kolkata")))
        position = self.portfolio.positions.get(symbol)
        contract = self.contract_selector.contracts.get(symbol)
        if not position or not contract:
            return
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        try:
            expiry = datetime.fromisoformat(contract.expiry).date()
        except ValueError:
            return
        if expiry <= now.date() and (now.hour, now.minute) >= (15, 20):
            self.execution.arm_exit(symbol, "EXPIRY_EXIT_15_20_IST")
            await self.execution.submit(symbol, "SELL", position.quantity)

    async def _session_close_loop(self) -> None:
        """Commit the last bar of the day.

        A candle is emitted only when a tick from the next bucket arrives, and
        after 15:30 there is no next regular-session bucket — so the closing
        bar was built, displayed after a restart, and never evaluated live.
        """
        while True:
            await asyncio.sleep(20)
            now = datetime.now(IST)
            today = now.date().isoformat()
            if not market_calendar.is_trading_day(now.date()) or self._flushed_session == today:
                continue
            if now.time() < market_close(now.date()):
                continue
            self._flushed_session = today
            for candle in self.candles.flush():
                self.candles_closed += 1
                self.events.publish("candle", candle)
                if candle.symbol in self._warming_symbols:
                    self._warm_live_candles[candle.symbol].append(candle)
                    continue
                self.history[candle.symbol].append(candle)
                signal = self.strategy.on_closed_candle(candle)
                if signal and self.repository.save_signal(signal):
                    self.signals_emitted += 1
                    await self._route_signal(signal)
            for candle in self.minute_candles.flush():
                self.candle_writer.add(candle, resolve_expiry(candle.symbol, self.contract_selector.contracts))
            await self.mp.close_positions()

    def warmup_symbols(self) -> list[str]:
        """Symbols whose history is loaded -- possibly from the broker -- at connect.

        Blast holdings are absent on purpose. Every contract the blast lane
        can enter is already in option_symbols and warmed for the MACD lane; a
        holding that has since rolled out of the band needs only live ticks
        for its stop and trail, which the stream subscription supplies, and
        warming it would be a history request made for that lane alone.
        """
        # Boot priority (16 Oct 2026): what the books hold first, then the underlyings, then the band. Both
        # warm-up lanes queue in this order, so open positions have indicators first after a restart.
        return list(dict.fromkeys([
            *self.portfolio.positions, *self.desk_holdings(),
            *self.settings.symbols, *self.mp_spot_symbols(), *self.option_symbols,
        ]))

    def spot_price(self, spot_symbol: str | None) -> float | None:
        """Last traded price of an underlying, for premium-to-spot ratios."""
        if not spot_symbol:
            return None
        tick = self.latest_ticks.get(spot_symbol)
        price = tick.ltp if tick else None
        return price if price and price > 0 else None

    async def _blast_maintenance_loop(self) -> None:
        """Persist the blast journal's forward excursions and age out the desk.

        Watchers are in-memory between flushes; this is what makes the shadow
        sample survive a restart, and what finalises a candidate once its
        observation window has run out.
        """
        while True:
            await asyncio.sleep(30)
            try:
                await asyncio.to_thread(self.blast.flush_watchers)
                self.blast.sweep_closed_positions()
                for smart in _smart_lanes(self):
                    smart.sweep_closed_positions()
            except Exception as exc:  # noqa: BLE001 - journalling must not die
                self.blast.error = f"journal flush failed: {exc}"

    def dispersion_point(self):
        """CE/PE MACD breadth across the ATM cohort, right now.

        Reads the same two structures the terminal renders from, so the stored
        series and the on-screen number cannot drift apart.
        """
        cohort = [
            contract for contract in self.contract_selector.contracts.values()
            if contract.moneyness == "ATM"
        ]
        rows = []
        for contract in cohort:
            point = self.strategy.points.get(contract.symbol)
            if point is not None:
                rows.append((contract.option_type, point.timestamp, point.macd))
        return latest_breadth(rows, total=len(cohort))

    async def _dispersion_loop(self) -> None:
        """Persist the breadth of each completed bar.

        Sampled on a timer rather than hooked into on_tick: a bar closes once
        per contract, so the tick path would fire this a thousand times a bar,
        and the cohort is anyway incomplete until the last contract has
        printed.  Re-writing the same bar as stragglers arrive is deliberate —
        the upsert keeps the settled count, not the first partial one.
        """
        while True:
            await asyncio.sleep(20)
            try:
                point = self.dispersion_point()
                if point is None:
                    continue
                marker = (point.timestamp, point.ce_above, point.pe_above)
                if marker == self._dispersion_bar:
                    continue
                await asyncio.to_thread(
                    record_dispersion,
                    self.settings.research_database_path,
                    self.settings.timeframe_seconds,
                    [point],
                    DISPERSION_LIVE,
                )
                self._dispersion_bar = marker
                self._dispersion_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — breadth is context, never a trade gate
                self._dispersion_error = str(exc)

    async def _expiry_flatten_loop(self) -> None:
        """Close anything still held in a contract that expires today.

        Rolling the SELECTION forward is only half a rollover. Nothing in the
        book closes an expiring position: an option that settles simply stops
        ticking, so the paper portfolio would carry its final mark as an open
        position indefinitely and the equity curve would never record the
        outcome. On 25 Aug 2026 that was five positions and ~52k of unrealized
        P&L that would have frozen rather than resolved.

        Held positions are deliberately never rolled into the next series --
        they are flattened, and the strategy re-enters on its own signal.
        """
        while True:
            await asyncio.sleep(20)
            cutoff = self._expiry_flatten_time()
            if cutoff is None:
                continue
            now = datetime.now(IST)
            if not market_calendar.is_trading_day(now.date()):
                continue
            if (now.hour, now.minute) < cutoff:
                # A stock option past its T-2 last day (the app was down that afternoon) is closed at once.
                if not (market_open(now) and self._overdue_stock_positions(now)):
                    continue
            await self._flatten_expiring_positions(now)

    async def _flatten_expiring_positions(self, now: datetime) -> None:
        """Retry each still-held expiry until its paper exit actually fills."""
        macd_symbols = self.expiring_positions(now)
        mp_symbols = self.expiring_desk_positions()
        blast_symbols = self.blast.expiring_positions(now.date(), now)
        if not (macd_symbols or mp_symbols or blast_symbols):
            self._flattened_expiry_day = now.date().isoformat()
            return

        for symbol in macd_symbols:
            position = self.portfolio.positions.get(symbol)
            if not position or position.quantity <= 0:
                continue
            key = f"macd_expiry:{symbol}"
            self.execution.arm_exit(symbol, "EXPIRY_DAY_FLATTEN")
            try:
                await self.execution.submit(symbol, "SELL", position.quantity)
                if symbol in self.portfolio.positions:
                    raise RuntimeError("expiry exit has not filled")
            except Exception as exc:  # noqa: BLE001 - one failed book must not block the others
                self.position_mark_errors[symbol] = str(exc)
                self.rollover_errors[key] = str(exc)
                self.execution._armed_exits.discard(symbol)
                self.execution.exit_reasons.pop(symbol, None)
            else:
                self.position_mark_errors.pop(symbol, None)
                self.rollover_errors.pop(key, None)

        await self._flatten_expiring_desk(mp_symbols)

        for symbol in blast_symbols:
            key = f"blast_expiry:{symbol}"
            try:
                filled = await self.blast.flatten(symbol, "EXPIRY_DAY_FLATTEN")
                if not filled or symbol in self.blast.portfolio.positions:
                    raise RuntimeError(self.blast.error or "expiry exit has not filled")
            except Exception as exc:  # noqa: BLE001 - keep retrying the other books
                self.rollover_errors[key] = str(exc)
            else:
                self.rollover_errors.pop(key, None)

        pending = (self.expiring_positions(now)
                   or any(symbol in self.mp.portfolio.positions for symbol in mp_symbols)
                   or self.blast.expiring_positions(now.date(), now))
        self._flattened_expiry_day = None if pending else now.date().isoformat()
        self.events.publish("snapshot_required", {"reason": "expiry_day_flatten"})

    def expiring_positions(self, now: datetime | None = None) -> list[str]:
        """Open positions whose contract expires today or has already died, and stock options at or past their
        series' last day (T-2, rollover.stock_exit_due) once the flatten time has come."""
        cutoff = self._expiry_flatten_time() or (15, 10)
        expiring = []
        for symbol in self.portfolio.positions:
            expiry = self._expiry_of(symbol)
            remaining = days_to_expiry(expiry)
            if (remaining is not None and remaining <= 0) or stock_exit_due(symbol, expiry, now, cutoff):
                expiring.append(symbol)
        return expiring

    def _expiry_of(self, symbol: str) -> str | None:
        contract = self.contract_selector.contracts.get(symbol)
        return getattr(contract, "expiry", None) if contract else self.execution.contract_expiries.get(symbol)

    def _overdue_stock_positions(self, now: datetime) -> bool:
        """Any book still holding a stock option whose T-2 last day is already over."""
        held = [*self.portfolio.positions, *self.blast.portfolio.positions, *self.desk_holdings()]
        expiries = {**self.execution.contract_expiries, **self.blast.execution.contract_expiries}
        for smart in _smart_lanes(self):
            held += smart.held_symbols()
            expiries.update(smart.execution.contract_expiries)
        for symbol in held:
            if not is_stock_option(symbol):
                continue
            last = stock_last_day(self._expiry_of(symbol) or expiries.get(symbol))
            if last is not None and now.date() > last:
                return True
        return False

    async def _rollover_loop(self) -> None:
        """Refresh the daily ATM universe without rolling an open position into another contract."""
        ist = ZoneInfo("Asia/Kolkata")
        while True:
            await asyncio.sleep(30)
            # Closed records leave the desk at 08:00 IST on the next trading
            # morning; this loop already wakes often enough to notice.
            self.execution.sweep_closed_positions()
            now = datetime.now(ist)
            today = now.date().isoformat()
            # A Fyers access token can expire while this process remains up
            # overnight. Revalidate as soon as the exchange date changes,
            # before the 08:45 contract rollover and before the UI can claim
            # that yesterday's authenticated session is still connected.
            if self.status == "connected" and self._auth_validated_date != today:
                if not await self._validate_broker_session(today):
                    continue
            if self.status != "connected":
                continue
            if (now.hour, now.minute) < (8, 45) or now.date().isoformat() == self._selection_date:
                await self._refresh_inactive_contracts(now)
                continue
            async with self._reconfigure_lock:
                await self.disconnect_feed()
                self.history.clear()
                self.history_errors.clear()
                self.latest_ticks.clear()
                await self.connect_feed()
                self.events.publish("snapshot_required", {"reason": "daily_contract_rollover"})

    async def _refresh_inactive_contracts(self, now: datetime) -> dict:
        """Swap a spot's ladder for one around the live price when its ATM contract is not trading (owner, 28 Oct).

        Runs inside the F&O session window only, at most every contract_refresh_minutes, and only after the feed
        has been live long enough to judge inactivity. A changed ladder re-runs the same feed reconfiguration the
        daily rollover uses: held contracts stay subscribed, new ones warm from stored bars and option-chain marks.
        """
        every = int(getattr(self.settings, "contract_refresh_minutes", 0) or 0)
        inactive = int(getattr(self.settings, "contract_inactive_minutes", 15)) * 60
        local = now.astimezone(IST)
        if (not every or not fo_session_open(now) or not (9, 45) <= (local.hour, local.minute) <= (14, 30)
                or self._contracts_live_since is None
                or (datetime.now(UTC) - self._contracts_live_since).total_seconds() < inactive
                or (self._last_contract_refresh and (now - self._last_contract_refresh).total_seconds() < every * 60)):
            return {}
        self._last_contract_refresh = now
        spots = {symbol: tick.ltp for symbol, tick in self.latest_ticks.items()
                 if symbol in set(self.settings.symbols) and tick.ltp > 0}
        today = local.date()
        last_trade = {}
        for symbol, tick in self.latest_ticks.items():
            at = tick.timestamp if tick.timestamp.tzinfo else tick.timestamp.replace(tzinfo=UTC)
            if tick.volume > 0 and at.astimezone(IST).date() == today:
                last_trade[symbol] = at          # Fyers stamps a tick with its last TRADE time when it has one
        try:
            changed = await self.contract_selector.refresh(
                spots, last_trade, now, inactive_seconds=inactive,
                max_steps=float(getattr(self.settings, "contract_refresh_max_steps", 2.0)))
        except Exception as exc:  # noqa: BLE001 -- a refresh failure keeps the current ladders
            self.contract_refresh_status = {"at": local.isoformat(timespec="seconds"), "error": f"{type(exc).__name__}: {exc}"[:300]}
            return {}
        self.contract_refresh_status = {"at": local.isoformat(timespec="seconds"), "checked_spots": len(spots),
                                        "replaced": sorted(changed)}
        if changed:
            async with self._reconfigure_lock:
                down_from = time.monotonic()
                await self.disconnect_feed()
                self.history.clear()
                self.history_errors.clear()
                self.latest_ticks.clear()
                closed = time.monotonic() - down_from
                await self.connect_feed()
            steps = getattr(self, "connect_steps", {})
            self.contract_refresh_status["feed_down_seconds"] = (
                round(closed + steps["stream"], 1) if "stream" in steps else None)
            self.contract_refresh_status["disconnect_seconds"] = round(closed, 1)
            self.events.publish("snapshot_required", {"reason": "contract_refresh", "spots": sorted(changed)})
        return changed

    async def _validate_broker_session(self, today: str | None = None) -> bool:
        """Fail the visible broker state when its read-only auth probe is rejected."""
        try:
            authenticated = await self.broker.validate_session()
        except Exception:
            # A temporary network/profile outage is not proof of invalid auth.
            return True
        if authenticated:
            self._auth_validated_date = today or datetime.now(IST).date().isoformat()
            return True
        async with self._reconfigure_lock:
            if self.status != "connected":
                return False
            await self.disconnect_feed()
            self.status = "error"
            self.error = "Fyers daily access token is invalid or expired; reconnect Fyers"
            self.events.publish("broker", self.broker_status())
        return False

    def seconds_since_last_tick(self) -> float | None:
        if self.last_tick_at is None:
            return None
        return max(0.0, (datetime.now(UTC) - self.last_tick_at).total_seconds())

    def token_expired(self) -> bool:
        checker = getattr(self.broker, "token_expired", None)
        return bool(checker()) if callable(checker) else False

    def socket_auth_rejected(self) -> bool:
        """The socket refuses a token REST still accepts (an expired token is reported as that instead)."""
        checker = getattr(self.broker, "socket_auth_rejected", None)
        return bool(checker()) if callable(checker) and not self.token_expired() else False

    def feed_alive(self) -> bool:
        """A connected socket that has stopped delivering is not a live feed."""
        if self.status != "connected":
            return False
        if self.socket_auth_rejected():
            return False
        if self.token_expired():
            # Quiet outside market hours is normal; an expired session is not.
            # Reporting "connected" through a dead token showed a green header
            # at 07:05 for a feed that could not possibly serve the 09:15 open.
            return False
        if not regular_session_open():
            return True                      # silence outside the session is normal
        if self.clock_skew_seconds() is not None:
            return False                     # ticks arrive but are unusable
        age = self.seconds_since_last_tick()
        return age is not None and age <= FEED_STALE_SECONDS

    def clock_skew_seconds(self) -> float | None:
        """How far ahead of this host a recent print was stamped, if recently."""
        last = getattr(self, "_last_future_tick", None)
        if last is None:
            return None
        seen_at, skew = last
        return skew if time.monotonic() - seen_at <= CLOCK_SKEW_REPORT_SECONDS else None

    def broker_status(self, include_symbols: bool = True) -> dict:
        skew = self.clock_skew_seconds()
        skew_error = (f"Host clock is {skew:.0f}s behind exchange time; "
                      f"{getattr(self, 'future_ticks_ignored', 0)} ticks ignored. Sync the system clock."
                      if skew is not None else None)
        return {
            "name": self.broker.name,
            # Report what the feed is DOING, not what connect() once returned.
            "status": ("token_expired" if self.token_expired() and self.status == "connected"
                       else "socket_auth_failed" if self.socket_auth_rejected()
                       else self.status if self.feed_alive()
                       else "stale" if self.status == "connected" else self.status),
            "token_expires_at": (lambda e: e.isoformat() if e else None)(
                getattr(self.broker, "token_expiry", lambda: None)()),
            "token_expired": self.token_expired(),
            "socket_auth_failed": self.socket_auth_rejected(),
            "error": self.error or skew_error,
            "clock_skew_seconds": skew,
            "future_ticks_ignored": getattr(self, "future_ticks_ignored", 0),
            # The full list is ~35 KB; /health, polled by Docker every 10 s,
            # asks without it.
            **({"symbols": self.all_symbols} if include_symbols else {"symbol_count": len(self.all_symbols)}),
            "spot_count": len(self.settings.symbols),
            "option_count": len(self.option_symbols),
            "analysis_option_count": len(getattr(self, "analysis_option_symbols", set())),
            "tick_buffer": getattr(self.broker, "tick_buffer_status", lambda: None)(),
        }

    def option_greeks(self, contract) -> dict:
        """Implied vol, gamma and GEX for one contract from live marks.

        Uses the option's own last trade for the premium and the underlying's
        last trade for spot. Both exchange quotes must be fresh; the selection
        reference is an underlying price, never an option premium. Open
        interest prefers the live tick and falls back to the selection value.
        """
        blank = {"iv": None, "gamma": None, "gex": None}
        moment = datetime.now(UTC)
        option_tick = self.latest_ticks.get(contract.symbol)
        spot_tick = self.latest_ticks.get(contract.spot_symbol)
        for tick in (option_tick, spot_tick):
            if tick is None or not math.isfinite(tick.ltp) or tick.ltp <= 0:
                self._greeks_cache.pop(contract.symbol, None)
                return blank
            stamp = tick.timestamp if tick.timestamp.tzinfo else tick.timestamp.replace(tzinfo=UTC)
            age = (moment - stamp).total_seconds()
            if age < -5 or age > self.settings.order_quote_max_age_seconds:
                self._greeks_cache.pop(contract.symbol, None)
                return blank
        premium = option_tick.ltp
        spot = spot_tick.ltp
        open_interest = None
        if option_tick is not None and option_tick.open_interest is not None:
            open_interest = option_tick.open_interest
        elif contract.oi:
            open_interest = contract.oi
        if not spot or open_interest is None:
            self._greeks_cache.pop(contract.symbol, None)
            return blank
        inputs = (float(premium or 0), float(spot), int(open_interest), self.settings.risk_free_rate)
        now = time.monotonic()
        cached = self._greeks_cache.get(contract.symbol)
        # Throttle display solves for a few seconds, but elapsed time changes
        # expiry even if prices stand still. Quote validity is checked first.
        if cached and 0 <= now - cached[0] < GREEKS_CACHE_SECONDS:
            return cached[2]
        result = contract_gex(
            premium=inputs[0],
            spot=inputs[1],
            strike=float(contract.strike),
            expiry=str(contract.expiry),
            option_type=str(contract.option_type),
            open_interest=inputs[2],
            lot_size=int(contract.lot_size or 0),
            rate=inputs[3],
            now=moment,
        )
        self._greeks_cache[contract.symbol] = (now, inputs, result)
        return result

    def snapshot(self) -> dict:
        listed = set(self.settings.symbols) | set(self.contract_selector.contracts)
        desk_held = self.desk_holdings()
        held_contracts = _held_contract_lots(self)
        return json_value(
            {
                "broker": self.broker_status(),
                "config": {
                    "feed_mode": self.settings.feed_mode,
                    "timeframe_seconds": self.settings.timeframe_seconds,
                    "fast": self.settings.fast_period,
                    "slow": self.settings.slow_period,
                    "signal": self.settings.signal_period,
                    "bb": [self.settings.bb_period, self.settings.bb_deviations],
                    "kama": [self.settings.kama_period, self.settings.kama_fast, self.settings.kama_slow],
                    "kama_rsi": [self.settings.kama_rsi_period, self.settings.kama_rsi_min],
                    "kama_roc": [self.settings.kama_roc_period, self.settings.kama_roc_min],
                    "entry_confirmations": {
                        "macd_zero_cross_up": True,
                        "kama": self.settings.require_kama_confirmation,
                        "kama_rsi": self.settings.require_kama_rsi_confirmation,
                        "kama_roc": self.settings.require_kama_roc_confirmation,
                    },
                    "entry_volume_ratio": self.settings.entry_volume_ratio,
                    "signal_mode": self.settings.signal_mode,
                    "auto_trade": self.settings.auto_trade,
                    # The terminal's closed-book arithmetic rolls on trading days too.
                    "market_holidays": market_calendar.holiday_list(),
                },
                "spot_watchlist": [
                    {
                        "symbol": symbol,
                        "tick": self.latest_ticks.get(symbol),
                        "indicator": self.strategy.points.get(symbol),
                    }
                    for symbol in self.settings.symbols
                ],
                "option_watchlist": [
                    {
                        "symbol": contract.symbol,
                        "underlying": contract.underlying,
                        "spot_symbol": contract.spot_symbol,
                        "sector": sector_of(contract.spot_symbol),
                        "option_type": contract.option_type,
                        "strike": contract.strike,
                        "expiry": contract.expiry,
                        "selection_price": contract.selection_price,
                        "liquidity_volume": contract.volume,
                        "oi": contract.oi,
                        "retained": contract.retained or contract.symbol in held_contracts,
                        "moneyness": contract.moneyness,
                        "analysis_only": contract.analysis_only,
                        "lot_size": contract.lot_size,
                        "tick": self.latest_ticks.get(contract.symbol),
                        "indicator": self.strategy.points.get(contract.symbol),
                        **self.option_greeks(contract),
                    }
                    for contract in self.contract_selector.contracts.values()
                ],
                # Only the symbols the two lists above do not already carry.
                # This used to repeat every quote and indicator a third time:
                # 848 KiB of a 2.9 MiB frame, re-sent on every connect and on
                # every resync after a reconfigure or the daily rollover. The
                # indicator half was never read by any client at all.
                "watchlist": [
                    {"symbol": symbol, "tick": self.latest_ticks.get(symbol)}
                    for symbol in self.all_symbols
                    if symbol not in listed
                ],
                "contract_selection": {
                    "basis": "daily pre-open spot snapshot",
                    "selection_date": self._selection_date,
                    "rollover": "new ATM contracts selected daily after 08:45 IST; held contracts remain until exit; expiry exit at 15:20 IST",
                    "errors": self.contract_selector.errors,
                    "source": self.contract_selector.selection_source,
                    "intraday_refresh": {
                        "every_minutes": getattr(self.settings, "contract_refresh_minutes", 0),
                        "inactive_minutes": getattr(self.settings, "contract_inactive_minutes", 15),
                        "max_steps": getattr(self.settings, "contract_refresh_max_steps", 2.0),
                        "window": "09:45-14:30 IST", "last": self.contract_refresh_status,
                        "replaced": self.contract_selector.refreshes[-40:],
                    },
                },
                # Last two bars per symbol only — the full series for the
                # selected chart streams from /api/chart, and shipping 636×500
                # bars made the snapshot a 24MB frame on every client connect.
                "candles": {symbol: list(rows)[-2:] for symbol, rows in self.history.items()},
                "history_errors": self.history_errors,
                "strategy": {
                    **self.strategy.snapshot(),
                    # Durable log survives feed reconnects and restarts; the
                    # strategy object's in-memory list is rebuilt from scratch
                    # on every connect_feed.
                    "signals": self.repository.rows("signals", 200),
                },
                "execution": self.execution.snapshot(),
                "blast": self.blast.snapshot(),
            }
        )

    def day_baseline_equity(self) -> float | None:
        """Equity at the last recorded point before today (IST) — the anchor
        for day P&L. Cached per IST date."""
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        if self._day_baseline_date != today:
            midnight_utc = datetime.fromisoformat(f"{today}T00:00:00+05:30").astimezone(UTC).isoformat()
            point = self.repository.last_equity_point_before(midnight_utc)
            if point is None:
                # No history before today (fresh equity log) — anchor to the
                # first sample of the day so day P&L still means something.
                point = self.repository.first_equity_point_on_or_after(midnight_utc)
            # Funding added after the anchor (e.g. a raise after yesterday's
            # close) is capital, not today's profit.
            self._day_baseline_equity = (
                None if point is None else point[1] + self.repository.capital_added_after(point[0]))
            self._day_baseline_date = today
        return self._day_baseline_equity

    def book_greeks(self) -> dict:
        """Delta, theta and vega of every paper book's open options (option_greeks.book_greeks).

        The limits are gross premium caps, which do not show direction: on 14 Oct 2026 the MACD book held
        36 CE / 7 PE, Rs 5.5 Cr of net long underlying delta on Rs 1.07 Cr of equity. Display only; no
        rule reads it. Spot is the latest tick, else the last stored minute close (off-session).
        """
        from .option_agent import parse_option
        now = datetime.now(UTC)
        stored: dict[str, float | None] = {}

        def spot_price(spot_symbol: str) -> float | None:
            tick = self.latest_ticks.get(spot_symbol)
            if tick is not None and tick.ltp and tick.ltp > 0:
                return tick.ltp
            if spot_symbol not in stored:
                try:
                    with sqlite3.connect(f"file:{self.settings.research_database_path}?mode=ro", uri=True, timeout=5) as db:
                        row = db.execute("""SELECT close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
                                            ORDER BY timestamp DESC LIMIT 1""", (spot_symbol,)).fetchone()
                    stored[spot_symbol] = float(row[0]) if row else None
                except sqlite3.Error:
                    stored[spot_symbol] = None
            return stored[spot_symbol]

        def spot_of(symbol: str):
            parsed = parse_option(symbol)
            if not parsed or not parsed.get("spot"):
                return None
            spot = spot_price(parsed["spot"])
            return (spot, parsed) if spot else None

        books = [("macd", self.portfolio), ("blast", self.blast.portfolio),
                 *[(lane.name, lane.portfolio) for lane in _smart_lanes(self) if getattr(lane, "kind", None) != "spot"]]
        return {"as_of": now.isoformat(),
                "model": "Black-Scholes on spot, r 6.5%, no dividend; delta in rupees of underlying",
                "books": {name: book_greeks(list(portfolio.positions.items()), portfolio.snapshot()["equity"], spot_of, now)
                          for name, portfolio in books}}

    def health(self) -> dict:
        now = time.monotonic()
        rate_window = 10.0
        recent = sum(1 for stamp in self._tick_times if now - stamp <= rate_window)
        last_age = None
        if self.last_tick_at is not None:
            last_age = round(max(0.0, (datetime.now(UTC) - self.last_tick_at).total_seconds()), 1)
        return {
            "status": self.status,
            "error": self.error,
            "uptime_seconds": round((datetime.now(UTC) - self.started_at).total_seconds()),
            "feed_connects": self.feed_connects,
            "ticks_total": self.ticks_total,
            "tick_rate_per_second": round(recent / rate_window, 1),
            "last_tick_age_seconds": last_age,
            "candles_closed": self.candles_closed,
            "dispersion": {
                "last_bar": self._dispersion_bar[0] if self._dispersion_bar else None,
                "error": self._dispersion_error,
            },
            "nightly": self.nightly_report if self.desk_local else self.desk_status.get("nightly"),
            "chain": self.chain_status if self.desk_local else self.desk_status.get("chain"),
            "signals_emitted": self.signals_emitted,
            "symbols": len(self.all_symbols),
            "history_errors": len(self.history_errors),
            "history_gaps_repaired": len(self.history_gaps),
            "history_gap_details": dict(list(self.history_gaps.items())[:20]),
            "history_error_details": dict(list(self.history_errors.items())[:20]),
            "warmup_report": getattr(self, "warmup_report", {}),
            "history_backfill": {**getattr(self, "history_backfill", {}),
                                 "errors": len(getattr(self, "history_backfill", {}).get("errors") or {})},
            "warmup": self.warmup_status(),
            "session_open": regular_session_open(),
            "market_calendar": {**market_calendar.status(datetime.now(IST).date()),
                                "rejected_tokens": self.calendar_rejected},
            "observed_at": datetime.now(UTC).isoformat(),
            "stream_clients": self.events.client_count,
            "feed_alive": self.feed_alive(),
            "token_expired": self.token_expired(),
            "socket_auth_failed": self.socket_auth_rejected(),
            "feed_recoveries": self.feed_recoveries,
            "lane_errors": self.lane_errors,
            "entry_scoring": self.entry_gate.status(),
            "whale_interest": {k: v for k, v in self.whale_interest.status().items() if k != "live"},
            "preopen_refreshes": self.preopen_refreshes,
            "extreme_booking": self.extreme_booker.health() if self.settings.extreme_booking_enabled else {"enabled": False},
            "entry_flow": self.entry_flow.health(),
            "context_indices": self.context_indices_status,
            "self_heal": {"outage_repair": self.outage_repair, "expired_settlements": self.expired_settlements[:10],
                          "lot_audit": self.lot_audit},
            "boot": self_heal.boot_report(self.settings.database_path, self.settings.engine_role, self.warmup_status(),
                                          getattr(self, "warmup_report", None), getattr(self, "history_backfill", None),
                                          self.status),
            "signals_dropped_stale": self.signals_dropped_stale,
            "signal_staleness_limit_seconds": self.signal_staleness_limit(),
            "last_stale_drop": self.last_stale_drop,
            "day_baseline_equity": self.day_baseline_equity(),
            "loop_lag_ms": self.loop_lag_ms(),
            "candle_writer": self.candle_writer.status(),
            "history_prune": self.history_prune,
            "archive": getattr(self, "archive_report", None) if self.desk_local else self.desk_status.get("archive"),
            "features": None if self.desk_local else self.desk_status.get("features"),
            "tick_store": (self.tick_store.status(include_database_counts=False)
                           if self.settings.tick_capture_enabled else None)
            if self.desk_local else self.desk_status.get("tick_store"),
            "mp": self.mp.health() if self.desk_local else self.desk_status.get("mp"),
            "layout": {"role": self.settings.engine_role,
                       "desk": None if self.desk_local else {
                           key: self.desk_status.get(key) for key in ("reachable", "checked_at", "error", "bus")},
                       "bus": self.bus.status() if self.bus else None},
            "blast": self.blast.health(),
            "smart_lanes": {smart.name: smart.health() for smart in _smart_lanes(self)},
            "exit_failures": self.exit_failures(),
            "rollover": self.rollover_status(),
        }

    async def _publish_tick(self, tick: Tick, *, analysis_only: bool) -> None:
        self.events.publish("tick", tick)
        if self.bus is not None:
            await self.bus.publish(tick, analysis_only=analysis_only, received_at=self.last_tick_at)

    # -- the desk, when it runs in its own process ---------------------------

    def desk_holdings(self) -> dict[str, int]:
        """Desk contracts that must stay subscribed: symbol -> lot size."""
        if self.desk_local:
            return {symbol: position.lot_size for symbol, position in self.mp.portfolio.positions.items()}
        return dict(self.remote_desk_holdings)

    def _load_desk_holdings(self) -> dict[str, int]:
        if self.desk_local:
            return {}
        try:
            raw = json.loads(self._desk_holdings_path.read_text())
            return {str(symbol): int(lot) for symbol, lot in raw.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def update_desk_holdings(self, holdings: dict[str, int]) -> list[str]:
        """Record the desk's holdings; returns held symbols not yet subscribed."""
        clean = {str(symbol): int(lot) for symbol, lot in holdings.items() if int(lot) > 0}
        if clean != self.remote_desk_holdings:
            self.remote_desk_holdings = clean
            temporary = self._desk_holdings_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(clean, sort_keys=True))
            temporary.replace(self._desk_holdings_path)
        return sorted(set(clean) - set(self.all_symbols))

    def desk_context(self) -> dict:
        """What the desk process needs to configure itself exactly as the
        single process would: today's contracts, the futures roll, the feed's
        symbols, lot sizes, and the settings the desk reads."""
        keys = [name for name in Settings.model_fields if name.startswith("mp_")] + [
            "vix_symbol", "symbols_csv", "min_days_to_expiry", "expiry_flatten_ist",
            "order_quote_max_age_seconds", "tick_capture_enabled", "tick_retention_days",
            "flow_retention_days", "api_token",
        ]
        return {
            "v": 1,
            # A desk process runs only beside a "strategy" process; beside
            # "all" it would be a second desk trading the same book.
            "role": self.settings.engine_role,
            "status": self.status,
            "selection_date": self._selection_date,
            "all_symbols": list(self.all_symbols),
            "analysis_symbols": sorted(getattr(self, "analysis_option_symbols", set())),
            "futures_rollover": dict(self.futures_rollover),
            "contracts": [asdict(contract) for contract in self.contract_selector.contracts.values()],
            "lot_sizes": dict(self.execution.lot_sizes),
            "settings": {key: getattr(self.settings, key) for key in keys},
        }

    async def _desk_status_loop(self) -> None:
        """Poll the desk process: its health fills this process's health
        report, and its whale alerts reach the Telegram alert manager here."""
        import httpx

        url = self.settings.desk_url.rstrip("/") + "/api/internal/desk/status"
        headers = {"X-Macd-Token": self.settings.api_token} if self.settings.api_token else {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                try:
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                    body = response.json()
                    self.whale_alert_queue.extend(body.pop("whale_alerts", []) or [])
                    self.desk_status = {**body, "reachable": True, "error": None,
                                        "checked_at": datetime.now(UTC).isoformat()}
                except Exception as exc:  # noqa: BLE001 - the desk being down must not affect trading
                    self.desk_status = {**self.desk_status, "reachable": False, "error": str(exc)[:300],
                                        "checked_at": datetime.now(UTC).isoformat()}
                await asyncio.sleep(5)

    def rollover_status(self) -> dict:
        """What the series rollover did today, and what it still cannot do."""
        return {
            "min_days_to_expiry": self.settings.min_days_to_expiry,
            "flatten_at_ist": self.settings.expiry_flatten_ist,
            "futures": dict(self.futures_rollover),
            "options_rolled": dict(self.contract_selector.rolled),
            "expiring_positions": self.expiring_positions(),
            "flattened_on": self._flattened_expiry_day,
            "errors": dict(self.rollover_errors),
            "selected_expiries": sorted({
                str(contract.expiry)
                for contract in self.contract_selector.contracts.values()
                if getattr(contract, "expiry", None)
            }),
        }
