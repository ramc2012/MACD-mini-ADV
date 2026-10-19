"""The review's proposals, measured on the LIVE blast journal (29 Sep - 14 Oct 2026). Read-only.

Every row is a contract the lane actually judged while it was in the tradable band, so there is no
survivorship. Rows: TAKEN, ENTRY_REJECTED, BREADTH_TOO_THIN and NOT_OFF_HIGH (all cleared the premium gate).
Per row:
  outcome   the lane's current exit, and the proposed variants, replayed on stored 1-minute bars from the
            journal time to expiry-day 15:10 (cap 15 sessions); two fill models as in exits.py
  greeks    Black-Scholes on the journal's own premium and spot (r 6.5%, no dividend): IV, delta, gamma,
            theta/day, vega, and the theta-to-gamma ratio; DTE in calendar days
  rv20      20-session close-to-close realized vol of the underlying to the previous close; iv_rv = IV / rv20
  spread    (ask-bid)/mid at entry from the tick store (raw ticks 12-14 Oct; minute rows on condensed days)
  flow      the underlying's classified buy-sell volume over the 30 minutes before, signed by the option side
  va_pos    spot's position in the PREVIOUS session's value area (70% of volume for stocks, of time for indices,
            0.1% price bins): 0 = VAL, 1 = VAH
Output: data/journal_feat.csv
"""
import json, math, sqlite3
from datetime import datetime, timedelta
import numpy as np, pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

R, COST, IST = 0.065, 0.002, 19800
b = sqlite3.connect('file:/data/blast_trader.sqlite3?mode=ro', uri=True)
h = sqlite3.connect('file:/data/historical.sqlite3?mode=ro', uri=True, timeout=30)
t = sqlite3.connect('file:/data/ticks.sqlite3?mode=ro', uri=True, timeout=30)
rows = [json.loads(p) | {'mfe': mfe, 'mae': mae, 'resolved': res} for p, mfe, mae, res in b.execute(
    """SELECT payload, mfe_pct, mae_pct, resolved FROM blast_journal
       WHERE reason IN ('TAKEN','ENTRY_REJECTED','BREADTH_TOO_THIN','NOT_OFF_HIGH')""")]
j = pd.DataFrame(rows)
j['t0'] = pd.to_datetime(j['at'], format='ISO8601').astype('int64') // 10**9
print('journal rows', len(j), j.reason.value_counts().to_dict())


def bs(S, K, T, sigma, cp):
    d1 = (math.log(S / K) + (R + sigma * sigma / 2) * T) / (sigma * math.sqrt(T)); d2 = d1 - sigma * math.sqrt(T)
    if cp == 'CE':
        return S * norm.cdf(d1) - K * math.exp(-R * T) * norm.cdf(d2), norm.cdf(d1), d1, d2
    return K * math.exp(-R * T) * norm.cdf(-d2) - S * norm.cdf(-d1), norm.cdf(d1) - 1, d1, d2


def greeks(prem, S, K, T, cp):
    intrinsic = max(0.0, (S - K) if cp == 'CE' else (K - S))
    if T <= 0 or prem <= intrinsic * 1.0001 + 1e-9:
        return dict(iv=np.nan, delta=np.nan, gamma=np.nan, theta=np.nan, vega=np.nan)
    try:
        iv = brentq(lambda s: bs(S, K, T, s, cp)[0] - prem, 1e-3, 5.0)
    except ValueError:
        return dict(iv=np.nan, delta=np.nan, gamma=np.nan, theta=np.nan, vega=np.nan)
    _, delta, d1, d2 = bs(S, K, T, iv, cp)
    gamma = norm.pdf(d1) / (S * iv * math.sqrt(T))
    common = -S * norm.pdf(d1) * iv / (2 * math.sqrt(T))
    theta = (common - R * K * math.exp(-R * T) * norm.cdf(d2)) if cp == 'CE' else (common + R * K * math.exp(-R * T) * norm.cdf(-d2))
    return dict(iv=iv, delta=delta, gamma=gamma, theta=theta / 365, vega=S * norm.pdf(d1) * math.sqrt(T) / 100)


spot1d = pd.read_csv('/w/data/spot1d.csv.gz').sort_values(['symbol', 'day0'])
spot1d['lr'] = spot1d.groupby('symbol').close.transform(lambda s: np.log(s).diff())
spot1d['rv20'] = spot1d.groupby('symbol').lr.transform(lambda s: s.rolling(20, min_periods=15).std().shift() * math.sqrt(252))
rv = spot1d.set_index(['symbol', 'day0']).rv20


def simulate(op, hi, lo, cl, hard=0.5, act=0.3, trail=0.4, ratchet=None, tstop=None, tthr=0.10, trail_pct=None, cons=False):
    entry = op[0]; be = entry * (1 + COST); tp = trail if trail_pct is None else trail_pct
    stop = entry * (1 - hard); peak = entry; floor = 0.0
    for k in range(len(op)):
        level = max(stop, floor)
        if op[k] <= level:
            return op[k] / entry - 1 - COST
        if lo[k] <= level:
            return (min(level, cl[k]) if cons else level) / entry - 1 - COST
        if tstop is not None and k + 1 >= tstop * 30 and peak < entry * (1 + tthr):
            return cl[k] / entry - 1 - COST
        peak = max(peak, cl[k] if cons else hi[k])
        if ratchet is not None and peak >= entry * (1 + ratchet):
            floor = max(floor, be)
        if peak >= entry * (1 + act):
            floor = max(floor, peak * (1 - tp), be)
    return cl[-1] / entry - 1 - COST


def value_area(spot_sym, day_start):
    """Previous session's value area from stored 1-minute spot bars."""
    prev = h.execute("""SELECT max(timestamp) FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
                        AND timestamp < ?""", (spot_sym, day_start)).fetchone()[0]
    if prev is None:
        return None
    p0 = (prev + IST) // 86400 * 86400 - IST
    bars = h.execute("""SELECT high, low, close, volume FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
                        AND timestamp >= ? AND timestamp < ?""", (spot_sym, p0, p0 + 86400)).fetchall()
    if len(bars) < 200:
        return None
    a = np.array(bars, dtype=float); tp = a[:, :3].mean(axis=1)
    w = a[:, 3] if a[:, 3].sum() > 0 else np.ones(len(a))
    step = tp.mean() * 0.001
    bins = np.floor(tp / step).astype(int); lo_b = bins.min()
    prof = np.bincount(bins - lo_b, weights=w)
    poc = int(prof.argmax()); lo_i = hi_i = poc; tot = prof[poc]
    while tot < 0.7 * prof.sum():
        left = prof[lo_i - 1] if lo_i > 0 else -1; right = prof[hi_i + 1] if hi_i + 1 < len(prof) else -1
        if right >= left:
            hi_i += 1; tot += prof[hi_i]
        else:
            lo_i -= 1; tot += prof[lo_i]
    return (lo_b + lo_i) * step, (lo_b + hi_i + 1) * step, (lo_b + poc + 0.5) * step


ids = dict(t.execute('SELECT symbol, id FROM tick_symbols'))
raw_lo, raw_hi = t.execute('SELECT min(ts_ms), max(ts_ms) FROM ticks').fetchone()
VARIANTS = {'cur': {}, 'r22': {'ratchet': 0.22}, 'r15': {'ratchet': 0.15}, 't8': {'tstop': 8}, 't4': {'tstop': 4},
            'r22t8': {'ratchet': 0.22, 'tstop': 8}, 'stop35': {'hard': 0.35}}
out = []
va_cache = {}
for _, r in j.iterrows():
    rec = {'id': r.id, 'reason': r.reason, 'day': r.day, 'symbol': r.symbol, 'side': r.option_type, 'root': r.spot_symbol,
           'premium': r.premium, 'spot': r.spot, 'premium_pct': r.premium_pct, 'breadth': r.breadth,
           'off_high_pct': r.off_high_pct, 'mfe': r.mfe, 'mae': r.mae, 'resolved': r.resolved}
    t0 = int(r.t0)
    exp = datetime.fromisoformat(str(r.expiry)[:10])
    exp_close = int(pd.Timestamp(f'{str(r.expiry)[:10]} 15:10', tz='Asia/Kolkata').timestamp())
    T = (pd.Timestamp(f'{str(r.expiry)[:10]} 15:30', tz='Asia/Kolkata').timestamp() - t0) / (365 * 86400)
    rec['dte'] = (exp.date() - datetime.fromisoformat(r.day).date()).days
    rec.update(greeks(r.premium, r.spot, float(r.strike), T, r.option_type))
    rec['theta_gamma'] = abs(rec['theta']) / (rec['gamma'] * r.spot * r.spot * 1e-4) if rec['gamma'] else np.nan  # theta per 1%-move gamma P&L
    day0 = (t0 + IST) // 86400 * 86400
    rec['rv20'] = rv.get((r.spot_symbol, day0 - IST), np.nan)       # spot1d keys on the UTC epoch of IST midnight
    rec['iv_rv'] = rec['iv'] / rec['rv20'] if rec['rv20'] and rec['rv20'] > 0 else np.nan
    path = h.execute("""SELECT open, high, low, close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
                        AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp""",
                     (r.symbol, t0 - t0 % 60, min(exp_close, t0 + 21 * 86400))).fetchall()
    if len(path) >= 2:
        a = np.array(path, dtype=float)
        for fill in ('', '_cons'):
            for name, kw in VARIANTS.items():
                rec[f'ret_{name}{fill}'] = simulate(a[:, 0], a[:, 1], a[:, 2], a[:, 3], cons=bool(fill), **kw)
        rec['path_minutes'] = len(a)
    sid = ids.get(r.symbol)
    if sid is not None and raw_lo <= t0 * 1000 <= raw_hi:
        q = t.execute("""SELECT bid, ask FROM ticks WHERE symbol_id = ? AND ts_ms >= ? AND ts_ms < ? AND bid > 0 AND ask > 0
                         ORDER BY ts_ms LIMIT 1""", (sid, (t0 - 60) * 1000, (t0 + 120) * 1000)).fetchone()
        if q:
            rec['spread_pct'] = (q[1] - q[0]) / ((q[1] + q[0]) / 2) * 100
    elif sid is not None:
        q = t.execute("""SELECT avg_spread, close FROM tick_minute_flow WHERE symbol_id = ? AND minute_ts >= ? AND minute_ts < ?
                         AND avg_spread IS NOT NULL ORDER BY minute_ts DESC LIMIT 1""", (sid, t0 - 300, t0 + 60)).fetchone()
        if q and q[1]:
            rec['spread_pct'] = q[0] / q[1] * 100
    uid = ids.get(r.spot_symbol)
    if uid is not None:
        q = t.execute('SELECT sum(delta), sum(volume) FROM tick_minute_flow WHERE symbol_id = ? AND minute_ts >= ? AND minute_ts < ?',
                      (uid, t0 - 1800, t0)).fetchone()
        if q and q[1]:
            rec['flow_signed'] = (q[0] / q[1]) * (1 if r.option_type == 'CE' else -1)
    key = (r.spot_symbol, day0)
    if key not in va_cache:
        va_cache[key] = value_area(r.spot_symbol, day0 - IST)
    va = va_cache[key]
    if va and va[1] > va[0]:
        rec['va_pos'] = (r.spot - va[0]) / (va[1] - va[0])
    out.append(rec)
f = pd.DataFrame(out)
f.to_csv('/w/data/journal_feat.csv', index=False)
print('rows', len(f), 'with path', f.ret_cur.notna().sum(), 'iv', f.iv.notna().sum(), 'spread', f.get('spread_pct', pd.Series()).notna().sum(),
      'flow', f.get('flow_signed', pd.Series()).notna().sum(), 'va', f.get('va_pos', pd.Series()).notna().sum())
