"""Real option premiums for the spot trades, from the TradeBot 1-min chains (NIFTY Apr-Aug 2025,
SENSEX Apr 2025-Mar 2026). Strike rule from the video: the CE of the nearest expiry (incl. expiry day)
whose premium at the entry minute is closest to 1% of spot. Entry = that minute's open, exit = the
exit minute's open (else the last close before it). Also the PE picked the same way, at the same times.
Usage: opt_tb.py <minutes>
"""
import sys, re, glob
import numpy as np, pandas as pd

TF = int(sys.argv[1]); W = '/w/'; IST = 19800; PFX = sys.argv[2] if len(sys.argv) > 2 else ''
t = pd.read_csv(W + f'out/{PFX}spot_trades_{TF}m.csv'); r = pd.read_csv(W + f'out/{PFX}spot_random_{TF}m.csv')
r = r[r.draw < 10]
trades = pd.concat([t, r.assign(set=lambda d: 'RANDOM_' + d.draw.astype(str))], ignore_index=True)
cache = {}
def load(f):
    if f not in cache:
        g = pd.read_csv(f); g['ts'] = (pd.to_datetime(g['time']).astype('int64') // 10**9).astype('int64')
        cache[f] = (g.ts.to_numpy(), g.open.to_numpy(float), g.close.to_numpy(float), g.high.to_numpy(float), g.low.to_numpy(float))
    return cache[f]
rows = []
for und in ('NIFTY', 'SENSEX'):
    cat = []
    for f in glob.glob(f'/tb/contracts/underlying={und}/expiry_kind=*/expiry=*/*/*.csv.gz'):
        m = re.search(r'expiry=(\d{4}-\d{2}-\d{2})/(CE|PE)/.* (\d+(?:\.\d+)?) (CE|PE) ', f)
        if m: cat.append((m.group(1), m.group(2), float(m.group(3)), f))
    cat = pd.DataFrame(cat, columns=['expiry', 'right', 'strike', 'file'])
    lo, hi = cat.expiry.min(), cat.expiry.max()
    sub = trades[trades.sym == und]
    day = pd.to_datetime(sub.entry_ts + IST, unit='s').dt.strftime('%Y-%m-%d')
    sub = sub[(day >= lo) & (day <= hi)].assign(dstr=day)
    for x in sub.itertuples():
        target = 0.01 * x.entry
        rec = {'sym': und, 'set': x.set, 'day': x.dstr, 'entry_ts': x.entry_ts, 'exit_ts': x.exit_ts, 'pts': x.pts, 'mins': x.mins, 'spot': x.entry}
        ok = True
        for right in ('CE', 'PE'):
            c = cat[(cat.right == right) & (cat.expiry >= x.dstr)]
            if c.empty: ok = False; break
            c = c[c.expiry == c.expiry.min()]
            # strikes within 6% of spot, take the premium nearest 1% of spot at the entry minute
            c = c[(c.strike - x.entry).abs() <= 0.06 * x.entry]
            best = None
            for y in c.itertuples():
                ts, op, cl, hh, ll = load(y.file)
                j = np.searchsorted(ts, x.entry_ts)
                if j >= len(ts) or ts[j] - x.entry_ts > 120 or op[j] <= 0: continue
                if best is None or abs(op[j] - target) < abs(best[1] - target): best = (y, op[j], j)
            if best is None: ok = False; break
            y, epx, j = best
            ts, op, cl, hh, ll = load(y.file)
            k = np.searchsorted(ts, x.exit_ts)
            if k < len(ts) and ts[k] - x.exit_ts <= 120: xpx = op[k]
            else:
                k = np.searchsorted(ts, x.exit_ts, side='right') - 1; xpx = cl[k]
            rec.update({f'{right}_strike': y.strike, f'{right}_expiry': y.expiry, f'{right}_entry': epx, f'{right}_exit': xpx,
                        f'{right}_mfe': hh[j:k + 1].max() / epx - 1 if k >= j else 0})
        if ok: rows.append(rec)
out = pd.DataFrame(rows); out.to_csv(W + f'out/{PFX}opt_tb_{TF}m.csv', index=False)
print(TF, out.groupby(['sym', 'set']).size().to_dict())
