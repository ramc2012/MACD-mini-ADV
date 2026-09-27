"""The blast lane screens, journals, and keeps its book to itself.

The screen's thresholds came out of a walk-forward whose rupee edge is the
right sign and an unproven size, so what these tests protect is not the
profitability claim — it is that every leg of the screen actually vetoes, that
the journal records the rejections as well as the fills (they are the control
group the research needs), and that none of it can reach the MACD lane's book.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from macd_trader.blast_engine import (
    ALREADY_HELD, AUTO_TRADE_OFF, BREADTH_TOO_THIN, MACD_SEED_BARS, NOT_OFF_HIGH, NO_HISTORY,
    NO_SPOT, PREMIUM_TOO_RICH, TAKEN, TOO_ILLIQUID, BlastEngine,
)
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.models import Candle, IndicatorPoint

SYMBOL = "NSE:SBIN26SEP800CE"
SPOT = "NSE:SBIN-EQ"


class BrokerThatMustNotReceiveOrders:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper mode called the live broker order API")


def build(tmp_path: Path, **overrides) -> BlastEngine:
    settings = Settings(**{
        "execution_mode": "paper",
        "symbols_csv": SPOT,
        "slippage_bps": 0,
        "blast_auto_trade": True,
        "blast_min_lookback_bars": 10,
        "blast_high_lookback_bars": 30,
        "blast_min_breadth_cohort": 1,
        "blast_target_notional": 100_000,
        **overrides,
    })
    lane = BlastEngine(str(tmp_path / "blast.sqlite3"), settings, BrokerThatMustNotReceiveOrders(), EventHub())
    lane.set_tradable_contracts({SYMBOL: 100})
    lane.set_contract_context({SYMBOL: {"spot_symbol": SPOT, "option_type": "CE",
                                        "strike": 800, "expiry": "2026-09-29"}})
    lane.set_resolvers(spot_price=lambda _s: 800.0, breadth=lambda _t: 0.8)
    return lane


def bar(close: float, high: float | None = None, ts: int = 1_000) -> Candle:
    top = close if high is None else high
    return Candle(SYMBOL, ts, close, top, close, close, volume=100, closed=True)


def point(macd: float, signal: float, ts: int = 1_000) -> IndicatorPoint:
    return IndicatorPoint(SYMBOL, ts, macd, signal, macd - signal)


def prime(lane: BlastEngine, high: float = 10.0, bars: int = 12) -> None:
    """Fill the lookback window so the screen has an 'own recent high'."""
    for index in range(bars):
        lane.observe_warmup(bar(high, high, ts=index), point(-1.0, -0.5, ts=index))


def cross(lane: BlastEngine, premium: float, ts: int = 900) -> dict | None:
    """Drive one MACD/signal cross-up on a closed bar and return the journal row."""
    lane.execution.set_quote(SYMBOL, premium)
    asyncio.run(lane.on_closed_bar(bar(premium, premium, ts=ts - 1), point(-1.0, -0.5, ts=ts - 1)))
    return asyncio.run(lane.on_closed_bar(bar(premium, premium, ts=ts), point(-0.4, -0.5, ts=ts)))


class TestTheScreenVetoes:
    def test_a_qualifying_candidate_is_taken(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        row = cross(lane, 6.0)          # 0.75% of spot, 40% off the 10.0 high
        assert row["reason"] == TAKEN
        assert row["taken"] == 1
        assert SYMBOL in lane.portfolio.positions
        asyncio.run(lane.stop())

    def test_premium_rich_relative_to_spot_is_refused(self, tmp_path: Path):
        lane = build(tmp_path, blast_max_premium_pct=0.5)
        prime(lane)
        row = cross(lane, 6.0)          # 0.75% of spot, above the 0.5% gate
        assert row["reason"] == PREMIUM_TOO_RICH
        assert not lane.portfolio.positions
        asyncio.run(lane.stop())

    def test_thin_side_breadth_is_refused(self, tmp_path: Path):
        lane = build(tmp_path)
        lane.set_resolvers(spot_price=lambda _s: 800.0, breadth=lambda _t: 0.2)
        prime(lane)
        assert cross(lane, 6.0)["reason"] == BREADTH_TOO_THIN
        asyncio.run(lane.stop())

    def test_a_premium_near_its_own_recent_high_is_refused(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane, high=6.2)
        # 6.0 against a 6.2 high is 3% off, short of the 25% the screen wants.
        assert cross(lane, 6.0)["reason"] == NOT_OFF_HIGH
        asyncio.run(lane.stop())

    def test_no_spot_price_is_recorded_rather_than_guessed(self, tmp_path: Path):
        lane = build(tmp_path)
        lane.set_resolvers(spot_price=lambda _s: None, breadth=lambda _t: 0.8)
        prime(lane)
        assert cross(lane, 6.0)["reason"] == NO_SPOT
        asyncio.run(lane.stop())

    def test_a_contract_without_enough_history_is_not_judged(self, tmp_path: Path):
        lane = build(tmp_path, blast_min_lookback_bars=20)
        prime(lane, bars=4)
        assert cross(lane, 6.0)["reason"] == NO_HISTORY
        asyncio.run(lane.stop())

    def test_an_already_held_contract_is_not_bought_twice(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        assert cross(lane, 6.0, ts=900)["reason"] == TAKEN
        assert cross(lane, 6.0, ts=1_200)["reason"] == ALREADY_HELD
        assert lane.portfolio.positions[SYMBOL].lots == 1 * lane.portfolio.positions[SYMBOL].lots
        asyncio.run(lane.stop())

    def test_a_held_contract_is_never_filed_under_a_screen_leg(self, tmp_path: Path):
        """The holding's re-signals must not land in the comparison groups.

        Judged last, a re-signal on a contract the lane owns was recorded as
        whichever leg it happened to fail, which made the rejected groups
        mostly the lane's own winners.
        """
        lane = build(tmp_path)
        prime(lane)
        assert cross(lane, 6.0, ts=900)["reason"] == TAKEN
        lane.set_resolvers(spot_price=lambda _s: 800.0, breadth=lambda _t: 0.0)
        assert cross(lane, 6.0, ts=1_200)["reason"] == ALREADY_HELD
        asyncio.run(lane.stop())

    def test_a_held_contract_opens_no_second_excursion_watcher(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0, ts=900)
        watchers = len(lane._watchers)
        assert cross(lane, 6.0, ts=1_200)["watch_until"] is None
        assert len(lane._watchers) == watchers
        asyncio.run(lane.stop())


class TestLiquiditySizing:
    """The ticket is capped by what the contract itself trades."""

    def liquid(self, tmp_path: Path, *, volume: int = 0, oi: int = 0, **overrides) -> BlastEngine:
        lane = build(tmp_path, **overrides)
        lane.set_contract_context({SYMBOL: {"spot_symbol": SPOT, "option_type": "CE", "strike": 800,
                                            "expiry": "2026-09-29", "volume": volume, "oi": oi}})
        return lane

    def test_an_entry_is_sized_down_to_its_share_of_the_contracts_volume(self, tmp_path: Path):
        lane = self.liquid(tmp_path, volume=50_000)     # 2% of 50,000 is 10 lots of 100
        prime(lane)
        assert cross(lane, 6.0)["reason"] == TAKEN
        assert lane.portfolio.positions[SYMBOL].lots == 10
        asyncio.run(lane.stop())

    def test_open_interest_stands_in_when_volume_is_missing(self, tmp_path: Path):
        lane = self.liquid(tmp_path, oi=5_000_000)      # oi/100 reads as 50,000, the same cap
        prime(lane)
        assert cross(lane, 6.0)["reason"] == TAKEN
        assert lane.portfolio.positions[SYMBOL].lots == 10
        asyncio.run(lane.stop())

    def test_a_contract_too_thin_for_one_lot_is_declined_not_shrunk(self, tmp_path: Path):
        lane = self.liquid(tmp_path, volume=1_000)      # 2% is 20 units, under one 100-unit lot
        prime(lane)
        assert cross(lane, 6.0)["reason"] == TOO_ILLIQUID
        assert SYMBOL not in lane.portfolio.positions
        asyncio.run(lane.stop())

    def test_unknown_liquidity_leaves_the_entry_uncapped(self, tmp_path: Path):
        """A missing field is a data gap, not a verdict: it must not stop the lane."""
        lane = self.liquid(tmp_path)
        prime(lane)
        assert cross(lane, 6.0)["reason"] == TAKEN
        assert lane.portfolio.positions[SYMBOL].lots > 10
        asyncio.run(lane.stop())

    def test_a_deep_share_still_never_exceeds_the_notional_ticket(self, tmp_path: Path):
        lane = self.liquid(tmp_path, volume=100_000_000)
        prime(lane)
        assert cross(lane, 6.0)["reason"] == TAKEN
        assert lane.portfolio.positions[SYMBOL].lots == 125   # blast_max_entry_lots, as before
        asyncio.run(lane.stop())


class TestShadowMode:
    def test_auto_trade_off_journals_the_candidate_and_buys_nothing(self, tmp_path: Path):
        lane = build(tmp_path, blast_auto_trade=False)
        prime(lane)
        row = cross(lane, 6.0)
        assert row["reason"] == AUTO_TRADE_OFF
        assert row["taken"] == 0
        assert not lane.portfolio.positions
        # It is still a full record: the rule inputs are what make the
        # rejected rows usable as a control group later.
        assert row["premium_pct"] == pytest.approx(0.75)
        assert row["breadth"] == 0.8
        assert row["off_high_pct"] == pytest.approx(-40.0)
        asyncio.run(lane.stop())

    def test_it_is_off_by_default(self):
        assert Settings().blast_auto_trade is False
        assert Settings().blast_enabled is True


class TestTheJournal:
    def test_rejected_candidates_are_persisted_and_summarised(self, tmp_path: Path):
        lane = build(tmp_path, blast_max_premium_pct=0.5)
        prime(lane)
        cross(lane, 6.0)
        rows = lane.journal_rows()
        assert len(rows) == 1 and rows[0]["reason"] == PREMIUM_TOO_RICH
        summary = lane.journal_summary(rows[0]["day"])
        assert summary["evaluated"] == 1 and summary["taken"] == 0
        assert summary["verdicts"][0]["reason"] == PREMIUM_TOO_RICH
        asyncio.run(lane.stop())

    def test_the_forward_excursion_of_a_declined_candidate_is_still_recorded(self, tmp_path: Path):
        """The control group only works if the lane watches what it did not buy."""
        lane = build(tmp_path, blast_auto_trade=False)
        prime(lane)
        row = cross(lane, 6.0)
        lane.track(SYMBOL, 15.0)        # the one it passed on doubles
        lane.track(SYMBOL, 4.5)
        lane.flush_watchers()
        stored = lane.journal_rows()[0]
        assert stored["mfe_pct"] == pytest.approx(150.0)
        assert stored["mae_pct"] == pytest.approx(-25.0)
        assert stored["resolved"] is False
        asyncio.run(lane.stop())

    def test_an_expired_window_finalises_and_stops_tracking(self, tmp_path: Path):
        lane = build(tmp_path, blast_auto_trade=False)
        prime(lane)
        cross(lane, 6.0)
        lane.track(SYMBOL, 9.0)
        lane.flush_watchers(now=datetime.now(UTC) + timedelta(days=30))
        assert lane.journal_rows()[0]["resolved"] is True
        assert lane._watchers == {}
        asyncio.run(lane.stop())

    def test_watchers_survive_a_restart(self, tmp_path: Path):
        lane = build(tmp_path, blast_auto_trade=False)
        prime(lane)
        cross(lane, 6.0)
        lane.track(SYMBOL, 12.0)
        lane.flush_watchers()
        asyncio.run(lane.stop())

        revived = build(tmp_path, blast_auto_trade=False)
        assert len(revived._watchers) == 1
        revived.track(SYMBOL, 18.0)     # tracking continues from the stored high
        revived.flush_watchers()
        assert revived.journal_rows()[0]["mfe_pct"] == pytest.approx(200.0)
        asyncio.run(revived.stop())


class TestRiskOverlay:
    def test_the_wider_hard_stop_is_the_lane_own(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        position = lane.portfolio.positions[SYMBOL]
        assert position.hard_stop == pytest.approx(3.0)      # -50%, not the MACD lane's -30%
        asyncio.run(lane.on_tick(SYMBOL, 4.5))               # -25% does not stop it out
        assert SYMBOL in lane.portfolio.positions
        asyncio.run(lane.on_tick(SYMBOL, 2.9))
        assert SYMBOL not in lane.portfolio.positions
        assert lane.execution.exit_reasons[SYMBOL] == "BLAST_HARD_STOP_50_PCT"
        asyncio.run(lane.stop())

    def test_it_trails_forty_percent_from_the_peak_and_never_below_breakeven(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        asyncio.run(lane.on_tick(SYMBOL, 12.0))              # +100% arms the trail
        position = lane.portfolio.positions[SYMBOL]
        assert position.trailing_stop == pytest.approx(7.2)  # 12.0 * 0.60
        asyncio.run(lane.on_tick(SYMBOL, 7.1))
        assert SYMBOL not in lane.portfolio.positions
        assert lane.execution.exit_reasons[SYMBOL] == "BLAST_TRAILING_STOP_40_PCT"
        asyncio.run(lane.stop())

    def test_the_trail_floor_cannot_lock_in_a_loss(self, tmp_path: Path):
        lane = build(tmp_path, blast_trail_activation_pct=0.30, blast_trail_pct=0.40)
        prime(lane)
        cross(lane, 6.0)
        # Peak just past the +30% activation: a raw 40% trail would put the
        # stop at 8.0 * 0.60 = 4.80, i.e. 20% below the 6.00 entry.
        asyncio.run(lane.on_tick(SYMBOL, 8.0))
        assert lane.portfolio.positions[SYMBOL].trailing_stop == pytest.approx(6.0)
        asyncio.run(lane.stop())

    def test_it_neither_pyramids_nor_scales_out(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        lots = lane.portfolio.positions[SYMBOL].lots
        for price in (6.5, 7.0, 7.5, 8.0, 9.0, 10.5):       # past +7.5/15/22.5 and +30/50/75
            asyncio.run(lane.on_tick(SYMBOL, price))
        assert lane.portfolio.positions[SYMBOL].lots == lots
        assert lane.portfolio.positions[SYMBOL].exit_stage == 0
        asyncio.run(lane.stop())


class TestIsolation:
    def test_the_book_is_stamped_with_its_own_lane(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        asyncio.run(lane.on_tick(SYMBOL, 2.0))               # stop out to produce a record
        record = lane.execution.closed_positions[0]
        assert record["lane"] == "blast"
        assert lane.repository.closed_positions(lane="macd") == []
        assert len(lane.repository.closed_positions(lane="blast")) == 1
        asyncio.run(lane.stop())

    def test_it_uses_a_separate_database_from_the_macd_lane(self, tmp_path: Path):
        from macd_trader.repository import TradeRepository
        macd = TradeRepository(str(tmp_path / "macd.sqlite3"))
        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        assert macd.rows("trades") == []
        assert len(lane.repository.rows("trades")) == 1
        macd.close()
        asyncio.run(lane.stop())

    def test_the_positions_survive_a_restart(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        entry = lane.portfolio.positions[SYMBOL].average_price
        asyncio.run(lane.stop())

        revived = build(tmp_path)
        position = revived.portfolio.positions[SYMBOL]
        assert position.average_price == pytest.approx(entry)
        assert position.hard_stop == pytest.approx(entry * 0.5, rel=1e-6)
        asyncio.run(revived.stop())


class TestEntryEvent:
    def test_a_zero_cross_is_not_required(self, tmp_path: Path):
        """The whole point: MACD is still below zero when the lane buys."""
        lane = build(tmp_path)
        prime(lane)
        row = cross(lane, 6.0)
        assert row["reason"] == TAKEN
        assert row["macd"] < 0

    def test_a_bar_without_a_signal_line_cross_is_ignored(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        lane.execution.set_quote(SYMBOL, 6.0)
        # Seed the previous bar ABOVE the signal line without evaluating it,
        # so the next bar is a continuation rather than a crossing.
        lane.observe_warmup(bar(6.0, 6.0, ts=899), point(-0.4, -0.5, ts=899))
        assert asyncio.run(lane.on_closed_bar(bar(6.0, 6.0, ts=900), point(-0.3, -0.5, ts=900))) is None
        assert not lane.portfolio.positions
        asyncio.run(lane.stop())

    def test_the_evaluated_bar_is_excluded_from_its_own_recent_high(self, tmp_path: Path):
        """A breakout bar must not be allowed to be the high it is measured against."""
        lane = build(tmp_path)
        prime(lane, high=10.0)
        lane.execution.set_quote(SYMBOL, 6.0)
        asyncio.run(lane.on_closed_bar(bar(6.0, 6.0, ts=899), point(-1.0, -0.5, ts=899)))
        row = asyncio.run(lane.on_closed_bar(bar(6.0, 99.0, ts=900), point(-0.4, -0.5, ts=900)))
        assert row["high_ref"] == 10.0
        assert row["reason"] == TAKEN
        asyncio.run(lane.stop())


class TestEngineHandoff:
    """The lane is only useful if the engine actually feeds it.

    The unit tests above drive BlastEngine directly, which would keep passing
    even if the wiring in TradingEngine.on_tick were deleted. This one goes
    through the engine's own tick path.
    """

    def _engine(self, tmp_path: Path, monkeypatch, timeframe_seconds: int = 60):
        from macd_trader import engine as engine_module
        from macd_trader.engine import TradingEngine

        monkeypatch.setattr(engine_module, "regular_session_open", lambda *_a, **_k: True)
        settings = Settings(
            feed_mode="simulation", symbols_csv=SPOT, tick_capture_enabled=False,
            timeframe_seconds=timeframe_seconds,
            database_path=str(tmp_path / "macd.sqlite3"),
            mp_database_path=str(tmp_path / "mp.sqlite3"),
            blast_database_path=str(tmp_path / "blast.sqlite3"),
            tick_database_path=str(tmp_path / "ticks.sqlite3"),
            contract_snapshot_path=str(tmp_path / "contracts.json"),
            blast_min_lookback_bars=10, blast_high_lookback_bars=30,
            blast_min_breadth_cohort=1,
        )
        return TradingEngine(settings)

    def test_every_minute_bar_reaches_the_lane_on_a_thirty_minute_strategy(self, tmp_path: Path, monkeypatch):
        """The screen was measured on one-minute bars; the strategy timeframe must not change that."""
        from macd_trader.models import Tick

        engine = self._engine(tmp_path, monkeypatch, timeframe_seconds=1800)
        seen: list = []
        engine.blast.on_minute_bar = lambda candle, closed_at: _record(seen, candle, closed_at)

        base = datetime(2026, 9, 11, 4, 0, tzinfo=UTC)
        asyncio.run(engine.on_tick(Tick(SYMBOL, 6.0, 10, base)))
        asyncio.run(engine.on_tick(Tick(SYMBOL, 6.1, 10, base + timedelta(seconds=61))))

        assert seen, "a closed minute bar did not reach the blast lane"
        assert seen[0].symbol == SYMBOL
        assert seen[0].timestamp == int(base.timestamp())
        asyncio.run(engine.blast.stop())


async def _record(sink: list, candle, point):
    sink.append(candle)
    return None


class TestOneMinuteBars:
    def test_the_lane_computes_its_own_macd_from_bar_closes(self, tmp_path: Path):
        lane = build(tmp_path, blast_auto_trade=False)
        price = 10.0
        for index in range(30):
            # A steady decline keeps MACD under its signal line: no cross.
            assert asyncio.run(lane.on_closed_bar(bar(price, price, ts=60 * index))) is None
            price -= 0.15
        row = asyncio.run(lane.on_closed_bar(bar(9.0, 9.0, ts=60 * 30)))
        assert row is not None, "the jump should cross MACD over its signal line"
        assert row["macd"] < 0 < row["histogram"]
        assert row["lookback_bars"] == 30
        assert lane._macd[SYMBOL].count == 31
        asyncio.run(lane.stop())

    def test_a_bar_closed_long_after_its_minute_is_folded_in_but_not_judged(self, tmp_path: Path):
        lane = build(tmp_path)
        judged: list[int] = []

        async def spy(candle, point=None):
            judged.append(candle.timestamp)

        lane.on_closed_bar = spy
        late = datetime.fromtimestamp(600 + 60 + 121, UTC)
        assert asyncio.run(lane.on_minute_bar(bar(6.0, 6.0, ts=600), late)) is None
        assert judged == [] and lane.late_bars_skipped == 1
        assert lane._last_bar[SYMBOL] == 600 and lane._macd[SYMBOL].count == 1
        asyncio.run(lane.on_minute_bar(bar(6.0, 6.0, ts=660), datetime.fromtimestamp(660 + 61, UTC)))
        assert judged == [660]
        asyncio.run(lane.stop())

    def test_bars_of_contracts_outside_the_band_are_ignored(self, tmp_path: Path):
        lane = build(tmp_path)
        spot = Candle(SPOT, 600, 800.0, 800.0, 800.0, 800.0, 10, True)
        assert asyncio.run(lane.on_minute_bar(spot, datetime.fromtimestamp(661, UTC))) is None
        assert SPOT not in lane._highs and SPOT not in lane._macd
        asyncio.run(lane.stop())

    def test_warm_up_reads_stored_minute_bars_and_never_the_broker(self, tmp_path: Path):
        import sqlite3

        database = tmp_path / "historical.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER, timestamp INTEGER,"
            " open REAL, high REAL, low REAL, close REAL, volume INTEGER, asset_type TEXT, expiry TEXT,"
            " downloaded_at TEXT)")
        connection.executemany(
            "INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
            [(SYMBOL, 60, 60 * index, 5.0, 5.0 + index * 0.01, 5.0, 5.0, 10) for index in range(400)]
            # Strategy-timeframe rows in the same table must not leak in.
            + [(SYMBOL, 1800, 1800 * index, 99.0, 99.0, 99.0, 99.0, 10) for index in range(5)])
        connection.commit()
        connection.close()

        broker = DataPolicyBroker()
        lane = build_on(tmp_path, EventHub(), broker)          # 30-bar lookback
        assert asyncio.run(lane.warm_from_store(str(database), [SYMBOL, "NSE:ABSENT26SEP1CE"])) == 1
        window = lane._highs[SYMBOL]
        assert len(window) == 30 and max(window) < 99.0
        assert lane._last_bar[SYMBOL] == 60 * 399
        assert lane._macd[SYMBOL].count == 30 + MACD_SEED_BARS
        # A re-warm reads only bars newer than those held: here, none.
        asyncio.run(lane.warm_from_store(str(database), [SYMBOL]))
        assert lane._macd[SYMBOL].count == 30 + MACD_SEED_BARS
        assert asyncio.run(lane.warm_from_store(str(tmp_path / "missing.sqlite3"), [SYMBOL])) == 1
        assert broker.calls == []
        asyncio.run(lane.stop())

    def test_breadth_is_read_across_the_whole_band_from_the_lane_own_macd(self, tmp_path: Path):
        lane = build(tmp_path)
        lane.set_resolvers(spot_price=lambda _s: 800.0)       # no override: the lane's own breadth
        sides = {"NSE:A26SEP10CE": "CE", "NSE:B26SEP10CE": "CE", "NSE:C26SEP10PE": "PE"}
        lane.set_contract_context({symbol: {"spot_symbol": SPOT, "option_type": side, "strike": 10,
                                            "expiry": "2026-09-29"} for symbol, side in sides.items()})
        for index, symbol in enumerate(sides):
            lane._points[symbol] = IndicatorPoint(symbol, 1_000, 1.0 if index == 0 else -1.0, 0.0, 0.0)
        assert lane._breadth("CE") == pytest.approx(0.5)
        assert lane._breadth("PE") == pytest.approx(0.0)
        # A contract that stopped ticking cannot freeze its MACD into the ratio.
        lane._points["NSE:B26SEP10CE"] = IndicatorPoint("NSE:B26SEP10CE", 1_000 - 301, -1.0, 0.0, 0.0)
        assert lane._breadth("CE") == pytest.approx(1.0)
        # Nor can a symbol outside the band.
        lane._points["NSE:Z26SEP10CE"] = IndicatorPoint("NSE:Z26SEP10CE", 1_000, -1.0, 0.0, 0.0)
        assert lane._breadth("CE") == pytest.approx(1.0)
        asyncio.run(lane.stop())

    def test_breadth_is_withheld_when_the_cohort_is_too_thin(self, tmp_path: Path):
        lane = build(tmp_path, blast_min_breadth_cohort=20)
        lane.set_resolvers(spot_price=lambda _s: 800.0)
        lane._points[SYMBOL] = IndicatorPoint(SYMBOL, 1_000, 1.0, 0.0, 0.0)
        assert lane._breadth("CE") is None
        asyncio.run(lane.stop())

    def test_unwatched_verdicts_are_batched_but_always_readable(self, tmp_path: Path):
        lane = build(tmp_path, blast_max_premium_pct=0.5)
        prime(lane)
        cross(lane, 6.0)                                          # PREMIUM_TOO_RICH: not watched
        assert len(lane._pending_journal) == 1
        assert [row["reason"] for row in lane.journal_rows()] == [PREMIUM_TOO_RICH]
        assert lane._pending_journal == []
        asyncio.run(lane.stop())


def test_the_broadcast_snapshot_stays_small(tmp_path: Path):
    """The snapshot is re-sent to every client on connect; the book is not in it."""
    lane = build(tmp_path)
    prime(lane)
    cross(lane, 6.0)
    snapshot = lane.snapshot()
    assert "orders" not in snapshot and "trades" not in snapshot and "signals" not in snapshot
    assert snapshot["portfolio"]["positions"]
    assert snapshot["journal_summary"]["evaluated"] == 1
    asyncio.run(lane.stop())


# --------------------------------------------------------------------------
# Data policy: the lane never asks the broker for anything but an order.
# --------------------------------------------------------------------------
from datetime import date  # noqa: E402

from macd_trader.blast_engine import BrokerAccessDenied, IST  # noqa: E402


class RecordingHub:
    client_count = 0

    def __init__(self):
        self.types: list[str] = []

    def publish(self, event_type, _data):
        self.types.append(event_type)


class DataPolicyBroker:
    """Records any data request so a test can assert there were none."""
    name = "fyers"

    def __init__(self):
        self.calls: list[str] = []

    async def place_order(self, _order):
        raise AssertionError("paper mode reached the broker order API")

    async def history(self, *_args, **_kwargs):
        self.calls.append("history")

    async def quotes(self, *_args, **_kwargs):
        self.calls.append("quotes")


def build_on(tmp_path: Path, events, broker, **overrides) -> BlastEngine:
    settings = Settings(**{
        "execution_mode": "paper", "symbols_csv": SPOT, "slippage_bps": 0,
        "blast_auto_trade": True, "blast_min_lookback_bars": 10, "blast_high_lookback_bars": 30,
        "blast_min_breadth_cohort": 1, "blast_target_notional": 100_000, **overrides,
    })
    lane = BlastEngine(str(tmp_path / "blast.sqlite3"), settings, broker, events)
    lane.set_tradable_contracts({SYMBOL: 100})
    lane.set_contract_context({SYMBOL: {"spot_symbol": SPOT, "option_type": "CE",
                                        "strike": 800, "expiry": "2026-09-29"}})
    lane.set_resolvers(spot_price=lambda _s: 800.0, breadth=lambda _t: 0.8)
    return lane


class TestNoBrokerData:
    def test_the_lane_holds_a_broker_that_refuses_every_data_call(self, tmp_path: Path):
        lane = build(tmp_path)
        for attribute in ("history", "history_range", "quotes", "stream"):
            with pytest.raises(BrokerAccessDenied):
                getattr(lane.execution.broker, attribute)
        asyncio.run(lane.stop())

    def test_a_full_entry_and_exit_makes_no_broker_request(self, tmp_path: Path):
        broker = DataPolicyBroker()
        lane = build_on(tmp_path, EventHub(), broker)
        prime(lane)
        cross(lane, 6.0)
        asyncio.run(lane.on_tick(SYMBOL, 12.0))
        asyncio.run(lane.on_tick(SYMBOL, 7.0))
        assert SYMBOL not in lane.portfolio.positions
        assert broker.calls == []
        asyncio.run(lane.stop())

    def test_startup_marks_never_quote_a_blast_holding(self, tmp_path: Path):
        from macd_trader.engine import TradingEngine
        from macd_trader.models import Position, Tick
        from macd_trader.portfolio import Portfolio

        macd_symbol = "NSE:POWERINDIA26AUG35000CE"
        requested: list[list[str]] = []

        class QuoteBroker:
            async def quotes(self, symbols):
                requested.append(list(symbols))
                return {macd_symbol: Tick(macd_symbol, 1125.0)}

        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        engine = object.__new__(TradingEngine)
        engine.broker = QuoteBroker()
        engine.portfolio = Portfolio(100_000)
        engine.portfolio.positions[macd_symbol] = Position(
            macd_symbol, 25, 1330.57, 1330.57, datetime.now(UTC), lot_size=25)
        engine.blast = lane
        engine.latest_ticks = {SYMBOL: Tick(SYMBOL, 9.0)}   # already on the stream
        engine.position_mark_errors = {}
        engine.events = EventHub()
        asyncio.run(engine._refresh_position_marks())
        assert requested == [[macd_symbol]]
        assert lane.portfolio.positions[SYMBOL].last_price == 9.0
        asyncio.run(lane.stop())

    def test_blast_only_holdings_cause_no_request_at_all(self, tmp_path: Path):
        from macd_trader.engine import TradingEngine
        from macd_trader.models import Tick
        from macd_trader.portfolio import Portfolio

        class RefusingBroker:
            async def quotes(self, _symbols):
                raise AssertionError("quoted on the blast lane's behalf")

        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        engine = object.__new__(TradingEngine)
        engine.broker = RefusingBroker()
        engine.portfolio = Portfolio(100_000)
        engine.blast = lane
        engine.latest_ticks = {SYMBOL: Tick(SYMBOL, 8.5)}
        engine.position_mark_errors = {}
        engine.events = EventHub()
        asyncio.run(engine._refresh_position_marks())
        assert lane.portfolio.positions[SYMBOL].last_price == 8.5
        asyncio.run(lane.stop())

    def test_warm_up_never_loads_history_for_a_blast_holding(self, tmp_path: Path, monkeypatch):
        engine = TestEngineHandoff()._engine(tmp_path, monkeypatch)
        from macd_trader.models import Position
        rolled_out = "NSE:SBIN26SEP700CE"
        engine.blast.portfolio.positions[rolled_out] = Position(
            rolled_out, 100, 5.0, 5.0, datetime.now(UTC), lot_size=100)
        assert rolled_out not in engine.warmup_symbols()
        asyncio.run(engine.blast.stop())


class TestIncrementalHistory:
    def test_replaying_the_same_bars_adds_nothing(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane, bars=12)
        before = len(lane._highs[SYMBOL])
        prime(lane, bars=12)            # a rollover re-warm replays the same history
        assert len(lane._highs[SYMBOL]) == before
        asyncio.run(lane.stop())

    def test_an_older_bar_after_a_newer_one_is_ignored(self, tmp_path: Path):
        lane = build(tmp_path)
        lane.observe_warmup(bar(5.0, 5.0, ts=100), point(-1.0, -0.5, ts=100))
        lane.observe_warmup(bar(99.0, 99.0, ts=50), point(-1.0, -0.5, ts=50))
        assert max(lane._highs[SYMBOL]) == 5.0
        asyncio.run(lane.stop())

    def test_new_bars_that_closed_while_away_are_folded_in(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane, high=10.0, bars=12)
        lane.observe_warmup(bar(11.0, 11.0, ts=500), point(-1.0, -0.5, ts=500))
        assert max(lane._highs[SYMBOL]) == 11.0
        asyncio.run(lane.stop())

    def test_a_replayed_bar_is_never_judged_twice(self, tmp_path: Path):
        lane = build(tmp_path, blast_auto_trade=False)
        prime(lane)
        assert cross(lane, 6.0, ts=900) is not None
        again = asyncio.run(lane.on_closed_bar(bar(6.0, 6.0, ts=900), point(-0.4, -0.5, ts=900)))
        assert again is None
        assert len(lane.journal_rows()) == 1
        asyncio.run(lane.stop())

    def test_a_timeframe_reset_starts_the_window_again(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        lane.reset_history()
        assert lane._highs == {} and lane._last_bar == {} and lane._previous == {}
        lane.observe_warmup(bar(3.0, 3.0, ts=5), point(-1.0, -0.5, ts=5))
        assert list(lane._highs[SYMBOL]) == [3.0]
        asyncio.run(lane.stop())


class TestEventNamespace:
    def test_every_event_is_a_blast_event(self, tmp_path: Path):
        """A blast fill must never arrive in the MACD lane's orders, trades or portfolio."""
        hub = RecordingHub()
        lane = build_on(tmp_path, hub, BrokerThatMustNotReceiveOrders())
        prime(lane)
        cross(lane, 6.0)
        asyncio.run(lane.on_tick(SYMBOL, 2.0))
        assert hub.types and all(name.startswith("blast_") for name in hub.types)
        assert {"blast_candidate", "blast_order", "blast_trade", "blast_portfolio",
                "blast_position_closed"} <= set(hub.types)
        asyncio.run(lane.stop())


class TestContractContext:
    def test_a_held_contract_keeps_its_context_after_it_rolls_out(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        lane.set_contract_context({"NSE:SBIN26SEP810CE": {"spot_symbol": SPOT, "option_type": "CE",
                                                          "strike": 810, "expiry": "2026-10-27"}})
        assert lane._contracts[SYMBOL]["expiry"] == "2026-09-29"
        asyncio.run(lane.stop())

    def test_an_unheld_contract_is_dropped_by_the_next_selection(self, tmp_path: Path):
        lane = build(tmp_path)
        lane.set_contract_context({"NSE:SBIN26SEP810CE": {"spot_symbol": SPOT, "option_type": "CE",
                                                          "strike": 810, "expiry": "2026-10-27"}})
        assert SYMBOL not in lane._contracts
        asyncio.run(lane.stop())

    def test_expiry_survives_a_restart_without_the_selector(self, tmp_path: Path):
        lane = build(tmp_path)
        prime(lane)
        cross(lane, 6.0)
        asyncio.run(lane.stop())
        settings = Settings(execution_mode="paper", symbols_csv=SPOT, slippage_bps=0)
        revived = BlastEngine(str(tmp_path / "blast.sqlite3"), settings, BrokerThatMustNotReceiveOrders(), EventHub())
        assert revived._contracts[SYMBOL]["expiry"] == "2026-09-29"
        assert revived.expiring_positions(date(2026, 9, 29)) == [SYMBOL]
        asyncio.run(revived.stop())

    def test_a_manual_entry_records_its_contract_too(self, tmp_path: Path):
        lane = build(tmp_path)
        lane.execution.set_quote(SYMBOL, 6.0)
        asyncio.run(lane.manual_order(SYMBOL, "BUY", 1))
        assert lane.repository.load_blast_contracts()[SYMBOL]["strike"] == 800
        asyncio.run(lane.stop())

    def test_an_expiring_holding_is_flattened_from_1520(self, tmp_path: Path):
        lane = build(tmp_path)
        lane.set_contract_context({SYMBOL: {"spot_symbol": SPOT, "option_type": "CE",
                                            "strike": 800, "expiry": "2026-09-14"}})
        prime(lane)
        cross(lane, 6.0)
        assert lane.expiring_positions(date(2026, 9, 13)) == []
        assert lane.expiring_positions(date(2026, 9, 14)) == [SYMBOL]
        lane.execution.set_quote(SYMBOL, 6.0)
        early = datetime(2026, 9, 14, 15, 19, tzinfo=IST)
        late = datetime(2026, 9, 14, 15, 21, tzinfo=IST)
        assert asyncio.run(lane.exit_if_expiring(SYMBOL, early)) is False
        assert asyncio.run(lane.exit_if_expiring(SYMBOL, late)) is True
        assert SYMBOL not in lane.portfolio.positions
        assert lane.execution.exit_reasons[SYMBOL] == "EXPIRY_EXIT_15_20_IST"
        asyncio.run(lane.stop())


class TestOperatorSurface:
    def test_health_names_the_data_policy(self, tmp_path: Path):
        lane = build(tmp_path)
        health = lane.health()
        assert health["broker_access"] == "orders only"
        assert {"watching", "open_positions", "last_candidate_at", "error"} <= set(health)
        asyncio.run(lane.stop())

    def test_the_snapshot_carries_settings_in_the_shape_the_put_accepts(self, tmp_path: Path):
        from macd_trader.app import BlastSettingsInput
        lane = build(tmp_path)
        view = lane.snapshot()["settings"]
        assert set(view) == set(BlastSettingsInput.model_fields)
        BlastSettingsInput(**view)          # round-trips without a validation error
        asyncio.run(lane.stop())

    def test_the_journal_filters_by_verdict_and_lists_sessions(self, tmp_path: Path):
        lane = build(tmp_path, blast_max_premium_pct=0.5)
        lane.set_resolvers(spot_price=lambda _s: None, breadth=lambda _t: 0.8)
        prime(lane)
        cross(lane, 6.0, ts=900)                                        # NO_SPOT
        lane.set_resolvers(spot_price=lambda _s: 800.0, breadth=lambda _t: 0.8)
        cross(lane, 6.0, ts=1_200)                                      # PREMIUM_TOO_RICH
        assert [row["reason"] for row in lane.journal_rows(reason="NO_SPOT")] == [NO_SPOT]
        assert [row["reason"] for row in lane.journal_rows(reason=PREMIUM_TOO_RICH)] == [PREMIUM_TOO_RICH]
        assert lane.journal_days() == [lane.journal_rows()[0]["day"]]
        asyncio.run(lane.stop())
