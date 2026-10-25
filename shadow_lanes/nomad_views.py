"""Read-only adapters for Nomad's original desks and fresh MACD observations."""
from datetime import datetime
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .nomad_read import fetch_all


def install(api, store, authorize):
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
                overrides = {
                    'paper': {'account':None,'shadow_only':True, **snap},
                    'book': {'open_positions':[],'closed':[],'equity_curve':[],'shadow_only':True},
                    'readiness': {**readiness,'selection_allowed':False,'publication_allowed':False,
                                  'observed_at':snap.get('checked_at'),'detail':'Fresh MACD observations; execution disabled'},
                    'summary': {'paper_only':True,'shadow_only':True,'latest_timing_bar':None,'activity':{},'thresholds':{},'capital':None},
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
        snap=store.snapshot('cbe'); latest=(snap.get('publications') or [None])[0]
        if scope=='retained':
            raise HTTPException(503,'The original CBE scan dataset was outside the recovery backup; its paper book remains available')
        rows=(latest or {}).get('watchlist') or []
        result=[{**r,'composite_alpha_score':r.get('score'),'directional_bias':'bullish','gate_passed':True,
                 'is_watchlist':True,'sector_code':r.get('sector'),'details':{'stock_selection':r}} for r in rows]
        return {'shadow_only':True,'config':{'timeframe':'daily'},'source':'MACD prospective shadow',
                'scan_date':(latest or {}).get('source_session'),'created_at':(latest or {}).get('decision_at'),
                'results':result,'watchlist':result,'watchlist_count':len(result),'scored_count':len(result),
                'readiness':snap.get('readiness')}

    @router.get('/cbe/paper-summary')
    def cbe_summary(scope:str='shadow'):
        if scope=='retained':return summary(book())
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
