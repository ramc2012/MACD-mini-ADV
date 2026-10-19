"""Step 2c: real NIFTY / SENSEX option premiums (TradeBot 1-min archive) for index events.

Same rules as live: nearest series that expires after the third session ahead, nearest strike
with a bar at the entry minute, entry at that minute's open. Controls: the same clock time on
8 randomly drawn other days per event (time-matched).
"""
import sys, os, re, glob, random
from datetime import datetime, timedelta
import numpy as np, pandas as pd

DS = sys.argv[1]; UND = DS.upper(); W = '/w/'; IST = 19800
spot = pd.read_csv(W + f'out/spot_{DS}.csv'); spot = spot[spot.set == 'ST+RSI70+R1']
ctrl = pd.read_csv(W + f'out/spot_ctrl_{DS}.csv')
ctrl = ctrl.groupby('event_ts', group_keys=False).apply(lambda g: g.sample(min(8, len(g)), random_state=7))
_s = pd.read_csv(f'/tb/spot/underlying={UND}/1minute.csv.gz', usecols=['time']); sessions = sorted(set(_s['time'].str[:10]))

cat = []
for f in glob.glob(f'/tb/contracts/underlying={UND}/expiry_kind=*/expiry=*/*/*.csv.gz'):
    m = re.search(r'expiry=(\d{4}-\d{2}-\d{2})/(CE|PE)/.* (\d+(?:\.\d+)?) (CE|PE) ', f)
    if m: cat.append((m.group(1), 1 if m.group(2) == 'CE' else -1, float(m.group(3)), f))
cat = pd.DataFrame(cat, columns=['expiry', 'side', 'strike', 'file'])
print(UND, 'contracts', len(cat), cat.expiry.min(), cat.expiry.max(), file=sys.stderr)
cache = {}
def load(f):
    if f not in cache:
        g = pd.read_csv(f); g['ts'] = (pd.to_datetime(g['time']).astype('int64') // 10**9).astype('int64')
        cache[f] = g[['ts', 'open', 'high', 'low', 'close']].sort_values('ts').reset_index(drop=True)
    return cache[f]

def after(day, n=3):
    i = sessions.index(day) if day in sessions else None
    if i is None or i + n >= len(sessions): return None
    return sessions[i + 1:i + 1 + n]

def d15(d): return int(datetime.fromisoformat(d + 'T15:15:00+05:30').timestamp())

def evaluate(day, entry_ts, spot_px, st_exit_ts):
    aft = after(day)
    if not aft: return None
    hz = {'m15': entry_ts + 900, 'm30': entry_ts + 1800, 'm60': entry_ts + 3600, 'close': d15(day),
          'd1': d15(aft[0]), 'd2': d15(aft[1]), 'd3': d15(aft[2])}
    out = {}
    for side, tag in ((1, 'ce'), (-1, 'pe')):
        c = cat[(cat.side == side) & (cat.expiry > aft[-1])]
        if c.empty: return None
        c = c[c.expiry == c.expiry.min()].assign(dist=lambda d: (d.strike - spot_px).abs()).sort_values('dist').head(4)
        chosen = None
        for r in c.itertuples():
            g = load(r.file)
            b = g[(g.ts >= entry_ts) & (g.ts <= hz['d3'] + 900)]
            if len(b) and b.ts.iloc[0] - entry_ts <= 120 and b.open.iloc[0] > 0:
                chosen = (r, b); break
        if chosen is None: return None
        r, b = chosen; entry = b.open.iloc[0]
        def at(t):
            x = b[b.ts <= t]
            return x.close.iloc[-1] / entry - 1 if len(x) else None
        res = {f'{tag}_strike': r.strike, f'{tag}_expiry': r.expiry, f'{tag}_entry': entry, f'{tag}_mny': (spot_px - r.strike) / spot_px * side}
        for k, t in hz.items(): res[f'{tag}_{k}'] = at(t)
        res[f'{tag}_st_exit'] = at(int(st_exit_ts)) if st_exit_ts == st_exit_ts and st_exit_ts else None
        res[f'{tag}_mfe'] = b.high.max() / entry - 1; res[f'{tag}_mae'] = b.low.min() / entry - 1
        out.update(res)
    return out

rows = []
for kind, df in (('signal', spot), ('control', ctrl)):
    for r in df.itertuples():
        res = evaluate(r.day, int(r.entry_ts), float(r.entry), r.st_exit_ts)
        if res: rows.append({'kind': kind, 'sym': UND, 'day': r.day, 'entry_ts': r.entry_ts,
                             'group': r.entry_ts if kind == 'signal' else r.event_ts, **res})
pd.DataFrame(rows).to_csv(W + f'out/{DS}_options.csv', index=False)
print(DS, pd.DataFrame(rows).groupby('kind').size().to_dict())
