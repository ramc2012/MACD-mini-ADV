"""Per-minute order flow for every underlying (spot / futures) on the desk's signal days. Read-only.

Condensed days come from tick_minute_flow as stored. Raw days (still in the ticks table) are condensed here with
the desk's own condense_ticks and session bounds, exactly as the nightly job will. Also records, per symbol-day,
the raw-tick quality numbers the audit needs (quotes present, volume increments).
Output: data/spot_flow.csv.gz, data/raw_quality.csv
"""
import csv, gzip, sqlite3, sys
from collections import defaultdict
sys.path.insert(0, '/eng')
from macd_trader.tick_store import condense_ticks
from macd_trader.sessions import session_bounds

IST = 19800
t = sqlite3.connect('file:/data/ticks.sqlite3?mode=ro', uri=True, timeout=60)
names = dict(t.execute('SELECT id, symbol FROM tick_symbols'))
under = {i: s for i, s in names.items() if s.endswith(('-EQ', '-INDEX')) or 'FUT' in s}
print('underlying symbols', len(under), flush=True)
out = gzip.open('/w/data/spot_flow.csv.gz', 'wt', newline='')
w = csv.writer(out)
cols = ['symbol', 'minute_ts', 'open', 'high', 'low', 'close', 'volume', 'buy_volume', 'sell_volume', 'delta', 'trades', 'ticks',
        'quote_n', 'mid_n', 'tick_n', 'zero_tick_n', 'pending_n', 'conflict_n', 'unclassified', 'avg_spread', 'ofi', 'ofi_events',
        'avg_confidence', 'src']
w.writerow(cols)
ids = ','.join(str(i) for i in under)
n = 0
for row in t.execute(f"""SELECT symbol_id, minute_ts, open, high, low, close, volume, buy_volume, sell_volume, delta, trades, ticks,
                                quote_n, mid_n, tick_n, zero_tick_n, pending_n, conflict_n, unclassified, avg_spread, ofi, ofi_events,
                                avg_confidence FROM tick_minute_flow WHERE symbol_id IN ({ids})"""):
    w.writerow([under[row[0]], *row[1:], 'condensed']); n += 1
print('condensed rows', n, flush=True)

days = sorted({d for (d,) in t.execute('SELECT DISTINCT (ts_ms / 1000 + ?) / 86400 FROM ticks', (IST,))})
q = csv.writer(open('/w/data/raw_quality.csv', 'w', newline=''))
q.writerow(['symbol', 'day', 'ticks', 'two_sided', 'with_volume_step', 'with_oi'])
import datetime as dt
for d in days:
    day = dt.date.fromtimestamp(d * 86400).isoformat()
    start, end = (d * 86400 - IST) * 1000, ((d + 1) * 86400 - IST) * 1000
    for sid, sym in under.items():
        rows = t.execute("""SELECT symbol_id, ts_ms, ltp, cum_volume, last_qty, bid, ask, bid_qty, ask_qty, tbq, tsq, oi
                            FROM ticks WHERE symbol_id = ? AND ts_ms >= ? AND ts_ms < ? ORDER BY ts_ms""", (sid, start, end)).fetchall()
        if not rows:
            continue
        s0, s1 = session_bounds(sym, day)
        minute, _ladder, _raw = condense_ticks(rows, {sid: (s0 * 1000, s1 * 1000)})
        prev = None; steps = 0
        for r in rows:
            if r[3] is not None and prev is not None and r[3] > prev:
                steps += 1
            prev = r[3] if r[3] is not None else prev
        q.writerow([sym, day, len(rows), sum(1 for r in rows if r[5] and r[6] and 0 < r[5] <= r[6]), steps,
                    sum(1 for r in rows if r[11])])
        for (_sid, ts), b in sorted(minute.items()):
            w.writerow([sym, ts, b['open'], b['high'], b['low'], b['close'], b['volume'], b['buy_volume'], b['sell_volume'],
                        b['buy_volume'] - b['sell_volume'], b['trades'], b['ticks'], b['quote_n'], b['mid_n'], b['tick_n'],
                        b['zero_tick_n'], b['pending_n'], b['conflict_n'], b['unclassified'],
                        round(b['spread_sum'] / b['spread_n'], 4) if b['spread_n'] else None, round(b['ofi'], 2), b['ofi_events'],
                        round(b['conf_sum'] / b['conf_volume'], 4) if b['conf_volume'] else None, 'raw'])
    print('raw day', day, flush=True)
out.close()
