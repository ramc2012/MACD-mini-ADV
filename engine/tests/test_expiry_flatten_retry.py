"""Expiry sweeps keep trying every held book until its exit fills."""

import asyncio
from datetime import UTC, datetime
from types import MethodType, SimpleNamespace

from macd_trader.desk import DeskMixin
from macd_trader.engine import IST, TradingEngine, regular_session_open


def test_expiry_exit_retries_each_failed_book_on_the_next_sweep():
    macd, mp_book, blast_book = "NSE:MACDCE", "NSE:MPCE", "NSE:BLASTCE"
    positions = {macd: SimpleNamespace(quantity=50)}
    mp_positions = {mp_book: SimpleNamespace(quantity=25)}
    blast_positions = {blast_book: SimpleNamespace(quantity=10)}
    attempts = {"macd": 0, "mp": 0, "blast": 0}
    published = []

    async def macd_submit(symbol, side, quantity):
        assert (symbol, side, quantity) == (macd, "SELL", 50)
        attempts["macd"] += 1
        if attempts["macd"] == 1:
            raise RuntimeError("Live price is stale")
        positions.pop(symbol)

    async def mp_submit(symbol, side, quantity, *, note):
        assert (symbol, side, quantity, note) == (mp_book, "SELL", 25, "MP_EXPIRY_DAY_FLATTEN")
        attempts["mp"] += 1
        mp_positions.pop(symbol)
        return SimpleNamespace(status="FILLED")

    async def blast_flatten(symbol, reason):
        assert (symbol, reason) == (blast_book, "EXPIRY_DAY_FLATTEN")
        attempts["blast"] += 1
        if attempts["blast"] == 1:
            return False
        blast_positions.pop(symbol)
        return True

    armed = set()
    reasons = {}
    execution = SimpleNamespace(
        arm_exit=lambda symbol, reason: (armed.add(symbol), reasons.update({symbol: reason})),
        submit=macd_submit,
        _armed_exits=armed,
        exit_reasons=reasons,
    )
    mp = SimpleNamespace(
        portfolio=SimpleNamespace(positions=mp_positions),
        expiring_symbols={mp_book},
        last_prices={}, last_price_at={}, entry_state={},
        last_order_rejection=None, submit=mp_submit,
    )
    blast = SimpleNamespace(
        portfolio=SimpleNamespace(positions=blast_positions),
        expiring_positions=lambda day: list(blast_positions),
        flatten=blast_flatten, error="temporary mark failure",
    )
    engine = SimpleNamespace(
        settings=SimpleNamespace(order_quote_max_age_seconds=10),
        portfolio=SimpleNamespace(positions=positions), execution=execution,
        mp=mp, blast=blast, position_mark_errors={}, rollover_errors={},
        _flattened_expiry_day=None,
        expiring_positions=lambda: list(positions),
        events=SimpleNamespace(publish=lambda topic, payload: published.append((topic, payload))),
    )
    # The desk's share of the sweep lives on DeskMixin, which TradingEngine inherits.
    engine.expiring_desk_positions = MethodType(DeskMixin.expiring_desk_positions, engine)
    engine._flatten_expiring_desk = MethodType(DeskMixin._flatten_expiring_desk, engine)
    now = datetime(2026, 9, 28, 15, 10, tzinfo=IST)

    asyncio.run(TradingEngine._flatten_expiring_positions(engine, now))
    assert attempts == {"macd": 1, "mp": 0, "blast": 1}
    assert engine._flattened_expiry_day is None
    assert set(engine.rollover_errors) == {
        f"macd_expiry:{macd}", f"mp_expiry:{mp_book}", f"blast_expiry:{blast_book}"
    }
    assert not armed and macd not in reasons

    mp.last_prices[mp_book] = 42.0
    mp.last_price_at[mp_book] = datetime.now(UTC)
    asyncio.run(TradingEngine._flatten_expiring_positions(engine, now))
    assert attempts == {"macd": 2, "mp": 1, "blast": 2}
    assert engine._flattened_expiry_day == "2026-09-28"
    assert not engine.rollover_errors and not engine.position_mark_errors
    assert len(published) == 2


def test_regular_session_ends_at_1530_not_at_the_end_of_that_minute():
    assert regular_session_open(datetime(2026, 9, 28, 9, 15, tzinfo=IST))
    assert regular_session_open(datetime(2026, 9, 28, 15, 29, 59, tzinfo=IST))
    assert not regular_session_open(datetime(2026, 9, 28, 15, 30, tzinfo=IST))
