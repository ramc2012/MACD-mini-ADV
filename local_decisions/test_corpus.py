from datetime import datetime,timezone
import json

import pytest

from local_decisions.build_corpus import message,split,smart_records,vanguard_records


def test_split_purges_labels_crossing_validation_or_test_boundaries():
    assert split({'decision_at':'2026-10-09T10:00:00+05:30','label_available_at':'2026-10-12T10:00:00+05:30'})=='purged'
    assert split({'decision_at':'2026-10-16T10:00:00+05:30','label_available_at':'2026-10-19T10:00:00+05:30'})=='purged'
    assert split({'decision_at':'2026-09-04T10:00:00+05:30','label_available_at':'2026-09-05T10:00:00+05:30'})=='train'
    with pytest.raises(ValueError):split({'decision_at':'2026-10-16T10:00:00+05:30','label_available_at':'2026-10-15T10:00:00+05:30'})


def test_future_outcomes_and_excursions_never_enter_prompt():
    record={'lane':'smart','instrument':'NSE:FIXTURECE','side':'CE','reference_price':10,'features':{'signal_macd':1},'decision_at':'2026-10-22T10:00:00+05:30',
            'net_return':.25,'stress_return':.20,'label_available_at':'2026-10-23T10:00:00+05:30','max_price':9999}
    result=message(record);content=json.loads(result['messages'][1]['content'])
    assert set(content['candidates'][0])=={'id','instrument','side','reference_price','source_at','facts'}
    assert 'net_return' not in result['messages'][1]['content'] and '9999' not in result['messages'][1]['content']
    assert json.loads(result['messages'][2]['content'])['reviews'][0]['verdict']=='approve'


def fixture():
    at='2026-10-05T04:30:00+00:00';end='2026-10-05T08:30:00+00:00';symbol='NSE:FIXTURE26OCT100CE'
    return {'source':'isolated','tables':{
      'signals':[{'signal_id':'s','payload':json.dumps(dict(signal_id='s',symbol=symbol,side='BUY',timestamp=at,price=10,macd=1,signal=.2,histogram=.8))}],
      'orders':[{'order_id':'o','payload':json.dumps({'signal_id':'s'})}],
      'trades':[{'payload':json.dumps(dict(symbol=symbol,side='BUY',timestamp=at,order_id='o',quantity=100))}],
      'closed_positions':[{'position_id':'p','payload':json.dumps(dict(symbol=symbol,entry_time=at,exit_time=end,quantity=100,entry_price=10,exit_price=12,gross_pnl=200,fees=0,partial=False,remaining_quantity=0))}]}}


def test_actual_position_must_be_complete_and_cost_floor_applies():
    f=fixture();records,excluded=smart_records(f)
    assert not excluded and len(records)==1 and records[0]['net_return']==pytest.approx(.1978)
    row=json.loads(f['tables']['closed_positions'][0]['payload']);row['quantity']=50
    f['tables']['closed_positions'][0]['payload']=json.dumps(row)
    records,excluded=smart_records(f)
    assert not records and excluded['incomplete_position_close']==1


def test_future_signal_link_is_quarantined():
    f=fixture();r=json.loads(f['tables']['signals'][0]['payload']);r['timestamp']='2026-10-05T04:31:00+00:00'
    f['tables']['signals'][0]['payload']=json.dumps(r)
    records,excluded=smart_records(f)
    assert not records and excluded['decision_entry_time_mismatch']==1


def test_vanguard_entry_before_frozen_decision_is_not_a_training_label():
    run=dict(source_session='2026-10-01',decision_at='2026-10-01T14:50:00+05:30',is_replay=False)
    row=dict(id=1,source_session='2026-10-01',entry_ts='2026-10-01T14:45:00+05:30',source_mark_ts='2026-10-01T14:40:00+05:30',horizon_sessions=1)
    records,excluded=vanguard_records({'vanguard_swing_watchlist_runs':[run],'vanguard_swing_watchlist_items':[row]})
    assert not records and excluded['future_or_retroactive_entry']==1


def test_rejecting_everything_is_reported_as_zero_coverage():
    from local_decisions.evaluate_local import metrics
    result=metrics([{'net_return':-.1,'stress_return':-.11}],[{'verdict':'reject'}])
    assert result['approval_fraction']==0 and result['approved']['rows']==0
    assert result['approved']['mean_net_return'] is None


def test_incomplete_predictions_cannot_produce_full_split_metrics():
    from local_decisions.evaluate_local import metrics
    with pytest.raises(ValueError):metrics([{'net_return':1,'stress_return':.9}],[])


def test_reconstructed_splits_keep_receipt_time_and_purge_economic_boundary():
    from local_decisions.build_research_corpus import split_research
    row={'lane':'vanguard','decision_at':'2026-10-09T15:00:00+05:30',
         'label_matures_at':'2026-10-12T15:00:00+05:30', 'label_available_at':'2026-10-25T12:00:00+05:30'}
    assert split_research(row)=='purged'
    row['decision_at']='2026-10-14T15:00:00+05:30';row['label_matures_at']='2026-10-16T15:00:00+05:30'
    assert split_research(row)=='test'
    assert row['label_available_at']=='2026-10-25T12:00:00+05:30'


def test_reconstructed_same_bar_target_and_stop_uses_stop():
    from local_decisions.label_reconstructed import label
    from zoneinfo import ZoneInfo
    at=datetime(2026,10,6,9,21,tzinfo=ZoneInfo('Asia/Kolkata'))
    class Cursor:
        def execute(self,*args):pass
        def fetchall(self):return [dict(time=at,open=100,high=150,low=50,close=100,synced_at=at)]
    row={'lane':'cbe','decision_at':'2026-10-05T15:15:00+05:30','symbol':'FIXTURE','horizon':1}
    result,reason=label(row,Cursor())
    assert reason is None and result['proxy_exit_reason']=='stop'
    assert result['same_bar_stop_target_ambiguity'] and result['net_return']<0
    assert result['historical_availability_verified'] is False


def test_nonfinite_paper_outcome_is_quarantined():
    f=fixture();row=json.loads(f['tables']['closed_positions'][0]['payload']);row['gross_pnl']=float('nan')
    f['tables']['closed_positions'][0]['payload']=json.dumps(row)
    records,excluded=smart_records(f)
    assert not records and excluded['invalid_or_missing_record']==1


def test_compute_guard_refuses_overlapping_model_allocations(tmp_path,monkeypatch):
    from local_decisions.compute_guard import exclusive_compute
    monkeypatch.chdir(tmp_path)
    with exclusive_compute():
        with pytest.raises(BlockingIOError):
            with exclusive_compute():pytest.fail('Second allocation must not start')
    with exclusive_compute():pass
