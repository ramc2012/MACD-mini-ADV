"""Read-only: Fyers daily candles 2021-01-01 .. 2024-08-31 for every agent spot (<=366-day windows)."""
import json, time, httpx
from macd_trader.universe import SPOT_SYMBOLS
cred = json.load(open('/app/runtime/credentials.json'))
auth = f"{cred['fyers_client_id']}:{cred['fyers_access_token']}"
windows = [('2021-01-01', '2021-12-31'), ('2022-01-01', '2022-12-31'), ('2023-01-01', '2023-12-31'), ('2024-01-01', '2024-08-31')]
out, errors = {}, {}
with httpx.Client(timeout=30) as client:
    for sym in dict.fromkeys(SPOT_SYMBOLS):
        bars = []
        for a, b in windows:
            for attempt in range(3):
                r = client.get('https://api-t1.fyers.in/data/history', headers={'Authorization': auth},
                               params={'symbol': sym, 'resolution': 'D', 'date_format': '1', 'range_from': a, 'range_to': b, 'cont_flag': '1'})
                if r.status_code == 429: time.sleep(3 + 3 * attempt); continue
                break
            p = r.json() if r.status_code == 200 else {}
            if p.get('candles') is not None: bars += p['candles']
            else: errors[f'{sym} {a}'] = r.status_code
            time.sleep(0.35)
        out[sym] = sorted({x[0]: x for x in bars}.values())
json.dump(out, open('/tmp/daily_2021_2024.json', 'w'))
print(len(out), 'symbols, errors', len(errors))
