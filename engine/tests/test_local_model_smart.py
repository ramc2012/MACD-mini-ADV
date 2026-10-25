import asyncio
from datetime import UTC,datetime,timedelta

import pytest

from test_smart_lane import build,SYMBOL
from macd_trader.smart_lane import Pending,BLAST_SOURCE,QWEN_REJECTED,QWEN_UNAVAILABLE,QWEN_STALE,TAKEN
from macd_trader.models import Signal
from macd_trader import local_model


def prepared(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCAL_DECISION_MODE','filter')
    monkeypatch.setenv('LOCAL_DECISION_URL','http://local-decisions:8435')
    lane=build(tmp_path);now=datetime.now(UTC)
    lane.execution.set_quote(SYMBOL,10,now)
    signal=Signal(SYMBOL,'BUY','fixture',10,1,1,1,timestamp=now)
    return lane,Pending(BLAST_SOURCE,SYMBOL,'CE',signal,now,now),now


def result(verdict='approve'):
    return {'verdict':verdict,'reason':'isolated fixture','expires_at':(datetime.now(UTC)+timedelta(seconds=30)).isoformat(),
            'reference_price':10,'model':'qwen3.5:9b'}


@pytest.mark.parametrize('verdict',['reject','wait'])
def test_qwen_veto_creates_no_order(tmp_path,monkeypatch,verdict):
    lane,item,now=prepared(tmp_path,monkeypatch)
    async def review(*args):return result(verdict)
    monkeypatch.setattr(local_model,'review_smart',review)
    asyncio.run(lane._decide(item,now,0))
    assert lane.counts[QWEN_REJECTED]==1 and not lane.portfolio.positions


def test_model_failure_cannot_fall_back_to_an_unreviewed_entry(tmp_path,monkeypatch):
    lane,item,now=prepared(tmp_path,monkeypatch)
    async def review(*args):raise TimeoutError()
    monkeypatch.setattr(local_model,'review_smart',review)
    asyncio.run(lane._decide(item,now,0))
    assert lane.counts[QWEN_UNAVAILABLE]==1 and not lane.portfolio.positions


def test_approval_cannot_bypass_price_drift_guard(tmp_path,monkeypatch):
    lane,item,now=prepared(tmp_path,monkeypatch)
    async def review(*args):
        lane.execution.set_quote(SYMBOL,11,datetime.now(UTC));return result()
    monkeypatch.setattr(local_model,'review_smart',review)
    asyncio.run(lane._decide(item,now,0))
    assert lane.counts[QWEN_STALE]==1 and not lane.portfolio.positions


def test_approval_rechecks_cash_and_fills_only_through_existing_paper_manager(tmp_path,monkeypatch):
    lane,item,now=prepared(tmp_path,monkeypatch)
    async def review(*args):return result()
    monkeypatch.setattr(local_model,'review_smart',review)
    asyncio.run(lane._decide(item,now,0))
    assert lane.counts[TAKEN]==1 and lane.portfolio.positions[SYMBOL].quantity>0


def test_existing_price_gate_runs_before_any_model_call(tmp_path,monkeypatch):
    lane,item,now=prepared(tmp_path,monkeypatch)
    lane.execution.last_prices.clear()
    async def review(*args):pytest.fail('Invalid quotes must not reach model')
    monkeypatch.setattr(local_model,'review_smart',review)
    asyncio.run(lane._decide(item,now,0))
    assert lane.counts['NO_PRICE']==1


def test_cash_is_rechecked_after_inference(tmp_path,monkeypatch):
    lane,item,now=prepared(tmp_path,monkeypatch)
    async def review(*args):
        lane.portfolio.cash=0
        return result()
    monkeypatch.setattr(local_model,'review_smart',review)
    asyncio.run(lane._decide(item,now,0))
    assert lane.counts['NO_CASH']==1 and not lane.portfolio.positions
