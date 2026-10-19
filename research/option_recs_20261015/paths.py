"""One-minute premium paths after each premium-cleared cross (read-only).

PASS rows: from the first minute after the signal bar to expiry day 15:10 IST (capped at 15 sessions).
BREADTH_TOO_THIN / NOT_OFF_HIGH rows (controls): 5 sessions. Output: data/paths.npz (row index into
out/crosses.csv.gz, ts, open, high, low, close, volume).
"""
import sqlite3
import numpy as np, pandas as pd

x = pd.read_csv('/w/out/crosses.csv.gz')
x = x[x.verdict.isin(['PASS', 'BREADTH_TOO_THIN', 'NOT_OFF_HIGH'])]
db = sqlite3.connect('file:/data/historical.sqlite3?mode=ro', uri=True, timeout=30)
out = {k: [] for k in ('row', 'ts', 'open', 'high', 'low', 'close', 'volume')}
for i, r in x.iterrows():
    exp_close = int(pd.Timestamp(f'{r.expiry} 15:10', tz='Asia/Kolkata').timestamp())
    horizon = (15 if r.verdict == 'PASS' else 5) * 86400 * 7 // 5  # sessions -> calendar seconds, roughly
    end = min(exp_close, int(r.signal_end) + horizon)
    rows = db.execute("""SELECT timestamp, open, high, low, close, volume FROM historical_candles
                         WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? AND timestamp <= ?
                         ORDER BY timestamp""", (r.symbol, int(r.signal_end), end)).fetchall()
    for ts, o, h, l, c, v in rows:
        out['row'].append(i); out['ts'].append(ts); out['open'].append(o); out['high'].append(h)
        out['low'].append(l); out['close'].append(c); out['volume'].append(v)
np.savez_compressed('/w/data/paths.npz', **{k: np.asarray(v) for k, v in out.items()})
print('rows', len(x), 'bars', len(out['ts']))
