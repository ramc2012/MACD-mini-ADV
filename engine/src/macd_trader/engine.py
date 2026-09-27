from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from contextlib import suppress
import json
from dataclasses import asdict
from pathlib import Path
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .brokers import Broker, create_broker
from .candle_store import (
    LiveCandleWriter, prior_session_candle_count, prune_history, resolve_expiry, store_historical_candles,
)
from .tick_store import TickStore
from .regimes import regime_for
from .rollover import days_to_expiry, first_tradable, is_futures_symbol, Expiry
from .candles import CandleAggregator
from .chart_history import load_chart_history
from .config import Settings
from .contracts import ContractSelector
from .events import EventHub, json_value
from .greeks import contract_gex
from .execution import ExecutionManager
from .models import Candle, Tick, Trade
from . import market_calendar
from .blast_engine import BlastEngine
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
# The ratio backfill runs off a browser poll, so a leg whose download fails
# would re-request on every refresh. Fyers rate-limits per endpoint — the desk
# collected 429s on /data/quotes on 4 Sep — so a failed attempt stands that leg
# down. A strike listed today cannot grow before-session history until
# tomorrow, and it shows up either as an empty range or — more often — as a
# successful download that carries only today's bars; both wait far longer
# than a transient failure does.
RATIO_BACKFILL_RETRY_SECONDS = 300.0
RATIO_BACKFILL_EMPTY_RETRY_SECONDS = 3_600.0
# Warm-up concurrency is already bounded inside warm() by history_slots(3).
# What rate-limited a restart into 138 dead contracts was the RETRY policy:
# three attempts 0.5s apart all land inside the same per-minute window.
WARMUP_ATTEMPTS = 5
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


def minute_closed_moment(candle) -> datetime:
    """The IST wall-clock moment a closed candle belongs to."""
    return datetime.fromtimestamp(candle.timestamp, UTC).astimezone(IST)


def regular_session_open(moment: datetime | None = None) -> bool:
    """True only during the NSE cash/derivatives session on a trading day."""
    local = (moment or datetime.now(IST)).astimezone(IST)
    return market_calendar.is_trading_day(local.date()) and (9, 15) <= (local.hour, local.minute) < (15, 30)


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


class TradingEngine(DeskMixin):
    def __init__(self, settings: Settings):
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
        self._preopen_refresh_day: str | None = None
        self.preopen_refreshes = 0
        self.signals_dropped_stale = 0
        self.last_stale_drop: dict | None = None
        self._selection_date: str | None = None
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
        self._rollover_task = asyncio.create_task(self._rollover_loop())
        self._session_close_task = asyncio.create_task(self._session_close_loop())
        self._watchdog_task = asyncio.create_task(self._feed_watchdog_loop())
        self._expiry_flatten_task = asyncio.create_task(self._expiry_flatten_loop())
        self._dispersion_task = asyncio.create_task(self._dispersion_loop())
        self._blast_task = asyncio.create_task(self._blast_maintenance_loop())
        if self.desk_local:
            self._nightly_task = asyncio.create_task(self._nightly_loop())
            self._chain_task = asyncio.create_task(self._chain_loop())
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

    async def connect_feed(self) -> None:
        self.status = "connecting"
        # A new connection attempt must not carry yesterday's rejection into
        # the visible in-progress state after a replacement token was accepted.
        self.error = None
        self.feed_connects += 1
        self.events.publish("broker", self.broker_status())
        try:
            await self.broker.connect()
            self._auth_validated_date = datetime.now(IST).date().isoformat()
            held_positions = {
                symbol: position.lot_size for symbol, position in self.portfolio.positions.items()
            }
            held_positions.update(self.desk_holdings())
            held = await self.contract_selector.restore_held_contracts(held_positions)
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
            # ITM/OTM legs are market-context inputs only.  They are live-feed
            # subscribers and chart sources, never additions to either paper
            # strategy's entry universe. Held contracts remain manageable.
            self.option_symbols = [
                row.symbol for row in contracts
                if not row.analysis_only or row.retained or row.symbol in held_positions
            ]
            self.analysis_option_symbols = {
                row.symbol for row in contracts if row.symbol not in self.option_symbols
            }
            await self._resolve_futures_series()
            # The desk's own instruments must be subscribed too: it watches
            # index futures, which are in neither the spot universe nor the
            # option list.
            self.all_symbols = list(dict.fromkeys(
                [*self.settings.symbols, *self.option_symbols, *self.analysis_option_symbols, *self.mp_spot_symbols(),
                 # A position opened in the outgoing series still has to be
                 # marked and exited, so it stays subscribed after the roll.
                 *self.portfolio.positions, *self.desk_holdings(),
                 # Stream subscription only, so a blast holding keeps its stop
                 # and trail live. Subscribing is not a fetch: it rides the
                 # websocket the engine already holds open.
                 *self.blast.portfolio.positions]))
            self.strategy = MACDStrategyManager(self.settings, self.events, self.option_symbols)
            tradable = {row.symbol: row.lot_size for row in contracts if row.symbol in self.option_symbols}
            self.execution.set_tradable_contracts(tradable)
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
            self.blast.set_resolvers(spot_price=self.spot_price)
            self.configure_desk(tradable)
            await self._refresh_position_marks()

            history_slots = asyncio.Semaphore(3)
            new_contracts = set(self.contract_selector.new_symbols)
            downloaded_bars: list[Candle] = []

            async def warm(symbol: str) -> None:
                for attempt in range(WARMUP_ATTEMPTS):
                    try:
                        async with history_slots:
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
                            needed = self.minimum_warmup_bars()
                            if symbol in new_contracts:
                                # Pull the complete available history once when
                                # a contract first enters the watchlist.  An
                                # unchanged, newly-listed option can legitimately
                                # have fewer than ``needed`` bars; re-requesting
                                # the same unavailable past on every restart only
                                # delays the websocket.  It remains non-mature
                                # until real bars accrue in the durable store.
                                try:
                                    broker_rows = await self.broker.history_range(
                                        symbol,
                                        self.settings.timeframe_seconds,
                                        datetime.now(UTC) - timedelta(days=NEW_CONTRACT_HISTORY_DAYS),
                                        datetime.now(UTC),
                                    )
                                except Exception:
                                    if len(stored["candles"]) < needed:
                                        raise
                            elif not stored["candles"] or self.stored_history_is_stale(stored["candles"]):
                                if stored["candles"]:
                                    self.history_gaps[symbol] = datetime.fromtimestamp(
                                        stored["candles"][-1].timestamp, UTC).astimezone(IST).date().isoformat()
                                try:
                                    broker_rows = await self.broker.history(symbol, self.settings.timeframe_seconds, 150)
                                except Exception:
                                    # A restart warms hundreds of contracts at once.  If
                                    # Fyers rate-limits a supplemental request, retain the
                                    # already durable research history rather than dropping
                                    # the whole symbol from the live strategy state.
                                    if not stored["candles"]:
                                        raise
                            if broker_rows:
                                if self.settings.timeframe_seconds == 60:
                                    downloaded_bars.extend(broker_rows)
                                await asyncio.sleep(0.20)
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
                # No broker history was available. Release the symbol with
                # whatever closed live bars arrived while FYERS was retried.
                self._finish_symbol_warmup(symbol, [])

            # The blast lane runs on one-minute bars, so it seeds from the
            # stored minute bars rather than this strategy-timeframe replay.
            try:
                await self.blast.warm_from_store(
                    self.settings.research_database_path,
                    [*self.option_symbols, *self.blast.portfolio.positions],
                )
            except Exception as exc:  # noqa: BLE001 - the lane must not block the feed
                self.blast.error = f"minute-bar warm-up failed: {exc}"
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
            await asyncio.gather(*(warm(symbol) for symbol in warm_symbols))
            if downloaded_bars:
                try:
                    await asyncio.to_thread(
                        store_historical_candles,
                        self.settings.research_database_path,
                        downloaded_bars,
                        None,
                        {row.symbol: row.expiry for row in contracts},
                    )
                except Exception as exc:  # noqa: BLE001 - cache failure must not kill the feed
                    self.candle_writer.last_error = f"history cache failed: {exc}"
        except Exception as exc:
            self.status = "error"
            self.error = str(exc)
        finally:
            self.events.publish("broker", self.broker_status())

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
            if self.status == "connected":
                self.status = "error"
                self.error = str(exc)
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
        symbols = list(dict.fromkeys([*self.portfolio.positions, *mp_positions]))
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
            self.error = None
        except Exception as exc:  # noqa: BLE001 — retry on the next sweep
            self.error = f"feed recovery failed: {exc}"
        finally:
            self.events.publish("broker", self.broker_status())

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

    async def reconfigure(self, new_settings: Settings) -> None:
        async with self._reconfigure_lock:
            await self.disconnect_feed()
            capital_delta = new_settings.initial_capital - self.settings.initial_capital
            if capital_delta:
                self.portfolio.initial_capital = new_settings.initial_capital
                self.portfolio.cash += capital_delta
            self.settings = new_settings
            self.calendar_rejected = market_calendar.configure(new_settings.market_holidays_csv)
            self.broker = create_broker(new_settings)
            self.execution.settings = new_settings
            self.execution.broker = self.broker
            self.blast.rebind_broker(self.broker)
            self.blast.apply_settings(new_settings)
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
            capital_delta = new_settings.initial_capital - self.settings.initial_capital
            if capital_delta:
                # Preserve every position and realized result while changing
                # only the paper account's funding base. A later restart
                # reaches the same state by replaying the durable trades from
                # the new initial capital.
                self.portfolio.initial_capital = new_settings.initial_capital
                self.portfolio.cash += capital_delta
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
                     "_blast_task", "_history_prune_task", "_desk_status_task"):
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
        for name in ("_nightly_task", "_chain_task"):
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
        if self.bus is not None:
            await self.bus.stop()
        with suppress(Exception):
            self.repository.save_position_excursions(self.portfolio.positions.values())
        self.repository.close()
        self.status = "stopped"

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
        _, minute_closed = self.minute_candles.on_tick(tick)
        if minute_closed and regular_session_open(minute_closed_moment(minute_closed)):
            self.candle_writer.add(minute_closed, resolve_expiry(minute_closed.symbol, self.contract_selector.contracts))
            if not analysis_only:
                # The blast lane screens one-minute bars whatever the strategy
                # timeframe is; that is the resolution its screen was measured at.
                await self.blast.on_minute_bar(minute_closed, tick.timestamp)
        if analysis_only:
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
            and regular_session_open(exchange_at)
        )
        if regular_session_open() and current_session_print:
            # Measure executable quote freshness by receipt, consistently
            # with feed health; Fyers exchange stamps can lag on valid quotes.
            await self.execution.on_tick(tick.symbol, tick.ltp, self.last_tick_at)
            await self.blast.on_tick(tick.symbol, tick.ltp, self.last_tick_at, session_open=True)
            await self._exit_expiring_position(tick.symbol)
        else:
            # Prior-session and off-session prints may value a holding, but
            # must not refresh the executable quote or trigger paper risk.
            self.portfolio.mark(tick.symbol, tick.ltp)
            self.blast.portfolio.mark(tick.symbol, tick.ltp)
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
        # Evaluate the forming premium candle against a cloned indicator state
        # so an eligible entry is sent at the triggering tick, not at close.
        signal = (None if current.symbol in self._warming_symbols
                  else self.strategy.on_live_candle(current))
        if signal and self._signal_bar_is_current(signal) and self.repository.save_signal(signal):
            self.signals_emitted += 1
            await self.execution.on_signal(signal)
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
            if signal and self._signal_bar_is_current(signal) and self.repository.save_signal(signal):
                self.signals_emitted += 1
                await self.execution.on_signal(signal)

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
            if (now.hour, now.minute) < (15, 30):
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
                    await self.execution.on_signal(signal)
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
        return list(dict.fromkeys([
            *self.settings.symbols, *self.option_symbols, *self.mp_spot_symbols(),
            *self.portfolio.positions, *self.desk_holdings(),
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
                continue
            await self._flatten_expiring_positions(now)

    async def _flatten_expiring_positions(self, now: datetime) -> None:
        """Retry each still-held expiry until its paper exit actually fills."""
        macd_symbols = self.expiring_positions()
        mp_symbols = self.expiring_desk_positions()
        blast_symbols = self.blast.expiring_positions(now.date())
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

        pending = (self.expiring_positions()
                   or any(symbol in self.mp.portfolio.positions for symbol in mp_symbols)
                   or self.blast.expiring_positions(now.date()))
        self._flattened_expiry_day = None if pending else now.date().isoformat()
        self.events.publish("snapshot_required", {"reason": "expiry_day_flatten"})

    def expiring_positions(self) -> list[str]:
        """Open positions whose contract expires today or has already died."""
        expiring = []
        for symbol in self.portfolio.positions:
            contract = self.contract_selector.contracts.get(symbol)
            remaining = days_to_expiry(getattr(contract, "expiry", None)) if contract else None
            if remaining is not None and remaining <= 0:
                expiring.append(symbol)
        return expiring

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
                continue
            async with self._reconfigure_lock:
                await self.disconnect_feed()
                self.history.clear()
                self.history_errors.clear()
                self.latest_ticks.clear()
                await self.connect_feed()
                self.events.publish("snapshot_required", {"reason": "daily_contract_rollover"})

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

    def feed_alive(self) -> bool:
        """A connected socket that has stopped delivering is not a live feed."""
        if self.status != "connected":
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
                       else self.status if self.feed_alive()
                       else "stale" if self.status == "connected" else self.status),
            "token_expires_at": (lambda e: e.isoformat() if e else None)(
                getattr(self.broker, "token_expiry", lambda: None)()),
            "token_expired": self.token_expired(),
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
        last trade for spot; open interest prefers the live tick and falls back
        to the value captured at selection.
        """
        option_tick = self.latest_ticks.get(contract.symbol)
        spot_tick = self.latest_ticks.get(contract.spot_symbol)
        premium = option_tick.ltp if option_tick else contract.selection_price
        spot = spot_tick.ltp if spot_tick else None
        open_interest = None
        if option_tick is not None and option_tick.open_interest is not None:
            open_interest = option_tick.open_interest
        elif contract.oi:
            open_interest = contract.oi
        if not spot or open_interest is None:
            return {"iv": None, "gamma": None, "gex": None}
        inputs = (float(premium or 0), float(spot), int(open_interest), self.settings.risk_free_rate)
        now = time.monotonic()
        cached = self._greeks_cache.get(contract.symbol)
        # Reuse while the marks are unchanged, and for a few seconds even when
        # they move: a display snapshot does not need a fresh solve per tick.
        if cached and (cached[1] == inputs or now - cached[0] < GREEKS_CACHE_SECONDS):
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
        )
        self._greeks_cache[contract.symbol] = (now, inputs, result)
        return result

    def snapshot(self) -> dict:
        listed = set(self.settings.symbols) | set(self.contract_selector.contracts)
        desk_held = self.desk_holdings()
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
                        "retained": contract.retained or contract.symbol in self.portfolio.positions or contract.symbol in desk_held,
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
            baseline = self.repository.last_equity_before(midnight_utc)
            if baseline is None:
                # No history before today (fresh equity log) — anchor to the
                # first sample of the day so day P&L still means something.
                baseline = self.repository.first_equity_on_or_after(midnight_utc)
            self._day_baseline_equity = baseline
            self._day_baseline_date = today
        return self._day_baseline_equity

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
            "warmup": {
                "required": len(set(self.settings.symbols) | set(self.option_symbols)),
                "with_indicators": sum(symbol in self.strategy.points for symbol in set(self.settings.symbols) | set(self.option_symbols)),
            },
            "session_open": regular_session_open(),
            "market_calendar": {**market_calendar.status(datetime.now(IST).date()),
                                "rejected_tokens": self.calendar_rejected},
            "observed_at": datetime.now(UTC).isoformat(),
            "stream_clients": self.events.client_count,
            "feed_alive": self.feed_alive(),
            "token_expired": self.token_expired(),
            "feed_recoveries": self.feed_recoveries,
            "preopen_refreshes": self.preopen_refreshes,
            "signals_dropped_stale": self.signals_dropped_stale,
            "signal_staleness_limit_seconds": self.signal_staleness_limit(),
            "last_stale_drop": self.last_stale_drop,
            "day_baseline_equity": self.day_baseline_equity(),
            "loop_lag_ms": self.loop_lag_ms(),
            "candle_writer": self.candle_writer.status(),
            "history_prune": self.history_prune,
            "tick_store": (self.tick_store.status(include_database_counts=False)
                           if self.settings.tick_capture_enabled else None)
            if self.desk_local else self.desk_status.get("tick_store"),
            "mp": self.mp.health() if self.desk_local else self.desk_status.get("mp"),
            "layout": {"role": self.settings.engine_role,
                       "desk": None if self.desk_local else {
                           key: self.desk_status.get(key) for key in ("reachable", "checked_at", "error", "bus")},
                       "bus": self.bus.status() if self.bus else None},
            "blast": self.blast.health(),
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
