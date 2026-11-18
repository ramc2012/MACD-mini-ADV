"""Grok picks and manages the smart books' trades (owner, 18 Nov 2026): it may skip any signal, every take is a
fixed ticket (Rs 1 L in the Rs 50 L book, a third of equity in the Rs 1 L index-only book), and its stop/target
premiums are enforced on ticks beside a 50% safety stop."""
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from macd_trader import smart_grok
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.models import Signal
from macd_trader.smart_lane import SmartLane, SmartLanes

pytestmark = pytest.mark.usefixtures("no_stock_t2")

STOCK = "NSE:SBIN26OCT800CE"
INDEX = "NSE:NIFTY26OCT25000CE"
T0 = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)          # 10:00 IST, Monday


class NoOrders:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper lanes must never reach the broker's order API")


class FakeGrok:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def decide(self, system, user, max_tokens=4000, schema=None, schema_name=""):
        self.calls.append({"system": system, "user": user, "schema": schema_name})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(user)
        return reply, {"latency_ms": 1200, "model": "grok-test", "prompt_tokens": 900, "completion_tokens": 80,
                       "reasoning_tokens": 40, "cost_ticks": 150_000_000}


def build(tmp_path: Path, name="smart_50l", capital=5_000_000, grok=None, **overrides) -> SmartLane:
    settings = Settings(**{"execution_mode": "paper", "slippage_bps": 0, "database_path": str(tmp_path / "m.sqlite3"),
                           **overrides})
    lane = SmartLane(name, capital, settings, NoOrders(), EventHub(), str(tmp_path / f"{name}.sqlite3"))
    lane.configure_grok(True, str(tmp_path))
    lane.grok.client_factory = lambda: grok
    lane.set_tradable_contracts({STOCK: 100, INDEX: 65})
    lane.set_contract_context({
        STOCK: {"spot_symbol": "NSE:SBIN-EQ", "option_type": "CE", "strike": 800, "expiry": "2099-12-30"},
        INDEX: {"spot_symbol": "NSE:NIFTY50-INDEX", "option_type": "CE", "strike": 25000, "expiry": "2099-12-30"}})
    lane.set_resolvers(macd_confirm=lambda *_a: True, dispersion=lambda: None)
    return lane


def signal(symbol=STOCK, price=10.0, bucket=1000) -> Signal:
    return Signal(symbol, "BUY", "TEST", price, 0.1, 0.0, 0.1, evaluated_candle_timestamp=bucket)


def take(stop=None, target=None, action="take"):
    def reply(user):
        import json
        ids = [c["id"] for c in json.loads(user)["candidates"]]
        return {"market_read": "calls favoured", "picks": [
            {"id": i, "action": action, "conviction": "high", "reason": "with the index", "stop_premium": stop,
             "target_premium": target} for i in ids]}
    return reply


async def run_batch(lane, start=T0):
    """Signal at start; the 5-minute check queues it; 60 s later the batch goes to Grok and comes back."""
    await lane.step(start + timedelta(seconds=330))
    await lane.step(start + timedelta(seconds=395))
    task = lane.grok._entry_task
    if task is not None:
        await task
    await lane.step(start + timedelta(seconds=400))
    lane.flush_journal()


def test_grok_takes_a_signal_at_the_fixed_ticket_and_its_target_closes_it(tmp_path):
    grok = FakeGrok(take(stop=6.0, target=20.0))
    lane = build(tmp_path, grok=grok)
    lane.on_macd_signal(signal(), "CE", {"dte": 20.0, "u_ret_open": 0.004}, now=T0)
    lane.execution.set_quote(STOCK, 10.0)
    asyncio.run(run_batch(lane))
    position = lane.portfolio.positions[STOCK]
    assert position.lots == 100                                   # Rs 1 L / Rs 1,000 a lot
    assert lane.meta[STOCK]["source"] == smart_grok.GROK_SOURCE
    assert lane.grok_levels[STOCK]["stop"] == 6.0 and lane.grok_levels[STOCK]["target"] == 20.0
    assert '"candidates"' in grok.calls[0]["user"] and "u_ret_open_pct" in grok.calls[0]["user"]
    taken = lane.journal_rows(decision="TAKEN")[0]
    assert taken["detail"]["grok"]["reason"] == "with the index" and taken["ticket"] == pytest.approx(100_000)
    # No trail, staged thirds or fixed +100% target in Grok mode: +90% is held, Grok's 20 closes it.
    asyncio.run(lane.execution.on_tick(STOCK, 19.0))
    assert lane.portfolio.positions[STOCK].lots == 100
    asyncio.run(lane.execution.on_tick(STOCK, 20.5))
    assert STOCK not in lane.portfolio.positions
    assert lane.trades()[0]["exit_reason"] == "GROK_TARGET" and lane.trades()[0]["source"] == "grok"
    usage = lane.grok.usage_today()          # calls are journalled at wall-clock time
    assert usage["calls"] == 1 and usage["taken"] == 1 and usage["cost_usd"] == pytest.approx(0.015)


def test_grok_may_skip_and_a_failed_call_skips_too(tmp_path):
    lane = build(tmp_path, grok=FakeGrok(take(action="skip"), smart_grok.brain.BrainError("HTTP 503")))
    lane.execution.set_quote(STOCK, 10.0)
    lane.on_macd_signal(signal(), "CE", {}, now=T0)
    asyncio.run(run_batch(lane))
    assert not lane.portfolio.positions and lane.counts.get("GROK_SKIP") == 1
    lane.on_macd_signal(signal(bucket=2000), "CE", {}, now=T0 + timedelta(minutes=30))
    lane.execution.set_quote(STOCK, 10.0)
    asyncio.run(run_batch(lane, T0 + timedelta(minutes=30)))
    assert not lane.portfolio.positions and lane.counts.get("GROK_UNAVAILABLE") == 1
    calls = lane.grok.calls()
    assert len(calls) == 2 and calls[0]["error"] == "HTTP 503" and calls[1]["response"]["picks"][0]["action"] == "skip"


def test_no_key_means_no_entry(tmp_path):
    lane = build(tmp_path, grok=None)
    lane.execution.set_quote(STOCK, 10.0)
    lane.on_macd_signal(signal(), "CE", {}, now=T0)
    asyncio.run(run_batch(lane))
    assert not lane.portfolio.positions and lane.counts.get("GROK_UNAVAILABLE") == 1


def test_the_one_lakh_book_takes_index_signals_only_at_a_third_of_equity(tmp_path):
    lane = build(tmp_path, name="smart_1l", capital=100_000, grok=FakeGrok(take()))
    lane.on_macd_signal(signal(STOCK), "CE", {}, now=T0)
    assert not lane._pending                                      # stock signal never reaches this book
    lane.on_macd_signal(signal(INDEX, 100.0), "CE", {}, now=T0)
    lane.execution.set_quote(INDEX, 100.0)
    asyncio.run(run_batch(lane))
    assert lane.portfolio.positions[INDEX].lots == 5              # Rs 33,000 / Rs 6,500 a lot
    assert lane.grok.slots == 3 and lane.grok.index_only


def test_a_premium_that_ran_away_while_grok_thought_is_not_chased(tmp_path):
    lane = build(tmp_path, grok=FakeGrok(take()))
    lane.on_macd_signal(signal(), "CE", {}, now=T0)
    lane.execution.set_quote(STOCK, 10.0)

    async def go():
        await lane.step(T0 + timedelta(seconds=330))
        await lane.step(T0 + timedelta(seconds=395))
        await lane.grok._entry_task
        lane.execution.set_quote(STOCK, 11.0)                    # +10% > 8%
        await lane.step(T0 + timedelta(seconds=400))
    asyncio.run(go())
    assert not lane.portfolio.positions and lane.counts.get("GROK_PRICE_MOVED") == 1


def test_the_safety_stop_holds_whatever_grok_set_and_bad_levels_are_refused(tmp_path):
    lane = build(tmp_path, grok=FakeGrok(take(stop=3.0, target=8.0)))   # stop under the 50% floor, target below
    lane.on_macd_signal(signal(), "CE", {}, now=T0)
    lane.execution.set_quote(STOCK, 10.0)
    asyncio.run(run_batch(lane))
    assert lane.grok_levels[STOCK]["stop"] is None and lane.grok_levels[STOCK]["target"] is None
    assert lane.portfolio.positions[STOCK].hard_stop == pytest.approx(5.0)
    asyncio.run(lane.execution.on_tick(STOCK, 4.9))
    assert lane.trades()[0]["exit_reason"] == "HARD_STOP_50_PCT"


def test_the_half_hourly_review_closes_halves_and_moves_levels(tmp_path):
    def review(user):
        import json
        rows = {r["symbol"]: r for r in json.loads(user)["positions"]}
        assert rows[STOCK]["stop"] == 6.0 and rows[STOCK]["entry_reason"] == "with the index"
        return {"market_read": "fading", "positions": [
            {"symbol": STOCK, "action": "reduce_half", "reason": "lock half", "stop_premium": 11.0, "target_premium": 30.0},
            {"symbol": INDEX, "action": "close", "reason": "momentum gone", "stop_premium": None, "target_premium": None}]}

    lane = build(tmp_path, grok=FakeGrok(take(stop=6.0, target=20.0), review))
    lane.on_macd_signal(signal(), "CE", {}, now=T0)
    lane.on_macd_signal(signal(INDEX, 100.0), "CE", {}, now=T0)
    lane.execution.set_quote(STOCK, 10.0)
    lane.execution.set_quote(INDEX, 100.0)

    async def go():
        await run_batch(lane)
        lane.execution.set_quote(STOCK, 12.0)
        lane.execution.set_quote(INDEX, 100.0)
        at = datetime(2026, 10, 5, 4, 46, 30, tzinfo=UTC)        # 10:16 IST: the next half-hour review
        await lane.step(at)
        await lane.grok._exit_task
        await lane.step(at + timedelta(seconds=2))
    asyncio.run(go())
    assert INDEX not in lane.portfolio.positions
    assert lane.portfolio.positions[STOCK].lots == 50
    assert lane.grok_levels[STOCK]["stop"] == 11.0 and lane.grok_levels[STOCK]["target"] == 30.0
    assert lane.grok.last_exit["actions"][0]["action"] == "reduce_half"
    reasons = {t["symbol"]: t["exit_reason"] for t in lane.trades()}
    assert reasons[INDEX] == "GROK_EXIT"


def test_levels_survive_a_restart_and_only_named_books_run_grok(tmp_path):
    lane = build(tmp_path, grok=FakeGrok(take(stop=6.0, target=20.0)))
    lane.on_macd_signal(signal(), "CE", {}, now=T0)
    lane.execution.set_quote(STOCK, 10.0)
    asyncio.run(run_batch(lane))
    again = SmartLane("smart_50l", 5_000_000, lane.settings, NoOrders(), EventHub(), lane.database_path)
    again.configure_grok(True, str(tmp_path))
    assert again.grok_levels[STOCK]["target"] == 20.0 and again.meta[STOCK]["source"] == "grok"
    assert again.portfolio.positions[STOCK].hard_stop == pytest.approx(5.0)
    settings = Settings(execution_mode="paper", database_path=str(tmp_path / "x" / "m.sqlite3"))
    books = SmartLanes(settings, NoOrders(), EventHub())
    assert all(b.grok is not None for b in books.lanes)
    off = SmartLanes(settings.model_copy(update={"smart_grok_enabled": False}), NoOrders(), EventHub())
    assert all(b.grok is None for b in off.lanes)
    assert smart_grok.is_index_option("NSE:BANKNIFTY26NOV60000PE") and not smart_grok.is_index_option(STOCK)
    assert smart_grok.is_index_option("BSE:SENSEX26N2085000CE")
