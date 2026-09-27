import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pytest import approx

from macd_trader import portfolio as portfolio_module
from macd_trader.config import Settings
from macd_trader.engine import TradingEngine
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.models import Position, Signal, Trade
from macd_trader.portfolio import Portfolio
from macd_trader.repository import TradeRepository


class LiveFeedBrokerThatMustNotReceiveOrders:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper mode called the live broker order API")


def test_paper_order_never_reaches_live_broker(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ")
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(
        settings,
        LiveFeedBrokerThatMustNotReceiveOrders(),
        Portfolio(100_000),
        repository,
        EventHub(),
    )
    manager.set_quote("NSE:SBIN-EQ", 800.0)
    order = asyncio.run(manager.submit("NSE:SBIN-EQ", "BUY", 1))
    assert order.status == "FILLED"
    assert order.broker_order_id.startswith("PAPER-")
    repository.close()


def test_hard_stop_closes_paper_option_position(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, EventHub())
    manager.set_tradable_symbols(["NSE:SBIN26AUG800CE"])
    manager.set_quote("NSE:SBIN26AUG800CE", 100)
    asyncio.run(manager.submit("NSE:SBIN26AUG800CE", "BUY", 1))
    asyncio.run(manager.on_tick("NSE:SBIN26AUG800CE", 69))
    assert "NSE:SBIN26AUG800CE" not in manager.portfolio.positions
    assert manager.exit_reasons["NSE:SBIN26AUG800CE"] == "HARD_STOP_30_PCT"
    record = manager.closed_positions[0]
    assert record["exit_reason"] == "HARD_STOP_30_PCT"
    assert record["symbol"] == "NSE:SBIN26AUG800CE"
    assert record["partial"] is False
    assert record["min_price"] == 69
    assert record["min_return_pct"] == approx(-31)
    assert len(repository.closed_positions(lane="macd")) == 1
    snapshot = manager.snapshot()
    assert snapshot["closed_positions"] == [record]
    assert snapshot["portfolio"]["closed_positions"] == [record]
    repository.close()


def test_manual_sell_is_labelled_manual(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, EventHub())
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_symbols([symbol])
    manager.set_quote(symbol, 100)
    asyncio.run(manager.submit(symbol, "BUY", 1))
    asyncio.run(manager.submit(symbol, "SELL", 1))
    assert manager.closed_positions[0]["exit_reason"] == "MANUAL"

    asyncio.run(manager.submit(symbol, "BUY", 1))
    asyncio.run(manager.on_tick(symbol, 69))
    assert manager.closed_positions[0]["exit_reason"] == "HARD_STOP_30_PCT"

    # The stale HARD_STOP must not be stamped on to the next hand-closed trade.
    manager.set_quote(symbol, 100)
    asyncio.run(manager.submit(symbol, "BUY", 1))
    asyncio.run(manager.submit(symbol, "SELL", 1))
    assert manager.closed_positions[0]["exit_reason"] == "MANUAL"
    assert [row["exit_reason"] for row in manager.closed_positions] == ["MANUAL", "HARD_STOP_30_PCT", "MANUAL"]
    repository.close()


def test_staged_exit_records_partial_slices(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0, max_trade_lots=4)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(1_000_000), repository, EventHub())
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_contracts({symbol: 750})
    manager.set_quote(symbol, 100)
    asyncio.run(manager.submit(symbol, "BUY", lots=2))
    asyncio.run(manager.on_tick(symbol, 131))
    assert len(manager.closed_positions) == 1
    record = manager.closed_positions[0]
    assert record["exit_reason"] == "STAGED_PROFIT_EXIT_1"
    assert record["partial"] is True
    assert record["quantity"] == 750
    assert record["lots"] == 1
    assert record["remaining_quantity"] == 750
    assert record["max_return_pct"] == approx(31)
    remaining = manager.portfolio.positions[symbol]
    assert remaining.exit_stage == 1
    assert remaining.position_id == record["position_id"]
    repository.close()


def test_closed_positions_survive_restart(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, EventHub())
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_symbols([symbol])
    manager.set_quote(symbol, 100)
    asyncio.run(manager.submit(symbol, "BUY", 1))
    asyncio.run(manager.on_tick(symbol, 69))
    closed_id = manager.closed_positions[0]["closed_id"]

    restarted = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, EventHub())
    assert [row["closed_id"] for row in restarted.closed_positions] == [closed_id]
    assert restarted.closed_positions[0]["exit_reason"] == "HARD_STOP_30_PCT"

    # A record already past its 06:00 IST cut-off is history, not desk state.
    book = Portfolio(100_000)
    book.apply_trade(Trade("o1", symbol, "BUY", 1, 100, timestamp=datetime(2026, 8, 3, 4, 0, tzinfo=UTC)))
    stale = book.apply_trade(Trade("o2", symbol, "SELL", 1, 90, timestamp=datetime(2026, 8, 3, 9, 0, tzinfo=UTC)))
    repository.save_closed_position(stale)
    again = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, EventHub())
    assert [row["closed_id"] for row in again.closed_positions] == [closed_id]
    assert len(repository.closed_positions(lane="macd")) == 2
    repository.close()


def test_sweep_hides_after_0600_ist(tmp_path: Path, monkeypatch):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    hub = EventHub()
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, hub)
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_symbols([symbol])
    manager.set_quote(symbol, 100)

    async def scenario():
        queue = await hub.subscribe()
        await manager.submit(symbol, "BUY", 1)
        assert manager.sweep_closed_positions() is False
        # Freeze the clock so the close is already past its visibility.
        monkeypatch.setattr(
            portfolio_module, "closed_visible_until",
            lambda _exit_time: datetime.now(UTC) - timedelta(minutes=1),
        )
        await manager.on_tick(symbol, 69)
        assert len(manager.closed_positions) == 1
        assert manager.visible_closed_positions() == []
        while not queue.empty():
            queue.get_nowait()
        assert manager.sweep_closed_positions() is True
        frame = queue.get_nowait()
        assert frame["type"] == "portfolio"
        assert frame["data"]["closed_positions"] == []
        assert manager.sweep_closed_positions() is False

    asyncio.run(scenario())
    assert len(repository.closed_positions(lane="macd")) == 1
    assert repository.closed_positions(lane="macd", visible_after=datetime.now(UTC)) == []
    assert repository.purge_closed_positions(datetime.now(UTC)) == 1
    assert repository.closed_positions(lane="macd") == []
    repository.close()


def test_portfolio_event_carries_closed_positions(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    hub = EventHub()
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, hub)
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_symbols([symbol])
    manager.set_quote(symbol, 100)

    async def scenario():
        queue = await hub.subscribe()
        await manager.submit(symbol, "BUY", 1)
        await manager.on_tick(symbol, 69)
        frames = []
        while not queue.empty():
            frames.append(queue.get_nowait())
        return frames

    frames = asyncio.run(scenario())
    types = [frame["type"] for frame in frames]
    closed_index = types.index("position_closed")
    last_portfolio_index = max(index for index, kind in enumerate(types) if kind == "portfolio")
    assert closed_index < last_portfolio_index
    assert len(frames[last_portfolio_index]["data"]["closed_positions"]) == 1
    assert frames[closed_index]["data"]["exit_reason"] == "HARD_STOP_30_PCT"
    repository.close()


def _bare_engine(settings: Settings, repository: TradeRepository, portfolio: Portfolio) -> TradingEngine:
    engine = object.__new__(TradingEngine)
    engine.settings = settings
    engine.repository = repository
    engine.portfolio = portfolio
    return engine


def test_excursions_persist_and_restore_via_engine(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    symbol = "NSE:SBIN26AUG800CE"
    book = Portfolio(100_000)
    entry = Trade("o1", symbol, "BUY", 10, 100, timestamp=datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    book.apply_trade(entry)
    repository.save_trade(entry)
    book.mark(symbol, 130)
    book.mark(symbol, 80)
    live = book.positions[symbol]
    stale = Position(symbol, 10, 50, 50, datetime(2026, 8, 1, 4, 0, tzinfo=UTC))
    stale.max_price = 999
    repository.save_position_excursions([stale, live])

    restored = Portfolio(100_000)
    _bare_engine(settings, repository, restored)._restore_portfolio()
    position = restored.positions[symbol]
    assert position.max_price == 130
    assert position.min_price == 80
    assert position.max_return_pct == approx(30)
    assert position.min_return_pct == approx(-20)
    # The persisted MFE re-arms the trail a restart used to disarm: peak 130
    # is at the 30% activation, so the stop sits at max(97.5, breakeven 100).
    assert position.peak_price == 130
    assert position.trailing_stop == 100.0

    repository.save_position_excursions(restored.positions.values())
    assert list(repository.load_position_excursions()) == [position.position_id]
    repository.close()


def test_restore_replays_fees(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    symbol = "NSE:SBIN26AUG800CE"
    repository.save_trade(Trade("o1", symbol, "BUY", 10, 100, fees=20, timestamp=datetime(2026, 9, 1, 4, 0, tzinfo=UTC)))
    repository.save_trade(Trade("o2", symbol, "SELL", 10, 110, fees=20, timestamp=datetime(2026, 9, 1, 5, 0, tzinfo=UTC)))
    restored = Portfolio(100_000)
    _bare_engine(settings, repository, restored)._restore_portfolio()
    assert restored.realized_pnl == 60
    assert restored.positions == {}
    repository.close()


def test_option_lots_are_converted_to_exchange_quantity(tmp_path: Path):
    # max_trade_lots pinned: the deployment .env sets MACD_MAX_TRADE_LOTS and
    # pydantic-settings loads it, so relying on the class default makes this
    # test depend on the machine it runs on.
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0, max_trade_lots=4)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, EventHub())
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_contracts({symbol: 750})
    manager.set_quote(symbol, 10)
    order = asyncio.run(manager.submit(symbol, "BUY", lots=2))
    position = manager.portfolio.positions[symbol]
    assert order.lots == 2
    assert order.lot_size == 750
    assert order.quantity == 1500
    assert position.lots == 2
    assert position.quantity == 1500
    repository.close()


def test_auto_entry_sizes_to_nearest_target_notional(tmp_path: Path):
    settings = Settings(
        execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0,
        auto_trade=True, max_trade_lots=4, target_position_notional=100_000,
    )
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(
        settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(5_000_000),
        repository, EventHub(),
    )
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_contracts({symbol: 750})
    manager.set_quote(symbol, 40)
    signal = Signal(symbol, "BUY", "TEST", 40, 1, 0, 1)

    asyncio.run(manager.on_signal(signal))

    position = manager.portfolio.positions[symbol]
    assert position.lots == 3
    assert position.quantity == 2250
    assert position.average_price * position.quantity == 90_000
    repository.close()


def test_target_sizing_respects_available_paper_cash(tmp_path: Path):
    settings = Settings(
        execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0,
        auto_trade=True, max_trade_lots=4, target_position_notional=100_000,
    )
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(
        settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(35_000),
        repository, EventHub(),
    )
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_contracts({symbol: 750})
    manager.set_quote(symbol, 40)

    assert manager.entry_lots(symbol, 40) == 1
    repository.close()


def test_notional_entry_can_exceed_pyramiding_lot_cap(tmp_path: Path):
    settings = Settings(
        execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0,
        auto_trade=True, max_trade_lots=4, target_position_notional=100_000,
        max_target_entry_lots=125,
    )
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(
        settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(5_000_000),
        repository, EventHub(),
    )
    symbol = "BSE:SENSEX26AUG77300PE"
    manager.set_tradable_contracts({symbol: 20})
    manager.set_quote(symbol, 43.75)
    signal = Signal(symbol, "BUY", "TEST", 43.75, 1, 0, 1)

    asyncio.run(manager.on_signal(signal))

    position = manager.portfolio.positions[symbol]
    assert position.lots == 114
    assert position.quantity == 2280
    assert position.average_price * position.quantity == 99_750
    repository.close()


def test_trailing_stop_arms_only_after_thirty_percent_profit(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, EventHub())
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_contracts({symbol: 750})
    manager.set_quote(symbol, 100)
    asyncio.run(manager.submit(symbol, "BUY", lots=1))
    asyncio.run(manager.on_tick(symbol, 129.99))
    assert manager.portfolio.positions[symbol].trailing_stop is None
    asyncio.run(manager.on_tick(symbol, 130))
    # The arming THRESHOLD is what this test is about, and it is unchanged.
    # The armed VALUE used to be 130 * 0.75 = 97.5 -- below the 100 entry, so
    # the "protective" stop guaranteed a 2.5% loss. It is now clamped to the
    # round-trip breakeven, which is exactly 100 here (slippage_bps=0, no fees).
    assert manager.portfolio.positions[symbol].trailing_stop == 100.0
    assert manager.portfolio.positions[symbol].trailing_stop >= 100  # never underwater
    asyncio.run(manager.on_tick(symbol, 97.5))
    assert symbol not in manager.portfolio.positions
    assert manager.exit_reasons[symbol] == "TRAILING_STOP_25_PCT"
    repository.close()


def test_macd_cross_down_does_not_exit_position(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(100_000), repository, EventHub())
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_contracts({symbol: 750})
    manager.set_quote(symbol, 100)
    asyncio.run(manager.submit(symbol, "BUY", lots=1))
    manager.set_quote(symbol, 95)
    signal = Signal(symbol, "SELL", "PREMIUM_MACD_ZERO_CROSS_DOWN", 95, -0.1, 0.2, -0.3)
    asyncio.run(manager.on_signal(signal))
    assert manager.portfolio.positions[symbol].quantity == 750
    assert symbol not in manager.exit_reasons
    repository.close()


def test_profitable_position_scales_to_four_lots_then_exits_one_lot(tmp_path: Path):
    settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ", slippage_bps=0, auto_trade=True, max_trade_lots=4)
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(), Portfolio(1_000_000), repository, EventHub())
    symbol = "NSE:SBIN26AUG800CE"
    manager.set_tradable_contracts({symbol: 750})
    manager.set_quote(symbol, 100)
    asyncio.run(manager.submit(symbol, "BUY", lots=1))
    for price in (107.5, 115, 122.5):
        asyncio.run(manager.on_tick(symbol, price))
    assert manager.portfolio.positions[symbol].lots == 4
    assert manager.portfolio.positions[symbol].entry_stage == 4
    asyncio.run(manager.on_tick(symbol, 145))
    assert manager.portfolio.positions[symbol].lots == 3
    assert manager.portfolio.positions[symbol].exit_stage == 1
    repository.close()


class TestMacdInvalidationExit:
    """Close a position whose entry thesis failed before it ever worked.

    The entry is a MACD zero-cross UP, so MACD closing back below zero is the
    thesis failing on its own terms, and it says so early: over 3-10 Sep it
    happened before the -30% stop in 87% of hard-stopped positions, a median
    20.8 hours ahead of it.

    The gate is the whole point. Acting on every dip below zero also cuts 29%
    of winning slices and gives back more profit than it saves (-1,009,174
    forgone against 598,770 avoided) -- a healthy trade breathes through zero
    on the way up. Only a position that has never worked may be cut.
    """

    SYM = "NSE:SBIN26AUG800CE"

    def _manager(self, tmp_path: Path, **overrides):
        settings = Settings(execution_mode="paper", symbols_csv="NSE:SBIN-EQ",
                            slippage_bps=0, **overrides)
        repository = TradeRepository(str(tmp_path / "book.sqlite3"))
        manager = ExecutionManager(settings, LiveFeedBrokerThatMustNotReceiveOrders(),
                                   Portfolio(1_000_000), repository, EventHub())
        manager.set_tradable_symbols([self.SYM])
        manager.set_quote(self.SYM, 100.0)
        asyncio.run(manager.submit(self.SYM, "BUY", 1))
        return manager

    def test_off_by_default(self, tmp_path: Path):
        manager = self._manager(tmp_path)
        assert manager.settings.macd_invalidation_exit is False
        assert asyncio.run(manager.on_closed_bar(self.SYM, -4.2)) is False
        assert self.SYM in manager.portfolio.positions
        manager.repository.close()

    def test_an_unproven_position_is_closed_when_macd_turns_negative(self, tmp_path: Path):
        manager = self._manager(tmp_path, macd_invalidation_exit=True)
        manager.set_quote(self.SYM, 92.0)          # -8%, never been up
        assert asyncio.run(manager.on_closed_bar(self.SYM, -4.2)) is True
        assert self.SYM not in manager.portfolio.positions
        closed = manager.visible_closed_positions()
        assert closed and closed[0]["exit_reason"] == "MACD_INVALIDATED"
        manager.repository.close()

    def test_a_position_that_has_proved_itself_is_left_alone(self, tmp_path: Path):
        """The objection this rule has to survive: it must not curtail winners."""
        manager = self._manager(tmp_path, macd_invalidation_exit=True)
        manager.portfolio.mark(self.SYM, 118.0)    # +18% MFE, above the 10% gate
        manager.set_quote(self.SYM, 96.0)
        assert asyncio.run(manager.on_closed_bar(self.SYM, -4.2)) is False
        assert self.SYM in manager.portfolio.positions
        manager.repository.close()

    def test_the_gate_is_the_configured_threshold(self, tmp_path: Path):
        manager = self._manager(tmp_path, macd_invalidation_exit=True,
                                macd_invalidation_max_mfe_pct=0.25)
        manager.portfolio.mark(self.SYM, 118.0)    # +18%: under a 25% gate now
        manager.set_quote(self.SYM, 96.0)
        assert asyncio.run(manager.on_closed_bar(self.SYM, -4.2)) is True
        manager.repository.close()

    def test_a_positive_macd_never_exits(self, tmp_path: Path):
        manager = self._manager(tmp_path, macd_invalidation_exit=True)
        manager.set_quote(self.SYM, 92.0)
        assert asyncio.run(manager.on_closed_bar(self.SYM, 0.0)) is False
        assert asyncio.run(manager.on_closed_bar(self.SYM, 3.1)) is False
        assert self.SYM in manager.portfolio.positions
        manager.repository.close()

    def test_an_unfillable_quote_leaves_no_stale_exit_reason(self, tmp_path: Path):
        """A stale quote must not wedge the bar-close path or mislabel the
        next exit: the hard stop is still armed and the next bar retries."""
        manager = self._manager(tmp_path, macd_invalidation_exit=True)
        manager.last_prices.pop(self.SYM, None)     # no price -> submit raises
        assert asyncio.run(manager.on_closed_bar(self.SYM, -4.2)) is False
        assert self.SYM in manager.portfolio.positions
        assert self.SYM not in manager.exit_reasons
        manager.repository.close()
