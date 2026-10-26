"""Derived-market refresh receipts. No broker, publisher or trading commands."""
import asyncio
from datetime import datetime,timedelta,time
import json
import os
from pathlib import Path
import sqlite3
import sys
from psycopg2.extensions import make_dsn
from . import source

LIVE_STEPS = (
 ('implied_vol','features/m_implied_vol.py','--lookback-days','1','--write'),
 ('premium_ratios','features/m_option_ratios.py','--lookback-days','3','--write'),
 ('iv_surface','features/m_iv_surface.py','--lookback-days','120','--write'),
 ('sentiment','features/m_sentiment.py','--lookback-days','5','--write'),
 ('flow','features/m2_flow.py','--lookback-days','130'),
 ('regime','features/m3_gex.py','--lookback-days','60'),
 ('sector','features/m4_sector.py','--lookback-days','180'),
 ('timing','features/m5_timing.py','--lookback-days','3','--buffer-days','60','--write','--no-spot-check'),
 ('futures_baselines','features/m_futures_oi.py','--lookback-days','3'),
)
EOD_STEPS = (
 ('market_profile','features/m_market_profile.py','--lookback-days','200','--write-days','2','--write'),
 ('oi_positioning','features/m_oi_positioning.py','--lookback-days','5','--write'),
)


def completed_bar_key(now):
    local=now.astimezone(source.IST);shifted=local-timedelta(seconds=90)
    if not source.is_session(local.date()) or shifted.date()!=local.date() or local.time()>=time(15,50) or shifted.time()<time(9,45):return None
    from model.session_clock import session_close
    if shifted.time()>=session_close(local.date()):index=12
    else:index=(shifted.hour*60+shifted.minute-555)//30-1
    return str(local.date()),index


def database():
    p=Path(os.environ.get('FEATURE_RECEIPTS_PATH','/runtime/shadow_lanes/feature_receipts.sqlite3'))
    p.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(p,timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,run_key TEXT,stage TEXT,started_at TEXT,finished_at TEXT,status TEXT,detail TEXT)')
    return c


def status():
    with database() as c:
        rows=c.execute('SELECT stage,finished_at,status,detail FROM runs WHERE id IN (SELECT max(id) FROM runs GROUP BY stage)').fetchall()
    return {name:dict(checked_at=at,status=s,detail=json.loads(d)) for name,at,s,d in rows}


def category(error):
    text=error.lower()
    for needle,label in ((b'no unique or exclusion constraint','missing_conflict_index'),(b'permission denied','permission_denied'),
                         (b'statement timeout','statement_timeout'),(b'no space left','storage_full')):
        if needle in text:return label
    return 'stage_failed'


async def refresh(run_key,steps=LIVE_STEPS):
    env=dict(os.environ)
    env['VANGUARD_DATABASE_URL']=make_dsn(host=env.get('MI_PG_HOST','nomadcurie_db'),dbname=env.get('LANE_SOURCE_DB','nomadcurie'),user=env['MI_PG_USER'],password=env['MI_PG_PASSWORD'])
    env.update(SHARED_MP_REDIS_URL='',VANGUARD_SCHEMA_MANAGED='true')
    results={}
    if any(s[0]=='futures_baselines' for s in steps):
        from .futures_context import sync
        start=datetime.now(source.IST).isoformat()
        try:
            detail=await asyncio.to_thread(sync)
            state='ok' if detail.get('written') else 'no_recorded_futures_ticks'
        except Exception as exc:
            state='source_failed';detail={'exception_class':type(exc).__name__,'broker_requests':0}
        with database() as c:c.execute('INSERT INTO runs(run_key,stage,started_at,finished_at,status,detail) VALUES(?,?,?,?,?,?)',
                (str(run_key),'shared_futures_ticks',start,datetime.now(source.IST).isoformat(),state,json.dumps(detail)))
        results['shared_futures_ticks']=state
    for name,script,*args in steps:
        if not script.startswith('features/') or '..' in script:raise ValueError('Derived feature command required')
        start=datetime.now(source.IST).isoformat()
        process=await asyncio.create_subprocess_exec(sys.executable,str(source.VG_ROOT/script),*args,cwd=source.VG_ROOT,env=env,
                 stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
        try:
            _,error=await asyncio.wait_for(process.communicate(),timeout=300)
            state='ok' if process.returncode==0 else category(error)
        except asyncio.TimeoutError:
            process.kill();await process.communicate();state='stage_timeout'
        except asyncio.CancelledError:
            if process.returncode is None:process.kill()
            await process.communicate();raise
        finish=datetime.now(source.IST).isoformat()
        detail={'returncode':process.returncode,'script':script,'args':args,'historical_availability_established':False}
        with database() as c:c.execute('INSERT INTO runs(run_key,stage,started_at,finished_at,status,detail) VALUES(?,?,?,?,?,?)',
                                      (str(run_key),name,start,finish,state,json.dumps(detail)))
        results[name]=state
        # Independent stages continue even after a failed upstream stage. Admission still checks complete inputs.
    return results
