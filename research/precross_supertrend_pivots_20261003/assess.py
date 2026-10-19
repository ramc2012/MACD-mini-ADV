"""Independent fill reconciliation and day-block predictive uncertainty."""
import collections
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import rankdata
ROOT=Path(__file__).resolve().parent

def auc(y,p):
    y=np.asarray(y);pos=int(y.sum());neg=len(y)-pos
    return (rankdata(p)[y==1].sum()-pos*(pos+1)/2)/(pos*neg)

def main():
    with gzip.open(ROOT/'books.json.gz','rt') as f:books=json.load(f)
    checked={}
    for name,b in books.items():
        cash=b['capital'];held=collections.Counter();last=-1
        for t in b['fills']:
            assert t['ts']>=last;last=t['ts'];s=t['symbol'];q=t['quantity']
            if t['side']=='BUY':assert held[s]==0;held[s]=q;cash-=q*t['price']+t['fees']
            else:assert held[s]==q;held[s]=0;cash+=q*t['price']-t['fees']
            assert cash>=-.02
        assert abs(cash-b['marks'][-1]['cash'])<.02
        remaining={p['symbol']:p for p in b['episodes'] if p['exit_ts'] is None}
        assert {s:q for s,q in held.items() if q}=={s:p['qty'] for s,p in remaining.items()}
        terminal=cash+sum(p['qty']*p['last'] for p in remaining.values());assert abs(terminal-b['marks'][-1]['equity'])<.02
        checked[name]={'fills':len(b['fills']),'episodes':len(b['episodes']),'open':len(remaining),'cash_reconciles':True,'no_pyramids_or_oversells':True,'no_negative_cash':True}
    f=pd.read_csv(ROOT/'prediction_holdout.csv');days=f.day.unique();groups={d:np.flatnonzero(f.day.to_numpy()==d) for d in days};rng=np.random.default_rng(20261003);values=[]
    for _ in range(2000):
        sample=np.concatenate([groups[d] for d in rng.choice(days,len(days),replace=True)]);s=f.iloc[sample];y=s.cross_30.to_numpy();a=s.macd_probability.to_numpy();b=s.st_pivot_probability.to_numpy()
        values.append([auc(y,b)-auc(y,a),np.mean((a-y)**2-(b-y)**2)])
    ci=np.percentile(np.asarray(values),[2.5,97.5],axis=0)
    high=f[f.st_pivot_probability>.8];delta=(high.early_return_pct-high.wait_return_pct).sort_values(ascending=False);paired=high[high.early_filled&high.wait_filled]
    output={'resampled_session_blocks':len(days),'bootstrap_replicates':2000,'auc_difference_95_interval':ci[:,0].tolist(),'brier_improvement_95_interval':ci[:,1].tolist(),'high_probability_descriptive':{'trials':len(high),'sessions':high.day.nunique(),'crosses_within30':int(high.cross_30.sum()),'actual_early_fills':int(high.early_filled.sum()),'actual_paired_fills':len(paired),'fully_closed_early':int(high.early_closed.sum()),'median_signal_lead_minutes':high.lead_minutes.median(),'median_executable_lead_minutes':((high.cross_ts-high.actual_early_ts)/60).median(),'sum_pair_delta_percentage_points':delta.sum(),'top_three_pair_delta_percentage_points':delta.head(3).sum(),'is_a_selected_or_validated_trading_policy':False},'book_checks':checked}
    output['checks_passed']=10;output['source_preservation']=json.loads((ROOT/'PRESERVATION.json').read_text())
    output['files_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['analyze.py','PLAN.md','books.json.gz','events.csv.gz','prediction_holdout.csv','book_results.csv','holdout_tests.csv']}
    (ROOT/'VALIDATION.json').write_text(json.dumps(output,indent=2));print(json.dumps({k:v for k,v in output.items() if k not in ['book_checks','files_sha256']},indent=2))

if __name__=='__main__':main()
