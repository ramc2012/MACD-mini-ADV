"""Were the MACD entry gate's skips right? Replay every scored signal on its real option bars. Read-only.
Entry: first 1-minute bar at/after the decision. Exit: -30% hard stop, 25% trail armed at +30% (floored at
breakeven), else the next session's close. Costs 1% round trip (spread). Signals are live-band, so no survivorship."""
import json, sqlite3
from datetime import datetime, timedelta
import numpy as np, pandas as pd
IST = 19800
h = sqlite3.connect('file:/data/historical.sqlite3?mode=ro', uri=True)
s = pd.read_sql("select at, day, symbol, score, threshold, decision, features from macd_entry_scores where day>='2026-10-01'", h)
s['dte'] = s.features.map(lambda f: json.loads(f).get('dte'))
s['t0'] = pd.to_datetime(s['at'], format='ISO8601').astype('int64') // 10**9
days = sorted({d for (d,) in h.execute("select distinct date(timestamp,'unixepoch','+330 minutes') from historical_candles where symbol='NSE:NIFTY50-INDEX' and timeframe_seconds=60 and timestamp>1790000000")})
def next_close(day):
    later = [d for d in days if d > day]
    d = later[0] if later else day
    return int(pd.Timestamp(f'{d} 15:25', tz='Asia/Kolkata').timestamp())
out = []
for r in s.itertuples():
    bars = h.execute('select timestamp, open, high, low, close from historical_candles where symbol=? and timeframe_seconds=60 and timestamp>=? and timestamp<=? order by timestamp',
                     (r.symbol, r.t0 - r.t0 % 60, next_close(r.day))).fetchall()
    if len(bars) < 5 or bars[0][0] - r.t0 > 180: continue
    e = bars[0][1]; peak = e; floor = 0; ret = None
    for ts, o, hi, lo, c in bars:
        level = max(e * 0.7, floor)
        if lo <= level: ret = min(o, level) / e - 1; break
        peak = max(peak, hi)
        if peak >= e * 1.3: floor = max(floor, peak * 0.75, e * 1.01)
    if ret is None: ret = bars[-1][4] / e - 1
    out.append({'day': r.day, 'decision': r.decision, 'score': r.score, 'dte': r.dte, 'ret': ret - 0.01})
o = pd.DataFrame(out)
pd.set_option('display.width', 200)
o['grp'] = np.where(o.decision == 'SKIPPED_LOW_SCORE', 'skipped', np.where(o.decision == 'TAKEN', 'taken', 'other'))
print('signals replayed', len(o))
print(o.groupby('grp').ret.agg(['size', 'mean', 'median', lambda x: (x > 0).mean()]).round(3))
o['dteb'] = pd.cut(o.dte, [0, 7, 14, 21, 60], labels=['<=7', '8-14', '15-21', '22+'])
print('\nby days to expiry (taken+skipped):')
print(o[o.grp != 'other'].groupby(['dteb', 'grp'], observed=True).ret.agg(['size', 'mean', lambda x: (x > 0).mean()]).round(3))
print('\nby score quintile (all scored):')
o['q'] = pd.qcut(o.score, 5, labels=False)
print(o.groupby('q').agg(n=('ret', 'size'), score=('score', 'mean'), ret=('ret', 'mean'), win=('ret', lambda x: (x > 0).mean())).round(3))
per = o[o.grp != 'other'].groupby(['day', 'grp']).ret.mean().unstack()
print('\nper day taken vs skipped:'); print(per.round(3))
d = (per.taken - per.skipped).dropna(); print('taken - skipped per day: mean %.3f, t %.2f, days %d' % (d.mean(), d.mean() / (d.std(ddof=1) / np.sqrt(len(d))), len(d)))
print('score vs return corr (all):', round(np.corrcoef(o.score, o.ret)[0, 1], 3), '| within dte<=14:', round(np.corrcoef(o[o.dte <= 14].score, o[o.dte <= 14].ret)[0, 1], 3))
