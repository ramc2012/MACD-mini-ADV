"""Latest stored feature observations, independent of completed daily candle dates."""
from datetime import datetime,time,timedelta
import threading,time as clock
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from . import source
from .decision_context import SPECS

_lock=threading.Lock();_saved=None;_saved_at=0


def view():
    global _saved,_saved_at
    with _lock:
        if _saved is not None and clock.monotonic()-_saved_at<5:return _saved
        now=datetime.now(source.IST);out={};c=source.connect(readonly=True)
        try:
            with c.cursor(cursor_factory=RealDictCursor) as q:
                q.execute("SELECT u.symbol,s.sector20 FROM fo_underlying_catalog u LEFT JOIN sector_taxonomy s USING(symbol) WHERE kind='STOCK' AND fno_active IS TRUE")
                names=q.fetchall();out={r['symbol']:{} for r in names}
                for group,table,key,event,receipt,cadence,fields in SPECS:
                    if key not in ('symbol','underlying','sector20'):continue
                    entities=list({r['sector20'] if key=='sector20' else r['symbol'] for r in names})
                    query=sql.SQL('SELECT DISTINCT ON({key}) * FROM {table} WHERE {key}=ANY(%s) AND {event}<=%s').format(
                        key=sql.Identifier(key),table=sql.Identifier(table),event=sql.Identifier(event))
                    cutoff=(now-timedelta(minutes=30)) if cadence=='bar' else now if event=='ts' else now.date()
                    if cadence=='eod' and now.time()<time(15,30):cutoff=source.previous_session(now.date())
                    params=[entities,cutoff]
                    if receipt:query+=sql.SQL(' AND {}<=%s').format(sql.Identifier(receipt));params.append(now)
                    query+=sql.SQL(' ORDER BY {key},{event} DESC').format(key=sql.Identifier(key),event=sql.Identifier(event))
                    q.execute(query,params)
                    rows={r[key]:r for r in q.fetchall()}
                    for r in names:
                        data=rows.get(r['sector20'] if key=='sector20' else r['symbol'])
                        if data:
                            out[r['symbol']][group]={'source':str(data[event]),'computed_at':str(data.get(receipt)) if receipt else None,
                                **{f:float(data[f]) if type(data.get(f)).__name__=='Decimal' else data.get(f) for f in fields}}
        finally:c.close()
        _saved={'symbols':out,'observed_at':now.isoformat(),'basis':'latest stored completed-bar features; EOD fields retain their own date'}
        _saved_at=clock.monotonic();return _saved
