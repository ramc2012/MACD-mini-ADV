"""Transport for local entry reviews. No credentials, model tools, broker or exit interface."""
from datetime import UTC, datetime
import hashlib
import math
import os
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import httpx


def configuration():
    mode=os.environ.get('LOCAL_DECISION_MODE','off')
    url=os.environ.get('LOCAL_DECISION_URL','').rstrip('/')
    if mode not in ('off','filter','shadow'):raise ValueError('Invalid local decision mode')
    p=urlparse(url)
    if mode!='off' and (p.scheme!='http' or p.hostname not in ('local-decisions','localhost','127.0.0.1') or p.username or p.password or p.path or p.query or p.fragment):
        raise ValueError('Local review service must be a fixed local endpoint')
    return mode,url


def scalar_facts(values):
    return {k:v for k,v in values.items() if v is None or isinstance(v,(bool,int)) or
            isinstance(v,float) and math.isfinite(v) or isinstance(v,str) and len(v)<=160}


async def review_smart(item,price,quote_at):
    mode,url=configuration()
    identity=hashlib.sha256(f'{item.source}:{item.symbol}:{item.bucket if item.bucket is not None else item.signal.timestamp.isoformat()}'.encode()).hexdigest()[:24]
    local=item.signal.timestamp.astimezone(ZoneInfo('Asia/Kolkata'))
    facts=scalar_facts({'source':item.source,'ranker_score':item.score,
        'signal_basis':'option_premium','signal_action':item.signal.side,'signal_kind':item.signal.kind,
        'signal_signal_definition':'EMA of option-premium MACD; its sign is not a BUY/SELL command or an underlying-price trend',
        'signal_macd':item.signal.macd,'signal_signal':item.signal.signal,'signal_histogram':item.signal.histogram,
        'signal_price':item.signal.price,'minutes_since_open':local.hour*60+local.minute-555})
    for key,value in scalar_facts(item.features).items():
        if key not in facts:facts[key]=value
    facts.update(phase='smart_entry',feature_snapshot_at=item.features.get('features_available_at') or item.signal.timestamp.isoformat(),
                 signal_id=item.signal.signal_id,signal_at=item.signal.timestamp.isoformat())
    payload={'lane':'smart','snapshot_at':datetime.now(UTC).isoformat(),'candidates':[
        {'id':identity,'instrument':item.symbol,'side':item.option_type,'reference_price':price,
         'source_at':quote_at.isoformat(),'facts':facts}]}
    async with httpx.AsyncClient(timeout=80,follow_redirects=False,trust_env=False) as client:
        r=await client.post(url+'/review',json=payload,headers={'X-Macd-Token':os.environ.get('MACD_API_TOKEN','')})
        r.raise_for_status();result=r.json()
    rows=result.get('reviews',[])
    if len(rows)!=1 or rows[0].get('id')!=identity or rows[0].get('verdict') not in ('approve','reject','wait'):
        raise ValueError('Local review identity mismatch')
    if datetime.fromisoformat(result['expires_at'])<=datetime.now(UTC):raise ValueError('Local review expired')
    return {**result,**rows[0],'reference_price':price}
