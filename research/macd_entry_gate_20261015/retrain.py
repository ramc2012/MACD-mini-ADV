"""Retrain the MACD entry gate with days-to-expiry matched to the expected holding period. Read-only.

Data: every signal the live gate scored (research DB macd_entry_scores, 30 Sep - 15 Oct 2026). Its features were
computed live by entry_scorer.build_features, so training and serving share one definition. All are live-band
contracts, so there is no survivorship (research/option_recs_20261015).

Label: the signal replayed on its real option minute bars with the MACD lane's exits: -30% hard stop, 25% trail
armed at +30% (floored at breakeven), and a cap at HORIZON_SESSIONS sessions (the lane's 75th-percentile hold is
2.9 days). Costs are 1% round trip. win = net return > 0. Signals without a complete horizon (the last two
sessions) are scored for skip rates but not trained on.

The hold-matched expiry feature replaces raw dte:
    theta_share = 1 - sqrt(max(D - H, 0) / D)
D is fractional calendar days to the 15:30 expiry and H the expected hold in calendar days. It is the share of
the option's remaining time value that decays during the hold (sqrt-time decay), and 1 when the contract expires
inside the hold. A contract 20 days out held 2 days loses ~5%; one 3 days out loses ~42%.

Fitting: logistic regression on the old model's standardisation, ridge-penalised toward the September weights
(prior strength LAMBDA, chosen walk-forward). The new feature has prior weight 0. Evaluation: expanding-window
walk-forward by day against the deployed model. Measures: AUC, kept-minus-skipped return per day, and the skip
rate at the 30% cut by DTE bucket.
"""
import json, math, sqlite3, sys
import numpy as np, pandas as pd
from scipy.optimize import minimize
from sklearn.metrics import roc_auc_score

sys.path.insert(0, '/eng')
from macd_trader.entry_model import MODEL as OLD

HORIZON_SESSIONS = 3
COST = 0.01
h = sqlite3.connect('file:/data/historical.sqlite3?mode=ro', uri=True)
s = pd.read_sql("select at, day, symbol, score, decision, signal_id, features from macd_entry_scores where day>='2026-09-30'", h)
s = s.drop_duplicates('signal_id', keep='first') if s.signal_id.notna().all() else s.drop_duplicates(['symbol', 'at'])
F = pd.DataFrame([json.loads(f) for f in s.features]); s = pd.concat([s.reset_index(drop=True), F], axis=1)
s['t0'] = pd.to_datetime(s['at'], format='ISO8601').astype('int64') // 10**9
sessions = sorted({d for (d,) in h.execute("select distinct date(timestamp,'unixepoch','+330 minutes') from historical_candles "
                                           "where symbol='NSE:NIFTY50-INDEX' and timeframe_seconds=60 and timestamp>1790000000")})


def horizon_end(day):
    later = [d for d in sessions if d > day]
    if len(later) < HORIZON_SESSIONS - 1:
        return None, False
    end_day = later[HORIZON_SESSIONS - 2] if HORIZON_SESSIONS > 1 else day
    return int(pd.Timestamp(f'{end_day} 15:25', tz='Asia/Kolkata').timestamp()), True


def replay(symbol, t0, end):
    bars = h.execute('select open, high, low, close from historical_candles where symbol=? and timeframe_seconds=60 '
                     'and timestamp>=? and timestamp<=? order by timestamp', (symbol, t0 - t0 % 60, end)).fetchall()
    if len(bars) < 5:
        return None
    e = bars[0][0]; peak = e; floor = 0.0
    if e <= 0:
        return None
    for o, hi, lo, c in bars:
        level = max(e * 0.7, floor)
        if lo <= level:
            return min(o, level) / e - 1 - COST
        peak = max(peak, hi)
        if peak >= e * 1.3:
            floor = max(floor, peak * 0.75, e * 1.01)
    return bars[-1][3] / e - 1 - COST


rets, complete = [], []
for r in s.itertuples():
    end, ok = horizon_end(r.day)
    if end is None:
        end = int(pd.Timestamp(f'{sessions[-1]} 15:25', tz='Asia/Kolkata').timestamp())
    rets.append(replay(r.symbol, r.t0, end)); complete.append(ok)
s['ret'] = rets; s['complete'] = complete
s = s[s.ret.notna()].reset_index(drop=True)
s['win'] = (s.ret > 0).astype(int)
# fractional calendar days to the 15:30 expiry: whole days plus what is left of today's session
s['D'] = s.dte + np.clip(375 - s.minutes_since_open, 0, 375) / 1440


def theta_share(D, H):
    D = np.maximum(D, 1e-3)
    return 1 - np.sqrt(np.maximum(D - H, 0) / D)


old_idx = {n: i for i, n in enumerate(OLD['features'])}


def design(df, feats, std):
    X = np.zeros((len(df), len(feats)))
    for j, n in enumerate(feats):
        m, sd = std[n]
        X[:, j] = np.clip((df[n].astype(float).fillna(m) - m) / (sd or 1), -OLD['winsor'], OLD['winsor'])
    return X


def fit(df, feats, std, lam, prior):
    X = design(df, feats, std); y = df.win.values
    w0 = np.array([prior.get(n, 0.0) for n in feats]); b0 = OLD['bias']
    def loss(p):
        b, w = p[0], p[1:]
        z = np.clip(b + X @ w, -30, 30)
        ll = np.mean(np.logaddexp(0, z) - y * z)
        return ll + lam * np.sum((w - w0) ** 2) / len(y) * 100
    p = minimize(loss, np.r_[b0, w0], method='L-BFGS-B').x
    return p[0], p[1:]


def score(df, feats, std, b, w):
    return 1 / (1 + np.exp(-np.clip(b + design(df, feats, std) @ w, -30, 30)))


base_feats = list(OLD['features'])
std_old = {n: (OLD['mean'][i], OLD['std'][i]) for n, i in old_idx.items()}
prior_old = {n: OLD['weights'][i] for n, i in old_idx.items()}
train_all = s[s.complete]
days = sorted(train_all.day.unique())
print(f"signals {len(s)} ({s.day.nunique()} days); trainable (complete {HORIZON_SESSIONS}-session horizon) {len(train_all)} over {len(days)} days")
print('label: win rate %.3f, mean ret %.3f' % (train_all.win.mean(), train_all.ret.mean()))


def walk_forward(variant, feats, lam, H=None, prior=prior_old):
    df = train_all.copy()
    std = dict(std_old)
    if H is not None:
        df['theta_share'] = theta_share(df.D, H)
    out = []
    for k, d in enumerate(days):
        if k < 3:
            continue
        tr, te = df[df.day < d], df[df.day == d]
        if variant == 'deployed':
            p = score(te, base_feats, std_old, OLD['bias'], np.array(OLD['weights']))
        else:
            if 'theta_share' in feats:
                std['theta_share'] = (tr.theta_share.mean(), tr.theta_share.std() or 1)
            b, w = fit(tr, feats, std, lam, prior)
            p = score(te, feats, std, b, w)
        out.append(te.assign(p=p))
    o = pd.concat(out)
    auc = roc_auc_score(o.win, o.p) if o.win.nunique() > 1 else np.nan
    # 30% cut within each test day (the gate's intent), to compare ranking skill fairly
    o['keep'] = o.groupby('day').p.transform(lambda x: x >= x.quantile(0.30))
    per = o.groupby(['day', 'keep']).ret.mean().unstack()
    diff = (per[True] - per[False]).dropna()
    return {'variant': variant, 'n_test': len(o), 'auc': round(auc, 3),
            'kept_mean': round(o[o.keep].ret.mean(), 4), 'skipped_mean': round(o[~o.keep].ret.mean(), 4),
            'kept_minus_skipped_pp': round(100 * diff.mean(), 2),
            'day_t': round(diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff))), 2) if len(diff) > 1 else np.nan,
            'days_better': int((diff > 0).sum()), 'test_days': len(diff)}


rows = [walk_forward('deployed', base_feats, 0)]
no_dte = [f for f in base_feats if f != 'dte']
for lam in (0.3, 1, 3, 10):
    rows.append(walk_forward(f'refit dte, lam {lam}', base_feats, lam))
    for H in (1.0, 2.0, 3.0):
        rows.append(walk_forward(f'theta_share H={H:g}, lam {lam}', no_dte + ['theta_share'], lam, H=H))
res = pd.DataFrame(rows)
pd.set_option('display.width', 220); pd.set_option('display.max_rows', 100)
print(res.to_string(index=False))
res.to_csv('/w/retrain_walkforward.csv', index=False)
s.to_pickle('/w/retrain_data.pkl')
