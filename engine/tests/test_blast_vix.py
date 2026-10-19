import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from macd_trader.blast_engine import vix_entry_policy, premium_size_multiplier
from macd_trader.config import Settings
from macd_trader.models import Tick
from test_blast_lane import build, prime, cross, bar, point, SYMBOL, SPOT


def quote(value, age=0):
    return Tick("NSE:INDIAVIX-INDEX", value, timestamp=datetime.now(UTC)-timedelta(seconds=age))


@pytest.mark.parametrize("vix,normal,hard,breadth", [(10,.6,1.2,.55),(20,1.2,2.4,.5),(40,2.4,4.8,.4),(100,2.4,4.8,.4)])
def test_higher_vix_widens_premium_and_lowers_breadth(vix, normal, hard, breadth):
    p = vix_entry_policy(Settings(), quote(vix))
    assert p["active"]
    assert p["normal_premium_limit_pct"] == pytest.approx(normal)
    assert p["hard_premium_limit_pct"] == pytest.approx(hard)
    assert p["min_side_breadth"] == pytest.approx(breadth)


# Quotes are built inside the test: built at collection time, the "10 s in the future" quote had aged
# past the -5 s guard by the time a long suite reached it.
@pytest.mark.parametrize("args,status", [(None,"missing"),((30,901),"stale"),((30,-10),"stale"),((float('nan'),),"invalid"),((0,),"invalid")])
def test_unusable_vix_keeps_fixed_rules_and_no_rich_extension(args, status):
    p = vix_entry_policy(Settings(), None if args is None else quote(*args))
    assert p["vix_status"] == status and not p["active"]
    assert p["normal_premium_limit_pct"] == p["hard_premium_limit_pct"] == 1.2
    assert p["min_side_breadth"] == .5


def test_policy_can_be_disabled_and_richness_reduces_ticket():
    p = vix_entry_policy(Settings(blast_vix_adaptive_enabled=False), quote(40))
    assert p["vix_status"] == "disabled" and p["hard_premium_limit_pct"] == 1.2
    p = vix_entry_policy(Settings(), quote(20))
    assert premium_size_multiplier(1.2, p) == 1
    assert premium_size_multiplier(1.8, p) == pytest.approx(4/9)
    assert premium_size_multiplier(2.4, p) == .25


def adaptive_lane(tmp_path, *, vix=40, breadth=.45, **settings):
    lane = build(tmp_path, **settings)
    lane.set_contract_context({SYMBOL:{"spot_symbol":SPOT,"option_type":"CE","expiry":"2099-12-30"}})
    lane.set_resolvers(spot_price=lambda _:800., breadth=lambda _:breadth, vix_quote=lambda:quote(vix))
    prime(lane, high=60)
    return lane


def test_rich_premium_enters_with_whole_lots_under_reduced_ticket(tmp_path):
    lane=adaptive_lane(tmp_path)
    offered=[];lane.candidate_listeners.append(offered.append)
    row=cross(lane,24.)
    assert row["reason"] == "TAKEN"
    assert row["entry_size_multiplier"] == pytest.approx(.64)
    assert row["entry_target_notional"] == pytest.approx(64000)
    assert lane.portfolio.positions[SYMBOL].lots == 26
    assert offered == []  # Blast widening does not enlarge the Smart population.
    assert lane.journal_rows()[0]["entry_policy"]["vix"] == 40
    asyncio.run(lane.stop())


def test_double_normal_premium_is_quarter_ticket_and_beyond_band_is_rejected(tmp_path):
    lane=adaptive_lane(tmp_path/'a')
    row=cross(lane,38.4)
    assert row["reason"] == "TAKEN" and row["entry_size_multiplier"] == pytest.approx(.25)
    assert lane.portfolio.positions[SYMBOL].lots == 6
    asyncio.run(lane.stop())
    lane=adaptive_lane(tmp_path/'b')
    assert cross(lane,39.)["reason"] == "PREMIUM_TOO_RICH"
    assert not lane.portfolio.positions
    asyncio.run(lane.stop())


def test_liquidity_cap_and_small_ticket_are_still_binding(tmp_path):
    lane=adaptive_lane(tmp_path/'a')
    lane.set_contract_context({SYMBOL:{"spot_symbol":SPOT,"option_type":"CE","expiry":"2099-12-30","volume":5000}})
    assert cross(lane,24.)["reason"] == "TAKEN"
    assert lane.portfolio.positions[SYMBOL].lots == 1
    asyncio.run(lane.stop())
    lane=adaptive_lane(tmp_path/'b',blast_target_notional=1000)
    row=cross(lane,24.)
    assert row["reason"] == "ENTRY_REJECTED" and "whole lot" in row["entry_rejection"]
    assert not lane.portfolio.positions
    asyncio.run(lane.stop())


def test_current_execution_price_is_rechecked_and_cannot_increase_ticket(tmp_path):
    lane=adaptive_lane(tmp_path/'a')
    lane.execution.set_quote(SYMBOL,39.)
    row=asyncio.run(lane._evaluate(bar(24.),point(-.4,-.5),60.,12))
    assert row["reason"] == "ENTRY_REJECTED" and "hard limit" in row["entry_rejection"]
    assert lane.repository.rows("orders") == []
    asyncio.run(lane.stop())
    lane=adaptive_lane(tmp_path/'b')
    lane.execution.set_quote(SYMBOL,30.)
    row=asyncio.run(lane._evaluate(bar(24.),point(-.4,-.5),60.,12))
    assert row["reason"] == "TAKEN"
    assert row["entry_size_multiplier"] == pytest.approx(.4096)
    assert lane.portfolio.positions[SYMBOL].lots == 13
    asyncio.run(lane.stop())


def test_original_smart_screen_remains_independent_of_blast_vix_rules(tmp_path):
    lane=adaptive_lane(tmp_path,vix=10,breadth=.52)
    offered=[];lane.candidate_listeners.append(offered.append)
    row=cross(lane,6.)
    assert row["reason"] == "BREADTH_TOO_THIN"
    assert row["smart_screen_passed"] and len(offered)==1
    asyncio.run(lane.stop())


def test_vix_without_a_breadth_cohort_does_not_allow_entries(tmp_path):
    lane=adaptive_lane(tmp_path,breadth=None)
    assert cross(lane,24.)["reason"] == "NO_BREADTH"
    asyncio.run(lane.stop())


def test_shared_vix_subscription_updates_policy_without_becoming_tradable(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from macd_trader.engine import TradingEngine
    from macd_trader import option_agent
    from test_engine_split import _settings

    engine = TradingEngine(_settings(tmp_path, 'strategy', smart_lanes_enabled=False,
                                     contract_snapshot_path=str(tmp_path/'contracts.json'),
                                     blast_vix_adaptive_enabled=False))
    engine.broker.connect = AsyncMock()
    engine.contract_selector.restore_held_contracts = AsyncMock(return_value=[])
    engine.contract_selector.build = AsyncMock(return_value=[])
    engine.entry_gate.refresh_reference = AsyncMock()
    engine._resolve_futures_series = AsyncMock()
    engine._refresh_position_marks = AsyncMock()
    engine.blast.warm_from_store = AsyncMock(return_value=0)
    engine.warmup_symbols = lambda: []
    monkeypatch.setattr(option_agent, 'watch_symbols', lambda _: [])
    delivered = asyncio.Event()
    subscribed = []

    async def stream(symbols, on_tick):
        subscribed.extend(symbols)
        await on_tick(quote(40))
        delivered.set()
        await asyncio.Event().wait()

    engine.broker.stream = stream

    async def run():
        try:
            await engine.connect_feed()
            assert engine.status == 'connected', engine.error
            await asyncio.wait_for(delivered.wait(), 2)
            assert engine.settings.vix_symbol in subscribed
            assert engine.settings.vix_symbol not in engine.execution.tradable_symbols
            assert engine.settings.vix_symbol not in engine.blast.execution.tradable_symbols
            assert not engine.blast.entry_policy()['active']
            # Enabling adaptation reuses the existing reference subscription.
            engine.blast.apply_settings(engine.settings.model_copy(update={'blast_vix_adaptive_enabled':True}))
            assert engine.blast.entry_policy()['vix'] == 40
            assert engine.blast.entry_policy()['min_side_breadth'] == pytest.approx(.4)
            assert engine.repository.rows('trades') == []
            assert engine.blast.repository.rows('trades') == []
        finally:
            if engine._stream_task:
                engine._stream_task.cancel()
                try:
                    await engine._stream_task
                except asyncio.CancelledError:
                    pass
            await engine.blast.stop()
            engine.repository.close()

    asyncio.run(run())
