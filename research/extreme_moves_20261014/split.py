import pandas as pd
r = pd.read_csv('/w/out/option_events.csv'); r['half'] = r.day.str[:7] < '2025-09'
for (u, d), g in r[r.k == 1.0].groupby(['und', 'dir']):
    print(u, 'up' if d > 0 else 'down', 'n', len(g), ' '.join(f"{h}: mean {g[f'with_{h}'].mean()*100:+.0f}% med {g[f'with_{h}'].median()*100:+.0f}% gained {(g[f'with_{h}']>0).mean()*100:.0f}%" for h in ('m60', 'close', 'd1')))
