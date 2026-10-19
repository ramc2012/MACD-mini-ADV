"""Step 2b (inside the desk container, read-only): real option premiums for live events.

For each event: nearest-strike CE and PE with a 1-min bar at the entry minute, from series that
expire after the third session ahead (the 3-day hold never crosses expiry). Entry = that minute's
open. Returns at the same horizons as the spot study, the Supertrend exit, and MFE/MAE to D+3.
"""
import csv, json, sqlite3, sys, random
from datetime import date
from macd_trader import option_agent as oa

IST = 19800
c = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro', uri=True)
cat = {}
for sym, exp in c.execute("SELECT symbol, MAX(expiry) FROM historical_candles WHERE timeframe_seconds=60 AND (symbol LIKE '%CE' OR symbol LIKE '%PE') GROUP BY symbol"):
    p = oa.parse_option(sym, exp)
    if p: cat.setdefault(p['root'], []).append((sym, p['expiry'], p['strike'], p['side']))
print('catalog roots', len(cat), file=sys.stderr)

from macd_trader.universe import INDEX_SPOTS
def root_of(spot):
    for k, v in INDEX_SPOTS.items():
        if v == spot: return k
    return spot.split(':')[1].rsplit('-', 1)[0]

def bars(sym, a, b):
    return c.execute('SELECT timestamp, open, high, low, close FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp BETWEEN ? AND ? ORDER BY timestamp', (sym, a, b)).fetchall()

def horizon_ts(day, entry_ts):
    after = oa._sessions_after(day)
    d15 = lambda d: int(__import__('datetime').datetime.fromisoformat(d + 'T15:15:00+05:30').timestamp())
    return {'m15': entry_ts + 900, 'm30': entry_ts + 1800, 'm60': entry_ts + 3600, 'close': d15(day),
            'd1': d15(after[0]), 'd2': d15(after[1]), 'd3': d15(after[2])}, after

def evaluate(spot_sym, day, entry_ts, spot, st_exit_ts):
    root = root_of(spot_sym)
    hz, after = horizon_ts(day, entry_ts)
    out = {}
    for side, tag in ((1, 'ce'), (-1, 'pe')):
        cands = [x for x in cat.get(root, []) if x[3] == side and x[1] > after[-1]]
        if not cands: return None
        nearest = min(x[1] for x in cands)
        cands = sorted([x for x in cands if x[1] == nearest], key=lambda x: abs(x[2] - spot))[:4]
        chosen = None
        for sym, exp, strike, _ in cands:
            b = bars(sym, entry_ts, hz['d3'] + 900)
            if b and b[0][0] - entry_ts <= 120:
                chosen = (sym, exp, strike, b); break
        if not chosen: return None
        sym, exp, strike, b = chosen
        entry = b[0][1]
        if not entry or entry <= 0: return None
        ts = [r[0] for r in b]
        def at(t):
            j = max(i for i in range(len(ts)) if ts[i] <= t) if ts[0] <= t else None
            return b[j][4] / entry - 1 if j is not None else None
        res = {f'{tag}_sym': sym, f'{tag}_strike': strike, f'{tag}_expiry': exp, f'{tag}_entry': entry,
               f'{tag}_mny': (spot - strike) / spot * side}
        for k, t in hz.items():
            # a horizon is valid only if the contract printed in that session
            r = at(t)
            res[f'{tag}_{k}'] = r
        res[f'{tag}_st_exit'] = at(int(st_exit_ts)) if st_exit_ts else None
        res[f'{tag}_mfe'] = max(r[2] for r in b) / entry - 1
        res[f'{tag}_mae'] = min(r[3] for r in b) / entry - 1
        out.update(res)
    return out

rows = list(csv.DictReader(open('/tmp/in_events.csv')))
w = None
fo = open('/tmp/live_options.csv', 'w', newline='')
for i, r in enumerate(rows):
    st = r.get('st_exit_ts')
    res = evaluate(r['sym'], r['day'], int(float(r['entry_ts'])), float(r['entry']), int(float(st)) if st not in (None, '', 'nan') else None)
    if res is None: continue
    rec = {'kind': r['kind'], 'sym': r['sym'], 'day': r['day'], 'entry_ts': r['entry_ts'], 'group': r.get('group', ''), **res}
    if w is None:
        w = csv.DictWriter(fo, fieldnames=list(rec.keys())); w.writeheader()
    w.writerow(rec)
    if i % 500 == 0: print(i, len(rows), file=sys.stderr, flush=True)
fo.close()
