"""Exit variants re-scored on contracts that were live in the tradable band at entry (tick-store days only)."""
import numpy as np, pandas as pd

pd.set_option('display.width', 250); pd.set_option('display.max_columns', 20)
f = pd.read_csv('/w/data/tickfeat.csv').set_index('row')
for fill in ('', '_cons'):
    t = pd.read_csv(f'/w/out/exit_trades{fill}.csv.gz')
    e = pd.read_csv(f'/w/out/entries{fill}.csv.gz', index_col=0)
    t = t.join(f[['member']], on='i').join(e[['day', 'right', 'root']], on='i')
    base = t[t.variant.str.startswith('current')].set_index('i')
    print(f"\n==== fill model: {'conservative' if fill else 'optimistic'}")
    print('current rule by band membership (covered days):')
    print(base.groupby(base.member.map({1.0: 'live band', 0.0: 'NOT in band'}), dropna=True).agg(
        n=('ret', 'size'), mean_ret=('ret', 'mean'), median=('ret', 'median'), peak_over_100=('peak', lambda s: (s > 1).mean())).round(3).to_string())
    m = t[t.member == 1]
    b = m[m.variant.str.startswith('current')].set_index('i').ret
    rows = []
    for v, d in m.groupby('variant', sort=False):
        r = d.set_index('i').ret
        diff = (r - b).groupby(d.set_index('i').day).mean()
        se = diff.std(ddof=1) / np.sqrt(len(diff))
        rows.append({'variant': v, 'n': len(r), 'days': d.day.nunique(), 'mean_%': r.mean() * 100, 'median_%': r.median() * 100,
                     'win_%': (r > 0).mean() * 100, 'vs_current_pp': (r - b).mean() * 100,
                     'day_t': diff.mean() / se if se > 0 else np.nan})
    print('live-band entries only:')
    print(pd.DataFrame(rows).round(2).to_string(index=False))
