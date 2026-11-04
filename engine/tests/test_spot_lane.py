"""The spot MACD lane trades each stock's future, long on a MACD cross up and short on a cross down, at exchange lots."""
import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from macd_trader import execution as execution_module
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.execution import ExitRetryWait
from macd_trader.smart_lane import hold_until
from macd_trader.spot_lane import SpotLane, cash_root, macd_cross, pick_series, staged_levels

STOCK, OTHER = "NSE:SBIN-EQ", "NSE:INFY-EQ"
FUT, OTHER_FUT = "NSE:SBIN27JANFUT", "NSE:INFY27JANFUT"
OPTION = "NSE:SBIN26NOV800CE"
LOT = 750
T0 = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)          # Monday 10:00 IST


@dataclass
class Series:
    symbol: str
    root: str
    expiry: str
    lot_size: int


class Broker:
    """Paper only: futures series from a fake master; any order reaching the broker fails the test."""

    name = "fyers"

    def __init__(self, lots=None):
        self.lots = lots or {"SBIN": LOT, "INFY": 400}

    async def futures_series(self, symbols):
        out = {}
        for request in symbols:
            root = request.split(":")[1][:-4]
            if root in self.lots:
                out[request] = [Series(f"NSE:{root}26OCTFUT", root, "2026-10-08", self.lots[root]),   # inside the hold
                                Series(f"NSE:{root}27JANFUT", root, "2027-01-25", self.lots[root])]
        return out

    async def place_order(self, _order):
        raise AssertionError("paper lanes must never reach the broker's order API")


def build(tmp_path: Path, broker=None, **overrides) -> SpotLane:
    settings = Settings(**{"execution_mode": "paper", "slippage_bps": 0, "database_path": str(tmp_path / "macd.sqlite3"),
                           "symbols_csv": f"{STOCK},{OTHER},NSE:NIFTY50-INDEX", **overrides})
    broker = broker or Broker()
    lane = SpotLane(settings, broker, EventHub(), str(tmp_path / "spot.sqlite3"))
    asyncio.run(lane.resolve_futures(broker, now=T0))
    return lane


def cross(lane, side="BUY", stock=STOCK, bucket=1000, intrabar=True):
    previous, macd = (-0.2, 0.1) if side == "BUY" else (0.2, -0.1)
    return lane.on_macd(stock, previous, macd, 800.0, bucket, intrabar=intrabar)


def enter(lane, price=800.0, side="BUY", stock=STOCK, future=FUT, bucket=1000):
    lane.execution.set_quote(future, price)
    signal = cross(lane, side, stock, bucket)
    assert signal is not None
    lane.admit(signal, now=T0)
    asyncio.run(lane.step(T0 + timedelta(seconds=2)))
    lane.flush_journal()
    return lane.portfolio.positions.get(future)


def settle(position, sessions=3):
    """Back-date the entry so the minimum hold has passed but the 5-session limit has not."""
    now = datetime.now(UTC)
    opened = now - timedelta(days=1)
    while hold_until(opened, sessions) > now:
        opened -= timedelta(days=1)
    assert hold_until(opened, 5) > now
    position.opened_at = opened


def tick(lane, price, symbol=FUT):
    asyncio.run(lane.execution.on_tick(symbol, price))


class TestSignals:
    def test_macd_crossing_zero_up_is_long_and_down_is_short(self):
        assert macd_cross(-0.1, 0.2) == "BUY" and macd_cross(0.0, 0.1) == "BUY"
        assert macd_cross(0.1, -0.2) == "SELL" and macd_cross(0.0, -0.1) == "SELL"
        assert macd_cross(0.1, 0.2) is None and macd_cross(-0.1, -0.2) is None and macd_cross(None, 0.2) is None

    def test_a_bar_fires_once_per_side_and_only_on_stocks(self, tmp_path):
        lane = build(tmp_path)
        first = cross(lane, "BUY", bucket=1000)
        assert first.side == "BUY" and first.kind == "SPOT_MACD_UP_INTRABAR"
        assert cross(lane, "BUY", bucket=1000, intrabar=False) is None          # the close of the same bar
        assert cross(lane, "SELL", bucket=1000).kind == "SPOT_MACD_DOWN_INTRABAR"
        assert cross(lane, "BUY", bucket=2800, intrabar=False).kind == "SPOT_MACD_UP"
        assert lane.on_macd(OPTION, -0.2, 0.1, 50.0, 1000, intrabar=True) is None
        assert lane.on_macd("NSE:NIFTY50-INDEX", -0.2, 0.1, 50.0, 1000, intrabar=True) is None

    def test_shorts_can_be_switched_off(self, tmp_path):
        lane = build(tmp_path, spot_short_enabled=False)
        assert cross(lane, "SELL") is None and cross(lane, "BUY") is not None


class TestFutures:
    def test_each_stock_trades_the_nearest_series_that_outlives_the_hold(self, tmp_path):
        lane = build(tmp_path)
        assert {stock: (leg.symbol, leg.lot_size) for stock, leg in lane.futures.items()} == {
            STOCK: (FUT, LOT), OTHER: (OTHER_FUT, 400)}
        assert lane.execution.lot_sizes == {FUT: LOT, OTHER_FUT: 400}
        assert set(lane.feed_symbols()) == {FUT, OTHER_FUT}
        assert cash_root("NSE:M&M-EQ") == ("NSE", "M&M") and cash_root(OPTION) is None

    def test_a_series_expiring_inside_the_hold_is_skipped(self):
        rows = [Series("A", "A", "2026-10-08", 10), Series("B", "B", "2026-10-27", 0), Series("C", "C", "2026-11-24", 10)]
        assert pick_series(rows, datetime(2026, 10, 12).date()).symbol == "C"
        assert pick_series(rows[:2], datetime(2026, 10, 12).date()) is None

    def test_the_option_universe_never_replaces_the_futures(self, tmp_path):
        lane = build(tmp_path)
        lane.set_tradable_contracts({OPTION: 750})
        assert lane.execution.lot_sizes == {FUT: LOT, OTHER_FUT: 400}

    def test_a_stock_without_a_series_is_journalled_not_traded(self, tmp_path):
        lane = build(tmp_path, broker=Broker({"SBIN": LOT}))
        lane.admit(cross(lane, "BUY", OTHER), now=T0)
        asyncio.run(lane.step(T0))
        assert lane.counts == {"NOT_TRADABLE": 1}
        assert "1 of 2 stocks" in lane.futures_error


class TestEntry:
    def test_a_cross_up_buys_one_exchange_lot_of_the_future(self, tmp_path):
        lane = build(tmp_path)
        position = enter(lane)
        assert position.quantity == LOT and position.lot_size == LOT and position.lots == 1
        assert position.hard_stop == pytest.approx(776.0)
        row = lane.journal_rows()[0]
        assert row["decision"] == "TAKEN" and row["lots"] == 1 and row["symbol"] == STOCK and row["option_type"] == "LONG"
        assert row["ticket"] == pytest.approx(600_000) and row["detail"]["future"] == FUT
        assert lane.meta[FUT]["spot_symbol"] == STOCK and lane.meta[FUT]["option_type"] == "LONG"

    def test_a_cross_down_sells_the_future_short(self, tmp_path):
        lane = build(tmp_path)
        position = enter(lane, side="SELL")
        assert position.quantity == -LOT and position.lots == 1
        assert position.hard_stop == pytest.approx(824.0) and position.peak_price == pytest.approx(800.0)
        assert lane.held_symbols() == [FUT] and lane.held_future(STOCK) == FUT
        snap = lane.snapshot()["portfolio"]["positions"][0]
        assert snap["side"] == "SHORT" and snap["underlying"] == STOCK and snap["margin"] == pytest.approx(150_000)

    def test_spot_lots_sets_the_lots_per_signal(self, tmp_path):
        assert enter(build(tmp_path, spot_lane_lots=3)).quantity == 3 * LOT

    def test_a_held_stock_is_not_entered_again_on_either_side(self, tmp_path):
        lane = build(tmp_path)
        enter(lane)
        lane.admit(cross(lane, "SELL", bucket=2800), now=T0)
        asyncio.run(lane.step(T0 + timedelta(seconds=4)))
        assert lane.counts["ALREADY_HELD"] == 1 and lane.portfolio.positions[FUT].quantity == LOT

    def test_entries_need_free_margin(self, tmp_path):
        lane = build(tmp_path, spot_lane_capital=200_000.0)      # one lot blocks Rs 1.5 L of margin
        assert enter(lane) is not None
        assert lane.free_margin() == pytest.approx(50_000)
        assert enter(lane, 1000.0, "SELL", OTHER, OTHER_FUT) is None   # Rs 1 L margin needed
        assert lane.counts["NO_CASH"] == 1

    def test_no_fresh_quote_waits_then_expires(self, tmp_path):
        lane = build(tmp_path)
        lane.admit(cross(lane), now=T0)
        asyncio.run(lane.step(T0))
        assert lane.counts == {"WAITING_PRICE": 1} and len(lane._pending) == 1
        asyncio.run(lane.step(T0 + timedelta(minutes=11)))
        assert lane.counts["NO_PRICE"] == 1 and not lane._pending

    def test_outside_the_session_nothing_is_traded(self, tmp_path):
        lane = build(tmp_path)
        lane.execution.set_quote(FUT, 800.0)
        evening = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)      # 16:30 IST
        lane.admit(cross(lane), now=evening)
        asyncio.run(lane.step(evening))
        assert lane.counts == {"SESSION_CLOSED": 1}


class TestLongExits:
    def test_the_hard_stop_is_three_percent(self, tmp_path):
        lane = build(tmp_path)
        enter(lane)
        tick(lane, 776.5)
        assert FUT in lane.portfolio.positions
        tick(lane, 776.0)
        assert FUT not in lane.portfolio.positions
        trade = lane.trades()[0]
        assert trade["exit_reason"] == "HARD_STOP_3_PCT" and trade["side"] == "LONG" and trade["underlying"] == STOCK

    def test_one_lot_has_no_thirds_and_rides_the_trail(self, tmp_path):
        lane = build(tmp_path)
        settle(enter(lane))
        tick(lane, 840.0)                               # +5%: past two thirds, but one lot cannot be split
        position = lane.portfolio.positions[FUT]
        assert position.quantity == LOT and position.trailing_stop == pytest.approx(819.0)
        tick(lane, 819.0)
        assert lane.trades()[0]["exit_reason"] == "TRAILING_STOP_2.5_PCT"

    def test_thirds_come_out_in_whole_lots(self, tmp_path):
        lane = build(tmp_path, spot_lane_lots=3)
        settle(enter(lane))
        tick(lane, 824.0)
        assert lane.portfolio.positions[FUT].lots == 2
        tick(lane, 840.0)
        assert lane.portfolio.positions[FUT].lots == 1
        tick(lane, 860.0)
        assert FUT not in lane.portfolio.positions
        assert lane.trades()[0]["exit_reason"] == "STAGED_PROFIT_EXIT_3"

    def test_macd_below_zero_closes_only_a_long_that_never_worked(self, tmp_path):
        lane = build(tmp_path)
        settle(enter(lane))
        settle(enter(lane, 1000.0, "BUY", OTHER, OTHER_FUT))
        tick(lane, 1011.0, OTHER_FUT)                   # INFY's best +1.1%: earned the ladder
        assert asyncio.run(lane.execution.on_closed_bar(OTHER, -0.5)) is False
        assert asyncio.run(lane.execution.on_closed_bar(STOCK, 0.5)) is False     # still above zero
        assert asyncio.run(lane.execution.on_closed_bar(FUT, -0.5)) is False      # the future's own MACD is not read
        assert asyncio.run(lane.execution.on_closed_bar(STOCK, -0.5)) is True
        assert FUT not in lane.portfolio.positions and OTHER_FUT in lane.portfolio.positions


class TestShortExits:
    def test_the_hard_stop_is_three_percent_above(self, tmp_path):
        lane = build(tmp_path)
        enter(lane, side="SELL")
        tick(lane, 823.5)
        assert FUT in lane.portfolio.positions
        tick(lane, 824.0)
        assert FUT not in lane.portfolio.positions
        trade = lane.trades()[0]
        assert trade["exit_reason"] == "HARD_STOP_3_PCT" and trade["side"] == "SHORT"
        assert trade["pnl"] == pytest.approx(-24.0 * LOT)
        assert lane.portfolio.cash == pytest.approx(lane.capital - 24.0 * LOT)

    def test_the_trail_follows_the_low_and_covers_two_and_a_half_percent_up(self, tmp_path):
        lane = build(tmp_path)
        settle(enter(lane, side="SELL"))
        tick(lane, 760.0)                               # -5%: trail at 779, under breakeven 800
        position = lane.portfolio.positions[FUT]
        assert position.peak_price == pytest.approx(760.0) and position.trailing_stop == pytest.approx(779.0)
        tick(lane, 770.0)                               # a bounce does not loosen it
        assert position.trailing_stop == pytest.approx(779.0)
        tick(lane, 779.0)
        trade = lane.trades()[0]
        assert trade["exit_reason"] == "TRAILING_STOP_2.5_PCT" and trade["pnl"] == pytest.approx(21.0 * LOT)

    def test_short_thirds_cover_in_whole_lots(self, tmp_path):
        lane = build(tmp_path, spot_lane_lots=3)
        settle(enter(lane, side="SELL"))
        tick(lane, 776.0)
        assert lane.portfolio.positions[FUT].quantity == -2 * LOT
        assert lane.portfolio.positions[FUT].peak_price == pytest.approx(776.0)   # kept through the part cover
        tick(lane, 760.0)
        assert lane.portfolio.positions[FUT].quantity == -LOT
        tick(lane, 740.0)
        assert FUT not in lane.portfolio.positions

    def test_macd_above_zero_covers_only_a_short_that_never_worked(self, tmp_path):
        lane = build(tmp_path)
        settle(enter(lane, side="SELL"))
        assert asyncio.run(lane.execution.on_closed_bar(STOCK, -0.5)) is False
        assert asyncio.run(lane.execution.on_closed_bar(STOCK, 0.5)) is True
        assert lane.trades()[0]["exit_reason"] == "MACD_INVALIDATED"

    def test_inside_the_minimum_hold_only_the_hard_stop_acts(self, tmp_path):
        lane = build(tmp_path)
        position = enter(lane, side="SELL")             # opened now: inside the first 3 sessions
        tick(lane, 740.0)                               # -7.5%: the trail arms but waits
        tick(lane, 790.0)
        assert asyncio.run(lane.execution.on_closed_bar(STOCK, 0.5)) is False
        assert position.quantity == -LOT and position.trailing_stop is not None
        tick(lane, 824.0)
        assert lane.trades()[0]["exit_reason"] == "HARD_STOP_3_PCT"

    def test_the_hold_limit_is_five_sessions(self, tmp_path):
        lane = build(tmp_path)
        position = enter(lane, side="SELL")
        position.opened_at = datetime.now(UTC) - timedelta(days=40)
        tick(lane, 801.0)
        assert lane.trades()[0]["exit_reason"] == "HOLD_LIMIT_5_SESSIONS"

    def test_a_restart_restores_a_short_with_its_stop_and_trail(self, tmp_path):
        lane = build(tmp_path, spot_min_hold_sessions=0)
        enter(lane, side="SELL")
        tick(lane, 770.0)
        lane.repository.save_position_excursions(list(lane.portfolio.positions.values()))
        again = build(tmp_path, spot_min_hold_sessions=0)
        position = again.portfolio.positions[FUT]
        assert position.quantity == -LOT and position.hard_stop == pytest.approx(824.0)
        assert position.peak_price == pytest.approx(770.0) and position.trailing_stop == pytest.approx(789.25)
        assert again.meta[FUT]["option_type"] == "SHORT" and again.execution.lot_sizes[FUT] == LOT


class TestExitBackOff:
    def test_a_rejected_automated_cover_waits_before_retrying(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        enter(lane, side="SELL")
        execution = lane.execution
        execution.set_quote(FUT, 830.0, datetime.now(UTC) - timedelta(minutes=5))     # stale
        execution.arm_exit(FUT, "HARD_STOP_3_PCT")
        with pytest.raises(RuntimeError):
            asyncio.run(execution.submit(FUT, "BUY", LOT))
        orders = len(lane.repository.rows("orders"))
        execution.arm_exit(FUT, "HARD_STOP_3_PCT")
        with pytest.raises(ExitRetryWait):
            asyncio.run(execution.submit(FUT, "BUY", LOT))
        assert len(lane.repository.rows("orders")) == orders          # no order while backing off
        assert execution.exit_failures[FUT]["count"] == 1
        later = time.monotonic() + 16
        monkeypatch.setattr(execution_module.time, "monotonic", lambda: later)
        execution.set_quote(FUT, 830.0)
        execution.arm_exit(FUT, "HARD_STOP_3_PCT")
        order = asyncio.run(execution.submit(FUT, "BUY", LOT))
        assert order.status == "FILLED" and FUT not in execution.exit_failures and FUT not in lane.portfolio.positions

    def test_a_manual_exit_is_never_held_back(self, tmp_path):
        lane = build(tmp_path)
        enter(lane)
        execution = lane.execution
        execution._exit_blocked_until[FUT] = time.monotonic() + 999
        execution.exit_failures[FUT] = {"count": 3}
        execution.set_quote(FUT, 790.0)
        assert asyncio.run(execution.submit(FUT, "SELL", LOT)).status == "FILLED"

    def test_an_exit_cannot_exceed_the_position(self, tmp_path):
        lane = build(tmp_path)
        enter(lane, side="SELL")
        with pytest.raises(ValueError):
            asyncio.run(lane.execution.submit(FUT, "BUY", 2 * LOT))


def test_staged_levels_parse():
    assert staged_levels("0.075, 0.03,x,-1,0.05") == (0.03, 0.05, 0.075)


def test_the_engine_queues_a_current_cross_and_drops_a_stale_one(tmp_path):
    from types import SimpleNamespace

    from macd_trader.engine import TradingEngine
    lane = build(tmp_path)
    stub = SimpleNamespace(spot_lane=lane, _signal_bar_is_current=lambda signal: signal.evaluated_candle_timestamp > 500)
    TradingEngine._spot_cross(stub, STOCK, 0.2, -0.1, 800.0, 1000, intrabar=True)
    TradingEngine._spot_cross(stub, OTHER, -0.2, 0.1, 900.0, 100, intrabar=False)       # stale bar
    assert [(p.symbol, p.option_type) for p in lane._pending] == [(STOCK, "SHORT")]
