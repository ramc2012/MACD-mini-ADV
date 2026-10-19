"""Freeze the archive/catalog universe and export minute histories read-only."""
import datetime as dt
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
INPUT=ROOT.parent
WORK=INPUT.parents[1]
OLD=Path('/Users/ramachandran/CLAUDE PROJECTS/MACD mini/runtime')
sys.path.insert(0,str(INPUT))
from prepare import export_source,option_parts

def main():
    catalog={};maps={};sourcefiles={}
    for label,path in [('old',OLD/'atm_contracts.json'),('current',WORK/'runtime/atm_contracts.json')]:
        payload=path.read_bytes();d=json.loads(payload);sourcefiles[label]={'file':'atm_contracts.json','sha256':hashlib.sha256(payload).hexdigest(),'snapshot_date':d['date'],'contracts':len(d['contracts'])}
        for c in d['contracts']:
            maps[c['underlying']]=c['spot_symbol']
            if c['symbol'] in catalog:
                prior=catalog[c['symbol']]
                if prior['expiry']!=c['expiry']:raise ValueError('Catalog expiry conflict '+c['symbol'])
            catalog[c['symbol']]=c
    with gzip.open(INPUT/'daily_mark_cache.json.gz','rt') as f:expiries=json.load(f)['expiries']
    for name in ['old_book_source.json.gz','current_book_source.json.gz']:
        with gzip.open(INPUT/name,'rt') as f:d=json.load(f)
        for t in d['trades']:
            s=t['symbol']
            if s in catalog:continue
            p=option_parts(s)
            if expiries.get(s):catalog[s]={'symbol':s,'underlying':p['root'],'spot_symbol':maps[p['root']],'expiry':expiries[s],'strike':p['strike'],'option_type':p['side'],'lot_size':t['lot_size']}
    contracts={};exclusions=[]
    for sym,c in catalog.items():
        if c['expiry']<'2026-09-01':continue
        if int(c.get('lot_size') or 0)<1:exclusions.append({'symbol':sym,'reason':'unknown lot'});continue
        p=option_parts(sym);p.update({'spot_symbol':c['spot_symbol'],'catalog_expiry':c['expiry'],'lot_size':int(c['lot_size'])})
        contracts[sym]=p
    (ROOT/'contracts.json').write_text(json.dumps(contracts,indent=2))
    wanted=sorted(set(contracts)|{c['spot_symbol'] for c in contracts.values()}|{'NSE:NIFTY50-INDEX'})
    start=int(dt.datetime(2026,8,1,tzinfo=dt.UTC).timestamp());end=int(dt.datetime(2026,10,2,tzinfo=dt.UTC).timestamp())
    with open(ROOT/'old_minutes.jsonl.gz','wb') as f:meta=export_source(str(OLD/'historical.sqlite3'),wanted,start,end,f)
    code='import datetime as dt,gzip,json,sqlite3,sys\n'+inspect.getsource(export_source)+'\nexport_source('+repr('/app/data/historical.sqlite3')+','+repr(wanted)+','+str(start)+','+str(end)+',sys.stdout.buffer)\n'
    with open(ROOT/'current_minutes.jsonl.gz','wb') as f:
        child=subprocess.Popen(['docker','exec','-i','macd-mini-parallel-engine-1','python','-'],stdin=subprocess.PIPE,stdout=f,text=True);child.communicate(code)
        if child.returncode:raise RuntimeError('Current read-only export failed')
    receipt={'read_at_utc':dt.datetime.now(dt.UTC).isoformat(),'source_catalogs':sourcefiles,'contract_count':len(contracts),'requested_series':len(wanted),'exclusions':exclusions,'old_metadata':meta,'precedence':'Current archive first for Smart reconstruction; old archive fills missing timestamps. Wider full-session coverage, frozen before outcomes.','selection':'Daily selection is reconstructed from the first completed opening spot minute and previous-session option volume, zero unknown OI. Same side-rounding and 1.5x liquidity function; nearest unexpired series. No daily historical chain exists.','files':{}}
    for n in ['old_minutes.jsonl.gz','current_minutes.jsonl.gz','contracts.json']:
        p=ROOT/n
        with p.open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
        receipt['files'][n]={'sha256':h,'bytes':p.stat().st_size}
    (ROOT/'smart_source_receipt.json').write_text(json.dumps(receipt,indent=2));print('Smart read-only universe:',len(contracts),'contracts,',len(wanted),'series',flush=True)

if __name__=='__main__':main()
