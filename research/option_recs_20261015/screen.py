"""Replay the blast screen on stored bars, 20 Aug - 14 Oct 2026, and write every signal-line cross.

Mirrors blast_engine on its live settings (30-minute signal bars aligned to 09:15, MACD 12/26/9 seeded at the
first close, premium/spot <= 1.2%, same-side breadth >= 0.50 over >= 20 contracts, >= 25% below the
two-session high, >= 120 minutes of history). Approximations, stated in FINDINGS.md: the recent high is the
max of the previous 25 thirty-minute highs (750 minutes), breadth is read off thirty-minute MACD, and the
VIX-adaptive premium allowance and the liquidity cap are not replayed (this is the "legacy screen" the smart
lanes consume).

Output: out/crosses.csv.gz, one row per cross with every leg's input and the first failing leg.
"""
import re
import numpy as np, pandas as pd

BAND_FROM = pd.Timestamp('2026-08-20', tz='Asia/Kolkata')
MONTHLY = re.compile(r"^(?P<root>[A-Z0-9&\-]+?)(?P<yy>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
WEEKLY = re.compile(r"^(?P<root>[A-Z&\-]+?)(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
INDEX = {"NIFTY": "NSE:NIFTY50-INDEX", "SENSEX": "BSE:SENSEX-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX", "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX"}


def parse(sym):
    body = sym.split(':', 1)[1]
    m = MONTHLY.match(body) or WEEKLY.match(body)
    if not m:
        return None, None, None
    return m['root'], float(m['strike']), m['right']


o = pd.read_csv('/w/data/opt30.csv.gz')
# extract.py read each contract once per distinct (symbol, expiry) pair, and 3,137 contracts are stored with
# both a set and a blank expiry: keep one row per contract-bar and the contract's known expiry.
o['expiry'] = o.groupby('symbol').expiry.transform('first')
o = o.drop_duplicates(['symbol', 'bucket'])
meta = o[['symbol']].drop_duplicates()
meta[['root', 'strike', 'right']] = meta.symbol.apply(lambda s: pd.Series(parse(s)))
o = o.merge(meta, on='symbol').dropna(subset=['root'])
o['spot_sym'] = o.root.map(lambda r: INDEX.get(r, f'NSE:{r}-EQ'))
o = o.sort_values(['symbol', 'bucket']).reset_index(drop=True)
g = o.groupby('symbol', sort=False)
ema = lambda s, n: s.ewm(span=n, adjust=False).mean()
o['macd'] = g.close.transform(lambda s: ema(s, 12) - ema(s, 26))
o['sig'] = o.groupby('symbol', sort=False).macd.transform(lambda s: ema(s, 9))
o['pmacd'] = g.macd.shift(); o['psig'] = g.sig.shift()
o['nbar'] = g.cumcount()
o['minutes_before'] = g.minutes.transform(lambda s: s.shift().fillna(0).cumsum())
o['recent_high'] = g.high.transform(lambda s: s.shift().rolling(25, min_periods=1).max())
o['day'] = pd.to_datetime(o.bucket, unit='s', utc=True).dt.tz_convert('Asia/Kolkata').dt.normalize()

# breadth: same-side share of contracts with a bar in the bucket and MACD > 0 (warm contracts only)
warm = o[o.nbar >= 26]
br = warm.groupby(['bucket', 'right']).agg(n=('macd', 'size'), up=('macd', lambda s: (s > 0).mean())).reset_index()
o = o.merge(br, on=['bucket', 'right'], how='left')

s = pd.read_csv('/w/data/spot30.csv.gz')[['symbol', 'bucket', 'close']].rename(columns={'symbol': 'spot_sym', 'close': 'spot'})
o = o.merge(s, on=['spot_sym', 'bucket'], how='left')

x = o[(o.pmacd <= o.psig) & (o.macd > o.sig) & (o.day >= BAND_FROM)].copy()
exp = pd.to_datetime(x.expiry).dt.tz_localize('Asia/Kolkata')
x['dte_cal'] = (exp - x.day).dt.days
x = x[x.dte_cal >= 1]                                       # min_days_to_expiry = 1: no expiry-day contracts
x['premium_pct'] = x.close / x.spot * 100
x['off_high_pct'] = (x.close / x.recent_high - 1) * 100


def leg(r):
    if not r.spot > 0: return 'NO_SPOT'
    if not r.n >= 20: return 'NO_BREADTH'
    if r.minutes_before < 120: return 'NO_HISTORY'
    if r.premium_pct > 1.2: return 'PREMIUM_TOO_RICH'
    if r.up < 0.5: return 'BREADTH_TOO_THIN'
    if r.off_high_pct > -25: return 'NOT_OFF_HIGH'
    return 'PASS'


x['verdict'] = x.apply(leg, axis=1)
x['signal_end'] = x.bucket + 1800                           # entry is the first minute after the signal bar
cols = ['symbol', 'root', 'right', 'strike', 'expiry', 'spot_sym', 'bucket', 'signal_end', 'close', 'spot', 'premium_pct',
        'up', 'n', 'off_high_pct', 'minutes_before', 'macd', 'sig', 'dte_cal', 'volume', 'verdict']
x[cols].rename(columns={'close': 'premium', 'up': 'breadth', 'n': 'cohort'}).to_csv('/w/out/crosses.csv.gz', index=False)
print(x.verdict.value_counts())
print('PASS by day:', x[x.verdict == 'PASS'].groupby(x.day.dt.date).size().describe().round(1).to_dict())
