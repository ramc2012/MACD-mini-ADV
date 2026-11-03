"""The spot MACD lane buys a Rs 1 L ticket of shares on the MACD lane's stock signals and exits on the scaled ladder."""
import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from macd_trader import execution as execution_module
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.execution import ExitRetryWait
from macd_trader.models import Signal
from macd_trader.spot_lane import SpotLane, staged_levels

SYMBOL = "NSE:SBIN-EQ"
OPTION = "NSE:SBIN26NOV800CE"
T0 = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)          # Monday 10:00 IST


class NoOrders:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper lanes must never reach the broker's order API")


def build(tmp_path: Path, **overrides) -> SpotLane:
    settings = Settings(**{"execution_mode": "paper", "slippage_bps": 0, "database_path": str(tmp_path / "macd.sqlite3"),
                           "symbols_csv": f"{SYMBOL},NSE:INFY-EQ,NSE:NIFTY50-INDEX", **overrides})
    return SpotLane(settings, NoOrders(), EventHub(), str(tmp_path / "spot.sqlite3"))


def signal(symbol=SYMBOL, price=2500.0, side="BUY", bucket=1000) -> Signal:
    return Signal(symbol, side, "PREMIUM_MACD_ZERO_CROSS_UP_INTRABAR", price, 0.1, 0.0, 0.1,
                  evaluated_candle_timestamp=bucket)


def enter(lane, price=2500.0, symbol=SYMBOL):
    lane.execution.set_quote(symbol, price)
    lane.on_spot_signal(signal(symbol, price), now=T0)
    asyncio.run(lane.step(T0 + timedelta(seconds=2)))
    lane.flush_journal()
    return lane.portfolio.positions.get(symbol)


def tick(lane, price, symbol=SYMBOL):
    asyncio.run(lane.execution.on_tick(symbol, price))


class TestEntry:
    def test_a_stock_signal_buys_whole_shares_within_the_ticket(self, tmp_path):
        lane = build(tmp_path)
        position = enter(lane, 2500.0)
        assert position.quantity == 40 and position.lot_size == 1
        row = lane.journal_rows()[0]
        assert row["decision"] == "TAKEN" and row["lots"] == 40 and row["ticket"] == 100_000
        assert row["detail"]["kind"] == "PREMIUM_MACD_ZERO_CROSS_UP_INTRABAR"
        assert lane.meta[SYMBOL]["option_type"] == "EQ"

    def test_only_cash_buys_are_taken(self, tmp_path):
        lane = build(tmp_path)
        lane.on_spot_signal(signal(OPTION), now=T0)
        lane.on_spot_signal(signal("NSE:NIFTY50-INDEX"), now=T0)
        lane.on_spot_signal(signal(side="SELL"), now=T0)
        lane.on_macd_signal(signal(OPTION), "CE", {}, now=T0)
        assert lane._pending == []

    def test_the_option_universe_never_replaces_the_share_universe(self, tmp_path):
        lane = build(tmp_path)
        lane.set_tradable_contracts({OPTION: 750})
        assert lane.execution.lot_sizes == {SYMBOL: 1, "NSE:INFY-EQ": 1}

    def test_a_held_stock_is_not_bought_again(self, tmp_path):
        lane = build(tmp_path)
        enter(lane)
        lane.on_spot_signal(signal(bucket=2000), now=T0)
        asyncio.run(lane.step(T0 + timedelta(seconds=4)))
        assert lane.counts["ALREADY_HELD"] == 1 and lane.portfolio.positions[SYMBOL].quantity == 40

    def test_a_share_dearer_than_the_ticket_is_journalled_not_bought(self, tmp_path):
        lane = build(tmp_path)
        assert enter(lane, 150_000.0) is None
        assert lane.counts == {"LOT_ABOVE_TICKET": 1}

    def test_a_partial_ticket_is_bought_from_what_cash_is_left(self, tmp_path):
        lane = build(tmp_path, spot_lane_capital=150_000.0)
        enter(lane, 2500.0)
        second = enter(lane, 1000.0, "NSE:INFY-EQ")
        assert second.quantity == 50                # Rs 50 k left after the first Rs 1 L ticket
        assert enter(build(tmp_path / "b", spot_lane_capital=500.0), 2500.0) is None

    def test_no_fresh_quote_waits_then_expires(self, tmp_path):
        lane = build(tmp_path)
        lane.on_spot_signal(signal(), now=T0)
        asyncio.run(lane.step(T0))
        assert lane.counts == {"WAITING_PRICE": 1} and len(lane._pending) == 1
        asyncio.run(lane.step(T0 + timedelta(minutes=11)))
        assert lane.counts["NO_PRICE"] == 1 and not lane._pending

    def test_outside_the_cash_session_nothing_is_bought(self, tmp_path):
        lane = build(tmp_path)
        lane.execution.set_quote(SYMBOL, 2500.0)
        evening = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)      # 16:30 IST
        lane.on_spot_signal(signal(), now=evening)
        asyncio.run(lane.step(evening))
        assert lane.counts == {"SESSION_CLOSED": 1}


class TestExits:
    def test_the_hard_stop_is_three_percent(self, tmp_path):
        lane = build(tmp_path)
        enter(lane)
        tick(lane, 2426.0)
        assert SYMBOL in lane.portfolio.positions
        tick(lane, 2425.0)
        assert SYMBOL not in lane.portfolio.positions
        assert lane.trades()[0]["exit_reason"] == "HARD_STOP_3_PCT"

    def test_thirds_come_out_at_three_five_and_seven_and_a_half_percent(self, tmp_path):
        lane = build(tmp_path)
        enter(lane)                                     # 40 shares at 2500
        tick(lane, 2575.0)
        assert lane.portfolio.positions[SYMBOL].quantity == 27
        tick(lane, 2625.0)
        assert lane.portfolio.positions[SYMBOL].quantity == 14
        tick(lane, 2687.5)
        assert SYMBOL not in lane.portfolio.positions
        assert lane.trades()[0]["exit_reason"] == "STAGED_PROFIT_EXIT_3"

    def test_the_trail_arms_at_three_percent_and_gives_back_two_and_a_half(self, tmp_path):
        lane = build(tmp_path, spot_staged_exits_csv="0.5")
        enter(lane)
        tick(lane, 2600.0)                              # +4%: trail at 2535
        position = lane.portfolio.positions[SYMBOL]
        assert position.trailing_stop == pytest.approx(2535.0)
        tick(lane, 2535.0)
        assert SYMBOL not in lane.portfolio.positions
        assert lane.trades()[0]["exit_reason"] == "TRAILING_STOP_2.5_PCT"

    def test_macd_below_zero_closes_only_a_position_that_never_worked(self, tmp_path):
        lane = build(tmp_path)
        enter(lane)
        enter(lane, 1000.0, "NSE:INFY-EQ")
        tick(lane, 1011.0, "NSE:INFY-EQ")               # INFY's best +1.1%: earned the ladder
        assert asyncio.run(lane.execution.on_closed_bar("NSE:INFY-EQ", -0.5)) is False
        assert asyncio.run(lane.execution.on_closed_bar(SYMBOL, -0.5)) is True
        assert SYMBOL not in lane.portfolio.positions and "NSE:INFY-EQ" in lane.portfolio.positions

    def test_the_hold_limit_is_twenty_sessions(self, tmp_path):
        lane = build(tmp_path)
        position = enter(lane)
        position.opened_at = datetime.now(UTC) - timedelta(days=40)
        tick(lane, 2510.0)
        assert SYMBOL not in lane.portfolio.positions
        assert lane.trades()[0]["exit_reason"] == "HOLD_LIMIT_20_SESSIONS"

    def test_a_restart_restores_the_book_and_its_stage(self, tmp_path):
        lane = build(tmp_path)
        enter(lane)
        tick(lane, 2575.0)
        lane.repository.save_position_excursions(list(lane.portfolio.positions.values()))
        again = build(tmp_path)
        position = again.portfolio.positions[SYMBOL]
        assert position.quantity == 27 and position.exit_stage == 1 and position.entry_stage == 40
        assert again.meta[SYMBOL]["source"] == "macd"
        assert again.execution.lot_sizes[SYMBOL] == 1


class TestExitBackOff:
    def test_a_rejected_automated_exit_waits_before_retrying(self, tmp_path, monkeypatch):
        lane = build(tmp_path)
        enter(lane)
        execution = lane.execution
        execution.set_quote(SYMBOL, 2400.0, datetime.now(UTC) - timedelta(minutes=5))     # stale
        execution.arm_exit(SYMBOL, "HARD_STOP_3_PCT")
        with pytest.raises(RuntimeError):
            asyncio.run(execution.submit(SYMBOL, "SELL", 40))
        orders = len(lane.repository.rows("orders"))
        execution.arm_exit(SYMBOL, "HARD_STOP_3_PCT")
        with pytest.raises(ExitRetryWait):
            asyncio.run(execution.submit(SYMBOL, "SELL", 40))
        assert len(lane.repository.rows("orders")) == orders          # no order while backing off
        assert execution.exit_failures[SYMBOL]["count"] == 1
        assert execution.exit_failures[SYMBOL]["retry_after_seconds"] == execution_module.EXIT_RETRY_SECONDS[0]
        later = time.monotonic() + 16
        monkeypatch.setattr(execution_module.time, "monotonic", lambda: later)
        execution.set_quote(SYMBOL, 2400.0)
        execution.arm_exit(SYMBOL, "HARD_STOP_3_PCT")
        order = asyncio.run(execution.submit(SYMBOL, "SELL", 40))
        assert order.status == "FILLED" and SYMBOL not in execution.exit_failures

    def test_a_manual_sell_is_never_held_back(self, tmp_path):
        lane = build(tmp_path)
        enter(lane)
        execution = lane.execution
        execution._exit_blocked_until[SYMBOL] = time.monotonic() + 999
        execution.exit_failures[SYMBOL] = {"count": 3}
        execution.set_quote(SYMBOL, 2400.0)
        order = asyncio.run(execution.submit(SYMBOL, "SELL", 40))     # not armed: a manual ticket
        assert order.status == "FILLED"


def test_staged_levels_parse():
    assert staged_levels("0.075, 0.03,x,-1,0.05") == (0.03, 0.05, 0.075)
