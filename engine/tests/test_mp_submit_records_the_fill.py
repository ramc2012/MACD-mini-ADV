"""A paper BUY must reach the durable book, not just the in-memory portfolio.

submit() referenced a bare `state` that it never bound -- a leftover from the
entry-fee bookkeeping added with the excursion work. Python raised NameError
at that line, which sits after portfolio.apply_trade() and `trades_today += 1`
but before save_trade(), save_equity_point() and the mp_trade/mp_portfolio
publishes. So the desk looked right on screen and wrong on disk: on 4 Sep 2026
the book took 6 orders and recorded 0 fills and 0 equity points, and when the
container restarted, the portfolio rebuilt from those trades and came back
without the day's four open positions.

Every other MP test opens a position by calling portfolio.apply_trade() and
repository.save_trade() directly, so none of them touched submit() and all 570
stayed green. These go through submit().
"""
from __future__ import annotations

import asyncio
import tempfile
import pytest
from datetime import UTC, datetime

from macd_trader.market_profile import IST
from macd_trader.mp_engine import MPEngine, MPSettings

SYM = "NSE:SBIN26SEP780CE"
LOT = 375


@pytest.fixture(autouse=True)
def regular_session_clock(monkeypatch):
    from macd_trader import mp_engine
    fixed = datetime(2026, 9, 2, 10, 0, tzinfo=IST)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(mp_engine, "datetime", Clock)
    monkeypatch.setattr(__import__(__name__, fromlist=["datetime"]), "datetime", Clock)


def _desk(folder: str, **overrides) -> MPEngine:
    desk = MPEngine(f"{folder}/mp.sqlite3", settings=MPSettings(enabled=True, **overrides))
    desk.session_day = datetime.now(IST).date().isoformat()
    desk.set_lot_sizes({SYM: LOT})
    desk.last_prices[SYM] = 40.0
    desk.last_price_at[SYM] = datetime.now(UTC)
    return desk


def test_a_buy_writes_the_trade_and_the_equity_point():
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        order = asyncio.run(desk.submit(SYM, "BUY", LOT, note="responsive_buy"))

        assert order is not None, desk.last_order_rejection
        assert order.status == "FILLED"
        # The three durable writes that the NameError skipped.
        fills = desk.repository.rows("trades")
        assert len(fills) == 1
        assert desk.repository.equity_rows()
        assert SYM in desk.portfolio.positions


def test_a_buy_publishes_the_trade_and_the_portfolio():
    """The same NameError also cost the UI its live MP entry events."""
    published: list[str] = []

    class _Events:
        def publish(self, event, payload):  # noqa: ARG002
            published.append(event)

    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        desk.events = _Events()
        assert asyncio.run(desk.submit(SYM, "BUY", LOT)) is not None
        assert {"mp_order", "mp_trade", "mp_portfolio"} <= set(published)


def test_adding_to_a_position_keeps_the_fees_already_paid():
    """entry_fees accumulates across a pyramid: the statement that writes it
    overwrites entry_state[symbol], so it must read the prior entry first."""
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        asyncio.run(desk.submit(SYM, "BUY", LOT))
        first = desk.entry_state[SYM]["entry_fees"]
        asyncio.run(desk.submit(SYM, "BUY", LOT))
        second = desk.entry_state[SYM]["entry_fees"]

        assert first == desk.settings.brokerage_per_leg
        assert second == 2 * desk.settings.brokerage_per_leg


def test_the_days_fills_survive_a_restart():
    """The failure the NameError actually caused: a restart that rebuilds the
    portfolio from the trade book found the day empty and dropped the position."""
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        assert asyncio.run(desk.submit(SYM, "BUY", LOT)) is not None
        held = desk.portfolio.positions[SYM].quantity

        restarted = MPEngine(f"{folder}/mp.sqlite3", settings=MPSettings(enabled=True))
        assert SYM in restarted.portfolio.positions
        assert restarted.portfolio.positions[SYM].quantity == held
