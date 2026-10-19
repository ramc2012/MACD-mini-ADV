"""Read-only Fyers DAILY history, 2019 -> now, for the indices and the F&O stock universe (app session token)."""
import json, time, httpx
from datetime import date
from macd_trader.config import Settings
s = Settings()
cred = json.load(open('/app/runtime/credentials.json'))
auth = f"{cred['fyers_client_id']}:{cred['fyers_access_token']}"
indices = ['NSE:NIFTY50-INDEX', 'NSE:NIFTYBANK-INDEX', 'BSE:SENSEX-INDEX', 'NSE:MIDCPNIFTY-INDEX', *s.context_indices]
stocks = [x for x in s.symbols if x.endswith('-EQ')]
def get(client, sym, a, b):
    for attempt in range(4):
        r = client.get('https://api-t1.fyers.in/data/history', headers={'Authorization': auth},
                       params={'symbol': sym, 'resolution': 'D', 'date_format': '1', 'range_from': a, 'range_to': b, 'cont_flag': '1'})
        if r.status_code == 429: time.sleep(3 + 3 * attempt); continue
        return (r.json().get('candles') or []) if r.status_code == 200 else []
    return []
out = {}
years = [(f'{y}-01-01', f'{y}-12-31') for y in range(2019, 2026)] + [('2026-01-01', date.today().isoformat())]
with httpx.Client(timeout=60) as client:
    for i, sym in enumerate(dict.fromkeys(indices + stocks)):
        rows = []
        for a, b in years:
            rows += get(client, sym, a, b); time.sleep(0.25)
        out[sym] = sorted({x[0]: x for x in rows}.values())
        print(i, sym, len(out[sym]), flush=True)
json.dump(out, open('/tmp/daily.json', 'w'))
print('done')
