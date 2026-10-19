"""Walk-forward comparison of index side/trade models on 2021-2026 (monthly refit, 3-session embargo).

Outcome per side in R (stop = -1 R): target +1.5 R, stop -1 R, time exit = signed move / ATR.
'win' = target hit, or a positive time exit. A model scores expected R for CE and for PE; the trade is
the better side, and it trades only if that expected R clears a threshold set on the TRAINING data
(coverage levels 100% / 60% / 35% of training days). Everything is chosen on the past only.
"""
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

df = pd.read_csv('/w/out/index_days.csv').sort_values(['day', 'und']).reset_index(drop=True)
for tag in ('ce', 'pe'):
    df[f'{tag}_R'] = np.where(df[f'{tag}_path'] == 'target', 1.5, np.where(df[f'{tag}_path'] == 'stop', -1.0, df[f'{tag}_ret'] / (df.atr / df.spot)))
    df[f'{tag}_win'] = (df[f'{tag}_R'] > 0).astype(int)
LIVE = ['u_ret_intraday', 'u_ret_1d', 'u_ret_5d', 'u_ret_20d', 'u_day_range_pos', 'u_range20_pos', 'u_macd_hist', 'u_rs_5d']
EXTRA = ['u_rv_ratio', 'gap', 'last_hour', 'move_atr', 'atr_pct', 'dist_ema20_atr', 'rsi_d', 'rsi_h', 'rsi_15', 'trend_d', 'trend_w', 'trend_h', 'vix', 'vix_chg5', 'streak']
for u in ('BANKNIFTY', 'SENSEX', 'MIDCPNIFTY'): df[f'is_{u}'] = (df.und == u).astype(float)
ALL = LIVE + EXTRA + ['is_BANKNIFTY', 'is_SENSEX', 'is_MIDCPNIFTY']
df[ALL] = df[ALL].fillna(df[ALL].median())
df['month'] = df.day.str[:7]
months = sorted(df.month.unique())
test_months = [m for m in months if m >= '2022-01']

def model(kind, feats):
    if kind == 'ridge': return lambda: make_pipeline(StandardScaler(), Ridge(alpha=50.0))
    if kind == 'gbm': return lambda: HistGradientBoostingRegressor(max_iter=200, learning_rate=0.04, max_depth=3, min_samples_leaf=60, l2_regularization=1.0)
CONFIGS = {'ridge_live': ('ridge', LIVE), 'ridge_all': ('ridge', ALL), 'gbm_live': ('gbm', LIVE), 'gbm_all': ('gbm', ALL)}

out = []
for m in test_months:
    test = df[df.month == m]
    first_test_day = test.day.min()
    days_sorted = sorted(df.day.unique()); k = days_sorted.index(first_test_day)
    train = df[df.day < days_sorted[max(0, k - 3)]]
    if len(train) < 300: continue
    rec = {'day': test.day.values, 'und': test.und.values, 'ce_R': test.ce_R.values, 'pe_R': test.pe_R.values}
    for name, (kind, feats) in CONFIGS.items():
        preds, thr = {}, {}
        for tag in ('ce', 'pe'):
            mdl = model(kind, feats)(); mdl.fit(train[feats].values, train[f'{tag}_R'].values)
            preds[tag] = mdl.predict(test[feats].values); thr[tag] = mdl.predict(train[feats].values)
        best_train = np.maximum(thr['ce'], thr['pe'])
        rec[f'{name}_ce'], rec[f'{name}_pe'] = preds['ce'], preds['pe']
        for cov in (1.0, 0.6, 0.35):
            rec[f'{name}_thr{cov}'] = np.full(len(test), np.quantile(best_train, 1 - cov))
    out.append(pd.DataFrame(rec))
res = pd.concat(out, ignore_index=True)
res['year'] = res.day.str[:4]
res.to_csv('/w/out/wf_preds.csv', index=False)

def score(side, take, R):
    r = np.where(side > 0, res.ce_R, res.pe_R)[take]
    return r
def report(label, side, take):
    r = np.where(side > 0, res.ce_R, res.pe_R)
    t = take & (side != 0)
    rr = r[t]
    yr = pd.DataFrame({'y': res.year[t], 'r': rr}).groupby('y').r.agg(lambda z: f"{(z>0).mean():.2f}/{z.mean():+.2f}R/n{len(z)}")
    print(f"{label:34s} trades={t.sum():5d} CE%={(side[t]>0).mean():.2f} win={(rr>0).mean():.3f} meanR={rr.mean():+.3f} | " + ' '.join(f'{k}:{v}' for k, v in yr.items()))
ones = np.ones(len(res), bool)
print('Baselines (every index-day traded):')
report('always CE', np.ones(len(res)), ones)
report('always PE', -np.ones(len(res)), ones)
nifty = pd.read_csv('/w/out/index_days.csv').set_index(['day', 'und'])
key = list(zip(res.day, res.und))
r5 = np.array([nifty.loc[k, 'u_ret_5d'] for k in key]); r1 = np.array([nifty.loc[k, 'u_ret_1d'] for k in key]); r20 = np.array([nifty.loc[k, 'u_ret_20d'] for k in key])
report('trend 5d (sign of 5-day return)', np.sign(r5), ones)
report('trend 20d (what live leans on)', np.sign(r20), ones)
report('fade 1d (against yesterday)', -np.sign(r1), ones)
print('\nModels (side = higher expected R; coverage = share of days traded, threshold set on training):')
for name in CONFIGS:
    side = np.where(res[f'{name}_ce'] >= res[f'{name}_pe'], 1, -1)
    best = np.maximum(res[f'{name}_ce'], res[f'{name}_pe'])
    for cov in (1.0, 0.6, 0.35):
        report(f'{name} cov{cov}', side, best >= res[f'{name}_thr{cov}'])
