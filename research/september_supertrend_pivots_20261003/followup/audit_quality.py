"""Read-only gap audit and closed-episode quality of saved matched books."""
import collections
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
INPUT=ROOT.parent
OLD=Path('/Users/ramachandran/CLAUDE PROJECTS/MACD mini/runtime')
IST=ZoneInfo('Asia/Kolkata')

def load(name):
    with gzip.open(INPUT/name,'rt') as f:return json.load(f)

def quality(book,episodes):
    begin=book['start'];finish=book['end'];qty=collections.Counter();cost=collections.Counter();flow=collections.Counter();fills=collections.Counter()
    # Applied fills alone give residual quantity for new episodes; carry-in is excluded.
    for a in book['applied_fills']:
        id=a['id']
        if episodes[id]['entry_date']<begin:continue
        sign=1 if a['side']=='BUY' else -1
        qty[id]+=sign*a['quantity'];flow[id]-=sign*a['quantity']*a['price'];fills[a['side']]+=1
        if sign>0:cost[id]+=a['quantity']*a['price']
    ids=list(cost);closed=[id for id in ids if qty[id]==0];opened=[id for id in ids if qty[id]>0]
    assert all(qty[id]>=0 for id in ids)
    p=np.asarray([flow[id] for id in closed]);returns=np.asarray([100*flow[id]/cost[id] for id in closed]);wins=p[p>0];loss=p[p<0]
    marks=np.asarray([book['episode_pnl'][id] for id in ids]);openp=[book['episode_pnl'][id] for id in opened]
    return {'new_episodes':len(ids),'closed_new_episodes':len(closed),'open_new_episodes':len(opened),'new_buy_fills':fills['BUY'],'new_sell_fills':fills['SELL'],
        'closed_win_rate_pct':float((p>0).mean()*100) if len(p) else None,'closed_profit_factor':float(wins.sum()/abs(loss.sum())) if len(loss) else None,
        'closed_mean_return_pct':float(returns.mean()) if len(p) else None,'closed_median_return_pct':float(np.median(returns)) if len(p) else None,
        'closed_mean_pnl_rupees':float(p.mean()) if len(p) else None,'closed_mean_win_rupees':float(wins.mean()) if len(wins) else None,'closed_mean_loss_rupees':float(loss.mean()) if len(loss) else None,
        'closed_total_pnl_rupees':float(p.sum()),'open_marked_pnl_rupees':float(sum(openp)),'all_new_marked_pnl_rupees':float(marks.sum()),'carry_in_pnl_rupees':book['pnl']-float(marks.sum()),
        'closed_count_CE':sum(episodes[id]['symbol'].endswith('CE') for id in closed),'closed_count_PE':sum(episodes[id]['symbol'].endswith('PE') for id in closed)}

def db_audit(path):
    c=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=30);c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")};out={'file':str(path.relative_to(OLD)),'query_only':True}
    for table in ['trades','signals','equity_points']:
        if table not in tables:continue
        stats=c.execute('SELECT COUNT(*),MIN(timestamp),MAX(timestamp) FROM '+table).fetchone();out[table]={'count':stats[0],'first':stats[1],'last':stats[2]}
        rows=c.execute('SELECT '+('payload' if table!='equity_points' else 'timestamp')+' FROM '+table+" WHERE timestamp >= '2026-09-18' AND timestamp < '2026-09-30' ORDER BY timestamp").fetchall();daily=collections.Counter();opt=collections.Counter()
        for row in rows:
            item=json.loads(row[0]) if table!='equity_points' else {'timestamp':row[0]}
            day=dt.datetime.fromisoformat(item['timestamp']).astimezone(IST).date().isoformat();daily[day]+=1
            if item.get('symbol','').endswith(('CE','PE')):opt[day]+=1
        out[table]['daily_18_29']=dict(daily);out[table]['option_daily_18_29']=dict(opt)
    c.rollback();c.close();return out

def main():
    books=load('books.json.gz');eps=load('episodes.json.gz');rows=[]
    for k,b in books.items():
        if not b['funded'] or b['extra_bps']!=0:continue
        rows.append({'cohort':k.split('|')[0],'rule':b['rule'],**quality(b,eps)})
    pd.DataFrame(rows).to_csv(ROOT/'actual_trade_quality.csv',index=False)
    paths=[p for p in OLD.rglob('*') if p.is_file() and ('macd_trader.sqlite3' in p.name)]
    audits=[]
    for p in sorted(paths):
        try:audits.append(db_audit(p))
        except sqlite3.Error as exc:audits.append({'file':str(p.relative_to(OLD)),'error':str(exc)})
    (ROOT/'old_backup_audit.json').write_text(json.dumps(audits,indent=2))
    counts=[]
    for label,name in [('old','old_minutes.jsonl.gz'),('current','current_minutes.jsonl.gz')]:
        byday={};print('coverage',label,flush=True)
        with gzip.open(INPUT/name,'rt') as f:
            for line in f:
                record=json.loads(line)
                if 'metadata' in record:continue
                a=np.asarray(record['bars'],dtype=float);days=(a[:,0].astype(np.int64)+19800)//86400;times=(a[:,0]+19800)%86400
                start=(dt.date(2026,9,1)-dt.date(1970,1,1)).days;end=(dt.date(2026,9,30)-dt.date(1970,1,1)).days
                mask=(days>=start)&(days<=end)&(times>=33300)&(times<55800)
                uniq,n=np.unique(days[mask],return_counts=True)
                option=record['symbol'].endswith(('CE','PE'))
                for d,size in zip(uniq,n):
                    day=(dt.date(1970,1,1)+dt.timedelta(days=int(d))).isoformat();row=byday.setdefault(day,{'option_rows':0,'underlying_rows':0,'options':0,'underlyings':0,'options_300_minutes':0})
                    row['option_rows' if option else 'underlying_rows']+=int(size);row['options' if option else 'underlyings']+=1
                    if option and size>=300:row['options_300_minutes']+=1
        counts.extend({'source':label,'date':day,**r} for day,r in sorted(byday.items()))
    pd.DataFrame(counts).to_csv(ROOT/'minute_coverage_by_day.csv',index=False)
    code='''import datetime as dt,json,os,sqlite3,urllib.request
out={'checked_at_utc':dt.datetime.now(dt.UTC).isoformat(),'smart':{}}
for name in ['smart_1l','smart_50l']:
 c=sqlite3.connect('file:/app/data/'+name+'.sqlite3?mode=ro',uri=True);c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
 out['smart'][name]={'fills':c.execute('SELECT COUNT(*) FROM trades').fetchone()[0],'fill_range':c.execute('SELECT MIN(timestamp),MAX(timestamp) FROM trades').fetchone(),'journal_by_day':c.execute('SELECT day,decision,COUNT(*) FROM smart_journal GROUP BY day,decision').fetchall()};c.rollback();c.close()
r=urllib.request.Request('http://127.0.0.1:8100/api/settings',headers={'Authorization':'Bearer '+os.environ.get('MACD_API_TOKEN','')})
s=json.load(urllib.request.urlopen(r,timeout=20));out['settings']={k:v for k,v in s.items() if k.startswith(('smart_','blast_')) or k in ['execution_mode','slippage_bps','fast_period','slow_period','signal_period','candle_seconds','spot_symbols_csv']}
import macd_trader.smart_lane as sm,inspect,hashlib
out['smart_model_sha256']=hashlib.sha256(json.dumps(sm.MODEL,sort_keys=True,separators=(',',':')).encode()).hexdigest();out['smart_model_fields']={k:v for k,v in sm.MODEL.items() if k not in ['trees']};out['smart_source_sha256']=hashlib.sha256(inspect.getsource(sm).encode()).hexdigest()
print(json.dumps(out))
'''
    child=subprocess.run(['docker','exec','-i','macd-mini-parallel-engine-1','python','-'],input=code,text=True,capture_output=True,check=True)
    (ROOT/'smart_runtime_audit.json').write_text(child.stdout)
    print('Quality tables and read-only gap/Smart audit saved.',flush=True)

if __name__=='__main__':main()
