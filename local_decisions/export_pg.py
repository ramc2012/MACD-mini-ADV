"""Run inside the existing lane container; stdout is redirected to a private data file."""
from datetime import datetime,timedelta
from pathlib import Path
import json

from psycopg2.extras import RealDictCursor
from shadow_lanes import source
from shadow_lanes.vendor.daily_stock_selection import DAILY_SQL

now=datetime.now(source.IST);out={'captured_at':now.isoformat(),'query_only':True}
c=source.connect(readonly=True)
try:
    with c.cursor(cursor_factory=RealDictCursor) as cur:
        for table in ('vanguard_swing_watchlist_runs','vanguard_swing_watchlist_items','vanguard_paper_picks','vanguard_paper_positions'):
            cur.execute('SELECT * FROM '+table);out[table]=[dict(r) for r in cur.fetchall()]
        cur.execute(DAILY_SQL.replace(':start','%(start)s').replace(':as_of','%(as_of)s'),{'start':now-timedelta(days=300),'as_of':now})
        out['cbe_daily']=[dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT DISTINCT ON (underlying,time) underlying,time,open,synced_at
          FROM underlying_spot_candles WHERE interval='3minute' AND time>='2026-09-01' AND time<%s
          AND (time AT TIME ZONE 'Asia/Kolkata')::time=time '09:21' AND open>0
          AND source IN ('upstox_spot','upstox_sweep','upstox')
          ORDER BY underlying,time,CASE source WHEN 'upstox_spot' THEN 0 WHEN 'upstox_sweep' THEN 1 ELSE 2 END,synced_at DESC""",(now,))
        out['cbe_next_open']=[dict(r) for r in cur.fetchall()]
        cur.execute('SELECT symbol,sector20 FROM sector_taxonomy');out['sectors']={r['symbol']:r['sector20'] for r in cur.fetchall()}
finally:c.close()
out['legacy_cbe']=json.loads(Path('/legacy/cbe/paper_positions.json').read_text())
out['legacy_cbe_open_events']=[]
p=Path('/legacy/cbe/paper_journal.jsonl')
if p.exists():
    for line in p.open():
        try:r=json.loads(line)
        except ValueError:continue
        if 'open' in str(r.get('event','')).lower():out['legacy_cbe_open_events'].append(r)
print(json.dumps(out,default=str))
