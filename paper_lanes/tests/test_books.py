"""Isolated prospective fills and real cash reconciliation; never uses production state."""
from datetime import datetime,timedelta
import json
from zoneinfo import ZoneInfo

import pytest

from paper_lanes.book import Book,fresh,money
from paper_lanes.manager import Manager,parse_quotes,next_session,session_open

IST=ZoneInfo('Asia/Kolkata')
ACT=datetime(2026,10,25,21,tzinfo=IST)
DEC=datetime(2026,10,26,15,20,tzinfo=IST)
OPEN=datetime(2026,10,27,9,21,tzinfo=IST)


def quote(now=OPEN,bid=100,ask=100.2,**values):
    return dict(at=now.isoformat(),bid=bid,ask=ask,bid_qty=100000,ask_qty=100000,volume=10000000,**values)


def book(tmp_path,lane='vanguard'):
    return Book(tmp_path/(lane+'.sqlite3'),lane,1000000,ACT)


def enqueue(b,instrument=None,cohort='future',rank=1,exit_due=None,sector='BANKS',**kw):
    instrument=instrument or ('NSE_FO|TEST' if b.lane=='vanguard' else 'NSE_EQ|TEST')
    row=dict(instrument=instrument,symbol=instrument,lot_size=50 if b.lane=='vanguard' else 1,tick=.05,
             expiry='2026-10-29' if b.lane=='vanguard' else None,sector=sector,
             exit_due=exit_due or OPEN.replace(hour=15,minute=15),candidate={'rank':rank})
    args=dict(cohort=cohort,rows=[row],decided_at=DEC,source_session=str(DEC.date()),
              entry_after=OPEN.replace(minute=20),deadline=OPEN.replace(minute=55),account_id=b.account_id)
    args.update(kw)
    return b.enqueue(**args)


def response(orders,now=OPEN,verdict='approve'):
    return dict(model='qwen3.5:9b',digest='fixture-only',context_hash='isolated',created_at=now.isoformat(),
                expires_at=(now+timedelta(seconds=30)).isoformat(),
                reviews=[dict(id=o['id'],verdict=verdict,reason='Isolated policy test') for o in orders])


def approved(b,quotes,now=OPEN,verdict='approve'):
    orders=b.review_candidates(quotes,now)
    assert orders
    b.apply_reviews(orders,response(orders,now,verdict),now)
    return orders


def cycle(b,quotes,now=OPEN,opened=True):
    b.cycle(quotes,now,opened,datetime.combine(next_session(now.date()),datetime.min.time(),IST).replace(hour=9,minute=15))


def test_new_account_immutable_capital_and_no_legacy_admission(tmp_path):
    b=book(tmp_path);s=b.snapshot(ACT)
    assert s['equity']==s['cash']==s['initial_capital']==1000000
    assert not s['positions'] and not s['fills'] and not s['orders']
    assert enqueue(b,account_id='retained-shadow-account')==0
    assert enqueue(b,decided_at=ACT-timedelta(days=1),source_session='2026-10-24')==0
    assert enqueue(b)==1 and enqueue(b)==0
    restarted=book(tmp_path)
    assert restarted.account_id==b.account_id and len(restarted.snapshot(ACT)['orders'])==1
    with pytest.raises(ValueError,match='capital'):Book(b.path,b.lane,5000000,ACT)


@pytest.mark.parametrize('change',[{'bid':0},{'ask':99},{'ask_qty':0},{'volume':0},{'at':(OPEN-timedelta(seconds=11)).isoformat()},
                                  {'at':(OPEN+timedelta(seconds=3)).isoformat()},{'at':'bad'},{'bid':float('nan')}])
def test_invalid_quote_never_admits(tmp_path,change):
    b=book(tmp_path);enqueue(b);q={**quote(),**change}
    assert not b.review_candidates({'NSE_FO|TEST':q},OPEN)
    cycle(b,{'NSE_FO|TEST':q});assert not b.snapshot(OPEN)['fills']


@pytest.mark.parametrize('verdict',['reject','wait'])
def test_qwen_veto_or_unavailable_never_fills(tmp_path,verdict):
    b=book(tmp_path);enqueue(b);qs={'NSE_FO|TEST':quote()}
    approved(b,qs,verdict=verdict);cycle(b,qs)
    assert not b.snapshot(OPEN)['fills']
    if verdict=='wait':
        orders=b.review_candidates(qs,OPEN+timedelta(seconds=61))
        assert not orders # quote is now stale
    other=book(tmp_path/'other');enqueue(other)
    orders=other.review_candidates(qs,OPEN);other.review_failed(orders,OPEN);cycle(other,qs)
    assert not other.snapshot(OPEN)['fills'] and other.snapshot(OPEN)['orders'][0]['attempts']==0 and other.snapshot(OPEN)['orders'][0]['service_failures']==1


def test_whole_lot_depth_fee_cash_and_idempotent_restart(tmp_path):
    b=book(tmp_path);enqueue(b);qs={'NSE_FO|TEST':{**quote(),'ask_qty':123,'bid_qty':170}}
    orders=approved(b,qs);assert orders[0]['planned_quantity']==100
    cycle(b,qs);s=b.snapshot(OPEN)
    assert s['open_count']==1 and s['positions'][0]['quantity']==100
    fill=s['fills'][0];assert fill['price']>qs['NSE_FO|TEST']['ask'] and fill['quantity']%50==0
    assert money(s['cash'])==money(s['initial_capital'])-fill['notional_paise']-fill['fee_paise']
    restarted=book(tmp_path);cycle(restarted,qs)
    assert len(restarted.snapshot(OPEN)['fills'])==1
    assert s['equity']==pytest.approx(s['initial_capital']+s['realized_pnl']+s['unrealized_pnl'])


@pytest.mark.parametrize('trigger,bid,expected',[('stop',80,'STOP'),('target',140,'TARGET'),('time',100,'TIME_EXIT')])
def test_exit_accounting_and_no_model_dependency(tmp_path,trigger,bid,expected):
    b=book(tmp_path);enqueue(b);qs={'NSE_FO|TEST':quote()};approved(b,qs);cycle(b,qs)
    b.control(True,OPEN)
    now=OPEN+timedelta(seconds=5) if trigger!='time' else OPEN.replace(hour=15,minute=15)
    qs={'NSE_FO|TEST':quote(now,bid,bid+.2)};cycle(b,qs,now);cycle(b,qs,now)
    s=b.snapshot(now)
    assert s['open_count']==0 and s['closed_count']==1 and len(s['fills'])==2
    assert s['closed'][0]['exit_reason']==expected and s['paused']
    assert s['cash']==s['equity']==pytest.approx(1000000+s['realized_pnl'])
    assert s['unrealized_pnl']==0 and s['fees_paid']>0
    assert s['closed'][0]['pnl_paise']==sum((1 if f['side']=='SELL' else -1)*f['notional_paise']-f['fee_paise'] for f in s['fills'])


def test_stock_cash_settlement_no_double_credit(tmp_path):
    b=book(tmp_path,'cbe');enqueue(b);qs={'NSE_EQ|TEST':quote()};approved(b,qs);cycle(b,qs)
    now=OPEN.replace(hour=15,minute=15);cycle(b,{'NSE_EQ|TEST':quote(now)},now);s=b.snapshot(now)
    assert s['unsettled_cash']>0 and s['cash']<s['equity']
    assert s['equity']==pytest.approx(1000000+s['realized_pnl'])
    settled=now+timedelta(days=1);settled=settled.replace(hour=9,minute=15)
    cycle(b,{},settled);cycle(b,{},settled)
    end=b.snapshot(settled)
    assert end['unsettled_cash']==0 and end['cash']==pytest.approx(s['equity'])


def test_paused_entries_stale_approval_drift_and_missed_window(tmp_path):
    b=book(tmp_path);enqueue(b);qs={'NSE_FO|TEST':quote()};approved(b,qs)
    b.control(True,OPEN);cycle(b,qs);assert not b.snapshot(OPEN)['fills']
    b.control(False,OPEN)
    cycle(b,{'NSE_FO|TEST':quote(OPEN,103,103.2)})
    assert b.snapshot(OPEN)['orders'][0]['state']=='PENDING_AI'
    later=OPEN+timedelta(minutes=1);qs={'NSE_FO|TEST':quote(later)};approved(b,qs,later)
    late=later+timedelta(seconds=31);cycle(b,{'NSE_FO|TEST':quote(late)},late)
    assert b.snapshot(late)['orders'][0]['state']=='PENDING_AI'
    missed=OPEN.replace(hour=10);cycle(b,{'NSE_FO|TEST':quote(missed)},missed)
    assert b.snapshot(missed)['orders'][0]['state']=='EXPIRED' and not b.snapshot(missed)['fills']


def test_stale_and_insufficient_depth_cannot_fake_exit(tmp_path):
    b=book(tmp_path);enqueue(b);qs={'NSE_FO|TEST':quote()};approved(b,qs);cycle(b,qs)
    late=OPEN.replace(hour=15,minute=15);cycle(b,qs,late)
    assert b.snapshot(late)['open_count']==1
    cycle(b,{'NSE_FO|TEST':{**quote(late),'bid_qty':1}},late)
    assert b.snapshot(late)['open_count']==1 and len(b.snapshot(late)['fills'])==1


def test_daily_loss_and_sector_exposure_block_new_entries(tmp_path):
    b=book(tmp_path,'cbe')
    for i in range(5):enqueue(b,instrument=f'NSE_EQ|{i}',cohort=f'future{i}',rank=i+1)
    qs={f'NSE_EQ|{i}':quote() for i in range(5)}
    approved(b,qs);cycle(b,qs)
    assert b.snapshot(OPEN)['open_count']<=3
    with b.transaction() as db:
        db.execute('UPDATE account SET cash_paise=cash_paise-5000000')
    assert not b.review_candidates(qs,OPEN)


def test_exits_commit_even_when_entry_is_corrupted(tmp_path):
    b=book(tmp_path);enqueue(b);qs={'NSE_FO|TEST':quote()};approved(b,qs);cycle(b,qs)
    enqueue(b,instrument='NSE_FO|BAD',cohort='bad')
    with b.transaction() as db:db.execute("UPDATE orders SET state='APPROVED',approval='broken' WHERE instrument='NSE_FO|BAD'")
    now=OPEN+timedelta(seconds=5)
    with pytest.raises(json.JSONDecodeError):cycle(b,{'NSE_FO|TEST':quote(now,80,80.2)},now)
    assert b.snapshot(now)['closed_count']==1 and len(b.snapshot(now)['fills'])==2


def test_exact_broker_timestamp_parser_and_closed_market(tmp_path):
    body={'status':'success','data':{'alias':{'instrument_token':'NSE_EQ|A','timestamp':str(int(OPEN.timestamp()*1000)),
         'depth':{'buy':[{'price':100,'quantity':1000}],'sell':[{'price':101,'quantity':2000}]},'volume':100000}}}
    result=parse_quotes(body,{'NSE_EQ|A'})
    assert fresh(result['NSE_EQ|A'],OPEN)
    assert not parse_quotes(body,{'NSE_EQ|WRONG'})
    del body['data']['alias']['timestamp'];assert not parse_quotes(body,{'NSE_EQ|A'})
    class NoNetwork:
        def quotes(self,keys):raise AssertionError('No quote request while closed')
    m=Manager(tmp_path,now=ACT,market=NoNetwork());m.poll(ACT)
    assert m.snapshot('cbe')['status']['state']=='market_closed'
    assert next_session(datetime(2026,10,30).date()).isoformat()=='2026-11-02'
    assert session_open(OPEN) and not session_open(ACT)
    late=OPEN.replace(hour=15,minute=35)                     # F&O trades to 15:40 since Aug 2026; the cash book stops at 15:30
    assert session_open(late,'vanguard') and not session_open(late,'cbe') and not session_open(late.replace(minute=40),'vanguard')


def test_manager_account_epoch_metadata_horizon_and_restart(tmp_path):
    m=Manager(tmp_path,now=ACT);pub=dict(lane='cbe',id='new',decision_at=DEC.isoformat(),source_session=str(DEC.date()),
        paper_account_id='old',watchlist=[{'instrument':'ABC','symbol':'ABC','horizon_sessions':1,'rank':1}])
    metadata={'ABC':{'instrument':'NSE_EQ|ABC','symbol':'ABC','lot_size':1,'tick':.01}}
    assert m.offer(pub,metadata)==0
    pub['paper_account_id']=m.books['cbe'].account_id
    assert m.offer(pub,metadata)==1 and m.offer(pub,metadata)==0
    m2=Manager(tmp_path,now=OPEN);assert m2.offer(pub,metadata)==0
    o=m2.snapshot('cbe')['orders'][0]
    assert o['tick']==.01 and o['entry_after'].startswith('2026-10-27T09:20')
    assert o['exit_due'].startswith('2026-10-27T15:15')
