from datetime import datetime,date,timedelta
import asyncio,json
import pytest
from shadow_lanes import source
from shadow_lanes.decision_context import block,SPECS
from shadow_lanes import feature_pipeline

NOW=datetime(2026,10,26,14,50,tzinfo=source.IST)

def test_future_receipt_or_unfinished_bar_never_becomes_current_confirmation():
    row={'ts':NOW.replace(hour=14,minute=45),'computed_at':NOW,'score':1}
    assert block('x',row,'ts','computed_at','bar',('score',),NOW)=={'x.status':'not_available_at_decision'}
    row['ts']=NOW-timedelta(hours=1);row['computed_at']=NOW+timedelta(seconds=1)
    assert 'x.score' not in block('x',row,'ts','computed_at','bar',('score',),NOW)


def test_unknown_receipt_is_disclosed_and_fields_are_allowlisted():
    row={'ts':NOW-timedelta(hours=1),'score':1,'password':'must-never-enter-model'}
    value=block('flow',row,'ts',None,'bar',('score',),NOW)
    assert value['flow.score']==1 and 'historical availability unverified' in value['flow.receipt_basis']
    assert not any('password' in k for k in value)
    assert block('flow',None,'ts',None,'bar',('score',),NOW)=={'flow.status':'missing'}


def test_prior_session_remains_explicit_and_nonfinite_values_are_excluded():
    row={'dt':date(2026,10,23),'computed_at':NOW-timedelta(days=2),'oi':float('nan'),'score':.2}
    value=block('eod',row,'dt','computed_at','eod',('oi','score'),NOW)
    assert value['eod.status']=='prior_session' and 'eod.oi' not in value and value['eod.score']==.2


def test_no_execution_or_outcome_features_are_in_context_contract():
    forbidden={'gross_pnl','net_return','max_return_pct','exit_price','realized_pnl','api_token','password'}
    assert len(SPECS)>=12
    assert not any(forbidden.intersection(fields) for *_,fields in SPECS)


def test_session_date_does_not_invent_a_0915_timestamp_or_hourly_staleness():
    row={'dt':NOW.date(),'computed_at':NOW,'score':.2}
    value=block('sector',row,'dt','computed_at','intraday_day',('score',),NOW)
    assert value['sector.status']=='observed' and value['sector.score']==.2
    assert value['sector.event_at'] is None and value['sector.age_hours'] is None
    assert value['sector.event_date']=='2026-10-26' and value['sector.event_time_resolution']=='session_date_only'
    assert value['sector.age_days']==0
    assert block('eod',row,'dt','computed_at','eod',('score',),NOW)=={'eod.status':'not_available_at_decision'}


def test_prior_session_cross_asset_beta_is_not_current_session_confirmation():
    from shadow_lanes.decision_context import cross_asset_context
    row={'driver':'crude_oil','dt':date(2026,10,23),'computed_at':NOW-timedelta(days=2),'beta':.2,'corr':.3,'lookback_days':60}
    facts=cross_asset_context([row],NOW)
    assert facts['cross_asset.status']=='prior_session' and facts['cross_asset.crude_oil.status']=='prior_session'
    assert facts['cross_asset.crude_oil.event_at'] is None and facts['cross_asset.crude_oil.event_date']=='2026-10-23'
    assert facts['cross_asset.crude_oil.beta']==.2


def test_independent_feature_stages_continue_after_failure(tmp_path,monkeypatch):
    monkeypatch.setenv('FEATURE_RECEIPTS_PATH',str(tmp_path/'receipts.sqlite3'))
    monkeypatch.setenv('MI_PG_USER','fixture');monkeypatch.setenv('MI_PG_PASSWORD','fixture')
    invoked=[]
    class Process:
        def __init__(self,code):self.returncode=code
        async def communicate(self):return b'',b'no unique or exclusion constraint' if self.returncode else b''
    async def create(*args,**kwargs):invoked.append(args);return Process(1 if len(invoked)==1 else 0)
    monkeypatch.setattr(feature_pipeline.asyncio,'create_subprocess_exec',create)
    result=asyncio.run(feature_pipeline.refresh('fixture',[('a','features/a.py'),('b','features/b.py')]))
    assert result=={'a':'missing_conflict_index','b':'ok'} and len(invoked)==2
    assert feature_pipeline.status()['a']['status']=='missing_conflict_index'


def test_bar_refresh_runs_once_after_close_delay_and_includes_final_bar():
    from shadow_lanes.feature_pipeline import completed_bar_key
    for hour,minute in ((0,0),(0,1),(0,2),(8,0),(9,15)):
        assert completed_bar_key(NOW.replace(hour=hour,minute=minute,second=0)) is None
    assert completed_bar_key(NOW.replace(hour=9,minute=45,second=59)) is None
    assert completed_bar_key(NOW.replace(hour=9,minute=46,second=30))==('2026-10-26',0)
    assert completed_bar_key(NOW.replace(hour=10,minute=0,second=0))==('2026-10-26',0)
    assert completed_bar_key(NOW.replace(hour=10,minute=16,second=30))==('2026-10-26',1)
    assert completed_bar_key(NOW.replace(hour=15,minute=41,second=30))==('2026-10-26',12)
