"""Read-only: MP lane fills -> round trips (FIFO per symbol) with setup; and every logged signal."""
import sqlite3, json, csv
from collections import defaultdict, deque
c = sqlite3.connect('file:/app/data/mp_trader.sqlite3?mode=ro', uri=True)
sig = {json.loads(p)['signal_id']: json.loads(p) for (p,) in c.execute('select payload from signals')}
orders = {json.loads(p)['order_id']: json.loads(p) for (p,) in c.execute('select payload from orders')}
trades = [json.loads(p) for (p,) in c.execute('select payload from trades order by timestamp')]
open_ = defaultdict(deque); rt = []
for t in trades:
    o = orders.get(t['order_id'], {})
    if t['side'] == 'BUY':
        s = sig.get(o.get('signal_id'), {})
        open_[t['symbol']].append({**t, 'kind': s.get('kind'), 'signal_price': s.get('price'), 'signal_ts': s.get('timestamp')})
    else:
        q = t['quantity']
        while q > 0 and open_[t['symbol']]:
            b = open_[t['symbol']][0]; take = min(q, b['quantity'])
            rt.append({'symbol': t['symbol'], 'kind': b['kind'], 'entry_ts': b['timestamp'], 'exit_ts': t['timestamp'], 'qty': take,
                       'entry': b['price'], 'exit': t['price'], 'pnl': (t['price'] - b['price']) * take - (b['fees'] + t['fees']) * take / b['quantity'],
                       'ret': t['price'] / b['price'] - 1, 'signal_price': b['signal_price']})
            b['quantity'] -= take; q -= take
            if b['quantity'] == 0: open_[t['symbol']].popleft()
exits = {json.loads(p).get('order_id'): json.loads(p).get('exit_reason') for (_, _, _, p) in c.execute('select * from mp_closed_positions')}
json.dump({'round_trips': rt, 'still_open': {k: list(v) for k, v in open_.items() if v}}, open('/tmp/mp_rt.json', 'w'))
w = csv.writer(open('/tmp/mp_signals.csv', 'w', newline=''))
w.writerow(['signal_id', 'timestamp', 'symbol', 'kind', 'price'])
for s in sig.values(): w.writerow([s['signal_id'], s['timestamp'], s['symbol'], s['kind'], s['price']])
print(len(rt), 'round trips;', len(sig), 'signals')
