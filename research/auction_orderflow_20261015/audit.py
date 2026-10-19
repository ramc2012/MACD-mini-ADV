"""Is the desk's order flow measuring something real? Per-day audit of the minute flow. Read-only."""
import sqlite3
import numpy as np, pandas as pd

pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
f = pd.read_csv('/w/data/spot_flow.csv.gz')
f['day'] = pd.to_datetime(f.minute_ts + 19800, unit='s').dt.date.astype(str)
f['kind'] = np.where(f.symbol.str.endswith('-INDEX'), 'index', np.where(f.symbol.str.contains('FUT'), 'future', 'stock'))
f = f.sort_values(['symbol', 'minute_ts'])
f['ret'] = f.groupby(['symbol', 'day']).close.pct_change()
f['classified'] = f.buy_volume + f.sell_volume

print('A. coverage and classification by day (stocks)')
s = f[f.kind == 'stock']
g = s.groupby('day').agg(symbols=('symbol', 'nunique'), minutes=('minute_ts', 'size'), src=('src', 'first'),
                         volume=('volume', 'sum'), classified=('classified', 'sum'), quote=('quote_n', 'sum'), mid=('mid_n', 'sum'),
                         tick=('tick_n', 'sum'), zero=('zero_tick_n', 'sum'), pending=('pending_n', 'sum'), conflict=('conflict_n', 'sum'),
                         unclassified=('unclassified', 'sum'), conf=('avg_confidence', 'median'))
g['classified_%'] = 100 * g.classified / g.volume
votes = g[['quote', 'mid', 'tick', 'zero', 'pending', 'conflict', 'unclassified']]
for c in votes.columns:
    g[c + '_%'] = 100 * votes[c] / votes.sum(axis=1)
print(g[['symbols', 'minutes', 'src', 'classified_%', 'quote_%', 'mid_%', 'tick_%', 'zero_%', 'pending_%', 'conflict_%', 'unclassified_%', 'conf']].round(1).to_string())

print('\nB. does minute delta move with price? (stocks; per symbol-day correlation of delta/volume with the minute return)')
def stats(x):
    x = x.dropna(subset=['ret'])
    x = x[x.volume > 0]
    if len(x) < 60:
        return pd.Series({'corr': np.nan, 'sign_agree': np.nan, 'ofi_corr': np.nan})
    d = x.delta / x.volume
    agree = (np.sign(d) == np.sign(x.ret))[(d != 0) & (x.ret != 0)].mean()
    return pd.Series({'corr': np.corrcoef(d, x.ret)[0, 1], 'sign_agree': agree,
                      'ofi_corr': np.corrcoef(x.ofi.fillna(0), x.ret)[0, 1] if x.ofi.notna().any() and x.ofi.std() > 0 else np.nan})
sd = s.groupby(['day', 'symbol']).apply(stats)
print(sd.groupby('day').median().round(3).to_string())
print('share of symbol-days with corr > 0.2:', round((sd['corr'] > 0.2).mean(), 3), ' corr < 0:', round((sd['corr'] < 0).mean(), 3))

print('\nC. indices and futures: what the flow carries')
print(f[f.kind != 'stock'].groupby(['kind', 'day']).agg(symbols=('symbol', 'nunique'), minutes=('minute_ts', 'size'),
      volume=('volume', 'sum'), classified=('classified', 'sum'), ofi_events=('ofi_events', 'sum')).to_string())

print('\nD. raw-tick quality (12-14 Oct)')
q = pd.read_csv('/w/data/raw_quality.csv')
q['kind'] = np.where(q.symbol.str.endswith('-INDEX'), 'index', 'stock')
q['two_sided_%'] = 100 * q.two_sided / q.ticks; q['volume_step_%'] = 100 * q.with_volume_step / q.ticks
print(q.groupby(['kind', 'day'])[['ticks', 'two_sided_%', 'volume_step_%']].median().round(1).to_string())

print('\nE. tick-derived volume vs the exchange minute candle (stocks, raw days)')
h = sqlite3.connect('file:/data/historical.sqlite3?mode=ro', uri=True)
raw = s[s.src == 'raw']
rows = []
for sym, x in raw.groupby('symbol'):
    c = pd.DataFrame(h.execute('SELECT timestamp, volume FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp BETWEEN ? AND ?',
                               (sym, int(x.minute_ts.min()), int(x.minute_ts.max()))).fetchall(), columns=['minute_ts', 'candle_volume'])
    m = x.merge(c, on='minute_ts')
    if len(m) > 100 and m.candle_volume.sum() > 0:
        rows.append({'symbol': sym, 'ratio': m.volume.sum() / m.candle_volume.sum(), 'minute_corr': np.corrcoef(m.volume, m.candle_volume)[0, 1]})
e = pd.DataFrame(rows)
print(e[['ratio', 'minute_corr']].describe(percentiles=[.1, .5, .9]).round(3).to_string())
sd.to_csv('/w/out/flow_price_corr.csv')
