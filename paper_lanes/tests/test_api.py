import ast
import asyncio
from datetime import datetime
from pathlib import Path

import httpx

from paper_lanes.manager import Manager,IST
from shadow_lanes.store import ShadowStore
from shadow_lanes.service import create_app


def test_paper_api_strict_controls_and_auth(tmp_path,monkeypatch):
    monkeypatch.setenv('MACD_API_TOKEN','isolated-test-token')
    manager=Manager(tmp_path/'paper',now=datetime(2026,10,25,21,tzinfo=IST))
    app=create_app(ShadowStore(tmp_path/'observations.sqlite3'),worker_enabled=False,paper_manager=manager)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            assert (await c.get('/api/paper-lanes/cbe')).status_code==401
            headers={'X-Macd-Token':'isolated-test-token'}
            for lane in ('cbe','vanguard'):
                s=(await c.get('/api/paper-lanes/'+lane,headers=headers)).json()
                assert s['enabled'] and s['mode']=='paper' and s['equity']==1000000 and not s['live_orders_possible']
                for bad in ({'action':'live'},{'action':'reset'},{'action':'pause','cash':200}):
                    assert (await c.post(f'/api/paper-lanes/{lane}/control',headers=headers,json=bad)).status_code==400
                assert (await c.post(f'/api/paper-lanes/{lane}/control',headers=headers,json={'action':'pause'})).json()['exits_remain_managed']
                assert (await c.get('/api/paper-lanes/'+lane,headers=headers)).json()['paused']
                await c.post(f'/api/paper-lanes/{lane}/control',headers=headers,json={'action':'resume'})
                assert not (await c.get('/api/paper-lanes/'+lane,headers=headers)).json()['paused']
            assert (await c.post('/api/paper-lanes/cbe/orders',headers=headers,json={})).status_code==404
    asyncio.run(check())


def test_gateway_new_routes_keep_existing_authorization():
    path=Path(__file__).resolve().parents[2]/'engine/src/macd_trader/app.py'
    tree=ast.parse(path.read_text())
    routes=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and any('/api/paper-lanes/' in ast.unparse(d) for d in n.decorator_list)]
    assert len(routes)==2
    assert all(any('Depends(authorize)' in ast.unparse(d) for d in n.decorator_list) for n in routes)


def test_one_book_cycle_failure_does_not_prevent_other_exits(tmp_path,monkeypatch):
    class Market:
        def quotes(self,keys):return {}
    m=Manager(tmp_path,now=datetime(2026,10,25,21,tzinfo=IST),market=Market());seen=[]
    def fail(*args):raise ValueError('isolated malformed entry')
    monkeypatch.setattr(m.books['vanguard'],'cycle',fail)
    monkeypatch.setattr(m.books['cbe'],'cycle',lambda *args:seen.append(True))
    m.poll(datetime(2026,10,27,9,21,tzinfo=IST))
    assert seen and 'vanguard' in m.last_error


def test_combined_quotes_use_one_shared_hub_request_and_clear_subscriptions(monkeypatch):
    import json
    from paper_lanes.manager import MarketData
    requests=[]
    def handler(request):
        body=json.loads(request.content);requests.append((request.url.path,body))
        return httpx.Response(200,json={'status':'success','data':{}})
    client=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    m=MarketData();m.quotes({'NSE_FO|A','NSE_EQ|B'});m.quotes({'NSE_FO|A','NSE_EQ|B'});m.quotes(set())
    assert [r[0] for r in requests]==['/v1/subscriptions','/v1/request','/v1/request','/v1/subscriptions']
    assert requests[1][1]['query']['instrument_key']=='NSE_EQ|B,NSE_FO|A'
    assert requests[1][1]['endpoint']=='market-quote/quotes' and requests[1][1]['ttl_ms']==1000
    assert requests[-1][1]['upstox']==[]


def test_new_cbe_cohort_flows_into_only_its_new_account(tmp_path,monkeypatch):
    from shadow_lanes import service,source,local_model
    now=datetime(2026,10,26,15,20,tzinfo=IST)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return now
    class Connection:
        def close(self):pass
    class Market:
        def metadata(self,lane,rows):
            assert lane=='cbe'
            return {'ABC':dict(instrument='NSE_EQ|ABC',symbol='ABC',lot_size=1,tick=.01)}
    monkeypatch.setenv('LOCAL_DECISION_MODE','filter')
    monkeypatch.setattr(service,'datetime',Clock)
    monkeypatch.setattr(source,'connect',lambda **kw:Connection())
    monkeypatch.setattr(source,'cbe',lambda *args:dict(status='ok',policy='isolated',blockers=[],source_session='2026-10-26',
        watchlist=[dict(instrument='ABC',symbol='ABC',source_mark=100,source_mark_ts=now.isoformat(),horizon_sessions=1)]))
    monkeypatch.setattr(source,'observe_outcomes',lambda *args:[])
    monkeypatch.setattr(local_model,'apply_review',lambda result,at,lane:{**result,'local_review':{'fixture_only':True}})
    m=Manager(tmp_path/'books',now=datetime(2026,10,25,21,tzinfo=IST),market=Market())
    store=ShadowStore(tmp_path/'observations.sqlite3');worker=service.ShadowWorker(store,m)
    worker.check(now);worker.check(now)
    pub=store.publications('cbe')[0]
    assert pub['paper_account_id']==m.books['cbe'].account_id
    assert 'fresh next-session Qwen' in pub['watchlist'][0]['actionable_reason']
    assert len(m.snapshot('cbe')['orders'])==1 and not m.snapshot('vanguard')['orders']
    assert not m.snapshot('cbe')['fills'] and pub['shadow_only'] # selection journal is not an execution journal
