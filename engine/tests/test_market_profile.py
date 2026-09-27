from datetime import datetime, timedelta, timezone

import pytest

from macd_trader.market_profile import IST, Profile, ProfileBook, bracket_of
from macd_trader.orderflow import OrderFlowTracker, classify
from macd_trader.models import Tick

pytestmark = pytest.mark.usefixtures("fixed_auction_session_clock")


def _tick(symbol, ltp, cum_volume, minute, bid=None, ask=None, last_qty=None):
    # Anchored to the fixture's exchange day: the desk only accepts prints
    # from its current trading session.
    today = datetime.now(IST).date()
    moment = datetime(today.year, today.month, today.day, tzinfo=IST) + timedelta(minutes=minute)
    return Tick(symbol, ltp, cum_volume, timestamp=moment.astimezone(timezone.utc),
                bid=bid, ask=ask, last_qty=last_qty)


def test_quote_rule_beats_tick_rule_inside_the_spread():
    # At the ask => buyer initiated regardless of the previous print.
    assert classify(101.0, 100.0, 101.0, 105.0) == (1, "quote")
    assert classify(100.0, 100.0, 101.0, 99.0) == (-1, "quote")
    # Inside the spread with no lean => falls back to the tick rule.
    assert classify(100.5, 100.0, 101.0, 100.0)[1] == "tick"


def test_cumulative_volume_is_differenced_into_prints():
    tracker = OrderFlowTracker()
    assert tracker.on_tick(_tick("X", 10.0, 1_000, 0)) is None  # baseline only
    row = tracker.on_tick(_tick("X", 10.5, 1_600, 1, bid=10.4, ask=10.5))
    assert row is not None and row.size == 600 and row.side == 1
    state = tracker.states["X"]
    assert state.buy_volume == 600 and state.cumulative_delta == 600


def test_cumulative_volume_prevents_repeated_ltq_from_double_counting():
    tracker = OrderFlowTracker()
    tracker.on_tick(_tick("X", 10.0, 1_000, 555, last_qty=50))
    first = tracker.on_tick(_tick("X", 10.1, 1_050, 556, bid=10.0, ask=10.1, last_qty=50))
    repeated = tracker.on_tick(_tick("X", 10.1, 1_050, 556, bid=10.0, ask=10.1, last_qty=50))
    assert first is not None and first.size == 50
    assert repeated is None
    assert tracker.states["X"].trades == 1


def test_value_area_brackets_the_point_of_control():
    profile = Profile("X", datetime.now(IST).date().isoformat(), tick_size=0.5)
    for minute, price in [(0, 100), (5, 100), (35, 101), (40, 100), (70, 100), (100, 99)]:
        profile.add(price, 100, 9 * 60 + 15 + minute)
    vah, val = profile.value_area()
    assert profile.poc == 100.0
    assert val is not None and vah is not None and val <= 100.0 <= vah


def test_initial_balance_only_uses_the_first_hour():
    profile = Profile("X", datetime.now(IST).date().isoformat(), tick_size=0.5)
    profile.add(100, 10, 9 * 60 + 20)     # bracket 0
    profile.add(105, 10, 9 * 60 + 50)     # bracket 1 (still IB)
    profile.add(200, 10, 11 * 60)         # bracket 3 — must not widen the IB
    assert profile.ib_high == 105 and profile.ib_low == 100
    assert profile.high == 200
    assert profile.day_type().startswith("trend_day")


def test_bracket_boundaries():
    assert bracket_of(9 * 60 + 15) == 0
    assert bracket_of(9 * 60 + 44) == 0
    assert bracket_of(9 * 60 + 45) == 1
    assert bracket_of(15 * 60) == 11


def test_profile_book_rolls_on_a_new_session():
    book = ProfileBook()
    base = datetime.now(IST).date()
    day1 = datetime(base.year, base.month, base.day, 10, 0, tzinfo=IST).astimezone(timezone.utc)
    day2 = day1 + timedelta(days=1)
    book.on_print("X", 100.0, 50, day1)
    first = book.get("X")
    book.on_print("X", 200.0, 50, day2)
    second = book.get("X")
    assert first is not second and second.day == (base + timedelta(days=1)).isoformat()
    assert second.high == 200.0


def test_absorption_needs_pressure_without_price_progress():
    tracker = OrderFlowTracker()
    tracker.on_tick(_tick("X", 100.0, 0, 0))
    # Heavy selling, price pinned => sellers absorbed.
    for i in range(1, 40):
        tracker.on_tick(_tick("X", 100.0, i * 100, i, bid=100.0, ask=100.05))
    result = tracker.absorption("X")
    assert result["detected"] and result["side"] == "sellers_absorbed"


def test_index_ticks_build_a_tpo_profile_without_volume():
    """Indices stream no volume (the feed's index payload has 8 fields, none of
    them volume or depth). TPO is time at price, so structure must still form."""
    from macd_trader.mp_engine import MPEngine, MPSettings
    import asyncio, tempfile, os

    with tempfile.TemporaryDirectory() as folder:
        engine = MPEngine(os.path.join(folder, "mp.sqlite3"),
                          settings=MPSettings(enabled=True, auto_trade=False))
        # Session minutes: 555 = 09:15. Bracket 0 is 09:15-09:44, bracket 1
        # is 09:45-10:14, so 24010 is touched in two brackets and is the POC.
        for minute, price in [(555, 24000.0), (560, 24010.0), (590, 24020.0), (595, 24010.0)]:
            asyncio.run(engine.on_tick(_tick("NSE:NIFTY50-INDEX", price, 0, minute)))
        profile = engine.profiles.get("NSE:NIFTY50-INDEX")
        assert profile is not None, "index must still receive a profile"
        assert len(profile.tpo) == 3            # three distinct price levels
        assert profile.poc == 24010.0           # touched in two brackets
        assert sum(profile.volume.values()) == 0
        assert engine.prints_seen == 0          # no volume => no flow prints


def test_option_ticks_still_record_flow_prints():
    from macd_trader.mp_engine import MPEngine, MPSettings
    import asyncio, tempfile, os

    with tempfile.TemporaryDirectory() as folder:
        engine = MPEngine(os.path.join(folder, "mp.sqlite3"),
                          settings=MPSettings(enabled=True, auto_trade=False))
        asyncio.run(engine.on_tick(_tick("NSE:SBIN26AUG800CE", 10.0, 1000, 555, bid=9.95, ask=10.0)))
        asyncio.run(engine.on_tick(_tick("NSE:SBIN26AUG800CE", 10.5, 1600, 556, bid=10.4, ask=10.5)))
        assert engine.prints_seen == 1
        state = engine.flow.states["NSE:SBIN26AUG800CE"]
        assert state.buy_volume == 600 and state.methods["quote"] == 1


def test_same_day_off_session_ticks_do_not_enter_the_auction():
    import asyncio, tempfile, os
    from macd_trader.mp_engine import MPEngine, MPSettings

    with tempfile.TemporaryDirectory() as folder:
        engine = MPEngine(os.path.join(folder, "mp.sqlite3"),
                          settings=MPSettings(enabled=True, auto_trade=False))
        asyncio.run(engine.on_tick(_tick("NSE:SBIN-EQ", 100.0, 1000, 9 * 60)))
        asyncio.run(engine.on_tick(_tick("NSE:SBIN-EQ", 101.0, 1100, 15 * 60 + 31)))
        assert engine.profiles.get("NSE:SBIN-EQ") is None
        assert engine.prints_seen == 0
        assert engine.off_session_ticks == 2


def test_stale_dated_ticks_do_not_roll_the_session_or_enter_the_profile():
    """Illiquid contracts re-broadcast a previous session's last trade. Those
    ticks must be ignored, not treated as a new session — keying the session
    off tick timestamps wiped every profile hundreds of times a second."""
    from macd_trader.mp_engine import MPEngine, MPSettings
    import asyncio, tempfile, os

    with tempfile.TemporaryDirectory() as folder:
        engine = MPEngine(os.path.join(folder, "mp.sqlite3"),
                          settings=MPSettings(enabled=True, auto_trade=False))
        live = _tick("NSE:SBIN26AUG800CE", 10.0, 1000, 560, bid=9.95, ask=10.0)
        asyncio.run(engine.on_tick(live))
        asyncio.run(engine.on_tick(_tick("NSE:SBIN26AUG800CE", 10.5, 1600, 561, bid=10.4, ask=10.5)))
        before = engine.profiles.get("NSE:SBIN26AUG800CE")
        assert before is not None and engine.prints_seen == 1

        yesterday = _tick("NSE:ILLIQ26AUG100CE", 5.0, 500, 560)
        yesterday.timestamp = yesterday.timestamp - timedelta(days=1)
        asyncio.run(engine.on_tick(yesterday))

        assert engine.stale_ticks == 1
        assert engine.profiles.get("NSE:ILLIQ26AUG100CE") is None
        # The live contract's profile must survive untouched.
        assert engine.profiles.get("NSE:SBIN26AUG800CE") is before
        assert engine.prints_seen == 1


def _mp_engine(folder):
    from macd_trader.mp_engine import MPEngine, MPSettings
    import os
    return MPEngine(os.path.join(folder, "mp.sqlite3"),
                    settings=MPSettings(enabled=True, auto_trade=False))


def test_value_area_reclaim_needs_a_recent_probe_not_a_session_low():
    """The session low sits below VAL by construction, so a level trigger fired
    on almost every contract every tick. Only a fresh probe-and-reclaim counts."""
    import asyncio, tempfile
    from macd_trader.market_profile import Profile

    with tempfile.TemporaryDirectory() as folder:
        engine = _mp_engine(folder)
        symbol = "NSE:SBIN26AUG800CE"
        profile = Profile(symbol, datetime.now(IST).date().isoformat(), tick_size=0.05)
        # TPO mass concentrated at 100 so the 70% value area does NOT reach
        # down to the 90.0 session low — otherwise VAL == low and the case
        # under test cannot exist.
        touches = ([(556 + 30 * i, 100.0) for i in range(6)]      # POC, 6 brackets
                   + [(557 + 30 * i, 101.0) for i in range(3)]
                   + [(558 + 30 * i, 99.0) for i in range(2)]
                   + [(559, 90.0)])                               # lone spike low
        for minute, price in touches:
            profile.add(price, 500, minute)
        engine.profiles.profiles[symbol] = profile
        engine.tapes.on_price(symbol, profile.day, profile.last_price or 100.0, 0, 610)
        engine.flow.states[symbol] = engine.flow.states.get(symbol) or __import__(
            "macd_trader.orderflow", fromlist=["FlowState"]).FlowState(symbol)
        engine.flow.states[symbol].buy_volume = 1000
        engine.flow.states[symbol].cumulative_delta = 1000

        vah, val = profile.value_area()
        assert val is not None and profile.low < val, "precondition: session low below value"
        bias, _ = engine.evaluate(symbol, 101.0, 740, commit=True)
        assert getattr(bias, "setup", None) != "value_area_reclaim", \
            "stale session low must not trigger a reclaim"

        # Now actually probe below value and reclaim it.
        engine.evaluate(symbol, val - 1.0, 741, commit=True)   # marks the probe
        bias, reason = engine.evaluate(symbol, val + 0.5, 742, commit=True)
        assert bias.setup == "value_area_reclaim", reason
        assert bias.direction == "bullish" and bias.option_type == "CE"


def test_ib_range_extension_fires_once_on_the_break_not_continuously():
    import tempfile
    from macd_trader.market_profile import Profile
    from macd_trader.orderflow import FlowState

    with tempfile.TemporaryDirectory() as folder:
        engine = _mp_engine(folder)
        symbol = "NSE:SBIN26AUG800CE"
        profile = Profile(symbol, datetime.now(IST).date().isoformat(), tick_size=0.05)
        for minute, price in [(555, 100.0), (560, 101.0), (590, 100.5), (600, 100.5)]:
            profile.add(price, 500, minute)
        engine.profiles.profiles[symbol] = profile
        engine.tapes.on_price(symbol, profile.day, profile.last_price or 100.0, 0, 610)
        state = FlowState(symbol)
        state.buy_volume, state.sell_volume = 900, 100      # imbalance 0.8
        engine.flow.states[symbol] = state

        assert profile.ib_high is not None
        engine.evaluate(symbol, profile.ib_high - 1, 609, commit=True)
        first, _ = engine.evaluate(symbol, profile.ib_high + 1, 610, commit=True)
        assert first.setup == "ib_range_extension" and first.direction == "bullish"
        # Still above the IB, but the break already happened — no re-fire.
        second, reason = engine.evaluate(symbol, profile.ib_high + 2, 611, commit=True)
        assert second is None, reason


def test_read_paths_do_not_consume_edge_triggers():
    """snapshot()/leaderboard() are polled every few seconds by the UI. If they
    mutate setup state they consume the trading path's edge triggers and start
    its cooldowns — a browser refresh must never cancel a real entry."""
    import tempfile
    from macd_trader.market_profile import Profile
    from macd_trader.orderflow import FlowState

    with tempfile.TemporaryDirectory() as folder:
        engine = _mp_engine(folder)
        symbol = "NSE:SBIN26AUG800CE"
        profile = Profile(symbol, datetime.now(IST).date().isoformat(), tick_size=0.05)
        for minute, price in [(555, 100.0), (560, 101.0), (590, 100.5), (600, 100.5)]:
            profile.add(price, 500, minute)
        engine.profiles.profiles[symbol] = profile
        engine.tapes.on_price(symbol, profile.day, profile.last_price or 100.0, 0, 610)
        state = FlowState(symbol)
        state.buy_volume, state.sell_volume = 900, 100
        engine.flow.states[symbol] = state
        breakout = profile.ib_high + 1
        engine.evaluate(symbol, profile.ib_high - 1, 609, commit=True)

        # Two read-only evaluations (as the UI would do) must leave no trace...
        assert engine.evaluate(symbol, breakout, 610)[0].setup == "ib_range_extension"
        assert engine.evaluate(symbol, breakout, 611)[0].setup == "ib_range_extension"
        assert engine._setup_state[symbol]["above_ib"] is False

        # ...so the trading path still sees a fresh break, and only then latches.
        assert engine.evaluate(symbol, breakout, 612, commit=True)[0].setup == "ib_range_extension"
        assert engine.evaluate(symbol, breakout, 613, commit=True)[0] is None


def test_cvd_curve_window_outlasts_its_deepest_consumer():
    """The curve is bounded for memory, but must still cover the widest
    lookback any consumer uses (240-point display, 120-point divergence)."""
    from macd_trader.orderflow import CVD_CURVE_POINTS, OrderFlowTracker

    assert CVD_CURVE_POINTS >= 240 * 2, "must comfortably exceed the served window"
    tracker = OrderFlowTracker()
    tracker.on_tick(_tick("X", 10.0, 0, 555))
    for i in range(1, CVD_CURVE_POINTS + 500):
        tracker.on_tick(_tick("X", 10.0 + (i % 7) * 0.05, i * 10, 555, bid=10.0, ask=10.05))
    state = tracker.states["X"]
    assert len(state.cvd_curve) == CVD_CURVE_POINTS      # bounded
    assert tracker.snapshot("X")["cvd_curve"].__len__() == 240   # display intact
    assert tracker.divergence("X")["detected"] in (True, False)  # analysis intact


def test_open_type_distinguishes_not_yet_from_never_watched():
    """Starting mid-session left open_type reading "forming" all day — a claim
    the data cannot support once the 09:15-09:30 window has passed unobserved."""
    early = Profile("X", datetime.now(IST).date().isoformat(), tick_size=0.05)
    early.add(100.0, 10, 9 * 60 + 20)                    # inside the open window
    assert early.open_observed is True

    missed = Profile("Y", datetime.now(IST).date().isoformat(), tick_size=0.05)
    missed.add(100.0, 10, 11 * 60)                       # first print at 11:00
    missed.add(101.0, 10, 11 * 60 + 30)
    assert missed.open_observed is False
    assert missed.open_type() == "unobserved"

    blank = Profile("Z", datetime.now(IST).date().isoformat(), tick_size=0.05)
    assert blank.open_type() == "forming"                # genuinely too early


def test_ib_complete_flags_a_partial_initial_balance():
    """A profile that started in bracket 1 has an artificially narrow initial
    balance, which inflates range extension and biases day_type to a trend."""
    full = Profile("X", datetime.now(IST).date().isoformat(), tick_size=0.05)
    full.add(100.0, 10, 9 * 60 + 20)                     # bracket 0
    # Watching from the open is necessary but not sufficient: at 09:20 the
    # initial balance still has 55 minutes to run.
    assert full.ib_complete is False and full.first_bracket == 0
    full.add(101.0, 10, 10 * 60 + 15)                    # bracket 2 — IB closed
    assert full.ib_complete is True

    partial = Profile("Y", datetime.now(IST).date().isoformat(), tick_size=0.05)
    partial.add(100.0, 10, 9 * 60 + 50)                  # bracket 1 — missed the open
    assert partial.ib_complete is False and partial.first_bracket == 1
    assert partial.snapshot()["ib_complete"] is False


def test_order_flow_survives_a_restart_via_session_state():
    """Profiles can be rebuilt from durable candles; order flow cannot, because
    candles carry no aggressor side. Without a state snapshot every restart
    silently zeroed cumulative delta, footprint and absorption."""
    import asyncio, os, tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "mp.sqlite3")
        engine = _mp_engine(folder)
        symbol = "NSE:SBIN26AUG800CE"
        asyncio.run(engine.on_tick(_tick(symbol, 10.0, 1000, 560, bid=9.95, ask=10.0)))
        asyncio.run(engine.on_tick(_tick(symbol, 10.5, 1600, 561, bid=10.4, ask=10.5)))
        asyncio.run(engine.on_tick(_tick(symbol, 10.2, 2000, 562, bid=10.2, ask=10.25)))

        before_flow = engine.flow.states[symbol]
        before_profile = engine.profiles.get(symbol)
        assert before_flow.trades > 0 and before_flow.cumulative_delta != 0
        written = engine.save_state()
        assert written > 0
        engine.repository.close()

        # A fresh engine on the same database = the restart case.
        from macd_trader.mp_engine import MPEngine, MPSettings
        revived = MPEngine(path, settings=MPSettings(enabled=True, auto_trade=False))
        restored = revived.restore_state(engine.session_day)
        assert restored == 1

        flow = revived.flow.states[symbol]
        assert flow.cumulative_delta == before_flow.cumulative_delta
        assert flow.buy_volume == before_flow.buy_volume
        assert flow.sell_volume == before_flow.sell_volume
        assert flow.trades == before_flow.trades
        assert flow.methods == before_flow.methods
        assert flow.volume_at_price                      # footprint nodes kept

        profile = revived.profiles.get(symbol)
        assert profile is not None
        assert profile.poc == before_profile.poc
        assert profile.value_area() == before_profile.value_area()
        assert profile.ib_high == before_profile.ib_high
        revived.repository.close()


def test_session_state_is_scoped_to_its_own_day():
    """Yesterday's aggregation must never be restored into today's session."""
    import os, tempfile

    with tempfile.TemporaryDirectory() as folder:
        engine = _mp_engine(folder)
        engine.repository.save_session_state("2026-08-19", {"profiles": {}, "flows": {}})
        assert engine.restore_state("2026-08-20") == 0
        assert engine.repository.load_session_state("2026-08-19") is not None
        engine.repository.close()


class TestInitialBalanceWindow:
    """The IB is complete when its window closes, not when it opens.

    Live at 09:41 the desk reported ib_complete=True for every symbol that had
    traded, and day_type "normal_day" off an initial balance with 34 minutes
    still to run.
    """

    @staticmethod
    def _profile_at(minutes):
        from macd_trader.market_profile import Profile
        profile = Profile("NSE:TEST-EQ", "2026-08-20", tick_size=0.05)
        for minute, price in minutes:
            profile.add(price, 100, minute)
        return profile

    def test_not_complete_inside_the_first_bracket(self):
        # 09:15 -> 09:41
        profile = self._profile_at([(555, 100.0), (581, 101.0)])
        assert profile.first_bracket == 0
        assert not profile.ib_complete
        assert profile.day_type() == "forming"

    def test_not_complete_inside_the_second_bracket(self):
        # 09:15 -> 10:10, still five minutes short of the IB close
        profile = self._profile_at([(555, 100.0), (610, 101.0)])
        assert not profile.ib_complete
        assert profile.day_type() == "forming"

    def test_complete_once_the_window_closes(self):
        # 09:15 -> 10:15, the first print of bracket 2. The IB needs real width
        # or day_type reports "forming" on the zero-range guard instead.
        profile = self._profile_at([(555, 100.0), (580, 105.0), (615, 101.0)])
        assert profile.ib_complete
        assert profile.day_type() != "forming"

    def test_late_start_is_never_complete_and_reads_unobserved(self):
        # First print at 10:00, so the true initial balance is unknowable.
        # (Note 09:44 would NOT qualify — bracket 0 runs to 09:44 inclusive;
        # that case is caught by open_type()'s narrower 09:15-09:30 window.)
        profile = self._profile_at([(600, 100.0), (700, 101.0)])
        assert profile.first_bracket > 0
        assert not profile.ib_complete
        assert profile.day_type() == "unobserved"

    def test_quiet_contract_still_completes(self):
        # Traded at the open, went quiet, traded again after 10:15. The IB
        # window has demonstrably closed even though bracket 1 has no prints.
        profile = self._profile_at([(555, 100.0), (700, 101.0)])
        assert 1 not in profile.brackets_seen
        assert profile.ib_complete

    def test_ib_range_still_only_spans_the_first_hour(self):
        profile = self._profile_at([(555, 100.0), (600, 105.0), (700, 130.0)])
        assert profile.ib_high == 105.0
        assert profile.ib_low == 100.0
        # Extension beyond a closed IB is what day_type is allowed to read.
        assert profile.day_type() in {
            "trend_day_up", "normal_variation_up", "neutral_day", "normal_day",
        }


def test_session_state_is_actually_written_from_the_tick_path():
    """save_state() existed, was tested, and had no caller.

    The throttle field and the interval constant were both defined, but nothing
    in the engine ever invoked them, so mp_session_state stayed empty through a
    full session and every restart discarded cumulative delta and footprint.
    Unit-testing save_state() directly could not catch that — this drives ticks.
    """
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as folder:
        engine = _mp_engine(folder)
        symbol = "NSE:SBIN26AUG800CE"
        async def drive_and_flush():
            await engine.on_tick(_tick(symbol, 10.0, 1000, 560, bid=9.95, ask=10.0))
            await engine.on_tick(_tick(symbol, 10.5, 1600, 561, bid=10.4, ask=10.5))
            await engine.flush_state()
        asyncio.run(drive_and_flush())

        assert engine.state_save_error is None, engine.state_save_error
        assert engine.state_saved_at is not None, "save_state() was never called from on_tick"
        assert engine.state_rows_saved > 0

        # Prove it is on disk and usable, not just that a field was set.
        day = engine.session_day
        engine.repository.close()
        from macd_trader.mp_engine import MPEngine, MPSettings
        import os
        revived = MPEngine(os.path.join(folder, "mp.sqlite3"),
                           settings=MPSettings(enabled=True, auto_trade=False))
        assert revived.restore_state(day) == 1
        assert revived.flow.states[symbol].cumulative_delta != 0


def _book_tick(symbol, ltp, cum_volume, minute, bid, ask, tbq, tsq, last_qty=None):
    """A tick carrying depth AND the pending totals, so the three-vote
    classifier has all three votes and the prior-book memory has something to
    round-trip through the checkpoint."""
    today = datetime.now(IST).date()
    moment = datetime(today.year, today.month, today.day, tzinfo=IST) + timedelta(minutes=minute)
    return Tick(symbol, ltp, cum_volume, timestamp=moment.astimezone(timezone.utc),
                bid=bid, ask=ask, last_qty=last_qty,
                total_buy_qty=tbq, total_sell_qty=tsq)


def test_the_checkpoint_carries_every_field_restore_state_reads():
    """restore_state() reads weighted_delta, conf_volume, the prior book, the
    prior pending totals, speed_samples and profile.day_types.

    Every one of them fails SILENTLY. A key renamed or dropped in save_state()
    leaves the field at its dataclass default, and the desk simply resumes with
    the confidence-weighted CVD at zero and the first print after the restart
    classified against no book -- no exception, no log line, a number on screen
    that is wrong by an unbounded amount. Asserting that a save happened cannot
    catch that, so this asserts the values survive the round trip.
    """
    import asyncio, os, tempfile

    with tempfile.TemporaryDirectory() as folder:
        engine = _mp_engine(folder)
        symbol = "NSE:SBIN26AUG800CE"

        async def drive():
            # Four brackets, so at least one day type is latched as a bracket
            # closes; pending totals move with the trades so the pending vote
            # fires and the confidence is not the tick rule's floor.
            await engine.on_tick(_book_tick(symbol, 10.0, 1000, 560, 9.95, 10.00, 9000, 9000))
            await engine.on_tick(_book_tick(symbol, 10.5, 1600, 590, 10.40, 10.50, 9000, 8300))
            await engine.on_tick(_book_tick(symbol, 10.2, 2200, 620, 10.20, 10.30, 8400, 8300))
            await engine.on_tick(_book_tick(symbol, 10.9, 2900, 650, 10.85, 10.90, 8400, 7600))
            await engine.flush_state()

        asyncio.run(drive())
        # The tick path's own save is throttled to once a minute (covered by
        # test_state_save_is_throttled), so persist the FINAL state explicitly:
        # this test is about what the payload carries, not when it is written.
        assert engine.save_state() > 0
        day = engine.session_day
        live_flow = engine.flow.states[symbol]
        live_profile = engine.profiles.get(symbol)
        assert live_flow.weighted_delta != 0.0, "no print carried a confidence to persist"
        assert live_profile.day_types, "no bracket closed, so nothing latched to persist"
        assert live_flow.speed_samples, "no speed window closed, so nothing to persist"
        engine.repository.close()

        revived = _mp_engine(folder)
        assert revived.restore_state(day) == 1
        state = revived.flow.states[symbol]
        profile = revived.profiles.get(symbol)

        assert state.weighted_delta == live_flow.weighted_delta
        assert state.conf_volume == live_flow.conf_volume
        assert (state.prior_bid, state.prior_ask) == (live_flow.prior_bid, live_flow.prior_ask)
        assert (state.prior_tbq, state.prior_tsq) == (live_flow.prior_tbq, live_flow.prior_tsq)
        assert list(state.speed_samples) == list(live_flow.speed_samples)[-60:]
        assert profile.day_types == live_profile.day_types
        # And the six-element print rows come back as prints that still carry
        # the method and the confidence they were classified with.
        assert state.recent[-1].method == live_flow.recent[-1].method
        assert state.recent[-1].confidence == live_flow.recent[-1].confidence
        revived.repository.close()


def test_a_legacy_checkpoint_loads_without_the_fields_it_predates():
    """`recent` rows gained a method and a confidence, and cvd_curve a weighted
    leg, after the first checkpoints were written. A restart on the morning
    after a deploy reads yesterday's shorter rows, and Print(*v) unpacking a
    five-element row must not raise -- the alternative is a desk that will not
    start."""
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        engine = _mp_engine(folder)
        symbol = "NSE:SBIN26AUG800CE"
        day = "2026-09-03"
        engine.repository.save_session_state(day, {
            "profiles": {symbol: {
                "tpo": {"10.0": [0, 1]}, "volume": {"10.0": 500},
                "open": 10.0, "last": 10.0, "high": 10.0, "low": 10.0,
                "ib_high": 10.0, "ib_low": 10.0,
                "first_bracket": 0, "last_minute": 600, "open_window": [10.0],
                # "day_types" absent entirely, as it was before the latch.
            }},
            "flows": {symbol: {
                "cumulative_delta": 120.0, "buy_volume": 300.0, "sell_volume": 180.0,
                "trades": 2, "last_price": 10.0,
                # No weighted_delta, conf_volume or prior book: this payload
                # predates all of them.
                "recent": [[1_788_241_500.0, 10.0, 100, 1, "quote"]],
                "cvd_curve": [[1_788_241_500, 120.0, 10.0]],
            }},
        })

        assert engine.restore_state(day) == 1
        state = engine.flow.states[symbol]
        assert state.recent[-1].method == "quote"
        # None, never 0.0: a print that predates the score did not score zero.
        assert state.recent[-1].confidence is None
        assert state.weighted_delta == 0.0
        assert (state.prior_bid, state.prior_ask) == (None, None)
        assert engine.profiles.get(symbol).day_types == {}
        # And the shorter curve row publishes a null weighted leg rather than
        # the raw value wearing its label.
        assert engine.flow.snapshot(symbol)["cvd_curve"][-1]["wcvd"] is None
        engine.repository.close()


def test_state_save_is_throttled():
    """Persisting on every tick would write hundreds of times a second."""
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as folder:
        engine = _mp_engine(folder)
        symbol = "NSE:SBIN26AUG800CE"
        async def drive():
            # Two ticks: the first has no prior quote to classify against and
            # returns before the save point.
            await engine.on_tick(_tick(symbol, 10.0, 1000, 560, bid=9.95, ask=10.0))
            await engine.on_tick(_tick(symbol, 10.5, 1600, 561, bid=10.4, ask=10.5))
            await engine.flush_state()
            first = engine.state_saved_at
            marker = engine._last_state_save
            assert first is not None
            for i in range(5):
                await engine.on_tick(_tick(symbol, 10.1 + i / 100, 1100, 561, bid=10.0, ask=10.1))
            return first, marker
        first, marker = asyncio.run(drive())
        assert engine.state_saved_at == first
        assert engine._last_state_save == marker
