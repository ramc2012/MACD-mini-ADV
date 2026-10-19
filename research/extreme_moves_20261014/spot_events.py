"""After an EXTREME index move, does the move continue or revert? (spot, 1-min, 2021 -> Oct 2026)

ATR = mean true range of the 14 sessions BEFORE the day (daily OHLC from the 1-min bars).
A) Intraday extension: the first minute of a session when the index is >= k x ATR away from the previous
   close (k = 1.0, 1.5, 2.0, 2.5), either way; entry = that minute's close. Outcomes signed in the move's
   direction: +30m, +60m, the session close, D+1 and D+3 closes, and the best/worst excursion to the close.
B) Close-to-close day: |close - previous close| >= k x ATR -> next 1 and 3 sessions (gap included).
C) Three-session run: |close - close 3 sessions ago| >= k x ATR (k = 2, 3) -> next 1 and 3 sessions.
Baselines: the same horizons from comparable non-event times (unconditional drift) are reported alongside.
"""
import json
import numpy as np, pandas as pd

A = json.load(open('/r/dd_nifty_4ind_20261009/data/idx1m.json'))
X = json.load(open('/r/index_model_20261012/data/idx_extra.json'))
SY = {'NIFTY': 'NSE:NIFTY50-INDEX', 'BANKNIFTY': 'NSE:NIFTYBANK-INDEX', 'SENSEX': 'BSE:SENSEX-INDEX', 'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX'}
IST = 19800
out_a, out_b, out_c = [], [], []
for und, sym in SY.items():
    m = pd.DataFrame(list(A.get(sym, [])) + list(X.get(sym, [])), columns=['ts', 'o', 'h', 'l', 'c', 'v']).drop_duplicates('ts').sort_values('ts')
    clock = (m.ts + IST) % 86400
    m = m[(clock >= 33300) & (clock < 55800)].copy()
    m['clock'] = (m.ts + IST) % 86400
    m['day'] = (m.ts + IST) // 86400
    d = m.groupby('day').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('c', 'size'))
    d = d[d.n >= 300]
    pc = d.c.shift()
    tr = pd.concat([d.h - d.l, (d.h - pc).abs(), (d.l - pc).abs()], axis=1).max(axis=1)
    d['atr'] = tr.shift().rolling(14).mean()                     # known before the session
    d['prev'] = pc
    days = list(d.index)
    closes = d.c.to_numpy()
    by_day = {k: g for k, g in m.groupby('day')}
    for i, day in enumerate(days):
        if i < 20 or i + 3 >= len(days) or not np.isfinite(d.atr.iloc[i]): continue
        atr, prev = d.atr.iloc[i], d.prev.iloc[i]
        g = by_day[day]; c = g.c.to_numpy(); cl = g.clock.to_numpy(); hi = g.h.to_numpy(); lo = g.l.to_numpy()
        dev = (c - prev) / atr
        for k in (1.0, 1.5, 2.0, 2.5):
            hits = np.nonzero(np.abs(dev) >= k)[0]
            if not len(hits): continue
            j = hits[0]; s = np.sign(dev[j]); p = c[j]
            def at(minutes):
                q = np.searchsorted(cl, cl[j] + minutes * 60)
                return s * (c[min(q, len(c) - 1)] / p - 1) if q < len(c) else np.nan
            rest_h, rest_l = hi[j:].max(), lo[j:].min()
            out_a.append({'und': und, 'day': day, 'k': k, 'dir': int(s), 'minute': int((cl[j] - 33300) // 60),
                          'm30': at(30), 'm60': at(60), 'close': s * (c[-1] / p - 1), 'd1': s * (closes[i + 1] / p - 1),
                          'd3': s * (closes[i + 3] / p - 1),
                          'mfe_to_close': (rest_h / p - 1) if s > 0 else (1 - rest_l / p),
                          'mae_to_close': (rest_l / p - 1) if s > 0 else (1 - rest_h / p), 'atr_pct': atr / p})
        move = (closes[i] - prev) / atr
        for k in (1.0, 1.5, 2.0, 2.5):
            if abs(move) >= k:
                s = np.sign(move)
                out_b.append({'und': und, 'day': day, 'k': k, 'dir': int(s), 'gap_next': s * (d.o.iloc[i + 1] / closes[i] - 1),
                              'd1': s * (closes[i + 1] / closes[i] - 1), 'd3': s * (closes[i + 3] / closes[i] - 1)})
        if i >= 3:
            run = (closes[i] - closes[i - 3]) / atr
            for k in (2.0, 3.0, 4.0):
                if abs(run) >= k:
                    s = np.sign(run)
                    out_c.append({'und': und, 'day': day, 'k': k, 'dir': int(s), 'd1': s * (closes[i + 1] / closes[i] - 1),
                                  'd3': s * (closes[i + 3] / closes[i] - 1)})
    # unconditional baselines for B/C (any day), signed by that day's own direction
    for i in range(20, len(days) - 3):
        s = np.sign(closes[i] - closes[i - 1])
        out_b.append({'und': und, 'day': days[i], 'k': 0.0, 'dir': int(s), 'gap_next': s * (d.o.iloc[i + 1] / closes[i] - 1),
                      'd1': s * (closes[i + 1] / closes[i] - 1), 'd3': s * (closes[i + 3] / closes[i] - 1)})
A_, B_, C_ = pd.DataFrame(out_a), pd.DataFrame(out_b), pd.DataFrame(out_c)
for df in (A_, B_, C_): df['year'] = pd.to_datetime(df.day, unit='D').dt.year
A_.to_csv('/w/out/intraday_extremes.csv', index=False); B_.to_csv('/w/out/day_extremes.csv', index=False); C_.to_csv('/w/out/run_extremes.csv', index=False)

def row(g, cols):
    r = {'n': len(g), 'days': g.day.nunique()}
    for c in cols:
        v = g[c].dropna(); r[f'{c}_bps'] = v.mean() * 1e4; r[f'{c}_cont%'] = (v > 0).mean() * 100
    return r
pd.set_option('display.width', 250)
print('### A) First touch of k x ATR from the previous close (intraday). + = continues in the move\'s direction')
t = pd.DataFrame([{'k': k, 'side': 'up' if s > 0 else 'down', **row(g, ['m30', 'm60', 'close', 'd1', 'd3'])} for (k, s), g in A_.groupby(['k', 'dir'])])
print(t.round(1).to_string(index=False))
print('\n    when it happens (median minute after 09:15) and room left:')
print(A_.groupby('k').agg(n=('day', 'size'), median_minute=('minute', 'median'), mfe_to_close_bps=('mfe_to_close', lambda x: x.mean() * 1e4), mae_to_close_bps=('mae_to_close', lambda x: x.mean() * 1e4)).round(1).to_string())
print('\n    by year, k=1.5, close / D+1 continuation (bps):')
print(A_[A_.k == 1.5].groupby(['year']).agg(n=('day', 'size'), close=('close', lambda x: x.mean() * 1e4), d1=('d1', lambda x: x.mean() * 1e4), d3=('d3', lambda x: x.mean() * 1e4)).round(1).to_string())
print('\n### B) Close-to-close day >= k x ATR (k=0 row = every day, the baseline)')
print(pd.DataFrame([{'k': k, 'side': 'up' if s > 0 else 'down', **row(g, ['gap_next', 'd1', 'd3'])} for (k, s), g in B_.groupby(['k', 'dir'])]).round(1).to_string(index=False))
print('\n### C) Three-session run >= k x ATR')
print(pd.DataFrame([{'k': k, 'side': 'up' if s > 0 else 'down', **row(g, ['d1', 'd3'])} for (k, s), g in C_.groupby(['k', 'dir'])]).round(1).to_string(index=False))
