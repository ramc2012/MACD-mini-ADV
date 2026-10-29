"""Bounded local daily market view and CBE inputs. No broker or ledger writes.

Historical indicators are recomputed from retained prices as observed NOW;
they are not reconstructed original decisions or proof of historical availability.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
import math
import threading
import time as clock

from psycopg2.extras import RealDictCursor
from psycopg2 import sql
from . import source
from .vendor.daily_stock_selection import signal, ema

from paper_lanes.daily_swing import signal as swing_signal, VERSION
# Thirteen aligned bars, including the exchange's final 15-minute closing bar.
# An incomplete 15:15 session slice is never treated as a completed daily candle.
DAILY_SQL = """
WITH chosen AS MATERIALIZED (
 SELECT DISTINCT ON (underlying,time) underlying,time,open,high,low,close,volume,synced_at
 FROM underlying_spot_candles
 WHERE interval='30minute' AND time>=%s AND synced_at<=%s
 AND time + CASE WHEN (time AT TIME ZONE 'Asia/Kolkata')::time=time '15:15'
   THEN interval '15 minutes' ELSE interval '30 minutes' END <= %s
 AND (time AT TIME ZONE 'Asia/Kolkata')::date<=%s
 AND (source IN ('upstox_spot','upstox_sweep','upstox','fyers','fyers_spot')
      OR source LIKE 'upstox_session_recovery_%%')
 AND (underlying='NIFTY' OR underlying IN
   (SELECT symbol FROM fo_underlying_catalog WHERE kind='STOCK' AND fno_active IS TRUE))
 AND (time AT TIME ZONE 'Asia/Kolkata')::time BETWEEN time '09:15' AND time '15:15'
 AND extract(minute from time AT TIME ZONE 'Asia/Kolkata') IN (15,45)
 AND close>0 AND open>0 AND low>0 AND high>=greatest(open,close) AND low<=least(open,close)
 ORDER BY underlying,time,CASE source WHEN 'upstox_spot' THEN 0 WHEN 'upstox_sweep' THEN 1
   WHEN 'upstox' THEN 2 WHEN 'fyers_spot' THEN 3 ELSE 4 END,synced_at DESC
)
SELECT underlying,(time AT TIME ZONE 'Asia/Kolkata')::date AS session,
 (array_agg(open ORDER BY time))[1]::float AS open,max(high)::float AS high,min(low)::float AS low,
 (array_agg(close ORDER BY time DESC))[1]::float AS close,sum(volume)::float AS volume,
 count(*) AS bars,max(synced_at) AS available_at
FROM chosen GROUP BY underlying,(time AT TIME ZONE 'Asia/Kolkata')::date
HAVING count(*)=13 ORDER BY underlying,session
"""


def indicators(values):
    if len(values)<35:return {}
    prices=[float(v) for v in values]
    def sequence(period):
        out=[prices[0]]
        for p in prices[1:]:out.append(out[-1]+2/(period+1)*(p-out[-1]))
        return out
    fast,slow=sequence(12),sequence(26)
    macd=[a-b for a,b in zip(fast,slow)]
    signal=ema(macd,9)
    changes=[b-a for a,b in zip(prices,prices[1:])]
    gain=sum(max(0,c) for c in changes[:14])/14
    loss=sum(max(0,-c) for c in changes[:14])/14
    for c in changes[14:]:gain=(gain*13+max(0,c))/14;loss=(loss*13+max(0,-c))/14
    rsi=100-100/(1+gain/loss) if loss else 100. if gain else 50.
    return dict(macd_line=macd[-1],macd_signal=signal,macd_hist=macd[-1]-signal,rsi_14=rsi,
                ema20=ema(prices,20),ema50=ema(prices,50))


def scan(connection,now,day=None):
    local=now.astimezone(source.IST)
    completed=local.date() if source.is_session(local.date()) and local.time()>=time(15,30) else source.previous_session(local.date())
    day=day or completed
    if day>completed:raise ValueError('A completed session is required')
    with connection.cursor(cursor_factory=RealDictCursor) as q:
        q.execute(DAILY_SQL,(datetime.combine(day-timedelta(days=700),time(0),source.IST),now,now,day))
        history=[dict(r) for r in q.fetchall() if source.is_session(r['session'])]
        q.execute('''SELECT symbol AS underlying,dt AS session,open::float,high::float,low::float,close::float,
          volume::float,synced_at AS available_at FROM bhavcopy_delivery WHERE dt>=%s AND dt<=%s AND synced_at<=%s
          AND close>0 AND open>0 AND low>0 AND high>=greatest(open,close) AND low<=least(open,close)''',
                  (day-timedelta(days=700),day,now))
        combined={(r['underlying'],r['session']):{**r,'price_basis':'complete_broker_session'} for r in history}
        for r in q.fetchall():
            if source.is_session(r['session']):combined[(r['underlying'],r['session'])]={**dict(r),'price_basis':'NSE_daily_report'}
        history=sorted(combined.values(),key=lambda r:(r['underlying'],r['session']))
        q.execute("SELECT u.symbol,s.sector20 FROM fo_underlying_catalog u LEFT JOIN sector_taxonomy s USING(symbol) WHERE kind='STOCK' AND fno_active IS TRUE ORDER BY symbol")
        universe=[dict(r) for r in q.fetchall()]
        q.execute('SELECT DISTINCT ON(underlying) * FROM features_mp WHERE dt<=%s AND computed_at<=%s ORDER BY underlying,dt DESC,computed_at DESC',(day,now))
        profiles={r['underlying']:dict(r) for r in q.fetchall()}
        from .decision_context import SPECS
        features=defaultdict(dict)
        for group,table,key,event,receipt,cadence,fields in SPECS:
            if key not in ('symbol','underlying') or group=='profile':continue
            query=sql.SQL('SELECT DISTINCT ON({key}) * FROM {table} WHERE {event}<=%s ORDER BY {key},{event} DESC').format(
                key=sql.Identifier(key),table=sql.Identifier(table),event=sql.Identifier(event))
            cutoff=datetime.combine(day,time(15,0),source.IST) if cadence=='bar' else datetime.combine(day,time(23,59,59),source.IST)
            # Date columns are explicitly session-grain. No invented intraday timestamp.
            q.execute(query,(cutoff if event=='ts' else day,))
            for r in q.fetchall():
                features[r[key]][group]={'source':str(r[event]),'available_at':str(r[receipt]) if receipt and r.get(receipt) else None,
                    **{f:float(r[f]) if type(r.get(f)).__name__=='Decimal' else r.get(f) for f in fields}}
    available=sorted({r['session'] for r in history if r['underlying']=='NIFTY'},reverse=True)
    # UI default chooses the latest actual completed benchmark, with visible source date.
    day=day if day in available else (available[0] if available else day)
    history=[r for r in history if r['session']<=day]
    series=defaultdict(list)
    for r in history:series[r['underlying']].append(r)
    benchmark={r['session']:r for r in series['NIFTY']}
    dates=sorted(benchmark)[-260:]
    recent=[];cursor_day=day
    while len(recent)<64:
        if source.is_session(cursor_day):recent.append(cursor_day)
        cursor_day-=timedelta(days=1)
    results=[]
    for item in universe:
        symbol=item['symbol'];bars=series[symbol];stock={r['session']:r for r in bars}
        aligned=[d for d in dates if d in stock]
        gaps=[d for d in dates if d not in stock]
        # Momentum uses exact benchmark sessions. Older missing seed observations
        # are disclosed; no price is filled or interpolated. The latest 64 closes must be complete; older seed gaps remain disclosed.
        ready=len(aligned)>=250 and all(d in stock and d in benchmark for d in recent) and dates[-1]==day
        row=swing_signal([stock[d] for d in aligned],[benchmark[d]['close'] for d in aligned]) if ready else {
            'eligible':False,'direction':'NEUTRAL','score':0,'reason':'Need 250 aligned completed daily sessions'}
        latest=bars[-1] if bars else {}
        profile=profiles.get(symbol,{})
        mp={k:(float(v) if type(v).__name__=='Decimal' else v)
            for k,v in profile.items() if k not in ('underlying','computed_at')}
        # A historical fallback stays visible but cannot qualify a fresh CBE plan.
        mp_current=profile.get('dt')==day
        metrics=indicators([stock[d]['close'] for d in aligned])
        eligible=bool(row.get('eligible') and row.get('direction')=='LONG' and metrics)
        reason=row.get('reason','Missing aligned complete daily history')
        if row.get('direction')=='SHORT':reason='Bearish trend; cash lane is long only'
        # MP is visible context, never an intraday admission or exit gate for this daily book.
        results.append({**metrics,**row,'instrument':symbol,'symbol':symbol,'sector':item['sector20'] or 'UNKNOWN',
            'latest_close':latest.get('close'),'price_session':str(latest['session']) if latest else None,
            'source_session':str(day),'source_mark':latest.get('close'),'price_basis':latest.get('price_basis'),
            'daily_history_complete':ready,'aligned_daily_observations':len(aligned),'older_seed_gaps':len(gaps),'horizon_sessions':None,'holding_mode':'daily_trend','strategy_version':VERSION,
            'source_mark_ts':datetime.combine(day,time(15,30),source.IST).isoformat(),
            'eligible':eligible,'reason':reason,'profile':mp,'mp_current':mp_current,'features':features[symbol],
            'history':[{k:(str(v) if k=='session' else v) for k,v in r.items() if k in ('session','open','high','low','close','volume')} for r in bars[-300:]]})
    results.sort(key=lambda r:(not r['eligible'],-float(r.get('score') or 0),r['symbol']))
    current=sum(r.get('price_session')==str(day) and r.get('daily_history_complete') for r in results)
    expected=len(universe)
    complete=expected>=10 and current>=.8*expected and day==completed
    return dict(version=VERSION,policy=VERSION,source_session=str(day),completed_session=str(completed),
        observed_at=now.isoformat(),sessions=[str(d) for d in available[:100]],expected=expected,current_complete=current,
        mp_complete=sum(r['mp_current'] for r in results),status='ok' if complete else 'blocked',
        blockers=[] if complete else ['250 aligned daily observations and 64 complete recent closes required across 80% of universe; no synthetic bars'],
        results=results,watchlist=[{**{k:v for k,v in r.items() if k!='history'},'rank':i+1}
            for i,r in enumerate(r for r in results if r['eligible'])][:10],
        benchmark_recent_gaps=[str(d) for d in recent if d not in benchmark],
        long_only=True,inputs='Completed daily EMA20/50/200, rising EMA50, 63-session relative momentum, 55-session breakout; 3 ATR20 daily trail, no time cap',
        provenance='Recomputed from locally retained broker bars and MP features. Historical receipt availability is unverified; these rows are not original decisions or fills.')


_cache={};_lock=threading.Lock()
def view(day=None,as_of=None):
    with _lock:
        now=as_of or datetime.now(source.IST);key=str(day)
        if key in _cache and clock.monotonic()-_cache[key][0]<60 and datetime.fromisoformat(_cache[key][1]['observed_at'])<=now:return _cache[key][1]
        c=source.connect(readonly=True)
        try:result=scan(c,now,day)
        finally:c.close()
        _cache.clear();_cache[key]=(clock.monotonic(),result)
        return result
