"""Blast exit variants on the replayed screen passes, paired on the same entries.

Fill: entry at the open of the first minute after the signal bar; a stop fills at its level, or at the bar's open
when the bar gaps through it; the peak is updated AFTER the stop check within a bar (conservative).
Round-trip cost 0.2% of entry. Positions end at the stop, the trail, or expiry day 15:10 (the lane's expiry sweep);
a position still open on 14 Oct is marked at its last close and flagged censored.
One position per contract at a time, as the lane never pyramids (holding windows from the current rule).
"""
import os
import numpy as np, pandas as pd

COST = 0.002
CONS = os.environ.get("FILL") == "cons"
SUFFIX = "_cons" if CONS else ""
print("fill model:", "conservative" if CONS else "optimistic")
x = pd.read_csv('/w/out/crosses.csv.gz')
z = np.load('/w/data/paths.npz')
row, ts, op, hi, lo, cl = z['row'], z['ts'], z['open'], z['high'], z['low'], z['close']
starts = np.searchsorted(row, np.arange(len(x)), 'left'); ends = np.searchsorted(row, np.arange(len(x)), 'right')
assert (np.diff(row) >= 0).all()

# per-contract 30-minute range before entry, for the volatility-scaled trail
o30 = pd.read_csv('/w/data/opt30.csv.gz', usecols=['symbol', 'bucket', 'high', 'low', 'close'])
o30['rng'] = (o30.high - o30.low) / o30.close
o30 = o30.drop_duplicates(['symbol', 'bucket']).sort_values(['symbol', 'bucket'])
o30['atr14'] = o30.groupby('symbol').rng.transform(lambda s: s.rolling(14, min_periods=5).mean())
x = x.join(o30.set_index(['symbol', 'bucket']).atr14, on=['symbol', 'bucket'])


def simulate(i, hard=0.5, act=0.3, trail=0.4, ratchet=None, tstop=None, tthr=0.10, vol_k=None, cons=False):
    """cons: peak from closes, and a breached stop fills at the worse of its level and the bar close."""
    a, b = starts[i], ends[i]
    if b - a < 1:
        return None
    entry = op[a]
    if not entry > 0:
        return None
    be = entry * (1 + COST)
    trail_pct = trail if vol_k is None else float(np.clip(vol_k * (x.at[i, 'atr14'] or 0.15), 0.25, 0.55))
    stop = entry * (1 - hard); peak = entry; floor = 0.0; armed = False
    for j in range(a, b):
        level = max(stop, floor)
        if op[j] <= level:
            return op[j], j, 'stop_gap' if level == stop else 'trail_gap', peak
        if lo[j] <= level:
            return (min(level, cl[j]) if cons else level), j, 'hard' if level == stop else ('trail' if armed else 'ratchet'), peak
        if tstop is not None and j - a + 1 >= tstop * 30 and peak < entry * (1 + tthr):
            return cl[j], j, 'time', max(peak, cl[j] if cons else hi[j])
        peak = max(peak, cl[j] if cons else hi[j])
        if ratchet is not None and peak >= entry * (1 + ratchet):
            floor = max(floor, be)
        if peak >= entry * (1 + act):
            armed = True
            floor = max(floor, peak * (1 - trail_pct), be)
    return cl[b - 1], b - 1, 'end', peak


p = x[x.verdict == 'PASS'].sort_values('signal_end')
held_until = {}; keep = []
for i, r in p.iterrows():
    if held_until.get(r.symbol, -1) >= r.signal_end:
        continue
    res = simulate(i, cons=CONS)
    if res is None:
        continue
    held_until[r.symbol] = ts[res[1]]
    keep.append(i)
print('PASS crosses', len(p), '-> entries after one-position-per-contract', len(keep))

VARIANTS = {
    'current (-50%, trail 40% from +30%, BE floor)': {},
    'ratchet to BE at +22%': {'ratchet': 0.22},
    'ratchet to BE at +15%': {'ratchet': 0.15},
    'time stop 4 bars if never +10%': {'tstop': 4},
    'time stop 8 bars if never +10%': {'tstop': 8},
    'time stop 12 bars if never +10%': {'tstop': 12},
    'ratchet +22% + time stop 8': {'ratchet': 0.22, 'tstop': 8},
    'vol-scaled trail (3x 30m range, 25-55%)': {'vol_k': 3.0},
    'tighter stop -35%': {'hard': 0.35},
}
res = {}
last_ts = ts.max()
for name, kw in VARIANTS.items():
    out = []
    for i in keep:
        price, j, why, peak = simulate(i, cons=CONS, **kw)
        entry = op[starts[i]]
        out.append({'i': i, 'ret': price / entry - 1 - COST, 'why': why, 'peak': peak / entry - 1,
                    'censored': why == 'end' and ts[j] >= last_ts - 86400, 'held_min': j - starts[i] + 1})
    res[name] = pd.DataFrame(out).set_index('i')

base = res['current (-50%, trail 40% from +30%, BE floor)']
e = x.loc[keep]
day = pd.to_datetime(e.signal_end, unit='s', utc=True).dt.tz_convert('Asia/Kolkata').dt.date
rows = []
for name, d in res.items():
    r = d.ret
    wins, losses = r[r > 0], r[r <= 0]
    diff = (r - base.ret).groupby(day).mean()
    t = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))) if diff.std(ddof=1) > 0 else np.nan
    top = r.sort_values(ascending=False)
    rows.append({'variant': name, 'n': len(r), 'mean_%': r.mean() * 100, 'median_%': r.median() * 100,
                 'win_%': (r > 0).mean() * 100, 'payoff': wins.mean() / -losses.mean() if len(losses) and losses.mean() < 0 else np.nan,
                 'top5_share_%': top.head(5).sum() / r[r > 0].sum() * 100 if (r > 0).any() else np.nan,
                 'vs_current_pp': (r - base.ret).mean() * 100, 'day_t': t,
                 'days_better': int((diff > 1e-9).sum()), 'days_worse': int((diff < -1e-9).sum())})
tab = pd.DataFrame(rows)
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 20)
print(tab.round(2).to_string(index=False))
tab.to_csv(f"/w/out/exit_variants{SUFFIX}.csv", index=False)

b = base.join(e[['symbol', 'right', 'root', 'premium_pct', 'dte_cal']])
b['peak_band'] = pd.cut(b.peak, [-1, 0.10, 0.22, 0.30, 0.6, 1.0, 100], labels=['<10%', '10-22%', '22-30%', '30-60%', '60-100%', '>100%'])
print('\ncurrent rule, by peak reached:')
print(b.groupby('peak_band', observed=True).agg(n=('ret', 'size'), mean_ret=('ret', 'mean'), sum_ret=('ret', 'sum')).round(3).to_string())
print('\nexit reasons (current):', base.why.value_counts().to_dict(), ' censored:', int(base.censored.sum()))
print('held minutes median', base.held_min.median())
for name in ('ratchet to BE at +22%', 'time stop 8 bars if never +10%'):
    d = res[name]
    ch = (d.ret - base.ret)
    print(f'\n{name}: changed {int((ch.abs() > 1e-9).sum())} trades; helped {int((ch > 1e-9).sum())} (+{ch[ch > 0].sum()*100:.0f} pp), '
          f'hurt {int((ch < -1e-9).sum())} ({ch[ch < 0].sum()*100:.0f} pp)')
pd.concat({k: v for k, v in res.items()}, names=['variant']).to_csv(f"/w/out/exit_trades{SUFFIX}.csv.gz")
e.assign(day=day.values).to_csv(f"/w/out/entries{SUFFIX}.csv.gz")
