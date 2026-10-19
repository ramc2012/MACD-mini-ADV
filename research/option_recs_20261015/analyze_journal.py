"""Tables for FINDINGS.md from data/journal_feat.csv (live blast journal, 29 Sep - 14 Oct 2026)."""
import numpy as np, pandas as pd

pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
f = pd.read_csv('/w/data/journal_feat.csv')
f = f[f.ret_cur.notna()].copy()
f['adelta'] = f.delta.abs()
f['doubled'] = np.where(f.resolved == 1, (f.mfe >= 100).astype(float), np.nan)
f['hit50'] = np.where(f.resolved == 1, (f.mae <= -50).astype(float), np.nan)
f['va_side'] = np.where(f.side == 'CE', f.va_pos, 1 - f.va_pos)   # >1: beyond the value area in the trade's direction
f['screen'] = np.where(f.reason.isin(['TAKEN', 'ENTRY_REJECTED']), 'passed screen', 'declined (breadth/off-high)')

# A. simulator check against the lane's own fills
L = pd.read_csv('/w/data/live_blast_closed.csv')
L['t'] = pd.to_datetime(L.entry_time, format='ISO8601').astype('int64') // 10**9
f['t0'] = pd.to_datetime(f.day) if False else None
tk = f[f.reason == 'TAKEN'].copy()
j = pd.read_csv('/w/data/journal_feat.csv')
print('A. live closed positions', len(L), 'mean return %', round(L.return_pct.mean(), 2))
m = L.merge(tk[['symbol', 'ret_cur', 'ret_cur_cons']], on='symbol', how='inner').drop_duplicates('symbol')
print(f'   matched by contract: {len(m)}; live mean {m.return_pct.mean():.1f}%  replay optimistic {m.ret_cur.mean()*100:.1f}%  '
      f'conservative {m.ret_cur_cons.mean()*100:.1f}%  corr(live, cons) {np.corrcoef(m.return_pct, m.ret_cur_cons)[0,1]:.2f}')


def day_t(a, b, day):
    d = (a - b).groupby(day).mean()
    se = d.std(ddof=1) / np.sqrt(len(d))
    return d.mean(), (d.mean() / se if se > 0 else np.nan), int((d > 1e-9).sum()), int((d < -1e-9).sum())


print('\nB. exit variants, paired on the same rows (pp = mean change in return per trade vs the current rule)')
names = {'r22': 'ratchet to BE at +22%', 'r15': 'ratchet to BE at +15%', 't8': 'time stop 8 bars (<+10%)',
         't4': 'time stop 4 bars (<+10%)', 'r22t8': 'ratchet 22 + time stop 8', 'stop35': 'stop -35%'}
rows = []
for pop, g in [('all 407 premium-cleared', f), ('passed screen (TAKEN/REJECTED)', f[f.screen == 'passed screen'])]:
    for fill in ('', '_cons'):
        base = g[f'ret_cur{fill}']
        rows.append({'population': pop, 'fill': fill or '_opt', 'variant': 'current', 'n': len(g), 'mean_%': base.mean() * 100})
        for k, v in names.items():
            mu, tt, up, dn = day_t(g[f'ret_{k}{fill}'], base, g.day)
            rows.append({'population': pop, 'fill': fill or '_opt', 'variant': v, 'n': len(g), 'mean_%': g[f'ret_{k}{fill}'].mean() * 100,
                         'vs_cur_pp': (g[f'ret_{k}{fill}'] - base).mean() * 100, 'day_t': tt, 'days_up': up, 'days_down': dn})
print(pd.DataFrame(rows).round(2).to_string(index=False))


def buckets(col, edges, labels, title):
    g = f.copy(); g['b'] = pd.cut(g[col], edges, labels=labels)
    t = g.groupby('b', observed=True).agg(n=('ret_cur', 'size'), days=('day', 'nunique'), ret_cur=('ret_cur', 'mean'),
                                          ret_cons=('ret_cur_cons', 'mean'), doubled=('doubled', 'mean'), hit_minus50=('hit50', 'mean'),
                                          passed=('screen', lambda s: (s == 'passed screen').sum()))
    print(f'\n{title}'); print(t.round(3).to_string())


print('\nC. features at entry vs outcome (ret_* = current exit; doubled / hit_minus50 from the 48 h journal watch)')
buckets('adelta', [0, .15, .25, .40, .60, 1], ['<0.15', '0.15-0.25', '0.25-0.40', '0.40-0.60', '>0.60'], '|delta|')
buckets('iv_rv', [0, 1.0, 1.3, 1.7, 50], ['IV<RV', '1.0-1.3', '1.3-1.7', '>1.7'], 'IV / RV20')
buckets('dte', [0, 3, 7, 14, 60], ['1-3', '4-7', '8-14', '15+'], 'days to expiry')
buckets('spread_pct', [-1, 1, 2, 5, 1000], ['<1%', '1-2%', '2-5%', '>5%'], 'bid-ask spread at entry, % of mid')
buckets('flow_signed', [-2, -0.05, 0.05, 2], ['against side', 'flat', 'with side'], "underlying's 30-min order-flow delta, signed by side (stocks, condensed days)")
buckets('va_side', [-50, 0, 0.25, 0.75, 1, 50], ['beyond VA against side', 'VA quarter against', 'middle half', 'VA quarter with side', 'beyond VA with side'], "spot vs previous session's value area, signed by option side")
buckets('iv', [0, 0.18, 0.24, 0.32, 5], ['<18%', '18-24%', '24-32%', '>32%'], 'implied volatility level (theta per gamma is ~IV squared under Black-Scholes)')

print('\nD. proposed filters: rows each would BLOCK vs rows it KEEPS (current exit; day-paired where both exist)')
f['side_sign'] = np.where(f.side == 'CE', 1, -1)
f['breakout'] = np.where(f.side == 'CE', f.va_pos > 1, f.va_pos < 0)
rules = {
    'Blast delta band 0.25-0.40 (block outside)': ~f.adelta.between(0.25, 0.40),
    'IV_TOO_EXPENSIVE: IV/RV > 1.5': f.iv_rv > 1.5,
    'SPREAD_TOO_WIDE > 3%': f.spread_pct > 3,
    'flow not aligned (stocks with flow data)': (f.flow_signed <= 0),
    'spot in middle half of value area': f.va_pos.between(0.25, 0.75),
}
rows = []
for name, block in rules.items():
    valid = f[name.startswith('flow') and f.flow_signed.notna() or True] if False else f
    if name.startswith('flow'):
        valid = f[f.flow_signed.notna()]; block = block[valid.index]
    for fill in ('', '_cons'):
        col = f'ret_cur{fill}'
        kb, kk = valid[block.astype(bool)], valid[~block.astype(bool)]
        dd = pd.DataFrame({'b': kb.groupby('day')[col].mean(), 'k': kk.groupby('day')[col].mean()}).dropna()
        diff = dd.k - dd.b
        se = diff.std(ddof=1) / np.sqrt(len(diff)) if len(diff) > 1 else np.nan
        rows.append({'rule': name, 'fill': fill or '_opt', 'blocked_n': len(kb), 'kept_n': len(kk),
                     'blocked_mean_%': kb[col].mean() * 100, 'kept_mean_%': kk[col].mean() * 100,
                     'keep_minus_block_pp': diff.mean() * 100, 'day_t': diff.mean() / se if se and se > 0 else np.nan, 'days': len(diff),
                     'blocked_doubled': kb.doubled.mean(), 'kept_doubled': kk.doubled.mean()})
print(pd.DataFrame(rows).round(3).to_string(index=False))

print('\nE. delta by days-to-expiry among TAKEN rows (the "lottery ticket" claim)')
tk = f[f.reason == 'TAKEN']
print(tk.groupby(pd.cut(tk.dte, [0, 3, 7, 14, 60]), observed=True).adelta.describe()[['count', 'min', '25%', '50%', '75%', 'max']].round(2).to_string())
print('\n|delta| of all rows by side:', f.groupby('side').adelta.median().round(2).to_dict(), ' median IV', round(f.iv.median(), 3),
      ' median RV20', round(f.rv20.median(), 3), ' median spread %', round(f.spread_pct.median(), 2))
