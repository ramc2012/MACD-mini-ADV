import asyncio
from datetime import datetime,timedelta,timezone
import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from local_decisions.service import Reviewer,ReviewInput,create_app,local_url,validate_reviews


def payload():
    now=datetime.now(timezone.utc)
    return {'lane':'smart','snapshot_at':now.isoformat(),'candidates':[{'id':'fixture-1','instrument':'NSE:FIXTURE26NOV100CE',
        'side':'CE','reference_price':10,'source_at':now.isoformat(),'facts':{'ranker_score':.7,'histogram':1}}]}


def worker(tmp_path,handler):
    return Reviewer(tmp_path/'reviews.sqlite',url='http://localhost:11434',transport=httpx.MockTransport(handler))


def test_concurrent_books_share_one_inference_and_exact_context(tmp_path):
    calls=[]
    async def handler(request):
        calls.append(request.url.path)
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'qwen3.5:9b','digest':'fixture-digest'}]})
        p=json.loads(request.content)
        assert p['keep_alive']==0 and p['think'] is False and p['options']['num_ctx']==4096
        assert 'tools' not in p
        await asyncio.sleep(.01)
        return httpx.Response(200,json={'done':True,'message':{'content':json.dumps({'reviews':[{'id':'fixture-1','verdict':'wait','reason':'Fixture lacks sufficient independent evidence'}]})}})
    r=worker(tmp_path,handler);request=ReviewInput(**payload())
    async def run():return await asyncio.gather(r.review(request),r.review(request))
    a,b=asyncio.run(run())
    assert calls.count('/api/chat')==1 and not a['cache_hit'] and b['cache_hit']
    assert a['context_hash']==b['context_hash'] and a['digest']=='fixture-digest'
    with r.db() as db:assert db.execute('SELECT count(*) FROM reviews').fetchone()[0]==1


@pytest.mark.parametrize('value',[
    {'reviews':[{'id':'invented-contract','verdict':'approve','reason':'x'}]},
    {'reviews':[{'id':'fixture-1','verdict':'approve','reason':'x','lots':999}]},
    {'reviews':[]},
    {'reviews':[{'id':'fixture-1','verdict':'BUY','reason':'x'}]},
])
def test_model_cannot_invent_identity_size_or_action(value):
    with pytest.raises(ValueError):validate_reviews(value,['fixture-1'])


def test_missing_model_never_downloads_or_calls_generation(tmp_path):
    calls=[]
    def handler(request):calls.append(request.url.path);return httpx.Response(200,json={'models':[]})
    r=worker(tmp_path,handler)
    with pytest.raises(HTTPException) as caught:asyncio.run(r.review(ReviewInput(**payload())))
    assert caught.value.status_code==503 and calls==['/api/tags']
    with r.db() as db:
        saved=db.execute('SELECT lane,input FROM training_snapshots').fetchall()
    assert len(saved)==1 and saved[0][0]=='smart' and 'fixture-1' in saved[0][1]


@pytest.mark.parametrize('url',['https://ollama.com','http://external.example:11434','http://localhost:11434/other','http://user:password@localhost:11434'])
def test_external_inference_endpoints_are_rejected(url):
    with pytest.raises(ValueError):local_url(url)


def test_stale_input_never_calls_model(tmp_path):
    r=worker(tmp_path,lambda _:pytest.fail('No model call for stale context'))
    p=payload();p['snapshot_at']=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    with pytest.raises(HTTPException) as caught:asyncio.run(r.review(ReviewInput(**p)))
    assert caught.value.status_code==409


def test_auth_and_upstream_failure_do_not_expose_private_error(tmp_path,monkeypatch):
    monkeypatch.setenv('MACD_API_TOKEN','fixture-only-token')
    def handler(request):
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'qwen3.5:9b','digest':'fixture'}]})
        return httpx.Response(500,json={'error':'private upstream detail'})
    with TestClient(create_app(worker(tmp_path,handler))) as c:
        assert c.post('/review',json=payload()).status_code==401
        response=c.post('/review',json=payload(),headers={'X-Macd-Token':'fixture-only-token'})
        assert response.status_code==503 and 'private' not in response.text
