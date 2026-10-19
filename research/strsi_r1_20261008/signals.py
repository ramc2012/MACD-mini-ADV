"""Step 1: spot signals for 'Supertrend up + RSI > 70 + close above daily R1 -> buy CE'.

Bars: 15-min, session-aligned from 09:15, built from 1-min spot. Indicators run across days
(as on a chart). Supertrend(10,3) with Wilder ATR; RSI(14) Wilder; classic floor pivots from
the previous session (P=(H+L+C)/3, R1=2P-L). A condition set fires on the FIRST completed bar
of a day where all its conditions hold, if that bar ends by 15:00. Entry = next minute's open.

Usage: signals.py live|nifty|sensex  -> events_<ds>.csv
"""
import sys, json, gzip, glob, math
from datetime import datetime, timedelta, timezone, date
import numpy as np, pandas as pd

DS = sys.argv[1]
OUT = '/w/out/'
BAR = int(sys.argv[2]) if len(sys.argv) > 2 else 15
IST = 19800
SETS = {'ST': 'A', 'RSI70': 'B', 'R1': 'C', 'ST+RSI70': 'AB', 'ST+R1': 'AC', 'RSI70+R1': 'BC', 'ST+RSI70+R1': 'ABC'}

def load():
    """Yields (spot_symbol, minute frame with ts,o,h,l,c) and a daily frame for pivots."""
    if DS == 'live':
        m = pd.read_csv('/w/data/spot_min.csv')
        daily = json.load(open('/w/data/daily_all.json'))['bars']
        for s, g in m.groupby('symbol'):
            g = g.rename(columns={'open': 'o', 'high': 'h', 'low': 'l', 'close': 'c'})[['ts', 'o', 'h', 'l', 'c']]
            d = pd.DataFrame(daily.get(s, []), columns=['ts', 'o', 'h', 'l', 'c', 'v'])
            if d.empty: continue
            d['day'] = pd.to_datetime(d.ts + IST, unit='s').dt.date
            yield s, g.sort_values('ts').reset_index(drop=True), d.drop_duplicates('day').set_index('day')[['h', 'l', 'c']]
    else:
        und = DS.upper()
        f = f'/tb/spot/underlying={und}/1minute.csv.gz'
        g = pd.read_csv(f)
        t = pd.to_datetime(g['time'])
        g['ts'] = (t.astype('int64') // 10**9).astype('int64')
        g = g.rename(columns={'open': 'o', 'high': 'h', 'low': 'l', 'close': 'c'})[['ts', 'o', 'h', 'l', 'c']]
        g = g.sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
        day = pd.to_datetime(g.ts + IST, unit='s').dt.date
        d = g.groupby(day).agg(h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('c', 'size'))
        d = d[d.n >= 300][['h', 'l', 'c']]
        yield und, g, d

def bars15(g):
    day = (g.ts + IST) // 86400
    open_ = day * 86400 - IST + 33300                      # 09:15 IST
    k = (g.ts - open_) // (BAR * 60)
    b = g.assign(day=day, start=open_ + k * BAR * 60).groupby('start').agg(
        o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), last=('ts', 'max'), day=('day', 'first'))
    b = b.reset_index()
    b['end'] = np.minimum(b['start'] + BAR * 60, b['last'] + 60)
    return b

def wilder_atr(h, l, c, n):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
    a = np.full(len(c), np.nan)
    if len(c) >= n:
        a[n - 1] = tr[:n].mean()
        for i in range(n, len(c)): a[i] = (a[i - 1] * (n - 1) + tr[i]) / n
    return a

def supertrend(h, l, c, n=10, mult=3.0):
    a = wilder_atr(h, l, c, n); mid = (h + l) / 2
    ub = mid + mult * a; lb = mid - mult * a
    d = np.zeros(len(c), int)
    for i in range(n - 1, len(c)):
        if i == n - 1: d[i] = -1; continue
        if not (ub[i] < ub[i - 1] or c[i - 1] > ub[i - 1]): ub[i] = ub[i - 1]
        if not (lb[i] > lb[i - 1] or c[i - 1] < lb[i - 1]): lb[i] = lb[i - 1]
        d[i] = (1 if c[i] > ub[i] else -1) if d[i - 1] < 0 else (-1 if c[i] < lb[i] else 1)
    return d

def rsi(c, n=14):
    out = np.full(len(c), np.nan)
    if len(c) < n + 1: return out
    dlt = np.diff(c); up = np.clip(dlt, 0, None); dn = np.clip(-dlt, 0, None)
    au, ad = up[:n].mean(), dn[:n].mean()
    out[n] = 100 - 100 / (1 + au / ad) if ad else 100
    for i in range(n + 1, len(c)):
        au = (au * (n - 1) + up[i - 1]) / n; ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100 - 100 / (1 + au / ad) if ad else 100
    return out

rows = []
for sym, g, daily in load():
    if len(g) < 3000: continue
    b = bars15(g)
    h, l, c = b.h.to_numpy(float), b.l.to_numpy(float), b.c.to_numpy(float)
    b['st'] = supertrend(h, l, c); b['rsi'] = rsi(c)
    days = sorted(daily.index)
    prev = {}
    for i, dd in enumerate(days[1:], 1):
        H, L, C = daily.loc[days[i - 1], ['h', 'l', 'c']]
        P = (H + L + C) / 3
        prev[dd] = (P, 2 * P - L, P + (H - L))
    b['date'] = pd.to_datetime(b.day * 86400, unit='s').dt.date
    piv = b['date'].map(lambda d: prev.get(d, (np.nan, np.nan, np.nan)))
    b['P'] = [p[0] for p in piv]; b['R1'] = [p[1] for p in piv]; b['R2'] = [p[2] for p in piv]
    b = b.iloc[60:]                                   # indicator warm-up
    cond = {'A': b.st.to_numpy() == 1, 'B': b.rsi.to_numpy() > 70, 'C': b.c.to_numpy() > b.R1.to_numpy()}
    end_ok = ((b.end + IST) % 86400) <= 15 * 3600       # bar ends by 15:00 IST
    for name, letters in SETS.items():
        ok = np.logical_and.reduce([cond[x] for x in letters]) & end_ok.to_numpy() & b.R1.notna().to_numpy()
        fired = b[ok].groupby('day').head(1)
        for _, r in fired.iterrows():
            rows.append({'set': name, 'sym': sym, 'day': str(r.date), 'signal_end': int(r.end), 'spot': r.c,
                         'rsi': round(r.rsi, 1), 'R1': r.R1, 'R2': r.R2, 'P': r.P, 'st': int(r.st)})
    # supertrend direction series for exits: bar end times and direction
    np.save(OUT + f'st_{DS}_{sym.replace(":", "_").replace("&", "and")}.npy', np.column_stack([b.end.to_numpy(), b.st.to_numpy()]))
ev = pd.DataFrame(rows)
ev.to_csv(OUT + f'events_{DS}_{BAR}.csv', index=False)
print(DS, BAR, ev.groupby('set').size().to_dict(), 'days', ev['day'].nunique() if len(ev) else 0)
