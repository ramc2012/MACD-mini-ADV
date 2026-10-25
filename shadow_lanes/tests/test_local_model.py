from datetime import datetime,timedelta,timezone

import pytest

from shadow_lanes import local_model,source
from shadow_lanes.service import ShadowWorker
from shadow_lanes.store import ShadowStore


def candidate():
    now=datetime.now(timezone.utc)
    return {'status':'ok','source_session':now.date().isoformat(),'policy':'fixture-policy',
            'watchlist':[{'instrument':'NSE_FO|fixture','symbol':'FIXTURE','option_type':'CE','source_mark':10,
                          'source_mark_ts':now.isoformat(),'horizon_sessions':3}]}


@pytest.mark.parametrize('verdict',['approve','reject','wait'])
def test_vanguard_filter_preserves_exact_identity_and_does_not_write_old_book(monkeypatch,verdict):
    monkeypatch.setenv('LOCAL_DECISION_MODE','filter');monkeypatch.setenv('LOCAL_DECISION_URL','http://local-decisions:8435')
    original=candidate();now=datetime.now(timezone.utc)
    class Response:
        def raise_for_status(self):pass
        def json(self):return response
    def post(url,**kwargs):
        row=kwargs['json']['candidates'][0]
        assert row['instrument']=='NSE_FO|fixture'
        response.update(reviews=[{'id':row['id'],'verdict':verdict,'reason':'fixture only'}])
        return Response()
    response={'expires_at':(now+timedelta(seconds=30)).isoformat(),'created_at':now.isoformat(),
              'model':'qwen3.5:9b','digest':'fixture','context_hash':'fixture'}
    monkeypatch.setattr(local_model.httpx,'post',post)
    result=local_model.apply_review(original,now)
    assert 'local_review' not in original['watchlist'][0]
    if verdict=='approve':assert result['watchlist'][0]['instrument']==original['watchlist'][0]['instrument']
    else:assert result['watchlist']==[] and result['status']=='blocked'
    assert result['policy'].endswith('-qwen35-filter-v1')


def test_failed_breadth_never_calls_local_model(monkeypatch):
    monkeypatch.setenv('LOCAL_DECISION_MODE','filter');monkeypatch.setenv('LOCAL_DECISION_URL','http://local-decisions:8435')
    monkeypatch.setattr(local_model.httpx,'post',lambda *a,**k:pytest.fail('Data gates must run before model'))
    blocked={'status':'blocked','watchlist':[],'blockers':['insufficient breadth']}
    assert local_model.apply_review(blocked,datetime.now(timezone.utc)) is blocked


def test_slow_review_cannot_publish_after_window(tmp_path,monkeypatch):
    from shadow_lanes import service
    now=datetime(2026,10,26,14,47,tzinfo=source.IST)
    monkeypatch.setenv('LOCAL_DECISION_MODE','filter')
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return now.replace(hour=15,minute=16)
    monkeypatch.setattr(service,'datetime',Clock)
    class Connection:
        def close(self):pass
    monkeypatch.setattr(source,'connect',lambda **kw:Connection())
    row=candidate();row['source_session']=str(now.date())
    monkeypatch.setattr(source,'vanguard',lambda *a:row)
    monkeypatch.setattr(local_model,'apply_review',lambda result,at:{**result,'local_review':{'fixture':True}})
    store=ShadowStore(tmp_path/'fresh.sqlite')
    ShadowWorker(store).check(now)
    assert store.publications()==[]
    assert 'outside the publication window' in store.snapshot('vanguard')['readiness']['blockers'][0]
