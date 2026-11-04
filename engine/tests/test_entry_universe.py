"""Owner, 4 Nov 2026: signal entries trade only the day's ATM legs, and only one position per stock."""
import asyncio
from pathlib import Path

import pytest

from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.execution import EntryBlocked, ExecutionManager, contract_root
from macd_trader.models import Signal
from macd_trader.portfolio import Portfolio
from macd_trader.repository import TradeRepository

ATM_17000 = "NSE:FORCEMOT26NOV17000CE"
ATM_18000 = "NSE:FORCEMOT26NOV18000CE"
PUT = "NSE:FORCEMOT26NOV17000PE"
OTHER = "NSE:HAL26NOV4900CE"


class NoBroker:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper mode called the broker")


def _manager(tmp_path: Path, universe: set[str] | None) -> ExecutionManager:
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0, auto_trade=True)
    manager = ExecutionManager(settings, NoBroker(), Portfolio(10_000_000), TradeRepository(str(tmp_path / "b.sqlite3")),
                               EventHub())
    manager.set_tradable_contracts({ATM_17000: 25, ATM_18000: 25, PUT: 25, OTHER: 150})
    manager.set_entry_universe(universe)
    for symbol in (ATM_17000, ATM_18000, PUT, OTHER):
        manager.set_quote(symbol, 100.0)
    return manager


def _signal(symbol: str) -> Signal:
    return Signal(symbol=symbol, side="BUY", kind="PREMIUM_MACD_ZERO_CROSS_UP", price=100.0, macd=1.0, signal=0.5,
                  histogram=0.5)


def test_contract_root_is_the_stock_or_index():
    assert contract_root(ATM_17000) == contract_root(PUT) == "FORCEMOT"
    assert contract_root("NSE:NIFTY2611024000CE") == "NIFTY"


def test_a_second_strike_of_a_held_stock_is_blocked_the_way_force_motors_was_not(tmp_path):
    # 4 Nov 2026: the intraday ATM refresh moved FORCEMOT from 17000 to 18000 while 17000 CE was held, and the new
    # ATM's own cross opened a second position on the same stock.
    manager = _manager(tmp_path, {ATM_17000, ATM_18000})
    asyncio.run(manager.submit(ATM_17000, "BUY", lots=1, signal=_signal(ATM_17000)))
    with pytest.raises(EntryBlocked, match="one position per stock"):
        asyncio.run(manager.submit(ATM_18000, "BUY", lots=1, signal=_signal(ATM_18000)))
    assert set(manager.portfolio.positions) == {ATM_17000}
    assert manager.entry_rule_status()["blocked"] == {"STOCK_HELD": 1}


def test_the_other_side_of_a_held_stock_is_blocked_too(tmp_path):
    manager = _manager(tmp_path, {ATM_17000, PUT})
    asyncio.run(manager.submit(ATM_17000, "BUY", lots=1, signal=_signal(ATM_17000)))
    asyncio.run(manager.on_signal(_signal(PUT)))                  # the MACD path swallows it, and counts it
    assert set(manager.portfolio.positions) == {ATM_17000}
    assert manager.last_entry_block["reason"] == "STOCK_HELD"


def test_only_the_atm_leg_opens_a_position(tmp_path):
    manager = _manager(tmp_path, {ATM_18000})                    # 17000 is ITM / a replaced ladder: analysis only
    with pytest.raises(EntryBlocked, match="not the ATM contract"):
        asyncio.run(manager.submit(ATM_17000, "BUY", lots=1, signal=_signal(ATM_17000)))
    asyncio.run(manager.submit(ATM_18000, "BUY", lots=1, signal=_signal(ATM_18000)))
    assert set(manager.portfolio.positions) == {ATM_18000}


def test_a_held_contract_still_adds_and_exits_after_it_leaves_the_atm_slot(tmp_path):
    manager = _manager(tmp_path, {ATM_17000})
    asyncio.run(manager.submit(ATM_17000, "BUY", lots=1, signal=_signal(ATM_17000)))
    manager.set_entry_universe({ATM_18000})                       # the refresh moved the ATM
    asyncio.run(manager.submit(ATM_17000, "BUY", lots=1, signal=_signal(ATM_17000)))    # an add to the held position
    assert manager.portfolio.positions[ATM_17000].quantity == 50
    asyncio.run(manager.submit(ATM_17000, "SELL", 50))
    assert ATM_17000 not in manager.portfolio.positions
    asyncio.run(manager.submit(ATM_18000, "BUY", lots=1, signal=_signal(ATM_18000)))    # the stock is free again
    assert set(manager.portfolio.positions) == {ATM_18000}


def test_other_stocks_manual_orders_and_books_without_the_rule_are_untouched(tmp_path):
    manager = _manager(tmp_path, {ATM_17000, OTHER})
    asyncio.run(manager.submit(ATM_17000, "BUY", lots=1, signal=_signal(ATM_17000)))
    asyncio.run(manager.submit(OTHER, "BUY", lots=1, signal=_signal(OTHER)))           # a different stock
    asyncio.run(manager.submit(ATM_18000, "BUY", lots=1))                             # manual: the owner's call
    assert set(manager.portfolio.positions) == {ATM_17000, OTHER, ATM_18000}
    (tmp_path / "free").mkdir()
    free = _manager(tmp_path / "free", None)                                          # e.g. the spot lane
    asyncio.run(free.submit(ATM_17000, "BUY", lots=1, signal=_signal(ATM_17000)))
    asyncio.run(free.submit(ATM_18000, "BUY", lots=1, signal=_signal(ATM_18000)))
    assert len(free.portfolio.positions) == 2 and free.entry_rule_status() is None
