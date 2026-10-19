"""Read-only Fyers history via the app session: 1-min MIDCPNIFTY 2021->, 1-min top-up of the other three to 12 Oct, daily VIX."""
import json, time, httpx
from datetime import date, timedelta
cred = json.load(open('/app/runtime/credentials.json'))
auth = f"{cred['fyers_client_id']}:{cred['fyers_access_token']}"
def get(client, sym, res, a, b):
    for attempt in range(3):
        r = client.get('https://api-t1.fyers.in/data/history', headers={'Authorization': auth},
                       params={'symbol': sym, 'resolution': res, 'date_format': '1', 'range_from': a, 'range_to': b, 'cont_flag': '1'})
        if r.status_code == 429: time.sleep(3 + 3 * attempt); continue
        return (r.json().get('candles') or []) if r.status_code == 200 else []
    return []
out = {}
with httpx.Client(timeout=60) as client:
    for sym, start in (('NSE:MIDCPNIFTY-INDEX', date(2021, 1, 1)), ('NSE:NIFTY50-INDEX', date(2026, 10, 1)),
                       ('NSE:NIFTYBANK-INDEX', date(2026, 10, 1)), ('BSE:SENSEX-INDEX', date(2026, 10, 1))):
        rows, a = [], start
        while a <= date(2026, 10, 12):
            b = min(a + timedelta(days=99), date(2026, 10, 12))
            rows += get(client, sym, '1', a.isoformat(), b.isoformat()); a = b + timedelta(days=1); time.sleep(0.4)
        out[sym] = sorted({x[0]: x for x in rows}.values()); print(sym, len(out[sym]), flush=True)
    vix = []
    for a, b in (('2021-01-01', '2021-12-31'), ('2022-01-01', '2022-12-31'), ('2023-01-01', '2023-12-31'), ('2024-01-01', '2024-12-31'), ('2025-01-01', '2025-12-31'), ('2026-01-01', '2026-10-12')):
        vix += get(client, 'NSE:INDIAVIX-INDEX', 'D', a, b); time.sleep(0.4)
    out['VIX_D'] = sorted({x[0]: x for x in vix}.values()); print('VIX daily', len(out['VIX_D']))
json.dump(out, open('/tmp/idx_extra.json', 'w'))
