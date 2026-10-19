"""Retrospective Smart policy, production execution in isolated research books.

Minute closes are sampled quotes, not an observed tick/spread replay. The static
ranker was trained through Oct 1; these results are not out-of-sample evidence.
"""
from __future__ import annotations
import asyncio
import collections
import datetime as dt
import gzip
import hashlib
import inspect
import json
import math
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
INPUT=ROOT.parent
WORK=INPUT.parents[1]
sys.path[:0]=[str(INPUT),str(WORK/'engine/src')]
import study as st
from macd_trader import smart_lane as sm,execution,models,repository
from macd_trader.config import Settings
from macd_trader.contracts import select_liquid_contract
from macd_trader.brokers import OptionChainEntry
from macd_trader.dispersion import DispersionPoint
from macd_trader.entry_scorer import build_features
from macd_trader.events import EventHub
from macd_trader.models import Signal

IST=ZoneInfo('Asia/Kolkata')
CLOCK=st.epoch('2026-09-01','09:16')

class ReplayDatetimeMeta(type):
    def __instancecheck__(cls,value):return isinstance(value,dt.datetime)

class ReplayDatetime(dt.datetime,metaclass=ReplayDatetimeMeta):
    @classmethod
    def now(cls,tz=None):
        value=dt.datetime.fromtimestamp(CLOCK,dt.UTC)
        return value.astimezone(tz) if tz else value.astimezone().replace(tzinfo=None)

class NoOrders:
    name='research-no-orders'
    async def place_order(self,*args,**kwargs):raise AssertionError('No broker order API allowed')

def install_clock():
    for module in [sm,execution,models,repository]:module.datetime=ReplayDatetime

def real_time(time):return dt.datetime.fromtimestamp(time,dt.UTC)

def augment_tables(minutes):
    tables=st.build_indicators(minutes)
    for table in tables.values():
        g=table['bars'];c=pd.Series(g.c.to_numpy())
        table['fast']=c.ewm(span=12,adjust=False).mean().to_numpy();table['slow']=c.ewm(span=26,adjust=False).mean().to_numpy()
        table['macd']=table['fast']-table['slow'];table['signal']=pd.Series(table['macd']).ewm(span=9,adjust=False).mean().to_numpy()
    return tables

class Archive:
    def __init__(self,minutes,tables,contracts,expiries):
        self.minutes=minutes;self.tables=tables;self.contracts=contracts;self.expiries=expiries;self.stats={};self.daily={}
        for symbol,a in minutes.items():
            days=(a[:,0].astype(np.int64)+19800)//86400;times=(a[:,0]+19800)%86400
            # The output of st.load_minutes is price-valid; exclude auction/off-session observations.
            wanted=(times>=33300)&(times<55800)
            a=a[wanted];self.minutes[symbol]=a
            stats={};daily={}
            for d in np.unique(days[wanted]):
                indices=np.flatnonzero((a[:,0].astype(np.int64)+19800)//86400==d)
                if not len(indices):continue
                b=a[indices];v=np.maximum(b[:,5],0);cv=np.cumsum(v)
                stats[int(d)]=(int(indices[0]),int(indices[-1]),np.maximum.accumulate(b[:,2]),np.minimum.accumulate(b[:,3]),cv,np.cumsum(b[:,4]*v))
                daily[int(d)]={'close':float(b[-1,4]),'volume':float(cv[-1]),'open':float(b[0,1]),'last_end':int(b[-1,0]+60)}
            self.stats[symbol]=stats;self.daily[symbol]=daily

    def index(self,symbol,time):
        a=self.minutes.get(symbol)
        if a is None or not len(a):return None
        k=np.searchsorted(a[:,0]+60,time,side='right')-1
        return int(k) if k>=0 else None

    def quote(self,symbol,time,maximum_age=180):
        if symbol in self.contracts and self.expiries.get(symbol,'9999-12-31')<st.datestr(st.dayid(time)):return None
        k=self.index(symbol,time)
        if k is None:return None
        a=self.minutes[symbol];end=int(a[k,0]+60)
        if st.dayid(end-1)!=st.dayid(time) or time-end>maximum_age:return None
        if symbol in self.contracts and a[k,5]<=0:return None
        return float(a[k,4]),end

    def prior_day(self,time,n=1):
        day=dt.datetime.fromtimestamp(time,IST).date()
        for _ in range(n):day=st.previous_trading_day(day)
        return (day-dt.date(1970,1,1)).days

    def previous(self,symbol,time,n=1):return self.daily.get(symbol,{}).get(self.prior_day(time,n))

    def state(self,symbol,time):
        table=self.tables.get(symbol)
        if table is None:return None
        k=np.searchsorted(table['end'],time,side='right')-1
        return (table,int(k)) if k>=0 else None

    def confirm(self,symbol,bucket,price,time):
        state=self.state(symbol,time)
        if state is None:return None
        table,k=state
        if bucket is not None and table['bars'].t.iloc[k]>=bucket:return bool(table['macd'][k]>0)
        f=table['fast'][k]+(price-table['fast'][k])*2/13
        s=table['slow'][k]+(price-table['slow'][k])*2/27
        return bool(f>s)

    def features(self,row,crowd):
        symbol=row['symbol'];time=row['ts'];contract=self.contracts[symbol];side=1 if contract['side']=='CE' else -1
        idx=self.index(symbol,time);a=self.minutes[symbol];d=st.dayid(time);lo,hi,highs,lows,volumes,pvs=self.stats[symbol][d];j=idx-lo
        spot=contract['spot_symbol'];sq=self.quote(spot,time,300);nq=self.quote('NSE:NIFTY50-INDEX',time,300)
        prev=self.previous(symbol,time);sprev=self.previous(spot,time);sprevious5=self.previous(spot,time,5);nprev=self.previous('NSE:NIFTY50-INDEX',time)
        sstats=self.stats.get(spot,{}).get(d);sidx=self.index(spot,time)
        sop=self.daily.get(spot,{}).get(d,{}).get('open');svwap=None
        if sstats and sidx is not None and sidx>=sstats[0]:
            sj=sidx-sstats[0];sv=sstats[4][sj];svwap=sstats[5][sj]/sv if sv>0 else None
        table=self.tables[symbol];k=np.searchsorted(table['bars'].t.to_numpy(),row['bucket'],side='left');history=table['macd'][max(0,k-500):k].tolist()
        same,other=crowd
        return build_features(price=row['price'],side=side,strike=contract['strike'],expiry=self.expiries[symbol],now=real_time(time),is_index=spot.endswith('-INDEX'),
            spot=sq[0] if sq else None,spot_prev_close=sprev['close'] if sprev else None,spot_open=sop,spot_close_5d=sprevious5['close'] if sprevious5 else None,spot_vwap=svwap,
            nifty=nq[0] if nq else None,nifty_prev_close=nprev['close'] if nprev else None,day_open=float(a[lo,1]),day_high=float(highs[j]),day_low=float(lows[j]),
            option_prev_close=prev['close'] if prev else None,volume_today=float(volumes[j]),prior_day_volume=prev['volume'] if prev else 0.,macd_history=history,macd_preview=row['macd'],crowd_same=same,crowd_other=other)

    def breadth(self,selected,time,blast=False):
        rows=[]
        for symbol in selected:
            state=self.state(symbol,time)
            if state is None:continue
            table,k=state;rows.append((symbol,table['bars'].t.iloc[k],table['macd'][k]))
        if not rows:return None,{}
        newest=max(x[1] for x in rows);fresh=[x for x in rows if x[1]==newest];out={}
        for side in ['CE','PE']:
            use=[x for x in rows if x[0].endswith(side) and x[1]>=newest-2100]
            out[side]=(sum(x[2]>0 for x in use)/len(use)) if len(use)>=20 else None
        ce=[x for x in fresh if x[0].endswith('CE')];pe=[x for x in fresh if x[0].endswith('PE')]
        return DispersionPoint(int(newest),int(sum(x[2]>0 for x in ce)),int(sum(x[2]>0 for x in pe)),len(ce),len(pe),len(selected)),out

    def select(self,day):
        time=st.epoch(day,'09:16');groups=collections.defaultdict(list);selected=[];contexts={}
        for symbol,c in self.contracts.items():
            if symbol not in self.minutes or self.expiries.get(symbol,'')<=day:continue
            prior=self.previous(symbol,time);quote=self.quote(symbol,time,120)
            volume=prior['volume'] if prior else 0.
            if volume<=0 and quote:
                k=self.index(symbol,time);volume=float(self.minutes[symbol][k,5])
            if volume<=0:continue
            groups[(c['spot_symbol'],c['side'])].append((symbol,c,volume))
        for (spot,side),items in sorted(groups.items()):
            sq=self.quote(spot,time,120)
            if sq is None:continue
            nearest=min(self.expiries[s] for s,c,v in items);items=[x for x in items if self.expiries[x[0]]==nearest]
            entries=[OptionChainEntry(symbol=s,strike=c['strike'],option_type=side,volume=int(v),oi=0) for s,c,v in items]
            picked=select_liquid_contract(entries,sq[0],side)
            if picked is None:continue
            s=picked.symbol;c=self.contracts[s];selected.append(s)
            contexts[s]={'spot_symbol':spot,'option_type':side,'strike':c['strike'],'expiry':nearest,'volume':float(picked.volume),'oi':0}
        return selected,contexts

    def candidates(self):
        # Build prospective cross observations; no outcome or future volume enters.
        events=collections.defaultdict(list);closed=collections.defaultdict(list)
        begin=st.epoch('2026-09-01','00:00');end=st.epoch('2026-09-30','16:00')
        for n,(symbol,c) in enumerate(self.contracts.items()):
            if symbol not in self.tables or symbol not in self.minutes:continue
            a=self.minutes[symbol];table=self.tables[symbol];g=table['bars'];starts=g.t.to_numpy();j=np.searchsorted(starts,a[:,0],side='right')-1
            before=np.maximum(j-1,0);preview=table['fast'][before]+(a[:,4]-table['fast'][before])*2/13-table['slow'][before]-(a[:,4]-table['slow'][before])*2/27
            mask=(j>26)&(table['macd'][before]<=0)&(preview>0)&(a[:,5]>0)&(a[:,0]+60>=begin)&(a[:,0]+60<=end)
            ix=np.flatnonzero(mask);seen=set()
            for i in ix:
                bucket=int(starts[j[i]])
                if bucket in seen:continue
                seen.add(bucket);time=int(a[i,0]+60)
                events[time].append({'ts':time,'symbol':symbol,'source':'macd','bucket':bucket,'price':float(a[i,4]),'macd':float(preview[i])})
            for k in np.flatnonzero((table['end']>=begin)&(table['end']<=end)):
                time=int(table['end'][k]);closed[time].append((symbol,float(table['macd'][k])))
                if k<1 or not(table['macd'][k-1]<=table['signal'][k-1] and table['macd'][k]>table['signal'][k]):continue
                # Blast's longer bar folds at the first completed minute of the next bucket.
                at=time+60
                if st.dayid(at)!=st.dayid(time-1) or (at+19800)%86400>55800:continue
                prevraw=np.flatnonzero(a[:,0]<g.t.iloc[k])[-max(0,750-30):]
                if len(prevraw)<120:continue
                high=float(a[prevraw,2].max());premium=float(g.c.iloc[k])
                if premium/high>0.75:continue
                events[at].append({'ts':at,'symbol':symbol,'source':'blast','bucket':int(g.t.iloc[k]),'price':premium,'macd':float(table['macd'][k]),'signal':float(table['signal'][k]),'off_high_pct':100*(premium/high-1)})
            if n%400==0:print('candidate series',n,flush=True)
        return events,closed

class OverlayLane(sm.SmartLane):
    def __init__(self,*args,rule,archive,**kwargs):
        self.rule=rule;self.archive=archive;self.overlay_counts=collections.Counter();super().__init__(*args,**kwargs)

    async def _decide(self,item,now,cut):
        if self.rule!='baseline' and item.symbol not in self.portfolio.positions and item.symbol in self.execution.tradable_symbols:
            try:price=self.execution._fresh_price(item.symbol)
            except RuntimeError:return await super()._decide(item,now,cut)
            viable=(item.source=='blast' or (self._macd_confirm(item.symbol,item.bucket,price) is True and item.score is not None and item.score>=cut))
            if viable:
                e={'symbol':item.symbol,'entry_ts':now.timestamp(),'first_fill_price':price*1.0005}
                values,details=st.features(e,self.archive.minutes,self.archive.tables)
                self.overlay_counts['evaluated']+=1
                if values[self.rule] is None:self.overlay_counts['unknown_passed']+=1
                if values[self.rule] is False:
                    self.overlay_counts['rejected']+=1
                    return self._record(item,'OVERLAY_REJECTED',now,price=price,cut=cut,detail={'overlay':self.rule,'visibility':details})
        return await super()._decide(item,now,cut)

def value_residuals(lane,archive,time):
    """Causal valuation only; never create a quote, exit, or release tied-up cash."""
    kinds=collections.Counter();missing=[];expired=[];value=0.
    for symbol,p in lane.portfolio.positions.items():
        price,kind=st.mark_price(symbol,time,archive.minutes,archive.expiries)
        kinds[kind]+=1
        if price is None:missing.append(symbol)
        else:p.last_price=price
        if kind=='expiry-intrinsic-scenario':expired.append(symbol);value+=p.quantity*p.last_price
    return {'mark_kinds':dict(kinds),'unpriced_positions':missing,'expired_residuals':expired,'expired_residual_value':value}

async def run(archive,events,closed,settings,rules,cost,temporary):
    global CLOCK
    CLOCK=st.epoch('2026-09-01','09:16');install_clock();states={};hub=EventHub();selected=[];context={};point=None
    for capital in [100000,5000000]:
        for rule in rules:
            name=f'smart_{capital}_{rule}_cost{cost}';s=settings.model_copy(update={'slippage_bps':settings.slippage_bps+cost})
            lane=OverlayLane(name,capital,s,NoOrders(),hub,str(Path(temporary)/(name+'.sqlite3')),rule=rule,archive=archive)
            lane.set_resolvers(macd_confirm=lambda sym,bucket,p:archive.confirm(sym,bucket,p,CLOCK),dispersion=lambda:point)
            states[name]={'lane':lane,'capital':capital,'rule':rule,'extra_bps':cost,'marks':[],'minimum_cash':capital,'max_sampled_drawdown':0.,'peak_equity':capital}
    days=[];d=dt.date(2026,9,1)
    while d<=dt.date(2026,9,30):
        if st.is_trading_day(d):days.append(d.isoformat())
        d+=dt.timedelta(days=1)
    selections=[];counts=collections.Counter();crowd=collections.Counter()
    for day in days:
        CLOCK=st.epoch(day,'09:16');selected,context=archive.select(day);selected_set=set(selected);selections.append({'day':day,'contracts':len(selected),'roots':len({archive.contracts[s]['root'] for s in selected})})
        lot_sizes={s:archive.contracts[s]['lot_size'] for s in selected}
        for state in states.values():
            lane=state['lane'];lane.set_tradable_contracts(lot_sizes);lane.set_contract_context(context);lane.execution.closed_positions=[]
        for at in range(st.epoch(day,'09:16'),st.epoch(day,'15:30')+1,60):
            CLOCK=at
            for state in states.values():
                lane=state['lane']
                for sym in list(lane.portfolio.positions):
                    quote=archive.quote(sym,at,0)
                    if quote:await lane.execution.on_tick(sym,quote[0],real_time(quote[1]))
                for sym,macd in closed.get(at,[]):
                    if sym in lane.portfolio.positions and archive.expiries.get(sym,'9999-12-31')>=day:await lane.execution.on_closed_bar(sym,macd)
                # Valid OHLC can value inventory even when volume is zero. It
                # does not pass the execution quote-age/volume checks. Expired
                # residuals use the disclosed intrinsic scenario, with no fill.
                valued=value_residuals(lane,archive,at)
                assert not valued['unpriced_positions'],valued
            candidates=[];point,blast_breadth=archive.breadth(selected,at)
            for row in sorted(events.get(at,[]),key=lambda x:(x['source'],x['symbol'])):
                sym=row['symbol']
                if sym not in selected_set:continue
                if row['source']=='macd':
                    side=archive.contracts[sym]['side'];other='PE' if side=='CE' else 'CE';crowd[(row['bucket'],side)]+=1
                    row['features']=archive.features(row,(crowd[(row['bucket'],side)],crowd[(row['bucket'],other)]));candidates.append(row);counts['macd_candidates']+=1
                else:
                    side=archive.contracts[sym]['side'];sq=archive.quote(archive.contracts[sym]['spot_symbol'],at,300);breadth=blast_breadth.get(side)
                    if sq is None or breadth is None or breadth<settings.blast_min_breadth or row['price']/sq[0]*100>settings.blast_max_premium_pct:continue
                    if int(context[sym]['volume']*settings.blast_max_volume_share_pct/100//archive.contracts[sym]['lot_size'])<1:continue
                    row['premium_pct']=row['price']/sq[0]*100;row['breadth']=breadth;candidates.append(row);counts['blast_candidates']+=1
            for state in states.values():
                lane=state['lane']
                for row in candidates:
                    sym=row['symbol'];side=archive.contracts[sym]['side']
                    if row['source']=='macd':
                        sig=Signal(sym,'BUY','RECONSTRUCTED_MACD_INTRABAR',row['price'],row['macd'],0.,row['macd'],timestamp=real_time(at),evaluated_candle_timestamp=row['bucket'])
                        lane.on_macd_signal(sig,side,row['features'],now=real_time(at))
                    elif settings.smart_blast_source:
                        lane.on_blast_candidate({'symbol':sym,'option_type':side,'premium':row['price'],'macd':row['macd'],'signal':row['signal'],'histogram':row['macd']-row['signal'],'premium_pct':row['premium_pct'],'breadth':row['breadth'],'off_high_pct':row['off_high_pct'],'bar_timestamp':row['bucket']},now=real_time(at))
                for item in lane._pending:
                    quote=archive.quote(item.symbol,at,180)
                    if quote:lane.execution.set_quote(item.symbol,quote[0],real_time(quote[1]))
            # Keep the real quote-age gate. Fifteen-second ranking waits are
            # quantized to the next fresh sampled minute, not a stale cached fill.
            CLOCK=at
            for state in states.values():
                lane=state['lane'];await lane.step(real_time(CLOCK));lane.execution.orders={k:o for k,o in lane.execution.orders.items() if o.status=='OPEN'}
                equity=lane.portfolio.cash+sum(p.quantity*p.last_price for p in lane.portfolio.positions.values())
                state['minimum_cash']=min(state['minimum_cash'],lane.portfolio.cash);assert lane.portfolio.cash>=-.01
                state['peak_equity']=max(state['peak_equity'],equity);state['max_sampled_drawdown']=max(state['max_sampled_drawdown'],state['peak_equity']-equity)
                if lane.error:raise AssertionError(lane.error)
                assert all(p.quantity>=0 and p.quantity%p.lot_size==0 for p in lane.portfolio.positions.values())
        for state in states.values():
            lane=state['lane'];lane.flush_journal();equity=lane.portfolio.cash+sum(p.quantity*p.last_price for p in lane.portfolio.positions.values())
            stale=[]
            for sym,p in lane.portfolio.positions.items():
                quote=archive.quote(sym,st.epoch(day,'15:30'),86400)
                if quote is None:stale.append(sym)
            valued=value_residuals(lane,archive,st.epoch(day,'15:30'))
            state['marks'].append({'day':day,'equity':equity,'cash':lane.portfolio.cash,'realized_pnl':lane.portfolio.realized_pnl,'open_positions':len(lane.portfolio.positions),'missing_same_day_marks':stale,**valued})
        print('Smart',cost,day,'selected',len(selected),'MACD/Blast',counts['macd_candidates'],counts['blast_candidates'],'baseline50L equity',round(states[f'smart_5000000_baseline_cost{cost}']['marks'][-1]['equity'],2),flush=True)
    output={}
    for name,state in states.items():
        lane=state.pop('lane');trades=sorted(lane.repository.rows('trades',1000000),key=lambda x:x['timestamp']);journal=lane.journal_rows(limit=1000000)
        if state['capital']==5000000 and state['rule']=='baseline':assert trades,'No baseline fills: inspect quote/clock adapter before reporting.'
        state.update({'trades':trades,'journal':journal,'counts':lane.counts,'overlay_counts':dict(lane.overlay_counts),'terminal':lane.portfolio.snapshot(),'source_by_order':{j['order_id']:j['source'] for j in journal if j.get('order_id')},'selection':selections,'candidate_counts':dict(counts)})
        output[name]=state
        lane._db.close();lane.repository._connection.close()
    return output

def main():
    source=json.loads((ROOT/'smart_runtime_audit.json').read_text());safe={k:v for k,v in source['resolved_settings'].items() if not k.endswith('_path')}
    settings=Settings(_env_file=None,**safe,allow_live_orders=False,auto_trade=True,feed_mode='simulation')
    assert hashlib.sha256(inspect.getsource(sm).encode()).hexdigest()==source['smart_source_sha256']
    assert hashlib.sha256(json.dumps(sm.MODEL,sort_keys=True,separators=(',',':')).encode()).hexdigest()==source['smart_model_sha256']
    st.INPUT_ROOT=ROOT;st.ROOT=ROOT;st.PREFER_CURRENT=True;st.CONTRACTS=json.loads((ROOT/'contracts.json').read_text());st.Q.clear()
    minutes,expiries=st.load_minutes();tables=augment_tables(minutes);archive=Archive(minutes,tables,st.CONTRACTS,expiries);events,closed=archive.candidates()
    (ROOT/'smart_candidate_coverage.json').write_text(json.dumps({'raw_macd':sum(row['source']=='macd' for items in events.values() for row in items),'raw_screenable_blast':sum(row['source']=='blast' for items in events.values() for row in items),'series':len(minutes),'contracts':len(st.CONTRACTS),'model_trained_through':'2026-10-01','out_of_sample':False},indent=2))
    with tempfile.TemporaryDirectory(prefix='smart-research-',dir=ROOT) as temporary:
        output=asyncio.run(run(archive,events,closed,settings,['baseline']+st.ENTRY_RULES,0,temporary))
        with gzip.open(ROOT/'smart_books.json.gz','wt') as f:json.dump(output,f,separators=(',',':'),default=lambda x:x.item() if hasattr(x,'item') else str(x))
        for cost in [20,50]:
            later=asyncio.run(run(archive,events,closed,settings,['baseline','premium_st','pivot_room','both_st_room'],cost,temporary))
            with gzip.open(ROOT/f'smart_books_cost{cost}.json.gz','wt') as f:json.dump(later,f,separators=(',',':'),default=lambda x:x.item() if hasattr(x,'item') else str(x))
    print('Smart retrospective production-policy replay complete.',flush=True)

if __name__=='__main__':main()
