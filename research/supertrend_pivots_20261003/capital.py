"""Chronological, funded-entry-only capital experiment with whole lots."""
import collections,gzip,heapq,json,pathlib
import numpy as np
import pandas as pd
from study import ROOT,net_return,date_str,t_test,holm

INITIAL=5_000_000.0
TICKET=100_000.0
PRIMARY=['baseline','no_trade','premium_st30_10_3_align','spot_st30_10_3_align','spot_st30_7_2_align','spot_daily_aligned_p','spot_daily_room','combined_st_pivot','exit_premium_st','exit_spot_st','exit_pivot_loss']

def replay(events,prices,lane,rule,days,mark_days,slip=5):
    queue=[];seq=0;cash=INITIAL;held={};blocked=collections.Counter();opened=0;turnover=0;realized=0;marks=[]
    overlay=rule[5:] if rule.startswith('exit_') else 'baseline'
    for r in events:
        tr=r['trades'][lane+'_'+overlay]
        if r['day'] not in days or not isinstance(tr,dict):continue
        if rule!='baseline' and not rule.startswith('exit_') and r['features'].get(rule) is not True:continue
        heapq.heappush(queue,(tr['entry_ts'],1,seq,('entry',r,tr)));seq+=1
    for day in sorted(mark_days):
        ts=day*86400-19800+56400 # 15:40 IST mark, after the final regular-session bar.
        heapq.heappush(queue,(ts,2,seq,('mark',day)));seq+=1
    while queue:
        ts,priority,_,payload=heapq.heappop(queue);kind=payload[0]
        if kind=='entry':
            _,r,tr=payload;sym=r['symbol'];lot=r['lot']
            if sym in held:blocked['already_held']+=1;continue
            unit=tr['entry']*(1+slip/10000)*lot*1.001
            lots=int((min(cash,TICKET)-20)//unit)
            if lots<1:blocked['cash_or_ticket']+=1;continue
            debit=unit*lots+20;cash-=debit;turnover+=debit;opened+=1
            held[sym]={'lots':lots,'lot':lot,'debit':debit,'credit':0.,'entry':tr['entry'],'last':tr['entry']}
            for t,p,n,reason in tr['fills']:
                heapq.heappush(queue,(t,0,seq,('exit',sym,p,reason)));seq+=1
        elif kind=='exit':
            _,sym,p,reason=payload
            if sym not in held:raise AssertionError('Exit without funded position')
            pos=held[sym]
            if reason=='partial':
                if pos['lots']<=1:continue
                lots=1
            else:lots=pos['lots']
            value=p*(1-slip/10000)*lots*pos['lot']*.998-20
            cash+=value;pos['credit']+=value;pos['lots']-=lots
            if not pos['lots']:realized+=pos['credit']-pos['debit'];del held[sym]
        else:
            _,day=payload;valuation=0;stale=0
            for sym,pos in held.items():
                t,c=prices[sym];j=np.searchsorted(t,ts,side='right')-1
                price=c[j] if j>=0 else pos['entry'];valuation+=price*pos['lots']*pos['lot']
                if j<0 or ts-t[j]>86400:stale+=1
            marks.append({'day':date_str(day),'equity':cash+valuation,'cash':cash,'positions':len(held),'stale_marks':stale})
        if cash<-.0001:raise AssertionError('Negative cash')
    if held:raise AssertionError('Unresolved capital-book position')
    if abs(cash-INITIAL-realized)>1e-6:raise AssertionError('Cash conservation failure')
    equity=np.array([INITIAL]+[r['equity'] for r in marks]+[cash]);dd=float(np.max(np.maximum.accumulate(equity)-equity))
    return {'pnl':cash-INITIAL,'return_pct':100*(cash/INITIAL-1),'max_drawdown':dd,'entries':opened,'turnover':turnover,'blocked':dict(blocked),'marks':marks}

def main():
    with gzip.open(ROOT/'events.jsonl.gz','rt') as f:events=[json.loads(line) for line in f]
    daily=json.loads((ROOT/'daily_deltas.json').read_text());split=daily['split']
    all_days=sorted({r['day'] for r in events});holdout=[d for d in all_days if date_str(d)>=split]
    needed={r['symbol'] for r in events};prices={}
    with gzip.open(ROOT/'candles5.jsonl.gz','rt') as f:
        for line in f:
            r=json.loads(line)
            if r['symbol'] in needed:
                a=np.asarray(r['bars']);prices[r['symbol']]=(a[:,7],a[:,4])
    results={};rows=[];summary=pd.read_csv(ROOT/'results.csv')
    for lane in ['standard','smart']:
        cutoff=min(holdout)*86400-19800+33300
        train_events=[r for r in events if r['date']<split and (r['trades'][lane+'_baseline']=='faded' or r['trades'][lane+'_baseline']['exit_ts']<cutoff)]
        candidates=['baseline','no_trade']+sorted(events[0]['features'])
        scores={rule:sum(net_return(r['trades'][lane+'_baseline'],r['lot'],5) for r in train_events if rule=='baseline' or r['features'].get(rule) is True) for rule in candidates}
        champion=max(scores,key=scores.get)
        for period,days in [('full',all_days),('holdout',holdout)]:
            last_exit=max(t['exit_ts'] for r in events if r['day'] in days for t in r['trades'].values() if isinstance(t,dict))
            end_day=int((last_exit+19800)//86400)
            source_days=json.loads((ROOT/'quality.json').read_text())['market_days']
            import datetime as dt
            mark_days=[(dt.date.fromisoformat(d)-dt.date(1970,1,1)).days for d in source_days]
            mark_days=[d for d in mark_days if min(days)<=d<=end_day]
            rules=list(dict.fromkeys(PRIMARY+[champion]))
            for rule in rules:
                value=replay(events,prices,lane,rule,set(days),mark_days);results[lane+'|'+period+'|'+rule]=value
                base=results[lane+'|'+period+'|baseline']
                delta=np.diff(np.r_[INITIAL,[r['equity'] for r in value['marks']]])-np.diff(np.r_[INITIAL,[r['equity'] for r in base['marks']]])
                t,p=t_test(delta)
                rows.append({'lane':lane,'period':period,'rule':rule,'training_champion':rule==champion,'pnl':value['pnl'],'delta_pnl':value['pnl']-base['pnl'],'return_pct':value['return_pct'],'max_drawdown':value['max_drawdown'],'entries':value['entries'],'daily_difference_t':t,'p_raw':p,'stale_mark_days':sum(r['stale_marks']>0 for r in value['marks'])})
    output=pd.DataFrame(rows)
    for period in ['full','holdout']:
        mask=(output.period==period)&(output.rule!='baseline');output.loc[mask,'p_holm']=holm(output.loc[mask,'p_raw'])
    (ROOT/'capital_books.json').write_text(json.dumps(results,indent=2));output.to_csv(ROOT/'capital_results.csv',index=False)
    sensitivity=[]
    last_exit=max(t['exit_ts'] for r in events for t in r['trades'].values() if isinstance(t,dict))
    end_day=int((last_exit+19800)//86400)
    mark_days=[d for d in mark_days if min(holdout)<=d<=end_day]
    for lane in ['standard','smart']:
        for slip in [5,25,50]:
            for rule in ['baseline','combined_st_pivot','spot_st30_7_2_align']:
                value=replay(events,prices,lane,rule,set(holdout),mark_days,slip)
                sensitivity.append({'lane':lane,'slippage_bps':slip,'rule':rule,'pnl':value['pnl'],'max_drawdown':value['max_drawdown'],'entries':value['entries']})
    pd.DataFrame(sensitivity).to_csv(ROOT/'capital_cost_sensitivity.csv',index=False)
    print(pd.DataFrame(rows).query('period=="holdout"').to_string(index=False))

if __name__=='__main__':main()
