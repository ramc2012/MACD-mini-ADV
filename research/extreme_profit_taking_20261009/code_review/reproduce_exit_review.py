"""Disposable-book reproductions; run with the app's Python dependencies.

Creates only TemporaryDirectory SQLite databases. No broker orders, production
books, runtime settings, or source are modified. This script deliberately closes
repositories directly when simulating an unclean process exit, bypassing the
lane's graceful shutdown checkpoint.
"""
import asyncio
import json
import tempfile
from pathlib import Path

from macd_trader.blast_engine import BlastEngine
from macd_trader.config import Settings
from macd_trader.engine import TradingEngine
from macd_trader.events import EventHub
from macd_trader.execution import ExecutionManager
from macd_trader.models import Signal
from macd_trader.portfolio import Portfolio
from macd_trader.repository import TradeRepository
from macd_trader.smart_lane import SmartLane

SYMBOL = "NSE:SBIN26OCT800CE"


class NoOrders:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper-only reproduction must not call broker")


def emit(case, **data):
    print(json.dumps({"case": case, **data}, sort_keys=True))


def fresh_manager(root, *, auto_trade=False):
    settings = Settings(execution_mode="paper", slippage_bps=0,
                        auto_trade=auto_trade,
                        database_path=str(root / "macd.sqlite3"))
    repository = TradeRepository(settings.database_path)
    portfolio = Portfolio(100_000)
    manager = ExecutionManager(settings, NoOrders(), portfolio, repository, EventHub())
    manager.set_tradable_contracts({SYMBOL: 100})
    return settings, repository, portfolio, manager


async def smart_stage(root):
    settings = Settings(execution_mode="paper", slippage_bps=0,
                        smart_max_hold_sessions=0,
                        database_path=str(root / "macd.sqlite3"))
    path = str(root / "smart.sqlite3")
    lane = SmartLane("smart_review", 100_000, settings, NoOrders(), EventHub(), path)
    lane.set_tradable_contracts({SYMBOL: 100})
    lane.execution.set_quote(SYMBOL, 10)
    await lane.execution.submit(SYMBOL, "BUY", lots=10,
                                signal=Signal(SYMBOL, "BUY", "REVIEW", 10, 0, 0, 0))
    await lane.execution.on_tick(SYMBOL, 13.1)
    p = lane.portfolio.positions[SYMBOL]
    before = {"lots": p.lots, "exit_stage": p.exit_stage}
    checkpoint = dict(lane.repository.load_position_excursions()[p.position_id])
    lane.repository.close()
    lane._db.close()
    restarted = SmartLane("smart_review", 100_000, settings, NoOrders(), EventHub(), path)
    restarted.set_tradable_contracts({SYMBOL: 100})
    p = restarted.portfolio.positions[SYMBOL]
    restored = {"lots": p.lots, "exit_stage": p.exit_stage}
    await restarted.execution.on_tick(SYMBOL, 13.1)
    p = restarted.portfolio.positions[SYMBOL]
    emit("Smart repeats staged exit after restart", before=before,
         checkpoint_exit_stage=checkpoint["exit_stage"], restored=restored,
         after_same_price_tick={"lots": p.lots, "exit_stage": p.exit_stage},
         close_reasons=[r["exit_reason"] for r in restarted.repository.closed_positions()])
    restarted.repository.close()
    restarted._db.close()


async def valuation_peak(root):
    settings, repository, portfolio, manager = fresh_manager(root)
    manager.set_quote(SYMBOL, 100)
    await manager.submit(SYMBOL, "BUY", lots=1)
    await manager.on_tick(SYMBOL, 140)
    p = portfolio.positions[SYMBOL]
    before = {"peak": p.peak_price, "trail": p.trailing_stop}
    portfolio.mark(SYMBOL, 200)  # exact valuation-only path used off session
    repository.save_position_excursions(portfolio.positions.values())
    restored_book = Portfolio(100_000)
    engine = object.__new__(TradingEngine)
    engine.settings, engine.repository, engine.portfolio = settings, repository, restored_book
    engine._restore_portfolio()
    p = restored_book.positions[SYMBOL]
    emit("Valuation-only high promoted to executable peak on restart", before=before,
         valuation_only_price=200, restored={"peak": p.peak_price, "trail": p.trailing_stop},
         current_price_130_exits_before_restart=130 <= before["trail"],
         current_price_130_exits_after_restart=130 <= p.trailing_stop)
    repository.close()


async def blast_checkpoint(root):
    settings = Settings(execution_mode="paper", slippage_bps=0,
                        database_path=str(root / "macd.sqlite3"))
    path = str(root / "blast.sqlite3")
    lane = BlastEngine(path, settings, NoOrders(), EventHub())
    lane.set_tradable_contracts({SYMBOL: 100})
    lane.execution.set_quote(SYMBOL, 10)
    await lane.execution.submit(SYMBOL, "BUY", lots=1)
    await lane.execution.on_tick(SYMBOL, 20)
    p = lane.portfolio.positions[SYMBOL]
    before = {"peak": p.peak_price, "trail": p.trailing_stop}
    lane.flush_watchers()  # what the 30-second engine maintenance loop runs
    checkpoint = dict(lane.repository.load_position_excursions()[p.position_id])
    lane.repository.close()  # simulate crash, not graceful lane.stop()
    restarted = BlastEngine(path, settings, NoOrders(), EventHub())
    p = restarted.portfolio.positions[SYMBOL]
    emit("Blast intratrade peak lost on unclean restart", before=before,
         checkpoint_max_price=checkpoint["max_price"],
         restored={"peak": p.peak_price, "trail": p.trailing_stop},
         current_price_11_exits_before_restart=11 <= before["trail"],
         current_price_11_exits_after_restart=p.trailing_stop is not None and 11 <= p.trailing_stop)
    restarted.repository.close()


async def spike_pyramid(root):
    _settings, repository, portfolio, manager = fresh_manager(root, auto_trade=True)
    manager.set_quote(SYMBOL, 100)
    await manager.submit(SYMBOL, "BUY", lots=1)
    path = []
    for price in (200, 200, 200, 150):
        await manager.on_tick(SYMBOL, price)
        p = portfolio.positions.get(SYMBOL)
        path.append({"price": price, "lots": p.lots if p else 0,
                     "average": p.average_price if p else None,
                     "exit_stage": p.exit_stage if p else None,
                     "realized_pnl": portfolio.realized_pnl})
    emit("Policy: jump adds before harvest; moving cost basis suppresses ladder", path=path,
         fills=[{"side": r["side"], "price": r["price"], "quantity": r["quantity"]}
                for r in repository.rows("trades")],
         last_exit_reason=manager.exit_reasons.get(SYMBOL))
    repository.close()


async def main():
    with tempfile.TemporaryDirectory(prefix="exit-review-") as td:
        root = Path(td)
        for name, case in (("smart", smart_stage), ("valuation", valuation_peak),
                           ("blast", blast_checkpoint), ("pyramid", spike_pyramid)):
            path = root / name
            path.mkdir()
            await case(path)


if __name__ == "__main__":
    asyncio.run(main())
