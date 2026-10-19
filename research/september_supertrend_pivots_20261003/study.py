"""Matched actual-entry September tests; source apps are never imported or written."""
from __future__ import annotations
import bisect
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from scipy.stats import t as student_t

INPUT_ROOT=Path(__file__).resolve().parent
PREFER_CURRENT='--prefer-current' in sys.argv
ROOT=INPUT_ROOT/'source_sensitivity_current' if PREFER_CURRENT else INPUT_ROOT
ROOT.mkdir(exist_ok=True)
WORK=INPUT_ROOT.parents[1]
sys.path.insert(0,str(WORK/'engine/src'))
from macd_trader.market_calendar import is_trading_day,previous_trading_day
from macd_trader.sessions import session_close

IST=ZoneInfo('Asia/Kolkata')
DAY=86400
Q=collections.Counter()
CONTRACTS=json.loads((INPUT_ROOT/'contracts.json').read_text())
RULES=['baseline','no_new_entries','premium_st','spot_st','both_st','premium_pivot','spot_pivot','pivot_room','both_st_pivot','both_st_room','exit_premium_st','exit_spot_st','exit_pivot_loss']
ENTRY_RULES=RULES[2:10]
EXIT_RULES=RULES[10:]


def epoch(day,clock='16:00'):
    return int(dt.datetime.fromisoformat(day+'T'+clock).replace(tzinfo=IST).timestamp())


def dayid(t):return (int(t)+19800)//DAY
def datestr(d):return (dt.date(1970,1,1)+dt.timedelta(days=int(d))).isoformat()
def stamp(t):return dt.datetime.fromisoformat(t).timestamp()


def load_gzip(path):
    with gzip.open(path,'rt') as f:return json.load(f)


def load_minutes():
    old={};meta={}
    with gzip.open(INPUT_ROOT/'old_minutes.jsonl.gz','rt') as f:
        for line in f:
            r=json.loads(line)
            if 'metadata' in r:meta['old']=r['metadata'];continue
            old[r['symbol']]=(np.asarray(r['bars'],dtype=float),r['expiries'])
    merged={};expiries={};cut=epoch('2026-09-29','00:00')
    with gzip.open(INPUT_ROOT/'current_minutes.jsonl.gz','rt') as f:
        for n,line in enumerate(f):
            r=json.loads(line)
            if 'metadata' in r:meta['current']=r['metadata'];continue
            symbol=r['symbol'];a=np.asarray(r['bars'],dtype=float)
            b,oe=old.pop(symbol,(np.empty((0,6)),[]))
            union=set(oe)|set(r['expiries'])
            if symbol in CONTRACTS:
                cat=CONTRACTS[symbol].get('catalog_expiry')
                if cat:union.add(cat)
            if len(union)>1:raise ValueError('Conflicting observed expiry '+symbol+':'+str(union))
            expiries[symbol]=next(iter(union),None)
            if len(b):
                shared,ib,ia=np.intersect1d(b[:,0],a[:,0],return_indices=True)
                Q['overlap_rows']+=len(shared)
                Q['overlap_ohlc_conflicts']+=int(np.any(np.abs(b[ib,1:5]-a[ia,1:5])>1e-6,axis=1).sum())
                # Old observed series controls pre-migration features; new archive fills gaps.
                z=np.vstack([a,b]) if PREFER_CURRENT else np.vstack([b[b[:,0]<cut],a,b[b[:,0]>=cut]])
                _,idx=np.unique(z[:,0],return_index=True);a=z[idx]
            valid=np.isfinite(a).all(axis=1)&(a[:,1]>0)&(a[:,4]>0)&(a[:,2]>=a[:,3])&(a[:,2]+1e-7>=np.maximum(a[:,1],a[:,4]))&(a[:,3]-1e-7<=np.minimum(a[:,1],a[:,4]))&(a[:,5]>=0)
            Q['invalid_ohlc_rows']+=int((~valid).sum());a=a[valid]
            merged[symbol]=a
            if n%150==0:print('merged series',n,flush=True)
    for symbol,(a,ex) in old.items():
        merged[symbol]=a;expiries[symbol]=ex[0] if len(ex)==1 else CONTRACTS.get(symbol,{}).get('catalog_expiry')
    for s in CONTRACTS:
        if not expiries.get(s):Q['unresolved_expiry']+=1
    quality={'counts':dict(Q),'metadata':meta,'merge_precedence':('Current archive first throughout, with old archive filling missing timestamps.' if PREFER_CURRENT else 'Old archive before 29 Sep; current archive thereafter; other source fills missing timestamps.'),'series':{s:{'rows':len(a),'first':int(a[0,0]),'last':int(a[-1,0]),'expiry':expiries.get(s)} for s,a in merged.items()}}
    (ROOT/'minute_quality.json').write_text(json.dumps(quality,indent=2))
    return merged,expiries


def bars30(symbol,a):
    t=a[:,0].astype(np.int64);days=(t+19800)//DAY
    times=(t+19800)%DAY
    ends={d:(session_close(symbol,datestr(d)).hour*3600+session_close(symbol,datestr(d)).minute*60) for d in np.unique(days)}
    trading_days=[d for d in ends if is_trading_day(dt.date.fromisoformat(datestr(d)))]
    mask=np.isin(days,trading_days)&(times>=33300)&np.array([m<ends[d] for d,m in zip(days,times)])
    b=a[mask];days=days[mask]
    start=days*DAY-19800+33300
    keys=start+((b[:,0]-start)//1800).astype(np.int64)*1800
    f=pd.DataFrame(b,columns=['t','o','h','l','c','v']);f['key']=keys;f['day']=days
    g=f.groupby('key',sort=True).agg(o=('o','first'),h=('h','max'),l=('l','min'),c=('c','last'),v=('v','sum'),n=('t','size'),day=('day','first')).reset_index().rename(columns={'key':'t'})
    g['end']=np.minimum(g.t+1800,np.array([d*DAY-19800+ends[d] for d in g.day]))
    return g


def st(g,period=10,mult=3):
    h=g.h.to_numpy();l=g.l.to_numpy();c=g.c.to_numpy()
    tr=np.maximum(h-l,np.maximum(abs(h-np.r_[c[0],c[:-1]]),abs(l-np.r_[c[0],c[:-1]])))
    atr=np.full(len(g),np.nan);direction=np.zeros(len(g),int);line=np.full(len(g),np.nan)
    if len(g)<period:return atr,direction,line
    atr[period-1]=tr[:period].mean()
    for k in range(period,len(g)):atr[k]=(atr[k-1]*(period-1)+tr[k])/period
    upper=(h+l)/2+mult*atr;lower=(h+l)/2-mult*atr
    for k in range(period-1,len(g)):
        if k==period-1:direction[k]=-1
        else:
            if not(upper[k]<upper[k-1] or c[k-1]>upper[k-1]):upper[k]=upper[k-1]
            if not(lower[k]>lower[k-1] or c[k-1]<lower[k-1]):lower[k]=lower[k-1]
            direction[k]=(1 if c[k]>upper[k] else -1) if direction[k-1]<0 else (-1 if c[k]<lower[k] else 1)
        line[k]=lower[k] if direction[k]>0 else upper[k]
    return atr,direction,line


def build_indicators(minutes):
    tables={}
    for n,(symbol,a) in enumerate(minutes.items()):
        g=bars30(symbol,a)
        if len(g)==0:continue
        atr,direction,line=st(g)
        pivots={}
        for day,rows in g.groupby('day'):
            end_clock=session_close(symbol,datestr(day));expected=(end_clock.hour*60+end_clock.minute)-555
            if rows.n.sum()>=.8*expected:
                H=rows.h.max();L=rows.l.min();C=rows.c.iloc[-1];P=(H+L+C)/3
                pivots[int(day)]=[P,2*P-L,P+H-L,2*P-H,P-H+L]
        tables[symbol]={'bars':g,'end':g.end.to_numpy(),'atr':atr,'direction':direction,'line':line,'pivots':pivots}
        if n%150==0:print('indicator series',n,flush=True)
    return tables


def previous_pivots(table,time):
    today=dt.datetime.fromtimestamp(time,IST).date()
    prev=previous_trading_day(today)
    return table['pivots'].get((prev-dt.date(1970,1,1)).days)


def available(table,time):
    if table is None:return None
    k=np.searchsorted(table['end'],time,side='right')-1
    if k<0 or not np.isfinite(table['atr'][k]):return None
    return k


def last_quote(a,time,day=None,allow_auction=True):
    k=np.searchsorted(a[:,0]+60,time,side='right')-1
    if k<0:return None
    if day is not None and dayid(a[k,0])!=day:return None
    return float(a[k,4]),int(a[k,0]+60)


def features(episode,minutes,tables):
    s=episode['symbol'];time=episode['entry_ts'];side=1 if CONTRACTS[s]['side']=='CE' else -1
    spot=CONTRACTS[s]['spot_symbol'];premium=tables.get(s);under=tables.get(spot)
    p=available(premium,time);u=available(under,time)
    pdaily=previous_pivots(premium,time) if premium else None
    sdaily=previous_pivots(under,time) if under else None
    spotquote=last_quote(minutes[spot],time,dayid(time)) if spot in minutes else None
    # A stale prior intraday quote cannot supply contemporary pivot headroom.
    if spotquote and time-spotquote[1]>300:spotquote=None
    premiumprice=episode['first_fill_price']/1.0005
    values={
        'premium_st':None if p is None else bool(premium['direction'][p]>0),
        'spot_st':None if u is None else bool(under['direction'][u]==side),
        'premium_pivot':None if pdaily is None else bool(premiumprice>pdaily[0]),
        'spot_pivot':None if sdaily is None or spotquote is None else bool((spotquote[0]-sdaily[0])*side>0),
        'pivot_room':None}
    room=None
    if sdaily is not None and spotquote is not None and u is not None and under['atr'][u]>0:
        obstacles=[(level-spotquote[0])*side for level in sdaily if (level-spotquote[0])*side>0]
        room=min(obstacles,default=math.inf)/under['atr'][u]
        values['pivot_room']=bool(room>=.5)
    def conjunction(*keys):
        v=[values[k] for k in keys]
        # A known rejection is a rejection even if another reading is missing.
        return False if False in v else None if None in v else True
    values['both_st']=conjunction('premium_st','spot_st')
    values['both_st_pivot']=conjunction('premium_st','spot_st','spot_pivot')
    values['both_st_room']=conjunction('premium_st','spot_st','pivot_room')
    details={'premium_st_bar_end':None if p is None else int(premium['end'][p]),'spot_st_bar_end':None if u is None else int(under['end'][u]),'spot_quote_end':None if spotquote is None else spotquote[1],'room_atr':None if room is None or not math.isfinite(room) else float(room)}
    return values,details


def episodes(source,book):
    active={};rows={};trade_episode={}
    trades=[]
    for raw in source['trades']:
        t={**raw,'ts':stamp(raw['timestamp'])};s=t['symbol'];qty=t['quantity']
        if t['side']=='BUY' and s not in active:
            id=f'{book}:{len(rows)}';active[s]=(id,0)
            rows[id]={'id':id,'symbol':s,'entry_ts':t['ts'],'entry_date':dt.datetime.fromtimestamp(t['ts'],IST).date().isoformat(),'first_fill_price':t['price'],'lot_size':t['lot_size'],'initial_quantity':qty,'fills':[],'exit_ts':None}
        if s not in active:raise ValueError('Unmatched sell '+s)
        id,old=active[s];new=old+(qty if t['side']=='BUY' else -qty)
        assert new>=0,(id,new)
        t['episode']=id;trades.append(t);trade_episode[t['trade_id']]=id
        rows[id]['fills'].append(t)
        if new==0:rows[id]['exit_ts']=t['ts'];del active[s]
        else:active[s]=(id,new)
    return trades,rows


class Book:
    def __init__(self,capital=5000000):
        self.cash=float(capital);self.realized=0.;self.positions={}

    def fill(self,t,quantity=None,price=None):
        qty=t['quantity'] if quantity is None else quantity
        price=t['price'] if price is None else price
        signed=qty if t['side']=='BUY' else -qty
        old,avg=self.positions.get(t['symbol'],(0,0.))
        assert old+signed>=0,('oversell',t)
        if signed<0:self.realized+=(price-avg)*qty
        elif old+signed:self.positions[t['symbol']]=(old+signed,(old*avg+qty*price)/(old+signed))
        if signed<0:
            if old+signed:self.positions[t['symbol']]=(old+signed,avg)
            else:self.positions.pop(t['symbol'])
        fees=t.get('fees',0.)*(qty/t['quantity']) if t['quantity'] else t.get('fees',0.)
        self.cash-=signed*price+fees;self.realized-=fees
        return signed*price+fees


def reconcile(source,trades):
    allbook=Book();cursor=0;bad=[];daily={};pointchecks=0
    points=source['equity_points']
    for p in points:
        time=stamp(p['timestamp'])
        while cursor<len(trades) and trades[cursor]['ts']<=time:
            allbook.fill(trades[cursor]);cursor+=1
        inferred_capital=p['cash']-allbook.cash+5000000
        realerr=allbook.realized-p['realized_pnl']
        # The capital increased before September; allow only the observed initial 1L/50L funding.
        okay=abs(realerr)<.01 and (abs(inferred_capital-5000000)<.01 or (time<epoch('2026-08-27','00:00') and abs(inferred_capital-1000000)<.01))
        if not okay:bad.append({'timestamp':p['timestamp'],'inferred_capital':inferred_capital,'realized_error':realerr})
        else:pointchecks+=1
        day=dt.datetime.fromtimestamp(time,IST).date().isoformat()
        if okay:daily[day]={**p,'open_positions_from_fills':len(allbook.positions),'cash_replay_error':p['cash']-allbook.cash,'realized_replay_error':realerr}
    oldmonth_start=daily.get('2026-08-31')
    last=daily[max(daily)]
    return {'points_checked':len(points),'points_accounting_consistent':pointchecks,'inconsistent_points':len(bad),'inconsistent_samples':bad[:4],'daily_last_consistent':daily,'last_recorded_realized':last['realized_pnl'],'september_realized_increase':None if oldmonth_start is None else last['realized_pnl']-oldmonth_start['realized_pnl']}


def mark_price(symbol,time,minutes,expiries):
    a=minutes.get(symbol);ex=expiries.get(symbol)
    day=dayid(time)
    if ex and datestr(day)>ex:
        spot=CONTRACTS[symbol]['spot_symbol'];u=minutes.get(spot)
        exday=(dt.date.fromisoformat(ex)-dt.date(1970,1,1)).days
        quote=last_quote(u,epoch(ex,'15:40'),exday) if u is not None else None
        if quote is None:return None,'missing-expiry-underlying'
        side=1 if CONTRACTS[symbol]['side']=='CE' else -1
        return max(0.,side*(quote[0]-CONTRACTS[symbol]['strike'])),'expiry-intrinsic-scenario'
    q=last_quote(a,time,day) if a is not None else None
    if q is None:
        # Known historical prices are carried transparently, as in a quote-driven paper book.
        # They are never described as a fresh/executable month-end quote.
        q=last_quote(a,time) if a is not None else None
        if q is None:return None,'missing-any-prior-option-mark'
        return q[0],'stale-option-close-'+str(day-dayid(q[1]))+'-days'
    return q[0],('observed-option-close' if time-q[1]<=3600 else 'older-same-day-option-close')


def marks(book,time,minutes,expiries):
    market=0.;quality=collections.Counter();missing=[]
    for s,(q,avg) in book.positions.items():
        price,kind=mark_price(s,time,minutes,expiries);quality[kind]+=1
        if price is None:missing.append(s)
        else:market+=q*price
    return {'time':time,'day':datestr(dayid(time)),'cash':book.cash,'realized':book.realized,'equity':None if missing else book.cash+market,'known_market_value':market,'open_positions':len(book.positions),'open_cost':sum(q*avg for q,avg in book.positions.values()),'unpriced_positions':missing,'mark_kinds':dict(quality)}


def overlay_exit(episode,rule,start,end,minutes,tables):
    s=episode['symbol'];table=tables.get(s)
    if table is None:return None
    g=table['bars'];lo=max(start,episode['entry_ts']);hi=min(end,episode['exit_ts'] or end)
    side=1 if CONTRACTS[s]['side']=='CE' else -1
    under=tables.get(CONTRACTS[s]['spot_symbol'])
    a=minutes.get(s);first=np.searchsorted(table['end'],lo,side='right')
    for k in range(first,len(g)):
        when=int(table['end'][k])
        if when>=hi:break
        trigger=False
        if rule=='exit_premium_st':trigger=table['direction'][k]<0
        elif rule=='exit_spot_st':
            u=available(under,when)
            trigger=u is not None and under['direction'][u]!=side
        elif rule=='exit_pivot_loss':
            p=previous_pivots(table,when);trigger=p is not None and float(g.c.iloc[k])<p[0]
        if not trigger:continue
        j=np.searchsorted(a[:,0],when,side='left')
        if j>=len(a):continue
        ts=int(a[j,0])
        if ts-when>300 or ts>=hi or dayid(ts)!=dayid(when) or a[j,5]<=0:
            Q['exit_trigger_no_nearby_traded_minute']+=1;continue
        close=session_close(s,datestr(dayid(ts)))
        if (ts+19800)%DAY>=close.hour*3600+close.minute*60:continue
        return {'ts':ts,'condition_ts':when,'price':round(float(a[j,1])*.9995,4),'episode':episode['id'],'symbol':s,'side':'SELL','quantity':0,'fees':0.,'trade_id':'overlay:'+episode['id'],'lot_size':episode['lot_size'],'rule':rule}
    return None


def simulate(trades,eps,rule,start,end,minutes,expiries,tables,extra_bps=0,funded=True):
    book=Book();kept={};flow=collections.defaultdict(float);status=collections.Counter();actual=collections.Counter();closed_early=set();applied=[]
    # Replay real carry-in funding and inventory without filtering or research charges.
    for t in trades:
        if t['ts']>=start:break
        book.fill(t);kept[t['episode']]=True
    startmark=marks(book,start-60,minutes,expiries)
    startreal=book.realized;startcash=book.cash
    # Weekend/holiday boundaries use the preceding trading session, not a stale month-end guess.
    prior=previous_trading_day(dt.datetime.fromtimestamp(start,IST).date())
    startmark=marks(book,epoch(prior.isoformat()),minutes,expiries)
    start_positions=book.positions.copy()
    for s,(q,avg) in start_positions.items():
        id=next((t['episode'] for t in reversed(trades) if t['symbol']==s and t['ts']<start),None)
        price,_=mark_price(s,epoch(prior.isoformat()),minutes,expiries)
        if price is not None:flow[id]-=q*price
    events=[(t['ts'],1,i,t) for i,t in enumerate(trades) if start<=t['ts']<=end]
    if rule in EXIT_RULES:
        for id,e in eps.items():
            if e['entry_ts']>end or (e['exit_ts'] and e['exit_ts']<=start):continue
            x=overlay_exit(e,rule,start,end,minutes,tables)
            if x:events.append((x['ts'],0,len(events),x))
    events.sort(key=lambda x:x[:3])
    days=[];d=dt.datetime.fromtimestamp(start,IST).date()
    while epoch(d.isoformat())<=end:
        if is_trading_day(d):days.append(d.isoformat())
        d+=dt.timedelta(days=1)
    marker_times=[epoch(d) for d in days];mi=0;curve=[];min_cash=book.cash
    for ts,kind,_,t in events:
        while mi<len(marker_times) and marker_times[mi]<ts:
            curve.append(marks(book,marker_times[mi],minutes,expiries));mi+=1
        id=t['episode'];e=eps[id];s=t['symbol']
        if id in closed_early:continue
        if kind==0:
            if id not in kept or not kept[id] or s not in book.positions:continue
            qty=book.positions[s][0]
            price=t['price']*(1-extra_bps/10000)
            book.fill(t,qty,price);flow[id]+=qty*price;closed_early.add(id);status['overlay_full_exits']+=1
            applied.append({'id':id,'ts':ts,'condition_ts':t['condition_ts'],'side':'SELL','quantity':qty,'price':price,'modelled':True})
            continue
        if t['side']=='BUY' and id not in kept:
            values=e['features'];unknown=rule in ENTRY_RULES and values[rule] is None
            accepted=rule=='baseline' or rule in EXIT_RULES or (rule in ENTRY_RULES and values[rule] is not False)
            if rule=='no_new_entries':accepted=False
            if unknown:status['unknown_reading_passed']+=1
            if rule in ENTRY_RULES and values[rule] is not None:status['classifiable_entries']+=1
            if not accepted:kept[id]=False;status['filtered_entries']+=1;continue
            price=t['price']*(1+extra_bps/10000)
            if funded and t['quantity']*price+t.get('fees',0.)>book.cash+1e-6:
                kept[id]=False;status['unaffordable_entries']+=1;continue
            kept[id]=True;status['new_episodes']+=1
        if not kept.get(id,False):continue
        qty=t['quantity'];price=t['price']*(1+(extra_bps/10000 if t['side']=='BUY' else -extra_bps/10000))
        if funded and t['side']=='BUY' and qty*price+t.get('fees',0.)>book.cash+1e-6:
            status['unaffordable_adds']+=1;continue
        if t['side']=='SELL':qty=min(qty,book.positions.get(s,(0,0))[0])
        if not qty:continue
        assert qty%t['lot_size']==0
        delta=book.fill(t,qty,price);flow[id]-=delta;actual['fills']+=1
        min_cash=min(min_cash,book.cash)
        if funded:assert book.cash>=-.01,(rule,ts,book.cash)
        applied.append({'id':id,'ts':ts,'side':t['side'],'quantity':qty,'price':price,'modelled':False,'trade_id':t['trade_id']})
    while mi<len(marker_times):curve.append(marks(book,marker_times[mi],minutes,expiries));mi+=1
    terminal=marks(book,end,minutes,expiries)
    for s,(qty,avg) in book.positions.items():
        id=next((a['id'] for a in reversed(applied) if eps[a['id']]['symbol']==s),None)
        if id is None:id=next((t['episode'] for t in reversed(trades) if t['symbol']==s and t['ts']<start),None)
        price,_=mark_price(s,end,minutes,expiries)
        if price is not None:flow[id]+=qty*price
    pnl=None if startmark['equity'] is None or terminal['equity'] is None else terminal['equity']-startmark['equity']
    if pnl is not None:assert abs(sum(flow.values())-pnl)<.02,(rule,pnl,sum(flow.values()))
    eq=[startmark['equity']]+[m['equity'] for m in curve]
    valid=all(x is not None for x in eq)
    dd=float(np.max(np.maximum.accumulate(eq)-eq)) if valid else None
    sidepnl={side:sum(v for id,v in flow.items() if id and CONTRACTS[eps[id]['symbol']]['side']==side) for side in ['CE','PE']}
    return {'rule':rule,'funded':funded,'start':datestr(dayid(start)),'end':datestr(dayid(end)),'extra_bps':extra_bps,'start_mark':startmark,'terminal':terminal,'pnl':pnl,'scenario_realized_pnl':book.realized-startreal,'start_cash':startcash,'min_cash':min_cash,'max_drawdown':dd,'status':dict(status),'fills':dict(actual),'marks':curve,'episode_pnl':dict(flow),'side_pnl':sidepnl,'applied_fills':applied}


def ttest(x):
    x=np.asarray(x,dtype=float)
    if len(x)<2:return 0.,1.
    se=x.std(ddof=1)/math.sqrt(len(x))
    if se<1e-10:return 0.,1.
    t=float(x.mean()/se);return t,float(2*student_t.sf(abs(t),len(x)-1))


def holm(values):
    values=np.array(values);order=np.argsort(values);out=np.zeros(len(values));high=0.
    for k,i in enumerate(order):high=max(high,values[i]*(len(values)-k));out[i]=min(1.,high)
    return out


def main():
    minutes,expiries=load_minutes();tables=build_indicators(minutes)
    books={};reconciliation={};coverage={};all_eps={};all_trades={}
    for name,path in [('old','old_book_source.json.gz'),('current','current_book_source.json.gz')]:
        source=load_gzip(INPUT_ROOT/path);trades,eps=episodes(source,name)
        for e in eps.values():e['features'],e['feature_visibility']=features(e,minutes,tables)
        all_eps.update(eps);all_trades[name]=trades
        reconciliation[name]=reconcile(source,trades)
        coverage[name]={'episodes':len(eps),'september_new_episodes':sum('2026-09-01'<=e['entry_date']<='2026-09-30' for e in eps.values()),'entry_days':sorted({e['entry_date'] for e in eps.values() if e['entry_date'].startswith('2026-09')}),'rules':{r:dict(collections.Counter('unknown' if e['features'][r] is None else 'pass' if e['features'][r] else 'reject' for e in eps.values() if e['entry_date'].startswith('2026-09'))) for r in ENTRY_RULES}}
        all_eps.update(eps)
        cohorts=[('old_full','2026-09-01','2026-09-30'),('old_active','2026-09-01','2026-09-18'),('old_train','2026-09-01','2026-09-11'),('old_holdout','2026-09-15','2026-09-18')] if name=='old' else [('current_sep','2026-09-29','2026-09-30')]
        for cohort,begin,finish in cohorts:
            for rule in RULES:
                result=simulate(trades,eps,rule,epoch(begin,'00:00'),epoch(finish),minutes,expiries,tables)
                books[cohort+'|'+rule]=result
            # A separate exact ledger reproduction retains the legacy overdraft.
            # It must not be silently relabelled a strict 50L funded book.
            for rule in RULES:
                books[cohort+'|'+rule+'|recorded']=simulate(trades,eps,rule,epoch(begin,'00:00'),epoch(finish),minutes,expiries,tables,funded=False)
            baseline=books[cohort+'|baseline|recorded']
            expected=[t for t in trades if epoch(begin,'00:00')<=t['ts']<=epoch(finish)]
            assert len(expected)==baseline['fills'].get('fills',0)
            assert [t['trade_id'] for t in expected]==[t['trade_id'] for t in baseline['applied_fills']]
            for cost in ([] if PREFER_CURRENT else [20,50]):
                for rule in ['baseline','both_st_pivot','both_st_room','exit_premium_st','exit_spot_st','exit_pivot_loss']:
                    books[cohort+'|'+rule+f'|cost{cost}']=simulate(trades,eps,rule,epoch(begin,'00:00'),epoch(finish),minutes,expiries,tables,cost)
            print('completed',cohort,'baseline P&L',baseline['pnl'],'realized',baseline['scenario_realized_pnl'],'missing',baseline['terminal']['unpriced_positions'],flush=True)
    rows=[]
    for key,result in books.items():
        cohort=key.split('|')[0]
        basekey=cohort+'|baseline'+('' if result['funded'] else '|recorded')
        if result['extra_bps']>0:basekey+='|cost'+str(result['extra_bps'])
        base=books[basekey]
        entry_days=sorted({e['entry_date'] for e in all_eps.values() if e['id'].startswith('old:' if cohort.startswith('old') else 'current:') and result['start']<=e['entry_date']<=result['end']})
        clusters=collections.defaultdict(float)
        for id in set(base['episode_pnl'])|set(result['episode_pnl']):
            entry=all_eps[id]['entry_date'];cluster=entry if entry>=result['start'] else 'carry-in'
            clusters[cluster]+=result['episode_pnl'].get(id,0)-base['episode_pnl'].get(id,0)
        # Carry-in is disclosed separately; it is not a pretend independent entry day.
        delta=[clusters[d] for d in entry_days];entry_t,p=ttest(delta) if result['pnl'] is not None else (None,1.)
        if any(m['equity'] is None for m in result['marks']+base['marks']):daily_t=daily_p=None
        else:
            differences=np.array([m['equity'] for m in result['marks']])-np.array([m['equity'] for m in base['marks']])
            daily_t,daily_p=ttest(np.diff(np.r_[0,differences]))
        pnl_delta=None if result['pnl'] is None or base['pnl'] is None else result['pnl']-base['pnl']
        rows.append({'cohort':cohort,'rule':result['rule'],'funded':result['funded'],'extra_bps':result['extra_bps'],'pnl':result['pnl'],'delta_pnl':pnl_delta,'realized_pnl':result['scenario_realized_pnl'],'delta_realized':result['scenario_realized_pnl']-base['scenario_realized_pnl'],'max_drawdown':result['max_drawdown'],'min_cash':result['min_cash'],'new_episodes':result['status'].get('new_episodes',0),'unaffordable_entries':result['status'].get('unaffordable_entries',0),'unaffordable_adds':result['status'].get('unaffordable_adds',0),'unknown_passed':result['status'].get('unknown_reading_passed',0),'CE_pnl':result['side_pnl']['CE'],'PE_pnl':result['side_pnl']['PE'],'entry_day_clusters':len(entry_days),'entry_day_t':entry_t,'entry_day_p':p,'carry_in_delta':clusters.get('carry-in',0),'daily_equity_t':daily_t,'daily_equity_p':daily_p,'unpriced_terminal':len(result['terminal']['unpriced_positions'])})
    frame=pd.DataFrame(rows);frame['entry_day_p_holm']=np.nan;frame['daily_equity_p_holm']=np.nan
    selected=(frame.cohort.isin(['old_holdout','current_sep']))&(frame.funded)&(frame.extra_bps==0)&(frame.rule!='baseline')
    frame.loc[selected,'entry_day_p_holm']=holm(frame.loc[selected,'entry_day_p'])
    if frame.loc[selected,'daily_equity_p'].notna().all():frame.loc[selected,'daily_equity_p_holm']=holm(frame.loc[selected,'daily_equity_p'])
    frame.to_csv(ROOT/'results.csv',index=False)
    with gzip.open(ROOT/'books.json.gz','wt') as f:json.dump(books,f,separators=(',',':'))
    with gzip.open(ROOT/'episodes.json.gz','wt') as f:json.dump(all_eps,f,separators=(',',':'))
    (ROOT/'ledger_reconciliation.json').write_text(json.dumps(reconciliation,indent=2))
    (ROOT/'feature_coverage.json').write_text(json.dumps(coverage,indent=2))
    (ROOT/'run_quality.json').write_text(json.dumps(dict(Q),indent=2))
    print(frame[(frame.extra_bps==0)&frame.funded&frame.rule.isin(['baseline','premium_st','spot_st','both_st','spot_pivot','both_st_pivot','both_st_room','exit_premium_st','exit_spot_st','exit_pivot_loss'])][['cohort','rule','pnl','delta_pnl','realized_pnl','entry_day_clusters','entry_day_p_holm','unpriced_terminal']].to_string(index=False))


if __name__=='__main__':main()
