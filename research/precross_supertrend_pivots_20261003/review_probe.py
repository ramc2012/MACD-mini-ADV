"""Read-only independent source-row and candle-clock checks for the review."""
import gzip,json,hashlib,sys,datetime as dt
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]/'engine/src'))
from macd_trader.sessions import session_close
from macd_trader.market_calendar import is_trading_day
OUT=ROOT/'review';OUT.mkdir(exist_ok=True)
f=pd.read_csv(ROOT/'events.csv.gz');pred=pd.read_csv(ROOT/'prediction_holdout.csv')
chosen=pd.concat([f.sample(48,random_state=103),pred[pred.st_pivot_probability>.8]])
symbols=set(chosen.symbol);sources={}
for source in ['old','current']:
    z={}
    with gzip.open(ROOT.parent/'september_supertrend_pivots_20261003/followup'/f'{source}_minutes.jsonl.gz','rt') as stream:
        for line in stream:
            r=json.loads(line)
            if r.get('symbol') in symbols:z[r['symbol']]=np.array(r['bars'],float)
    sources[source]=z
    print(source,'sample symbols',len(z),flush=True)
rows=[];actual={}
for s in symbols:
    pieces=[sources[x][s] for x in ['current','old'] if s in sources[x]]
    a=np.concatenate(pieces);_,i=np.unique(a[:,0],return_index=True);a=a[i]
    good=np.isfinite(a).all(axis=1)&(a[:,1]>0)&(a[:,4]>0)&(a[:,2]>=a[:,3])&(a[:,2]+1e-7>=np.maximum(a[:,1],a[:,4]))&(a[:,3]-1e-7<=np.minimum(a[:,1],a[:,4]))&(a[:,5]>=0)
    a=a[good];clock=(a[:,0]+19800)%86400;days=((a[:,0]+19800)//86400).astype(int)
    dates={d:(dt.date(1970,1,1)+dt.timedelta(days=int(d))) for d in np.unique(days)}
    ends={d:session_close(s,date).hour*3600+session_close(s,date).minute*60 for d,date in dates.items()}
    valid=np.array([is_trading_day(dates[d]) and 33300<=c<ends[d] for d,c in zip(days,clock)])
    a=a[valid];actual[s]=a
    for e in chosen[chosen.symbol==s].itertuples():
        # Only observations whose one-minute candle has finished are admitted.
        prefix=a[a[:,0]+60<=e.ts];barstart=((prefix[:,0]+19800)//86400)*86400-19800+33300
        key=barstart+np.floor((prefix[:,0]-barstart)/1800)*1800
        keys=np.unique(key);past=keys[keys<e.bucket];fast=slow=None
        for k in past:
            price=prefix[key==k][-1,4]
            fast=price if fast is None else fast+(price-fast)*2/13
            slow=price if slow is None else slow+(price-slow)*2/27
        price=prefix[-1,4];m=(fast+(price-fast)*2/13)-(slow+(price-slow)*2/27)
        zero=(325*slow-297*fast)/28
        rows.append({'symbol':s,'ts':int(e.ts),'rule':e.rule,'price_difference':price-e.price,'zero_difference':zero-e.zero_price,'macd_pct_difference':100*m/price-e.macd_pct,'last_observation_end':int(prefix[-1,0]+60),'same_bar':int(keys[-1])==int(e.bucket),'volume_positive':bool(prefix[-1,5]>0)})
pd.DataFrame(rows).to_csv(OUT/'independent_prefix_checks.csv',index=False)
assert max(abs(r['zero_difference']) for r in rows)<1e-8
assert max(abs(r['macd_pct_difference']) for r in rows)<1e-8
assert all(r['price_difference']==0 and r['last_observation_end']==r['ts'] and r['same_bar'] and r['volume_positive'] for r in rows)
# Inspect cost-scenario 'breakeven' trailing exits at the execution price.
with gzip.open(ROOT/'books.json.gz','rt') as stream:books=json.load(stream)
fee_floor=[]
for name,b in books.items():
    if b['extra_bps']==0:continue
    for p in b['episodes']:
        if p['reason']=='TRAIL' and p['pnl']<0 and p['pnl']>=-2*b['fee_per_order']-.001:
            fee_floor.append({'book':name,'symbol':p['symbol'],'entry_ts':p['entry_ts'],'exit_ts':p['exit_ts'],'net_pnl':p['pnl'],'fees_roundtrip':2*b['fee_per_order']})
pd.DataFrame(fee_floor).to_csv(OUT/'fee_floor_examples.csv',index=False)
out={'sampled_events':len(rows),'sampled_contracts':len(symbols),'all_sampled_prefix_macd_zero_and_prices_match':True,'maximum_zero_price_error':max(abs(r['zero_difference']) for r in rows),'fee_scenario_trailing_losses_within_order_fees':len(fee_floor),'source_hashes':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['analyze.py','FINDINGS.md','events.csv.gz','books.json.gz']}}
(OUT/'independent_source_checks.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
