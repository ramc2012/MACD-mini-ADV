"""Step 2a: spot forward returns after each signal, plus controls.

Entry: open of the first 1-min bar starting at/after the signal bar's end.
Horizons: +15m, +30m, +60m, same-day 15:15, D+1/D+2/D+3 15:15 closes, and a Supertrend exit
(first 15-min bar closing with Supertrend down after entry, capped at D+3 15:15).
Controls (live): same day + same entry minute, every other underlying with a bar (market-matched).
Controls (index): same index, same entry clock time, every other day (time-matched).
"""
import sys, json
import numpy as np, pandas as pd

DS = sys.argv[1]
W = '/w/'
IST = 19800
ev = pd.read_csv(W + f'out/events_{DS}_15.csv')

if DS == 'live':
    m = pd.read_csv(W + 'data/spot_min.csv')[['symbol', 'ts', 'open', 'close']]
    by = {s: g.sort_values('ts') for s, g in m.groupby('symbol')}
else:
    g = pd.read_csv(f'/tb/spot/underlying={DS.upper()}/1minute.csv.gz')
    g['ts'] = (pd.to_datetime(g['time']).astype('int64') // 10**9).astype('int64')
    by = {DS.upper(): g[['ts', 'open', 'close']].sort_values('ts').drop_duplicates('ts')}

arr = {s: (g.ts.to_numpy(), g.open.to_numpy(float), g.close.to_numpy(float)) for s, g in by.items()}
def sessions(ts):
    d = np.unique((ts + IST) // 86400)
    return d
def st_series(sym):
    try:
        a = np.load(W + f'out/st_{DS}_{sym.replace(":", "_").replace("&", "and")}.npy')
        return a[:, 0].astype(np.int64), a[:, 1].astype(int)
    except FileNotFoundError:
        return None

HZ = ['m15', 'm30', 'm60', 'close', 'd1', 'd2', 'd3', 'st_exit']
def path(sym, signal_end):
    ts, op, cl = arr[sym]
    i = np.searchsorted(ts, signal_end)
    if i >= len(ts) or ts[i] - signal_end > 300: return None
    entry_ts, entry = ts[i], op[i]
    day = (entry_ts + IST) // 86400
    days = sessions(ts); k = np.searchsorted(days, day)
    def close_at(t):
        j = np.searchsorted(ts, t, side='right') - 1
        return cl[j] if j >= i else np.nan
    def day_close(off):
        if k + off >= len(days): return np.nan, None
        t = days[k + off] * 86400 - IST + 15 * 3600 + 15 * 60     # 15:15 IST
        j = np.searchsorted(ts, t, side='right') - 1
        if j < 0 or (ts[j] + IST) // 86400 != days[k + off]: return np.nan, None
        return cl[j], t
    out = {'entry_ts': int(entry_ts), 'entry': entry}
    for name, dt in (('m15', 900), ('m30', 1800), ('m60', 3600)):
        out[name] = close_at(entry_ts + dt) / entry - 1
    for off, name in ((0, 'close'), (1, 'd1'), (2, 'd2'), (3, 'd3')):
        c, _ = day_close(off); out[name] = c / entry - 1
    cap = day_close(3)[1] or day_close(2)[1]
    st = st_series(sym); out['st_exit'] = np.nan; out['st_exit_ts'] = None
    if st is not None and cap:
        e, d = st
        j = np.searchsorted(e, entry_ts, side='right')
        flips = np.nonzero(d[j:] == -1)[0]
        t_exit = e[j + flips[0]] if len(flips) and e[j + flips[0]] <= cap else cap
        out['st_exit'] = close_at(t_exit) / entry - 1; out['st_exit_ts'] = int(t_exit)
    return out

rows = []
for r in ev.itertuples():
    p = path(r.sym, r.signal_end)
    if p: rows.append({'set': r.set, 'sym': r.sym, 'day': r.day, 'rsi': r.rsi, **p})
res = pd.DataFrame(rows)

# controls
ctrl_rows = []
abc = res[res.set == 'ST+RSI70+R1']
if DS == 'live':
    firing = abc.groupby("entry_ts").sym.apply(set).to_dict()
    for r in abc.drop_duplicates("entry_ts").itertuples():
        for s in arr:
            if s in firing[r.entry_ts]: continue
            p = path(s, r.entry_ts - 1)
            if p and p["entry_ts"] == r.entry_ts:
                ctrl_rows.append({"event_sym": r.sym, 'event_ts': r.entry_ts, 'sym': s, 'day': r.day, **p})
else:
    sym = DS.upper(); ts = arr[sym][0]
    days = sessions(ts)
    for r in abc.itertuples():
        clock = (r.entry_ts + IST) % 86400
        for d in days:
            t = d * 86400 - IST + clock
            if (t + IST) // 86400 == (r.entry_ts + IST) // 86400: continue
            p = path(sym, t - 1)
            if p and p['entry_ts'] == t:
                ctrl_rows.append({'event_sym': sym, 'event_ts': r.entry_ts, 'sym': sym, 'day': str(pd.to_datetime(d * 86400, unit='s').date()), **p})
ctrl = pd.DataFrame(ctrl_rows)
res.to_csv(W + f'out/spot_{DS}.csv', index=False); ctrl.to_csv(W + f'out/spot_ctrl_{DS}.csv', index=False)

def clustered(x, groups):
    """Mean, and t-stat with the day as the unit (many names fire on one market day)."""
    df = pd.DataFrame({'x': x, 'g': groups}).dropna()
    if df.empty: return np.nan, np.nan, 0
    per = df.groupby('g').x.mean()
    t = per.mean() / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 2 and per.std(ddof=1) > 0 else np.nan
    return df.x.mean(), t, len(per)

print(f'##### {DS}: spot return after entry (bps), mean | win% | t over days')
for s in ['ST', 'R1', 'RSI70', 'ST+R1', 'ST+RSI70+R1']:
    sub = res[res.set == s]
    line = f'{s:13s} n={len(sub):5d} '
    for h in HZ:
        mu, t, nd = clustered(sub[h].to_numpy() * 1e4, sub['day'].to_numpy())
        line += f'| {h} {mu:+6.1f} {np.mean(sub[h].dropna() > 0)*100:3.0f}% t{t:+.1f} '
    print(line)
if len(ctrl):
    line = f'{"CONTROL":13s} n={len(ctrl):5d} '
    for h in HZ:
        mu, t, nd = clustered(ctrl[h].to_numpy() * 1e4, ctrl['day'].to_numpy())
        line += f'| {h} {mu:+6.1f} {np.mean(ctrl[h].dropna() > 0)*100:3.0f}% t{t:+.1f} '
    print(line)
    # excess over the matched control
    cm = ctrl.groupby('event_ts')[HZ].mean() if DS == 'live' else None
    if DS == 'live':
        j = abc.set_index('entry_ts')[HZ + ['day']].join(cm, rsuffix='_c', how='inner')
        line = f'{"EXCESS vs mkt":13s} n={len(j):5d} '
        for h in HZ:
            mu, t, nd = clustered((j[h] - j[h + '_c']).to_numpy() * 1e4, j['day'].to_numpy())
            line += f'| {h} {mu:+6.1f} {np.mean((j[h]-j[h+"_c"]).dropna() > 0)*100:3.0f}% t{t:+.1f} '
        print(line)
