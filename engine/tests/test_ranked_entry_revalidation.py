"""A ranked MACD entry must still qualify when its queue is drained."""
import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from macd_trader import engine as engine_module, entry_scorer, execution, models
from macd_trader.config import Settings
from macd_trader.blast_engine import BlastEngine
from macd_trader.events import EventHub
from macd_trader.contracts import OptionContract
from macd_trader.engine import TradingEngine
from macd_trader.indicators import IncrementalKAMA
from macd_trader.models import Candle, IndicatorPoint, Signal, Tick


SYMBOL = "NSE:TEST26OCT100CE"
START = datetime(2026, 9, 28, 5, 30, tzinfo=UTC)  # Monday 11:00 IST


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        moment = START

        @classmethod
        def now(cls, tz=None):
            return cls.moment.astimezone(tz) if tz else cls.moment.replace(tzinfo=None)

    for module in (engine_module, entry_scorer, execution, models):
        monkeypatch.setattr(module, "datetime", Clock)
    return Clock


@pytest.fixture
def engine(tmp_path, clock, monkeypatch):
    configured = Settings(
        engine_role="strategy", symbols_csv="NSE:TEST-EQ", feed_mode="simulation",
        execution_mode="paper", allow_live_orders=False, auto_trade=True,
        fast_period=2, slow_period=3, signal_period=2, kama_period=2, bb_period=2,
        kama_rsi_period=2, kama_roc_period=1,
        tick_capture_enabled=False, mp_enabled=False, blast_enabled=False,
        smart_lanes_enabled=False, macd_entry_filter=True, macd_entry_rank_seconds=0,
        database_path=str(tmp_path / "book.sqlite3"),
        mp_database_path=str(tmp_path / "mp.sqlite3"),
        blast_database_path=str(tmp_path / "blast.sqlite3"),
        research_database_path=str(tmp_path / "history.sqlite3"),
        tick_database_path=str(tmp_path / "ticks.sqlite3"),
        contract_snapshot_path=str(tmp_path / "contracts.json"),
        runtime_settings_path=str(tmp_path / "settings.json"),
    )
    result = TradingEngine(configured)
    result.contract_selector.contracts = {
        SYMBOL: OptionContract("TEST", "NSE:TEST-EQ", "CE", SYMBOL, 100, "2026-10-27", 100,
                               lot_size=1),
    }
    result.execution.set_tradable_contracts({SYMBOL: 1})
    for offset, price in enumerate((10, 9, 8, 7, 6), start=-5):
        stamp = int((START + timedelta(minutes=offset)).timestamp())
        result.strategy.on_closed_candle(Candle(SYMBOL, stamp, price, price, price, price), warmup=True)
    # Isolate revalidation from the statistical model's unrelated threshold.
    monkeypatch.setattr(entry_scorer, "probability", lambda features: 0.99)
    yield result
    result.repository.close()
    result.mp.repository.close()
    result.blast.repository.close()


async def _tick(engine, clock, price, seconds):
    clock.moment = START + timedelta(seconds=seconds)
    await engine.on_tick(Tick(SYMBOL, price, timestamp=clock.moment))


def test_new_bucket_commits_prior_close_before_previewing(engine, clock):
    async def scenario():
        await _tick(engine, clock, 20, 0)
        await _tick(engine, clock, 20, 60)

    asyncio.run(scenario())
    assert engine.strategy.indicators[SYMBOL].count == 6
    assert len(engine.strategy.signals) == 1
    assert len(engine.entry_gate._queue) == 1
    assert engine.strategy.signals[0].evaluated_candle_timestamp == int(START.timestamp())


def test_ranked_entry_that_fades_during_wait_does_not_submit(engine, clock):
    async def scenario():
        await _tick(engine, clock, 20, 0)
        assert len(engine.entry_gate._queue) == 1
        await _tick(engine, clock, 6, 15)
        await engine.entry_gate._drain(engine.execution, engine.portfolio, engine._queued_macd_entry_rejection)

    asyncio.run(scenario())
    assert engine.entry_gate.counts["MACD_FADED"] == 1
    assert not engine.portfolio.positions
    assert engine.repository.rows("orders") == []
    assert engine.repository.rows("trades") == []


def test_ranked_entry_that_still_stands_fills_in_paper(engine, clock):
    async def scenario():
        await _tick(engine, clock, 20, 0)
        await _tick(engine, clock, 21, 15)
        await engine.entry_gate._drain(engine.execution, engine.portfolio, engine._queued_macd_entry_rejection)

    asyncio.run(scenario())
    assert engine.entry_gate.counts["TAKEN"] == 1
    assert engine.portfolio.positions[SYMBOL].quantity == 1
    assert len(engine.repository.rows("orders")) == 1
    assert engine.execution.settings.allow_live_orders is False


def test_ranked_entry_requires_a_fresh_quote(engine, clock):
    async def scenario():
        await _tick(engine, clock, 20, 0)
        clock.moment = START + timedelta(seconds=15)
        await engine.entry_gate._drain(engine.execution, engine.portfolio, engine._queued_macd_entry_rejection)

    asyncio.run(scenario())
    assert engine.entry_gate.counts["STALE_PRICE"] == 1
    assert engine.repository.rows("orders") == []


@pytest.mark.parametrize("confirmation", ["kama", "rsi", "roc"])
def test_enabled_confirmation_must_survive_the_ranking_wait(engine, clock, confirmation):
    if confirmation == "kama":
        engine.settings.require_kama_confirmation = True
        kama = IncrementalKAMA(2, 20, 30)
        for price in (10, 9, 8, 7, 6):
            kama.update(price)
        engine.strategy.kama[SYMBOL] = kama
        faded_price = 8
    elif confirmation == "rsi":
        engine.settings.require_kama_rsi_confirmation = True
        engine.settings.kama_rsi_min = 65
        faded_price = 12
    else:
        engine.settings.require_kama_roc_confirmation = True
        engine.settings.kama_roc_min = 30
        faded_price = 12

    async def scenario():
        await _tick(engine, clock, 20, 0)
        assert len(engine.entry_gate._queue) == 1
        count = engine.strategy.indicators[SYMBOL].count
        kama_before = engine.strategy.kama[SYMBOL].value
        await _tick(engine, clock, faded_price, 15)
        assert engine.strategy.indicators[SYMBOL].preview(faded_price).macd > 0
        await engine.entry_gate._drain(engine.execution, engine.portfolio, engine._queued_macd_entry_rejection)
        assert engine.strategy.indicators[SYMBOL].count == count
        assert engine.strategy.kama[SYMBOL].value == kama_before

    asyncio.run(scenario())
    assert engine.entry_gate.counts["CONFIRMATION_FADED"] == 1
    assert engine.repository.rows("orders") == []


def test_cross_surviving_the_bar_boundary_can_still_enter(engine, clock):
    async def scenario():
        await _tick(engine, clock, 20, 0)
        await _tick(engine, clock, 21, 60)
        await engine.entry_gate._drain(engine.execution, engine.portfolio, engine._queued_macd_entry_rejection)

    asyncio.run(scenario())
    assert engine.entry_gate.counts["TAKEN"] == 1
    assert engine.portfolio.positions[SYMBOL].quantity == 1


def test_queue_does_not_enter_after_originating_bar_is_superseded(engine, clock):
    async def scenario():
        await _tick(engine, clock, 20, 0)
        await _tick(engine, clock, 21, 60)
        await _tick(engine, clock, 22, 120)
        await engine.entry_gate._drain(engine.execution, engine.portfolio, engine._queued_macd_entry_rejection)

    asyncio.run(scenario())
    assert engine.entry_gate.counts["BAR_STALE"] == 1
    assert engine.repository.rows("orders") == []


def test_gate_supports_async_validator_and_fails_closed_on_errors(engine, clock):
    async def reject(signal):
        raise RuntimeError("indicator state unavailable")

    async def scenario():
        await _tick(engine, clock, 20, 0)
        await engine.entry_gate._drain(engine.execution, engine.portfolio, reject)

    asyncio.run(scenario())
    assert engine.entry_gate.counts["VALIDATION_ERROR"] == 1
    assert "indicator state unavailable" in engine.entry_gate.error
    assert engine.repository.rows("orders") == []


def test_expired_macd_buy_is_blocked_before_order_persistence(engine):
    engine.execution.set_contract_expiries({SYMBOL: "2026-09-25"})
    engine.execution.set_quote(SYMBOL, 20)
    with pytest.raises(ValueError, match="Expired contract"):
        asyncio.run(engine.execution.submit(SYMBOL, "BUY", lots=1))
    assert engine.repository.rows("orders") == []
    assert not engine.portfolio.positions


@pytest.mark.parametrize("expiry", ["2026-09-28", "2026-10-27"])
def test_valid_and_same_day_expiry_buys_remain_allowed(engine, expiry):
    engine.execution.set_contract_expiries({SYMBOL: expiry})
    engine.execution.set_quote(SYMBOL, 20)
    order = asyncio.run(engine.execution.submit(SYMBOL, "BUY", lots=1))
    assert order.status == "FILLED"


def test_known_unusable_expiry_blocks_only_buy(engine):
    engine.execution.set_contract_expiries({SYMBOL: None})
    engine.execution.set_quote(SYMBOL, 20)
    with pytest.raises(ValueError, match="expiry is unavailable"):
        asyncio.run(engine.execution.submit(SYMBOL, "BUY", lots=1))
    assert engine.repository.rows("orders") == []


def test_expired_holding_remains_sellable_with_a_fresh_quote(engine, clock):
    engine.execution.set_contract_expiries({SYMBOL: "2026-09-28"})
    engine.execution.set_quote(SYMBOL, 20)
    asyncio.run(engine.execution.submit(SYMBOL, "BUY", lots=1))
    clock.moment = START + timedelta(days=1)
    engine.execution.set_quote(SYMBOL, 21)
    order = asyncio.run(engine.execution.submit(SYMBOL, "SELL", lots=1))
    assert order.status == "FILLED"
    assert not engine.portfolio.positions
    assert engine.execution.contract_expiries[SYMBOL] == "2026-09-28"


def test_blast_restored_metadata_blocks_expired_entry_without_changing_book(engine):
    blast = engine.blast
    blast.set_tradable_contracts({SYMBOL: 50})
    context = {"spot_symbol": "NSE:TEST-EQ", "option_type": "CE", "expiry": "2026-09-25"}
    blast.set_contract_context({SYMBOL: context})
    blast.execution.set_quote(SYMBOL, 20)
    signal = Signal(SYMBOL, "BUY", "BLAST", 20, 1, 0, 1)
    order = asyncio.run(blast._enter(signal))
    assert order is None
    assert "Expired contract" in blast.error
    assert blast.repository.rows("orders") == []
    assert not blast.portfolio.positions
    assert blast._contracts[SYMBOL]["expiry"] == "2026-09-25"


def test_blast_restart_preserves_held_position_and_its_expired_buy_guard(engine, clock):
    blast = engine.blast
    blast.set_tradable_contracts({SYMBOL: 50})
    blast.set_contract_context({SYMBOL: {"spot_symbol": "NSE:TEST-EQ", "option_type": "CE",
                                        "expiry": "2026-09-28"}})
    blast.execution.set_quote(SYMBOL, 20)
    asyncio.run(blast.manual_order(SYMBOL, "BUY", lots=1))
    asyncio.run(blast.stop())
    clock.moment = START + timedelta(days=1)
    restored = BlastEngine(engine.settings.blast_database_path, engine.settings, engine.broker, EventHub())
    try:
        restored.set_tradable_contracts({})
        restored.execution.set_quote(SYMBOL, 20)
        with pytest.raises(ValueError, match="Expired contract"):
            asyncio.run(restored.manual_order(SYMBOL, "BUY", lots=1))
        assert restored.portfolio.positions[SYMBOL].quantity == 50
        assert len(restored.repository.rows("orders")) == 1
        assert restored._contracts[SYMBOL]["expiry"] == "2026-09-28"
    finally:
        asyncio.run(restored.stop())


def test_score_journal_migrates_additively_and_records_static_model_identity(engine, clock):
    database = engine.entry_gate.database_path
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE macd_entry_scores (
          at TEXT NOT NULL, day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT, score REAL, threshold REAL,
          decision TEXT NOT NULL, signal_id TEXT, features TEXT)""")
        connection.execute("INSERT INTO macd_entry_scores VALUES (?,?,?,?,?,?,?,?,?)",
                           ("old", "2026-09-25", "OLD", "CE", 0.5, 0.3, "TAKEN", "old-signal", "{}"))

    async def scenario():
        await _tick(engine, clock, 20, 0)
        await _tick(engine, clock, 6, 15)
        await engine.entry_gate._drain(engine.execution, engine.portfolio, engine._queued_macd_entry_rejection)

    asyncio.run(scenario())
    engine.entry_gate._write(engine.entry_gate._journal)
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT symbol,decision,model_sha256 FROM macd_entry_scores ORDER BY rowid").fetchall()
    assert rows == [("OLD", "TAKEN", None), (SYMBOL, "MACD_FADED", entry_scorer.MODEL_SHA256)]
    status = engine.entry_gate.status()["model"]
    assert status["update_mode"] == "static_export"
    assert status["artifact_sha256"] == entry_scorer.MODEL_SHA256


def test_closed_and_intrabar_signals_use_the_same_presignal_dip_features(engine):
    bucket = int(START.timestamp())
    signal = Signal(SYMBOL, "BUY", "PREMIUM_MACD_ZERO_CROSS_UP", 20, 2.029, 1, 1.029,
                    evaluated_candle_timestamp=bucket)
    contract = engine.contract_selector.contracts[SYMBOL]
    intrabar = engine._entry_features(signal, contract)
    engine.strategy.point_history[SYMBOL].append(IndicatorPoint(SYMBOL, bucket, signal.macd, 1, 1.029))
    # Any later point is also unavailable at the original decision time.
    engine.strategy.point_history[SYMBOL].append(IndicatorPoint(SYMBOL, bucket + 60, 3, 2, 1))
    closed = engine._entry_features(signal, contract)
    for name in ("dip_bars", "dip_depth_pct", "macd_slope_pct", "bars_seen"):
        assert closed[name] == pytest.approx(intrabar[name])
    assert closed["dip_bars"] > 0
    assert closed["macd_slope_pct"] > 0
