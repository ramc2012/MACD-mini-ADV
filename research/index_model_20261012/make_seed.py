"""Seed rows for agent_index_days: daily OHLC for every session (Fyers 1-min) through the last labelled
research day, with the research features and live-plan labels (R, stop = 1 unit) where they exist."""
import json
import numpy as np, pandas as pd
IST = 19800
A = json.load(open('/r/dd_nifty_4ind_20261009/data/idx1m.json')); X = json.load(open('/w/data/idx_extra.json'))
SYMS = {'NIFTY': 'NSE:NIFTY50-INDEX', 'BANKNIFTY': 'NSE:NIFTYBANK-INDEX', 'SENSEX': 'BSE:SENSEX-INDEX', 'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX'}
feat = pd.read_csv('/w/out/index_days.csv')
last_day = feat.day.max()
KEYS = ['spot', 'atr', 'u_ret_1d', 'u_ret_5d', 'u_ret_20d', 'u_range20_pos', 'gap', 'last_hour', 'move_atr', 'rsi_d', 'rsi_h', 'trend_d', 'trend_w', 'trend_h']
rows = []
for und, sym in SYMS.items():
    m = pd.DataFrame(list(A.get(sym, [])) + list(X.get(sym, [])), columns=['ts', 'o', 'h', 'l', 'c', 'v']).drop_duplicates('ts').sort_values('ts')
    clock = (m.ts + IST) % 86400
    m = m[(clock >= 33300) & (clock < 55800)]
    m['day'] = pd.to_datetime((m.ts + IST) // 86400, unit='D').dt.date.astype(str)
    d = m.groupby('day').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('c', 'size'))
    d = d[(d.n >= 300) & (d.index <= last_day)]
    f = feat[feat.und == und].set_index('day')
    for day, r in d.iterrows():
        rec = {'underlying': und, 'day': day, 'open': r.o, 'high': r.h, 'low': r.l, 'close': r.c}
        if day in f.index:
            x = f.loc[day]
            rec['features'] = {k: (int(x[k]) if k.startswith('trend') else float(x[k])) for k in KEYS}
            for tag in ('ce', 'pe'):
                path = x[f'{tag}_path']; ret = x[f'{tag}_ret']
                rec[f'{tag}_path'] = path
                rec[f'{tag}_r'] = 1.5 if path == 'target' else (-1.0 if path == 'stop' else float(ret / (x.atr / x.spot)))
                sp, sr = x[f'{tag}_t1s1_path'], x[f'{tag}_t1s1_ret']
                rec[f'{tag}_sel'] = 1.0 if sp == 'target' else (-1.0 if sp == 'stop' else float(sr / (x.atr / x.spot)))
        rows.append(rec)
json.dump(rows, open('/w/out/seed_index_days.json', 'w'))
print(len(rows), 'rows;', sum(1 for r in rows if 'features' in r), 'with features; last day', last_day)
