"""Scorecard side rule from the conditions that held in >=4/5 years, plus vetoes; evaluated per year.
To limit in-sample bias, also a strict version: conditions re-selected each test year using ONLY prior years."""
import numpy as np, pandas as pd
df = pd.read_csv('/w/out/index_days.csv'); df = df[df.day >= '2022-01-01'].copy(); df['year'] = df.day.str[:4]
def R(tag, sfx, tm, sm):
    p = df[f'{tag}{sfx}_path']; ret = df[f'{tag}{sfx}_ret'] / (df.atr / df.spot) / sm
    return np.where(p == 'target', tm / sm, np.where(p == 'stop', -1.0, ret))
plans = {'live 1.5/1.0': ('', 1.5, 1.0), '1.0/1.0': ('_t1s1', 1.0, 1.0), '0.75/1.0': ('_t075s1', 0.75, 1.0), '2.0/1.0': ('_t2s1', 2.0, 1.0)}
bull = (df.last_hour > 0).astype(int) + (df.trend_h == 1) + (df.rsi_h > 70) + (df.move_atr > 0.5) + (df.rsi_d < 30)
bear = (df.last_hour < 0).astype(int) + (df.trend_h == -1) + (df.move_atr < -0.5)
veto_pe = (df.rsi_d < 30) | (df.rsi_h > 70)
score = bull - bear
def evaluate(name, side):
    for pname, (sfx, tm, sm) in plans.items():
        ce, pe = R('ce', sfx, tm, sm), R('pe', sfx, tm, sm)
        r = np.where(side > 0, ce, np.where(side < 0, pe, np.nan)); ok = ~np.isnan(r)
        strike = np.where(side > 0, df[f'ce{sfx}_path'] == 'target', df[f'pe{sfx}_path'] == 'target')[ok].mean()
        yrs = ' '.join(f"{y}:{(r[ok & (df.year==y).values]>0).mean():.2f}" for y in sorted(df.year.unique()))
        print(f"  {name:40s} {pname:12s} trades={ok.sum():4d} CE%={(side[ok]>0).mean():.2f} strike={strike:.2f} win={(r[ok]>0).mean():.3f} meanR={np.nanmean(r):+.3f} | {yrs}")
print('### baselines')
evaluate('always CE', np.ones(len(df)))
evaluate('trend 20d (live leaning)', np.sign(df.u_ret_20d.values))
print('### scorecard (bull: last hour up, hourly uptrend, hourly RSI>70, today >+0.5 ATR, daily RSI<30; bear: mirrors of the first, second and fourth; puts vetoed when daily RSI<30 or hourly RSI>70)')
for k in (1, 2, 3):
    side = np.where(score >= k, 1, np.where((score <= -k) & ~veto_pe, -1, 0))
    evaluate(f'score >= {k} (no trade otherwise)', side)
side = np.where(score > 0, 1, np.where((score < 0) & ~veto_pe, -1, 1))
evaluate('score sign, default CE', side)
