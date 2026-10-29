from datetime import datetime,timedelta
import json
import pytest
from paper_lanes.daily_swing import signal,exit_rule,VERSION
from paper_lanes.manager import Manager
from paper_lanes.tests.test_books import book,enqueue,approved,cycle,quote,OPEN,DEC,ACT,IST


def bars(n=260):
    return [dict(open=100+i,high=100+i,low=99+i,close=100+i) for i in range(n)]


def test_daily_breakout_requires_long_term_history_and_relative_strength():
    data=bars()
    assert not signal(data[:249],[100.]*249)['eligible']
    r=signal(data,[100.]*len(data))
    assert r['eligible'] and r['ema20']>r['ema50']>r['ema200'] and r['atr20']>0
    assert r['horizon_sessions'] is None
    data[-2]['high']=1000
    assert not signal(data,[100.]*len(data))['eligible']


def test_discontinuity_is_not_a_breakout():
    data=bars();data[-1]=dict(open=600,high=600,low=600,close=600)
    assert 'corporate-action' in signal(data,[100.]*len(data))['reason']


def test_trail_never_falls_and_trend_exits_use_two_closes():
    row=dict(latest_close=120,previous_close=119,ema50=115,previous_ema50=114,ema200=100,atr20=2)
    assert exit_rule(row,110)==(114,None)
    assert exit_rule({**row,'atr20':20},114)==(114,None)
    assert exit_rule({**row,'latest_close':113},110)[1] is None
    assert exit_rule({**row,'latest_close':113,'previous_close':112},110)[1]=='DAILY_EMA50_EXIT'
    assert exit_rule({**row,'ema50':99},110)[1]=='DAILY_TREND_BREAK'
    assert exit_rule({**row,'latest_close':109},110)[1]=='DAILY_ATR_EXIT'
    assert exit_rule({'latest_close':120},110)==(110,None)


def daily_book(tmp_path):
    b=book(tmp_path,'cbe');b.enable_daily_swing(ACT)
    enqueue(b)
    with b.transaction() as db:
        o=db.execute('SELECT * FROM orders').fetchone()
        payload={'rank':1,'strategy_version':VERSION,'holding_mode':'daily_trend','atr20':2,'source_mark':100,'ema50':90}
        db.execute('UPDATE orders SET payload=?,symbol=?',(json.dumps(payload),'TEST'))
    qs={'NSE_EQ|TEST':quote()};approved(b,qs);cycle(b,qs)
    return b


def test_no_target_time_exit_or_holding_limit_even_after_months(tmp_path):
    b=daily_book(tmp_path)
    for days in (0,1,7,30,120):
        now=OPEN+timedelta(days=days);now=now.replace(hour=15,minute=20)
        cycle(b,{'NSE_EQ|TEST':quote(now,bid=120,ask=120.1)},now)
        assert b.snapshot(now)['open_count']==1
    s=b.snapshot(now)
    assert s['positions'][0]['exit_due'] is None and s['positions'][0]['target']==0
    assert s['policy']['max_hold_sessions'] is None


def test_daily_exit_is_idempotent_and_fills_only_next_session_fresh_quote(tmp_path):
    b=daily_book(tmp_path);eod=OPEN.replace(hour=16)
    row=dict(symbol='TEST',price_session=str(eod.date()),aligned_daily_observations=260,
             latest_close=94,previous_close=99,ema50=98,previous_ema50=100,ema200=80,atr20=2)
    view=dict(source_session=str(eod.date()),completed_session=str(eod.date()),results=[row])
    after=(eod+timedelta(days=1)).replace(hour=9,minute=15)
    b.apply_daily(view,eod,after);b.apply_daily(view,eod+timedelta(seconds=1),after)
    cycle(b,{'NSE_EQ|TEST':quote(eod)},eod)
    assert b.snapshot(eod)['open_count']==1
    cycle(b,{'NSE_EQ|TEST':quote(eod)},after) # stale prior-session quote cannot fill
    assert b.snapshot(after)['open_count']==1
    cycle(b,{'NSE_EQ|TEST':quote(after)},after)
    s=b.snapshot(after)
    assert s['open_count']==0 and s['closed'][0]['exit_reason']=='DAILY_EMA50_EXIT'
    assert sum(e['kind']=='DAILY_REVIEW' for e in s['events'])==1
    assert len([f for f in s['fills'] if f['side']=='SELL'])==1 and s['unsettled_cash']>0


def test_emergency_stop_can_protect_entry_day(tmp_path):
    b=daily_book(tmp_path);later=OPEN+timedelta(minutes=10)
    cycle(b,{'NSE_EQ|TEST':quote(later,bid=85,ask=85.1)},later)
    assert b.snapshot(later)['closed'][0]['exit_reason']=='STOP'


def test_daily_instruction_survives_restart(tmp_path):
    b=daily_book(tmp_path)
    restarted=type(b)(b.path,'cbe',1000000,OPEN)
    assert restarted.snapshot(OPEN)['positions'][0]['daily_management']['trail']>0
    later=OPEN+timedelta(days=90)
    cycle(restarted,{'NSE_EQ|TEST':quote(later)},later)
    assert restarted.snapshot(later)['open_count']==1


def test_migration_supersedes_pending_but_does_not_rewrite_history_or_cash(tmp_path):
    b=book(tmp_path,'cbe');enqueue(b)
    before=b.snapshot(ACT)
    b.enable_daily_swing(ACT);b.enable_daily_swing(ACT)
    after=b.snapshot(ACT)
    assert after['cash']==before['cash'] and after['account_id']==before['account_id']
    assert after['orders'][0]['state']=='SUPERSEDED'
    assert sum(e['kind']=='DAILY_STRATEGY_ACTIVATED' for e in after['events'])==1


def test_migration_refuses_to_rewrite_open_legacy_terms(tmp_path):
    b=book(tmp_path,'cbe');enqueue(b);qs={'NSE_EQ|TEST':quote()};approved(b,qs);cycle(b,qs)
    before=b.snapshot(OPEN)
    with pytest.raises(ValueError):b.enable_daily_swing(OPEN)
    assert b.snapshot(OPEN)['positions']==before['positions']


def test_daily_policy_rejects_old_publications_and_keeps_vanguard_terms(tmp_path,monkeypatch):
    monkeypatch.setenv('CBE_DAILY_SWING_ENABLED','true')
    m=Manager(tmp_path,capital=1000000,now=ACT)
    p=dict(lane='cbe',id='old',decision_at=DEC.isoformat(),source_session=str(DEC.date()),
           paper_account_id=m.books['cbe'].account_id,watchlist=[dict(symbol='TEST',instrument='TEST')])
    meta={'TEST':dict(instrument='NSE_EQ|TEST',symbol='TEST',lot_size=1,tick=.05)}
    assert m.offer(p,meta)==0
    p['id']='new';p['watchlist'][0].update(strategy_version=VERSION,holding_mode='daily_trend',atr20=2)
    assert m.offer(p,meta)==1
    assert m.snapshot('cbe')['orders'][0]['exit_due'] is None
    assert m.books['vanguard'].terms.get('holding_mode') is None
