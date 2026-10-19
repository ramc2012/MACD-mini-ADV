"""Book or hold? Real index option premiums at the moment the index first reaches k x ATR from the previous close.

Events from spot_events.py (A). For each event: the ATM option ON the move's side (CE for an up move, PE
for a down move) and the opposite one, from the nearest expiry that has at least one more session
(expiry-day decay excluded), priced at the event minute. Return of HOLDING from the event to +30m, +60m,
15:25 the same day, D+1 15:25 and D+3 15:25. Negative = booking at the extreme beat holding.
Sources: TradeBot 1-min chains (NIFTY Apr-Aug 2025, SENSEX Apr 2025-Feb 2026).
"""
import re, glob
import numpy as np, pandas as pd
ev = pd.read_csv('/w/out/intraday_extremes.csv')
ev['date'] = pd.to_datetime(ev.day, unit='D').dt.date.astype(str)
spot = {u: None for u in ('NIFTY', 'SENSEX')}
rows = []
for und in ('NIFTY', 'SENSEX'):
    cat = []
    for f in glob.glob(f'/tb/contracts/underlying={und}/expiry_kind=*/expiry=*/*/*.csv.gz'):
        m = re.search(r'expiry=(\d{4}-\d{2}-\d{2})/(CE|PE)/.* (\d+(?:\.\d+)?) (CE|PE) ', f)
        if m: cat.append((m.group(1), m.group(2), float(m.group(3)), f))
    cat = pd.DataFrame(cat, columns=['expiry', 'right', 'strike', 'file'])
    lo, hi = cat.expiry.min(), cat.expiry.max()
    s = pd.read_csv(f'/tb/spot/underlying={und}/1minute.csv.gz'); s['ts'] = (pd.to_datetime(s['time']).astype('int64') // 10**9)
    s['date'] = s.time.str[:10]; sessions = sorted(s.date.unique())
    cache = {}
    def load(f):
        if f not in cache:
            g = pd.read_csv(f); g['ts'] = (pd.to_datetime(g['time']).astype('int64') // 10**9)
            cache[f] = g.set_index('ts').close
        return cache[f]
    e = ev[(ev.und == und) & (ev.date >= lo) & (ev.date <= hi)]
    for r in e.itertuples():
        day = r.date
        if day not in sessions: continue
        i = sessions.index(day)
        if i + 3 >= len(sessions): continue
        t0 = int(pd.Timestamp(f'{day} 09:15', tz='Asia/Kolkata').timestamp()) + r.minute * 60
        sp = s[(s.ts >= t0) & (s.ts < t0 + 120)]
        if sp.empty: continue
        S = sp.close.iloc[0]
        marks = {'m30': t0 + 1800, 'm60': t0 + 3600}
        for k_, dd in (('close', day), ('d1', sessions[i + 1]), ('d3', sessions[i + 3])):
            marks[k_] = int(pd.Timestamp(f'{dd} 15:25', tz='Asia/Kolkata').timestamp())
        rec = {'und': und, 'day': day, 'k': r.k, 'dir': r.dir, 'minute': r.minute}
        for leg, right in (('with', 'CE' if r.dir > 0 else 'PE'), ('against', 'PE' if r.dir > 0 else 'CE')):
            c = cat[(cat.right == right) & (cat.expiry > day)]
            if c.empty: break
            c = c[c.expiry == c.expiry.min()]
            c = c.iloc[(c.strike - S).abs().argsort()[:1]]
            px = load(c.file.iloc[0])
            p0 = px[(px.index >= t0) & (px.index < t0 + 180)]
            if p0.empty or p0.iloc[0] <= 0: break
            p0 = p0.iloc[0]
            for name, t in marks.items():
                q = px[px.index <= t]
                rec[f'{leg}_{name}'] = (q.iloc[-1] / p0 - 1) if len(q) and q.index[-1] > t0 else np.nan
            rec[f'{leg}_premium'] = p0; rec[f'{leg}_dte'] = (pd.Timestamp(c.expiry.iloc[0]) - pd.Timestamp(day)).days
        rows.append(rec)
res = pd.DataFrame(rows); res.to_csv('/w/out/option_events.csv', index=False)
pd.set_option('display.width', 250)
print('events priced:', len(res), res.groupby(['und', 'k']).size().to_dict())
H = ['m30', 'm60', 'close', 'd1', 'd3']
for leg in ('with', 'against'):
    print(f"\n### Holding the option {'ON' if leg == 'with' else 'AGAINST'} the move's side after first touch of k x ATR (mean / median % change of premium; share that gained)")
    out = []
    for (k, d), g in res.groupby(['k', 'dir']):
        rec = {'k': k, 'move': 'up' if d > 0 else 'down', 'n': len(g)}
        for h in H:
            v = g[f'{leg}_{h}'].dropna()
            rec[h] = f"{v.mean()*100:+.1f}/{v.median()*100:+.1f} ({(v>0).mean()*100:.0f}%)" if len(v) else '-'
        out.append(rec)
    print(pd.DataFrame(out).to_string(index=False))
