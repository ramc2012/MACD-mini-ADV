import copy
from datetime import datetime,timedelta
import pytest
from paper_lanes import local_intelligence as model
from paper_lanes.book import fresh
from paper_lanes.manager import Manager
from test_books import book,enqueue,quote,OPEN,ACT,cycle


def contexts():
    return {'daily.status':'completed_session_context','daily.source_session':'2026-10-26','daily.ema20':90.,
        'daily.ema50':80.,'daily.momentum_fast':.01,'daily.momentum_slow':.04,'daily.relative_momentum':.02,
        'daily.latest_close':100.,'profile.status':'prior_session','profile.event_date':'2026-10-26','profile.poc':95.,'profile.vah':102.,'profile.val':92.,'sector.status':'observed',
        'sector.rs_z20':1.,'regime.status':'observed','regime.age_hours':.1,'timing.status':'observed','timing.age_hours':.1,'timing.timing_score':70.}


def candidate(**changes):
    return {'id':'a','instrument':'NSE_EQ|TEST','side':'LONG','reference_price':100.,
        'source_at':OPEN.isoformat(),'facts':{'source_session':'2026-10-26','rank':1,'signal_close':100.,
            'current_underlying_bid':100.,'current_underlying_ask':100.2,'underlying_signal_close':100.},**changes}


def test_local_models_support_both_lanes_and_preserve_side_and_provenance():
    for lane,c in [('cbe',candidate()),('vanguard',candidate(instrument='NSE_FO|TEST',side='PE'))]:
        ctx=contexts();before=copy.deepcopy(ctx);r=model.review(lane,[c],{'a':ctx},OPEN)
        assert r['reviews'][0]['verdict']=='approve' and 0<=r['reviews'][0]['priority']<=1
        assert ctx==before and r['decision_engine']=='local'
        assert model.status(lane)['qwen_required'] is False
    with pytest.raises(ValueError):model.review('cbe',[candidate()],{},OPEN)


def test_core_missing_or_conflicting_context_does_not_approve_and_optional_stale_is_neutral():
    ctx=contexts();ctx['daily.ema20']=None
    assert model.assess('cbe',candidate(),ctx)['verdict']=='wait'
    ctx=contexts();ctx['daily.momentum_slow']=-.01
    assert model.assess('cbe',candidate(),ctx)['verdict']=='reject'
    ctx=contexts();ctx.update({'sector.status':'stale','sector.rs_z20':1000})
    out=model.assess('cbe',candidate(),ctx)
    assert out['verdict']=='approve' and out['components']['sector']==.5 and out['warnings']
    ctx['risk.fno_banned']=True
    assert model.assess('vanguard',candidate(side='CE'),ctx)['verdict']=='reject'
    assert model.assess('cbe',candidate(),ctx)['verdict']=='approve' # derivatives ban does not ban cash stocks
    ctx=contexts();ctx['timing.age_hours']=4
    assert model.assess('vanguard',candidate(side='CE'),ctx)['verdict']=='wait'
    ctx=contexts();ctx['profile.event_date']='2026-10-23'
    assert model.assess('cbe',candidate(),ctx)['verdict']=='wait'


def test_wait_does_not_exhaust_a_local_context_budget_and_never_fills(tmp_path):
    b=book(tmp_path,'cbe');enqueue(b);qs={'NSE_EQ|TEST':quote()}
    for i in range(4):
        now=OPEN+timedelta(seconds=61*i);qs['NSE_EQ|TEST']=quote(now)
        orders=b.review_candidates(qs,now,local=True);assert orders
        c=candidate();c['id']=orders[0]['id'];ctx=contexts();ctx['daily.ema20']=None
        b.apply_reviews(orders,model.review('cbe',[c],{c['id']:ctx},now),now)
        cycle(b,qs,now)
    assert not b.snapshot(now)['fills'] and b.snapshot(now)['orders'][0]['attempts']==0


def test_approved_local_model_still_cannot_bypass_quotes_cash_or_paper_accounting(tmp_path):
    b=book(tmp_path,'cbe');enqueue(b);qs={'NSE_EQ|TEST':quote()}
    orders=b.review_candidates(qs,OPEN,local=True);c=candidate();c['id']=orders[0]['id']
    b.apply_reviews(orders,model.review('cbe',[c],{c['id']:contexts()},OPEN),OPEN)
    cycle(b,qs,OPEN+timedelta(seconds=31))
    assert not b.snapshot(OPEN)['fills'] # expired approval and stale quote cannot fill


def test_actual_fill_orders_local_priorities_before_source_rank_with_limited_cash(tmp_path):
    b=book(tmp_path,'cbe');enqueue(b,instrument='NSE_EQ|LOW',cohort='low',rank=1,sector='LOW')
    enqueue(b,instrument='NSE_EQ|HIGH',cohort='high',rank=2,sector='HIGH')
    qs={k:quote(ask=100,bid=100) for k in ('NSE_EQ|LOW','NSE_EQ|HIGH')}
    orders=b.review_candidates(qs,OPEN,local=True);cs=[];ctx={}
    for o in orders:
        c=candidate();c['id']=o['id'];c['facts']['rank']=1 if o['instrument'].endswith('HIGH') else 10
        cs.append(c);ctx[c['id']]=contexts()
    b.apply_reviews(orders,model.review('cbe',cs,ctx,OPEN),OPEN)
    with b.transaction() as db:db.execute('update account set cash_paise=1200000')
    cycle(b,qs,OPEN)
    fills=b.snapshot(OPEN)['fills']
    assert fills and fills[0]['order_id']==next(o['id'] for o in orders if o['instrument'].endswith('HIGH'))
    assert b.snapshot(OPEN)['cash']>=0


def test_manager_uses_local_context_without_model_service_and_still_fills_only_through_book(tmp_path,monkeypatch):
    from paper_lanes import manager as module
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return OPEN.astimezone(tz) if tz else OPEN.replace(tzinfo=None)
    monkeypatch.setattr(module,'datetime',Clock)
    monkeypatch.setattr(module.httpx,'post',lambda *a,**kw:pytest.fail('No external model-service call'))
    mgr=Manager(folder=tmp_path,capital=1000000,now=ACT)
    b=mgr.books['cbe'];enqueue(b)
    mgr.last_quotes={'NSE_EQ|TEST':quote()}
    monkeypatch.setattr(mgr,'contexts',lambda cs,n:{c['id']:contexts() for c in cs})
    mgr.review_once()
    assert b.snapshot(OPEN)['orders'][0]['state']=='APPROVED'
    assert mgr.snapshot('cbe')['qwen_required'] is False
    cycle(b,mgr.last_quotes,OPEN)
    assert len(b.snapshot(OPEN)['fills'])==1 and b.snapshot(OPEN)['cash']>=0
