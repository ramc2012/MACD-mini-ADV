"""Read the current Docker paper ledgers and reconcile fills with portfolio APIs.

Source connections are mode=ro/query_only; only local research files are written.
No repository constructor, strategy reset, settings write or order API is used.
"""
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")
READER = r'''
import datetime as dt, json, os, sqlite3, urllib.request
output = {'read_at_utc': dt.datetime.now(dt.UTC).isoformat(), 'books': {}}
for name in ['macd_trader', 'blast_trader', 'smart_1l', 'smart_50l']:
    c = sqlite3.connect('file:/app/data/' + name + '.sqlite3?mode=ro', uri=True)
    c.execute('PRAGMA query_only=ON'); c.execute('BEGIN')
    b = {'source_database': '/app/data/' + name + '.sqlite3', 'query_only': c.execute('PRAGMA query_only').fetchone()[0]}
    b['trades'] = [json.loads(r[0]) for r in c.execute('SELECT payload FROM trades ORDER BY timestamp, rowid')]
    b['equity_points'] = [dict(zip(['timestamp','equity','cash','realized_pnl','unrealized_pnl'], r)) for r in c.execute('SELECT timestamp,equity,cash,realized_pnl,unrealized_pnl FROM equity_points ORDER BY timestamp')]
    b['signal_count'] = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    c.rollback(); c.close()
    output['books'][name] = b
for name, route in [('macd_trader','/api/portfolio'),('blast_trader','/api/blast/book'),('smart_1l','/api/lanes/smart_1l/snapshot'),('smart_50l','/api/lanes/smart_50l/snapshot')]:
    request = urllib.request.Request('http://127.0.0.1:8100' + route, headers={'Authorization': 'Bearer ' + os.environ.get('MACD_API_TOKEN','')})
    raw = json.load(urllib.request.urlopen(request, timeout=20))
    output['books'][name]['api_response'] = raw
print(json.dumps(output, separators=(',',':')))
'''


def replay(trades, capital):
    cash, realized, positions = capital, 0.0, {}
    for t in trades:
        signed = t['quantity'] if t['side'] == 'BUY' else -t['quantity']
        price, fee = t['price'], t.get('fees', 0.0)
        cash -= signed * price + fee
        realized -= fee
        old_qty, old_avg = positions.get(t['symbol'], (0, 0.0))
        new_qty = old_qty + signed
        if old_qty and old_qty * signed < 0:
            realized += min(abs(old_qty), abs(signed)) * (price-old_avg) * (1 if old_qty > 0 else -1)
        if new_qty == 0:
            positions.pop(t['symbol'], None)
        elif old_qty == 0 or old_qty * new_qty < 0:
            positions[t['symbol']] = (new_qty, price)
        elif old_qty * signed > 0:
            positions[t['symbol']] = (new_qty, (abs(old_qty)*old_avg+abs(signed)*price)/abs(new_qty))
        else:
            positions[t['symbol']] = (new_qty, old_avg)
    return cash, realized, positions


def portfolio_from_api(name, raw):
    if name == 'macd_trader':
        return raw
    if name == 'blast_trader':
        return raw['portfolio']
    return raw.get('portfolio', raw)


def main():
    result = subprocess.run(['docker','exec','-i','macd-mini-parallel-engine-1','python','-'], input=READER, text=True, capture_output=True, check=True)
    payload = result.stdout.encode()
    data = json.loads(payload)
    with gzip.open(ROOT/'paper_book_source.json.gz', 'wb') as f:
        f.write(payload)
    receipt = {'read_at_utc': data['read_at_utc'], 'source_payload_sha256': hashlib.sha256(payload).hexdigest(), 'books': {}}
    for name,b in data['books'].items():
        api = portfolio_from_api(name, b['api_response'])
        if 'initial_capital' not in api:
            raise ValueError('Missing portfolio schema: ' + name)
        capital = api['initial_capital']
        cash, realized, pos = replay(b['trades'], capital)
        expected = {p['symbol']: p for p in api['positions']}
        cash_error = cash-api['cash']
        realized_error = realized-api['realized_pnl']
        assert abs(cash_error) < .01 and abs(realized_error) < .01, (name, cash_error, realized_error)
        assert set(pos) == set(expected), name
        for symbol,(qty,avg) in pos.items():
            assert qty == expected[symbol]['quantity'] and abs(avg-expected[symbol]['average_price']) < 1e-8
        equity_error = api['equity']-capital-api['realized_pnl']-api['unrealized_pnl']
        assert abs(equity_error) < .01, (name,equity_error)
        days = {}
        for point in b['equity_points']:
            day = dt.datetime.fromisoformat(point['timestamp']).astimezone(IST).date().isoformat()
            days[day] = point
        checkpoints = []
        for day, point in days.items():
            prior = [t for t in b['trades'] if dt.datetime.fromisoformat(t['timestamp']) <= dt.datetime.fromisoformat(point['timestamp'])]
            day_cash, day_realized, day_pos = replay(prior, capital)
            checkpoints.append({'day_ist':day, **point, 'pnl_from_capital':point['equity']-capital, 'fill_count':len(prior), 'open_positions_from_fills':len(day_pos), 'cash_replay_error': day_cash-point['cash'], 'realized_replay_error': day_realized-point['realized_pnl']})
            assert abs(day_cash-point['cash']) < .01 and abs(day_realized-point['realized_pnl']) < .01
        receipt['books'][name] = {
            'source_database': b['source_database'], 'query_only':b['query_only'],
            'fill_count': len(b['trades']), 'signal_count':b['signal_count'],
            'first_fill': b['trades'][0]['timestamp'] if b['trades'] else None,
            'last_fill': b['trades'][-1]['timestamp'] if b['trades'] else None,
            'first_equity_point': b['equity_points'][0]['timestamp'],
            'last_equity_point': b['equity_points'][-1]['timestamp'],
            'initial_capital':capital, 'api_cash':api['cash'], 'api_equity':api['equity'],
            'api_realized_pnl':api['realized_pnl'], 'api_unrealized_pnl':api['unrealized_pnl'],
            'api_net_pnl':api['equity']-capital, 'api_open_positions':len(expected),
            'cash_replay_error':cash_error, 'realized_replay_error':realized_error,
            'equity_identity_error':equity_error, 'daily_endpoints':checkpoints,
            'all_fills_have_zero_fees':all(t.get('fees',0)==0 for t in b['trades'])}
    (ROOT/'paper_book_reconciliation.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2))


if __name__ == '__main__':
    main()
