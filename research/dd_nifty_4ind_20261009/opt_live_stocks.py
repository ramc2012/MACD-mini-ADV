"""Real premiums from the app DB (read-only) for 2026 trades: nearest expiry, premium nearest 1% of spot."""
import csv, sqlite3, sys
from datetime import datetime, timezone, timedelta
from macd_trader import option_agent as oa
c = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro', uri=True)
cat = {}
for sym, exp in c.execute("SELECT symbol, MAX(expiry) FROM historical_candles WHERE timeframe_seconds=60 AND (symbol LIKE '%CE' OR symbol LIKE '%PE') GROUP BY symbol"):
    p = oa.parse_option(sym, exp)
    if p: cat.setdefault(p['root'], []).append((sym, p['expiry'], p['strike'], 'CE' if p['side'] > 0 else 'PE'))
IST = timezone(timedelta(hours=5, minutes=30))
def bar(sym, t, after=True):
    if after:
        return c.execute('SELECT timestamp, open, close FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<=? ORDER BY timestamp LIMIT 1', (sym, t, t + 120)).fetchone()
    return c.execute('SELECT timestamp, open, close FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp<=? ORDER BY timestamp DESC LIMIT 1', (sym, t)).fetchone()
def hi(sym, a, b):
    return c.execute('SELECT MAX(high) FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp BETWEEN ? AND ?', (sym, a, b)).fetchone()[0]
rows = list(csv.DictReader(open('/tmp/trades.csv')))
out = csv.writer(open('/tmp/opt_live.csv', 'w', newline=''))
out.writerow(['sym', 'set', 'tf', 'day', 'entry_ts', 'exit_ts', 'pts', 'mins', 'spot', 'CE_strike', 'CE_expiry', 'CE_entry', 'CE_exit', 'CE_mfe', 'PE_strike', 'PE_expiry', 'PE_entry', 'PE_exit', 'PE_mfe'])
for r in rows:
    et, xt, spot = int(r['entry_ts']), int(r['exit_ts']), float(r['entry'])
    day = datetime.fromtimestamp(et, IST).date().isoformat()
    rec = [r['sym'], r['set'], r['tf'], day, et, xt, r['pts'], r['mins'], spot]
    ok = True
    for right in ('CE', 'PE'):
        cands = [x for x in cat.get(r['sym'], []) if x[3] == right and x[1] >= day]
        if not cands: ok = False; break
        e = min(x[1] for x in cands)
        best = None
        for sym, exp, strike, _ in cands:
            if exp != e or abs(strike - spot) > 0.06 * spot: continue
            b = bar(sym, et)
            if b and b[1] > 0 and (best is None or abs(b[1] - 0.01 * spot) < abs(best[1][1] - 0.01 * spot)): best = (sym, b, strike, exp)
        if not best: ok = False; break
        sym, b, strike, exp = best
        xb = bar(sym, xt) or bar(sym, xt, after=False)
        h = hi(sym, et, xt) or b[1]
        rec += [strike, exp, b[1], xb[1] if xb[0] >= xt else xb[2], h / b[1] - 1]
    if ok: out.writerow(rec)
