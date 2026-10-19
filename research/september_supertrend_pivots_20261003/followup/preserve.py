"""Read-only final source preservation checks; no order or config APIs."""
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent
code='''import json,os,sqlite3,urllib.request
def get(path):
 r=urllib.request.Request('http://127.0.0.1:8100'+path,headers={'Authorization':'Bearer '+os.environ.get('MACD_API_TOKEN','')})
 return json.load(urllib.request.urlopen(r,timeout=20))
out={'books':{}}
for name in ['macd_trader','smart_1l','smart_50l']:
 c=sqlite3.connect('file:/app/data/'+name+'.sqlite3?mode=ro',uri=True);c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
 out['books'][name]={'trades':[json.loads(r[0]) for r in c.execute('SELECT payload FROM trades ORDER BY timestamp,rowid')]}
 if name.startswith('smart'):out['books'][name]['journal_count']=c.execute('SELECT COUNT(*) FROM smart_journal').fetchone()[0]
 c.rollback();c.close()
out['portfolio']=get('/api/portfolio');out['snapshot']=get('/api/snapshot')
print(json.dumps(out))
'''

def main():
    p=subprocess.run(['docker','exec','-i','macd-mini-parallel-engine-1','python','-'],input=code,text=True,capture_output=True,check=True)
    live=json.loads(p.stdout)
    with gzip.open(ROOT.parent/'current_book_source.json.gz','rt') as f:before=json.load(f)
    a=live['portfolio'];b=before['api_response'];receipt=json.loads((ROOT.parent/'source_receipt.json').read_text())
    old=Path('/Users/ramachandran/CLAUDE PROJECTS/MACD mini/runtime/macd_trader.sqlite3')
    h=hashlib.sha256(old.read_bytes()).hexdigest()
    result={'checked_at_utc':dt.datetime.now(dt.UTC).isoformat(),'old_ledger_sha256':h,'old_ledger_unchanged':h==receipt['old_ledger_sha256_before'],'current_fill_count':len(live['books']['macd_trader']['trades']),'current_fills_unchanged':live['books']['macd_trader']['trades']==before['trades'],'current_positions_unchanged':a['positions']==b['positions'],'current_open_positions':len(a['positions']),'cash_and_realized_unchanged':a['cash']==b['cash'] and a['realized_pnl']==b['realized_pnl'],'smart':{k:{'fills':len(v['trades']),'journal_decisions':v['journal_count']} for k,v in live['books'].items() if k.startswith('smart')},'live_orders_unlocked':live['snapshot']['execution']['live_orders_unlocked']}
    assert result['old_ledger_unchanged'] and result['current_fills_unchanged'] and result['current_positions_unchanged'] and result['cash_and_realized_unchanged']
    assert not result['live_orders_unlocked'] and all(v['fills']==v['journal_decisions']==0 for v in result['smart'].values())
    (ROOT/'PRESERVATION.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':main()
