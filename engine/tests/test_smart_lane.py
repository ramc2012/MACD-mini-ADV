"""The smart lanes trade the research's best rules, journal every decision, and keep their own books."""
import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from macd_trader import smart_lane
from macd_trader.blast_engine import PREMIUM_TOO_RICH
from macd_trader.config import Settings
from macd_trader.dispersion import DispersionPoint
from macd_trader.events import EventHub
from macd_trader.models import Signal
from macd_trader.smart_lane import (
    BLAST_SOURCE, MACD_SOURCE, SmartLane, SmartLanes, dispersion_size, lane_label, score,
)

SYMBOL = "NSE:SBIN26OCT800CE"
T0 = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)          # 10:00 IST


class NoOrders:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper lanes must never reach the broker's order API")


def build(tmp_path: Path, capital=100_000, **overrides) -> SmartLane:
    settings = Settings(**{"execution_mode": "paper", "slippage_bps": 0, "database_path": str(tmp_path / "macd.sqlite3"),
                           **overrides})
    lane = SmartLane("smart_test", capital, settings, NoOrders(), EventHub(), str(tmp_path / "lane.sqlite3"))
    lane.set_tradable_contracts({SYMBOL: 100})
    lane.set_contract_context({SYMBOL: {"spot_symbol": "NSE:SBIN-EQ", "option_type": "CE", "strike": 800,
                                        "expiry": "2099-12-30", "volume": 0, "oi": 0}})
    lane.set_resolvers(macd_confirm=lambda *_a: True, dispersion=lambda: None)
    return lane


def signal(price=10.0, bucket=1000) -> Signal:
    return Signal(SYMBOL, "BUY", "TEST", price, 0.1, 0.0, 0.1, evaluated_candle_timestamp=bucket)


def due(lane, at):
    return asyncio.run(lane.step(at))


def enter(lane, monkeypatch, price=10.0, s=0.9):
    monkeypatch.setattr(smart_lane, "score", lambda features: features["s"])
    lane.on_macd_signal(signal(price), "CE", {"s": s}, now=T0)
    lane.execution.set_quote(SYMBOL, price)
    due(lane, T0 + timedelta(seconds=330))
    lane.flush_journal()
    return lane.portfolio.positions.get(SYMBOL)


def tick(lane, price):
    asyncio.run(lane.execution.on_tick(SYMBOL, price))


class TestEntry:
    def test_known_expired_contract_is_rejected_without_an_order(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        lane.set_contract_context({SYMBOL: {"option_type": "CE", "expiry": "2000-01-01"}})
        assert enter(lane, monkeypatch) is None
        assert lane.counts == {"ENTRY_REJECTED": 1}
        assert "Expired contract" in lane.journal_rows()[0]["detail"]["error"]
        assert lane.repository.rows("orders") == []
        assert not lane.portfolio.positions

    def test_it_waits_out_the_delay_then_requires_the_cross_to_still_stand(self, tmp_path, monkeypatch):
        monkeypatch.setattr(smart_lane, "score", lambda features: 0.9)
        lane = build(tmp_path)
        lane.execution.set_quote(SYMBOL, 10.0)
        lane.on_macd_signal(signal(), "CE", {"x": 1}, now=T0)
        assert due(lane, T0 + timedelta(seconds=120)) == 0          # inside the 5-minute delay
        lane.set_resolvers(macd_confirm=lambda *_a: False)
        assert due(lane, T0 + timedelta(seconds=330)) == 1
        assert lane.counts == {"MACD_FADED": 1} and not lane.portfolio.positions

    def test_the_weakest_scores_are_skipped_against_a_rolling_cut(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        lane._scores = [i / 100 for i in range(100)]                  # rolling cut = 0.30 quantile
        assert lane.cutoff() == pytest.approx(0.297, abs=1e-3)
        monkeypatch.setattr(smart_lane, "score", lambda features: features["s"])
        lane.execution.set_quote(SYMBOL, 10.0)
        lane.on_macd_signal(signal(), "CE", {"s": 0.10}, now=T0)
        due(lane, T0 + timedelta(seconds=330))
        assert lane.counts == {"SKIPPED_LOW_SCORE": 1}

    def test_a_tenth_of_equity_in_whole_lots_sized_down_on_the_weak_side(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        position = enter(lane, monkeypatch)                            # Rs 10k ticket / Rs 1,000 a lot
        assert position.lots == 10 and lane.meta[SYMBOL]["source"] == MACD_SOURCE
        other = build(tmp_path / "b")
        other.set_resolvers(dispersion=lambda: DispersionPoint(0, 20, 80, 100, 100, 200))  # CE 20% vs PE 80%
        assert enter(other, monkeypatch).lots == 2                     # x0.25

    def test_one_lot_above_the_ticket_is_not_forced(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        assert enter(lane, monkeypatch, price=200.0) is None           # Rs 20,000 a lot vs a Rs 10,000 ticket
        assert lane.counts == {"LOT_ABOVE_TICKET": 1}
        row = lane.journal_rows()[0]
        assert row["decision"] == "LOT_ABOVE_TICKET" and row["detail"]["one_lot"] == 20_000

    def test_the_liquidity_cap_bounds_the_fifty_lakh_book(self, tmp_path, monkeypatch):
        lane = build(tmp_path, capital=5_000_000)
        lane.set_contract_context({SYMBOL: {"option_type": "CE", "expiry": "2099-12-30", "volume": 30_000, "oi": 0}})
        assert enter(lane, monkeypatch).lots == 6                      # 2% of 30,000 units / 100 a lot


class TestExits:
    def test_forty_percent_stop(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        enter(lane, monkeypatch)
        tick(lane, 6.5)
        assert SYMBOL in lane.portfolio.positions
        tick(lane, 5.9)
        assert SYMBOL not in lane.portfolio.positions
        assert lane.execution.exit_reasons[SYMBOL] == "HARD_STOP_40_PCT"

    def test_all_out_at_double(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        enter(lane, monkeypatch)
        tick(lane, 20.5)
        # the staged exits sell a lot each on the way, the target takes the rest
        assert SYMBOL not in lane.portfolio.positions
        assert lane.execution.exit_reasons[SYMBOL] == "TARGET_100_PCT"

    def test_thirty_percent_trail_arms_at_plus_forty(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        enter(lane, monkeypatch)
        tick(lane, 14.5)                                               # +45%: armed at 14.5 * 0.7 = 10.15
        position = lane.portfolio.positions[SYMBOL]
        assert position.trailing_stop == pytest.approx(10.15)
        tick(lane, 10.1)
        assert SYMBOL not in lane.portfolio.positions
        assert lane.execution.exit_reasons[SYMBOL] == "TRAILING_STOP_30_PCT"

    def test_hold_limit_and_expiry(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        enter(lane, monkeypatch)
        lane.portfolio.positions[SYMBOL].opened_at = datetime.now(UTC) - timedelta(days=12)
        tick(lane, 10.2)
        assert lane.execution.exit_reasons[SYMBOL] == "HOLD_LIMIT_4_SESSIONS"
        other = build(tmp_path / "b")
        enter(other, monkeypatch)
        other.meta[SYMBOL]["expiry"] = (date.today() - timedelta(days=1)).isoformat()
        tick(other, 10.2)
        assert other.execution.exit_reasons[SYMBOL] == "EXPIRY_DAY_FLATTEN"

    def test_macd_invalidation_cuts_a_macd_entry_that_never_worked(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        enter(lane, monkeypatch)
        assert asyncio.run(lane.execution.on_closed_bar(SYMBOL, -0.1)) is True


class TestBlastSource:
    def test_blast_candidates_keep_the_blast_lanes_exits(self, tmp_path):
        lane = build(tmp_path)
        lane.execution.set_quote(SYMBOL, 10.0)
        lane.on_blast_candidate({"symbol": SYMBOL, "premium": 10.0, "option_type": "CE", "macd": -0.4,
                                 "signal": -0.5, "histogram": 0.1, "bar_timestamp": 900, "premium_pct": 0.8,
                                 "breadth": 0.7, "off_high_pct": -40}, now=T0)
        due(lane, T0 + timedelta(seconds=20))
        assert lane.meta[SYMBOL]["source"] == BLAST_SOURCE
        # MACD below zero is the blast entry itself, not an invalidation
        assert asyncio.run(lane.execution.on_closed_bar(SYMBOL, -0.4)) is False
        tick(lane, 5.5)                                                # -45%: inside the 50% stop
        assert SYMBOL in lane.portfolio.positions
        tick(lane, 4.9)
        assert lane.execution.exit_reasons[SYMBOL] == "HARD_STOP_50_PCT"

    def test_the_blast_screen_offers_every_pass_even_with_its_own_trading_off(self, tmp_path):
        from test_blast_lane import build as build_blast, cross, prime
        blast = build_blast(tmp_path, blast_auto_trade=False)
        offered = []
        blast.candidate_listeners.append(offered.append)
        prime(blast)
        cross(blast, 6.0)
        assert len(offered) == 1 and offered[0]["journal_id"]
        rich = build_blast(tmp_path / "rich", blast_auto_trade=False)
        rich.candidate_listeners.append(offered.append)
        prime(rich)
        assert cross(rich, 9.9 * 2)["reason"] in (PREMIUM_TOO_RICH, "NOT_OFF_HIGH")
        assert len(offered) == 1
        asyncio.run(blast.stop()); asyncio.run(rich.stop())


class TestBooks:
    def test_durable_expiry_blocks_new_buys_after_restart_and_preserves_exit(self, tmp_path, monkeypatch):
        from macd_trader import execution

        class EntryClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return T0.astimezone(tz) if tz else T0.replace(tzinfo=None)

        monkeypatch.setattr(execution, "datetime", EntryClock)

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                moment = T0 + timedelta(days=1)
                return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

        lane = build(tmp_path)
        lane.set_contract_context({SYMBOL: {"option_type": "CE", "expiry": "2026-10-05"}})
        position = enter(lane, monkeypatch)
        held_quantity = position.quantity
        lane.repository.close()
        lane._db.close()
        monkeypatch.setattr(execution, "datetime", ExpiredClock)
        restarted = SmartLane("smart_test", lane.capital, lane.settings, NoOrders(), EventHub(), lane.database_path)
        try:
            restarted.set_tradable_contracts({})
            restarted.execution.set_quote(SYMBOL, 10)
            with pytest.raises(ValueError, match="Expired contract"):
                asyncio.run(restarted.execution.submit(SYMBOL, "BUY", lots=1))
            assert restarted.portfolio.positions[SYMBOL].quantity == held_quantity
            assert restarted.meta[SYMBOL]["expiry"] == "2026-10-05"
            assert len(restarted.repository.rows("orders")) == 1
            order = asyncio.run(restarted.execution.submit(SYMBOL, "SELL", held_quantity))
            assert order.status == "FILLED"
            assert not restarted.portfolio.positions
        finally:
            restarted.repository.close()
            restarted._db.close()

    def test_round_trip_is_journalled_tagged_and_survives_a_restart(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        enter(lane, monkeypatch)
        restarted = build(tmp_path)
        assert restarted.portfolio.positions[SYMBOL].lots == 10
        assert restarted.meta[SYMBOL]["source"] == MACD_SOURCE
        tick(restarted, 5.0)
        trades = restarted.trades()
        assert len(trades) == 1 and trades[0]["source"] == MACD_SOURCE and trades[0]["pnl"] == pytest.approx(-5_000)
        summary = restarted.summary()
        assert summary["all"]["trades"] == 1 and summary["decisions"]["TAKEN"] == 1
        assert summary["by_source"][MACD_SOURCE]["net"] == pytest.approx(-5_000)

    def test_two_lanes_one_per_capital(self, tmp_path):
        settings = Settings(execution_mode="paper", database_path=str(tmp_path / "macd.sqlite3"))
        lanes = SmartLanes(settings, NoOrders(), EventHub())
        assert [lane.name for lane in lanes.lanes] == ["smart_1l", "smart_50l"]
        assert [lane.capital for lane in lanes.lanes] == [100_000, 5_000_000]
        assert lane_label(25_000) == "25k" and lane_label(1e7) == "1cr"


def test_dispersion_sizing_and_the_ranker():
    point = DispersionPoint(0, 10, 90, 100, 100, 200)
    assert dispersion_size("PE", point, 0.25) == 1.0
    assert dispersion_size("CE", point, 0.25) == 0.25
    assert dispersion_size("CE", DispersionPoint(0, 40, 50, 100, 100, 200), 0.25) == pytest.approx(0.8)
    assert dispersion_size("CE", DispersionPoint(0, 1, 9, 10, 10, 20), 0.25) == 1.0   # cohort too thin to judge
    assert 0.0 < score({}) < 1.0
