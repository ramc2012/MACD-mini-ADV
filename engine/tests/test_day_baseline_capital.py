"""Day P&L must not count a paper capital change as profit or loss."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from macd_trader.config import Settings
from macd_trader.engine import TradingEngine


def _engine(tmp_path, capital=5_000_000.0):
    return TradingEngine(Settings(
        symbols_csv="NSE:TEST-EQ", tick_capture_enabled=False, initial_capital=capital,
        database_path=str(tmp_path / "book.sqlite3"), mp_database_path=str(tmp_path / "mp.sqlite3"),
        research_database_path=str(tmp_path / "history.sqlite3"),
        tick_database_path=str(tmp_path / "ticks.sqlite3"),
        contract_snapshot_path=str(tmp_path / "contracts.json")))


def _yesterday_close(engine, equity):
    at = (datetime.now(UTC) - timedelta(days=1)).replace(hour=10, minute=0).isoformat()
    with engine.repository._lock, engine.repository._connection:
        engine.repository._connection.execute(
            "INSERT INTO equity_points(timestamp, equity, cash, realized_pnl, unrealized_pnl) VALUES (?, ?, ?, 0, 0)",
            (at, equity, equity))


def test_a_raise_during_the_day_moves_the_baseline_with_it(tmp_path):
    engine = _engine(tmp_path)
    _yesterday_close(engine, 5_000_000.0)
    assert engine.day_baseline_equity() == 5_000_000.0
    asyncio.run(engine.reconfigure_strategy(engine.settings.model_copy(update={"initial_capital": 10_000_000.0})))
    assert engine.day_baseline_equity() == 10_000_000.0
    assert engine.portfolio.snapshot()["equity"] - engine.day_baseline_equity() == 0


def test_a_raise_after_the_close_is_not_the_next_day_s_profit(tmp_path):
    engine = _engine(tmp_path)
    _yesterday_close(engine, 5_000_000.0)
    asyncio.run(engine.reconfigure_strategy(engine.settings.model_copy(update={"initial_capital": 10_000_000.0})))
    # A restart (or the next day) recomputes the baseline from the stored log.
    fresh = _engine(tmp_path, capital=10_000_000.0)
    assert fresh.day_baseline_equity() == 10_000_000.0
