"""The auction desk takes both directions, and says why when it will not trade.

The desk generated 500 signals and placed zero orders. Four things had to be
true at once for that: lot sizes were only ever fed the option contracts while
the desk watched futures and equities, order placement was gated off, the
notional cap was below one index-futures lot, and every setup was bullish.
"""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from datetime import datetime, timedelta

from macd_trader.directional import SessionTape, TapeBook
from macd_trader.market_profile import IST, Profile
from macd_trader.mp_engine import MPEngine, MPSettings
from macd_trader.models import Trade
from macd_trader.orderflow import FlowState


def _engine(folder: str, **overrides) -> MPEngine:
    settings = MPSettings(enabled=True, auto_trade=True, **overrides)
    return MPEngine(f"{folder}/mp.sqlite3", settings=settings)


def _profile(symbol: str, touches) -> Profile:
    profile = Profile(symbol, datetime.now(IST).date().isoformat(), tick_size=0.05)
    for minute, price in touches:
        profile.add(price, 500, minute)
    return profile


def _seeded(engine: MPEngine, underlying: str, *, buy: int, sell: int) -> Profile:
    profile = _profile(underlying, [(555, 100.0), (560, 101.0), (590, 100.5), (600, 100.5)])
    engine.profiles.profiles[underlying] = profile
    state = FlowState(underlying)
    state.buy_volume, state.sell_volume = buy, sell
    state.cumulative_delta = buy - sell
    engine.flow.states[underlying] = state
    engine.tapes.on_price(underlying, profile.day, 100.5, 0, 610)
    engine.set_option_map({underlying: {"CE": "NSE:SBIN26SEP800CE", "PE": "NSE:SBIN26SEP800PE"}})
    engine.set_directional_scope([underlying])
    # Establish the observed side of each level before asking for a crossing.
    engine.evaluate(underlying, 100.5, 610, commit=True)
    return profile


class TestBothDirections:
    def test_downside_initial_balance_extension_buys_the_put(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder)
            underlying = "NSE:SBIN-EQ"
            profile = _seeded(engine, underlying, buy=100, sell=900)   # imbalance -0.8
            bias, reason = engine.evaluate(underlying, profile.ib_low - 1, 610, commit=True)
            assert bias is not None, reason
            assert bias.setup == "ib_range_extension"
            assert bias.direction == "bearish"
            assert bias.option_type == "PE"
            assert engine.contract_for(underlying, "PE") == "NSE:SBIN26SEP800PE"

    def test_upside_extension_still_buys_the_call(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder)
            underlying = "NSE:SBIN-EQ"
            profile = _seeded(engine, underlying, buy=900, sell=100)
            bias, reason = engine.evaluate(underlying, profile.ib_high + 1, 610, commit=True)
            assert bias is not None and bias.option_type == "CE", reason

    def test_responsive_selling_mirrors_responsive_buying(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder)
            underlying = "NSE:SBIN-EQ"
            profile = _seeded(engine, underlying, buy=100, sell=900)
            vah, _ = profile.value_area()
            engine.evaluate(underlying, vah + 1.0, 611, commit=True)      # probe above value
            bias, reason = engine.evaluate(underlying, vah - 0.5, 612, commit=True)
            assert bias is not None, reason
            assert bias.setup == "value_area_reclaim" and bias.direction == "bearish"


class TestSelectiveOvernightCarry:
    @staticmethod
    def _held(engine: MPEngine, underlying: str, option_type: str, *, imbalance: float) -> str:
        symbol = f"NSE:SBIN26SEP800{option_type}"
        profile = _profile(underlying, [(555, 100.0), (560, 101.0), (590, 100.5), (600, 100.5)])
        # Carry reads the closing location already computed by Market Profile;
        # keep this unit test focused on the carry gate rather than VA math.
        profile.position = lambda: "above_value" if option_type == "CE" else "below_value"
        engine.profiles.profiles[underlying] = profile
        flow = FlowState(underlying)
        if imbalance > 0:
            flow.buy_volume, flow.sell_volume = 900, 100
        else:
            flow.buy_volume, flow.sell_volume = 100, 900
        flow.cumulative_delta = flow.buy_volume - flow.sell_volume
        engine.flow.states[underlying] = flow
        engine.portfolio.apply_trade(Trade("entry", symbol, "BUY", 750, 40.0, lot_size=750))
        engine.entry_state[symbol] = {"option_type": option_type, "underlying": underlying}
        engine.underlying_of[symbol] = underlying
        return symbol

    def test_aligned_call_is_carried(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder, allow_overnight_carry=True)
            symbol = self._held(engine, "NSE:SBIN-EQ", "CE", imbalance=0.8)
            allowed, reason = engine.carry_decision(symbol)
            assert allowed is True
            assert "above_value" in reason and "imbalance +0.800" in reason

    def test_aligned_put_is_carried(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder, allow_overnight_carry=True)
            symbol = self._held(engine, "NSE:SBIN-EQ", "PE", imbalance=-0.8)
            allowed, reason = engine.carry_decision(symbol)
            assert allowed is True
            assert "below_value" in reason and "imbalance -0.800" in reason

    def test_disabled_or_expiring_contract_is_not_carried(self):
        with tempfile.TemporaryDirectory() as folder:
            disabled = _engine(folder)
            symbol = self._held(disabled, "NSE:SBIN-EQ", "CE", imbalance=0.8)
            assert disabled.carry_decision(symbol)[0] is False

        with tempfile.TemporaryDirectory() as folder:
            expiring = _engine(folder, allow_overnight_carry=True)
            symbol = self._held(expiring, "NSE:SBIN-EQ", "CE", imbalance=0.8)
            expiring.set_expiring_symbols({symbol})
            assert expiring.carry_decision(symbol) == (False, "contract expires today")

    def test_session_close_preserves_only_confirmed_carry(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder, allow_overnight_carry=True)
            kept = self._held(engine, "NSE:SBIN-EQ", "CE", imbalance=0.8)
            closed = "NSE:SBIN26SEP780CE"
            engine.portfolio.apply_trade(Trade("entry-2", closed, "BUY", 750, 40.0, lot_size=750))
            engine.entry_state[closed] = {"option_type": "CE"}
            submitted = []

            async def record(symbol, side, quantity, **_):
                submitted.append((symbol, side, quantity))

            engine.submit = record
            asyncio.run(engine.close_positions())
            assert submitted == [(closed, "SELL", 750)]
            assert engine.entry_state[kept]["carry"]["allowed"] is True
            assert engine.entry_state[closed]["carry"]["allowed"] is False


class TestScope:
    def test_the_desk_reads_the_underlying_not_the_option_premium(self):
        """A market profile of an option premium is not a profile of anything."""
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder)
            _seeded(engine, "NSE:SBIN-EQ", buy=900, sell=100)
            bias, reason = engine.evaluate("NSE:SBIN26SEP800CE", 42.0, 610, commit=True)
            assert bias is None
            assert "scope" in reason

    def test_default_snapshot_focuses_a_directional_instrument(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder)
            underlying = "NSE:SBIN-EQ"
            _seeded(engine, underlying, buy=900, sell=100)
            option = "NSE:SBIN26SEP800CE"
            noisy = _profile(option, [(555, 40.0), (590, 42.0)])
            engine.profiles.profiles[option] = noisy
            state = FlowState(option)
            state.trades = 10_000
            engine.flow.states[option] = state

            assert engine.snapshot()["focus"] == underlying


class TestRejectionsAreVisible:
    def _bias(self, engine, underlying, profile):
        bias, _ = engine.evaluate(underlying, profile.ib_high + 1, 610, commit=True)
        return bias

    def test_a_missing_lot_size_is_reported_not_swallowed(self):
        """This is what actually happened: 500 signals, 0 orders, no reason."""
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder)
            underlying = "NSE:SBIN-EQ"
            profile = _seeded(engine, underlying, buy=900, sell=100)
            engine.last_prices["NSE:SBIN26SEP800CE"] = 40.0
            bias = self._bias(engine, underlying, profile)
            asyncio.run(engine._enter(underlying, bias, None))
            assert engine.last_order_rejection is not None
            assert "lot size" in engine.last_order_rejection
            assert not engine.portfolio.positions

    def test_a_lot_above_the_notional_cap_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder, max_notional_per_trade=100_000.0)
            underlying = "NSE:SBIN-EQ"
            profile = _seeded(engine, underlying, buy=900, sell=100)
            engine.set_lot_sizes({"NSE:SBIN26SEP800CE": 750})
            engine.last_prices["NSE:SBIN26SEP800CE"] = 400.0        # 750 * 400 = 300,000
            bias = self._bias(engine, underlying, profile)
            asyncio.run(engine._enter(underlying, bias, None))
            assert "exceeds" in (engine.last_order_rejection or "")
            assert not engine.portfolio.positions

    def test_a_lot_inside_the_cap_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = _engine(folder, max_notional_per_trade=100_000.0)
            underlying = "NSE:SBIN-EQ"
            profile = _seeded(engine, underlying, buy=900, sell=100)
            engine.set_lot_sizes({"NSE:SBIN26SEP800CE": 750})
            engine.last_prices["NSE:SBIN26SEP800CE"] = 40.0         # 750 * 40 = 30,000
            assert engine.target_quantity("NSE:SBIN26SEP800CE", 40.0) == 2250
            bias = self._bias(engine, underlying, profile)
            assert bias is not None
            # submit() still refuses entries outside the regular session, which
            # is a separate guard; assert the sizing gate itself passed.
            asyncio.run(engine._enter(underlying, bias, None))
            assert "lot size" not in (engine.last_order_rejection or "")
            assert "exceeds" not in (engine.last_order_rejection or "")


class TestSessionTape:
    def test_the_opening_range_closes_after_thirty_minutes(self):
        tape = SessionTape(day="2026-08-26")
        for offset in range(0, 30):
            tape.on_price(100 + offset * 0.1, 10, 555 + offset)
        assert not tape.opening_range_complete
        tape.on_price(104.0, 10, 585)
        assert tape.opening_range_complete
        assert tape.or_high == 102.9 and tape.or_low == 100.0
        # The 104 print lands after the range closed and must not widen it.
        assert tape.or_high < 104.0

    def test_vwap_is_volume_weighted_not_an_average_of_prices(self):
        tape = SessionTape(day="2026-08-26")
        tape.on_price(100.0, 1, 555)
        tape.on_price(200.0, 9, 556)
        assert tape.vwap == (100.0 * 1 + 200.0 * 9) / 10

    def test_the_channel_excludes_the_current_minute(self):
        tape = SessionTape(day="2026-08-26")
        for offset in range(25):
            tape.on_price(100.0, 1, 555 + offset)
        tape.on_price(150.0, 1, 580)
        high, _ = tape.channel()
        assert high == 100.0, "a break must be measured against PRIOR bars, not this one"

    def test_the_channel_uses_minute_highs_and_lows_not_first_prints(self):
        tape = SessionTape(day="2026-08-26")
        for offset in range(21):
            minute = 555 + offset
            tape.on_price(100.0, 1, minute)
            tape.on_price(110.0 if offset == 10 else 101.0, 1, minute)
            tape.on_price(90.0 if offset == 11 else 99.0, 1, minute)
        high, low = tape.channel()
        assert high == 110.0 and low == 90.0

    def test_first_observation_is_a_baseline_not_a_crossing(self):
        from macd_trader.directional import _crossed

        marks = {}
        assert _crossed(marks, "above_vwap", True) is False
        assert _crossed(marks, "above_vwap", False) is False
        assert _crossed(marks, "above_vwap", True) is True

    def test_a_new_day_resets_the_tape(self):
        book = TapeBook()
        book.on_price("NSE:SBIN-EQ", "2026-08-25", 100.0, 5, 555)
        book.on_price("NSE:SBIN-EQ", "2026-08-26", 200.0, 5, 555)
        assert book.get("NSE:SBIN-EQ").or_high == 200.0

    def test_tape_state_survives_a_restart_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            day = datetime.now(IST).date().isoformat()
            first = _engine(folder)
            first.session_day = day
            first.set_directional_scope(["NSE:SBIN-EQ"])
            for offset in range(25):
                first.tapes.on_price("NSE:SBIN-EQ", day, 100 + offset, 10, 555 + offset)
            first.save_state()

            restored = _engine(folder)
            restored.set_directional_scope(["NSE:SBIN-EQ"])
            assert restored.restore_state(day) > 0 or restored.tapes.get("NSE:SBIN-EQ") is not None
            tape = restored.tapes.get("NSE:SBIN-EQ")
            assert tape is not None
            assert tape.last_minute == 579
            assert tape.vwap_volume == 250
            assert tape.opening_range_complete is False

    def test_missing_tape_is_backfilled_from_durable_ohlcv(self):
        with tempfile.TemporaryDirectory() as folder:
            history = f"{folder}/history.sqlite3"
            db = sqlite3.connect(history)
            db.execute(
                """CREATE TABLE historical_candles (
                     symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER,
                     open REAL, high REAL, low REAL, close REAL, volume INTEGER)"""
            )
            start = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
            for offset in range(31):
                moment = start + timedelta(minutes=offset)
                db.execute(
                    "INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)",
                    ("NSE:SBIN-EQ", 60, int(moment.timestamp()), 100, 105, 95, 101, 10),
                )
            db.commit()
            db.close()

            engine = MPEngine(f"{folder}/mp.sqlite3", settings=MPSettings(enabled=True),
                              history_database_path=history)
            engine.set_directional_scope(["NSE:SBIN-EQ"])
            # Pin the clock past the last seeded candle. Without this the
            # test only passed when the suite happened to run after 09:46 IST.
            rows = engine.backfill_session(
                include_profiles=False, tape_symbols={"NSE:SBIN-EQ"},
                now=start + timedelta(minutes=31),
            )
            tape = engine.tapes.get("NSE:SBIN-EQ")
            assert rows == 31 and tape is not None
            assert tape.or_high == 105 and tape.or_low == 95
            assert tape.opening_range_complete
            assert tape.channel() == (105, 95)
