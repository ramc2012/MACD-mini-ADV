import json
from datetime import datetime,timedelta
import sqlite3
import pytest
from paper_lanes.book import Book
from paper_lanes.manager import Manager,IST
from paper_lanes.maintenance import reset_cbe

NOW=datetime(2026,10,27,14,50,tzinfo=IST)


def test_reset_archives_old_account_and_rejects_its_cohorts(tmp_path):
    folder=tmp_path/'books';old=Book(folder/'cbe.sqlite3','cbe',5000000,NOW-timedelta(days=1))
    with old.transaction() as c:old.event(c,NOW,'OLD_EVENT',None,{'test':'retained'})
    archive=tmp_path/'archive'
    receipt=reset_cbe(folder,archive,5000000,NOW)
    new=Book(folder/'cbe.sqlite3','cbe',5000000,NOW)
    assert new.account_id!=old.account_id and receipt['previous_account_id']==old.account_id
    assert new.snapshot(NOW)['cash']==5000000 and not new.snapshot(NOW)['orders']
    assert new.enqueue('old',[],NOW,str(NOW.date()),NOW+timedelta(minutes=1),NOW+timedelta(minutes=5),old.account_id)==0
    with sqlite3.connect(archive/'backup.sqlite3') as c:
        assert c.execute('PRAGMA quick_check(1)').fetchone()[0]=='ok'
        assert c.execute("SELECT count(*) FROM events WHERE kind='OLD_EVENT'").fetchone()[0]==1
    with pytest.raises(ValueError):reset_cbe(folder,archive,5000000,NOW)


def test_atm_must_still_match_fresh_underlying_before_review_and_fill(tmp_path):
    b=Book(tmp_path/'vanguard.sqlite3','vanguard',5000000,NOW)
    o={'payload':json.dumps({'atm_guard':{'spot_instrument':'NSE_EQ|S','strikes':[950,1000,1050],'strike':1000}})}
    def quote(mid,at=NOW):return dict(at=at.isoformat(),bid=mid-.1,ask=mid+.1,bid_qty=10000,ask_qty=10000,volume=100000)
    assert not b.atm_reason(o,{'NSE_EQ|S':quote(1001)},NOW)
    assert 'no longer ATM' in b.atm_reason(o,{'NSE_EQ|S':quote(1040)},NOW)
    assert 'Fresh underlying' in b.atm_reason(o,{'NSE_EQ|S':quote(1001,NOW-timedelta(seconds=20))},NOW)


def test_vanguard_enters_same_session_and_cash_cbe_next_session(tmp_path):
    m=Manager(tmp_path,capital=5000000,now=NOW-timedelta(hours=1),market=object())
    pub=dict(lane='vanguard',id='new',decision_at=NOW.isoformat(),source_session=str(NOW.date()),
        paper_account_id=m.books['vanguard'].account_id,watchlist=[dict(instrument='NSE_FO|X',symbol='X',contract_kind='ATM',horizon_sessions=1)])
    meta={'NSE_FO|X':dict(instrument='NSE_FO|X',symbol='X',expiry='2026-11-24',lot_size=50,tick=.05,
        atm_guard={'spot_instrument':'NSE_EQ|S','strikes':[1000],'strike':1000})}
    assert m.offer(pub,meta)==1
    order=m.snapshot('vanguard')['orders'][0]
    assert order['entry_after'].startswith('2026-10-27T14:50:01') and order['deadline'].startswith('2026-10-27T15:15')
    assert 'NSE_EQ|S' in m.instruments() and 'NSE_FO|X' in m.instruments()


def test_approved_vanguard_fills_only_while_atm_then_manages_exit_without_qwen(tmp_path):
    b=Book(tmp_path/'vanguard.sqlite3','vanguard',5000000,NOW-timedelta(hours=1))
    guard={'spot_instrument':'NSE_EQ|S','strikes':[950,1000,1050],'strike':1000}
    row=dict(instrument='NSE_FO|X',symbol='X',sector='TEST',lot_size=50,tick=.05,expiry='2026-10-29',
        candidate={'contract_kind':'ATM','atm_guard':guard},exit_due=NOW+timedelta(days=1))
    assert b.enqueue('new',[row],NOW-timedelta(seconds=10),str(NOW.date()),NOW-timedelta(seconds=5),NOW+timedelta(minutes=10),b.account_id)==1
    def quote(bid,ask):return dict(at=NOW.isoformat(),bid=bid,ask=ask,bid_qty=100000,ask_qty=100000,volume=10000000)
    quotes={'NSE_FO|X':quote(100,101),'NSE_EQ|S':quote(999,1001)}
    orders=b.review_candidates(quotes,NOW)
    assert len(orders)==1
    response=dict(model='isolated-fixture',digest='test',context_hash='test',created_at=NOW.isoformat(),
        expires_at=(NOW+timedelta(seconds=90)).isoformat(),reviews=[{'id':orders[0]['id'],'verdict':'approve','reason':'Isolated paper test'}])
    b.apply_reviews(orders,response,NOW)
    quotes['NSE_EQ|S']=quote(1039,1041)
    b.cycle(quotes,NOW,True,NOW+timedelta(days=1))
    assert not b.snapshot(NOW)['positions']
    quotes['NSE_EQ|S']=quote(999,1001)
    b.cycle(quotes,NOW+timedelta(seconds=1),True,NOW+timedelta(days=1))
    assert b.snapshot(NOW)['open_count']==1 and len(b.snapshot(NOW)['fills'])==1
    quotes['NSE_FO|X']=quote(140,141)
    b.cycle(quotes,NOW+timedelta(seconds=2),True,NOW+timedelta(days=1))
    result=b.snapshot(NOW)
    assert result['open_count']==0 and result['closed_count']==1 and len(result['fills'])==2
    assert result['closed'][0]['exit_reason']=='TARGET'


def test_vanguard_buys_no_stock_option_inside_its_series_last_five_sessions(tmp_path):
    # Owner, 18 Nov 2026 (R1, as the engine's books): from E-5 a stock series only closes.
    from paper_lanes.manager import series_closed
    from datetime import date
    assert series_closed(date(2026,11,16),'2026-11-23') and not series_closed(date(2026,11,13),'2026-11-23')
    day=datetime(2026,11,19,14,50,tzinfo=IST)
    m=Manager(tmp_path,capital=5000000,now=day-timedelta(hours=1),market=object())
    pub=dict(lane='vanguard',id='nov',decision_at=day.isoformat(),source_session=str(day.date()),
        paper_account_id=m.books['vanguard'].account_id,watchlist=[dict(instrument=f'NSE_FO|{k}',symbol=k,contract_kind='ATM',horizon_sessions=3) for k in 'ND'])
    guard={'spot_instrument':'NSE_EQ|S','strikes':[1000],'strike':1000}
    meta={'NSE_FO|N':dict(instrument='NSE_FO|N',symbol='N',expiry='2026-11-23',lot_size=50,tick=.05,atm_guard=guard),
          'NSE_FO|D':dict(instrument='NSE_FO|D',symbol='D',expiry='2026-12-29',lot_size=50,tick=.05,atm_guard=guard)}
    assert m.offer(pub,meta)==1
    assert [o['instrument'] for o in m.snapshot('vanguard')['orders']]==['NSE_FO|D']
