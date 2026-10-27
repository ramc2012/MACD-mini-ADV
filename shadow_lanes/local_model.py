"""Qwen filters fresh Vanguard/CBE candidate cohorts; paper admission is separate."""
from datetime import datetime, timezone
import hashlib
import math
import os
from urllib.parse import urlparse

import httpx


def apply_review(result,now,lane='vanguard'):
    mode=os.environ.get('LOCAL_DECISION_MODE','off')
    if mode=='off':return result
    url=os.environ.get('LOCAL_DECISION_URL','').rstrip('/')
    parsed=urlparse(url)
    if mode not in ('filter','shadow') or parsed.scheme!='http' or parsed.hostname not in ('local-decisions','127.0.0.1','localhost') or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Invalid local decision endpoint or mode')
    rows=result.get('watchlist',[])
    if result.get('status')!='ok' or not rows:return result
    identities={}
    candidates=[]
    for row in rows:
        identity=hashlib.sha256(f"{lane}:{result['source_session']}:{row['instrument']}:{row.get('horizon_sessions')}".encode()).hexdigest()[:24]
        if identity in identities:raise ValueError('Duplicate candidate identity')
        identities[identity]=row
        fields=['symbol','rank','horizon_sessions','direction_score','contract_score','combined_score',
                'expected_net_return','option_volume','option_oi','contract_kind','strike','expiry','conviction',
                'momentum_fast','momentum_slow','relative_momentum','score','direction',
                'ema20','ema50','macd_line','macd_signal','macd_hist','rsi_14']
        facts={k:row[k] for k in fields if k in row and (isinstance(row[k],(str,int,bool)) or isinstance(row[k],float) and math.isfinite(row[k]))}
        facts.update(phase='next_session_watchlist',underlying=row.get('symbol') or row.get('instrument'))
        candidates.append({'id':identity,'instrument':row['instrument'],'side':row.get('option_type') or 'LONG',
                           'reference_price':row['source_mark'],'source_at':str(row['source_mark_ts']),'facts':facts})
    # Each candidate gets its complete context within the small local model's window.
    # Watchlist reviews are observations made individually; tomorrow's entries require separate fresh reviews.
    decisions=[];receipts=[]
    for candidate in candidates:
        response=httpx.post(url+'/review',json={'lane':lane,'snapshot_at':datetime.now(timezone.utc).isoformat(),'candidates':[candidate]},
                            headers={'X-Macd-Token':os.environ.get('MACD_API_TOKEN','')},timeout=80,follow_redirects=False,trust_env=False)
        response.raise_for_status();receipt=response.json();rows=receipt.get('reviews',[])
        if len(rows)!=1 or rows[0].get('id')!=candidate['id']:raise ValueError('Review identities do not match the frozen candidates')
        if datetime.fromisoformat(receipt['expires_at'])<=datetime.now(timezone.utc):raise ValueError('Expired local review')
        decisions.extend(rows);receipts.append(receipt)
    review={**receipts[-1],'reviews':decisions,'candidate_receipts':receipts,'phase':'next_session_watchlist',
            'not_an_entry_approval':True}
    selected=[]
    for decision in decisions:
        if decision['verdict'] not in ('approve','reject','wait'):raise ValueError('Invalid local verdict')
        if mode=='shadow' or decision['verdict']=='approve':
            selected.append({**identities[decision['id']],'local_review':{**decision,'model':review['model'],
                'digest':review['digest'],'context_hash':next(r['context_hash'] for r in receipts if r['reviews'][0]['id']==decision['id']),
                'created_at':next(r['created_at'] for r in receipts if r['reviews'][0]['id']==decision['id'])}})
    return {**result,'watchlist':selected,'status':'ok' if selected else 'blocked',
            'blockers':[] if selected else [f'Qwen did not approve an eligible {lane} candidate'],
            'policy':result['policy']+('-qwen35-filter-v1' if mode=='filter' else '-qwen35-shadow-v1'),
            'local_review':review}
