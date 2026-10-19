"""Causal premium pre-zero behavior and early-entry research; no live writes."""
from __future__ import annotations
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from scipy.stats import rankdata, t as student_t

ROOT=Path(__file__).resolve().parent
INPUT=ROOT.parent/'september_supertrend_pivots_20261003/followup'
sys.path[:0]=[str(INPUT),str(INPUT.parent)]
import smart_replay as helper
st=helper.st
RULES=['turn','closed_st','preview_st','above_p','reclaim_p','preview_st_p','preview_st_room','underlying_context']
NAMES={'turn':'MACD turn only','closed_st':'Completed premium ST','preview_st':'Forming premium ST','above_p':'Premium above P','reclaim_p':'Premium reclaims P','preview_st_p':'Forming ST + above P','preview_st_room':'Forming ST + premium room','underlying_context':'Forming ST + underlying context','cross5':'Cross + 5 minutes','cross1':'Cross + 1 minute'}
BEGIN='2026-09-01';END='2026-09-30';HOLD_BEGIN='2026-09-15';TRAIN_END='2026-09-11'

def zero_price(fast,slow):return (slow*(1-2/27)-fast*(1-2/13))/(2/13-2/27)

def bands(g):
    atr,direction,line=st.st(g);h=g.h.to_numpy();l=g.l.to_numpy();c=g.c.to_numpy();u=(h+l)/2+3*atr;d=(h+l)/2-3*atr
    for i in range(10,len(g)):
        if not(u[i]<u[i-1] or c[i-1]>u[i-1]):u[i]=u[i-1]
        if not(d[i]>d[i-1] or c[i-1]<d[i-1]):d[i]=d[i-1]
    return u,d

def preview_st(price,high,low,prior_close,prior_atr,prior_upper,prior_lower,prior_direction):
    tr=max(high-low,abs(high-prior_close),abs(low-prior_close));atr=(prior_atr*9+tr)/10
    u=(high+low)/2+3*atr;d=(high+low)/2-3*atr
    if not(u<prior_upper or prior_close>prior_upper):u=prior_upper
    if not(d>prior_lower or prior_close<prior_lower):d=prior_lower
    direction=(1 if price>u else -1) if prior_direction<0 else (-1 if price<d else 1)
    return direction,(d if direction>0 else u),atr

def first_per_bucket(mask,buckets):
    ix=np.flatnonzero(mask)
    if not len(ix):return ix
    _,first=np.unique(buckets[ix],return_index=True)
    return ix[np.sort(first)]

def masks(values):
    c=values
    return {'turn':True,'closed_st':c['closed_st_bull']==1,'preview_st':c['preview_st_bull']==1,'above_p':c['above_p']==1,'reclaim_p':c['reclaim_p']==1,
            'preview_st_p':c['preview_st_bull']==1 and c['above_p']==1,'preview_st_room':c['preview_st_bull']==1 and c['premium_room']>=.5,
            'underlying_context':c['preview_st_bull']==1 and c['underlying_st']==1 and c['underlying_room']>=.5}

class Data:
    def __init__(self,archive):
        self.archive=archive;self.series={};self.days=[];self.selected={};self.context={};self.events=[];self.stream=collections.defaultdict(list);self.cross_stream=collections.defaultdict(list);self.coverage=[]
        d=dt.date.fromisoformat(BEGIN)
        while d<=dt.date.fromisoformat(END):
            if st.is_trading_day(d):self.days.append(d.isoformat())
            d+=dt.timedelta(days=1)
        wanted=set()
        for day in self.days:
            selected,context=archive.select(day);self.selected[day]=set(selected);self.context[day]=context;wanted.update(selected)
            self.coverage.append({'day':day,'selected_contracts':len(selected),'roots':len({archive.contracts[s]['root'] for s in selected})})
        for n,s in enumerate(sorted(wanted)):
            if s not in archive.tables:continue
            self.build_series(s)
            if n%150==0:print('causal minute features',n,flush=True)

    def build_series(self,s):
        a=self.archive.minutes[s];table=self.archive.tables[s];g=table['bars'];j=np.searchsorted(g.t.to_numpy(),a[:,0],side='right')-1;p=np.maximum(j-1,0);price=a[:,4];time=a[:,0].astype(np.int64)+60
        m=table['fast'][p]+(price-table['fast'][p])*2/13-table['slow'][p]-(price-table['slow'][p])*2/27
        signal=table['signal'][p]+(m-table['signal'][p])*2/10
        zp=zero_price(table['fast'][p],table['slow'][p]);closed=np.searchsorted(table['end'],time,side='right')-1
        u,l=bands(g)
        ph=pd.Series(a[:,2]).groupby(j).cummax().to_numpy();pl=pd.Series(a[:,3]).groupby(j).cummin().to_numpy()
        tr=np.maximum(ph-pl,np.maximum(abs(ph-g.c.to_numpy()[p]),abs(pl-g.c.to_numpy()[p])));atr=(table['atr'][p]*9+tr)/10
        upper=(ph+pl)/2+3*atr;lower=(ph+pl)/2-3*atr
        upper=np.where((upper<u[p])|(g.c.to_numpy()[p]>u[p]),upper,u[p]);lower=np.where((lower>l[p])|(g.c.to_numpy()[p]<l[p]),lower,l[p])
        direction=np.where(table['direction'][p]<0,np.where(price>upper,1,-1),np.where(price<lower,-1,1));line=np.where(direction>0,lower,upper)
        cross=first_per_bucket((j>26)&(table['macd'][p]<=0)&(m>0)&(a[:,5]>0),j)
        cross_t=time[cross];firstcross=np.full(len(g),np.inf);firstcross[j[cross]]=cross_t
        rising=np.zeros(len(a),bool);slope=np.full(len(a),np.nan)
        if len(a)>3:
            rising[3:]=(np.diff(m)[2:]>0)&(np.diff(m)[1:-1]>0)&(np.diff(m)[:-2]>0)&(j[3:]==j[:-3])&(time[3:]-time[:-3]==180)&(a[3:,5]>0)&(a[2:-1,5]>0)&(a[1:-2,5]>0)&(a[:-3,5]>0)
            slope[3:]=(m[3:]-m[:-3])/3
        projected=np.divide(-m,slope,out=np.full(len(a),np.inf),where=slope>0)
        base=(j>26)&(table['macd'][p]<=0)&(m<0)&(m>signal)&rising&(projected<=30)&(time<firstcross[np.maximum(j,0)])
        valid_close=table['end'][table['macd']>0]
        self.series[s]={'a':a,'t':time,'m':m,'signal':signal,'zero':zp,'closed':closed,'j':j,'table':table,'cross_t':cross_t,'positive_closed':valid_close,'base':base,'slope':slope,'projected':projected,'st_direction':direction,'st_line':line}
        for day in self.days:
            if s not in self.selected[day]:continue
            begin=st.epoch(day,'09:16');finish=st.epoch(day,'14:45');in_day=(time>=begin)&(time<=finish)
            cc=cross[(time[cross]>=begin)&(time[cross]<=st.epoch(day,'15:20'))]
            for k in cc:self.cross_stream[int(time[k])].append({'symbol':s,'kind':'cross','ts':int(time[k]),'index':int(k),'day':day})
            ix=np.flatnonzero(base&in_day);seen={r:set() for r in RULES}
            for k in ix:
                bucket=int(j[k]);feature=self.features(s,int(k));ok=masks(feature)
                for rule in RULES:
                    if not ok[rule] or bucket in seen[rule]:continue
                    seen[rule].add(bucket);e={'symbol':s,'rule':rule,'kind':'early','ts':int(time[k]),'index':int(k),'day':day,'bucket':int(g.t.iloc[bucket]),**feature}
                    self.labels(e);self.events.append(e);self.stream[e['ts']].append(e)

    def features(self,s,k):
        d=self.series[s];a=d['a'];table=d['table'];time=int(d['t'][k]);p=int(d['closed'][k]);c=self.archive.contracts[s];side=1 if c['side']=='CE' else -1;u=self.archive.tables.get(c['spot_symbol']);ui=st.available(u,time)
        premium_p=st.previous_pivots(table,time);spot_p=st.previous_pivots(u,time) if u else None;spot_q=self.archive.quote(c['spot_symbol'],time,300)
        atr=table['atr'][p] if p>=0 else np.nan;P=premium_p[0] if premium_p else np.nan;price=float(a[k,4]);line=float(d['st_line'][k]);room=np.nan;u_room=np.nan
        if premium_p and atr>0:room=min([x-price for x in premium_p if x>price],default=5*atr)/atr
        if spot_p and spot_q and ui is not None and u['atr'][ui]>0:u_room=min([(x-spot_q[0])*side for x in spot_p if (x-spot_q[0])*side>0],default=5*u['atr'][ui])/u['atr'][ui]
        expiry=self.archive.expiries[s];dte=(dt.date.fromisoformat(expiry)-dt.date.fromisoformat(st.datestr(st.dayid(time)))).days
        return {'price':price,'side':c['side'],'dte':dte,'minutes_since_open':((time+19800)%86400-33300)/60,'macd_pct':100*d['m'][k]/price,'hist_pct':100*(d['m'][k]-d['signal'][k])/price,'slope_pct':100*d['slope'][k]/price,'zero_price':float(d['zero'][k]),'zero_gap_pct':100*(d['zero'][k]/price-1),'projected_minutes':float(d['projected'][k]),'log_premium':math.log(price),
                'closed_st_bull':float(table['direction'][p]>0) if p>=0 and np.isfinite(atr) else np.nan,'preview_st_bull':float(d['st_direction'][k]>0),'st_distance_atr':(price-line)/atr if atr>0 else np.nan,'st_zero_gap_atr':(d['zero'][k]-line)/atr if atr>0 else np.nan,'above_p':float(price>P) if np.isfinite(P) else np.nan,'reclaim_p':float(price>P and a[k-3,4]<=P) if np.isfinite(P) else np.nan,'pivot_distance_atr':(price-P)/atr if atr>0 and np.isfinite(P) else np.nan,'premium_room':float(room),'underlying_st':float(u['direction'][ui]==side) if ui is not None else np.nan,'underlying_room':float(u_room),'premium_atr_pct':100*atr/price if atr>0 else np.nan}

    def labels(self,e):
        d=self.series[e['symbol']];t=e['ts'];end=st.epoch(e['day'],'15:30');x=d['cross_t'];j=np.searchsorted(x,t,side='right');cross=int(x[j]) if j<len(x) and x[j]<=end else None;e['cross_ts']=cross;e['lead_minutes']=None if cross is None else (cross-t)/60
        for h in [30,60,120]:e[f'cross_{h}']=None if t+h*60>end else int(cross is not None and cross<=t+h*60)
        e['cross_by_close']=int(cross is not None);closed=d['positive_closed'];k=np.searchsorted(closed,t,side='right');e['confirmed_60']=None if t+3600>end else int(k<len(closed) and closed[k]<=t+3600)
        if cross is not None:
            z=self.entry_quote(e['symbol'],cross+300,True)
            e['delayed_price']=None if z is None else float(d['a'][z,4]);e['saving_pct']=None if z is None else 100*(d['a'][z,4]/e['price']-1)
        else:e['delayed_price']=None;e['saving_pct']=None

    def entry_quote(self,s,due,positive):
        d=self.series[s];k=np.searchsorted(d['t'],due);end=min(due+180,st.epoch(st.datestr(st.dayid(due)),'15:24'))
        while k<len(d['t']) and d['t'][k]<=end:
            if d['a'][k,5]>0:
                if (d['m'][k]>0) if positive else (d['m'][k]<0):return k
                return None
            k+=1
        return None

    def mark(self,s,time):return st.mark_price(s,time,self.archive.minutes,self.archive.expiries)

def exit_reason(p,price,time,macd_closed,cross,slip,timeout=30):
    # Scheduler arms failure at the deadline even if no executable tick exists.
    if cross and not p.get('forced') and (not p['early'] or time<=p['signal_ts']+timeout*60):p['cross_seen']=True
    if p['early'] and not p['cross_seen'] and time>=p['signal_ts']+timeout*60:p['forced']='NO_CROSS_TIMEOUT'
    p['peak']=max(p['peak'],price);ret=p['peak']/p['entry']-1
    if time>=st.epoch(p['entry_day'],'15:25'):return 'SESSION_FLATTEN'
    if p.get('forced'):return p['forced']
    if price<=p['entry']*.7:return 'HARD_STOP'
    if ret>=.3 and price<=max(p['peak']*.75,p['entry']/(1-slip)):return 'TRAIL'
    if p['cross_seen'] and macd_closed is not None and macd_closed<0 and ret<.1:return 'MACD_INVALIDATED'
    return None

def event_trade(data,e,early,slip=.0005,timeout=30):
    s=e['symbol'];d=data.series[s];cross=e['cross_ts'];due=e['ts']+60 if early else (None if cross is None or cross>e['ts']+timeout*60 else cross+300)
    if due is None:return {'filled':False,'return_pct':0.,'reason':'NO_CROSS'}
    k=data.entry_quote(s,due,not early)
    if k is None or (early and cross is not None and d['t'][k]>=cross):return {'filled':False,'return_pct':0.,'reason':'NO_EARLY_PRICE' if early else 'FADED_OR_NO_PRICE'}
    price=float(d['a'][k,4]);t=int(d['t'][k]);p={'entry':price*(1+slip),'peak':price,'early':early,'signal_ts':e['ts'],'cross_seen':not early,'entry_day':e['day']};end=st.epoch(e['day'],'15:30');out=None;reason=None
    for i in range(k+1,len(d['t'])):
        now=int(d['t'][i])
        if now>end:break
        if p['early'] and not p['cross_seen'] and now>e['ts']+timeout*60:p['forced']='NO_CROSS_TIMEOUT'
        if d['a'][i,5]<=0:continue
        cp=int(d['closed'][i]);closed=(d['table']['macd'][cp] if cp>=0 and d['table']['end'][cp]==now else None)
        before_deadline=cross is not None and cross<=e['ts']+timeout*60 and cross<=now
        reason=exit_reason(p,float(d['a'][i,4]),now,closed,before_deadline,slip,timeout)
        if reason:out=float(d['a'][i,4])*(1-slip);break
    if out is None:out,kind=data.mark(s,end);reason='UNRESOLVED_'+kind
    return {'filled':True,'return_pct':100*(out/p['entry']-1),'reason':reason,'entry_ts':t,'entry_price':price,'exit_ts':int(d['t'][i]) if reason and not reason.startswith('UNRESOLVED') else end,'closed':not reason.startswith('UNRESOLVED')}

class Book:
    def __init__(self,capital,rule,cost=0,timeout=30):
        self.capital=capital;self.rule=rule;self.cash=float(capital);self.realized=0.;self.positions={};self.pending={};self.episodes=[];self.fills=[];self.marks=[];self.slip=(5+cost)/10000;self.fee=20. if cost else 0.;self.cost=cost;self.timeout=timeout;self.counts=collections.Counter();self.minimum_cash=capital

    def offer(self,e,early):
        s=e['symbol']
        if s in self.positions or s in self.pending:self.counts['held_or_pending']+=1;return
        delay=60 if early or self.rule=='cross1' else 300
        self.pending[s]={**e,'early':early,'due':e['ts']+delay}

    def buy(self,data,s,price,time,early,e):
        c=data.archive.contracts[s];lot=c['lot_size'];equity=self.cash+sum(p['qty']*p['last'] for p in self.positions.values());ticket=min(100000,equity*.1);liquidity=data.context[e['day']][s]['volume'];cap=int(liquidity*.02//lot)
        reserve=self.fee*(len(self.positions)+2)
        lots=max(0,int(min(ticket,self.cash-reserve)//(lot*price*(1+self.slip))));lots=min(lots,cap,125)
        if lots<1:self.counts['cash_lot_or_liquidity_rejected']+=1;return
        qty=lots*lot;debit=qty*price*(1+self.slip)+self.fee;assert debit<=self.cash+.01
        p={'symbol':s,'entry_ts':time,'entry_day':e['day'],'entry':price*(1+self.slip),'peak':price,'early':early,'signal_ts':e['ts'],'cross_seen':not early,'qty':qty,'lot':lot,'cost':debit,'last':price,'rule':self.rule,'source':'early' if early else 'wait','credit':None,'exit_ts':None,'reason':None}
        self.cash-=debit;self.positions[s]=p;self.episodes.append(p);self.fills.append({'symbol':s,'ts':time,'side':'BUY','quantity':qty,'price':price*(1+self.slip),'fees':self.fee});self.counts['early_filled' if early else 'wait_filled']+=1

    def sell(self,s,price,time,reason):
        p=self.positions.pop(s);credit=p['qty']*price*(1-self.slip)-self.fee;self.cash+=credit;self.realized+=credit-p['cost'];p.update(credit=credit,exit_ts=time,reason=reason,pnl=credit-p['cost']);self.fills.append({'symbol':s,'ts':time,'side':'SELL','quantity':p['qty'],'price':price*(1-self.slip),'fees':self.fee})

    def step(self,data,time):
        day=st.datestr(st.dayid(time))
        for s,p in list(self.positions.items()):
            d=data.series[s];i=np.searchsorted(d['t'],time)
            crossing=False
            if i<len(d['t']) and d['t'][i]==time:
                j=np.searchsorted(d['cross_t'],time);cross=j<len(d['cross_t']) and d['cross_t'][j]==time;crossing=cross
                if cross and not p.get('forced') and time<=p['signal_ts']+self.timeout*60:p['cross_seen']=True
                if p['early'] and not p['cross_seen'] and time>=p['signal_ts']+self.timeout*60:p['forced']='NO_CROSS_TIMEOUT'
                if d['a'][i,5]>0 and data.archive.expiries[s]>=day:
                    cp=d['closed'][i];closed=d['table']['macd'][cp] if cp>=0 and d['table']['end'][cp]==time else None
                    reason=exit_reason(p,float(d['a'][i,4]),time,closed,cross,self.slip,self.timeout)
                    if reason:self.sell(s,float(d['a'][i,4]),time,reason);continue
            if p['early'] and not p['cross_seen'] and time>=p['signal_ts']+self.timeout*60:p['forced']='NO_CROSS_TIMEOUT'
            value,kind=data.mark(s,time);assert value is not None,(s,time,kind);p['last']=value
        for s,e in sorted(list(self.pending.items())):
            if time<e['due']:continue
            if st.dayid(time)!=st.dayid(e['ts']) or time>e['due']+180 or time>=st.epoch(day,'15:25'):
                self.counts['pending_expired']+=1;del self.pending[s];continue
            d=data.series[s];i=np.searchsorted(d['t'],time)
            if i>=len(d['t']) or d['t'][i]!=time or d['a'][i,5]<=0:continue
            # A pre-cross order must not be filled after any intervening cross.
            j=np.searchsorted(d['cross_t'],e['ts'],side='right');prior_cross=j<len(d['cross_t']) and d['cross_t'][j]<=time
            qualifies=(d['m'][i]<0 and not prior_cross) if e['early'] else d['m'][i]>0
            del self.pending[s]
            if not qualifies:self.counts['entry_condition_faded']+=1;continue
            self.buy(data,s,float(d['a'][i,4]),time,e['early'],e)
        self.minimum_cash=min(self.minimum_cash,self.cash);assert self.cash>=-.01

    def snapshot(self,data,day):
        quality=collections.Counter();expired=0
        for s,p in self.positions.items():
            value,kind=data.mark(s,st.epoch(day,'15:30'));assert value is not None;p['last']=value;quality[kind]+=1
            if kind=='expiry-intrinsic-scenario':expired+=1
        self.marks.append({'day':day,'equity':self.cash+sum(p['qty']*p['last'] for p in self.positions.values()),'cash':self.cash,'realized':self.realized,'open':len(self.positions),'mark_kinds':dict(quality),'expired_residuals':expired})

def run_books(data,rules,cost=0,timeout=30):
    books={f'{cap}|{rule}|{cost}|{timeout}':Book(cap,rule,cost,timeout) for cap in [100000,5000000] for rule in rules}
    for day in data.days:
        for time in range(st.epoch(day,'09:16'),st.epoch(day,'15:30')+1,60):
            for b in books.values():b.step(data,time)
            early=data.stream.get(time,[]);cross=data.cross_stream.get(time,[])
            for b in books.values():
                if b.rule in RULES:
                    for e in sorted(early,key=lambda e:e['symbol']):
                        if e['rule']==b.rule:b.offer(e,True)
                for e in sorted(cross,key=lambda e:e['symbol']):b.offer(e,False)
        for b in books.values():b.snapshot(data,day)
        print('books',cost,timeout,day,'baseline50L',round(books[f'5000000|cross5|{cost}|{timeout}'].marks[-1]['equity'],2),flush=True)
    out={}
    for name,b in books.items():
        for p in b.positions.values():p['pnl']=p['qty']*p['last']-p['cost'];p['unresolved']=True
        assert abs(sum(p['pnl'] for p in b.episodes)-(b.marks[-1]['equity']-b.capital))<.02
        assert all(p['qty']>0 and p['qty']%p['lot']==0 for p in b.episodes)
        out[name]={'capital':b.capital,'rule':b.rule,'extra_bps':cost,'fee_per_order':b.fee,'timeout':timeout,'minimum_cash':b.minimum_cash,'marks':b.marks,'episodes':b.episodes,'fills':b.fills,'counts':dict(b.counts)}
    return out

def auc(y,p):
    y=np.asarray(y);n=y.sum();z=len(y)-n
    return float((rankdata(p)[y==1].sum()-n*(n+1)/2)/(n*z)) if n*z else None

def metrics(y,p):return {'n':len(y),'positive_rate':float(np.mean(y)),'auc':auc(y,p),'brier':float(np.mean((p-y)**2)),'logloss':float(-np.mean(y*np.log(np.clip(p,1e-10,1))+(1-y)*np.log(np.clip(1-p,1e-10,1))))}

def logistic(train,test,columns):
    raw=train[columns].to_numpy(dtype=float);raw2=test[columns].to_numpy(dtype=float);missing=~np.isfinite(raw);missing2=~np.isfinite(raw2);raw[missing]=np.nan;raw2[missing2]=np.nan
    med=np.nanmedian(raw,axis=0);med=np.nan_to_num(med);raw=np.where(missing,med,raw);raw2=np.where(missing2,med,raw2);mean=raw.mean(axis=0);sd=raw.std(axis=0);sd=np.where(sd<1e-9,1,sd)
    X=np.column_stack([np.ones(len(raw)),np.clip((raw-mean)/sd,-5,5),missing.astype(float)]);T=np.column_stack([np.ones(len(raw2)),np.clip((raw2-mean)/sd,-5,5),missing2.astype(float)]);y=train.cross_30.to_numpy()
    def objective(w):
        z=X@w;p=expit(z);pen=w.copy();pen[0]=0
        return np.logaddexp(0,z).sum()-y@z+5*np.sum(pen**2),X.T@(p-y)+10*pen
    fit=minimize(objective,np.zeros(X.shape[1]),jac=True,method='L-BFGS-B',options={'maxiter':500,'ftol':1e-10});assert fit.success,fit.message
    return expit(T@fit.x),{'columns':columns,'median':med.tolist(),'mean':mean.tolist(),'scale':sd.tolist(),'weights':fit.x.tolist(),'lambda':10.,'trained_days':[BEGIN,TRAIN_END]}

def cluster_p(x):
    x=np.asarray(x,float);n=len(x)
    if n<3 or np.std(x,ddof=1)==0:return None
    return float(2*student_t.sf(abs(x.mean()/(x.std(ddof=1)/math.sqrt(n))),n-1))

def holm(values):
    valid=[(i,p) for i,p in enumerate(values) if p is not None];order=sorted(valid,key=lambda x:x[1]);out=[None]*len(values);maximum=0
    for k,(i,p) in enumerate(order):maximum=max(maximum,min(1,p*(len(order)-k)));out[i]=maximum
    return out

def summarize(data,events,books):
    f=pd.DataFrame(events);f.to_csv(ROOT/'events.csv.gz',index=False,compression='gzip');results=[]
    for period,begin,end in [('development',BEGIN,TRAIN_END),('holdout',HOLD_BEGIN,END),('sep18_29','2026-09-18','2026-09-29'),('sep19_29','2026-09-19','2026-09-29')]:
        for rule in RULES:
            b=f[(f.rule==rule)&(f.day>=begin)&(f.day<=end)];er=np.array([e['early_return_pct'] for e in b.to_dict('records') if e['early_filled']]);wait=b.wait_return_pct.to_numpy();early=b.early_return_pct.to_numpy()
            results.append({'period':period,'rule':rule,'trials':len(b),'cross_30_pct':100*b.cross_30.mean(),'cross_60_pct':100*b.cross_60.mean(),'cross_120_pct':100*b.cross_120.mean(),'confirmed_60_pct':100*b.confirmed_60.mean(),'cross_by_close_pct':100*b.cross_by_close.mean(),'early_fills':int(b.early_filled.sum()),'wait_fills':int(b.wait_filled.sum()),'early_mean_return_pct':float(er.mean()) if len(er) else None,'early_win_pct':float(np.mean(er>0)*100) if len(er) else None,'early_unit_pf':float(er[er>0].sum()/abs(er[er<0].sum())) if np.any(er<0) else None,'mean_pair_delta_pct_per_trial':float(np.mean(early-wait)) if len(b) else None,'median_lead_minutes':b.lead_minutes.median(),'median_saving_at_signal_pct':b.saving_pct.median(),'median_zero_gap_pct':b.zero_gap_pct.median(),'early_timeouts':int((b.early_reason=='NO_CROSS_TIMEOUT').sum()),'early_unresolved':int(b.early_reason.str.startswith('UNRESOLVED').sum())})
            timely=b[b.cross_30==1];paired=b[b.early_filled&b.wait_filled];closed=b[b.early_closed]
            returns=closed.early_return_pct.to_numpy();results[-1].update(median_timely_lead_minutes=timely.lead_minutes.median(),timely_price_saving_pct=(100*(paired.delayed_price/paired.actual_early_price-1)).median(),paired_actual_fills=len(paired),early_fully_closed=len(closed),early_closed_win_pct=float(np.mean(returns>0)*100) if len(returns) else None,early_closed_unit_pf=float(returns[returns>0].sum()/abs(returns[returns<0].sum())) if np.any(returns<0) else None)
    pd.DataFrame(results).to_csv(ROOT/'behavior_and_event_returns.csv',index=False)
    rows=[];daily=[]
    for name,b in books.items():
        marks=b['marks'];prev=b['capital']
        for m in marks:daily.append({'book':name,'capital':b['capital'],'rule':b['rule'],'extra_bps':b['extra_bps'],'timeout':b['timeout'],'day':m['day'],'pnl':m['equity']-prev});prev=m['equity']
        for period,begin,end in [('development',BEGIN,TRAIN_END),('holdout',HOLD_BEGIN,END),('full_sep',BEGIN,END),('sep18_29','2026-09-18','2026-09-29'),('sep19_29','2026-09-19','2026-09-29')]:
            prior=[m for m in marks if m['day']<begin];opening=prior[-1]['equity'] if prior else b['capital'];segment=[m for m in marks if begin<=m['day']<=end];ep=[p for p in b['episodes'] if begin<=p['entry_day']<=end];closed=[p for p in ep if p['exit_ts'] and p['exit_ts']<=st.epoch(end,'16:00')];pnl=np.array([p['pnl'] for p in closed]);ret=np.array([p['pnl']/p['cost']*100 for p in closed]);curve=np.array([opening]+[m['equity'] for m in segment])
            rows.append({'book':name,'capital':b['capital'],'rule':b['rule'],'extra_bps':b['extra_bps'],'timeout':b['timeout'],'period':period,'marked_pnl':segment[-1]['equity']-opening,'daily_drawdown':float(np.max(np.maximum.accumulate(curve)-curve)),'new_trades':len(ep),'early_trades':sum(p['early'] for p in ep),'fully_closed':len(closed),'closed_win_pct':float(np.mean(pnl>0)*100) if len(pnl) else None,'closed_pf':float(pnl[pnl>0].sum()/abs(pnl[pnl<0].sum())) if np.any(pnl<0) else None,'closed_mean_return_pct':float(ret.mean()) if len(ret) else None,'closed_median_return_pct':float(np.median(ret)) if len(ret) else None,'end_open':segment[-1]['open'],'expired_residuals':segment[-1]['expired_residuals']})
    r=pd.DataFrame(rows);d=pd.DataFrame(daily);base=r[r.rule=='cross5'][['capital','extra_bps','timeout','period','marked_pnl']].rename(columns={'marked_pnl':'baseline_pnl'});r=r.merge(base,on=['capital','extra_bps','timeout','period'],validate='many_to_one');r['delta_pnl']=r.marked_pnl-r.baseline_pnl;r.to_csv(ROOT/'book_results.csv',index=False);d.to_csv(ROOT/'book_daily.csv',index=False)
    development=r[(r.capital==5000000)&(r.extra_bps==0)&(r.timeout==30)&(r.period=='development')&r.rule.isin(['cross5']+RULES)];selected=development.sort_values(['marked_pnl','rule'],ascending=[False,True]).iloc[0].rule
    (ROOT/'training_selection.json').write_text(json.dumps({'rule':selected,'criterion':'development 50L marked P&L; includes no-early cross5 control','development_only':True},indent=2))
    trials=[]
    a=d[(d.capital==5000000)&(d.extra_bps==0)&(d.timeout==30)&(d.day>=HOLD_BEGIN)].pivot(index='day',columns='rule',values='pnl')
    for control in ['cross5','cross1']:
        for rule in RULES:
            z=(a[rule]-a[control]).to_numpy();trials.append({'rule':rule,'control':control,'sessions':len(z),'mean_daily_difference':float(z.mean()),'t':float(z.mean()/(np.std(z,ddof=1)/math.sqrt(len(z)))) if np.std(z,ddof=1)>0 else None,'p':cluster_p(z),'difference_total':float(z.sum())})
    train=f[(f.rule=='turn')&(f.day<=TRAIN_END)].copy();test=f[(f.rule=='turn')&(f.day>=HOLD_BEGIN)].copy();train['is_put']=(train.side=='PE').astype(int);test['is_put']=(test.side=='PE').astype(int)
    basecols=['zero_gap_pct','projected_minutes','slope_pct','hist_pct','log_premium','dte','minutes_since_open','is_put','premium_atr_pct'];extra=['closed_st_bull','preview_st_bull','st_distance_atr','st_zero_gap_atr','above_p','reclaim_p','pivot_distance_atr','premium_room','underlying_st','underlying_room']
    p1,m1=logistic(train,test,basecols);p2,m2=logistic(train,test,basecols+extra);y=test.cross_30.to_numpy();test['macd_probability']=p1;test['st_pivot_probability']=p2;test.to_csv(ROOT/'prediction_holdout.csv',index=False)
    losses=(p1-y)**2-(p2-y)**2;pred=pd.DataFrame({'day':test.day,'improvement':losses}).groupby('day').improvement.mean();trials.append({'rule':'MACD+ST+pivot model','control':'MACD geometry model','sessions':len(pred),'mean_daily_difference':float(pred.mean()),'p':cluster_p(pred.to_numpy()),'difference_total':None})
    for row,p in zip(trials,holm([row['p'] for row in trials])):row['p_holm_17']=p
    pd.DataFrame(trials).to_csv(ROOT/'holdout_tests.csv',index=False)
    (ROOT/'prediction_models.json').write_text(json.dumps({'no_smart_model_used':True,'target':'executable sampled MACD cross within 30 minutes','development_n':len(train),'holdout_n':len(test),'macd_only':{'metrics':metrics(y,p1),'model':m1},'macd_st_pivot':{'metrics':metrics(y,p2),'model':m2},'day_brier_improvement':pred.to_dict()},indent=2))
    calibration=[]
    for model in ['macd_probability','st_pivot_probability']:
        bins=pd.cut(test[model],[0,.2,.5,.8,1],include_lowest=True)
        for name,z in test.groupby(bins,observed=True):
            paired=z[z.early_filled&z.wait_filled];timely=z[z.cross_30==1]
            calibration.append({'model':model,'probability_band':str(name),'trials':len(z),'average_prediction':z[model].mean(),'cross_30_pct':100*z.cross_30.mean(),'early_fills':int(z.early_filled.sum()),'mean_early_return_pct_per_trial':z.early_return_pct.mean(),'mean_wait_return_pct_per_trial':z.wait_return_pct.mean(),'mean_pair_delta_pct':(z.early_return_pct-z.wait_return_pct).mean(),'median_timely_lead':timely.lead_minutes.median(),'paired_actual_fills':len(paired),'median_actual_price_saving_pct':(100*(paired.delayed_price/paired.actual_early_price-1)).median()})
    pd.DataFrame(calibration).to_csv(ROOT/'probability_diagnostics.csv',index=False)
    # A common MACD-turn population, balanced within geometry/day/side strata.
    test['gap_bin']=pd.cut(test.zero_gap_pct,[-np.inf,1,3,10,np.inf],labels=False);test['time_bin']=pd.cut(test.projected_minutes,[-np.inf,5,15,30.001],labels=False);balanced=[]
    for feature in ['closed_st_bull','preview_st_bull','above_p','reclaim_p']:
        cells=[]
        for key,z in test.groupby(['day','side','gap_bin','time_bin'],observed=True):
            left=z[z[feature]==0];right=z[z[feature]==1]
            if min(len(left),len(right))<3:continue
            cells.append((min(len(left),len(right)),left.cross_30.mean(),right.cross_30.mean()))
        weight=sum(x[0] for x in cells);balanced.append({'feature':feature,'common_strata':len(cells),'balanced_weight_each_state':weight,'negative_cross_rate':None if not weight else sum(w*a for w,a,b in cells)/weight,'positive_cross_rate':None if not weight else sum(w*b for w,a,b in cells)/weight})
    pd.DataFrame(balanced).to_csv(ROOT/'geometry_matched_states.csv',index=False)
    aligned=[]
    for items in data.cross_stream.values():
        for e in items:
            d=data.series[e['symbol']];cross_price=float(d['a'][e['index'],4])
            for offset in [-60,-30,-15,-10,-5,-1,0,5,15,30]:
                at=e['ts']+offset*60;k=np.searchsorted(d['t'],at)
                if k>=len(d['t']) or d['t'][k]!=at or st.dayid(at)!=st.dayid(e['ts']) or d['a'][k,5]<=0:continue
                f=data.features(e['symbol'],int(k));aligned.append({'day':e['day'],'symbol':e['symbol'],'cross_ts':e['ts'],'offset_minutes':offset,'premium_relative_to_cross_pct':100*(f['price']/cross_price-1),'macd_below_zero':int(d['m'][k]<0),'closed_st_bull':f['closed_st_bull'],'preview_st_bull':f['preview_st_bull'],'above_p':f['above_p'],'zero_gap_pct':f['zero_gap_pct']})
    al=pd.DataFrame(aligned);al.to_csv(ROOT/'cross_aligned.csv.gz',index=False,compression='gzip');asum=[]
    for period,lo,hi in [('development',BEGIN,TRAIN_END),('holdout',HOLD_BEGIN,END)]:
        for offset,z in al[(al.day>=lo)&(al.day<=hi)].groupby('offset_minutes'):
            asum.append({'period':period,'offset_minutes':int(offset),'observations':len(z),'median_premium_vs_cross_pct':z.premium_relative_to_cross_pct.median(),'below_zero_pct':100*z.macd_below_zero.mean(),'closed_st_bull_pct':100*z.closed_st_bull.mean(),'preview_st_bull_pct':100*z.preview_st_bull.mean(),'above_p_pct':100*z.above_p.mean()})
    pd.DataFrame(asum).to_csv(ROOT/'cross_aligned_summary.csv',index=False)
    print(r[(r.capital==5000000)&(r.extra_bps==0)&(r.timeout==30)&(r.period=='holdout')][['rule','marked_pnl','delta_pnl','new_trades','early_trades','closed_pf']].to_string(index=False),flush=True)

def main():
    global ROOT
    old='--old-source' in sys.argv
    if old:ROOT=ROOT/'source_sensitivity_old';ROOT.mkdir(exist_ok=True)
    st.INPUT_ROOT=INPUT;st.ROOT=ROOT;st.PREFER_CURRENT=not old;st.CONTRACTS=json.loads((INPUT/'contracts.json').read_text());st.Q.clear()
    minutes,expiries=st.load_minutes();tables=helper.augment_tables(minutes);archive=helper.Archive(minutes,tables,st.CONTRACTS,expiries);data=Data(archive)
    (ROOT/'selection_coverage.json').write_text(json.dumps(data.coverage,indent=2))
    for i,e in enumerate(data.events):
        early=event_trade(data,e,True);wait=event_trade(data,e,False)
        e.update(early_filled=early['filled'],early_closed=early.get('closed',False),early_return_pct=early['return_pct'],early_reason=early['reason'],wait_filled=wait['filled'],wait_return_pct=wait['return_pct'],wait_reason=wait['reason'])
        if early['filled']:e['actual_early_ts']=early['entry_ts'];e['actual_early_price']=early['entry_price']
        if i%10000==0:print('event paths',i,flush=True)
    books=run_books(data,['cross5','cross1']+RULES)
    for cost in [20,50]:books.update(run_books(data,['cross5','cross1','reclaim_p','preview_st','preview_st_p','preview_st_room'],cost))
    if not old:
        for timeout in [15,60]:books.update(run_books(data,['cross5','cross1','preview_st','preview_st_p','preview_st_room'],0,timeout))
    with gzip.open(ROOT/'books.json.gz','wt') as f:json.dump(books,f,separators=(',',':'),default=lambda x:x.item() if hasattr(x,'item') else str(x))
    summarize(data,data.events,books)
    (ROOT/'DATA_VALIDATION.json').write_text(json.dumps({'source_precedence':'old before migration' if old else 'current first','source_receipt_sha256':hashlib.sha256((INPUT/'smart_source_receipt.json').read_bytes()).hexdigest(),'series':len(minutes),'daily_selection_known_inputs_only':True,'rule_trials':len(data.events),'independent_scorer_fit_only_through':TRAIN_END,'no_smart_model_scores_or_labels_used':True,'quotes_require_positive_volume':True,'book_count':len(books),'all_cash_nonnegative':all(b['minimum_cash']>=-.01 for b in books.values()),'all_episode_equity_reconciliations_passed':True,'broker_order_calls':0,'indicator_definitions':['https://www.tradingview.com/support/solutions/43000634738-supertrend/','https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/']},indent=2))
    print('Pre-cross study complete:',ROOT,flush=True)

if __name__=='__main__':main()
