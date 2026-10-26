"""Container-only read-only candle reconstruction, never a trading/history writer.

stdin contains the frozen feature requests. stdout is redirected to a private file.
These labels are research proxies with no historical availability or L1 fill proof.
"""
from collections import Counter
from datetime import datetime,time,timedelta
import json
import math
import sys

from psycopg2.extras import RealDictCursor
from shadow_lanes import source


def label(row,cur):
    day=datetime.fromisoformat(row['decision_at']).astimezone(source.IST).date()
    entry_day=source.next_session(day);exit_day=source.next_session(day,row['horizon'])
    if row['lane']=='vanguard' and row['expiry']<str(exit_day):return None,'expires_before_exit'
    start=datetime.combine(entry_day,time(9,21),source.IST)
    due=datetime.combine(exit_day,time(15,10) if row.get('expiry')==str(exit_day) else time(15,15),source.IST)
    if row['lane']=='vanguard':
        cur.execute("""SELECT DISTINCT ON (time) time,open,high,low,close,synced_at FROM option_premium_candles
          WHERE instrument_key=%s AND underlying=%s AND expiry=%s AND strike=%s AND option_type=%s
          AND interval='3minute' AND time>=%s AND time<=%s
          ORDER BY time,synced_at DESC""",(row['instrument'],row['symbol'],row['expiry'],row['strike'],row['side'],start,due+timedelta(minutes=3)))
    else:
        cur.execute("""SELECT DISTINCT ON (time) time,open,high,low,close,synced_at FROM underlying_spot_candles
          WHERE underlying=%s AND interval='3minute' AND time>=%s AND time<=%s
          AND source IN ('upstox_spot','upstox_sweep','upstox')
          ORDER BY time,synced_at DESC""",(row['symbol'],start,due+timedelta(minutes=3)))
    bars=[dict(r) for r in cur.fetchall() if time(9,15)<=r['time'].astimezone(source.IST).time()<time(15,30)]
    if not bars or bars[0]['time']>start+timedelta(minutes=6):return None,'missing_entry_bar'
    vg=row['lane']=='vanguard';slip=.0015 if vg else .0005;spread=.005 if vg else .001
    entry=float(bars[0]['open'])*(1+slip+spread);stop=entry*(.85 if vg else .95);target=entry*(1.30 if vg else 1.10)
    if entry<=0:return None,'invalid_entry'
    prior=None;exit=None;reason=None;ambiguous=False;end=None;observed=[]
    for bar in bars:
        at=bar['time'].astimezone(source.IST)
        if prior and prior.date()==at.date() and (at-prior).total_seconds()>360:return None,'intraday_bar_gap'
        if prior and prior.date()!=at.date():
            if at.date()!=source.next_session(prior.date()) or prior.time()<time(15,24) or at.time()>time(9,21):
                return None,'session_edge_or_whole_session_gap'
        o,h,l,c=(float(bar[k]) for k in ('open','high','low','close'))
        if not all(math.isfinite(x) for x in (o,h,l,c)) or min(o,h,l,c)<=0 or h<max(o,c) or l>min(o,c):return None,'invalid_ohlc'
        observed.append(bar['synced_at'])
        if at>=due:
            if at>due+timedelta(minutes=3):break
            exit=o;end=at;reason='time';break
        if l<=stop:
            exit=min(o,stop);end=at+timedelta(minutes=3);reason='stop';ambiguous=h>=target;break
        if h>=target:
            exit=min(o,target) if o>=target else target;end=at+timedelta(minutes=3);reason='target';break
        prior=at
    if exit is None:return None,'missing_exit_bar'
    exit*=1-slip-spread
    net=exit/entry-1-.001*(1+exit/entry)
    return {**row,'label_kind':'reconstructed_candle_policy_proxy','quality_tier':'reconstructed_unverified_availability',
            'label_available_at':max([end]+observed).isoformat(),'label_matures_at':end.isoformat(),
            'net_return':net,'stress_return':net-(.01 if vg else .002),
            'source':'retained_exact_contract_or_stock_3minute_bars',
            'proxy_entry_at':bars[0]['time'].isoformat(),'proxy_exit_at':end.isoformat(),'proxy_exit_reason':reason,
            'same_bar_stop_target_ambiguity':ambiguous,'historical_availability_verified':False,
            'selection_bias':'Retrospective candle reconstruction; current stock universe and selected frozen option cohorts; no L1/queue proof'},None


def main():
    c=source.connect(readonly=True);out=[];excluded=Counter()
    try:
        with c.cursor(cursor_factory=RealDictCursor) as cur:
            for row in json.load(sys.stdin):
                result,reason=label(row,cur)
                if result:out.append(result)
                else:excluded[row['lane']+':'+reason]+=1
    finally:c.close()
    print(json.dumps({'quality_tier':'reconstructed_unverified_availability','live_promotion_allowed':False,'records':out,'excluded':dict(excluded)},default=str))


if __name__=='__main__':main()
