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
                'momentum_fast','momentum_slow','relative_momentum','score','direction']
        facts={k:row[k] for k in fields if k in row and (isinstance(row[k],(str,int,bool)) or isinstance(row[k],float) and math.isfinite(row[k]))}
        candidates.append({'id':identity,'instrument':row['instrument'],'side':row.get('option_type') or 'LONG',
                           'reference_price':row['source_mark'],'source_at':str(row['source_mark_ts']),'facts':facts})
    response=httpx.post(url+'/review',json={'lane':lane,'snapshot_at':now.isoformat(),'candidates':candidates},
                        headers={'X-Macd-Token':os.environ.get('MACD_API_TOKEN','')},timeout=80,follow_redirects=False,trust_env=False)
    response.raise_for_status();review=response.json();decisions=review.get('reviews',[])
    if len(decisions)!=len(identities) or {r.get('id') for r in decisions}!=set(identities):raise ValueError('Review identities do not match the frozen candidates')
    if datetime.fromisoformat(review['expires_at'])<=datetime.now(timezone.utc):raise ValueError('Expired local review')
    selected=[]
    for decision in decisions:
        if decision['verdict'] not in ('approve','reject','wait'):raise ValueError('Invalid local verdict')
        if mode=='shadow' or decision['verdict']=='approve':
            selected.append({**identities[decision['id']],'local_review':{**decision,'model':review['model'],
                'digest':review['digest'],'context_hash':review['context_hash'],'created_at':review['created_at']}})
    return {**result,'watchlist':selected,'status':'ok' if selected else 'blocked',
            'blockers':[] if selected else [f'Qwen did not approve an eligible {lane} candidate'],
            'policy':result['policy']+('-qwen35-filter-v1' if mode=='filter' else '-qwen35-shadow-v1'),
            'local_review':review}
