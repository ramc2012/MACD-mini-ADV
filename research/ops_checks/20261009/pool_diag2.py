import sqlite3, json
from collections import Counter
from datetime import datetime, timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))
E = lambda d, hm: int(datetime.fromisoformat(f'{d}T{hm}:00+05:30').timestamp())
c = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro', uri=True)
def day_stats(d):
    a, cut, b = E(d, '09:15'), E(d, '14:45'), E(d, '15:30')
    rows = c.execute("SELECT symbol, SUM(timestamp < ?), MAX(CASE WHEN timestamp < ? THEN timestamp END) FROM historical_candles WHERE timeframe_seconds=60 AND timestamp >= ? AND timestamp < ? AND (symbol LIKE '%CE' OR symbol LIKE '%PE') GROUP BY symbol", (cut, cut, a, b)).fetchall()
    n = len(rows); full = sum(1 for s, k, m in rows if k >= 300); thin = sum(1 for s, k, m in rows if k < 60)
    last1444 = sum(1 for s, k, m in rows if m and m >= cut - 120)
    hours = c.execute("SELECT strftime('%H', timestamp, 'unixepoch', '+330 minutes') h, COUNT(*), COUNT(DISTINCT symbol) FROM historical_candles WHERE timeframe_seconds=60 AND timestamp >= ? AND timestamp < ? AND (symbol LIKE '%CE' OR symbol LIKE '%PE') GROUP BY h", (a, b)).fetchall()
    return {'day': d, 'option_symbols': n, '>=300 bars by 14:45': full, '<60 bars': thin, 'bar at 14:43-14:44': last1444, 'bars/distinct symbols by hour': hours}
for d in ('2026-09-30', '2026-10-01', '2026-10-07', '2026-10-08', '2026-10-09'):
    print(json.dumps(day_stats(d)))
