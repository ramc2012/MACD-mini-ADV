import numpy as np, pandas as pd
ev = pd.read_csv('/w/out/tl_events_features.csv', parse_dates=['day'])
cand = pd.read_csv('/w/data/cands.csv'); cand['date'] = pd.to_datetime(cand.day); cand['sym'] = 'NSE:' + cand.underlying + '-EQ'
dates = sorted(pd.to_datetime(pd.read_csv('/w/out/tl_events_features.csv', usecols=['day']).day.unique()))
allc = sorted(set(cand.date))
nxt = lambda d: next((x for x in allc if x > d), None)
rules = {
  'PE: rejected at falling line + lagging NIFTY 20d + volume>=1.5x': (ev.kind == 'bounce_down') & (ev.rs20_up == False) & (ev.volr >= 1.5),
  'PE: rejected at falling line (any)': ev.kind == 'bounce_down',
  'CE: bounce on rising line + weak close + low ATR%': (ev.kind == 'bounce_up') & (ev.ibs < 0.5) & (ev.atr_pct <= ev.atr_pct.quantile(0.33)),
  'CE: breakout + 12-1 momentum up + lagging NIFTY 20d': (ev.kind == 'breakout') & (ev.mom12_up == True) & (ev.rs20_up == False),
}
for name, m in rules.items():
    side = -1 if name.startswith('PE') else 1
    e = ev[m & (ev.day >= '2026-08-20')].copy(); e['trade_date'] = e.day.map(nxt)
    x = cand[cand.side == side].merge(e[['sym', 'trade_date']], left_on=['sym', 'date'], right_on=['sym', 'trade_date'])
    pool = cand[cand.side == side].groupby('day').y.mean()
    if x.empty: print(f'{name}: no events in the option window'); continue
    per = x.groupby('day').y.mean(); ex = per - pool.reindex(per.index)
    print(f"{name}: {e.sym.nunique()} names / {len(e)} events, {len(x)} contracts on {len(per)} days | mean y {x.y.mean()*100:+.1f}% win {(x.y>0).mean():.2f} | vs pool {ex.mean()*100:+.1f} pts (t {ex.mean()/(ex.std()/np.sqrt(len(ex))) if len(ex)>2 else float('nan'):+.1f})")
