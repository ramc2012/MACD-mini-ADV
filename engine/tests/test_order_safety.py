import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.portfolio import Portfolio
from macd_trader.repository import TradeRepository


class NoBrokerOrders:
    async def place_order(self, _order):
        raise AssertionError("paper order reached broker")


@pytest.fixture
def manager(tmp_path):
    repository = TradeRepository(str(tmp_path / "paper.sqlite3"))
    manager = ExecutionManager(Settings(symbols_csv="TEST", slippage_bps=5), NoBrokerOrders(), Portfolio(150), repository, EventHub())
    manager.set_quote("TEST", 100)
    yield manager
    repository.close()


def test_insufficient_cash_and_unheld_or_oversized_sells_are_rejected(manager):
    with pytest.raises(ValueError, match="cash"):
        asyncio.run(manager.submit("TEST", "BUY", lots=2))
    with pytest.raises(ValueError, match="holdings"):
        asyncio.run(manager.submit("TEST", "SELL", lots=1))
    asyncio.run(manager.submit("TEST", "BUY", lots=1))
    with pytest.raises(ValueError, match="holdings"):
        asyncio.run(manager.submit("TEST", "SELL", lots=2))
    assert manager.portfolio.cash == pytest.approx(49.95)
    assert len(manager.repository.rows("trades")) == 1


@pytest.mark.parametrize("age", [None, 11, -30])
def test_missing_stale_or_future_quotes_cannot_fill(manager, age):
    if age is None:
        manager.last_price_at.clear()
    else:
        manager.last_price_at["TEST"] = datetime.now(UTC) - timedelta(seconds=age)
    with pytest.raises(RuntimeError, match="stale"):
        asyncio.run(manager.submit("TEST", "BUY", lots=1))
    assert not manager.repository.rows("trades")


def test_limit_prices_bound_both_sides_after_slippage(manager):
    async def scenario():
        buy = await manager.submit("TEST", "BUY", lots=1, order_type="LIMIT", limit_price=100)
        sell = await manager.submit("TEST", "SELL", lots=1, order_type="LIMIT", limit_price=100)
        assert buy.fill_price == 100
        assert sell.fill_price == 100
    asyncio.run(scenario())


def test_delayed_fill_persists_and_publishes_final_order(manager):
    async def scenario():
        queue = await manager.events.subscribe()
        order = await manager.submit("TEST", "BUY", lots=1, order_type="LIMIT", limit_price=99)
        assert order.status == "OPEN"
        await manager.on_tick("TEST", 98)
        assert order.status == "FILLED"
        assert manager.repository.rows("orders")[0]["status"] == "FILLED"
        frames = []
        while not queue.empty():
            frames.append(queue.get_nowait())
        assert any(f["type"] == "order" and f["data"]["status"] == "FILLED" for f in frames)
        await manager.on_tick("TEST", 98)
        assert len(manager.repository.rows("trades")) == 1
    asyncio.run(scenario())


def test_pending_buys_reserve_cash_and_concurrent_buys_do_not_overspend(manager):
    async def scenario():
        await manager.submit("TEST", "BUY", lots=1, order_type="LIMIT", limit_price=99)
        with pytest.raises(ValueError, match="cash"):
            await manager.submit("TEST", "BUY", lots=1)
        await manager.on_tick("TEST", 98)
        outcomes = await asyncio.gather(manager.submit("TEST", "BUY", lots=1), manager.submit("TEST", "BUY", lots=1), return_exceptions=True)
        assert all(isinstance(result, ValueError) for result in outcomes)
        assert manager.portfolio.cash >= 0
    asyncio.run(scenario())


def test_limit_requires_a_price(manager):
    with pytest.raises(ValueError, match="limit_price"):
        asyncio.run(manager.submit("TEST", "BUY", lots=1, order_type="LIMIT"))


def test_protective_exit_cancels_resting_sell_instead_of_being_blocked(manager):
    async def scenario():
        await manager.submit("TEST", "BUY", lots=1)
        resting = await manager.submit("TEST", "SELL", lots=1, order_type="LIMIT", limit_price=120)
        await manager.on_tick("TEST", 69)
        assert resting.status == "CANCELLED"
        assert not manager.portfolio.positions
        assert manager.closed_positions[0]["exit_reason"] == "HARD_STOP_30_PCT"
    asyncio.run(scenario())


def test_portfolio_limits_restrict_new_buys_but_preserve_holdings_and_exits(manager):
    async def scenario():
        manager.portfolio.cash = 1000
        manager.settings.max_positions = 1
        manager.set_tradable_symbols(["TEST", "OTHER"])
        manager.set_quote("OTHER", 100)
        await manager.submit("TEST", "BUY", lots=1)
        with pytest.raises(ValueError, match="positions"):
            await manager.submit("OTHER", "BUY", lots=1)
        manager.settings.min_cash_reserve = 850
        with pytest.raises(ValueError, match="reserve"):
            await manager.submit("TEST", "BUY", lots=1)
        assert manager.portfolio.positions["TEST"].quantity == 1
        await manager.submit("TEST", "SELL", lots=1)
        assert not manager.portfolio.positions
    asyncio.run(scenario())
