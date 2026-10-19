import sqlite3, csv
from macd_trader.universe import INDEX_SPOTS
c = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro', uri=True)
picked = {(d, s) for d, s in c.execute("SELECT p.day, p.symbol FROM agent_picks p JOIN agent_runs r USING(run_id) WHERE r.source IN ('scheduled','walk-forward')")}
w = csv.writer(open('/tmp/cands.csv', 'w', newline=''))
w.writerow(['day', 'symbol', 'side', 'underlying', 'entry', 'r1', 'r2', 'r3', 'y', 'picked'])
for d, s, side, u, e, r1, r2, r3, y in c.execute('SELECT day, symbol, side, underlying, entry_price, r1, r2, r3, y FROM agent_candidates WHERE y IS NOT NULL'):
    if u in INDEX_SPOTS: continue
    w.writerow([d, s, side, u, e, r1, r2, r3, y, int((d, s) in picked)])
