import pandas as pd
fr = []
for side in ('', 'pe_'):
    for tf in (5, 3):
        t = pd.read_csv(f'/w/out/stk_{side}spot_trades_{tf}m.csv'); r = pd.read_csv(f'/w/out/stk_{side}spot_random_{tf}m.csv')
        r = r[r.draw < 3].assign(set=lambda d: 'RANDOM_' + d.draw.astype(str))
        x = pd.concat([t[t.set == 'ALL4'], r], ignore_index=True)
        x = x[x.entry_ts >= 1787198400].assign(tf=tf, system='PE' if side else 'CE')     # from 20 Aug 2026
        fr.append(x)
x = pd.concat(fr, ignore_index=True)
x['set'] = x['system'] + '|' + x['set']
x[['sym', 'set', 'tf', 'entry_ts', 'exit_ts', 'entry', 'pts', 'mins']].to_csv('/w/out/stk_trades_in.csv', index=False)
print(x.groupby(['system', 'tf']).size().to_dict(), len(x))
