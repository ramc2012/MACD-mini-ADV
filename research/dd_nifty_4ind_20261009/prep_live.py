import pandas as pd
fr = []
for tf in (5, 3):
    t = pd.read_csv(f'/w/out/pe_spot_trades_{tf}m.csv'); r = pd.read_csv(f'/w/out/pe_spot_random_{tf}m.csv'); r = r[r.draw < 10].assign(set=lambda d: 'RANDOM_' + d.draw.astype(str))
    x = pd.concat([t, r], ignore_index=True); x = x[x.entry_ts >= 1786160000].assign(tf=tf)   # from 8 Aug 2026
    fr.append(x)
pd.concat(fr)[['sym', 'set', 'tf', 'entry_ts', 'exit_ts', 'entry', 'pts', 'mins']].to_csv('/w/out/pe_live_trades_in.csv', index=False)
print(pd.concat(fr).groupby(['tf', 'sym', 'set']).size().loc[lambda s: ~s.index.get_level_values(2).str.startswith('RANDOM')].to_dict())
