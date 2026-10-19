"""Strict out-of-sample: for each test year, keep only candidate conditions whose win-rate lift (for that
side) was positive in EVERY prior year (>=2 years), build the scorecard from those, test on the year."""
import numpy as np, pandas as pd
df = pd.read_csv('/w/out/index_days.csv'); df = df[df.day >= '2022-01-01'].copy(); df['year'] = df.day.str[:4].astype(int)
def R(tag, sfx, tm, sm):
    p = df[f'{tag}{sfx}_path']; ret = df[f'{tag}{sfx}_ret'] / (df.atr / df.spot) / sm
    return np.where(p == 'target', tm / sm, np.where(p == 'stop', -1.0, ret))
PLAN = ('_t1s1', 1.0, 1.0)
ce, pe = R('ce', *PLAN), R('pe', *PLAN)
ce_alt, pe_alt = R('ce', '', 1.5, 1.0), R('pe', '', 1.5, 1.0)
C = {  # candidate conditions: (favours CE mask, favours PE mask)
 'last_hour': (df.last_hour > 0, df.last_hour < 0), 'trend_h': (df.trend_h == 1, df.trend_h == -1),
 'trend_d': (df.trend_d == 1, df.trend_d == -1), 'trend_w': (df.trend_w == 1, df.trend_w == -1),
 'move_follow': (df.move_atr > 0.5, df.move_atr < -0.5), 'move_fade': (df.move_atr < -0.5, df.move_atr > 0.5),
 'rsi_h_follow': (df.rsi_h > 70, df.rsi_h < 30), 'rsi_h_fade': (df.rsi_h < 30, df.rsi_h > 70),
 'rsi_d_follow': (df.rsi_d > 70, df.rsi_d < 30), 'rsi_d_fade': (df.rsi_d < 30, df.rsi_d > 70),
 'ret5_follow': (df.u_ret_5d > 0, df.u_ret_5d < 0), 'ret20_follow': (df.u_ret_20d > 0, df.u_ret_20d < 0),
 'gap_follow': (df.gap > 0.003, df.gap < -0.003), 'range20_high': (df.u_range20_pos > 0.3, df.u_range20_pos < -0.3),
}
rows = []
for test in (2024, 2025, 2026):
    prior = df.year < test
    chosen_ce, chosen_pe = [], []
    for name, (mc, mp) in C.items():
        for tag, m, r, chosen in (('ce', mc, ce, chosen_ce), ('pe', mp, pe, chosen_pe)):
            lifts = []
            for y in range(2022, test):
                yy = (df.year == y).values
                base = (r[yy] > 0).mean(); sub = (r[yy & m.values] > 0)
                lifts.append(sub.mean() - base if sub.size >= 20 else -1)
            if min(lifts) > 0: chosen.append(name)
    bull = sum(C[n][0].astype(int) for n in chosen_ce) if chosen_ce else 0
    bear = sum(C[n][1].astype(int) for n in chosen_pe) if chosen_pe else 0
    yy = (df.year == test).values
    for k in (1, 2, 3):
        side = np.where((bull - bear) >= k, 1, np.where((bear - bull) >= k, -1, 0))
        for plan, a, b in (('1.0/1.0', ce, pe), ('1.5/1.0 live', ce_alt, pe_alt)):
            r = np.where(side > 0, a, np.where(side < 0, b, np.nan))[yy]; ok = ~np.isnan(r)
            base_ce = (a[yy] > 0).mean()
            rows.append({'test': test, 'k': k, 'plan': plan, 'trades': ok.sum(), 'CE%': round((side[yy][ok] > 0).mean(), 2),
                         'win': round((r[ok] > 0).mean(), 3), 'meanR': round(np.nanmean(r), 3), 'alwaysCE_win': round(base_ce, 3),
                         'alwaysCE_R': round(np.mean(a[yy]), 3), 'ce_conds': ','.join(chosen_ce), 'pe_conds': ','.join(chosen_pe)})
out = pd.DataFrame(rows)
print(out.drop(columns=['ce_conds', 'pe_conds']).to_string(index=False))
print(out.drop_duplicates('test')[['test', 'ce_conds', 'pe_conds']].to_string(index=False))
