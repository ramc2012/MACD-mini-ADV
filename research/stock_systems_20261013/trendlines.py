"""Algorithmic trendlines, no lookahead, on daily bars (210 F&O stocks + 4 indices, 2022 -> Oct 2026).

Swing high at bar i: high[i] is the max of high[i-k .. i+k]; it becomes KNOWN at bar i+k. Resistance =
line through the last two known swing highs when the second is LOWER (a falling trendline); support =
line through the last two known swing lows when the second is HIGHER (rising). A line is live until a
close crosses it after its second anchor. Events at the close of day t (stand-in for our 14:45):
  breakout   : first close above live falling resistance by > 0.1 ATR          -> bullish (CE)
  breakdown  : first close below live rising support by > 0.1 ATR              -> bearish (PE)
  bounce_up  : low within 0.25 ATR of live rising support, close above it, close > open   -> bullish
  bounce_down: high within 0.25 ATR of live falling resistance, close below it, close < open -> bearish
Measured: next 1/3/5-day raw return in the signal's direction, its hit rate, excess over the day's
universe, against the base rate of all stock-days; t-stats clustered by day; per-year signs.
"""
import json
import numpy as np, pandas as pd

exec(open('/w/systems.py').read().split("def daily_ic")[0])      # C, H, L, O, frames, INDEX
IDXS = [s for s in frames if s in INDEX]

def events_for(o, h, l, c, k):
    n = len(c)
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    sh, sl = [], []                       # known swings: (index, price)
    res = sup = None                      # (i1, p1, i2, p2)
    out = []
    for t in range(2 * k + 1, n):
        i = t - k                         # the bar whose swing status becomes known now
        win_h, win_l = h[i - k:i + k + 1], l[i - k:i + k + 1]
        if h[i] == win_h.max(): sh.append((i, h[i]))
        if l[i] == win_l.min(): sl.append((i, l[i]))
        if len(sh) >= 2 and sh[-1][1] < sh[-2][1] and (res is None or res[2] != sh[-1][0]):
            res = (sh[-2][0], sh[-2][1], sh[-1][0], sh[-1][1])
        if len(sl) >= 2 and sl[-1][1] > sl[-2][1] and (sup is None or sup[2] != sl[-1][0]):
            sup = (sl[-2][0], sl[-2][1], sl[-1][0], sl[-1][1])
        a = atr[t]
        if not np.isfinite(a) or a <= 0: continue
        for kind, line in (('res', res), ('sup', sup)):
            if line is None: continue
            i1, p1, i2, p2 = line
            slope = (p2 - p1) / (i2 - i1)
            val = lambda j: p2 + slope * (j - i2)
            if kind == 'res':
                if c[t] > val(t) + 0.1 * a and c[t - 1] <= val(t - 1):
                    out.append((t, 'breakout', 1)); res = None
                elif c[t] > val(t): res = None                          # broken without a clean first cross
                elif h[t] >= val(t) - 0.25 * a and c[t] < val(t) and c[t] < o[t]:
                    out.append((t, 'bounce_down', -1))
            else:
                if c[t] < val(t) - 0.1 * a and c[t - 1] >= val(t - 1):
                    out.append((t, 'breakdown', -1)); sup = None
                elif c[t] < val(t): sup = None
                elif l[t] <= val(t) + 0.25 * a and c[t] > val(t) and c[t] > o[t]:
                    out.append((t, 'bounce_up', 1))
    return out

fwd = {hz: C.shift(-hz) / C - 1 for hz in (1, 3, 5)}
mean_fwd = {hz: f.mean(axis=1) for hz, f in fwd.items()}
rows = []
for k in (3, 5, 10):
    for sym in list(C.columns) + IDXS:
        df = frames[sym] if sym in IDXS else None
        o_, h_, l_, c_ = ((df.o, df.h, df.l, df.c) if df is not None else (O[sym], H[sym], L[sym], C[sym]))
        s = pd.DataFrame({'o': o_, 'h': h_, 'l': l_, 'c': c_}).dropna()
        if len(s) < 300: continue
        for t, kind, d in events_for(s.o.to_numpy(), s.h.to_numpy(), s.l.to_numpy(), s.c.to_numpy(), k):
            day = s.index[t]
            if day < pd.Timestamp('2022-01-01'): continue
            rec = {'k': k, 'sym': sym, 'is_index': sym in IDXS, 'day': day, 'kind': kind, 'dir': d}
            cc = s.c.to_numpy()
            for hz in (1, 3, 5):
                if t + hz < len(cc):
                    r = cc[t + hz] / cc[t] - 1
                    rec[f'r{hz}'] = d * r
                    rec[f'x{hz}'] = d * (r - mean_fwd[hz].get(day, np.nan)) if sym not in IDXS else np.nan
            rows.append(rec)
ev = pd.DataFrame(rows); ev.to_csv('/w/out/trendline_events.csv', index=False)
# base: every stock-day, direction-free (|return| sign 50/50 by construction is not the base; use long and short separately)
base = {hz: {1: (fwd[hz].loc['2022':] > 0).stack().mean(), -1: (fwd[hz].loc['2022':] < 0).stack().mean()} for hz in (1, 3, 5)}
pd.set_option('display.width', 220)
print('Base rates 2022+ (all stock-days): up next day %.3f, down %.3f; up over 3 days %.3f' % (base[1][1], base[1][-1], base[3][1]))
def tstat(x, days):
    per = pd.Series(x.values, index=days.values).groupby(level=0).mean()
    return per.mean() / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 2 else np.nan
out = []
for (k, isidx, kind), g in ev.groupby(['k', 'is_index', 'kind']):
    d = g.dir.iloc[0]
    row = {'k': k, 'universe': 'index' if isidx else 'stocks', 'event': kind, 'n': len(g)}
    for hz in (1, 3, 5):
        r = g[f'r{hz}'].dropna()
        row[f'hit{hz}'] = (r > 0).mean(); row[f'hit{hz}_vs_base'] = (r > 0).mean() - base[hz][d]
        row[f'ret{hz}_bps'] = r.mean() * 1e4
        if not isidx:
            x = g[f'x{hz}'].dropna(); row[f'excess{hz}_bps'] = x.mean() * 1e4; row[f't_excess{hz}'] = tstat(x, g.loc[x.index, 'day'])
    yr = g.groupby(g.day.dt.year)['x3' if not isidx else 'r3'].mean()
    row['years_positive_h3'] = f"{int((yr > 0).sum())}/{len(yr)}"
    out.append(row)
res = pd.DataFrame(out); res.to_csv('/w/out/trendline_results.csv', index=False)
print(res[['k', 'universe', 'event', 'n', 'hit1', 'hit1_vs_base', 'hit3', 'hit3_vs_base', 'ret1_bps', 'ret3_bps', 'ret5_bps', 'excess1_bps', 't_excess1', 'excess3_bps', 't_excess3', 'years_positive_h3']].round(3).to_string(index=False))
