"""The MP desk's two P&L surfaces must reconcile, and say how.

`statistics()` derives round trips by FIFO-matching persisted fills and charges
each round trip only the pro-rata slice of the entry fee belonging to the
quantity that actually closed. The portfolio's `realized_pnl` is cash-basis:
every rupee of fee left the account at fill time, including on positions that
are still open.

Both are correct answers to different questions, and both are shown in the UI
as "P&L". Measured against the live book on 2026-08-27 they disagreed by
exactly Rs 80 — the four open positions' Rs 20 entry fees — with nothing on
either surface explaining the gap. These tests pin the bridge that does.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta

from macd_trader.models import Trade
from macd_trader.mp_engine import MPEngine, MPSettings


def _engine(folder: str) -> MPEngine:
    return MPEngine(f"{folder}/mp.sqlite3", settings=MPSettings(enabled=True))


def _fill(engine: MPEngine, symbol: str, side: str, qty: int, price: float,
          *, fees: float = 20.0, minute: int = 0, lot_size: int = 5) -> None:
    """Persist a fill exactly the way the desk does, so statistics() sees it.

    lot_size is a per-CONTRACT constant, never the fill quantity: the portfolio
    rejects a lot-size change on an open position, so deriving it from qty would
    make any partial exit raise instead of scaling out.
    """
    trade = Trade(
        order_id=f"o-{symbol}-{side}-{minute}",
        trade_id=f"t-{symbol}-{side}-{minute}",
        symbol=symbol, side=side, quantity=qty, price=price,
        lots=max(1, qty // lot_size), lot_size=lot_size, fees=fees,
        timestamp=datetime(2026, 8, 26, 9, 15) + timedelta(minutes=minute),
    )
    engine.portfolio.apply_trade(trade)
    engine.repository.save_trade(trade)


def test_a_fully_closed_book_reconciles_with_nothing_left_over():
    """No open positions -> no parked entry fees -> the two surfaces agree
    exactly, and the bridge field is zero rather than absent."""
    with tempfile.TemporaryDirectory() as folder:
        engine = _engine(folder)
        _fill(engine, "TEST-CE", "BUY", 100, 50.0, minute=0)
        _fill(engine, "TEST-CE", "SELL", 100, 60.0, minute=10)

        stats = engine.statistics()
        assert stats["round_trips"] == 1
        assert stats["open_position_entry_fees"] == 0.0
        assert stats["net_cash_basis"] == stats["net"]
        assert round(stats["net_cash_basis"], 6) == round(engine.portfolio.realized_pnl, 6)


def test_an_open_position_parks_its_entry_fee_and_the_bridge_explains_it():
    """This is the live 2026-08-27 shape: some round trips closed, some
    positions still open. `net` excludes the open entry fees; realized_pnl has
    already paid them; `net_cash_basis` is the one that matches the book."""
    with tempfile.TemporaryDirectory() as folder:
        engine = _engine(folder)
        # One completed round trip.
        _fill(engine, "CLOSED-CE", "BUY", 100, 50.0, minute=0)
        _fill(engine, "CLOSED-CE", "SELL", 100, 60.0, minute=10)
        # Two positions opened and still held — Rs 20 entry fee each.
        _fill(engine, "OPEN-A-CE", "BUY", 50, 30.0, minute=20)
        _fill(engine, "OPEN-B-PE", "BUY", 75, 40.0, minute=30)

        stats = engine.statistics()
        assert stats["round_trips"] == 1
        assert stats["open_position_entry_fees"] == 40.0, "two open positions x Rs 20"
        # The identity the UI can now show instead of an unexplained gap.
        assert round(stats["net"] - stats["open_position_entry_fees"], 6) == round(
            stats["net_cash_basis"], 6
        )
        assert round(stats["net_cash_basis"], 6) == round(engine.portfolio.realized_pnl, 6)
        # And the gap is real, not cosmetic — `net` alone does NOT match the book.
        assert round(stats["net"], 6) != round(engine.portfolio.realized_pnl, 6)


def test_partially_closing_a_position_only_parks_the_unclosed_share_of_the_fee():
    """Selling half leaves half the entry fee parked. A flat per-position
    charge would over- or under-state the bridge as soon as scaling out
    happens, which this desk does (entry_stage / exit_stage)."""
    with tempfile.TemporaryDirectory() as folder:
        engine = _engine(folder)
        _fill(engine, "SCALE-CE", "BUY", 100, 50.0, fees=20.0, minute=0)
        _fill(engine, "SCALE-CE", "SELL", 40, 55.0, fees=20.0, minute=10)

        stats = engine.statistics()
        assert stats["round_trips"] == 1
        # 60 of 100 still held -> 60% of the Rs 20 entry fee remains parked.
        assert round(stats["open_position_entry_fees"], 6) == 12.0
        assert round(stats["net_cash_basis"], 6) == round(engine.portfolio.realized_pnl, 6)


def test_the_bridge_survives_a_restart_because_it_is_derived_from_persisted_fills():
    """Neither surface stores a P&L number: the portfolio is replayed from the
    `trades` table by _restore() and statistics() re-derives from the same
    rows. A second engine on the same database must therefore reproduce both
    figures exactly — this is what makes the reconciliation durable rather
    than an artefact of one process's memory."""
    with tempfile.TemporaryDirectory() as folder:
        engine = _engine(folder)
        _fill(engine, "CLOSED-CE", "BUY", 100, 50.0, minute=0)
        _fill(engine, "CLOSED-CE", "SELL", 100, 60.0, minute=10)
        _fill(engine, "OPEN-A-CE", "BUY", 50, 30.0, minute=20)
        before = engine.statistics()
        before_realized = engine.portfolio.realized_pnl
        engine.repository.close()

        reborn = _engine(folder)  # fresh Portfolio + _restore() replay
        after = reborn.statistics()

        assert after["round_trips"] == before["round_trips"]
        assert round(after["net"], 6) == round(before["net"], 6)
        assert round(after["open_position_entry_fees"], 6) == round(
            before["open_position_entry_fees"], 6
        )
        assert round(reborn.portfolio.realized_pnl, 6) == round(before_realized, 6)
        assert round(after["net_cash_basis"], 6) == round(reborn.portfolio.realized_pnl, 6)


def test_an_empty_book_reports_zeroes_not_missing_keys():
    """The UI reads these unconditionally; absent keys would render as blank
    rather than as a genuine zero."""
    with tempfile.TemporaryDirectory() as folder:
        stats = _engine(folder).statistics()
        assert stats["round_trips"] == 0
        assert stats["open_position_entry_fees"] == 0.0
        assert stats["net_cash_basis"] == 0.0
