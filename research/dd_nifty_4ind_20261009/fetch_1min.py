"""Read-only: 1-min index history from Fyers via the app's own session, 100-day windows."""
import json, time, httpx
from datetime import date, timedelta
cred = json.load(open('/app/runtime/credentials.json'))
auth = f"{cred['fyers_client_id']}:{cred['fyers_access_token']}"
syms = ['NSE:NIFTY50-INDEX', 'NSE:NIFTYBANK-INDEX', 'BSE:SENSEX-INDEX']
start, end = date(2021, 1, 1), date(2026, 10, 8)
out, errs = {}, []
with httpx.Client(timeout=60) as client:
    for s in syms:
        rows, a = [], start
        while a <= end:
            b = min(a + timedelta(days=99), end)
            for attempt in range(3):
                r = client.get('https://api-t1.fyers.in/data/history', headers={'Authorization': auth},
                               params={'symbol': s, 'resolution': '1', 'date_format': '1', 'range_from': a.isoformat(), 'range_to': b.isoformat(), 'cont_flag': '1'})
                if r.status_code == 429: time.sleep(3 + 3 * attempt); continue
                break
            p = r.json() if r.status_code == 200 else {}
            if p.get('candles') is not None: rows.extend(p['candles'])
            else: errs.append((s, a.isoformat(), r.status_code, p.get('message')))
            a = b + timedelta(days=1); time.sleep(0.4)
        out[s] = sorted({x[0]: x for x in rows}.values())
        print(s, len(out[s]), flush=True)
json.dump(out, open('/tmp/idx1m.json', 'w'))
print('errors', errs[:5], len(errs))
