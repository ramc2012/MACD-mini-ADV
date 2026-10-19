import numpy as np, pandas as pd
a = pd.read_csv('/w/agent_1445.csv'); a['day'] = a.day.astype(str)
v = pd.read_csv('/w/vanguard_items.csv', header=None, names=['day', 'symbol', 'otype', 'horizon', 'kind', 'entry', 'd1', 'd2', 'd3'])
v['side'] = np.where(v.otype == 'CE', 1, -1)
pool = a[a.kind == 'pool']; ours = a[a.kind == 'pick']
days = sorted(set(v.day) & set(ours.day))
v, ours, pool = v[v.day.isin(days)], ours[ours.day.isin(days)], pool[pool.day.isin(days)]
COST = 0.01
def stats(x, col, bench=None):
    s = x[col].dropna() - COST
    if not len(s): return '-'
    out = f"n={len(s):3d} mean={s.mean()*100:+6.1f}% med={s.median()*100:+6.1f}% win={(s>0).mean()*100:3.0f}%"
    if bench is not None:
        b = bench.groupby(['day', 'side'])[col].mean()
        ex = (x.set_index(['day', 'side'])[col] - b.reindex(x.set_index(['day', 'side']).index).values).dropna()
        per = ex.groupby(level=0).mean(); t = per.mean() / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 2 else np.nan
        out += f" | vs pool same day+side {ex.mean()*100:+5.1f} pts (t {t:+.1f}, {len(per)} days)"
    return out
print('Sessions compared:', len(days), days[0], '->', days[-1])
print('All marks at 14:45; 1% round-trip cost deducted; stock options only (indices excluded from our lists).\n')
for col, lab in (('d1', 'D+1'), ('d2', 'D+2'), ('d3', 'D+3')):
    print(f'### {lab}')
    for name, x in (('Vanguard swing list', v), ('Our option agent', ours), ('Our whole candidate pool', pool)):
        print(f'  {name:26s} ALL {stats(x, col, None if name.startswith("Our whole") else pool)}')
        for s, sn in ((1, 'CE'), (-1, 'PE')):
            print(f'  {"":26s} {sn}  {stats(x[x.side == s], col, None if name.startswith("Our whole") else pool)}')
print('\n### Per session D+1 mean (net): Vanguard vs ours vs pool')
t = pd.DataFrame({'vanguard': v.groupby('day').d1.mean() - COST, 'ours': ours.groupby('day').d1.mean() - COST, 'pool': pool.groupby('day').d1.mean() - COST})
print((t * 100).round(1).to_string())
d = (t.ours - t.vanguard).dropna(); print(f'ours minus Vanguard, per-day mean {d.mean()*100:+.1f} pts, t {d.mean()/(d.std(ddof=1)/np.sqrt(len(d))):+.2f}, ours better on {(d>0).sum()}/{len(d)} days')
print('\nOverlap of names (same day, same side):', sum(len(set(v[(v.day == dd) & (v.side == s)].symbol) & set(ours[(ours.day == dd) & (ours.side == s)].symbol.str.extract(r':([A-Z&\-0-9]+?)26')[0])) for dd in days for s in (1, -1)), 'of', len(v))
