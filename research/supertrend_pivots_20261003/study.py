"""Independent deterministic indicator/entry/exit experiment on raw candles.

Run with the bundled Python and PYTHONPATH=.deps. Never opens a live database.
"""
from __future__ import annotations
import bisect, collections, datetime as dt, gzip, hashlib, json, math, pathlib, re
import numpy as np
import pandas as pd
from scipy.stats import t as student_t

ROOT = pathlib.Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[1]
PARAMS = [(10,3),(7,2),(10,2),(10,4),(14,4)]
Q = collections.Counter()
EPOCH_DAY = 86400

def day_of(ts):
    return (np.asarray(ts,dtype=np.int64)+19800)//EPOCH_DAY

def date_str(day):
    return (dt.date(1970,1,1)+dt.timedelta(days=int(day))).isoformat()

def frame(record):
    a=np.asarray(record['bars'],dtype=float)
    f=pd.DataFrame(a,columns=['t','o','h','l','c','v','n','end'])
    f['t']=f.t.astype('int64'); f['end']=f.end.astype('int64')
    f['day']=day_of(f.t)
    return f

def aggregate(f,minutes):
    if minutes==5:
        return f.copy()
    sec=minutes*60
    session_open=f.day*86400-19800+33300
    group=session_open+(f.t-session_open)//sec*sec
    z=f.groupby(group,sort=True).agg(o=('o','first'),h=('h','max'),l=('l','min'),c=('c','last'),v=('v','sum'),n=('n','sum'),end=('end','max'),day=('day','first'))
    z.index.name='t'; z=z.reset_index()
    # A short final session bar is committed only at its recorded session end.
    return z

def macd(c):
    c=pd.Series(c,dtype=float)
    return (c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean()).to_numpy()

def atr(f,period=10):
    c=f.c.to_numpy(); h=f.h.to_numpy(); l=f.l.to_numpy()
    tr=np.maximum(h-l,np.maximum(np.abs(h-np.r_[c[0],c[:-1]]),np.abs(l-np.r_[c[0],c[:-1]])))
    out=np.full(len(f),np.nan)
    if len(f)>=period:
        out[period-1]=tr[:period].mean()
        for i in range(period,len(f)):
            out[i]=(out[i-1]*(period-1)+tr[i])/period
    return out

def supertrend(f,period=10,mult=3):
    a=atr(f,period); c=f.c.to_numpy(); mid=(f.h.to_numpy()+f.l.to_numpy())/2
    ub=mid+mult*a; lb=mid-mult*a
    direction=np.zeros(len(f),dtype=int); line=np.full(len(f),np.nan); age=np.zeros(len(f),dtype=int)
    for i in range(period-1,len(f)):
        if i==period-1:
            direction[i]=-1; line[i]=ub[i]
        else:
            if not (ub[i]<ub[i-1] or c[i-1]>ub[i-1]): ub[i]=ub[i-1]
            if not (lb[i]>lb[i-1] or c[i-1]<lb[i-1]): lb[i]=lb[i-1]
            direction[i]=(1 if c[i]>ub[i] else -1) if direction[i-1]<0 else (-1 if c[i]<lb[i] else 1)
            line[i]=lb[i] if direction[i]>0 else ub[i]
            age[i]=age[i-1]+1 if direction[i]==direction[i-1] else 0
    return {'dir':direction,'line':line,'atr':a,'age':age}

def pivots(f,weekly=False):
    days=f.groupby('day').agg(h=('h','max'),l=('l','min'),c=('c','last'),n=('n','sum'))
    # Require nearly a full regular session; incomplete first days cannot set pivots.
    days=days[days.n>=300].copy()
    if weekly:
        dates=[dt.date(1970,1,1)+dt.timedelta(days=int(d)) for d in days.index]
        keys=[d.toordinal()-d.weekday() for d in dates]
        days['week']=keys
        periods=days.groupby('week').agg(h=('h','max'),l=('l','min'),c=('c','last'),sessions=('n','size'))
        periods=periods[periods.sessions>=3]
        keys=np.array(periods.index); previous=[]
        for d in f.day:
            date=dt.date(1970,1,1)+dt.timedelta(days=int(d)); wk=date.toordinal()-date.weekday()
            j=np.searchsorted(keys,wk,side='left')-1
            previous.append(j)
    else:
        periods=days; keys=np.array(periods.index)
        previous=np.searchsorted(keys,f.day.to_numpy(),side='left')-1
    out=np.full((len(f),7),np.nan)
    if not len(periods):return out
    previous=np.asarray(previous);valid=previous>=0
    if not weekly:valid &= (f.day.to_numpy()-keys[np.maximum(previous,0)])<=5
    h,l,c=periods[['h','l','c']].to_numpy()[previous[valid]].T
    p=(h+l+c)/3;bc=(h+l)/2;tc=2*p-bc
    out[valid]=np.column_stack([p,2*p-l,p+h-l,2*p-h,p-h+l,np.minimum(bc,tc),np.maximum(bc,tc)])
    return out

def indicators(f):
    result={'daily':pivots(f),'weekly':pivots(f,True)}
    for minutes in (5,15,30):
        g=aggregate(f,minutes)
        for period,mult in (PARAMS if minutes==30 else [(10,3)]):
            st=supertrend(g,period,mult)
            result[(minutes,period,mult)]={'end':g.end.to_numpy(),**st}
    g=aggregate(f,30); result['bar30']=g; result['macd']=macd(g.c)
    result['fast']=pd.Series(g.c).ewm(span=12,adjust=False).mean().to_numpy()
    result['slow']=pd.Series(g.c).ewm(span=26,adjust=False).mean().to_numpy()
    result['array']=f.to_numpy();result['columns']={c:j for j,c in enumerate(f.columns)};result['days']=sorted(set(f.day));result['end30']=g.end.to_numpy()
    result['atr5']=atr(f)
    return result

def at(st,time):
    k=np.searchsorted(st['end'],time,side='right')-1
    if k<0:return None
    if time-st['end'][k]>3600:return None
    if not np.isfinite(st['atr'][k]):return None
    return {key:value[k] for key,value in st.items() if key!='end'}

def pivot_features(p,c,atr_value,side,prior_c,prior_l,prior_h):
    if not np.isfinite(p).all() or not (atr_value>0):return None
    P,R1,R2,S1,S2,BC,TC=p
    levels=sorted([S2,S1,P,R1,R2])
    obstacles=[x for x in levels if (x-c)*side>0]
    room=min([abs(x-c)/atr_value for x in obstacles],default=math.inf)
    nearest=min(levels,key=lambda x:abs(x-c))
    break_level=any((prior_c-x)*side<=0<(c-x)*side for x in levels)
    rejection=any((prior_l<=x<=prior_h) and (c-x)*side>0 and abs(c-x)<=atr_value for x in levels)
    return {'above_p':c>P,'aligned_p':(c-P)*side>0,'aligned_cpr':c>TC if side>0 else c<BC,
            'against_cpr':c<BC if side>0 else c>TC,'inside_cpr':BC<=c<=TC,
            'room':room,'break':break_level,'rejection':rejection,'nearest_dist':abs(c-nearest)/atr_value}

def conditions(f,ind,i,spot,spotind,side,time):
    values={}; complete=True
    for label,table,direction in [('premium',ind,1),('spot',spotind,side)]:
        for minutes in (5,15,30):
            for period,mult in (PARAMS if minutes==30 else [(10,3)]):
                st=at(table[(minutes,period,mult)],time) if table else None
                prefix=f'{label}_st{minutes}_{period}_{mult}'
                if st is None:
                    complete=False; values[prefix+'_align']=None
                    if (minutes,period,mult)==(30,10,3):values[prefix+'_oppose']=values[prefix+'_fresh']=None
                    continue
                align=st['dir']==direction
                values[prefix+'_align']=align
                if (minutes,period,mult)==(30,10,3):
                    values[prefix+'_oppose']=not align; values[prefix+'_fresh']=align and st['age']<=2
        if label=='premium':g=f; k=i; idx=ind
        elif spot is not None:
            g=spot; k=np.searchsorted(g.end.to_numpy(),time,side='right')-1; idx=spotind
        else:g=None;k=-1;idx=None
        for kind in ('daily','weekly'):
            pv=None
            if g is not None and k>=1 and g.day.iloc[k]==f.day.iloc[i]:
                pv=pivot_features(idx[kind][k],g.c.iloc[k],idx['atr5'][k],direction,g.c.iloc[k-1],g.l.iloc[k-1],g.h.iloc[k-1])
            for name in ('aligned_p','aligned_cpr','against_cpr','inside_cpr','break','rejection'):
                values[f'{label}_{kind}_{name}']=None if pv is None else pv[name]
            values[f'{label}_{kind}_room']=None if pv is None else pv['room']>=0.5
            values[f'{label}_{kind}_near_obstacle']=None if pv is None else pv['room']<0.5
    values['combined_st_pivot']=None if values['premium_st30_10_3_align'] is None or values['spot_st30_10_3_align'] is None or values['spot_daily_room'] is None else values['premium_st30_10_3_align'] and values['spot_st30_10_3_align'] and values['spot_daily_room']
    return {k:None if v is None else bool(v) for k,v in values.items()}

def simulate(f,ind,i,entry_i,expiry,smart,overlay,spotind,side):
    """Five-minute conservative OHLC replay, no current-bar high used in stop."""
    a=ind['array']; col=ind['columns']
    entry=float(a[entry_i,col['o']]); startday=int(a[entry_i,col['day']]); alldays=ind['days']
    expiry_day=(dt.date.fromisoformat(expiry)-dt.date(1970,1,1)).days if expiry else 10**9
    lastday=alldays[min(bisect.bisect_left(alldays,startday)+3,len(alldays)-1)]
    if entry<1:return None
    entry30=np.searchsorted(ind['bar30'].end.to_numpy(),f.t.iloc[entry_i],side='right')-1
    if smart:
        fast=ind['fast'];slow=ind['slow']
        # Preview forming bar at the last completed five-minute close; do not commit it to EMA state.
        preview=f.c.iloc[entry_i-1]
        if entry30<0 or fast[entry30]+2/13*(preview-fast[entry30])-(slow[entry30]+2/27*(preview-slow[entry30]))<=0:return 'faded'
    peak=entry; stop=entry*(0.6 if smart else 0.7); trail=None; left=4; stage=0; fills=[]
    bar30=ind['bar30']; mac=ind['macd']; mac_idx=entry30
    for j in range(entry_i,len(f)):
        row=a[j]; ts=int(row[col['t']]); end=int(row[col['end']]); day=int(row[col['day']])
        o,h,l,c=(float(row[col[x]]) for x in ['o','h','l','c'])
        if row[col['n']] < (end-ts)//60:
            return None
        if j>entry_i and ts-int(a[j-1,col['end']])>300 and day==int(a[j-1,col['day']]):return None
        minute=(ts+19800)%86400//60
        reason=None; price=None
        if day>expiry_day or (day==expiry_day and minute>=910):reason='expiry';price=o
        elif l<=max(stop,trail or 0):reason='stop';price=min(o,max(stop,trail or 0))
        elif smart and h>=entry*2:reason='target';price=max(o,entry*2)
        if reason:
            fills.append((ts if price==o else end,price,left,reason));left=0;break
        # Partial stages at configured barriers; one lot retained. Stop checked first above.
        while stage<3 and left>1 and h>=entry*(1+[.3,.5,.75][stage]):
            fills.append((end,entry*(1+[.3,.5,.75][stage]),1,'partial'));left-=1;stage+=1
        oldpeak=peak; peak=max(peak,h)
        if peak>=entry*(1.4 if smart else 1.3):
            trail=max(trail or 0,entry*1.0011,peak*(.7 if smart else .75))
        k=np.searchsorted(ind['end30'],end,side='right')-1
        if k>mac_idx and k>=0:
            if mac[k]<0 and oldpeak<entry*1.1:reason='macd_invalid'
            if overlay=='premium_st' and ind[(30,10,3)]['dir'][k]<0:reason='st_exit'
            if overlay=='spot_st':
                st=at(spotind[(30,10,3)],end) if spotind else None
                if st and st['dir']!=side:reason='spot_st_exit'
            mac_idx=k
        if overlay=='pivot_loss' and np.isfinite(ind['daily'][j,0]) and c<ind['daily'][j,0]:reason='pivot_exit'
        if day==lastday and (end+19800)%86400//60>=925:reason='hold_limit'
        if reason:
            nxt=j+1
            # A close-driven exit can fill only on the next observed bar, except at session close.
            if nxt<len(f) and int(f.t.iloc[nxt])-end<=300:
                fills.append((int(f.t.iloc[nxt]),float(f.o.iloc[nxt]),left,reason));left=0;break
            if reason=='hold_limit':fills.append((end,c,left,reason));left=0;break
    if left:return None
    return {'entry':entry,'entry_ts':int(f.t.iloc[entry_i]),'exit_ts':int(fills[-1][0]),'fills':fills,'raw_ret':sum(p*n for _,p,n,_ in fills)/(entry*4)-1}

def net_return(trade,lot,slip):
    if not isinstance(trade,dict):return 0.0
    entry=trade['entry']*(1+slip/10000); units=4*lot
    debit=entry*units*1.001+20
    credit=sum(p*(1-slip/10000)*n*lot*.998-20 for _,p,n,_ in trade['fills'])
    return (credit-debit)/debit

def root_mapping():
    cache=json.loads((WORKSPACE/'runtime/atm_contracts.json').read_text())['contracts']
    maps={}; lots={}
    for c in cache:
        maps[c['underlying']]=c['spot_symbol']; lots[c['underlying']]=int(c.get('lot_size') or 0)
    maps.update({'NIFTY':'NSE:NIFTY50-INDEX','BANKNIFTY':'NSE:NIFTYBANK-INDEX','FINNIFTY':'NSE:FINNIFTY-INDEX','MIDCPNIFTY':'NSE:MIDCPNIFTY-INDEX','SENSEX':'BSE:SENSEX-INDEX'})
    for root in maps:lots.setdefault(root,{'NIFTY':65,'BANKNIFTY':30,'SENSEX':20,'FINNIFTY':60,'MIDCPNIFTY':120}.get(root,0))
    return maps,lots

def build_events():
    maps,lots=root_mapping(); roots=sorted(maps,key=len,reverse=True)
    spots={}; metadata=json.loads((ROOT/'source_manifest.json').read_text())
    with gzip.open(ROOT/'candles5.jsonl.gz','rt') as src:
        for line in src:
            record=json.loads(line)
            if record['asset']!='option':spots[record['symbol']]=frame(record)
    spotind={s:indicators(f) for s,f in spots.items()}
    marketdays=sorted({int(d) for f in spots.values() for d in f.day})
    last_entry_day=marketdays[-5]
    events=[]; expiries=json.loads((ROOT/'resolved_expiries.json').read_text())
    with gzip.open(ROOT/'candles5.jsonl.gz','rt') as src:
        for series_number,line in enumerate(src):
            record=json.loads(line)
            if record['asset']!='option':continue
            Q['option_series']+=1
            body=record['symbol'].split(':')[-1]
            root=next((r for r in roots if body.startswith(r) and body[len(r):len(r)+2]=='26'),None)
            if root is None or not lots.get(root):Q['missing_root_lot']+=1;continue
            f=frame(record)
            if len(f)<200:Q['too_short']+=1;continue
            ind=indicators(f); g=ind['bar30']; m=ind['macd']
            crosses=np.flatnonzero((m>0)&(np.r_[np.nan,m[:-1]]<=0)&(np.arange(len(m))>=80))
            side=1 if body.endswith('CE') else -1; typ='CE' if side>0 else 'PE'
            sp=spots.get(maps[root]); spi=spotind.get(maps[root]); expiry=expiries.get(record['symbol']) or record['expiry']
            if not expiry:Q['missing_expiry_series']+=1;continue
            for k in crosses:
                time=int(g.end.iloc[k]); day=int(g.day.iloc[k]); Q['crosses']+=1
                if day>last_entry_day:Q['late_entry']+=1;continue
                if g.n.iloc[k] < (g.end.iloc[k]-g.t.iloc[k])/60:Q['incomplete_signal']+=1;continue
                if expiry and date_str(day)>=expiry:Q['expiry_entry']+=1;continue
                i=np.searchsorted(f.end.to_numpy(),time,side='right')-1
                ei=np.searchsorted(f.t.to_numpy(),time,side='left'); si=np.searchsorted(f.t.to_numpy(),time+300,side='left')
                if si>=len(f) or int(f.t.iloc[si])!=time+300 or int(f.t.iloc[ei])!=time:Q['missing_entry']+=1;continue
                if f.v.iloc[ei]<=0 or f.o.iloc[ei]<1:Q['illiquid_entry']+=1;continue
                if si+1>=len(f):continue
                feats=conditions(f,ind,i,sp,spi,side,time)
                row={'id':len(events),'symbol':record['symbol'],'root':root,'option_type':typ,'is_index':maps[root].endswith('-INDEX'),'day':day,'date':date_str(day),'signal_ts':time,'lot':lots[root],'expiry':expiry,'features':feats,'trades':{}}
                for lane,smart,enter in [('standard',False,ei),('smart',True,si)]:
                    for overlay in ['baseline','premium_st','spot_st','pivot_loss']:
                        row['trades'][lane+'_'+overlay]=simulate(f,ind,i,enter,expiry,smart,overlay,spi,side)
                # All configurations use the same resolved opportunity set. Censor count disclosed.
                if any(t is None for t in row['trades'].values()):Q['censored']+=1;continue
                events.append(row);Q['eligible']+=1
            if series_number%250==0:print('processed',series_number,'events',len(events),flush=True)
    with gzip.open(ROOT/'events.jsonl.gz','wt') as out:
        for row in events:out.write(json.dumps(row,separators=(',',':'))+'\n')
    (ROOT/'quality.json').write_text(json.dumps({'counts':dict(Q),'market_days':[date_str(d) for d in marketdays],'last_entry_day':date_str(last_entry_day),'lot_assumption':'3 Oct current lot inventory by underlying; historical lots not independently reconstructed'},indent=2))
    return events

def t_test(x):
    x=np.asarray(x,dtype=float);n=len(x)
    se=x.std(ddof=1)/math.sqrt(n) if n>1 else math.inf
    t=float(x.mean()/se) if se>0 and math.isfinite(se) else 0
    return t,float(2*student_t.sf(abs(t),n-1)) if n>1 else 1

def holm(p):
    p=np.asarray(p); order=np.argsort(p); result=np.zeros(len(p)); high=0
    for rank,i in enumerate(order):high=max(high,p[i]*(len(p)-rank));result[i]=min(1,high)
    return result

def analyze(events):
    days=sorted({r['day'] for r in events});split=days[len(days)//2]; dayidx={d:i for i,d in enumerate(days)}
    names=sorted(events[0]['features']); rows=[]; daily_values={}
    for lane in ['standard','smart']:
        base=np.array([net_return(r['trades'][lane+'_baseline'],r['lot'],5) for r in events])
        for name in ['baseline','no_trade']+names+['exit_premium_st','exit_spot_st','exit_pivot_loss']:
            available=np.ones(len(events),bool);take=np.ones(len(events),bool)
            overlay='baseline'
            if name=='no_trade':take[:]=False
            elif name.startswith('exit_'):overlay=name[5:]
            elif name!='baseline':
                available=np.array([r['features'].get(name) is not None for r in events]);take=np.array([r['features'].get(name) is True for r in events])
            returns=np.array([net_return(r['trades'][lane+'_'+overlay],r['lot'],5) for r in events])*take
            executed=np.array([isinstance(r['trades'][lane+'_'+overlay],dict) for r in events])
            # Missing context declines entries operationally, but inference compares the known-context universe.
            compare=np.where(available,returns-base,0)
            daily=np.bincount([dayidx[r['day']] for r in events],weights=compare,minlength=len(days))
            daily_values[lane+'|'+name]=daily.tolist()
            for period,mask in [('full',np.ones(len(events),bool)),('train',np.array([r['day']<split for r in events])),('holdout',np.array([r['day']>=split for r in events]))]:
                selected_days=[d for d in days if (period=='full' or (d<split if period=='train' else d>=split))]
                dv=np.array([daily[dayidx[d]] for d in selected_days]);t,p=t_test(dv)
                weekly=collections.defaultdict(float)
                for d in selected_days:
                    date=dt.date(1970,1,1)+dt.timedelta(days=int(d));iso=date.isocalendar();weekly[(iso.year,iso.week)]+=daily[dayidx[d]]
                week_t,week_p=t_test(list(weekly.values()))
                kept=mask&take&available&executed
                chosen=returns[kept]
                row={'lane':lane,'rule':name,'period':period,'opportunities':int(mask.sum()),'known':int((mask&available).sum()),'trades':int(kept.sum()),'retained_pct':100*kept.sum()/max(1,(mask&available&executed).sum()),'mean_net_pct':100*chosen.mean() if len(chosen) else 0,'sum_return_units':float(returns[mask].sum()),'baseline_same_known_units':float(base[mask&available].sum()),'delta_units':float(compare[mask].sum()),'daily_delta_t':t,'p_raw':p,'win_pct':100*(chosen>0).mean() if len(chosen) else 0,'CE_mean_pct':0.,'PE_mean_pct':0.}
                for typ in ['CE','PE']:
                    tm=kept&np.array([r['option_type']==typ for r in events]);row[typ+'_mean_pct']=float(100*returns[tm].mean()) if tm.sum() else 0
                    row[typ+'_trades']=int(tm.sum())
                row.update({'weekly_delta_t':week_t,'weekly_p_raw':week_p,'week_clusters':len(weekly)})
                rows.append(row)
    summary=pd.DataFrame(rows)
    for period in ['full','train','holdout']:
        mask=(summary.period==period)&(summary.rule!='baseline');summary.loc[mask,'p_holm']=holm(summary.loc[mask,'p_raw']);summary.loc[mask,'weekly_p_holm']=holm(summary.loc[mask,'weekly_p_raw'])
    summary.to_csv(ROOT/'results.csv',index=False)
    (ROOT/'daily_deltas.json').write_text(json.dumps({'days':[date_str(d) for d in days],'split':date_str(split),'values':daily_values},indent=2))
    # Fixed-rule out-of-sample selection, labels must have resolved before the four-session purge.
    wf=[]
    for lane in ['standard','smart']:
        for start in range(15,len(days),5):
            training=days[:start-4];testing=days[start:start+5]
            trainset=set(training);testset=set(testing)
            candidates=['baseline','no_trade']+names
            totals={}
            for rule in candidates:
                eligible=[r for r in events if r['day'] in trainset and (r['trades'][lane+'_baseline']=='faded' or isinstance(r['trades'][lane+'_baseline'],dict) and r['trades'][lane+'_baseline']['exit_ts']<days[start]*86400-19800+33300)]
                totals[rule]=sum(net_return(r['trades'][lane+'_baseline'],r['lot'],5) for r in eligible if rule=='baseline' or r['features'].get(rule) is True)
            champion=max(totals,key=totals.get)
            test=[r for r in events if r['day'] in testset]
            base=sum(net_return(r['trades'][lane+'_baseline'],r['lot'],5) for r in test)
            value=sum(net_return(r['trades'][lane+'_baseline'],r['lot'],5) for r in test if champion=='baseline' or r['features'].get(champion) is True)
            wf.append({'lane':lane,'train_end':date_str(training[-1]),'test_start':date_str(testing[0]),'test_end':date_str(testing[-1]),'rule':champion,'baseline_units':base,'selected_units':value,'delta_units':value-base})
    pd.DataFrame(wf).to_csv(ROOT/'walk_forward.csv',index=False)
    costs=[]
    for lane in ['standard','smart']:
        for slip in [5,25,50]:
            for rule in ['baseline','premium_st30_10_3_align','spot_st30_10_3_align','spot_daily_aligned_p','spot_daily_room','combined_st_pivot']:
                ret=[net_return(r['trades'][lane+'_baseline'],r['lot'],slip) for r in events if isinstance(r['trades'][lane+'_baseline'],dict) and (rule=='baseline' or r['features'].get(rule) is True)]
                costs.append({'lane':lane,'slippage_bps':slip,'rule':rule,'trades':len(ret),'mean_net_pct':100*np.mean(ret) if ret else 0,'sum_return_units':sum(ret)})
    pd.DataFrame(costs).to_csv(ROOT/'cost_sensitivity.csv',index=False)
    return summary

if __name__=='__main__':
    import sys
    if '--analyze-only' in sys.argv:
        with gzip.open(ROOT/'events.jsonl.gz','rt') as f:events=[json.loads(line) for line in f]
    else:events=build_events()
    result=analyze(events)
    print(result[(result.period=='holdout')&(result.rule.isin(['baseline','premium_st30_10_3_align','spot_st30_10_3_align','spot_daily_aligned_p','spot_daily_room','combined_st_pivot']))].to_string(index=False),flush=True)
