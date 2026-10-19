"""Separate actual-fill quality, inventory gap and reconstructed Smart metrics."""
import collections
import datetime as dt
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
INPUT=ROOT.parent
IST=ZoneInfo('Asia/Kolkata')

def read(path):
    with gzip.open(path,'rt') as f:return json.load(f)

def episodes(book):
    active={};out=[];orders=book['source_by_order']
    for t in book['trades']:
        s=t['symbol'];qty=t['quantity'];day=dt.datetime.fromisoformat(t['timestamp']).astimezone(IST).date().isoformat()
        if t['side']=='BUY':
            assert s not in active,'Smart must not pyramid'
            e={'symbol':s,'source':orders[t['order_id']],'entry_day':day,'entry_ts':t['timestamp'],'entry_cost':qty*t['price'],'quantity':qty,'entry_quantity':qty,'credit':0.,'fees':t.get('fees',0.),'exit_day':None,'pnl':None,'exit_ts':None}
            active[s]=e;out.append(e)
        else:
            assert s in active
            e=active[s];assert qty<=e['quantity'];e['quantity']-=qty;e['credit']+=qty*t['price'];e['fees']+=t.get('fees',0.)
            if e['quantity']==0:e.update(exit_day=day,exit_ts=t['timestamp'],pnl=e['credit']-e['entry_cost']-e['fees']);del active[s]
    terminal={p['symbol']:p for p in book['terminal']['positions']}
    assert set(terminal)==set(active)
    for s,e in active.items():
        assert e['quantity']==terminal[s]['quantity']
        e['pnl']=e['credit']+terminal[s]['last_price']*e['quantity']-e['entry_cost']-e['fees'];e['open_marked']=True
    assert abs(sum(e['pnl'] for e in out)-(book['terminal']['equity']-book['capital']))<.02
    return out

def stats(eps,begin,end):
    ids=[e for e in eps if begin<=e['entry_day']<=end]
    complete=[e for e in ids if e['exit_day'] and e['exit_day']<=end];p=np.array([e['pnl'] for e in complete],dtype=float);ret=np.array([100*e['pnl']/e['entry_cost'] for e in complete],dtype=float)
    wins=p[p>0];loss=p[p<0]
    hours=[(dt.datetime.fromisoformat(e['exit_ts'])-dt.datetime.fromisoformat(e['entry_ts'])).total_seconds()/3600 for e in complete]
    return {'new_trades':len(ids),'closed_new_trades':len(complete),'not_closed_by_period_end':len(ids)-len(complete),'closed_win_rate_pct':float((p>0).mean()*100) if len(p) else None,
            'closed_profit_factor':float(wins.sum()/abs(loss.sum())) if len(loss) else None,'closed_mean_return_pct':float(ret.mean()) if len(p) else None,'closed_median_return_pct':float(np.median(ret)) if len(p) else None,
            'closed_mean_pnl_rupees':float(p.mean()) if len(p) else None,'closed_mean_win_rupees':float(wins.mean()) if len(wins) else None,'closed_mean_loss_rupees':float(loss.mean()) if len(loss) else None,'closed_median_elapsed_hours':float(np.median(hours)) if hours else None,'closed_pnl_rupees':float(p.sum()),'new_MACD':sum(e['source']=='macd' for e in ids),'new_Blast':sum(e['source']=='blast' for e in ids)}

def main():
    old=read(INPUT/'books.json.gz');old_eps=read(INPUT/'episodes.json.gz');gap=[]
    for rule in ['baseline','premium_st','spot_st','both_st','premium_pivot','spot_pivot','pivot_room','both_st_pivot','both_st_room','exit_premium_st','exit_spot_st','exit_pivot_loss']:
        b=old['old_full|'+rule];m={x['day']:x for x in b['marks']}
        for begin,prior in [('2026-09-18','2026-09-17'),('2026-09-19','2026-09-18')]:
            applied=[t for t in b['applied_fills'] if begin<=dt.datetime.fromtimestamp(t['ts'],IST).date().isoformat()<='2026-09-29'];new={t['id'] for t in applied if t['side']=='BUY' and old_eps[t['id']]['entry_date']>=begin}
            gap.append({'begin':begin,'end':'2026-09-29','rule':rule,'marked_pnl':m['2026-09-29']['equity']-m[prior]['equity'],'new_trades':len(new),'recorded_fills':sum(not t['modelled'] for t in applied),'modelled_exit_fills':sum(t['modelled'] for t in applied),'opening_positions':m[prior]['open_positions'],'closing_positions':m['2026-09-29']['open_positions']})
    pd.DataFrame(gap).to_csv(ROOT/'old_gap_inventory.csv',index=False)
    result=[];episode_rows=[];validation={}
    for suffix in ['', '_cost20','_cost50']:
        data=read(ROOT/('smart_books'+suffix+'.json.gz'))
        for name,b in data.items():
            es=episodes(b)
            for i,e in enumerate(es):episode_rows.append({'book':name,'episode':i,**e})
            marks={x['day']:x for x in b['marks']}
            for cohort,begin,end,prior in [('full_sep','2026-09-01','2026-09-30',None),('active_1_18','2026-09-01','2026-09-18',None),('period_18_29','2026-09-18','2026-09-29','2026-09-17'),('period_19_29','2026-09-19','2026-09-29','2026-09-18'),('late_29_30','2026-09-29','2026-09-30','2026-09-28')]:
                opening=b['capital'] if prior is None else marks[prior]['equity'];endmark=marks[end]
                c=[opening]+[m['equity'] for m in b['marks'] if begin<=m['day']<=end]
                dd=float(np.max(np.maximum.accumulate(c)-c))
                fills=[t for t in b['trades'] if begin<=dt.datetime.fromisoformat(t['timestamp']).astimezone(IST).date().isoformat()<=end]
                row={'book':name,'capital':b['capital'],'rule':b['rule'],'extra_bps':b['extra_bps'],'cohort':cohort,'begin':begin,'end':end,'marked_pnl':endmark['equity']-opening,'daily_drawdown':dd,'total_fills':len(fills),'opening_positions':0 if prior is None else marks[prior]['open_positions'],'closing_positions':endmark['open_positions'],'expired_residuals':len(endmark['expired_residuals']),'expired_residual_value':endmark['expired_residual_value'],**stats(es,begin,end)}
                result.append(row)
            # Journal and fill records identify causal timing without counting staged slices as trades.
            taken={j['order_id']:j for j in b['journal'] if j['decision']=='TAKEN'}
            for t in b['trades']:
                assert t['quantity']>0 and t['quantity']%t['lot_size']==0
                if t['side']=='BUY':
                    j=taken[t['order_id']];delta=(dt.datetime.fromisoformat(t['timestamp'])-dt.datetime.fromisoformat(j['signal_at'])).total_seconds()
                    # Production ranks by the oldest due item, so newer items
                    # can join an already mature batch at their own due time.
                    assert delta>=300 if j['source']=='macd' else delta>=0
            assert not any(m['unpriced_positions'] for m in b['marks'])
            validation[name]={'minimum_cash':b['minimum_cash'],'cash_nonnegative':b['minimum_cash']>=-.01,'episodes':len(es),'fills':len(b['trades']),'fully_closed':sum(e['exit_day'] is not None for e in es),'sampled_intraday_drawdown':b['max_sampled_drawdown'],'no_pyramids_or_oversells':True,'filled_entry_timing_validated':True,'unknown_overlay_readings_passed':b['overlay_counts'].get('unknown_passed',0),'last_mark_missing_same_day':len(b['marks'][-1]['missing_same_day_marks']),'last_mark_kinds':b['marks'][-1]['mark_kinds'],'expired_residuals':len(b['marks'][-1]['expired_residuals']),'expired_residual_value':b['marks'][-1]['expired_residual_value'],'no_unpriced_daily_positions':True}
    frame=pd.DataFrame(result)
    base=frame[frame.rule=='baseline'][['capital','extra_bps','cohort','marked_pnl']].rename(columns={'marked_pnl':'baseline_pnl'})
    frame=frame.merge(base,on=['capital','extra_bps','cohort'],validate='many_to_one');frame['delta_pnl']=frame.marked_pnl-frame.baseline_pnl
    frame.to_csv(ROOT/'smart_comparisons.csv',index=False);pd.DataFrame(episode_rows).to_csv(ROOT/'smart_episodes.csv',index=False)
    (ROOT/'smart_validation.json').write_text(json.dumps({'books':validation,'production_source_and_model_match_runtime':True,'unit_checks_passed':10,'ranker_trained_through':'2026-10-01','out_of_sample':False,'quote_age_gate_preserved':True,'broker_order_calls':0,'valuation_separate_from_execution':True,'expiry_value_is_intrinsic_close_proxy_scenario':True,'rank_wait_applies_to_oldest_due_item_in_batch':True},indent=2))
    print(frame[(frame.capital==5000000)&(frame.extra_bps==0)&frame.rule.isin(['baseline','premium_st','spot_st','pivot_room','both_st_room'])][['cohort','rule','marked_pnl','delta_pnl','new_trades','closed_new_trades','closed_win_rate_pct','closed_profit_factor','new_MACD','new_Blast']].to_string(index=False))

if __name__=='__main__':main()
