"""Confirm original sources are unchanged after the isolated experiment."""
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'september_supertrend_pivots_20261003'
sys.path.insert(0,str(BASE/'followup'))
from preserve import code as capture_code

def main():
    child=subprocess.run(['docker','exec','-i','macd-mini-parallel-engine-1','python','-'],input=capture_code,text=True,capture_output=True,check=True)
    live=json.loads(child.stdout)
    with gzip.open(BASE/'current_book_source.json.gz','rt') as f:before=json.load(f)
    a=live['portfolio'];b=before['api_response'];receipt=json.loads((BASE/'source_receipt.json').read_text())
    old=Path('/Users/ramachandran/CLAUDE PROJECTS/MACD mini/runtime/macd_trader.sqlite3');h=hashlib.sha256(old.read_bytes()).hexdigest()
    out={'checked_at_utc':dt.datetime.now(dt.UTC).isoformat(),'old_ledger_sha256':h,'old_ledger_unchanged':h==receipt['old_ledger_sha256_before'],'current_fills_unchanged':live['books']['macd_trader']['trades']==before['trades'],'current_fill_count':len(live['books']['macd_trader']['trades']),'current_positions_unchanged':a['positions']==b['positions'],'current_open_positions':len(a['positions']),'cash_and_realized_unchanged':a['cash']==b['cash'] and a['realized_pnl']==b['realized_pnl'],'smart':{k:{'fills':len(v['trades']),'journal_decisions':v['journal_count']} for k,v in live['books'].items() if k.startswith('smart')},'live_orders_unlocked':live['snapshot']['execution']['live_orders_unlocked']}
    assert all(out[k] for k in ['old_ledger_unchanged','current_fills_unchanged','current_positions_unchanged','cash_and_realized_unchanged']) and not out['live_orders_unlocked']
    (ROOT/'PRESERVATION.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))

if __name__=='__main__':main()
