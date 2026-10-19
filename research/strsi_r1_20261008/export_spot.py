"""Read-only export of 1-min spot bars (all agent spots) from historical.sqlite3."""
import sqlite3, csv
from macd_trader.universe import SPOT_SYMBOLS
c = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro', uri=True)
w = csv.writer(open('/tmp/spot_min.csv', 'w', newline=''))
w.writerow(['symbol', 'ts', 'open', 'high', 'low', 'close', 'volume'])
for s in SPOT_SYMBOLS:
    for row in c.execute('SELECT timestamp, open, high, low, close, volume FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp < 1791398700 ORDER BY timestamp', (s,)):
        w.writerow((s,) + tuple(row))
