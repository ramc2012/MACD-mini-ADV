import json
import numpy as np, pandas as pd
W = '/w/'; LOTS = json.load(open('/s/data/lots.json'))
print('######## SPOT, 214 stocks, 20 Aug - 7 Oct 2026 (bps of stock price per trade; intraday, Supertrend exit)')
for side, pfx in (('CE system (long)', 'stk_'), ('PE mirror (short)', 'stk_pe_')):
    for tf in (5, 3):
        t = pd.read_csv(W + f'out/{pfx}spot_trades_{tf}m.csv'); r = pd.read_csv(W + f'out/{pfx}spot_random_{tf}m.csv')
        t = t[t.entry_ts >= 1787198400]; r = r[r.entry_ts >= 1787198400]
        print(f'--- {side}, {tf}-min')
        for s in ('ST', 'ST+RSI', 'ST+RSI+R1', 'ALL4'):
            x = t[t.set == s]
            per_day = x.groupby('day').ret_bps.mean(); tt = per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day)))
            print(f"  {s:10s} n={len(x):6d} win={(x.ret_bps>0).mean():.2f} mean={x.ret_bps.mean():+6.1f} bps  t(days)={tt:+.1f} hold={x.mins.median():.0f}m")
        per = r.groupby('draw').ret_bps.agg(['mean', 'size', lambda z: (z > 0).mean()])
        print(f"  RANDOM     n={per['size'].mean():6.0f} win={per['<lambda_0>'].mean():.2f} mean={per['mean'].mean():+6.1f} bps (draw range {per['mean'].min():+.1f} .. {per['mean'].max():+.1f})")
o = pd.read_csv(W + 'out/stk_opt.csv')
o['system'] = o.set.str.split('|').str[0]; o['which'] = o.set.str.split('|').str[1]
o['lot'] = o.sym.map(lambda s: LOTS.get(s, np.nan))
o = o[o.lot.notna()]
for R_ in ('CE', 'PE'):
    e, x = o[f'{R_}_entry'], o[f'{R_}_exit']
    o[f'{R_}_ret'] = (x - e - (40 / o.lot + 0.0005 * e + 0.0015 * x + 0.0025 * (e + x))) / e
o['main_ret'] = np.where(o.system == 'CE', o.CE_ret, o.PE_ret); o['other_ret'] = np.where(o.system == 'CE', o.PE_ret, o.CE_ret)
o['main_entry'] = np.where(o.system == 'CE', o.CE_entry, o.PE_entry)
o['prem_pct_spot'] = o.main_entry / o.spot * 100
o['month'] = o.day.str[:7]
print('\n######## REAL STOCK OPTION PREMIUMS (premium nearest 1% of spot among stored strikes; net of Rs 40/lot, 0.05/0.15%, 0.25% slippage each side), Rs 50k per trade')
for (sysm, tf), g in o.groupby(['system', 'tf']):
    a = g[g.which == 'ALL4']; rnd = g[g.which.str.startswith('RANDOM')]
    w, l = a.main_ret[a.main_ret > 0], a.main_ret[a.main_ret <= 0]
    pd_ = a.groupby('day').main_ret.mean(); tt = pd_.mean() / (pd_.std(ddof=1) / np.sqrt(len(pd_)))
    pos = (a.main_ret * 50000).clip(lower=0).sort_values(ascending=False)
    print(f"--- {sysm} system, {tf}-min: ALL4 n={len(a)} win={(a.main_ret>0).mean():.2f} mean={a.main_ret.mean()*100:+.1f}% median={a.main_ret.median()*100:+.1f}% payoff={w.mean()/-l.mean():.2f} total=Rs {a.main_ret.sum()*50000:+,.0f} t(days)={tt:+.1f}; premium/spot median {a.prem_pct_spot.median():.1f}%")
    print(f"      opposite option at the same moments: mean {a.other_ret.mean()*100:+.1f}%, win {(a.other_ret>0).mean():.2f}")
    rr = rnd.groupby('which').main_ret.agg(['mean', 'size', 'sum'])
    print(f"      RANDOM entries (3 draws): mean {rr['mean'].mean()*100:+.1f}%  total per draw Rs {rr['sum'].mean()*50000:+,.0f}")
    print('      by month (ALL4):', {m: (len(v), round((v.main_ret > 0).mean(), 2), round(v.main_ret.mean() * 100, 1)) for m, v in a.groupby('month')})
