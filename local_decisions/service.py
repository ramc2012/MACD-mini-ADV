"""One bounded Ollama worker shared by Smart books and Vanguard shadow selections."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .policy import SYSTEM,SYSTEM_SHA256,SMART_FEATURE_NOTES

UTC = timezone.utc


def training_busy():
    path=Path(os.environ.get('LOCAL_MODEL_WORKLOAD_FILE','/runtime/local_decisions/model_workload.json'))
    try:
        value=json.loads(path.read_text())
        # A dead lease writer must not permit Ollama to overlap a still-running
        # native trainer. The watcher explicitly clears active when jobs end.
        datetime.fromisoformat(value['expires_at'])
        return bool(value.get('active'))
    except FileNotFoundError:return False
    except (OSError,ValueError,KeyError,TypeError):return True


def local_url(value):
    parsed=urlparse(value)
    if parsed.scheme!='http' or parsed.hostname not in ('localhost','127.0.0.1','host.docker.internal') or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Ollama must use the local host, without credentials or redirects')
    return value.rstrip('/')


class Candidate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id: str=Field(min_length=1,max_length=100)
    instrument: str=Field(min_length=1,max_length=160)
    side: Literal['CE','PE','LONG','SHORT']
    reference_price: float=Field(gt=0,allow_inf_nan=False)
    source_at: datetime
    facts: dict=Field(default_factory=dict)

    @field_validator('facts')
    @classmethod
    def bounded_facts(cls,value):
        if len(value)>384:raise ValueError('Too many candidate fields')
        for key,v in value.items():
            if len(str(key))>64 or not isinstance(v,(str,int,float,bool,type(None))):raise ValueError('Only bounded scalar facts are accepted')
            if isinstance(v,str) and len(v)>160:raise ValueError('Fact text too long')
            if isinstance(v,float) and not math.isfinite(v):raise ValueError('Nonfinite fact')
        return value


class ReviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    lane: Literal['smart','vanguard','cbe']
    snapshot_at: datetime
    candidates: list[Candidate]=Field(min_length=1,max_length=10)


def output_schema(ids):
    return {'type':'object','additionalProperties':False,'required':['reviews'],'properties':{'reviews':{
        'type':'array','minItems':len(ids),'maxItems':len(ids),'items':{'type':'object','additionalProperties':False,
        'required':['id','verdict','reason'],'properties':{'id':{'type':'string','enum':ids},
        'verdict':{'type':'string','enum':['approve','reject','wait']},'reason':{'type':'string','minLength':1,'maxLength':240}}}}}}


def validate_reviews(value,ids):
    if not isinstance(value,dict) or set(value)!={'reviews'} or not isinstance(value['reviews'],list):raise ValueError('Invalid review envelope')
    rows=value['reviews']
    if len(rows)!=len(ids) or len({r.get('id') for r in rows if isinstance(r,dict)})!=len(ids):raise ValueError('Missing or duplicate review identities')
    for r in rows:
        if not isinstance(r,dict) or set(r)!={'id','verdict','reason'} or r['id'] not in ids or r['verdict'] not in ('approve','reject','wait') or not isinstance(r['reason'],str) or not 1<=len(r['reason'])<=240:
            raise ValueError('Invalid review')
    return rows


def compact_facts(facts):
    """Keep every scalar while sharing repeated group prefixes in model input."""
    out={}
    for key,value in facts.items():
        if '.' in key:
            group,field=key.split('.',1)
            if group not in facts:
                out.setdefault(group,{})[field]=value
                continue
        out[key]=value
    return out


class Reviewer:
    def __init__(self,folder=None,url=None,model=None,transport=None):
        self.url=local_url(url or os.environ.get('OLLAMA_URL','http://host.docker.internal:11434'))
        self.model=model or os.environ.get('OLLAMA_MODEL','qwen3.5:9b')
        if self.model!='qwen3.5:9b':raise ValueError('Only the owner-selected local qwen3.5:9b is enabled')
        self.path=Path(folder or os.environ.get('LOCAL_DECISION_DATABASE','/runtime/local_decisions/reviews.sqlite3'))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.transport=transport;self.lock=asyncio.Lock();self.waiting=0;self.status_cache=None;self.status_at=0.
        self.calls=0;self.hits=0;self.last_error=None
        self.context_lock=asyncio.Lock();self.context_cache={}
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS reviews(context_hash TEXT NOT NULL,at TEXT NOT NULL,lane TEXT NOT NULL,model TEXT NOT NULL,digest TEXT NOT NULL,input TEXT NOT NULL,output TEXT NOT NULL,PRIMARY KEY(context_hash,at))''')
            db.execute('''CREATE TABLE IF NOT EXISTS training_snapshots(context_hash TEXT PRIMARY KEY,at TEXT NOT NULL,lane TEXT NOT NULL,input TEXT NOT NULL)''')

    def db(self):
        return sqlite3.connect(self.path,timeout=5)

    async def availability(self):
        if self.status_cache and time.monotonic()-self.status_at<10:return self.status_cache
        try:
            async with httpx.AsyncClient(transport=self.transport,timeout=4,follow_redirects=False,trust_env=False) as client:
                r=await client.get(self.url+'/api/tags');r.raise_for_status()
                model=next((m for m in r.json().get('models',[]) if m.get('name')==self.model),None)
            result={'ready':bool(model),'model':self.model,'digest':model.get('digest') if model else None,
                    'detail':'Local model available' if model else 'Selected model is not visible to Ollama; no download is attempted'}
        except (httpx.HTTPError,ValueError,TypeError):
            result={'ready':False,'model':self.model,'digest':None,'detail':'Local Ollama is unavailable'}
        self.status_cache=result;self.status_at=time.monotonic();return result

    def fresh(self,request):
        now=datetime.now(UTC)
        if request.snapshot_at.tzinfo is None or not -5<=(now-request.snapshot_at).total_seconds()<=30:
            raise HTTPException(409,'Review input snapshot is stale or has no timezone')
        ids=[r.id for r in request.candidates]
        if len(set(ids))!=len(ids):raise HTTPException(422,'Candidate ids must be unique')
        for row in request.candidates:
            if row.source_at.tzinfo is None:raise HTTPException(422,'Source timestamps need a timezone')
            age=(request.snapshot_at-row.source_at).total_seconds()
            phase=row.facts.get('phase','smart_entry' if request.lane=='smart' else 'fresh_entry_review')
            limit=96*3600 if phase=='next_session_watchlist' and request.lane!='smart' else 15
            if not -5<=age<=limit:raise HTTPException(409,'Candidate source data is stale')

    async def review(self,request):
        self.fresh(request)
        context_url=os.environ.get('LOCAL_CONTEXT_URL','')
        context_failed=False
        if context_url:
            if context_url not in ('http://shadow-lanes:8430','http://127.0.0.1:8430'):
                raise HTTPException(503,'Invalid local market context endpoint')
            if len(request.candidates)!=1:raise HTTPException(413,'Rich market context requires one candidate per review')
            row=request.candidates[0];phase=row.facts.get('phase','smart_entry' if request.lane=='smart' else 'next_session_watchlist')
            key=json.dumps([phase,row.instrument,row.facts.get('underlying'),row.facts.get('expiry'),row.facts.get('strike')],sort_keys=True)
            try:
                async with self.context_lock:
                    saved=self.context_cache.get(key)
                    if saved and time.monotonic()-saved[0]<5:context=saved[1]
                    else:
                        async with httpx.AsyncClient(timeout=6,trust_env=False,follow_redirects=False) as client:
                            r=await client.post(context_url+'/api/decision-context',json={'phase':phase,'candidates':[row.model_dump(mode='json')]},headers={'X-Macd-Token':os.environ.get('MACD_API_TOKEN','')})
                            r.raise_for_status();context=r.json()['contexts'][row.id]
                        self.context_cache={key:(time.monotonic(),context)}
                # Original signal/risk facts win; providers cannot overwrite reviewed entry terms.
                notes=SMART_FEATURE_NOTES if request.lane=='smart' else {}
                if request.lane!='smart':
                    notes={'execution_quote_required_for_this_review':phase!='next_session_watchlist',
                        'market_closed_expected_for_phase':phase=='next_session_watchlist',
                        'cash_spot_lane':request.lane=='cbe','option_greeks_required':request.lane=='vanguard'}
                merged={**context,**row.facts,**notes};row.facts=Candidate.bounded_facts(merged)
            except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
                code='context_validation' if isinstance(exc,ValueError) else 'context_http' if isinstance(exc,httpx.HTTPError) else 'context_schema'
                self.last_error=code
                row.facts={**row.facts,'context_status':'unavailable','context_error':code};context_failed=True
        # Capture the exact observed inputs even when the model is absent, fails or vetoes.
        snapshot=json.dumps({'request':request.model_dump(mode='json'),'system':SYSTEM,'system_sha256':SYSTEM_SHA256},sort_keys=True,allow_nan=False)
        with self.db() as db:db.execute('INSERT OR IGNORE INTO training_snapshots VALUES(?,?,?,?)',
            (hashlib.sha256(snapshot.encode()).hexdigest(),datetime.now(UTC).isoformat(),request.lane,snapshot))
        if context_failed:raise HTTPException(503,'Shared market context unavailable; new entries wait')
        if training_busy():raise HTTPException(503,'Native research training is active; new model-reviewed entries wait')
        if self.waiting>=4:raise HTTPException(503,'Local review queue is full; new entries wait')
        self.waiting+=1
        try:
            # One loaded inference across both books and Vanguard; no concurrent model allocations.
            async with asyncio.timeout(75):
                async with self.lock:
                    status=await self.availability()
                    if not status['ready']:raise HTTPException(503,status['detail'])
                    model_ids=['candidate'] if context_url else [r.id for r in request.candidates]
                    model_rows=[{**r.model_dump(mode='json'),'id':identity} for r,identity in zip(request.candidates,model_ids)]
                    if request.lane in ('vanguard','cbe'):
                        for r in model_rows:r['facts']=compact_facts(r['facts'])
                    context={'policy':'qwen35-review-v1','lane':request.lane,'candidates':model_rows}
                    def bind(value):
                        return {**value,'reviews':[{**value['reviews'][0],'id':request.candidates[0].id}]} if context_url else value
                    body=json.dumps(context,sort_keys=True,allow_nan=False,separators=(',',':'))
                    if len(body)>20000:raise HTTPException(413,'Review context exceeds its local budget')
                    key=hashlib.sha256((str(status['digest'])+SYSTEM_SHA256+body).encode()).hexdigest()
                    with self.db() as db:saved=db.execute('SELECT output FROM reviews WHERE context_hash=? ORDER BY at DESC LIMIT 1',(key,)).fetchone()
                    if saved:
                        result=json.loads(saved[0])
                        if datetime.fromisoformat(result['expires_at'])>datetime.now(UTC):
                            self.hits+=1;return bind({**result,'cache_hit':True})
                    self.fresh(request)
                    payload={'model':self.model,'stream':False,'think':False,'keep_alive':0,
                             'options':{'temperature':0,'num_ctx':8192 if len(body)>6000 else 4096,'num_predict':640},
                             'format':output_schema(model_ids),
                             'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':body}]}
                    started=time.monotonic()
                    async with httpx.AsyncClient(transport=self.transport,timeout=65,follow_redirects=False,trust_env=False) as client:
                        r=await client.post(self.url+'/api/chat',json=payload);r.raise_for_status();response=r.json()
                    if response.get('done') is not True or response.get('done_reason')=='length':raise ValueError('Incomplete model response')
                    reviews=validate_reviews(json.loads(response['message']['content']),model_ids)
                    at=datetime.now(UTC)
                    result={'reviews':reviews,'model':self.model,'digest':status['digest'],'context_hash':key,
                            'purpose':'validation_only' if request.candidates[0].facts.get('validation_only') else request.candidates[0].facts.get('phase','entry'),
                            'created_at':at.isoformat(),'expires_at':(at+timedelta(seconds=30)).isoformat(),
                            'latency_ms':round((time.monotonic()-started)*1000),'cache_hit':False,'policy':'qwen35-review-v1'}
                    result=bind(result)
                    with self.db() as db:db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?)',
                        (key,at.isoformat(),request.lane,self.model,status['digest'],body,json.dumps(result)))
                    self.calls+=1;self.last_error=None;return result
        except HTTPException:raise
        except (TimeoutError,httpx.HTTPError,ValueError,KeyError,TypeError,json.JSONDecodeError):
            self.last_error='Local model timed out or returned an invalid review; new entries wait'
            raise HTTPException(503,self.last_error) from None
        finally:self.waiting-=1


def create_app(reviewer=None):
    worker=reviewer or Reviewer();app=FastAPI(title='Local Qwen decision reviews');app.state.reviewer=worker
    def auth(x_macd_token:str=Header(default='')):
        if os.environ.get('MACD_API_TOKEN','') and x_macd_token!=os.environ['MACD_API_TOKEN']:raise HTTPException(401,'Invalid API token')

    @app.get('/health')
    async def health():
        return {'ok':True,**await worker.availability()}

    @app.get('/status')
    async def status(x_macd_token:str=Header(default='')):
        auth(x_macd_token)
        with worker.db() as db:
            rows=db.execute('SELECT at,lane,output FROM reviews ORDER BY at DESC LIMIT 5').fetchall()
            captures=dict(db.execute('SELECT lane,count(*) FROM training_snapshots GROUP BY lane').fetchall())
            lane_stats={name:{'completed':0,'approve':0,'reject':0,'wait':0,'validation_only':0,'context_failures':0} for name in ('smart','vanguard','cbe')}
            for lane,body in db.execute('SELECT lane,output FROM reviews'):
                result=json.loads(body)
                if result.get('purpose')=='validation_only':lane_stats[lane]['validation_only']+=1;continue
                lane_stats[lane]['completed']+=1
                for verdict in result['reviews']:lane_stats[lane][verdict['verdict']]+=1
            for lane,body in db.execute('SELECT lane,input FROM training_snapshots'):
                facts=json.loads(body)['request']['candidates'][0]['facts']
                if facts.get('context_status')=='unavailable':lane_stats[lane]['context_failures']+=1
        try:training=json.loads(Path('/runtime/local_decisions/training_manifest.json').read_text())
        except (OSError,ValueError):training={'status':'base_model_only','deployed_weight_finetune':False}
        return {**await worker.availability(),'mode':'filter','smart_new_entries_only':True,'training_busy':training_busy(),
                'vanguard_shadow_only':os.environ.get('PAPER_LANES_ENABLED','false')!='true','paper_lanes_only':True,
                'queue':worker.waiting,'inferences':worker.calls,'cache_hits':worker.hits,'last_error':worker.last_error,
                'training':training,'captured_input_snapshots':captures,'lane_stats':lane_stats,
                'recent':[{'at':a,'lane':lane,**json.loads(output)} for a,lane,output in rows]}

    @app.post('/review')
    async def review(body:ReviewInput,x_macd_token:str=Header(default='')):
        auth(x_macd_token);return await worker.review(body)
    return app


if __name__=='__main__':
    import uvicorn
    uvicorn.run(create_app(),host='0.0.0.0',port=8435)
