"""Reuse the shared recorded futures ticks; no broker/history request or socket.

Only observed close/OI/cumulative volume are written. OHLC is intentionally NULL
because a partial recorded tick window is not an official daily candle.
"""
from datetime import datetime,time
import math,os
import httpx,psycopg2
from psycopg2.extras import execute_values
from . import source


def rows_from_ticks(universe,ticks,now):
    contracts={r['canonical']:r for r in universe if r.get('exchange')=='NSE' and r.get('upstox') and r.get('expiry_ms')}
    rows=[]
    for symbol,at,price,oi,volume in ticks:
        r=contracts.get(symbol)
        if not r or at.tzinfo is None:continue
        local=at.astimezone(source.IST)
        if local.date()!=now.astimezone(source.IST).date() or not time(9,15)<=local.time()<=time(15,40) or at>now:continue
        if not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0 or not oi or oi<=0:continue
        expiry=datetime.fromtimestamp(r['expiry_ms']/1000,source.IST).date()
        if expiry<local.date():continue
        rows.append((local.date(),r['underlying'],expiry,r['upstox'],price,volume,oi,'hub_observed_futures_ticks',at))
    return rows


def sync(now=None):
    now=now or datetime.now(source.IST)
    if not source.is_session(now.date()):return {'written':0,'reason':'non_session'}
    # Cached local instrument master only; this endpoint does not fetch a broker.
    with httpx.Client(timeout=5,trust_env=False,follow_redirects=False) as h:
        r=h.get('http://host.docker.internal:8400/v1/universe');r.raise_for_status();universe=r.json()['front_futures']
    names=[r['canonical'] for r in universe if r.get('exchange')=='NSE' and r.get('upstox')]
    c=psycopg2.connect(host=os.environ.get('MI_PG_HOST','nomadcurie_db'),user=os.environ['MI_PG_USER'],password=os.environ['MI_PG_PASSWORD'],dbname=os.environ.get('MI_PG_DB','market_intelligence'),connect_timeout=5)
    c.set_session(readonly=True)
    try:
        with c.cursor() as q:
            q.execute("SET statement_timeout='15s'")
            q.execute('''SELECT DISTINCT ON(symbol) symbol,ts,ltp,oi,cum_volume FROM md_ticks
               WHERE symbol=ANY(%s) AND ts>=%s AND ts<=%s AND broker='upstox' AND oi>0 AND ltp>0
               ORDER BY symbol,ts DESC''',(names,datetime.combine(now.date(),time(9,15),source.IST),now))
            rows=rows_from_ticks(universe,q.fetchall(),now)
    finally:c.close()
    c=source.connect(readonly=False)
    try:
        with c,c.cursor() as q:
            if rows:execute_values(q,'''INSERT INTO stock_futures_daily(ts,symbol,expiry,instrument_key,close,volume,oi,source,observation_at)
                 VALUES %s ON CONFLICT(ts,symbol,expiry) DO UPDATE SET close=excluded.close,volume=excluded.volume,
                 oi=excluded.oi,observation_at=excluded.observation_at,synced_at=now()
                 WHERE stock_futures_daily.source='hub_observed_futures_ticks'
                 AND (stock_futures_daily.observation_at IS NULL OR excluded.observation_at>stock_futures_daily.observation_at)''',rows)
    finally:c.close()
    return {'written':len(rows),'basis':'observed tick levels; not official daily OHLC','broker_requests':0}


def supplement(connection,symbol,as_of):
    """Do not compare a new level with a compressed/gapped or rolled history."""
    with connection.cursor() as q:
        q.execute('''SELECT ts,expiry,close,oi,volume,observation_at,synced_at,source FROM stock_futures_daily
          WHERE symbol=%s AND ts<=%s AND synced_at<=%s ORDER BY ts DESC,expiry LIMIT 80''',(symbol,as_of.date(),as_of))
        rows=q.fetchall()
    if not rows:return {'futures.level_status':'missing'}
    current=rows[0];by_day={}
    for row in rows:by_day.setdefault(row[0],row)
    count=0;day=current[0]
    for _ in range(60):
        day=source.previous_session(day)
        if day not in by_day:break
        count+=1
    result={'futures.level_close':current[2],'futures.level_oi':current[3],'futures.level_volume':current[4],
            'futures.contiguous_prior_sessions':count,'futures.level_source':current[7],
            'futures.level_observation_at':current[5].isoformat() if current[5] else None,
            'futures.level_available_at':current[6].isoformat()}
    prior=by_day.get(source.previous_session(current[0]));rolled=bool(prior and prior[1]!=current[1])
    result['futures.previous_session_comparable']=bool(prior) and not rolled
    if not prior or rolled:
        for k in ('d_price_pct','d_oi_pct','d_oi_pct_z','oi_state','activity_surge'):result['futures.'+k]=None
    if count<20:
        for k in ('d_oi_pct_z','oi_z','volume_z','oi_pctile','activity_surge'):result['futures.'+k]=None
    if current[5]:
        result['futures.level_age_hours']=round((as_of-current[5]).total_seconds()/3600,2)
        result['futures.level_status']='current_session' if current[0]==as_of.date() else 'prior_session'
    else:result['futures.level_status']='historical_observation_time_unverified'
    return result
