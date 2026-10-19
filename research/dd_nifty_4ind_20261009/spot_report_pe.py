import sys
import numpy as np, pandas as pd
W='/w/'
for tf in (5, 3):
    t = pd.read_csv(W+f'out/pe_spot_trades_{tf}m.csv'); r = pd.read_csv(W+f'out/pe_spot_random_{tf}m.csv')
    for df in (t, r): df['year'] = pd.to_datetime(df.day*86400, unit='s').dt.year
    print(f'\n################ {tf}-minute bars: PUT MIRROR: index points per trade (positive = index fell) (spot, before option costs)')
    for sym in ('NIFTY','BANKNIFTY','SENSEX'):
        print(f'--- {sym}')
        for s in ('ST','ST+RSI','ST+RSI+R1','ALL4'):
            x = t[(t.sym==sym)&(t.set==s)]
            w, l = x.pts[x.pts>0], x.pts[x.pts<=0]
            top5 = x.pts.sort_values(ascending=False).head(5).sum()/x.pts.sum() if x.pts.sum()>0 else np.nan
            print(f"{s:10s} n={len(x):5d} win={(x.pts>0).mean():.2f} mean={x.pts.mean():+6.1f} pts ({x.ret_bps.mean():+5.1f} bps) total={x.pts.sum():+8.0f} payoff={w.mean()/-l.mean():.2f} hold={x.mins.median():.0f}m top5share={top5:.2f}")
        rr = r[r.sym==sym]; per = rr.groupby('draw').agg(n=('pts','size'), win=('pts', lambda z:(z>0).mean()), mean=('pts','mean'), tot=('pts','sum'))
        a4 = t[(t.sym==sym)&(t.set=='ALL4')]
        print(f"RANDOM(ST-up, same count/yr, 30 draws) win={per.win.mean():.2f} mean={per['mean'].mean():+6.1f} total={per.tot.mean():+8.0f} [p5 {per.tot.quantile(.05):+.0f}, p95 {per.tot.quantile(.95):+.0f}]  ALL4 total rank vs draws: {(per.tot < a4.pts.sum()).mean():.2f}")
        yr = a4.groupby('year').agg(n=('pts','size'), win=('pts', lambda z: round((z>0).mean(),2)), mean_pts=('pts', lambda z: round(z.mean(),1)), total_pts=('pts', lambda z: round(z.sum())))
        ry = rr.groupby(['draw','year']).pts.sum().groupby('year').mean().round()
        yr['random_total_pts'] = ry
        print(yr.to_string())
