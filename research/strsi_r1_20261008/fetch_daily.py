"""Read-only: Fyers daily candles for every agent spot through the app's own session (<=366-day windows)."""
import json, time, httpx
from macd_trader.universe import SPOT_SYMBOLS
cred = json.load(open('/app/runtime/credentials.json'))
auth = f"{cred['fyers_client_id']}:{cred['fyers_access_token']}"
windows = [('2024-09-01', '2025-08-31'), ('2025-09-01', '2026-03-31'), ('2026-04-01', '2026-10-07')]
out, errors = {}, {}
with httpx.Client(timeout=30) as client:
    for sym in dict.fromkeys(list(SPOT_SYMBOLS)):
        bars = []
        for a, b in windows:
            for attempt in range(3):
                r = client.get('https://api-t1.fyers.in/data/history', headers={'Authorization': auth},
                               params={'symbol': sym, 'resolution': 'D', 'date_format': '1', 'range_from': a, 'range_to': b, 'cont_flag': '1'})
                if r.status_code == 429: time.sleep(2 + 3 * attempt); continue
                break
            p = r.json() if r.status_code == 200 else {'s': 'error', 'message': f'http {r.status_code}'}
            if p.get('candles') is not None: bars.extend(p['candles'])
            else: errors[f'{sym} {a}'] = p.get('message')
            time.sleep(0.35)
        out[sym] = sorted({row[0]: row for row in bars}.values())
json.dump({'bars': out}, open('/tmp/daily_all.json', 'w'))
print(len(out), 'symbols; errors', len(errors), list(errors.items())[:3])
