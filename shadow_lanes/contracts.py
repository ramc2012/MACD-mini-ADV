"""Current exact identities from the hub's loaded master, never historical token joins.

Broker tokens can be reused after expiry. All fresh candidates must match the
complete tuple. The retained catalog and historical observations are untouched.
"""
from datetime import datetime
import os
import httpx
from .source import IST


def hub_url():
    url=os.environ.get('PAPER_HUB_URL','http://host.docker.internal:8400').rstrip('/')
    if url not in ('http://hub:8400','http://host.docker.internal:8400','http://127.0.0.1:8400'):
        raise ValueError('Current reference must use the local hub')
    return url


def resolve(keys):
    keys=sorted(set(keys))
    if not keys:return {}
    if len(keys)>20000:raise ValueError('Bounded current contract lookup required')
    with httpx.Client(timeout=10,trust_env=False,follow_redirects=False) as h:
        r=h.post(hub_url()+'/v1/resolve/batch',json={'upstox':keys});r.raise_for_status()
        return r.json()


def expiry(meta):
    return str(datetime.fromtimestamp(meta['expiry_ms']/1000,IST).date()) if meta.get('expiry_ms') else None


def matches(row,meta):
    return bool(meta and meta.get('upstox')==row.get('instrument') and meta.get('underlying')==row.get('symbol')
        and expiry(meta)==str(row.get('expiry')) and meta.get('option_type')==row.get('option_type')
        and float(meta.get('strike') or 0)==float(row.get('strike') or 0)
        and (meta.get('lot_size') or 0)>0 and (meta.get('tick_size') or 0)>0)


def verify_candidates(rows):
    master=resolve([r['instrument'] for r in rows])
    return [{**r,'lot_size':master[r['instrument']]['lot_size'],'tick_size':master[r['instrument']]['tick_size'],
             'canonical':master[r['instrument']]['canonical'],'identity_source':'current_hub_master'}
            for r in rows if matches(r,master.get(r['instrument']))]


def option_rows(symbol):
    with httpx.Client(timeout=10,trust_env=False,follow_redirects=False) as h:
        r=h.get(hub_url()+'/v1/options',params={'underlying':symbol,'exchange':'NSE','expiries':3})
        r.raise_for_status();return r.json()
