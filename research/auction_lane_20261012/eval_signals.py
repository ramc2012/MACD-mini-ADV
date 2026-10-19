"""Read-only: every logged MP signal on its real option minute bars, under several exits.
Entry = first bar open after the signal; costs 0.5% round trip (25 bps slippage each side) + Rs 40.
Exit rules evaluated on 1-min OHLC (stop checked on the bar low before the peak updates):
  lane  : hard -25%, trail 20% off the peak once +20% (floored at breakeven), else 15:20
  wide  : hard -40%, trail 30% once +40%, else 15:20
  eod   : hold to 15:20
  t30/t60: exit after 30/60 minutes
Also the opposite option (CE<->PE, same strike/expiry) under 'lane' and 'eod', as a side check."""
import sqlite3, csv, json
from datetime import datetime
c = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro', uri=True)
def eod_ts(ts):
    d = datetime.fromtimestamp(ts).astimezone().date()
    return int(datetime.fromisoformat(f'{d}T15:20:00+05:30').timestamp())
def bars(sym, a, b):
    return c.execute('SELECT timestamp, open, high, low, close FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<=? ORDER BY timestamp', (sym, a, b)).fetchall()
def run(bs, rule):
    e = bs[0][1]; peak = e; trail = None
    hard, act, tr = {'lane': (0.25, 0.20, 0.20), 'wide': (0.40, 0.40, 0.30)}.get(rule, (None, None, None))
    t0 = bs[0][0]
    for ts, o, h, l, cl in bs:
        if rule in ('t30', 't60') and ts - t0 >= (1800 if rule == 't30' else 3600):
            return o / e - 1
        if hard is not None:
            stop = max(e * (1 - hard), trail or 0)
            if l <= stop:
                return min(o, stop) / e - 1
            if h > peak:
                peak = h
                if peak >= e * (1 + act):
                    trail = max(peak * (1 - tr), e * 1.006)
    return bs[-1][4] / e - 1
out = []
for r in csv.DictReader(open('/tmp/mp_signals.csv')):
    if not r['timestamp'] or r['symbol'] == 'symbol': continue
    ts = int(datetime.fromisoformat(r['timestamp']).timestamp())
    sym = r['symbol']
    if not sym.endswith(('CE', 'PE')): continue
    bs = bars(sym, ts, eod_ts(ts))
    if len(bs) < 3 or bs[0][0] - ts > 180 or bs[0][1] <= 0: continue
    rec = {'ts': ts, 'day': r['timestamp'][:10], 'symbol': sym, 'kind': r['kind'], 'entry': bs[0][1],
           'minute': (ts + 19800) % 86400 // 60, 'mfe': max(b[2] for b in bs) / bs[0][1] - 1, 'mae': min(b[3] for b in bs) / bs[0][1] - 1}
    for rule in ('lane', 'wide', 'eod', 't30', 't60'):
        rec[rule] = run(bs, rule) - 0.005 - 40 / 100000
    opp = sym[:-2] + ('PE' if sym.endswith('CE') else 'CE')
    ob = bars(opp, ts, eod_ts(ts))
    if len(ob) >= 3 and ob[0][0] - ts <= 180 and ob[0][1] > 0:
        rec['opp_lane'] = run(ob, 'lane') - 0.005; rec['opp_eod'] = run(ob, 'eod') - 0.005; rec['opp_t60'] = run(ob, 't60') - 0.005
    out.append(rec)
json.dump(out, open('/tmp/mp_sig_eval.json', 'w'))
print(len(out), 'signals evaluated')
