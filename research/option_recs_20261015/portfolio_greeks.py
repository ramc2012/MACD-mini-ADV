"""Greeks of the open MACD and blast books at the 14 Oct 2026 close (Black-Scholes, r 6.5%)."""
import json, math, re
import numpy as np, pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

R = 0.065
MONTHLY = re.compile(r"^(?P<root>[A-Z0-9&\-]+?)(?P<yy>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
WEEKLY = re.compile(r"^(?P<root>[A-Z&\-]+?)(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
INDEX = {"NIFTY": "NSE:NIFTY50-INDEX", "SENSEX": "BSE:SENSEX-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX", "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX"}
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
spot = pd.read_csv('/w/data/spot1d.csv.gz').sort_values('day0').groupby('symbol').close.last()
now = pd.Timestamp('2026-10-14 15:30', tz='Asia/Kolkata')


def expiry(m, exch):
    if 'mon' in m.groupdict():
        y, mo = 2000 + int(m['yy']), MON.index(m['mon']) + 1
        d = pd.Timestamp(y, mo, 1) + pd.offsets.MonthEnd(0)
        while d.weekday() != (3 if exch == 'BSE' else 1):
            d -= pd.Timedelta(days=1)
        return d
    mo = {"O": 10, "N": 11, "D": 12}.get(m['m']) or int(m['m'])
    return pd.Timestamp(2000 + int(m['yy']), mo, int(m['dd']))


rows = []
for book, d in json.load(open('/w/data/open_positions_20261014.json')).items():
    for p in d['positions']:
        exch, body = p['symbol'].split(':', 1)
        m = MONTHLY.match(body) or WEEKLY.match(body)
        if not m:
            continue
        S = spot.get(INDEX.get(m['root'], f"NSE:{m['root']}-EQ")); K = float(m['strike']); cp = m['right']
        T = ((expiry(m, exch).tz_localize('Asia/Kolkata') + pd.Timedelta(hours=15, minutes=30)) - now).total_seconds() / (365 * 86400)
        prem = p['last_price']; q = p['quantity']
        def price(s):
            d1 = (math.log(S / K) + (R + s * s / 2) * T) / (s * math.sqrt(T)); d2 = d1 - s * math.sqrt(T)
            return (S * norm.cdf(d1) - K * math.exp(-R * T) * norm.cdf(d2)) if cp == 'CE' else (K * math.exp(-R * T) * norm.cdf(-d2) - S * norm.cdf(-d1))
        try:
            iv = brentq(lambda s: price(s) - prem, 1e-3, 5)
        except (ValueError, ZeroDivisionError):
            iv = np.nan
        if iv == iv:
            d1 = (math.log(S / K) + (R + iv * iv / 2) * T) / (iv * math.sqrt(T))
            delta = norm.cdf(d1) - (0 if cp == 'CE' else 1)
            theta = -S * norm.pdf(d1) * iv / (2 * math.sqrt(T)) / 365
            vega = S * norm.pdf(d1) * math.sqrt(T) / 100
        else:
            delta = (1 if cp == 'CE' else -1) if prem > 0 else 0; theta = vega = 0.0
        rows.append({'book': book, 'symbol': p['symbol'], 'root': m['root'], 'side': cp, 'dte': round(T * 365, 1), 'value': prem * q,
                     'delta': delta, 'delta_rupees': delta * q * S, 'theta_day': theta * q, 'vega_1pt': vega * q, 'equity': d['equity']})
g = pd.DataFrame(rows)
pd.set_option('display.width', 250)
for book, x in g.groupby('book'):
    eq = x.equity.iloc[0]
    print(f"\n{book}: {len(x)} positions, premium at risk Rs {x.value.sum():,.0f} ({x.value.sum()/eq*100:.2f}% of equity)")
    print(f"  net delta Rs {x.delta_rupees.sum():,.0f} of underlying ({x.delta_rupees.sum()/eq*100:+.1f}% of equity); "
          f"gross |delta| Rs {x.delta_rupees.abs().sum():,.0f}; theta Rs {x.theta_day.sum():,.0f}/day "
          f"({x.theta_day.sum()/x.value.sum()*100:.1f}% of premium/day); vega Rs {x.vega_1pt.sum():,.0f} per IV point")
    s = x.groupby('side').agg(n=('symbol', 'size'), value=('value', 'sum'), delta_rupees=('delta_rupees', 'sum'), theta_day=('theta_day', 'sum'))
    print(s.round(0).to_string())
    top = x.groupby('root').delta_rupees.sum().abs().sort_values(ascending=False)
    print('  largest |delta| by underlying:', {k: f"{v/1e5:.1f}L" for k, v in top.head(5).items()},
          f"top-1 share {top.iloc[0]/top.sum()*100:.0f}%")
g.to_csv('/w/out/portfolio_greeks_20261014.csv', index=False)
