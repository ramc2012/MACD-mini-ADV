"""Book-level replay of the 691 logged signals with the lane's caps (8 open, 12 entries/day, one per contract,
Rs 1 L per trade), under the current rules and candidate tunings. Exit value per signal is precomputed
(lane = -25% stop / 20% trail from +20%, t60 = 60-minute exit). Hold time approximated by the exit rule."""
import json, numpy as np, pandas as pd
d = pd.DataFrame(json.load(open('/w/out/mp_sig_eval.json'))).sort_values('ts')
d['setup'] = d.kind.str.replace('MP_', '').str[:-3]
def book(sel, exit_col, hold_min, start_min=0, cap=12):
    pnl, rows = 0.0, []
    for day, g in d[sel].groupby('day'):
        open_until, taken, held = [], 0, set()
        for r in g.itertuples():
            if r.minute < start_min: continue
            open_until = [(t, s) for t, s in open_until if t > r.ts]
            held = {s for _, s in open_until}
            if taken >= cap or len(open_until) >= 8 or r.symbol in held: continue
            taken += 1; ret = getattr(r, exit_col); rows.append((day, ret))
            open_until.append((r.ts + hold_min * 60, r.symbol))
    x = pd.DataFrame(rows, columns=['day', 'ret'])
    per = x.groupby('day').ret.sum() * 100000
    return f"trades={len(x):3d} win={(x.ret>0).mean():.2f} mean={x.ret.mean()*100:+5.1f}% book P&L Rs {x.ret.sum()*100000:+9,.0f}  by day {per.round(-2).astype(int).tolist()}"
allsig = d.symbol.notna()
no_bad = ~d.kind.isin(['MP_IB_RANGE_EXTENSION_CE', 'MP_VALUE_AREA_BREAKOUT_CE', 'MP_OPENING_RANGE_BREAKOUT_CE'])
print('CURRENT (lane exits, from 09:45, first 12):        ', book(allsig, 'lane', 90))
print('from 10:15 (IB complete), lane exits:               ', book(allsig, 'lane', 90, 615))
print('lane exits + 60-min time stop (approx = t60 col):   ', book(allsig, 't60', 60))
print('from 10:15 + 60-min exit:                            ', book(allsig, 't60', 60, 615))
print('from 10:15 + 60-min exit, cap 20/day:                ', book(allsig, 't60', 60, 615, 20))
print('from 10:15 + 60-min, drop 3 CE breakout setups:      ', book(no_bad, 't60', 60, 615))
print('index contracts only, from 10:15, 60-min:            ', book(d.symbol.str.contains('NIFTY|SENSEX'), 't60', 60, 615))
print('stocks only, from 10:15, 60-min:                     ', book(~d.symbol.str.contains('NIFTY|SENSEX'), 't60', 60, 615))
