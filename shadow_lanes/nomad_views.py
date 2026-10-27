"""Read-only adapters for Nomad's original desks and fresh MACD observations."""
from datetime import datetime,date,time
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .nomad_read import fetch_all


def install(api, store, authorize,paper=None):
    from .nomad_vanguard import router as vanguard
    from fastapi import Depends

    @api.middleware('http')
    async def shadow_scope(request: Request, call_next):
        if request.url.path.startswith('/api/nomad/'):
            try:
                authorize(request.headers.get('x-macd-token',''))
            except HTTPException as exc:
                return JSONResponse({'detail':exc.detail}, status_code=exc.status_code)
            scope = request.query_params.get('scope','shadow')
            if scope not in ('shadow','retained'):
                return JSONResponse({'detail':'Unknown data scope'}, status_code=400)
            if request.method != 'GET':
                return JSONResponse({'detail':'The ported desks are read only'}, status_code=405)
            if scope == 'shadow' and request.url.path.startswith('/api/nomad/vanguard/'):
                resource = request.url.path.split('/vanguard/',1)[1]
                snap = store.snapshot('vanguard')
                readiness = snap.get('readiness') or {}
                latest = (snap.get('publications') or [None])[0]
                rows = (latest or {}).get('watchlist') or []
                timing_bar=None
                if resource=='summary':
                    try:
                        values=await fetch_all('SELECT max(ts) AS latest FROM timing WHERE ts<=now()')
                        timing_bar=values[0]['latest'] if values else None
                    except HTTPException:pass
                overrides = {
                    'paper': {'account':None,'shadow_only':True,'macd_paper_only':paper is not None, **snap},
                    'book': {'open_positions':[],'closed':[],'equity_curve':[],'shadow_only':True},
                    'readiness': {**readiness,'selection_allowed':False,'publication_allowed':False,
                                  'observed_at':snap.get('checked_at'),'detail':('New paper entries require current data and Qwen; exits managed separately' if paper else 'Fresh MACD observations; execution disabled')},
                    'summary': {'paper_only':True,'shadow_only':True,'latest_timing_bar':timing_bar,'activity':{},'thresholds':{},'capital':None},
                    'watchlist': {'shadow_only':True,'latest':latest,'items':rows,'history':snap.get('publications',[]),'current_items':rows},
                    'strategy-journals': {'journals':{},'swing':{'latest':latest,'items':rows}},
                    'selection': {'rows':rows,'selected':rows,'shadow_only':True},
                    'stock-selection': {'status':'shadow_only','watchlist':[],'candidate_count':0,'selected_count':0,
                                        'blockers':['Stock funding is disabled for the fresh MACD lane']},
                    'paper-allocations': {'status':'shadow_only','batches':[],'allocations':[]},
                    'observation-followup': {'shadow_only':True,'items':[],'quality':[],'stock_quality':[],'promotion_gates':[], **snap},
                }
                if resource in overrides:
                    return JSONResponse(jsonable_encoder(overrides[resource]))
        return await call_next(request)

    api.include_router(vanguard, dependencies=[Depends(authorize)])
    router = APIRouter(prefix='/api/nomad', dependencies=[Depends(authorize)])

    @router.get('/{lane}/market-context')
    def market_context(lane: str, session: str | None=None):
        from .daily_market import view
        if lane not in ('vanguard','cbe'):raise HTTPException(404,'Unknown lane')
        try:return view(date.fromisoformat(session) if session else None)
        except ValueError:raise HTTPException(422,'Choose an available completed session') from None

    def book():
        path=Path(os.environ.get('LEGACY_CBE_FILE','/legacy/cbe/paper_positions.json'))
        try: return json.loads(path.read_text())
        except (OSError, ValueError): raise HTTPException(503,'Retained CBE book is unavailable') from None

    def summary(data):
        # Preserve recorded financial values; never mark or advance the old book on GET.
        from .nomad_cbe_summary import summarize
        return {**summarize(data),
                'admissions_blocked':True,'performance_note':'Retained Nomad account; frozen history, no live funding or exit management',
                'risk_flags':['retained_read_only']}

    @router.get('/cbe/latest')
    def cbe_latest(scope: str='shadow'):
        snap=store.snapshot('cbe')
        publications=[p for p in snap.get('publications',[]) if not paper or p.get('paper_account_id')==paper.books['cbe'].account_id]
        latest=(publications or [None])[0]
        if scope=='retained':
            raise HTTPException(503,'The original CBE scan dataset was outside the recovery backup; its paper book remains available')
        from .daily_market import view
        from .source import IST
        market=view()
        selected={r['instrument'] for r in (latest or {}).get('watchlist',[])}
        result=[{**{k:v for k,v in r.items() if k!='history'},'composite_alpha_score':r.get('score'),
                 'directional_bias':{'LONG':'bullish','SHORT':'bearish'}.get(r.get('direction'),'neutral'),
                 'gate_passed':r['eligible'],'is_watchlist':r['instrument'] in selected,'sector_code':r.get('sector'),
                 'recent_closes_30d':[b['close'] for b in r['history'][-30:]],
                 'details':{'stock_selection':{k:v for k,v in r.items() if k not in ('history','profile')}}} for r in market['results']]
        return {'shadow_only':paper is None,'config':{'timeframe':'daily'},'source':'MACD prospective paper candidates' if paper else 'MACD prospective shadow',
                'scan_date':market['source_session'],'created_at':market['observed_at'],
                'source_as_of':datetime.combine(date.fromisoformat(market['source_session']),time(15,30),IST).isoformat(),
                'universe_size':market['expected'],
                'results':result,'watchlist':[r for r in result if r['is_watchlist']],
                'watchlist_count':len(selected),'scored_count':len(result),
                'readiness':snap.get('readiness')}

    @router.get('/cbe/paper-summary')
    def cbe_summary(scope:str='shadow'):
        if scope=='retained':return summary(book())
        if paper:
            p=paper.snapshot('cbe')
            return {**p,'shadow_only':False,'macd_paper_only':True,'total_equity':p['equity'],'open_positions':p['open_count'],
                    'closed_positions':p['closed_count'],'admissions_blocked':p['paused']}
        return {'shadow_only':True,'admissions_blocked':True,'open_positions':0,'closed_positions':0,
                'initial_capital':None,'total_equity':None,'realized_pnl':None,'unrealized_pnl':None,'risk_flags':['execution_disabled']}

    @router.get('/cbe/paper-positions')
    def cbe_positions(scope:str='shadow',limit:int=Query(500,ge=1,le=2000)):
        if scope=='retained':
            data=book()
            return {'summary':summary(data),'open_positions':data.get('open_positions') or [],
                    'closed_positions':(data.get('closed_positions') or [])[-limit:],'read_only':True}
        return {'summary':cbe_summary(),'open_positions':[],'closed_positions':[],'shadow_only':True}

    @router.get('/cbe/paper-journal')
    def cbe_journal(scope:str='shadow',limit:int=Query(500,ge=1,le=2000)):
        if scope=='retained':
            path=Path('/legacy/cbe/paper_journal.jsonl'); records=[]
            if path.exists():
                for line in path.read_text().splitlines()[-limit:]:
                    try:records.append(json.loads(line))
                    except ValueError:continue
            return {'count':len(records),'records':records,'read_only':True}
        if paper:
            events=paper.snapshot('cbe')['events'][:limit]
            return {'count':len(events),'records':[{'recorded_at':r['at'],'event':r['kind'],'entry_reason':r['detail'].get('reason') or r['kind'],'order_id':r['order_id']} for r in events]}
        events=store.snapshot('cbe').get('journal',[])
        return {'count':len(events),'records':[{'recorded_at':r['at'],'event':r['kind'],'entry_reason':'Shadow readiness observation; no fill'} for r in events]}

    @router.get('/charts/option-ohlc')
    async def option_chart(underlying:str,expiry:str,strike:float,option_type:str,interval:str='30minute',limit:int=Query(200,ge=1,le=500)):
        if option_type not in ('CE','PE') or interval not in ('5minute','15minute','30minute'):
            raise HTTPException(400,'Unsupported contract identity or interval')
        rows=await fetch_all('''SELECT time,open,high,low,close,volume FROM option_premium_candles
          WHERE underlying=:underlying AND expiry=:expiry AND strike=:strike AND option_type=:side AND interval=:interval
          ORDER BY time DESC LIMIT :limit''',{'underlying':underlying,'expiry':expiry,'strike':strike,'side':option_type,'interval':interval,'limit':limit})
        return {'bars':list(reversed(rows)),'bar_count':len(rows),'detail':'Retained exact-contract candles; indicators not reconstructed'}

    @router.get('/mp/unified/verdicts')
    def mp_verdicts():
        return {'status':'unavailable','verdicts':[],'note':'Old MP verdict history was outside the recovery backup'}

    api.include_router(router)
