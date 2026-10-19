import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from macd_trader import engine as module
from macd_trader.engine import TradingEngine, _held_contract_lots
from macd_trader.events import EventHub
from macd_trader.models import Position, Tick
from macd_trader.portfolio import Portfolio
from macd_trader.smart_lane import SmartLane
from macd_trader.config import Settings

SYMBOL = "NSE:VEDL26OCT265CE"


class PaperBroker:
    async def place_order(self, order):
        raise AssertionError("Paper close reached broker order API")

    async def quotes(self, symbols):
        return {s: Tick(s, 2.65) for s in symbols}


def engine_with_smart(tmp_path):
    settings = Settings(execution_mode="paper", slippage_bps=0)
    lane = SmartLane("smart_test", 100000, settings, PaperBroker(), EventHub(), str(tmp_path / "smart.sqlite3"))
    lane.set_tradable_contracts({SYMBOL: 100})
    lane.execution.set_quote(SYMBOL, 5.0)
    asyncio.run(lane.execution.submit(SYMBOL, "BUY", 100))
    engine = object.__new__(TradingEngine)
    engine.settings = settings
    engine.broker = PaperBroker()
    engine.portfolio = Portfolio(100000)
    engine.desk_holdings = lambda: {}
    engine.blast = SimpleNamespace(portfolio=Portfolio(100000), execution=None, mark_from=lambda _: 0)
    engine.smart_lanes = SimpleNamespace(lanes=[lane])
    engine.contract_selector = SimpleNamespace(contracts={})
    engine.last_tick_at = datetime.now(UTC)
    engine.latest_ticks = {}
    engine.lane_errors = {}
    engine.position_mark_errors = {}
    engine.events = EventHub()
    return engine, lane


def test_retention_includes_other_books_without_changing_entry_symbols(tmp_path):
    engine, lane = engine_with_smart(tmp_path)
    engine.option_symbols = []
    engine.blast.portfolio.positions["NSE:BLAST26OCT1CE"] = Position(
        "NSE:BLAST26OCT1CE", 250, 10, 10, datetime.now(UTC), lot_size=250)
    assert _held_contract_lots(engine) == {SYMBOL: 100, "NSE:BLAST26OCT1CE": 250}
    assert engine.option_symbols == []


def test_smart_only_startup_marks_do_not_trigger_an_exit(tmp_path):
    engine, lane = engine_with_smart(tmp_path)
    asyncio.run(engine._refresh_position_marks())
    assert lane.portfolio.positions[SYMBOL].last_price == 2.65
    assert len(lane.repository.rows("trades")) == 1


def test_analysis_held_contract_hits_native_smart_stop(tmp_path, monkeypatch):
    engine, lane = engine_with_smart(tmp_path)
    monkeypatch.setattr(module, "in_session", lambda *_: True)
    tick = Tick(SYMBOL, 2.65, timestamp=datetime.now(UTC))
    asyncio.run(engine._manage_analysis_holdings(tick, tick.timestamp))
    assert SYMBOL not in lane.portfolio.positions
    assert lane.execution.closed_positions[-1]["exit_reason"] == "HARD_STOP_40_PCT"
    assert engine.lane_errors == {}


def test_old_session_analysis_quote_only_marks_the_holding(tmp_path, monkeypatch):
    engine, lane = engine_with_smart(tmp_path)
    monkeypatch.setattr(module, "in_session", lambda *_: True)
    tick = Tick(SYMBOL, 2.65, timestamp=datetime.now(UTC) - timedelta(days=1))
    asyncio.run(engine._manage_analysis_holdings(tick, tick.timestamp))
    assert lane.portfolio.positions[SYMBOL].last_price == 2.65
    assert len(lane.repository.rows("trades")) == 1


def test_explicit_close_is_durable_and_duplicate_cannot_sell_twice(tmp_path):
    engine, lane = engine_with_smart(tmp_path)
    order = asyncio.run(engine.close_smart_paper_position("smart_test", SYMBOL, 100))
    assert order.status == "FILLED"
    assert order.fill_price == 2.65
    assert lane.execution.closed_positions[-1]["exit_reason"] == "MANUAL_USER_CLOSE"
    assert len(lane.repository.rows("trades")) == 2
    with pytest.raises(ValueError, match="quantity changed"):
        asyncio.run(engine.close_smart_paper_position("smart_test", SYMBOL, 100))
    assert len(lane.repository.rows("trades")) == 2
    restored = SmartLane("smart_test", 100000, engine.settings, PaperBroker(), EventHub(), lane.database_path)
    assert SYMBOL not in restored.portfolio.positions


def test_explicit_close_refuses_live_mode_and_changed_quantity(tmp_path):
    engine, lane = engine_with_smart(tmp_path)
    with pytest.raises(ValueError, match="quantity changed"):
        asyncio.run(engine.close_smart_paper_position("smart_test", SYMBOL, 200))
    engine.settings = engine.settings.model_copy(update={"execution_mode": "live"})
    with pytest.raises(PermissionError, match="paper-only"):
        asyncio.run(engine.close_smart_paper_position("smart_test", SYMBOL, 100))
    assert len(lane.repository.rows("trades")) == 1


def test_engine_analysis_tick_reaches_exit_without_opening_another_book(tmp_path, monkeypatch):
    bare, lane = engine_with_smart(tmp_path)
    settings = Settings(feed_mode="simulation", symbols_csv="NSE:VEDL-EQ", tick_capture_enabled=False,
                        smart_lanes_enabled=False, database_path=str(tmp_path / "core.sqlite3"),
                        mp_database_path=str(tmp_path / "mp.sqlite3"), blast_database_path=str(tmp_path / "blast.sqlite3"),
                        tick_database_path=str(tmp_path / "ticks.sqlite3"),
                        research_database_path=str(tmp_path / "history.sqlite3"),
                        contract_snapshot_path=str(tmp_path / "contracts.json"))
    engine = TradingEngine(settings)
    engine.smart_lanes = SimpleNamespace(lanes=[lane])
    engine.analysis_option_symbols = {SYMBOL}
    monkeypatch.setattr(module, "in_session", lambda *_: True)
    asyncio.run(engine.on_tick(Tick(SYMBOL, 2.65, timestamp=datetime.now(UTC))))
    assert SYMBOL not in lane.portfolio.positions
    assert engine.repository.rows("trades") == []
    assert engine.blast.repository.rows("trades") == []
    assert engine.lane_errors == {}


def test_bad_close_quote_leaves_the_ledger_untouched(tmp_path):
    engine, lane = engine_with_smart(tmp_path)
    class BadBroker:
        async def quotes(self, symbols):
            return {SYMBOL: Tick(SYMBOL, float("nan"))}
    engine.broker = BadBroker()
    with pytest.raises(RuntimeError, match="valid broker quote"):
        asyncio.run(engine.close_smart_paper_position("smart_test", SYMBOL, 100))
    assert len(lane.repository.rows("trades")) == 1
