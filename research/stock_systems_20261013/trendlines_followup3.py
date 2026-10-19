"""(1) Stock trendline events vs real option outcomes (24 Aug - 7 Oct 2026).
(2) Index trendlines on 30-minute bars (Fyers 1-min, 2021 -> Oct 2026), state at the 14:45 decision:
    did the last 10 sessions' 30-min bars include a live trendline event, and how did the index lane's
    plan (stop 1 ATR / target 1.5 ATR / 3 sessions) do on that side, vs that side's base rate?"""
import json
import numpy as np, pandas as pd
exec(open('/w/trendlines3.py').read().split("fwd = {hz:")[0])         # events_for + panel

# ---- (1) stocks on options
ev = pd.read_csv('/w/out/trendline_events_3touch.csv', parse_dates=['day'])
ev = ev[(~ev.is_index) & (ev.k == 5)]
cand = pd.read_csv('/w/data/cands.csv'); cand['date'] = pd.to_datetime(cand.day); cand['sym'] = 'NSE:' + cand.underlying + '-EQ'
dates = sorted(C.index)
nxt = {d: dates[i + 1] for i, d in enumerate(dates[:-1])}
ev['trade_date'] = ev.day.map(nxt)                                   # event at close t -> act on day t+1 at 14:45
print('### (1) Stock trendline events (k=5) acted on next day at 14:45, real options')
for kind, g in ev.groupby('kind'):
    side = int(g.dir.iloc[0])
    x = cand[cand.side == side].merge(g[['sym', 'trade_date']], left_on=['sym', 'date'], right_on=['sym', 'trade_date'])
    if x.empty: print(f'  {kind}: no matches'); continue
    pool = cand[cand.side == side].groupby('day').y.mean()
    ex = x.groupby('day').y.mean() - pool.reindex(x.groupby('day').y.mean().index)
    print(f"  {kind:12s} side {'CE' if side > 0 else 'PE'}: {x.underlying.nunique()} names, {len(x)} contracts on {ex.size} days | mean y {x.y.mean()*100:+.1f}% vs pool {pool.mean()*100:+.1f}% | excess {ex.mean()*100:+.1f} pts (t {ex.mean()/(ex.std()/np.sqrt(len(ex))):+.1f})")

# ---- (2) indices on 30-min bars
idx = json.load(open('/r/dd_nifty_4ind_20261009/data/idx1m.json')); extra = json.load(open('/r/index_model_20261012/data/idx_extra.json'))
days_tbl = pd.read_csv('/r/index_model_20261012/out/index_days.csv')
SY = {'NIFTY': 'NSE:NIFTY50-INDEX', 'BANKNIFTY': 'NSE:NIFTYBANK-INDEX', 'SENSEX': 'BSE:SENSEX-INDEX', 'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX'}
rows = []
for und, sym in SY.items():
    m = pd.DataFrame(list(idx.get(sym, [])) + list(extra.get(sym, [])), columns=['ts', 'o', 'h', 'l', 'c', 'v']).drop_duplicates('ts').sort_values('ts')
    clock = (m.ts + 19800) % 86400
    m = m[(clock >= 33300) & (clock < 55800)].copy()
    m['day'] = pd.to_datetime((m.ts + 19800) // 86400, unit='D').dt.date.astype(str)
    m['bucket'] = (clock.loc[m.index] - 33300) // 1800
    b = m.groupby(['day', 'bucket']).agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), end=('ts', 'max')).reset_index()
    b['end_clock'] = (b.end + 60 + 19800) % 86400
    for k in (3, 5, 8):
        evs = events_for(b.o.to_numpy(), b.h.to_numpy(), b.l.to_numpy(), b.c.to_numpy(), k)
        e = pd.DataFrame(evs, columns=['t', 'kind', 'dir'])
        if e.empty: continue
        e['day'] = b.day.to_numpy()[e.t]; e['end_clock'] = b.end_clock.to_numpy()[e.t]
        e = e[e.end_clock <= 14 * 3600 + 45 * 60]                     # known by the 14:45 decision
        last = e.groupby('day').tail(1)                                # the latest event of the session before 14:45
        for r in last.itertuples():
            rows.append({'und': und, 'k': k, 'day': r.day, 'kind': r.kind, 'dir': r.dir})
E = pd.DataFrame(rows)
T = days_tbl[['und', 'day', 'ce_path', 'pe_path', 'ce_ret', 'pe_ret', 'atr', 'spot']].copy()
for tag in ('ce', 'pe'):
    T[f'{tag}_R'] = np.where(T[f'{tag}_path'] == 'target', 1.5, np.where(T[f'{tag}_path'] == 'stop', -1.0, T[f'{tag}_ret'] / (T.atr / T.spot)))
T = T[T.day >= '2022-01-01']
base = {1: (T.ce_R > 0).mean(), -1: (T.pe_R > 0).mean()}
J = E.merge(T, on=['und', 'day'])
J['R'] = np.where(J.dir > 0, J.ce_R, J.pe_R); J['year'] = J.day.str[:4]
print('\n### (2) Index 30-min trendline event earlier the same session (by 14:45) -> the lane plan on that side')
print('    base win: CE %.3f PE %.3f over %d index-days' % (base[1], base[-1], len(T)))
for (k, kind), g in J.groupby(['k', 'kind']):
    d = g.dir.iloc[0]
    yr = g.groupby('year').R.apply(lambda z: (z > 0).mean() - base[d])
    print(f"  k={k} {kind:12s} n={len(g):4d} win={(g.R>0).mean():.3f} ({(g.R>0).mean()-base[d]:+.3f} vs {'CE' if d>0 else 'PE'} base) meanR={g.R.mean():+.3f} | lift>0 in {int((yr>0).sum())}/{len(yr)} yrs")
