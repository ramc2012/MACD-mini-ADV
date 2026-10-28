"""Allocation/admission contracts in isolated books, with no broker order API."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest
from test_smart_lane import build,signal,SYMBOL,T0
from macd_trader import smart_intelligence as brain
from macd_trader.dispersion import DispersionPoint
from macd_trader.engine import TradingEngine


def test_priority_uses_side_breadth_fresh_alignment_and_exposure_not_flow_direction():
    breadth=DispersionPoint(0,80,20,100,100,200)
    good=brain.evaluate(research_percentile=.5,option_type='CE',breadth=breadth,
        live={'macd_pct':.01,'u_vwap':.01,'u_ret_open':.01,'nifty_ret_prev':.01})
    weak=brain.evaluate(research_percentile=.5,option_type='PE',breadth=breadth,
        live={'macd_pct':.001,'u_vwap':-.01,'u_ret_open':-.01,'nifty_ret_prev':-.01},exposure_fraction=.2)
    assert 0<=weak['priority']<good['priority']<=1 and not good['qwen_required']
    assert 'diagnostic_only' in good['flow_use']
    missing=brain.evaluate(research_percentile=float('nan'),option_type='PE',live={'macd_pct':float('nan')})
    assert 0<=missing['priority']<=1 and missing['components']['premium_momentum']==.5


def test_missing_quote_waits_for_shared_tick_then_fills_once_without_refreshing_deadline(tmp_path):
    lane=build(tmp_path);lane.on_macd_signal(signal(),'CE',{},now=T0)
    asyncio.run(lane.step(T0+timedelta(seconds=320)))
    assert lane.counts=={'WAITING_PRICE':1} and len(lane._pending)==1
    due=lane._pending[0].due
    asyncio.run(lane.step(T0+timedelta(seconds=326)))
    assert lane.counts=={'WAITING_PRICE':1} and lane._pending[0].due==due
    lane.execution.set_quote(SYMBOL,10,datetime.now(UTC))
    asyncio.run(lane.step(T0+timedelta(seconds=332)))
    asyncio.run(lane.step(T0+timedelta(seconds=334)))
    assert lane.counts['TAKEN']==1 and not lane._pending
    assert len(lane.repository.rows('trades'))==1


def test_missing_quote_expires_and_does_not_replay_a_missed_entry(tmp_path):
    lane=build(tmp_path);lane.on_macd_signal(signal(),'CE',{},now=T0)
    asyncio.run(lane.step(T0+timedelta(seconds=320)))
    asyncio.run(lane.step(T0+timedelta(seconds=901)))
    assert lane.counts=={'WAITING_PRICE':1,'NO_PRICE':1} and not lane._pending
    assert not lane.repository.rows('orders')


def test_local_priority_allocates_last_affordable_ticket_best_first(tmp_path,monkeypatch):
    lane=build(tmp_path,smart_ticket_fraction=1,smart_rank_seconds=0,smart_entry_delay_seconds=0)
    other='NSE:OTHER26NOV800CE'
    lane.set_tradable_contracts({SYMBOL:100,other:100})
    lane.set_contract_context({**lane._contracts,other:{'spot_symbol':'NSE:OTHER-EQ','expiry':'2099-12-30','volume':0}})
    lane.portfolio.cash=1000
    monkeypatch.setattr('macd_trader.smart_lane.score',lambda f:.5)
    lane.set_resolvers(entry_context=lambda p,n:{'live':{'macd_pct':-.01 if p.symbol==SYMBOL else .02}})
    low=signal();high=signal();high.symbol=other
    lane.on_macd_signal(low,'CE',{'fixture':1},now=T0)
    lane.on_macd_signal(high,'CE',{'fixture':1},now=T0)
    for symbol in (SYMBOL,other):lane.execution.set_quote(symbol,10,datetime.now(UTC))
    asyncio.run(lane.step(T0))
    assert set(lane.portfolio.positions)=={other} and lane.portfolio.cash==0
    lane.flush_journal();taken=next(r for r in lane.journal_rows() if r['decision']=='TAKEN')
    assert taken['detail']['allocation']['policy']==brain.VERSION


def test_duplicate_signal_and_same_bar_are_not_queued_or_scored_twice(tmp_path):
    lane=build(tmp_path);s=signal()
    lane.on_macd_signal(s,'CE',{'fixture':1},now=T0)
    lane.on_macd_signal(s,'CE',{'fixture':1},now=T0)
    lane.on_macd_signal(signal(),'CE',{'fixture':1},now=T0)
    assert len(lane._pending)==len(lane._scores)==1


def test_smart_stays_paper_even_if_global_mode_changes(tmp_path):
    lane=build(tmp_path,execution_mode='live',allow_live_orders=True)
    assert lane.execution.settings.execution_mode=='paper' and not lane.execution.settings.allow_live_orders


def test_partial_profit_stages_survive_restart_without_reselling_a_completed_stage(tmp_path,monkeypatch):
    from test_smart_lane import NoOrders
    from macd_trader.smart_lane import SmartLane
    from macd_trader.events import EventHub
    lane=build(tmp_path,smart_rank_seconds=0,smart_entry_delay_seconds=0)
    monkeypatch.setattr('macd_trader.smart_lane.score',lambda f:.99)
    lane.execution.set_quote(SYMBOL,10,datetime.now(UTC))
    lane.on_macd_signal(signal(),'CE',{'fixture':1},now=T0)
    asyncio.run(lane.step(T0))
    asyncio.run(lane.execution.on_tick(SYMBOL,13.1,datetime.now(UTC)))
    asyncio.run(lane.execution.on_tick(SYMBOL,15.1,datetime.now(UTC)))
    pos=lane.portfolio.positions[SYMBOL]
    assert pos.exit_stage==2 and pos.quantity==800
    lane.repository.save_position_excursions(lane.portfolio.positions.values())
    quantity=pos.quantity
    restored=SmartLane(lane.name,lane.capital,lane.settings,NoOrders(),EventHub(),lane.database_path)
    assert restored.portfolio.positions[SYMBOL].exit_stage==2
    assert restored.portfolio.positions[SYMBOL].entry_stage==10
    assert restored.portfolio.positions[SYMBOL].entry_anchor==10
    assert restored.portfolio.positions[SYMBOL].quantity==quantity
    restored.execution.set_quote(SYMBOL,14,datetime.now(UTC))
    before=len(restored.repository.rows('trades'))
    asyncio.run(restored.execution.on_tick(SYMBOL,14,datetime.now(UTC)))
    assert len(restored.repository.rows('trades'))==before # stage 3 starts at +75%, not +30%


def test_ticket_and_cash_budget_include_shared_ask(tmp_path,monkeypatch):
    from macd_trader.models import Tick
    lane=build(tmp_path,smart_ticket_fraction=1,smart_rank_seconds=0,smart_entry_delay_seconds=0)
    lane.portfolio.cash=2000
    monkeypatch.setattr('macd_trader.smart_lane.score',lambda f:.99)
    lane.execution.quote_source=lambda s:Tick(s,10,bid=9.9,ask=10.1)
    lane.execution.set_quote(SYMBOL,10,datetime.now(UTC))
    lane.on_macd_signal(signal(),'CE',{'fixture':1},now=T0)
    asyncio.run(lane.step(T0))
    assert lane.counts=={'TAKEN':1} and lane.portfolio.positions[SYMBOL].lots==1
    assert lane.portfolio.cash==pytest.approx(990)


def test_no_smart_admission_outside_regular_session(tmp_path):
    lane=build(tmp_path,smart_entry_delay_seconds=0,smart_rank_seconds=0)
    closed=T0.replace(hour=2)
    lane.on_macd_signal(signal(),'CE',{},now=closed)
    lane.execution.set_quote(SYMBOL,10,datetime.now(UTC))
    asyncio.run(lane.step(closed))
    assert lane.counts=={'SESSION_CLOSED':1} and not lane.repository.rows('orders')


def test_live_context_uses_current_quotes_without_recounting_signals_or_reading_stale_spot():
    from macd_trader.models import Tick
    now=datetime.now(UTC);nifty='NSE:NIFTY50-INDEX';spot='NSE:TEST-EQ'
    engine=SimpleNamespace(settings=SimpleNamespace(order_quote_max_age_seconds=10),
        latest_ticks={SYMBOL:Tick(SYMBOL,10,timestamp=now),spot:Tick(spot,90,timestamp=now,avg_trade_price=100),
            nifty:Tick(nifty,99,timestamp=now,prev_close=100)},
        strategy=SimpleNamespace(indicators={SYMBOL:SimpleNamespace(preview=lambda p:SimpleNamespace(macd=.1))}),
        contract_selector=SimpleNamespace(contracts={SYMBOL:SimpleNamespace(option_type='PE',spot_symbol=spot)}),
        entry_gate=SimpleNamespace(days=SimpleNamespace(get=lambda s:(100,None,None))),
        entry_flow=SimpleNamespace(context=lambda *a,**kw:{'source':'desk','flow_scope':'session','session_cvd_share_signed':-.5}))
    out=TradingEngine._smart_entry_context(engine,SimpleNamespace(symbol=SYMBOL),now)
    assert out['live']['u_vwap']==pytest.approx(.1) and out['live']['macd_pct']==.01
    assert out['flow']['source']=='desk'
    engine.latest_ticks[spot].timestamp=now-timedelta(seconds=11)
    assert TradingEngine._smart_entry_context(engine,SimpleNamespace(symbol=SYMBOL),now)['live']['u_vwap'] is None
