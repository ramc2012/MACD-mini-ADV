"""Which conditions raise the index lane's strike rate, consistently across years? And plan trade-offs."""
import numpy as np, pandas as pd
df = pd.read_csv('/w/out/index_days.csv'); df = df[df.day >= '2022-01-01'].copy(); df['year'] = df.day.str[:4]
PLANS = {'': '1.5/1.0 ATR, 3d (live)', '_t2s1': '2.0/1.0, 3d', '_t1s1': '1.0/1.0, 3d', '_t1s075': '1.0/0.75, 3d', '_t075s1': '0.75/1.0, 3d', '_d1t075s075': '0.75/0.75, 1d'}
def R(tag, sfx):
    tm, sm = {'': (1.5, 1), '_t2s1': (2, 1), '_t1s1': (1, 1), '_t1s075': (1, .75), '_t075s1': (.75, 1), '_d1t075s075': (.75, .75)}[sfx]
    p = df[f'{tag}{sfx}_path']; ret = df[f'{tag}{sfx}_ret'] / (df.atr / df.spot) / sm   # in units of the stop
    return np.where(p == 'target', tm / sm, np.where(p == 'stop', -1.0, ret))
print('### Plan trade-off (every index-day, 2022-2026): strike = target first; win = target or positive time exit; R in stop units')
for sfx, name in PLANS.items():
    for tag in ('ce', 'pe'):
        r = R(tag, sfx); strike = (df[f'{tag}{sfx}_path'] == 'target').mean()
        yrs = ' '.join(f"{y}:{(r[df.year==y]>0).mean():.2f}" for y in sorted(df.year.unique()))
        print(f"  {name:22s} {tag.upper()} strike={strike:.2f} win={(r>0).mean():.2f} meanR={r.mean():+.3f} | win by year {yrs}")
df['ceR'], df['peR'] = R('ce', ''), R('pe', '')
base = {t: (df[f'{t}R'] > 0).groupby(df.year).mean() for t in ('ce', 'pe')}
def cond(label, mask_ce, mask_pe):
    out = []
    for tag, m in (('ce', mask_ce), ('pe', mask_pe)):
        if m is None: continue
        x = df[m]; w = (x[f'{tag}R'] > 0)
        lift = (w.groupby(x.year).mean() - base[tag]).dropna()
        out.append(f"{tag.upper()} n={len(x):4d} win={w.mean():.2f} ({w.mean()-(df[f'{tag}R']>0).mean():+.2f}) meanR={x[f'{tag}R'].mean():+.2f} lift>0 in {int((lift>0).sum())}/{len(lift)} yrs")
    print(f"  {label:44s} " + ' | '.join(out))
print('\n### Conditions (live plan 1.5/1.0): win vs that side\'s overall base, and in how many years the lift held')
for tf in ('trend_w', 'trend_d', 'trend_h'):
    cond(f'{tf} aligned with side', df[tf] == 1, df[tf] == -1)
    cond(f'{tf} against side', df[tf] == -1, df[tf] == 1)
cond('weekly+daily+hourly all aligned', (df.trend_w == 1) & (df.trend_d == 1) & (df.trend_h == 1), (df.trend_w == -1) & (df.trend_d == -1) & (df.trend_h == -1))
cond('daily RSI extreme: CE if <30 / PE if >70 (fade)', df.rsi_d < 30, df.rsi_d > 70)
cond('daily RSI extreme: CE if >70 / PE if <30 (follow)', df.rsi_d > 70, df.rsi_d < 30)
cond('hourly RSI: CE if <30 / PE if >70 (fade)', df.rsi_h < 30, df.rsi_h > 70)
cond('hourly RSI: CE if >70 / PE if <30 (follow)', df.rsi_h > 70, df.rsi_h < 30)
cond('today up >0.5 ATR: CE / down: PE (follow)', df.move_atr > 0.5, df.move_atr < -0.5)
cond('today up >0.5 ATR: PE / down: CE (fade)', df.move_atr < -0.5, df.move_atr > 0.5)
cond('last hour up: CE / down: PE', df.last_hour > 0, df.last_hour < 0)
cond('VIX top third (by year)', df.vix > df.groupby('year').vix.transform(lambda z: z.quantile(.67)), df.vix > df.groupby('year').vix.transform(lambda z: z.quantile(.67)))
cond('VIX bottom third', df.vix < df.groupby('year').vix.transform(lambda z: z.quantile(.33)), df.vix < df.groupby('year').vix.transform(lambda z: z.quantile(.33)))
cond('VIX rising 5d >10%', df.vix_chg5 > .10, df.vix_chg5 > .10)
cond('ATR% top third (volatile)', df.atr_pct > df.atr_pct.quantile(.67), df.atr_pct > df.atr_pct.quantile(.67))
cond('ATR% bottom third (quiet)', df.atr_pct < df.atr_pct.quantile(.33), df.atr_pct < df.atr_pct.quantile(.33))
for d, n in enumerate(['Mon', 'Tue', 'Wed', 'Thu', 'Fri']): cond(f'entry on {n}', df.dow == d, df.dow == d)
# adaptive side prior: the side whose plan earned more over the trailing N sessions (known by t-4)
print('\n### Adaptive side: trade the side that earned more over the trailing N sessions (labels known 4 sessions back)')
for N in (10, 20, 40, 60, 120):
    sides = []
    for und, g in df.sort_values('day').groupby('und'):
        diff = (g.ceR - g.peR).shift(4).rolling(N, min_periods=N // 2).mean()
        sides.append(pd.Series(np.sign(diff).values, index=g.index))
    s = pd.concat(sides).reindex(df.index)
    r = np.where(s > 0, df.ceR, np.where(s < 0, df.peR, np.nan))
    ok = ~np.isnan(r)
    yrs = ' '.join(f"{y}:{(r[ok & (df.year==y).values]>0).mean():.2f}/{np.nanmean(r[ok & (df.year==y).values]):+.2f}R" for y in sorted(df.year.unique()))
    print(f"  N={N:3d} trades={ok.sum()} CE%={(s[ok]>0).mean():.2f} win={(r[ok]>0).mean():.3f} meanR={np.nanmean(r):+.3f} | {yrs}")
