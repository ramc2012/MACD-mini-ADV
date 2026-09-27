"""Paper execution invariants across delayed ticks, restarts, and write errors."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from macd_trader import engine as engine_module
from macd_trader.config import Settings
from macd_trader.engine import TradingEngine
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.models import Order, Tick, Trade
from macd_trader.portfolio import Portfolio
from macd_trader.repository import TradeRepository


class NoBrokerOrders:
    async def place_order(self, _order):
        raise AssertionError("paper execution reached the broker")


def _manager(settings, repository, portfolio=None):
    return ExecutionManager(settings, NoBrokerOrders(), portfolio or Portfolio(1_000_000), repository, EventHub())


def test_previous_session_and_reordered_ticks_cannot_fill_or_stop(tmp_path, monkeypatch):
    symbol = "NSE:SBIN26SEP800CE"
    settings = Settings(
        symbols_csv="NSE:SBIN-EQ", feed_mode="simulation", execution_mode="paper",
        slippage_bps=0, database_path=str(tmp_path / "engine.sqlite3"),
        research_database_path=str(tmp_path / "research.sqlite3"),
        tick_capture_enabled=False, mp_enabled=False, blast_enabled=False,
    )
    engine = TradingEngine(settings)
    engine.execution.set_tradable_contracts({symbol: 1})
    engine.execution.set_quote(symbol, 100)
    asyncio.run(engine.execution.submit(symbol, "BUY", lots=1))
    resting = asyncio.run(engine.execution.submit(symbol, "BUY", lots=1, order_type="LIMIT", limit_price=95))
    # Force the wall-clock session gate open; source timestamps remain real.
    monkeypatch.setattr(engine_module, "regular_session_open", lambda *_: True)
    current = datetime.now(UTC)

    asyncio.run(engine.on_tick(Tick(symbol, 90, timestamp=current - timedelta(days=1))))
    assert resting.status == "OPEN"
    assert engine.portfolio.positions[symbol].quantity == 1
    assert engine.portfolio.positions[symbol].last_price == 90  # valuation only
    assert engine.execution.last_prices[symbol] == 100  # executable quote unchanged

    asyncio.run(engine.on_tick(Tick(symbol, 100, timestamp=current)))
    assert engine.portfolio.positions[symbol].last_price == 100
    asyncio.run(engine.on_tick(Tick(symbol, 69, timestamp=current - timedelta(seconds=1))))
    assert engine.portfolio.positions[symbol].quantity == 1
    assert engine.portfolio.positions[symbol].last_price == 100
    assert resting.status == "OPEN"
    assert len(engine.repository.rows("trades")) == 1
    engine.repository.close()


def test_staged_position_restores_anchor_and_counters(tmp_path):
    symbol = "NSE:SBIN26SEP800CE"
    settings = Settings(symbols_csv="NSE:SBIN-EQ", execution_mode="paper", slippage_bps=0,
                        auto_trade=True, max_trade_lots=4)
    path = str(tmp_path / "book.sqlite3")
    repository = TradeRepository(path)
    manager = _manager(settings, repository)
    manager.set_tradable_contracts({symbol: 1})
    manager.set_quote(symbol, 100)
    asyncio.run(manager.submit(symbol, "BUY", lots=1))
    for price in (107.5, 115, 122.5, 145):
        asyncio.run(manager.on_tick(symbol, price))
    live = manager.portfolio.positions[symbol]
    assert (live.lots, live.entry_anchor, live.entry_stage, live.exit_stage) == (3, 100, 4, 1)
    repository.close()

    reopened = TradeRepository(path)
    portfolio = Portfolio(1_000_000)
    engine = object.__new__(TradingEngine)
    engine.settings, engine.repository, engine.portfolio = settings, reopened, portfolio
    engine._restore_portfolio()
    restored = portfolio.positions[symbol]
    assert (restored.lots, restored.entry_anchor, restored.entry_stage, restored.exit_stage) == (3, 100, 4, 1)
    restarted = _manager(settings, reopened, portfolio)
    restarted.set_tradable_contracts({symbol: 1})
    restarted.set_quote(symbol, 135)
    asyncio.run(restarted.on_tick(symbol, 135))
    assert portfolio.positions[symbol].lots == 3  # no repeated add or first scale-out
    assert len(reopened.rows("trades")) == 5
    reopened.close()


def test_open_limit_orders_resume_and_invalid_contracts_cancel(tmp_path):
    symbol, obsolete = "NSE:SBIN26SEP800CE", "NSE:SBIN26AUG800CE"
    settings = Settings(symbols_csv="NSE:SBIN-EQ", execution_mode="paper", slippage_bps=0)
    path = str(tmp_path / "book.sqlite3")
    repository = TradeRepository(path)
    manager = _manager(settings, repository)
    manager.set_tradable_contracts({symbol: 1, obsolete: 1})
    manager.set_quote(symbol, 100)
    manager.set_quote(obsolete, 100)
    valid = asyncio.run(manager.submit(symbol, "BUY", lots=1, order_type="LIMIT", limit_price=95))
    old = asyncio.run(manager.submit(obsolete, "BUY", lots=1, order_type="LIMIT", limit_price=95))
    assert valid.status == old.status == "OPEN"
    repository.close()

    reopened = TradeRepository(path)
    restarted = _manager(settings, reopened)
    assert set(restarted.orders) == {valid.order_id, old.order_id}
    restarted.set_tradable_contracts({symbol: 1})
    assert restarted.orders[old.order_id].status == "CANCELLED"
    assert next(row for row in reopened.rows("orders") if row["order_id"] == old.order_id)["status"] == "CANCELLED"
    restarted.set_quote(symbol, 100)
    asyncio.run(restarted.on_tick(symbol, 90))
    assert restarted.orders[valid.order_id].status == "FILLED"
    assert len(reopened.rows("trades")) == 1
    reopened.close()


def test_old_open_order_with_a_trade_is_reconciled_without_refilling(tmp_path):
    path = str(tmp_path / "book.sqlite3")
    repository = TradeRepository(path)
    symbol = "NSE:SBIN26SEP800CE"
    order = Order(symbol, "BUY", 1, order_type="LIMIT", limit_price=90, status="OPEN")
    repository.save_order(order)
    repository.save_trade(Trade(order.order_id, symbol, "BUY", 1, 90))
    repository.close()

    reopened = TradeRepository(path)
    manager = _manager(Settings(symbols_csv="NSE:SBIN-EQ", execution_mode="paper"), reopened)
    assert not manager.orders
    assert reopened.rows("orders")[0]["status"] == "FILLED"
    assert reopened.rows("orders")[0]["fill_price"] == 90
    reopened.close()


def test_fill_write_failure_rolls_back_database_and_memory(tmp_path):
    symbol = "NSE:SBIN26SEP800CE"
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = _manager(Settings(symbols_csv="NSE:SBIN-EQ", execution_mode="paper", slippage_bps=0), repository)
    manager.set_tradable_contracts({symbol: 1})
    manager.set_quote(symbol, 100)
    repository._connection.execute(
        "CREATE TRIGGER reject_checkpoint BEFORE INSERT ON position_excursions "
        "BEGIN SELECT RAISE(ABORT, 'checkpoint failed'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="checkpoint failed"):
        asyncio.run(manager.submit(symbol, "BUY", lots=1))
    assert manager.portfolio.cash == 1_000_000
    assert manager.portfolio.positions == {}
    assert repository.rows("trades") == []
    assert repository.rows("orders")[0]["status"] == "REJECTED"
    repository.close()


def test_resting_orders_from_an_earlier_session_expire(tmp_path):
    symbol = "NSE:SBIN26SEP800CE"
    settings = Settings(symbols_csv="NSE:SBIN-EQ", execution_mode="paper", slippage_bps=0)
    path = str(tmp_path / "book.sqlite3")
    repository = TradeRepository(path)
    yesterday = Order(symbol, "BUY", 1, order_type="LIMIT", limit_price=95, status="OPEN",
                      created_at=datetime.now(UTC) - timedelta(days=1))
    today = Order(symbol, "BUY", 1, order_type="LIMIT", limit_price=95, status="OPEN")
    repository.save_order(yesterday)
    repository.save_order(today)
    repository.close()

    reopened = TradeRepository(path)
    restarted = _manager(settings, reopened)
    restarted.set_tradable_contracts({symbol: 1})
    assert restarted.orders[yesterday.order_id].status == "CANCELLED"
    assert restarted.orders[today.order_id].status == "OPEN"
    restarted.set_quote(symbol, 100)
    asyncio.run(restarted.on_tick(symbol, 90))
    assert restarted.orders[today.order_id].status == "FILLED"
    assert len(reopened.rows("trades")) == 1
    reopened.close()


def test_resting_order_left_open_past_the_close_expires_on_the_next_tick(tmp_path):
    symbol = "NSE:SBIN26SEP800CE"
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = _manager(Settings(symbols_csv="NSE:SBIN-EQ", execution_mode="paper", slippage_bps=0), repository)
    manager.set_tradable_contracts({symbol: 1})
    manager.set_quote(symbol, 100)
    resting = asyncio.run(manager.submit(symbol, "BUY", lots=1, order_type="LIMIT", limit_price=95))
    resting.created_at -= timedelta(days=1)  # the process ran overnight
    asyncio.run(manager.on_tick(symbol, 90))
    assert resting.status == "CANCELLED"
    assert repository.rows("trades") == []
    repository.close()


def test_future_stamped_ticks_are_ignored_visibly(tmp_path, monkeypatch):
    symbol = "NSE:SBIN26SEP800CE"
    settings = Settings(
        symbols_csv="NSE:SBIN-EQ", feed_mode="simulation", execution_mode="paper",
        slippage_bps=0, database_path=str(tmp_path / "engine.sqlite3"),
        research_database_path=str(tmp_path / "research.sqlite3"),
        tick_capture_enabled=False, mp_enabled=False, blast_enabled=False,
    )
    engine = TradingEngine(settings)
    engine.status = "connected"
    engine.execution.set_tradable_contracts({symbol: 1})
    monkeypatch.setattr(engine_module, "regular_session_open", lambda *_: True)
    current = datetime.now(UTC)

    # Ordinary NTP error is tolerated.
    asyncio.run(engine.on_tick(Tick(symbol, 100, timestamp=current + timedelta(seconds=10))))
    assert engine.latest_ticks[symbol].ltp == 100
    assert engine.broker_status()["clock_skew_seconds"] is None

    # A host clock minutes behind is refused, and says so.
    asyncio.run(engine.on_tick(Tick(symbol, 90, timestamp=current + timedelta(minutes=3))))
    assert engine.latest_ticks[symbol].ltp == 100
    status = engine.broker_status()
    assert status["future_ticks_ignored"] == 1
    assert status["clock_skew_seconds"] > 170
    assert status["status"] == "stale"
    assert "Sync the system clock" in status["error"]
    engine.repository.close()
