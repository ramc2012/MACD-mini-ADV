"""Versioned local paper models: no LLM, broker/order interface or outcome input.

Scores rank already eligible candidates. They are not profit probabilities.
Stale/missing optional context is neutral and explicitly disclosed.
"""
from datetime import timedelta
from pathlib import Path
import hashlib,json,math

VERSION='lane-local-context-v1'
WEIGHTS={'cbe':{'daily':.35,'profile':.20,'sector':.10,'continuation':.15,'source_rank':.10,'positioning':.10},
         'vanguard':{'daily':.15,'profile':.20,'sector':.15,'continuation':.15,'source_rank':.15,'timing':.10,'positioning':.10}}
DIGEST=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def number(v):
    return float(v) if isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v) else None

def align(value,scale):
    v=number(value)
    return .5 if v is None else .5+.5*math.tanh(v/scale)

def usable(f,group):
    return f.get(group+'.status') in ('observed','prior_session','completed_session_context','observed_disclosures')

def assess(lane,candidate,f):
    side=1 if candidate['side'] in ('CE','LONG') else -1
    signal=candidate.get('facts',{})
    warnings=[k.removesuffix('.status')+': '+str(v) for k,v in f.items() if k.endswith('.status') and not usable(f,k.removesuffix('.status'))]
    verdict,reason='approve','Eligible source signal and fresh execution checks; local context ranks admission'
    if lane=='cbe':
        fields=[number(f.get('daily.'+k)) for k in ('ema20','ema50','momentum_fast','momentum_slow','relative_momentum')]
        if f.get('daily.status')!='completed_session_context' or any(v is None for v in fields):
            verdict,reason='wait','Completed daily indicators unavailable; no favorable substitution'
        elif f.get('daily.source_session')!=signal.get('source_session'):
            verdict,reason='wait','Daily source session does not match the frozen cash candidate'
        elif not usable(f,'profile') or f.get('profile.event_date')!=signal.get('source_session') or any(number(f.get('profile.'+k)) is None for k in ('poc','vah','val')):
            verdict,reason='wait','Completed market profile must match the frozen cash session'
        elif fields[0]<=fields[1] or any(v<=0 for v in fields[2:]):
            verdict,reason='reject','Completed daily EMA/momentum/relative strength no longer supports the cash-long candidate'
        elif candidate['reference_price']<=fields[0]:
            verdict,reason='wait','Current cash price is below EMA20; wait within the original window'
    else:
        if candidate['side'] not in ('CE','PE'):
            verdict,reason='reject','Vanguard requires an exact CE/PE option candidate'
        elif f.get('risk.fno_banned') is True:
            verdict,reason='reject','Published F&O ban prevents new option exposure'
        elif any(f.get(g+'.status')!='observed' or number(f.get(g+'.age_hours')) is None or not 0<=f[g+'.age_hours']<=1 for g in ('regime','timing')):
            verdict,reason='wait','Current completed-bar regime and timing observations required'
        elif not all(number(signal.get(k)) is not None for k in ('current_underlying_bid','current_underlying_ask','underlying_signal_close')):
            verdict,reason='wait','Fresh underlying and frozen signal close required for ATM continuation'
    latest,ema=number(f.get('daily.latest_close')),number(f.get('daily.ema20'))
    daily_votes=[]
    if usable(f,'daily'):
        if latest and ema:daily_votes.append(align(side*(latest/ema-1),.02))
        histogram=number(f.get('daily.macd_hist'));rsi=number(f.get('daily.rsi_14'));relative=number(f.get('daily.relative_momentum'))
        if histogram is not None and latest:daily_votes.append(align(side*histogram/latest,.005))
        if rsi is not None:daily_votes.append(align(side*(rsi-50),20.))
        if relative is not None:daily_votes.append(align(side*relative,.05))
    daily=sum(daily_votes)/len(daily_votes) if daily_votes else .5
    mid=(signal['current_underlying_bid']+signal['current_underlying_ask'])/2 if all(number(signal.get(k)) for k in ('current_underlying_bid','current_underlying_ask')) else candidate['reference_price'] if lane=='cbe' else None
    poc=number(f.get('profile.poc'))
    profile=align(side*(mid/poc-1),.01) if usable(f,'profile') and mid and poc else .5
    sector=align(side*f['sector.rs_z20'],1.) if usable(f,'sector') and number(f.get('sector.rs_z20')) is not None else .5
    base=number(signal.get('underlying_signal_close') if lane=='vanguard' else signal.get('signal_close'))
    continuation=align(side*(mid/base-1),.01) if mid and base else .5
    rank=number(signal.get('rank'));source_rank=max(0.,min(1.,1-(rank-1)/10)) if rank else .5
    timing=max(0.,min(1.,f['timing.timing_score']/100)) if usable(f,'timing') and number(f.get('timing.timing_score')) is not None else .5
    state=f.get('positioning.oi_state') if usable(f,'positioning') else None
    positioning=.5+.25*side*{'long_buildup':1,'short_cover':1,'short_buildup':-1,'long_unwind':-1}.get(state,0)
    components=dict(daily=daily,profile=profile,sector=sector,continuation=continuation,source_rank=source_rank,timing=timing,positioning=positioning)
    priority=sum(components[k]*weight for k,weight in WEIGHTS[lane].items())
    return {'id':candidate['id'],'verdict':verdict,'reason':reason,'priority':round(priority,6),
            'components':components,'weights':WEIGHTS[lane],'warnings':warnings,'policy':VERSION,
            'basis':'untrained context model; ordinal priority, not expected return',
            'feature_groups':{k:v for k,v in f.items() if k.endswith('.status')}}

def review(lane,candidates,contexts,now):
    if lane not in WEIGHTS or not 1<=len(candidates)<=10:raise ValueError('Bounded known lane required')
    if {r['id'] for r in candidates}!=set(contexts):raise ValueError('Exact current context identities required')
    rows=[assess(lane,r,contexts[r['id']]) for r in candidates]
    digest=hashlib.sha256(json.dumps({'lane':lane,'candidates':candidates,'contexts':contexts},sort_keys=True,default=str,allow_nan=False).encode()).hexdigest()
    return {'reviews':rows,'model':VERSION+'-'+lane,'digest':DIGEST,'context_hash':digest,
            'created_at':now.isoformat(),'expires_at':(now+timedelta(seconds=30)).isoformat(),'decision_engine':'local'}

def status(lane):
    return {'policy':VERSION,'model':VERSION+'-'+lane,'digest':DIGEST,'qwen_required':False,'mode':'local',
            'weights':WEIGHTS[lane],'validation':'Untrained context baseline; prospective profitability unproven'}
