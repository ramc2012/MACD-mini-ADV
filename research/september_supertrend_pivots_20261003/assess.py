"""Validate saved results, add same-cost entry-filter comparisons and attribution."""
import collections
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import study as s

ROOT=Path(__file__).resolve().parent


def main():
    if (ROOT/'daily_mark_cache.json.gz').exists():
        cache=s.load_gzip(ROOT/'daily_mark_cache.json.gz')
        assert cache['purpose']=='Closing valuation only; cannot be used for intraday signals or exits.'
        expiries=cache['expiries'];thin={k:np.asarray(v,dtype=float) for k,v in cache['minutes'].items()}
    else:
        minutes,expiries=s.load_minutes()
        thin={}
        for symbol,a in minutes.items():
            selected=set();days=(a[:,0].astype(np.int64)+19800)//86400
            for d in np.unique(days):
                for clock in ['15:40','16:00']:
                    at=s.epoch(s.datestr(d),clock)
                    k=np.searchsorted(a[:,0]+60,at,side='right')-1
                    if k>=0 and days[k]==d:selected.add(int(k))
            thin[symbol]=a[sorted(selected)]
        del minutes
        with gzip.open(ROOT/'daily_mark_cache.json.gz','wt') as f:
            json.dump({'purpose':'Closing valuation only; cannot be used for intraday signals or exits.','expiries':expiries,'minutes':{k:v.tolist() for k,v in thin.items()}},f,separators=(',',':'))
    eps=s.load_gzip(ROOT/'episodes.json.gz');books=s.load_gzip(ROOT/'books.json.gz')
    cohorts=[('old_full','old','2026-09-01','2026-09-30'),('old_active','old','2026-09-01','2026-09-18'),('old_train','old','2026-09-01','2026-09-11'),('old_holdout','old','2026-09-15','2026-09-18'),('current_sep','current','2026-09-29','2026-09-30')]
    costrows=[];checks={}
    for cohort,name,begin,finish in cohorts:
        source=s.load_gzip(ROOT/('old_book_source.json.gz' if name=='old' else 'current_book_source.json.gz'))
        trades,_=s.episodes(source,name);e={k:v for k,v in eps.items() if k.startswith(name+':')}
        zero=s.simulate(trades,e,'baseline',s.epoch(begin,'00:00'),s.epoch(finish),thin,expiries,{})
        original=books[cohort+'|baseline']
        assert abs(zero['pnl']-original['pnl'])<.01
        assert all(abs(a['equity']-b['equity'])<.01 for a,b in zip(zero['marks'],original['marks']))
        checks[cohort]={'closing_cache_equity_parity':True,'funded_baseline_min_cash':original['min_cash'],'rejected_adds':original['status'].get('unaffordable_adds',0),'rejected_entries':original['status'].get('unaffordable_entries',0)}
        for cost in [0,20,50]:
            calculated={}
            for rule in ['baseline','no_new_entries']+s.ENTRY_RULES:
                calculated[rule]=s.simulate(trades,e,rule,s.epoch(begin,'00:00'),s.epoch(finish),thin,expiries,{},cost)
            base=calculated['baseline']
            for rule,r in calculated.items():
                costrows.append({'cohort':cohort,'extra_bps':cost,'rule':rule,'pnl':r['pnl'],'delta_same_cost_baseline':r['pnl']-base['pnl'],'max_daily_drawdown':r['max_drawdown'],'new_episodes':r['status'].get('new_episodes',0),'rejected_entries':r['status'].get('unaffordable_entries',0),'rejected_adds':r['status'].get('unaffordable_adds',0)})
    pd.DataFrame(costrows).to_csv(ROOT/'entry_cost_sensitivity.csv',index=False)
    # Correct the existing exit/joint cost rows to their same-cost baseline too.
    r=pd.read_csv(ROOT/'results.csv')
    for i,row in r[r.extra_bps>0].iterrows():
        base=books[row.cohort+'|baseline|cost'+str(int(row.extra_bps))]
        r.loc[i,'delta_pnl']=row.pnl-base['pnl']
        r.loc[i,'delta_realized']=row.realized_pnl-base['scenario_realized_pnl']
        r.loc[i,'carry_in_delta']=np.nan
        # These cost scenarios are descriptive; do not reuse zero-cost p values.
        r.loc[i,['entry_day_t','entry_day_p','daily_equity_t','daily_equity_p']]=np.nan
    r.to_csv(ROOT/'results.csv',index=False)
    primary=r[(r.cohort=='old_train')&r.funded&(r.extra_bps==0)&r.rule.isin(['baseline','no_new_entries']+s.ENTRY_RULES)]
    selected=primary.sort_values(['pnl','rule'],ascending=[False,True]).iloc[0]
    selection={'objective':'Training-period funded marked P&L, including baseline and no-new-entry controls','training_period':'1–11 September 2026','selected_rule':selected.rule,'training_pnl':selected.pnl,'training_delta':selected.delta_pnl,'holdout':[]}
    for cohort in ['old_holdout','current_sep']:
        row=r[(r.cohort==cohort)&r.funded&(r.extra_bps==0)&(r.rule==selected.rule)].iloc[0]
        selection['holdout'].append({k:row[k] for k in ['cohort','pnl','delta_pnl','entry_day_clusters','entry_day_p_holm']})
    (ROOT/'training_selection.json').write_text(json.dumps(selection,indent=2,default=lambda value:value.item()))
    # Tail attribution is diagnostic on the actual active-period funded baseline.
    base=books['old_active|baseline'];ids=[k for k in base['episode_pnl'] if eps[k]['entry_date']>='2026-09-01']
    top=sorted(ids,key=lambda k:base['episode_pnl'][k],reverse=True)[:max(1,int(np.ceil(.05*len(ids))))]
    tails={'baseline_september_episode_count':len(ids),'top_five_percent_count':len(top),'top_five_percent_pnl':sum(base['episode_pnl'][k] for k in top),'baseline_new_episode_pnl':sum(base['episode_pnl'][k] for k in ids),'rules':{}}
    for rule in s.ENTRY_RULES:
        b=books['old_active|'+rule]
        taken={x['id'] for x in b['applied_fills'] if x['side']=='BUY'}
        tails['rules'][rule]={'top_episodes_retained':sum(k in taken for k in top),'top_episodes_filtered':sum(k not in taken for k in top),'top_original_pnl_retained':sum(base['episode_pnl'][k] for k in top if k in taken),'top_original_pnl_lost':sum(base['episode_pnl'][k] for k in top if k not in taken)}
    (ROOT/'tail_attribution.json').write_text(json.dumps(tails,indent=2))
    source=s.load_gzip(ROOT/'old_book_source.json.gz')
    old_path=Path(source['source_database'])
    h=hashlib.sha256(old_path.read_bytes()).hexdigest()
    assert h==source['source_sha256']
    checks['old_source_book_unchanged']=True
    checks['old_source_book_sha256']=h
    checks['recorded_baseline_exact_fill_reproduction']=True
    checks['recorded_baseline_september_realized_pnl']=books['old_full|baseline|recorded']['scenario_realized_pnl']
    checks['strict_funded_cash_nonnegative']=all(b['min_cash']>=-.01 for b in books.values() if b['funded'])
    checks['all_modelled_exit_fills_after_condition']=all(x['ts']>=x['condition_ts'] for b in books.values() for x in b['applied_fills'] if x['modelled'])
    (ROOT/'VALIDATION.json').write_text(json.dumps(checks,indent=2))
    print('Validation and cost checks completed. Training selection:',selection)
    print('Tail attribution:',tails)


if __name__=='__main__':main()
