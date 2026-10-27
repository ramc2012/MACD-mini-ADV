import asyncio,json
from datetime import datetime,timezone,timedelta
import httpx,pytest
from fastapi import HTTPException
from local_decisions.service import Candidate,compact_facts,Reviewer,ReviewInput


def test_context_scalar_boundary_and_lossless_group_compaction():
    facts={'daily.history_basis':'Actual closes; no interpolation. CBE needs 60 observations and 50 complete recent exchange sessions. Older seed gaps are disclosed.',
        'daily.source_session':'2026-10-27','profile.poc':100.,'profile.status':'observed','rank':1}
    assert Candidate.bounded_facts(facts)==facts
    compact=compact_facts(facts)
    assert compact['daily']['history_basis']==facts['daily.history_basis']
    assert compact['profile']['poc']==100 and compact['rank']==1
    assert len(json.dumps(compact))<len(json.dumps(facts))
    with pytest.raises(ValueError):Candidate.bounded_facts({'text':'x'*161})
    Candidate.bounded_facts({str(n):n for n in range(384)})
    with pytest.raises(ValueError):Candidate.bounded_facts({str(n):n for n in range(385)})


def test_current_quote_gate_stays_strict_while_dated_watchlist_plans_can_be_reviewed(tmp_path):
    now=datetime.now(timezone.utc)
    worker=Reviewer(tmp_path/'test.sqlite',url='http://localhost:11434',transport=httpx.MockTransport(lambda r:httpx.Response(500)))
    body={'lane':'cbe','snapshot_at':now,'candidates':[dict(id='plan',instrument='TEST',side='LONG',reference_price=100,
        source_at=now-timedelta(hours=5),facts={'phase':'next_session_watchlist'})]}
    worker.fresh(ReviewInput(**body))
    body['candidates'][0]['facts']['phase']='fresh_entry_review'
    with pytest.raises(HTTPException):worker.fresh(ReviewInput(**body))


def test_bad_shared_fact_reports_schema_failure_without_calling_the_model(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCAL_CONTEXT_URL','http://shadow-lanes:8430')
    now=datetime.now(timezone.utc)
    request=ReviewInput(lane='cbe',snapshot_at=now,candidates=[dict(id='test',instrument='TEST',side='LONG',reference_price=100,
        source_at=now,facts={'phase':'next_session_watchlist'})])
    def handle(r):
        assert r.url.path=='/api/decision-context'
        return httpx.Response(200,json={'contexts':{'test':{'daily.history_basis':'x'*174}}})
    client=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client(**{**kwargs,'transport':httpx.MockTransport(handle)}))
    worker=Reviewer(tmp_path/'test.sqlite',url='http://localhost:11434')
    with pytest.raises(HTTPException):asyncio.run(worker.review(request))
    assert worker.last_error=='context_validation' and worker.calls==0
    with worker.db() as c:
        stored=json.loads(c.execute('SELECT input FROM training_snapshots').fetchone()[0])
    assert stored['request']['candidates'][0]['facts']['context_error']=='context_validation'
