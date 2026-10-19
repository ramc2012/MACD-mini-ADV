import json, math, sys
import numpy as np, pandas as pd
from scipy.optimize import minimize
sys.path.insert(0, '/eng')
from macd_trader.entry_model import MODEL as OLD
H, LAM, SKIP = 2.0, 3.0, 0.30
s = pd.read_pickle('/w/retrain_data.pkl')
theta = lambda D: 1 - np.sqrt(np.maximum(np.maximum(D, 1e-3) - H, 0) / np.maximum(D, 1e-3))
s['theta_share'] = theta(s.D)
feats = [f for f in OLD['features'] if f != 'dte'] + ['theta_share']
oi = {n: i for i, n in enumerate(OLD['features'])}
tr = s[s.complete]
std = {n: (OLD['mean'][oi[n]], OLD['std'][oi[n]]) for n in feats if n in oi}
std['theta_share'] = (float(tr.theta_share.mean()), float(tr.theta_share.std()))
def X_of(df):
    return np.column_stack([np.clip((df[n].astype(float).fillna(std[n][0]) - std[n][0]) / (std[n][1] or 1), -4, 4) for n in feats])
w0 = np.array([OLD['weights'][oi[n]] if n in oi else 0.0 for n in feats])
X, y = X_of(tr), tr.win.values
def loss(p):
    z = np.clip(p[0] + X @ p[1:], -30, 30)
    return np.mean(np.logaddexp(0, z) - y * z) + LAM * np.sum((p[1:] - w0) ** 2) / len(y) * 100
p = minimize(loss, np.r_[OLD['bias'], w0], method='L-BFGS-B').x
b, w = p[0], p[1:]
prob = lambda df: 1 / (1 + np.exp(-np.clip(b + X_of(df) @ w, -30, 30)))
sc = prob(tr)
pct = {str(q): float(np.percentile(sc, q)) for q in range(5, 100, 5)}
cut = pct['30']
old_cut = OLD['percentiles'][str(30)] if str(30) in OLD['percentiles'] else OLD['percentiles'][30]
def old_prob(df):
    Xo = np.column_stack([np.clip((df[n].astype(float).fillna(OLD['mean'][i]) - OLD['mean'][i]) / (OLD['std'][i] or 1), -4, 4) for i, n in enumerate(OLD['features'])])
    return 1 / (1 + np.exp(-np.clip(OLD['bias'] + Xo @ np.array(OLD['weights']), -30, 30)))
s['p_new'], s['p_old'] = prob(s), old_prob(s)
s['dteb'] = pd.cut(s.dte, [-1, 3, 7, 14, 21, 60], labels=['0-3', '4-7', '8-14', '15-21', '22+'])
t = s.groupby('dteb', observed=True).agg(n=('win', 'size'), deployed_skip=('p_old', lambda x: (x < old_cut).mean()),
                                         retrained_skip=('p_new', lambda x: (x < cut).mean()), win=('win', 'mean'), ret=('ret', 'mean'))
print('skip rate at the 30% cut by days to expiry (all 10 days):'); print(t.round(3).to_string())
dd = s.groupby('day').agg(deployed_skip=('p_old', lambda x: (x < old_cut).mean()), retrained_skip=('p_new', lambda x: (x < cut).mean()))
print('\nby day:'); print(dd.round(2).to_string())
print('\nweights that moved most from September:')
mv = sorted(zip(feats, w, w0), key=lambda r: -abs(r[1] - r[2]))[:8]
for n, a, c in mv: print(f'  {n:20s} {c:+.3f} -> {a:+.3f}')
print('theta_share weight %+.3f (positive = more decay during the hold scores HIGHER)' % w[feats.index('theta_share')])
export = {"features": feats, "weights": [float(v) for v in w], "bias": float(b),
          "mean": [float(std[n][0]) for n in feats], "std": [float(std[n][1]) for n in feats], "winsor": 4.0,
          "percentiles": pct, "hold_days": H,
          "trained_on": {"rows": int(len(tr)), "from": str(tr.day.min()), "to": str(tr.day.max()),
                         "source": "live gate journal (macd_entry_scores) replayed with the lane's exits over 3 sessions; ridge toward the September model, strength 3"},
          "walk_forward": json.load(open('/w/wf_summary.json'))}
json.dump(export, open('/w/model_v2.json', 'w'), indent=1)
print('exported; cut at 30%:', round(cut, 4))
