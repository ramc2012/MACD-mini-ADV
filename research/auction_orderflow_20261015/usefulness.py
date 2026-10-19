"""Do the desk's auction / order-flow readings help paper entries? Read-only.

Features (underlying, from the minute flow, using only minutes completed BEFORE the entry):
  d15, d30   classified buy-sell volume over the last 15/30 minutes / volume, signed by the option side (CE +1, PE -1)
  cvd_day    session cumulative delta / session volume, signed
  ofi15      sum(OFI) / sum(|OFI|) over 15 minutes, signed
  vwap_dev   spot vs session VWAP (from the same minutes), signed
Index options use the index FUTURE's flow (the spot carries no volume or quotes).
Outcomes:
  MP signals  real option 1-minute bars: lane exits (-25% stop, 20% trail from +20%, 60-minute time exit) and the opposite
              option at the same strike/expiry; plus the underlying's next-60-minute move, signed
  lane trades the realised return of each entry (FIFO from the trade ledger)
Statistics are clustered by day (few days: read the day counts).
"""
import re, sqlite3
import numpy as np, pandas as pd

pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
IST = 19800
MONTHLY = re.compile(r"^(?P<root>[A-Z0-9&\-]+?)(?P<yy>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
WEEKLY = re.compile(r"^(?P<root>[A-Z&\-]+?)(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")

f = pd.read_csv('/w/data/spot_flow.csv.gz').sort_values(['symbol', 'minute_ts'])
f['day'] = (f.minute_ts + IST) // 86400
futs = sorted(s for s in f.symbol.unique() if 'FUT' in s)
print('futures in the tick store:', futs)
def fut_for(root):
    for s in futs:
        body = s.split(':')[1]
        if root == 'NIFTY' and body.startswith('NIFTY') and not body.startswith(('NIFTYBANK', 'NIFTYMID')): return s
        if root == 'BANKNIFTY' and body.startswith('BANKNIFTY'): return s
        if root == 'SENSEX' and body.startswith('SENSEX'): return s
    return None
g = f.groupby(['symbol', 'day'], sort=False)
for n in (15, 30):
    f[f'dv{n}'] = g.delta.transform(lambda s: s.rolling(n, min_periods=n // 2).sum())
    f[f'v{n}'] = g.volume.transform(lambda s: s.rolling(n, min_periods=n // 2).sum())
f['cd'] = g.delta.cumsum(); f['cv'] = g.volume.cumsum()
f['ofi_s'] = g.ofi.transform(lambda s: s.fillna(0).rolling(15, min_periods=7).sum())
f['ofi_a'] = g.ofi.transform(lambda s: s.fillna(0).abs().rolling(15, min_periods=7).sum())
f['pv'] = (f.close * f.volume); f['cpv'] = f.groupby(['symbol', 'day']).pv.cumsum()
f['d15'] = f.dv15 / f.v15; f['d30'] = f.dv30 / f.v30; f['cvd_day'] = f.cd / f.cv
f['ofi15'] = f.ofi_s / f.ofi_a; f['vwap_dev'] = f.close / (f.cpv / f.cv) - 1
f['fwd30'] = g.close.shift(-30) / f.close - 1; f['fwd60'] = g.close.shift(-60) / f.close - 1
FEAT = ['d15', 'd30', 'cvd_day', 'ofi15', 'vwap_dev']
idx = f.set_index(['symbol', 'minute_ts'])

# ---- 1. does flow lead price at all? every 5th stock minute, signed features unsigned here
s = f[f.symbol.str.endswith('-EQ') & (f.minute_ts % 300 == 0)].dropna(subset=['d15', 'fwd30'])
print('\n1. stock minutes: next-30/60-minute return by quintile of the trailing 15-minute delta share (bp)')
s['q'] = s.groupby('day').d15.transform(lambda x: pd.qcut(x.rank(method='first'), 5, labels=False))
t1 = s.groupby('q')[['fwd30', 'fwd60']].mean() * 1e4
print(t1.round(1).to_string())
per_day = s.groupby(['day', 'q']).fwd30.mean().unstack()
spread = (per_day[4] - per_day[0]) * 1e4
print(f'   top-minus-bottom fwd30: {spread.mean():+.1f} bp, day t {spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread))):.2f}, days {len(spread)} '
      f'(positive on {(spread > 0).sum()})')
for col in ('cvd_day', 'ofi15', 'vwap_dev'):
    x = s.dropna(subset=[col]); x['q'] = x.groupby('day')[col].transform(lambda v: pd.qcut(v.rank(method='first'), 5, labels=False))
    d = x.groupby(['day', 'q']).fwd30.mean().unstack(); sp = (d[4] - d[0]) * 1e4
    print(f'   {col:9s} top-minus-bottom fwd30 {sp.mean():+.1f} bp, day t {sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp))):.2f}')


def place(symbol, ts):
    body = symbol.split(':', 1)[1]
    m = MONTHLY.match(body) or WEEKLY.match(body)
    if not m: return None
    root = m['root']
    spot = {'NIFTY': fut_for('NIFTY'), 'BANKNIFTY': fut_for('BANKNIFTY'), 'SENSEX': fut_for('SENSEX')}.get(root, f'NSE:{root}-EQ')
    if root == 'MIDCPNIFTY': return None
    side = 1 if m['right'] == 'CE' else -1
    minute = (int(ts) // 60) * 60 - 60                          # last completed minute
    try:
        r = idx.loc[(spot, minute)]
    except KeyError:
        return None
    out = {'root': root, 'side': side, 'index': root in ('NIFTY', 'BANKNIFTY', 'SENSEX'), 'strike': float(m['strike']), 'right': m['right']}
    for c in FEAT:
        out[c] = r[c] * side if pd.notna(r[c]) else np.nan
    out['spot_fwd60'] = r.fwd60 * side if pd.notna(r.fwd60) else np.nan
    out['spot_fwd30'] = r.fwd30 * side if pd.notna(r.fwd30) else np.nan
    return out


def bucket_report(df, ret, title, feats=FEAT):
    print(f'\n{title}')
    rows = []
    for c in feats:
        x = df.dropna(subset=[c, ret])
        if len(x) < 30: continue
        x = x.assign(b=pd.cut(x[c], [-np.inf, -0.05, 0.05, np.inf] if c != 'vwap_dev' else [-np.inf, -0.002, 0.002, np.inf],
                              labels=['against', 'flat', 'with']))
        m = x.groupby('b', observed=True)[ret].agg(['size', 'mean'])
        dd = x.groupby(['day', 'b'], observed=True)[ret].mean().unstack()
        diff = (dd.get('with') - dd.get('against')).dropna() if {'with', 'against'} <= set(dd.columns) else pd.Series(dtype=float)
        se = diff.std(ddof=1) / np.sqrt(len(diff)) if len(diff) > 1 else np.nan
        rows.append({'feature': c, 'n_against': m['size'].get('against', 0), 'mean_against_%': 100 * m['mean'].get('against', np.nan),
                     'n_with': m['size'].get('with', 0), 'mean_with_%': 100 * m['mean'].get('with', np.nan),
                     'with_minus_against_pp': 100 * diff.mean() if len(diff) else np.nan, 'day_t': diff.mean() / se if se and se > 0 else np.nan,
                     'days': len(diff)})
    print(pd.DataFrame(rows).round(2).to_string(index=False))


# ---- 2. MP signals
h = sqlite3.connect('file:/data/historical.sqlite3?mode=ro', uri=True)
def bars(sym, a, b):
    return h.execute('SELECT timestamp, open, high, low, close FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<=? ORDER BY timestamp', (sym, a, b)).fetchall()
def lane_exit(bs, hard=0.25, act=0.20, trail=0.20, max_min=60):
    e = bs[0][1]; peak = e; floor = 0
    for k, (ts, o, hi, lo, cl) in enumerate(bs):
        if k >= max_min:
            return o / e - 1
        stop = max(e * (1 - hard), floor)
        if lo <= stop:
            return min(o, stop) / e - 1
        peak = max(peak, hi)
        if peak >= e * (1 + act):
            floor = max(floor, peak * (1 - trail), e * 1.005)
    return bs[-1][4] / e - 1
sig = pd.read_csv('/w/data/mp_signals.csv')
sig['ts'] = pd.to_datetime(sig.timestamp, format='ISO8601').astype('int64') // 10**9
sig['setup'] = sig.kind.str.replace('MP_', '').str.replace('_CE', '').str.replace('_PE', '')
rows = []
for r in sig.itertuples():
    p = place(r.symbol, r.ts)
    if p is None: continue
    eod = (r.ts + IST) // 86400 * 86400 - IST + 15 * 3600 + 20 * 60 - IST + IST
    eod = int(pd.Timestamp(pd.Timestamp(r.ts, unit='s', tz='UTC').tz_convert('Asia/Kolkata').date().isoformat() + ' 15:20', tz='Asia/Kolkata').timestamp())
    bs = bars(r.symbol, r.ts, eod)
    if len(bs) < 3 or bs[0][0] - r.ts > 180 or bs[0][1] <= 0: continue
    opp = r.symbol[:-2] + ('PE' if r.symbol.endswith('CE') else 'CE')
    ob = bars(opp, r.ts, eod)
    rows.append({**p, 'day': (r.ts + IST) // 86400, 'setup': r.setup, 'minute': ((r.ts + IST) % 86400) // 60,
                 'ret': lane_exit(bs) - 0.005, 'opp': (lane_exit(ob) - 0.005) if len(ob) >= 3 and abs(ob[0][0] - bs[0][0]) <= 180 and ob[0][1] > 0 else np.nan})
S = pd.DataFrame(rows)
print(f'\n2. MP signals scored: {len(S)} of {len(sig)} ({S.day.nunique()} days); costs 0.5% round trip')
t2 = S.groupby('setup').agg(n=('ret', 'size'), days=('day', 'nunique'), ret_pct=('ret', 'mean'), win=('ret', lambda x: (x > 0).mean()),
                            opposite_pct=('opp', 'mean'), spot_fwd60_bp=('spot_fwd60', 'mean'))
t2[['ret_pct', 'opposite_pct']] *= 100; t2['spot_fwd60_bp'] *= 1e4
print(t2.sort_values('n', ascending=False).round(2).to_string())
allx = S.groupby('day')[['ret', 'opp', 'spot_fwd60']].mean()
print(f"   all signals: option {100*S.ret.mean():+.2f}%  opposite {100*S.opp.mean():+.2f}%  spot next 60 min signed {1e4*S.spot_fwd60.mean():+.1f} bp "
      f"(days with spot moving the signal's way: {(allx.spot_fwd60 > 0).sum()} of {len(allx)})")
print('   stock vs index-future flow:', S.groupby('index').agg(n=('ret', 'size'), ret=('ret', 'mean'), d15_missing=('d15', lambda x: x.isna().mean())).round(3).to_dict())
bucket_report(S[~S['index']], 'ret', 'MP signals on stocks: option return (lane exits) by the flow at the signal, signed by side')
bucket_report(S[~S['index']], 'spot_fwd60', "MP signals on stocks: the underlying's next 60 minutes (signed) by the flow at the signal")

# ---- 3. MP round trips actually taken
rt = pd.read_csv('/w/data/mp_round_trips.csv')
rt['ts'] = pd.to_datetime(rt.entry_ts, format='ISO8601').astype('int64') // 10**9
R = pd.DataFrame([{**(place(r.symbol, r.ts) or {}), 'day': (r.ts + IST) // 86400, 'ret': r.ret, 'setup': str(r.kind)} for r in rt.itertuples()])
print(f'\n3. MP round trips taken: {len(R)}, mean {100*R.ret.mean():+.2f}%, win {100*(R.ret > 0).mean():.0f}%')
bucket_report(R.dropna(subset=['side']), 'ret', 'MP round trips: realised return by flow at entry')

# ---- 4. other lanes
L = pd.read_csv('/w/data/lane_entries.csv')
L['ts'] = pd.to_datetime(L.entry_ts, format='ISO8601').astype('int64') // 10**9
X = pd.DataFrame([{**(place(r.symbol, r.ts) or {}), 'lane': r.lane, 'day': (r.ts + IST) // 86400, 'ret': r.ret} for r in L.itertuples()])
X = X.dropna(subset=['side'])
print(f'\n4. other lanes, entries with flow at entry: {len(X)}; by lane', X.groupby('lane').ret.agg(['size', 'mean']).round(3).to_dict())
stock = X[~X['index'].astype(bool)]
bucket_report(stock, 'ret', 'MACD + smart + blast entries on stock options: realised return by underlying flow at entry')
bucket_report(stock[stock.lane == 'macd_trader'], 'ret', 'MACD lane only')
S.to_csv('/w/out/mp_signal_features.csv', index=False); X.to_csv('/w/out/lane_entry_features.csv', index=False)
