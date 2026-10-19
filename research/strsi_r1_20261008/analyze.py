"""Step 3: did buying the ATM CE on 'Supertrend up + RSI>70 + above R1' give sustained gains?

Per dataset: CE return at each horizon (gross and net of Rs 20/order x2 + 0.2% of premium),
win rate, payoff, median, top-5 share, t over days; the matched controls; CE minus PE at the
same moment (side check) and CE minus half a straddle; month split; MFE reach.
"""
import json, sys
import numpy as np, pandas as pd

W = '/w/'
LOTS = json.load(open(W + 'data/lots.json'))
HZ = ['m15', 'm30', 'm60', 'close', 'd1', 'd2', 'd3', 'st_exit']
LAST_LIVE = '2026-10-07'

def lot(sym, ds):
    if ds != 'live': return LOTS[f'{ds.upper()}_2025']
    root = {'NSE:NIFTY50-INDEX': 'NIFTY', 'NSE:NIFTYBANK-INDEX': 'BANKNIFTY', 'BSE:SENSEX-INDEX': 'SENSEX', 'NSE:MIDCPNIFTY-INDEX': 'MIDCPNIFTY'}.get(sym, sym.split(':')[1].rsplit('-', 1)[0])
    return LOTS.get(root, 1000)

def load(ds):
    f = W + ('out/live_options.csv' if ds == 'live' else f'out/{ds}_options.csv')
    d = pd.read_csv(f)
    if ds == 'live':
        spot = pd.read_csv(W + 'data/spot_min.csv', usecols=['symbol', 'ts'])
        days = sorted(set(pd.to_datetime(spot.ts + 19800, unit='s').dt.date.astype(str)))
        days = [x for x in days if x <= LAST_LIVE]
        nxt = lambda d, k: days[days.index(d) + k] if d in days and days.index(d) + k < len(days) else '9999'
        for k, h in ((1, 'd1'), (2, 'd2'), (3, 'd3')):
            bad = d['day'].map(lambda x: nxt(x, k) > LAST_LIVE)
            for side in ('ce', 'pe'): d.loc[bad, f'{side}_{h}'] = np.nan
        bad = d['day'].map(lambda x: nxt(x, 3) > LAST_LIVE)
        for side in ('ce', 'pe'): d.loc[bad, f'{side}_st_exit'] = np.nan
    d['cost'] = 0.002 + 40 / (d['ce_entry'] * d['sym'].map(lambda s: lot(s, ds)))
    d['month'] = d['day'].str[:7]
    return d

def tday(x, g):
    df = pd.DataFrame({'x': x, 'g': g}).dropna()
    if len(df) < 3: return np.nan, np.nan
    per = df.groupby('g').x.mean()
    sd = per.std(ddof=1)
    return df.x.mean(), (per.mean() / (sd / np.sqrt(len(per))) if len(per) > 2 and sd > 0 else np.nan)

def describe(d, col, net=True):
    x = d[col] - (d['cost'] if net else 0)
    x = x.dropna()
    if len(x) < 3: return None
    w, l = x[x > 0], x[x <= 0]
    pos = x.clip(lower=0).sort_values(ascending=False)
    mu, t = tday(d[col] - (d['cost'] if net else 0), d['day'])
    return {'n': len(x), 'win': round((x > 0).mean(), 3), 'mean': round(mu, 3), 'median': round(x.median(), 3), 't_days': round(t, 2),
            'payoff': round(w.mean() / -l.mean(), 2) if len(w) and len(l) and l.mean() < 0 else None,
            'top5_share_of_gains': round(pos.head(5).sum() / pos.sum(), 2) if pos.sum() > 0 else None}

out = {}
for ds in sys.argv[1:]:
    d = load(ds)
    sig, ctl = d[d.kind == 'signal'], d[d.kind == 'control']
    r = {'n_signals': len(sig), 'n_controls': len(ctl), 'days': sig['day'].nunique(), 'horizons': {}}
    for h in HZ:
        ce, pe = f'ce_{h}', f'pe_{h}'
        row = {'CE signal (net)': describe(sig, ce), 'CE control (net)': describe(ctl, ce),
               'CE signal gross': describe(sig, ce, net=False)}
        # paired side check and half straddle, at the same moment
        s = sig.dropna(subset=[ce, pe])
        if len(s) > 2:
            row['CE minus PE (signal)'] = {'mean': round((s[ce] - s[pe]).mean(), 3), 'CE better %': round(((s[ce] - s[pe]) > 0).mean(), 3),
                                           't_days': round(tday((s[ce] - s[pe]).to_numpy(), s['day'].to_numpy())[1], 2)}
            row['CE minus half straddle'] = round((s[ce] - (s[ce] + s[pe]) / 2).mean(), 3)
        # excess over the matched controls of the same event
        cm = ctl.groupby('group')[ce].mean()
        j = sig.set_index('group')[[ce, 'day']].join(cm.rename('c'), how='inner').dropna()
        if len(j) > 2:
            mu, t = tday((j[ce] - j['c']).to_numpy(), j['day'].to_numpy())
            row['CE excess vs matched control'] = {'n': len(j), 'mean': round(mu, 3), 't_days': round(t, 2)}
        r['horizons'][h] = row
    # sustained: how far did the CE run before D+3, and how often did it keep a gain
    r['mfe_reach'] = {f'>= +{int(k*100)}%': round((sig['ce_mfe'] >= k).mean(), 3) for k in (0.1, 0.2, 0.5, 1.0)}
    r['mfe_reach_control'] = {f'>= +{int(k*100)}%': round((ctl['ce_mfe'] >= k).mean(), 3) for k in (0.1, 0.2, 0.5, 1.0)}
    held = sig.dropna(subset=['ce_close', 'ce_d1', 'ce_d2', 'ce_d3'])
    r['kept_gain_all_of_close_d1_d2_d3'] = {'n': len(held), 'share': round(((held[['ce_close', 'ce_d1', 'ce_d2', 'ce_d3']] > held['cost'].to_numpy()[:, None]).all(axis=1)).mean(), 3) if len(held) else None}
    r['by_month_d1_net'] = {m: describe(g, 'ce_d1') for m, g in sig.groupby('month')}
    r['by_month_close_net'] = {m: describe(g, 'ce_close') for m, g in sig.groupby('month')}
    r['moneyness_mean'] = round(sig['ce_mny'].mean(), 4)
    out[ds] = r
json.dump(out, open(W + 'out/results.json', 'w'), indent=1, default=float)
for ds, r in out.items():
    print(f"\n######## {ds}: {r['n_signals']} signals on {r['days']} days, {r['n_controls']} controls; mean CE moneyness {r['moneyness_mean']}")
    for h, row in r['horizons'].items():
        s, c, g = row['CE signal (net)'], row['CE control (net)'], row['CE signal gross']
        f = lambda z: f"n{z['n']:4d} win {z['win']:.2f} mean {z['mean']:+.3f} med {z['median']:+.3f} t {z['t_days']:+.1f} payoff {z['payoff']}" if z else '-'
        print(f"{h:8s} SIGNAL {f(s)} | gross mean {g['mean'] if g else '-'} | CONTROL {f(c)} | CE-PE {row.get('CE minus PE (signal)')} | vs ctrl {row.get('CE excess vs matched control')}")
    print('MFE reach signal', r['mfe_reach'], 'control', r['mfe_reach_control'])
    print('kept a net gain at close, D1, D2 and D3:', r['kept_gain_all_of_close_d1_d2_d3'])
    print('by month (D1 net):', {m: (v['n'], v['win'], v['mean']) if v else None for m, v in r['by_month_d1_net'].items()})
