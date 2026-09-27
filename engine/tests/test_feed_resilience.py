"""Guards for the 19 Aug failure: a socket dead through the open, and
signals computed on bars wall clock had long left behind."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from macd_trader.engine import (
    TradingEngine,
    preopen_window,
    regular_session_open,
)

IST = ZoneInfo("Asia/Kolkata")


def test_history_warmup_does_not_block_startup_and_is_cancelled_on_shutdown(tmp_path):
    import asyncio
    from macd_trader.config import Settings

    engine = TradingEngine(Settings(
        symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False,
        database_path=str(tmp_path / "book.sqlite3"),
        mp_database_path=str(tmp_path / "mp.sqlite3"),
        research_database_path=str(tmp_path / "history.sqlite3"),
        tick_database_path=str(tmp_path / "ticks.sqlite3"),
        contract_snapshot_path=str(tmp_path / "contracts.json"),
    ))

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_connection():
            entered.set()
            await release.wait()

        engine.connect_feed = slow_connection
        await asyncio.wait_for(engine.start(), timeout=1)
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert engine.snapshot()["broker"]["status"] == "connecting"
        startup = engine._startup_task
        assert not startup.done()
        await engine.stop()
        assert startup.cancelled()
        assert engine.status == "stopped"

    asyncio.run(scenario())


def _moment(h, m, day=19):
    return datetime(2026, 8, day, h, m, tzinfo=IST)


class TestPreopenWindow:
    def test_covers_the_nse_preopen(self):
        assert preopen_window(_moment(9, 0))
        assert preopen_window(_moment(9, 5))
        assert preopen_window(_moment(9, 14))

    def test_excludes_the_session_and_early_morning(self):
        # 09:15 belongs to the session watchdog, not the pre-open one.
        assert not preopen_window(_moment(9, 15))
        assert regular_session_open(_moment(9, 15))
        # The socket connecting at 07:30 must not trigger pre-open logic yet.
        assert not preopen_window(_moment(7, 30))

    def test_excludes_weekends(self):
        assert not preopen_window(datetime(2026, 8, 22, 9, 5, tzinfo=IST))  # Saturday

    def test_watchdog_now_covers_the_gap_that_lost_the_open(self):
        # Before the fix the watchdog ignored everything before 09:15, so a
        # socket that died while idling was only discovered at the bell.
        watched = lambda t: regular_session_open(t) or preopen_window(t)
        assert watched(_moment(9, 5))
        assert watched(_moment(9, 14))


class _Engine:
    """Only the staleness guard is under test; borrow it off the real class."""
    _signal_bar_is_current = TradingEngine._signal_bar_is_current

    def __init__(self):
        self.signals_dropped_stale = 0


def _signal(age_seconds):
    bar = datetime.now(UTC).timestamp() - age_seconds
    return SimpleNamespace(evaluated_candle_timestamp=int(bar))


class TestStaleBarGuard:
    """The staleness limit must scale with the bar period.

    The strategy evaluates the FORMING bar and fires once, whenever inside that
    bar the condition first becomes true, stamping the bar's START time. So a
    perfectly good signal is up to one full timeframe "old". A flat 180s limit
    against the live 1800s timeframe restricted signals to the first three
    minutes of each 30-minute bar and silently dropped the rest.
    """

    @staticmethod
    def _engine(timeframe):
        from macd_trader.engine import TradingEngine
        obj = _Engine()
        obj.settings = SimpleNamespace(timeframe_seconds=timeframe)
        obj.signal_staleness_limit = TradingEngine.signal_staleness_limit.__get__(obj)
        obj._signal_bar_is_current = TradingEngine._signal_bar_is_current.__get__(obj)
        return obj

    def test_intrabar_signal_late_in_a_30_minute_bar_survives(self):
        # A cross detected 25 minutes into an 1800s bar: entirely legitimate.
        engine = self._engine(1800)
        assert engine._signal_bar_is_current(_signal(25 * 60))
        assert engine.signals_dropped_stale == 0

    def test_closed_30_minute_bar_survives(self):
        # Exactly one period old — the closed-bar evaluation.
        engine = self._engine(1800)
        assert engine._signal_bar_is_current(_signal(1800))

    def test_bar_from_a_previous_period_is_still_rejected(self):
        # Two periods back means the feed skipped a whole bar.
        engine = self._engine(1800)
        assert not engine._signal_bar_is_current(_signal(3600))
        assert engine.signals_dropped_stale == 1
        assert engine.last_stale_drop["staleness_seconds"] == 3600

    def test_one_minute_timeframe_keeps_the_old_effective_limit(self):
        engine = self._engine(60)
        assert engine.signal_staleness_limit() == 180
        assert engine._signal_bar_is_current(_signal(120))
        assert not engine._signal_bar_is_current(_signal(400))

    def test_every_signal_recorded_on_19_aug_would_now_pass(self):
        # Max observed staleness that day was exactly 1800s = one full bar.
        engine = self._engine(1800)
        for observed in (2, 120, 167, 1500, 1799, 1800):
            assert engine._signal_bar_is_current(_signal(observed)), observed
        assert engine.signals_dropped_stale == 0

    def test_missing_stamp_is_not_treated_as_stale(self):
        engine = self._engine(1800)
        assert engine._signal_bar_is_current(SimpleNamespace(evaluated_candle_timestamp=None))


class TestWarmupRetryPolicy:
    """A restart must not rate-limit itself out of its own indicator state.

    Concurrency was never the problem — warm() already holds a 3-slot
    semaphore. The retry policy was: three attempts, 0.5s and 1.0s apart, all
    inside the same per-minute window, then give up with no scheduled retry.
    """

    def test_backoff_clears_a_per_minute_window(self):
        from macd_trader.engine import WARMUP_ATTEMPTS, WARMUP_RATE_LIMIT_BACKOFF
        assert len(WARMUP_RATE_LIMIT_BACKOFF) >= WARMUP_ATTEMPTS - 1
        assert sum(WARMUP_RATE_LIMIT_BACKOFF) > 60, "must outlast a per-minute quota"

    def test_backoff_is_monotonic(self):
        from macd_trader.engine import WARMUP_RATE_LIMIT_BACKOFF
        assert list(WARMUP_RATE_LIMIT_BACKOFF) == sorted(WARMUP_RATE_LIMIT_BACKOFF)

    def test_old_policy_would_not_have_cleared_the_window(self):
        assert sum(0.5 * (i + 1) for i in range(2)) < 60


class TestSocketRestartIsBounded:
    """The 8 Sep outage: a restart step that could never time out.

    At 09:05 the pre-open refresh cancelled the stream task, called close() on
    a socket whose SDK reconnect thread was mid-flight, and never returned.
    Both the SDK's connect and its close run under asyncio.to_thread, which
    ignores cancellation, so nothing downstream could interrupt them. The last
    tick was 09:05:01; the market opened ten minutes later on a dead feed and
    the desk took no trade all day. Because it HUNG rather than raised, the
    except clause never ran (health.error stayed null), the watchdog never
    reached its staleness check (feed_recoveries stayed 0), and the
    _reconfigure_lock it held blocked every later attempt to install a token.
    """

    @staticmethod
    def _engine(tmp_path):
        from macd_trader.config import Settings
        return TradingEngine(Settings(
            symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False,
            database_path=str(tmp_path / "book.sqlite3"),
            mp_database_path=str(tmp_path / "mp.sqlite3"),
            research_database_path=str(tmp_path / "history.sqlite3"),
            tick_database_path=str(tmp_path / "ticks.sqlite3"),
            contract_snapshot_path=str(tmp_path / "contracts.json"),
        ))

    def test_a_close_that_never_returns_does_not_wedge_the_restart(self, tmp_path, monkeypatch):
        import asyncio
        from macd_trader import engine as engine_module

        monkeypatch.setattr(engine_module, "FEED_STEP_TIMEOUT", 0.05)
        engine = self._engine(tmp_path)
        started = []

        async def never_returns():
            await asyncio.Event().wait()

        async def connect():
            started.append("connect")

        async def stream(symbols, handler):
            await asyncio.Event().wait()

        engine.broker.close = never_returns
        engine.broker.connect = connect
        engine.broker.stream = stream

        async def scenario():
            # Must finish despite close() never returning, and must still go on
            # to reconnect -- abandoning the stuck thread, not the session.
            await asyncio.wait_for(engine.restart_stream(), timeout=2)
            assert started == ["connect"]
            engine._stream_task.cancel()

        asyncio.run(scenario())

    def test_a_stream_task_with_slow_cleanup_is_abandoned(self, tmp_path, monkeypatch):
        """Cancelling is not the same as being gone: a task whose cleanup
        outlives the cancel used to hold the restart open for as long as it
        liked, with the _reconfigure_lock still held."""
        import asyncio
        from macd_trader import engine as engine_module

        monkeypatch.setattr(engine_module, "FEED_STEP_TIMEOUT", 0.05)
        engine = self._engine(tmp_path)

        async def slow_to_die():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(10)

        async def noop():
            pass

        async def stream(symbols, handler):
            await asyncio.Event().wait()

        async def scenario():
            engine._stream_task = asyncio.create_task(slow_to_die())
            await asyncio.sleep(0)
            engine.broker.close = noop
            engine.broker.connect = noop
            engine.broker.stream = stream
            await asyncio.wait_for(engine.restart_stream(), timeout=2)
            engine._stream_task.cancel()

        asyncio.run(scenario())

    def test_a_freshly_booted_machine_can_recover_its_feed(self, tmp_path, monkeypatch):
        """Monotonic time starts at boot; the first sweep after a reboot must not sit in a cooldown."""
        import asyncio
        from macd_trader import engine as engine_module

        monkeypatch.setattr(engine_module, "FEED_STEP_TIMEOUT", 0.05)
        monkeypatch.setattr(engine_module, "regular_session_open", lambda *a: True)
        monkeypatch.setattr(engine_module.time, "monotonic", lambda: 12.0)
        engine = self._engine(tmp_path)
        recovered = []

        async def not_refreshed():
            return False

        async def restart():
            recovered.append(1)

        engine._preopen_refresh = not_refreshed
        engine.restart_stream = restart
        engine.seconds_since_last_tick = lambda: 9999.0
        asyncio.run(asyncio.wait_for(engine._watchdog_sweep(), timeout=2))
        assert recovered == [1]
        assert engine.feed_recoveries == 1

    def test_a_hung_preopen_refresh_no_longer_disables_the_watchdog(self, tmp_path, monkeypatch):
        """The exact 8 Sep mechanism, and the reason the desk sat out the day."""
        import asyncio
        from macd_trader import engine as engine_module

        monkeypatch.setattr(engine_module, "FEED_STEP_TIMEOUT", 0.05)
        monkeypatch.setattr(engine_module, "regular_session_open", lambda *a: True)
        engine = self._engine(tmp_path)
        recovered = []

        async def hangs_forever():
            await asyncio.Event().wait()

        async def restart():
            recovered.append(1)

        engine._preopen_refresh = hangs_forever
        engine.restart_stream = restart
        engine.seconds_since_last_tick = lambda: 9999.0

        async def scenario():
            # Sweep one: the refresh hangs. It must return, and say so.
            await asyncio.wait_for(engine._watchdog_sweep(), timeout=2)
            assert engine.error == "pre-open feed refresh timed out"
            assert recovered == []

            # Sweep two: with the refresh no longer hanging, the stale feed is
            # recovered. Before the fix the loop never got here again at all.
            async def done():
                return False
            engine._preopen_refresh = done
            await asyncio.wait_for(engine._watchdog_sweep(), timeout=2)
            assert recovered == [1]
            assert engine.feed_recoveries == 1

        asyncio.run(scenario())

    def test_a_raising_sweep_does_not_end_the_watchdog_loop(self, tmp_path, monkeypatch):
        import asyncio
        from macd_trader import engine as engine_module

        engine = self._engine(tmp_path)
        sweeps = []

        async def explode():
            sweeps.append(1)
            raise RuntimeError("boom")

        engine._watchdog_sweep = explode
        monkeypatch.setattr(engine_module, "FEED_WATCHDOG_INTERVAL", 0.001)

        async def scenario():
            task = asyncio.create_task(engine._feed_watchdog_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            # The loop kept sweeping after the first raise instead of dying.
            assert len(sweeps) > 1
            assert "boom" in engine.error

        asyncio.run(scenario())


class TestPreopenRefreshOnlyRecyclesAnIdleSocket:
    """8 Sep: the refresh destroyed a socket that had just carried 1,323 ticks.

    The refresh exists for the 19 Aug failure -- the SDK spends its five
    reconnects during the long pre-open idle, so a socket still claiming
    "connected" can be dead at the bell. That argument only applies to a
    socket that is idle. Recycling one that is actively delivering is pure
    downside, and on 8 Sep the teardown is exactly what hung.
    """

    @staticmethod
    def _engine(tmp_path):
        from macd_trader.config import Settings
        return TradingEngine(Settings(
            symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False,
            database_path=str(tmp_path / "book.sqlite3"),
            mp_database_path=str(tmp_path / "mp.sqlite3"),
            research_database_path=str(tmp_path / "history.sqlite3"),
            tick_database_path=str(tmp_path / "ticks.sqlite3"),
            contract_snapshot_path=str(tmp_path / "contracts.json"),
        ))

    def _at_0905(self, monkeypatch, engine_module):
        # 8 Sep 2026 is a Tuesday. _moment() is pinned to August, where the
        # 8th is a Saturday and the weekday gate would short-circuit the test.
        fixed = datetime(2026, 9, 8, 9, 5, tzinfo=IST)
        real = engine_module.datetime

        class _Clock(real):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed

        monkeypatch.setattr(engine_module, "datetime", _Clock)

    def test_a_live_socket_is_left_alone(self, tmp_path, monkeypatch):
        import asyncio
        from macd_trader import engine as engine_module

        engine = self._engine(tmp_path)
        self._at_0905(monkeypatch, engine_module)
        restarts = []
        engine.restart_stream = lambda: restarts.append(1)
        engine.seconds_since_last_tick = lambda: 1.0      # ticks arriving now

        assert asyncio.run(engine._preopen_refresh()) is False
        assert restarts == []
        # Day left unmarked, so a socket that falls quiet later still gets one.
        assert engine._preopen_refresh_day is None

    def test_an_idle_socket_is_still_recycled(self, tmp_path, monkeypatch):
        import asyncio
        from macd_trader import engine as engine_module

        engine = self._engine(tmp_path)
        self._at_0905(monkeypatch, engine_module)
        restarts = []

        async def restart():
            restarts.append(1)

        engine.restart_stream = restart
        engine.seconds_since_last_tick = lambda: 4000.0   # silent all pre-open

        assert asyncio.run(engine._preopen_refresh()) is True
        assert restarts == [1]

    def test_a_feed_that_never_ticked_is_recycled(self, tmp_path, monkeypatch):
        """A fresh process has no tick yet; that must not be read as healthy."""
        import asyncio
        from macd_trader import engine as engine_module

        engine = self._engine(tmp_path)
        self._at_0905(monkeypatch, engine_module)
        restarts = []

        async def restart():
            restarts.append(1)

        engine.restart_stream = restart
        engine.seconds_since_last_tick = lambda: None

        assert asyncio.run(engine._preopen_refresh()) is True
        assert restarts == [1]


class TestStuckSdkThreadsCannotStarveTheEngine:
    """An abandoned socket close keeps its worker forever.

    restart_stream gives up on a close that will not return, which is right --
    a leaked thread beats a wedged desk. But the leak has to be contained: the
    chain collector, whale sampler and dispersion writer all reach the DEFAULT
    executor through asyncio.to_thread, so leaking socket closes into it would
    trade one silent outage for another.
    """

    def test_sdk_calls_do_not_run_on_the_default_executor(self):
        import asyncio
        import inspect
        from macd_trader import brokers

        source = inspect.getsource(brokers.FyersBroker.close)
        assert "_in_sdk_pool" in source
        assert "to_thread" not in source
        assert inspect.getsource(brokers.FyersBroker.stream).count("_in_sdk_pool") == 1

    def test_a_wedged_sdk_call_leaves_to_thread_working(self):
        """The property that matters: default-executor work still completes
        while an SDK call is parked forever."""
        import asyncio
        import threading
        from macd_trader import brokers

        parked = threading.Event()

        async def scenario():
            stuck = asyncio.ensure_future(brokers._in_sdk_pool(parked.wait))
            await asyncio.sleep(0.05)
            # The engine's own to_thread work is unaffected by the stuck call.
            assert await asyncio.to_thread(lambda: "chain row written") == "chain row written"
            parked.set()
            await stuck

        asyncio.run(scenario())


class TestStoredHistoryGapDetection:
    """A hole in the durable candles used to enter the indicators silently.

    warm() reached for Fyers only when a symbol was NEW or had NO stored bars.
    A symbol with years of history and one missing session took neither branch.
    On 8 Sep the feed died at 09:05 and wrote no candles all day; next morning
    215 equities, indices and futures warmed with MACD state that jumped from
    7 Sep 15:15 straight to 9 Sep, so the 12/26 EMAs swallowed a whole session
    in one bar -- while health reported warmup 693/693, history_errors 0.
    """

    @staticmethod
    def _engine(tmp_path):
        from macd_trader.config import Settings
        return TradingEngine(Settings(
            symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False,
            database_path=str(tmp_path / "book.sqlite3"),
            mp_database_path=str(tmp_path / "mp.sqlite3"),
            research_database_path=str(tmp_path / "history.sqlite3"),
            tick_database_path=str(tmp_path / "ticks.sqlite3"),
            contract_snapshot_path=str(tmp_path / "contracts.json"),
        ))

    @staticmethod
    def _bars(*dates):
        from macd_trader.models import Candle
        out = []
        for d in dates:
            moment = datetime(d.year, d.month, d.day, 15, 15, tzinfo=IST)
            out.append(Candle("NSE:TEST-EQ", int(moment.timestamp()), 1.0, 1.0, 1.0, 1.0))
        return out

    def _today(self, monkeypatch, engine_module, when):
        real = engine_module.datetime

        class _Clock(real):
            @classmethod
            def now(cls, tz=None):
                return when.astimezone(tz) if tz else when

        monkeypatch.setattr(engine_module, "datetime", _Clock)

    def test_a_missed_session_is_detected(self, tmp_path, monkeypatch):
        """The 8 Sep hole: last bar Monday, now Wednesday, Tuesday missing."""
        import datetime as dt
        from macd_trader import engine as engine_module

        engine = self._engine(tmp_path)
        self._today(monkeypatch, engine_module, datetime(2026, 9, 9, 7, 35, tzinfo=IST))
        assert engine.stored_history_is_stale(self._bars(dt.date(2026, 9, 7))) is True

    def test_yesterdays_close_is_current(self, tmp_path, monkeypatch):
        import datetime as dt
        from macd_trader import engine as engine_module

        engine = self._engine(tmp_path)
        self._today(monkeypatch, engine_module, datetime(2026, 9, 9, 7, 35, tzinfo=IST))
        assert engine.stored_history_is_stale(self._bars(dt.date(2026, 9, 8))) is False

    def test_a_weekend_is_not_a_gap(self, tmp_path, monkeypatch):
        """Friday's close read on Monday morning must not trigger a refetch --
        otherwise every Monday costs a supplemental request per symbol."""
        import datetime as dt
        from macd_trader import engine as engine_module

        engine = self._engine(tmp_path)
        # Mon 7 Sep 2026, last bar Fri 4 Sep.
        self._today(monkeypatch, engine_module, datetime(2026, 9, 7, 7, 35, tzinfo=IST))
        assert engine.stored_history_is_stale(self._bars(dt.date(2026, 9, 4))) is False

    def test_a_long_outage_is_detected_across_a_weekend(self, tmp_path, monkeypatch):
        import datetime as dt
        from macd_trader import engine as engine_module

        engine = self._engine(tmp_path)
        # Tue 8 Sep, last bar Thu 3 Sep: Friday alone makes it stale.
        self._today(monkeypatch, engine_module, datetime(2026, 9, 8, 7, 35, tzinfo=IST))
        assert engine.stored_history_is_stale(self._bars(dt.date(2026, 9, 3))) is True

    def test_no_stored_bars_is_not_a_gap(self, tmp_path):
        """An empty history already has its own branch in warm(); reporting it
        as a gap too would double-count it in health."""
        engine = self._engine(tmp_path)
        assert engine.stored_history_is_stale([]) is False

    def test_health_exposes_repaired_gaps(self, tmp_path):
        engine = self._engine(tmp_path)
        engine.history_gaps["NSE:RELIANCE-EQ"] = "2026-09-07"
        payload = engine.health()
        assert payload["history_gaps_repaired"] == 1
        assert payload["history_gap_details"]["NSE:RELIANCE-EQ"] == "2026-09-07"


def test_saving_a_setting_does_not_blank_the_indicator_state(tmp_path):
    """reconfigure_strategy rebuilds MACDStrategyManager, which empties
    strategy.points. It used to re-warm only the option legs, so saving any
    setting left every spot, index and future with no indicators until its
    next closed bar. On 11 Sep that took health from 690/690 to 478/690
    eighty minutes before the open, with all 212 spots blank."""
    import asyncio
    from macd_trader.config import Settings
    from macd_trader.models import Candle

    settings = Settings(symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False,
                        database_path=str(tmp_path / "book.sqlite3"),
                        mp_database_path=str(tmp_path / "mp.sqlite3"),
                        research_database_path=str(tmp_path / "history.sqlite3"),
                        tick_database_path=str(tmp_path / "ticks.sqlite3"),
                        contract_snapshot_path=str(tmp_path / "contracts.json"))
    engine = TradingEngine(settings)
    spot, option = "NSE:TEST-EQ", "NSE:TEST26SEP100CE"
    engine.all_symbols = [spot, option]
    engine.option_symbols = [option]
    base = 1_780_000_000 - (1_780_000_000 % settings.timeframe_seconds)
    for symbol in (spot, option):
        for i in range(120):
            price = 100 + (i % 7)
            engine.history[symbol].append(
                Candle(symbol, base + i * settings.timeframe_seconds, price, price + 1, price - 1, price))

    asyncio.run(engine.reconfigure_strategy(settings))
    assert spot in engine.strategy.points, "spot lost its indicator state on a settings save"
    assert option in engine.strategy.points
