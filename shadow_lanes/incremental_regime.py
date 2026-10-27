"""Current GEX with a once-per-session raw-derived baseline cache.

Cached baseline uses the SAME snapshot loader and formula as the full job.
The retained regime table can contain later legacy rows; max(ts) there is not
an equivalent baseline. No broker request or historical decision is created.
"""
from datetime import datetime,time,timedelta
import os,sqlite3,uuid
from pathlib import Path
from . import source


def cache():
    path=Path(os.environ.get('REGIME_BASELINE_DATABASE','/runtime/shadow_lanes/regime_baseline.sqlite3'))
    path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(path,timeout=10)
    db.executescript('''CREATE TABLE IF NOT EXISTS builds(id TEXT PRIMARY KEY,observed_at TEXT,completed_through TEXT);
    CREATE TABLE IF NOT EXISTS baseline(build_id TEXT,symbol TEXT,day TEXT,ts TEXT,net_gex REAL,flip REAL,
      PRIMARY KEY(build_id,symbol,day));''')
    return db


def stable_daily(g,frame):
    keys=[k for k in ('underlying','day','expiry','strike','option_type','gamma','oi') if k in frame.columns]
    return g.build_daily_series(frame.sort_values(keys,kind='mergesort'))


def store_baseline(daily,now):
    import pandas as pd
    identity=uuid.uuid4().hex
    completed=now.date() if now.time()>=time(15,50) else source.previous_session(now.date())
    with cache() as db:
        db.execute('INSERT INTO builds VALUES(?,?,?)',(identity,now.isoformat(),str(completed)))
        db.executemany('INSERT INTO baseline VALUES(?,?,?,?,?,?)',[(identity,r.symbol,str(r.day),str(r.ts),float(r.net_gex),
            None if pd.isna(r.gamma_flip_level) else float(r.gamma_flip_level)) for r in daily.itertuples()])
    return identity


def load_baseline(now):
    import pandas as pd
    with cache() as db:
        build=db.execute('SELECT id,completed_through FROM builds ORDER BY observed_at DESC LIMIT 1').fetchone()
        if not build or build[1]<str(source.previous_session(now.date())):return None
        rows=db.execute('SELECT symbol,day,ts,net_gex,flip FROM baseline WHERE build_id=?',(build[0],)).fetchall()
    frame=pd.DataFrame(rows,columns=['symbol','day','ts','net_gex','gamma_flip_level'])
    if frame.empty:return None
    frame['day']=frame.day.map(lambda d:datetime.fromisoformat(d).date())
    frame['ts']=pd.to_datetime(frame.ts,utc=True)
    return frame[(frame.day<now.date())&(frame.ts>now-timedelta(days=60))]


def ensure_baseline():
    from features import m3_gex as g
    now=datetime.now(source.IST)
    with cache() as db:
        last=db.execute('SELECT completed_through FROM builds ORDER BY observed_at DESC LIMIT 1').fetchone()
    if last and last[0]>=str(now.date()):return {'status':'cached'}
    c=source.connect(False);c.commit();c.autocommit=True
    try:
        with c.cursor() as q:q.execute("SET statement_timeout='120s'")
        daily=stable_daily(g,g.load_snapshot_rows(c,None,60))
        identity=store_baseline(daily,now)
        return {'status':'built','id':identity,'rows':len(daily),'broker_requests':0}
    finally:c.close()


def run():
    from features import m3_gex as g
    import pandas as pd
    c=source.connect(False);c.commit();c.autocommit=True;now=datetime.now(source.IST)
    try:
        previous=load_baseline(now)
        if previous is None:
            with c.cursor() as q:q.execute("SET statement_timeout='120s'")
            all_days=stable_daily(g,g.load_snapshot_rows(c,None,60));store_baseline(all_days,now)
            result=g.compute_regime(all_days)
            result=result[result.day==now.date()]
            return {'written':g.upsert(c,result),'mode':'baseline_bootstrap','broker_requests':0}
        current=stable_daily(g,g.load_snapshot_rows(c,None,1))
        if current.empty:return {'written':0,'reason':'no_current_gamma'}
        current=current[current.day==now.date()]
        if current.empty:return {'written':0,'reason':'no_current_gamma'}
        result=g.compute_regime(pd.concat([previous,current],ignore_index=True).sort_values(['symbol','day']))
        result=result[result.day==now.date()]
        return {'written':g.upsert(c,result),'mode':'current_only_verified_baseline','broker_requests':0}
    finally:c.close()


if __name__=='__main__':
    import json
    result=run();print(json.dumps(result));raise SystemExit(0 if result['written'] else 1)
