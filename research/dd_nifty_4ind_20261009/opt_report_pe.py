"""Option P&L of the DD Nifty system on real premiums, the ablation and random entries; plus an
estimate for 2021-2026 by fitting premium change to index points and holding time on the real trades."""
import numpy as np, pandas as pd
W = '/w/'
LOT = {('NIFTY', 2025): 75, ('NIFTY', 2026): 65, ('SENSEX', 2025): 20, ('SENSEX', 2026): 20, ('BANKNIFTY', 2026): 30, ('BANKNIFTY', 2025): 35}
SLIP = 0.0025   # each side, fraction of premium

def prep(d, src):
    d = d.copy(); d['src'] = src
    d['year'] = d['day'].astype(str).str[:4].astype(int)
    d['lot'] = [LOT[(s, y if y >= 2025 else 2025)] for s, y in zip(d.sym, d.year)]
    d['expiry_day'] = (d['CE_expiry'].astype(str) == d['day'].astype(str))
    for R in ('CE', 'PE'):
        e, x = d[f'{R}_entry'], d[f'{R}_exit']
        cost_pts = 40 / d.lot + 0.0005 * e + 0.0015 * x + SLIP * (e + x)
        d[f'{R}_net_pts'] = x - e - cost_pts
        d[f'{R}_ret'] = d[f'{R}_net_pts'] / e
        d[f'{R}_rs_lot'] = d[f'{R}_net_pts'] * d.lot
        d[f'{R}_rs_50k'] = d[f'{R}_ret'] * 50000
    return d

tb = pd.concat([prep(pd.read_csv(W + f'out/pe_opt_tb_{tf}m.csv').assign(tf=tf), 'TradeBot') for tf in (5, 3)])
lv = prep(pd.read_csv(W + 'out/pe_opt_live.csv'), 'app 2026')
allr = pd.concat([tb, lv], ignore_index=True)

def summ(x, col='PE'):
    if len(x) == 0: return '-'
    r = x[f'{col}_ret']; rs = x[f'{col}_rs_50k']
    w, l = r[r > 0], r[r <= 0]
    top = rs.sort_values(ascending=False).head(5).sum() / rs.sum() if rs.sum() > 0 else np.nan
    return (f"n={len(x):4d} win={(r > 0).mean():.2f} mean={r.mean()*100:+6.1f}% med={r.median()*100:+6.1f}% "
            f"payoff={w.mean()/-l.mean() if len(w) and len(l) else np.nan:.2f} Rs/50k-trade total={rs.sum():+9.0f} top5={top:.2f}")

for (src, tf), g in allr.groupby(['src', 'tf']):
    print(f'\n######## {src}, {tf}-minute bars (PE system, PE: premium ~1% of spot, nearest expiry; net of charges + 0.25% slippage each side)')
    for sym, gs in g.groupby('sym'):
        print(f'--- {sym} {gs.day.min()} .. {gs.day.max()}')
        for s in ('ST', 'ST+RSI', 'ST+RSI+R1', 'ALL4'):
            x = gs[gs.set == s]
            print(f"  {s:10s} PE {summ(x)}")
        x = gs[gs.set == 'ALL4']
        if len(x):
            print(f"  {'ALL4':10s} CE at the same times {summ(x, 'CE')}")
            print(f"  ALL4 index points/trade {x.pts.astype(float).mean():+.1f}, hold {x.mins.astype(float).median():.0f} min, expiry-day trades {x.expiry_day.mean():.0%}, mean PE entry {x.PE_entry.mean():.0f}")
        rnd = gs[gs.set.str.startswith('RANDOM')]
        if len(rnd):
            per = rnd.groupby('set').agg(win=('PE_ret', lambda z: (z > 0).mean()), mean=('PE_ret', 'mean'), tot=('PE_rs_50k', 'sum'), n=('PE_ret', 'size'))
            print(f"  RANDOM ST-up entries (10 draws): win={per.win.mean():.2f} mean={per['mean'].mean()*100:+.1f}% total/draw={per.tot.mean():+.0f} (ALL4 beats {(per.tot < x.PE_rs_50k.sum()).mean():.0%} of draws)")

# --- estimate 2021-2026 option P&L from spot trades, calibrated on the real trades
real = allr[~allr.set.str.startswith('RANDOM')].copy()
real['pts'] = real.pts.astype(float); real['mins'] = real.mins.astype(float)
real['pts_pct'] = real.pts / real.spot
X = np.column_stack([real.pts_pct, real.mins / 375, real.expiry_day.astype(float), np.ones(len(real))])
beta, *_ = np.linalg.lstsq(X, real.PE_ret, rcond=None)
pred = X @ beta
print(f"\n######## calibration on {len(real)} real trades: PE net return = {beta[0]:.1f} x index move + {beta[1]*100:.1f}% per session held + {beta[2]*100:.1f}% if expiry day + {beta[3]*100:.1f}%  (R2 {1 - ((real.PE_ret-pred)**2).sum()/((real.PE_ret-real.PE_ret.mean())**2).sum():.2f})")
for tf in (5, 3):
    t = pd.read_csv(W + f'out/pe_spot_trades_{tf}m.csv'); t = t[t.set == 'ALL4'].copy()
    t['year'] = pd.to_datetime(t.day * 86400, unit='s').dt.year
    wd = pd.to_datetime(t.entry_ts + 19800, unit='s').dt.weekday
    # expiry-day flag approximation: NIFTY Thursday until Aug 2025 then Tuesday; SENSEX Friday until 2025 then Tuesday/Thursday - unknown -> 0
    Xs = np.column_stack([t.pts / t.entry, t.mins / 375, np.zeros(len(t)), np.ones(len(t))])
    t['est_rs_50k'] = (Xs @ beta) * 50000
    print(f'\n######## ESTIMATE (not real premiums): ALL4 on {tf}-min, Rs 50k premium per trade, by year')
    print(t.pivot_table(index='year', columns='sym', values='est_rs_50k', aggfunc='sum').round(-2).to_string())
