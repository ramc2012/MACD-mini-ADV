"""Read-only: our agent's STOCK picks (indices excluded) and its whole stock candidate pool, marked at 14:45 on D+1..D+3."""
import sqlite3, csv
from datetime import datetime
from macd_trader import option_agent as oa
from macd_trader.universe import INDEX_SPOTS
c = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro', uri=True)
def mark(sym, day):
    t = int(datetime.fromisoformat(f'{day}T14:45:00+05:30').timestamp())
    r = c.execute('SELECT close FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp < ? AND timestamp >= ? ORDER BY timestamp DESC LIMIT 1', (sym, t, t - 900)).fetchone()
    return r[0] if r else None
days = ['2026-09-08','2026-09-09','2026-09-10','2026-09-11','2026-09-15','2026-09-16','2026-09-17','2026-09-18','2026-09-29','2026-09-30','2026-10-01','2026-10-09']
w = csv.writer(open('/tmp/agent_1445.csv', 'w', newline=''))
w.writerow(['day', 'kind', 'source', 'symbol', 'side', 'entry', 'd1', 'd2', 'd3'])
for day in days:
    after = [d for d in oa._sessions_after(day)]
    run = c.execute("SELECT run_id, source FROM agent_runs WHERE day=? ORDER BY CASE source WHEN 'scheduled' THEN 0 ELSE 1 END LIMIT 1", (day,)).fetchone()
    picks = {r[0] for r in c.execute('SELECT symbol FROM agent_picks WHERE run_id=?', (run[0],))} if run else set()
    for sym, side, und, entry in c.execute('SELECT symbol, side, underlying, entry_price FROM agent_candidates WHERE day=?', (day,)):
        if und in INDEX_SPOTS or not entry: continue
        rets = [(m / entry - 1) if (m := mark(sym, d)) else None for d in after]
        w.writerow([day, 'pick' if sym in picks else 'pool', run[1] if run else '', sym, side, entry] + rets)
print('done')
