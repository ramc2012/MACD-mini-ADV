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


def test_live_input_preserves_training_signal_fields_with_bounded_facts(tmp_path,monkeypatch):
    import httpx,json
    lane,item,now=prepared(tmp_path,monkeypatch)
    item.features={f'feature_{i}':i for i in range(40)}
    seen=[]
    def handler(request):
        p=json.loads(request.content);seen.append(p)
        return httpx.Response(200,json={'reviews':[{'id':p['candidates'][0]['id'],'verdict':'wait'}],
            'expires_at':(datetime.now(UTC)+timedelta(seconds=30)).isoformat()})
    client=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    asyncio.run(local_model.review_smart(item,10,now))
    f=seen[0]['candidates'][0]['facts']
    assert f['feature_39']==39 and f['signal_macd']==1 and f['signal_histogram']==1 and f['signal_price']==10
    assert f['phase']=='smart_entry' and f['feature_snapshot_at']==item.created.isoformat()
    assert f['signal_basis']=='option_premium' and f['signal_action']=='BUY'
    assert 'not a BUY/SELL command' in f['signal_signal_definition']


def test_smart_default_only_accepts_macd_stream_and_macd_filter_default_is_preserved(tmp_path):
    from macd_trader.config import Settings
    lane=build(tmp_path)
    assert Settings().macd_entry_filter is True
    lane.on_blast_candidate({'symbol':SYMBOL},now=datetime.now(UTC))
    assert not lane._pending


@pytest.mark.parametrize('metadata_ok',[True,False])
def test_smart_context_copy_cannot_change_or_block_macd_routing(metadata_ok):
    from types import SimpleNamespace
    from macd_trader.engine import TradingEngine
    received=[];intake=[];features={'native_signal_feature':1}
    async def on_signal(value):received.append(value)
    smart=SimpleNamespace(on_macd_signal=lambda *args:intake.append(args))
    contract=SimpleNamespace(option_type='CE',spot_symbol='NSE:FIXTURE-EQ',strike=100 if metadata_ok else None,expiry='2026-11-24')
    signal=Signal(SYMBOL,'BUY','fixture',10,1,1,1)
    engine=SimpleNamespace(entry_gate=SimpleNamespace(enabled=False),settings=SimpleNamespace(),
        contract_selector=SimpleNamespace(contracts={SYMBOL:contract}),
        execution=SimpleNamespace(tradable_symbols={SYMBOL},on_signal=on_signal),
        smart_lanes=SimpleNamespace(lanes=[smart]),_entry_features=lambda *_:features)
    asyncio.run(TradingEngine._route_signal(engine,signal))
    assert received==[signal] and features=={'native_signal_feature':1}
    assert len(intake)==1
    if metadata_ok:assert intake[0][2]['underlying']=='NSE:FIXTURE-EQ' and intake[0][2] is not features
    else:assert intake[0][2] is None
