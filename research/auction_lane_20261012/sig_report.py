import json, numpy as np, pandas as pd
d = pd.DataFrame(json.load(open('/w/out/mp_sig_eval.json')))
d['setup'] = d.kind.str.replace('MP_', '').str.replace('_CE', '').str.replace('_PE', '')
d['side'] = d.kind.str[-2:]
d['idx'] = d.symbol.str.contains('NIFTY|SENSEX')
d['hour'] = d.minute // 60
def s(x, col):
    v = x[col].dropna()
    if len(v) < 3: return f'n={len(v)}'
    per = x.groupby('day')[col].mean(); t = per.mean() / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 2 else np.nan
    w, l = v[v > 0], v[v <= 0]
    return f"n={len(v):3d} win={(v>0).mean():.2f} mean={v.mean()*100:+5.1f}% med={v.median()*100:+5.1f}% payoff={w.mean()/-l.mean() if len(w) and len(l) else np.nan:.2f}"
print('days:', d.day.value_counts().sort_index().to_dict())
print('\n### ALL signals by exit rule')
for col in ('lane', 'wide', 'eod', 't30', 't60'): print(f'  {col:5s}', s(d, col))
print(f"  opposite option, lane exit: {s(d, 'opp_lane')} | eod: {s(d, 'opp_eod')}")
print('\n### by setup x side (lane exit | eod | t60 | opposite-side eod)')
for (st, sd), g in d.groupby(['setup', 'side']):
    if len(g) < 5: continue
    print(f"  {st:26s} {sd} lane {s(g,'lane')} | eod mean {g.eod.mean()*100:+5.1f}% | t60 {g.t60.mean()*100:+5.1f}% | opp eod {g.opp_eod.mean()*100:+5.1f}%")
print('\n### index vs stock (lane / eod)')
for k, g in d.groupby('idx'): print(f"  {'index' if k else 'stock'}: {s(g,'lane')} | eod {g.eod.mean()*100:+.1f}%")
print('\n### by hour of entry (lane)')
for k, g in d.groupby('hour'): print(f"  {k}:00 {s(g,'lane')} | eod {g.eod.mean()*100:+.1f}%")
print('\n### by day and side (eod mean): side matters?')
print(d.pivot_table(index='day', columns='side', values='eod', aggfunc='mean').round(3).to_string())
print('\n### MFE reach: share of signals whose option ran >= +20% / +50% before 15:20', round((d.mfe >= .2).mean(), 2), round((d.mfe >= .5).mean(), 2))
