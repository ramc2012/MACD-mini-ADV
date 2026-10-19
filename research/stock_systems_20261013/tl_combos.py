"""Trendline events + classic filters. Filters are SELECTED on 2022-2024 and TESTED on 2025 -> Oct 2026.

Events: the fixed detector (trendlines3.events_for, k=5, valid after a third touch) on daily bars of 210
F&O stocks. At the event bar t we record filter states; outcome = the signal-direction excess return
over the day's universe at t+1 and t+3 closes, and the raw-direction hit rate. A filter (or a pair) is
SELECTED if on 2022-24 it raised mean excess AND hit rate over the unfiltered event set in each of the
three years with >= 30 events a year. Selected filters are then reported on the untouched 2025-26.
"""
import itertools, json
import numpy as np, pandas as pd
exec(open('/w/trendlines3.py').read().split("fwd = {hz:")[0])        # events_for (fixed) + panel

nifty = frames['NSE:NIFTY50-INDEX'].c.reindex(C.index)
def rsi14(x): return rsi(x, 14)
R14, R2 = rsi14(C), rsi(C, 2)
sma200, sma50n = C.rolling(200).mean(), nifty.rolling(50).mean()
mom12 = C.shift(21) / C.shift(252) - 1
volr = V / V.shift().rolling(20).mean()
ibs = (C - L) / (H - L).replace(0, np.nan)
rs20 = (C / C.shift(20) - 1).sub(nifty / nifty.shift(20) - 1, axis=0)
trr = pd.concat([H - L, (H - C.shift()).abs(), (L - C.shift()).abs()]).groupby(level=0).max()
atr14 = trr.rolling(14).mean()
fwd = {hz: C.shift(-hz) / C - 1 for hz in (1, 3)}
mkt = {hz: f.mean(axis=1) for hz, f in fwd.items()}

rows = []
for sym in C.columns:
    s = pd.DataFrame({'o': O[sym], 'h': H[sym], 'l': L[sym], 'c': C[sym]}).dropna()
    if len(s) < 300: continue
    pos = {d: i for i, d in enumerate(s.index)}
    for t, kind, d in events_for(s.o.to_numpy(), s.h.to_numpy(), s.l.to_numpy(), s.c.to_numpy(), 5):
        day = s.index[t]
        if day < pd.Timestamp('2022-01-01'): continue
        g = lambda df: df.at[day, sym] if day in df.index else np.nan
        rec = {'sym': sym, 'day': day, 'kind': kind, 'dir': d}
        for hz in (1, 3):
            r = g(fwd[hz])
            rec[f'x{hz}'] = d * (r - mkt[hz].get(day, np.nan)); rec[f'r{hz}'] = d * r
        c0 = g(C); rec.update({
            'trend_up': c0 > g(sma200), 'mom12_up': g(mom12) > 0, 'rsi14': g(R14), 'rsi2': g(R2), 'volr': g(volr),
            'ibs': g(ibs), 'rs20_up': g(rs20) > 0, 'mkt_up': nifty.get(day, np.nan) > sma50n.get(day, np.nan),
            'atr_pct': g(atr14) / c0})
        rows.append(rec)
ev = pd.DataFrame(rows).dropna(subset=['x3'])
ev['year'] = ev.day.dt.year
# filters, each phrased "in the trade's favour" for its direction where that makes sense
F = {
    'with long trend (200dma)': lambda e: np.where(e.dir > 0, e.trend_up, ~e.trend_up),
    'against long trend (200dma)': lambda e: np.where(e.dir > 0, ~e.trend_up, e.trend_up),
    'with 12-1 momentum': lambda e: np.where(e.dir > 0, e.mom12_up, ~e.mom12_up),
    'with market (NIFTY vs 50dma)': lambda e: np.where(e.dir > 0, e.mkt_up, ~e.mkt_up),
    'against market': lambda e: np.where(e.dir > 0, ~e.mkt_up, e.mkt_up),
    'with relative strength 20d': lambda e: np.where(e.dir > 0, e.rs20_up, ~e.rs20_up),
    'against relative strength 20d': lambda e: np.where(e.dir > 0, ~e.rs20_up, e.rs20_up),
    'volume >= 1.5x': lambda e: e.volr >= 1.5,
    'volume < 1x (quiet)': lambda e: e.volr < 1.0,
    'strong close (IBS in favour)': lambda e: np.where(e.dir > 0, e.ibs >= 0.7, e.ibs <= 0.3),
    'weak close (IBS against)': lambda e: np.where(e.dir > 0, e.ibs < 0.5, e.ibs > 0.5),
    'RSI14 not stretched (<65 long / >35 short)': lambda e: np.where(e.dir > 0, e.rsi14 < 65, e.rsi14 > 35),
    'RSI14 stretched (>65 long / <35 short)': lambda e: np.where(e.dir > 0, e.rsi14 >= 65, e.rsi14 <= 35),
    'RSI2 pulled back (<50 long / >50 short)': lambda e: np.where(e.dir > 0, e.rsi2 < 50, e.rsi2 > 50),
    'low ATR% (bottom third)': lambda e: e.atr_pct <= e.atr_pct.quantile(0.33),
    'high ATR% (top third)': lambda e: e.atr_pct >= e.atr_pct.quantile(0.67),
}
masks = {n: pd.Series(f(ev), index=ev.index).astype(bool) for n, f in F.items()}
combos = dict(masks)
for a, b in itertools.combinations(F, 2):
    combos[f'{a} + {b}'] = masks[a] & masks[b]

def summary(sub):
    return {'n': len(sub), 'x1': sub.x1.mean() * 1e4, 'x3': sub.x3.mean() * 1e4, 'hit3': (sub.r3 > 0).mean()}
train, test = ev.year <= 2024, ev.year >= 2025
rows = []
for kind in ('breakout', 'breakdown', 'bounce_up', 'bounce_down'):
    K_ = ev.kind == kind
    base_tr = {y: summary(ev[K_ & (ev.year == y)]) for y in (2022, 2023, 2024)}
    base_te = summary(ev[K_ & test])
    for name, m in combos.items():
        ok = True
        for y in (2022, 2023, 2024):
            sub = ev[K_ & m & (ev.year == y)]
            if len(sub) < 30: ok = False; break
            s = summary(sub)
            if not (s['x3'] > base_tr[y]['x3'] and s['hit3'] > base_tr[y]['hit3']): ok = False; break
        if not ok: continue
        tr_, te_ = summary(ev[K_ & m & train]), summary(ev[K_ & m & test])
        rows.append({'event': kind, 'filter': name, 'train_n': tr_['n'], 'train_x3_bps': tr_['x3'], 'train_hit3': tr_['hit3'],
                     'test_n': te_['n'], 'test_x3_bps': te_['x3'], 'test_hit3': te_['hit3'],
                     'test_base_x3': base_te['x3'], 'test_base_hit3': base_te['hit3'],
                     'test_beats_base': te_['x3'] > base_te['x3'] and te_['hit3'] > base_te['hit3']})
sel = pd.DataFrame(rows)
sel.to_csv('/w/out/tl_combos_selected.csv', index=False); ev.to_csv('/w/out/tl_events_features.csv', index=False)
pd.set_option('display.width', 250); pd.set_option('display.max_colwidth', 70)
print('events', len(ev), ev.kind.value_counts().to_dict())
print('\nUnfiltered, 2025-26 (test): ' + '; '.join(f"{k}: n={len(g)} x3 {g.x3.mean()*1e4:+.1f}bps hit3 {(g.r3>0).mean():.3f}" for k, g in ev[test].groupby('kind')))
print(f'\nFilters selected on 2022-24 (beat the unfiltered events on excess AND hit rate in all 3 years): {len(sel)} of {len(combos)*4} tried')
if len(sel):
    print(sel.sort_values(['event', 'test_x3_bps'], ascending=[True, False]).round(3).to_string(index=False))
    print('\nShare of selected filters that ALSO beat the unfiltered events in 2025-26:', round(sel.test_beats_base.mean(), 2))
