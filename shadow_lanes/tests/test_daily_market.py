from datetime import datetime,date,timedelta
import pytest
from shadow_lanes import source
from shadow_lanes.daily_market import indicators,scan


def test_daily_indicators_flat_and_rising():
    flat=indicators([100.]*100)
    assert flat['rsi_14']==50 and flat['macd_hist']==0 and flat['ema50']==100
    rising=indicators(list(range(1,101)))
    assert rising['rsi_14']==100 and rising['ema20']>rising['ema50'] and rising['macd_line']>0


def fixture(monkeypatch,gap=None,mp_current=True):
    monkeypatch.setattr(source,'is_session',lambda d:d.weekday()<5)
    now=datetime(2026,10,27,8,tzinfo=source.IST)
    days=[];d=date(2026,10,26)
    while len(days)<100:
        if d.weekday()<5:days.append(d)
        d-=timedelta(days=1)
    days.reverse();history=[]
    for name in ['NIFTY']+['STOCK'+str(i) for i in range(10)]:
        for i,d in enumerate(days):
            if name!='NIFTY' and gap is not None and d==days[gap]:continue
            price=100+i*(.1 if name=='NIFTY' else 1)
            history.append(dict(underlying=name,session=d,close=price,open=price,high=price,low=price,volume=10000,
                available_at=now-timedelta(days=1),bars=13))
    data=[history,[],[dict(symbol='STOCK'+str(i),sector20='TEST') for i in range(10)],
          [dict(underlying='STOCK'+str(i),dt=days[-1] if mp_current else days[-2],poc=150,computed_at=now-timedelta(hours=1)) for i in range(10)]]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):self.rows=data.pop(0) if data else []
        def fetchall(self):return self.rows
    class Connection:
        def cursor(self,**kw):return Cursor()
    return Connection(),now


def test_full_universe_with_old_seed_gap_disclosed(monkeypatch):
    c,now=fixture(monkeypatch,gap=10)
    r=scan(c,now)
    assert len(r['results'])==10 and len(r['watchlist'])==10
    assert r['current_complete']==10 and r['mp_complete']==10
    assert all(x['older_seed_gaps']==1 and x['aligned_daily_observations']==99 for x in r['results'])
    assert all(x['source_mark_ts'].startswith('2026-10-26T15:30') for x in r['watchlist'])


@pytest.mark.parametrize('gap,mp_current',[(90,True),(None,False)])
def test_recent_price_gap_or_stale_mp_blocks_candidates_but_keeps_visible_data(monkeypatch,gap,mp_current):
    c,now=fixture(monkeypatch,gap,mp_current)
    r=scan(c,now)
    assert not r['watchlist'] and len(r['results'])==10
    assert all(x['latest_close']>0 and x['rsi_14'] is not None for x in r['results'])


def test_atm_selection_excludes_wings_and_duplicate_directions():
    rows=[dict(symbol=s,option_type=side,contract_kind=kind,combined_score=score,direction_score=score,contract_score=score)
          for s,side,kind,score in [('A','CE','WING_25D',100),('A','PE','ATM',90),('A','CE','ATM',80),('B','CE','ATM',70)]]
    selected=source.select_atm(rows)
    assert [(r['symbol'],r['option_type']) for r in selected]==[('A','PE'),('B','CE')]
    assert all(r['expected_net_return'] is None and r['expected_net_lower'] is None for r in selected)
    assert all('not calibrated' in r['ranker_entry_basis'] for r in selected)


def test_atm_selection_fills_ten_calls_and_ten_puts():
    rows=[dict(symbol=f'C{i}',option_type='CE',contract_kind='ATM',combined_score=100-i,direction_score=1,contract_score=1,horizon_sessions=1)
          for i in range(30)]+[
          dict(symbol=f'P{i}',option_type='PE',contract_kind='ATM',combined_score=10-i,direction_score=1,contract_score=1,horizon_sessions=2)
          for i in range(12)]
    selected=source.select_atm(rows)
    sides=[r['option_type'] for r in selected]
    assert sides.count('CE')==10 and sides.count('PE')==10           # strong calls do not crowd out the puts
    assert all(r['horizon_sessions']==3 for r in selected)
    assert {r['model_horizon_sessions'] for r in selected}=={1,2}
    assert [r['side_rank'] for r in selected if r['option_type']=='PE']==list(range(1,11))


def test_evidence_votes_and_the_screen_drop_only_untradable_or_clearly_opposed_picks():
    bullish={'flow_score':45,'rs_z20':1.2,'ce_state':'long_buildup','pe_state':'long_unwind','regime':'NEG',
             'timing_state':'IGNITION','timing_score':72,'va_position':0.8}
    votes,net=source.evidence(bullish,'CE')
    assert votes=={'flow':1,'sector_rs':1,'own_side_oi':1,'regime':1,'timing':1} and net==5
    assert source.evidence(bullish,'PE')[1]==-3          # the same tape argues against buying the put
    quiet={'flow_score':5,'rs_z20':0.1,'regime':'NEUTRAL','timing_state':'BALANCED'}
    assert source.evidence(quiet,'PE')==({'flow':0,'sector_rs':0,'own_side_oi':0,'regime':0,'timing':0},0)
    row=lambda **kw:{'option_type':'CE','strike':100,'option_volume':5000,'lot_size':1000,**kw}
    assert not source.screen(row(),quiet,101)['filtered']                      # neutral evidence still passes
    assert 'not at the money' in source.screen(row(strike=110),quiet,100)['filter_reasons'][0]
    assert 'lots traded' in source.screen(row(option_volume=1000),quiet,100)['filter_reasons'][0]
    assert not source.screen(row(option_type='PE'),{**bullish,'timing_state':'BALANCED'},100)['filtered']   # net -2 passes now
    assert 'evidence against' in source.screen(row(option_type='PE'),bullish,100)['filter_reasons'][0]    # net -3 does not
    assert source.screen(row(source_mark=2),quiet,100)['premium_pct']==0.02


def test_the_most_out_of_the_money_leg_leads_and_cheapness_breaks_ties():
    rows=[dict(symbol=s,option_type='CE',contract_kind='ATM',combined_score=score,direction_score=0,contract_score=0,otm=otm,premium_pct=share)
          for s,score,otm,share in [('ITM',1.0,-0.01,0.03),('OTM',0.2,0.012,0.02),('ATM_CHEAP',0.5,0.0,0.015),('ATM_RICH',0.9,0.0,0.025)]]
    assert [r['symbol'] for r in source.select_atm(rows)]==['OTM','ATM_CHEAP','ATM_RICH','ITM']
    assert source.screen({'option_type':'PE','strike':98,'option_volume':5000,'lot_size':1000,'source_mark':2},{},100)['otm']==0.020000000000000018
