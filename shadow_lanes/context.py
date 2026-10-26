"""Mirror already-collected public participant context; no download or app API dependency."""
import os
import psycopg2
from psycopg2.extras import execute_values


def sync_participants(destination):
    source = psycopg2.connect(host=os.environ.get("MI_PG_HOST", "nomadcurie_db"), user=os.environ["MI_PG_USER"],
        password=os.environ["MI_PG_PASSWORD"], dbname=os.environ.get("MI_PG_DB", "market_intelligence"), connect_timeout=5)
    source.set_session(readonly=True)
    try:
        with source.cursor() as cur:
            cur.execute("SET statement_timeout='5s'")
            cur.execute("SELECT day,participant,bucket,long_contracts,short_contracts FROM participant_oi WHERE day>=CURRENT_DATE-7")
            rows = cur.fetchall()
        if rows:
            with destination, destination.cursor() as cur:
                execute_values(cur, """INSERT INTO participant_oi(dt,participant,bucket,long_contracts,short_contracts)
                    VALUES %s ON CONFLICT(dt,participant,bucket) DO UPDATE SET
                    long_contracts=excluded.long_contracts,short_contracts=excluded.short_contracts""", rows)
        return len(rows)
    finally:
        source.close()


def _append_missing(cur,table,columns,rows,keys,clock):
    """Insert-only mirror under one advisory transaction lock; preserve retained rows."""
    from psycopg2 import sql
    if not rows:return 0
    indexes=[columns.index(k) for k in keys];ci=columns.index(clock)
    rows=list({tuple(r[i] for i in indexes):r for r in rows}.values())
    low=min(r[ci] for r in rows);high=max(r[ci] for r in rows)
    cur.execute(sql.SQL('SELECT {} FROM {} WHERE {}>=%s AND {}<=%s').format(
        sql.SQL(',').join(map(sql.Identifier,keys)),sql.Identifier(table),sql.Identifier(clock),sql.Identifier(clock)),(low,high))
    seen=set(cur.fetchall());rows=[r for r in rows if tuple(r[i] for i in indexes) not in seen]
    if not rows:return 0
    if 'id' in columns:
        # Recovered market tables have no serial default. Allocate IDs while
        # the dedicated public-context advisory lock excludes another mirror.
        cur.execute(sql.SQL('SELECT coalesce(max(id),0) FROM {}').format(sql.Identifier(table)));last=cur.fetchone()[0]
        ix=columns.index('id');fixed=[]
        for n,row in enumerate(rows,1):v=list(row);v[ix]=last+n;fixed.append(tuple(v))
        rows=fixed
    query=sql.SQL('INSERT INTO {} ({}) VALUES %s ON CONFLICT DO NOTHING').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)))
    execute_values(cur,query.as_string(cur),rows,page_size=1000)
    return len(rows)


def sync_public_reports(destination):
    """Mirror only already-fetched public reports. This function never downloads."""
    from datetime import datetime,timedelta
    from psycopg2.extras import RealDictCursor
    from .source import IST
    today=datetime.now(IST).date();since=today-timedelta(days=7)
    c=psycopg2.connect(host=os.environ.get('MI_PG_HOST','nomadcurie_db'),user=os.environ['MI_PG_USER'],password=os.environ['MI_PG_PASSWORD'],dbname=os.environ.get('MI_PG_DB','market_intelligence'),connect_timeout=5)
    c.set_session(readonly=True);tables={}
    try:
        with c.cursor(cursor_factory=RealDictCursor) as q:
            q.execute("SET statement_timeout='8s'")
            for table,clock in (('equity_daily','day'),('bulk_block','day'),('announcements','dt'),('fo_ban','day'),('board_meetings','meeting_date')):
                if table=='board_meetings':q.execute('SELECT * FROM board_meetings WHERE meeting_date>=%s AND collected_at<=now()',(today,))
                else:q.execute('SELECT * FROM '+table+' WHERE '+clock+'>=%s',(since,))
                tables[table]=[dict(r) for r in q.fetchall()]
    finally:c.close()
    out={}
    with destination,destination.cursor() as q:
        q.execute("SELECT pg_try_advisory_xact_lock(hashtext('platform-public-context-mirror'))")
        if not q.fetchone()[0]:return {'status':'another_mirror_active'}
        rows=[(r['day'],r['symbol'],r['open'],r['high'],r['low'],r['close'],r['prev_close'],r['volume'],
               r['turnover_lacs']*100000 if r['turnover_lacs'] is not None else None,r['deliv_qty'],r['deliv_pct'],'platform_nse_current',r['collected_at']) for r in tables['equity_daily'] if r['series']=='EQ']
        cols=['dt','symbol','open','high','low','close','prev_close','volume','value','deliverable_qty','delivery_pct','source','synced_at']
        out['delivery']=_append_missing(q,'bhavcopy_delivery',cols,rows,['dt','symbol'],'dt')
        rows=[(None,r['day'],r['symbol'],r['client_name'],r['deal_type'],r['kind'],r['quantity'],r['price'],'platform_nse_current',r['collected_at']) for r in tables['bulk_block']]
        cols=['id','dt','symbol','client_name','deal_type','kind','quantity','price','source','synced_at']
        out['deals']=_append_missing(q,'bulk_block',cols,rows,['dt','symbol','client_name','deal_type','kind','quantity','price'],'dt')
        rows=[(None,r['symbol'],r['dt'],r['subject'],r['description'],r['attachment_url'],None,'platform_nse_current',r['collected_at']) for r in tables['announcements']]
        cols=['id','symbol','dt','subject','description','attachment_url','category','source','synced_at']
        out['announcements']=_append_missing(q,'announcements',cols,rows,['symbol','dt','subject'],'dt')
        rows=[(r['day'],r['symbol'],'Published NSE ban list','platform_nse_current',r['collected_at']) for r in tables['fo_ban']]
        out['ban']=_append_missing(q,'fo_security_ban',['snapshot_date','symbol','reason','source','synced_at'],rows,['snapshot_date','symbol'],'snapshot_date')
        rows=[(r['symbol'],r['meeting_date'],'platform_nse_current',r['collected_at']) for r in tables['board_meetings'] if 'result' in r['purpose'].lower()]
        out['results']=_append_missing(q,'results_calendar',['symbol','results_date','source','synced_at'],rows,['symbol','results_date'],'results_date')
    out['participant_rows']=sync_participants(destination)
    return out
