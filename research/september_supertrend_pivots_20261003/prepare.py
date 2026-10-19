"""Extract actual paper fills and requested minute histories, read-only."""
import datetime as dt
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parents[1]
OLD = Path('/Users/ramachandran/CLAUDE PROJECTS/MACD mini/runtime')
START = int(dt.datetime(2026,8,1,tzinfo=dt.UTC).timestamp())
END = int(dt.datetime(2026,10,2,tzinfo=dt.UTC).timestamp())


def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''):h.update(block)
    return h.hexdigest()


def option_parts(symbol):
    m=re.fullmatch(r'([^:]+):(.+?)26(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC|[1-9OND]\d{2})(\d+(?:\.\d+)?)(CE|PE)',symbol)
    if not m:raise ValueError('Unparsed traded option '+symbol)
    return {'exchange':m[1],'root':m[2],'expiry_code':m[3],'strike':float(m[4]),'side':m[5]}


def export_source(dbpath, symbols, start, end, output):
    # This function is also sent to Docker verbatim; globals use stdlib only.
    c=sqlite3.connect('file:'+dbpath+'?mode=ro',uri=True,timeout=30)
    c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    meta={'source':dbpath,'read_at_utc':dt.datetime.now(dt.UTC).isoformat(),'query_only':1,'raw_rows':0,'series':0,'empty':[], 'query':'SELECT timestamp,open,high,low,close,volume,asset_type,expiry FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<? ORDER BY timestamp'}
    with gzip.GzipFile(fileobj=output,mode='wb',compresslevel=3) as z:
        for n,symbol in enumerate(symbols):
            bars=[];expiries=set();assets=set()
            for t,o,h,l,close,v,asset,expiry in c.execute(meta['query'],(symbol,start,end)):
                bars.append([t,o,h,l,close,v]);assets.add(asset)
                if expiry:expiries.add(expiry)
            if not bars:meta['empty'].append(symbol);continue
            record={'symbol':symbol,'bars':bars,'assets':sorted(assets),'expiries':sorted(expiries)}
            z.write((json.dumps(record,separators=(',',':'))+'\n').encode())
            meta['raw_rows']+=len(bars);meta['series']+=1
            if n%50==0:print('minute extract',n,'/',len(symbols),meta['raw_rows'],'rows',file=sys.stderr,flush=True)
        z.write((json.dumps({'metadata':meta},separators=(',',':'))+'\n').encode())
    c.rollback();c.close()
    return meta


def main():
    ledger=OLD/'macd_trader.sqlite3'
    before=sha(ledger)
    c=sqlite3.connect('file:'+str(ledger)+'?mode=ro',uri=True)
    c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    old={'read_at_utc':dt.datetime.now(dt.UTC).isoformat(),'source_database':str(ledger),'query_only':1,'source_sha256':before,'quick_check':c.execute('PRAGMA quick_check(1)').fetchone()[0]}
    old['trades']=[json.loads(r[0]) for r in c.execute('SELECT payload FROM trades ORDER BY timestamp,rowid')]
    old['orders']={r[0]:json.loads(r[1]) for r in c.execute('SELECT order_id,payload FROM orders')}
    old['equity_points']=[dict(zip(['timestamp','equity','cash','realized_pnl','unrealized_pnl'],r)) for r in c.execute('SELECT timestamp,equity,cash,realized_pnl,unrealized_pnl FROM equity_points ORDER BY timestamp')]
    c.rollback();c.close()
    assert sha(ledger)==before
    with gzip.open(ROOT/'old_book_source.json.gz','wt') as f:json.dump(old,f,separators=(',',':'))
    # Already verified live ledger capture from the preceding reconciliation.
    with gzip.open(WORK/'research/supertrend_pivots_20261003/paper_book_source.json.gz','rt') as f:
        live=json.load(f)['books']['macd_trader']
    with gzip.open(ROOT/'current_book_source.json.gz','wt') as f:json.dump(live,f,separators=(',',':'))
    settings=json.loads((OLD/'settings.json').read_text())
    safe={k:v for k,v in settings.items() if not any(x in k.lower() for x in ['token','secret','password','credential','client_id'])}
    (ROOT/'old_settings.json').write_text(json.dumps(safe,indent=2))
    catalog={};rootmaps={}
    for path in [WORK/'runtime/atm_contracts.json',OLD/'atm_contracts.json']:
        for r in json.loads(path.read_text())['contracts']:
            catalog[r['symbol']]=r;rootmaps[r['underlying']]=r['spot_symbol']
    request=sorted({t['symbol'] for t in old['trades']+live['trades']})
    contracts={}
    for symbol in request:
        p=option_parts(symbol)
        if p['root'] not in rootmaps:raise ValueError('No authoritative spot map for '+symbol)
        p['spot_symbol']=rootmaps[p['root']]
        if symbol in catalog:p['catalog_expiry']=catalog[symbol]['expiry']
        contracts[symbol]=p
    symbols=sorted(set(request)|{v['spot_symbol'] for v in contracts.values()})
    (ROOT/'contracts.json').write_text(json.dumps(contracts,indent=2))
    with open(ROOT/'old_minutes.jsonl.gz','wb') as f:
        oldmeta=export_source(str(OLD/'historical.sqlite3'),symbols,START,END,f)
    code='import datetime as dt,gzip,json,sqlite3,sys\n'+inspect.getsource(export_source)+'\n'
    code+='export_source('+repr('/app/data/historical.sqlite3')+','+repr(symbols)+','+str(START)+','+str(END)+',sys.stdout.buffer)\n'
    with open(ROOT/'current_minutes.jsonl.gz','wb') as f:
        child=subprocess.Popen(['docker','exec','-i','macd-mini-parallel-engine-1','python','-'],stdin=subprocess.PIPE,stdout=f,text=True)
        child.communicate(code)
        if child.returncode:raise RuntimeError('Current read-only extract failed')
    receipt={'read_at_utc':old['read_at_utc'],'old_ledger_sha256_before':before,'old_ledger_sha256_after':sha(ledger),'old_quick_check':old['quick_check'],'requested_options':len(request),'requested_series':len(symbols),'old_metadata':oldmeta,'files':{}}
    for name in ['old_book_source.json.gz','current_book_source.json.gz','old_minutes.jsonl.gz','current_minutes.jsonl.gz']:
        path=ROOT/name;receipt['files'][name]={'sha256':sha(path),'bytes':path.stat().st_size}
    assert receipt['old_ledger_sha256_before']==receipt['old_ledger_sha256_after']
    (ROOT/'source_receipt.json').write_text(json.dumps(receipt,indent=2))
    print('Read-only inputs ready:',receipt['requested_options'],'options;',receipt['files'])


if __name__=='__main__':main()
