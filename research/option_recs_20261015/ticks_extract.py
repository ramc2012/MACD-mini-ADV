"""Point-in-time features from the live tick store for each premium-cleared cross (read-only).

  member     1 if the contract had live ticks that IST day (it was in the subscribed, tradable band), 0 if the day
             has tick coverage but the contract did not tick, blank on days without tick coverage. Stored minute
             bars cannot answer this: backfill rewrites downloaded_at, and contracts that later moved into the band
             get history from before they were tradable (survivorship).
  spread_pct (ask - bid) / mid at entry. Raw ticks (12-14 Oct): the first two-sided quote at or after the entry
             minute. Condensed days: that minute's mean ask - bid over its close.
  und_delta30, und_vol30  the underlying's classified buy-minus-sell volume and volume over the 30 minutes before
             entry (condensed days only; indices carry no volume).
Output: data/tickfeat.csv
"""
import sqlite3
from collections import defaultdict
import pandas as pd

IST = 19800
x = pd.read_csv('/w/out/crosses.csv.gz')
x = x[x.verdict.isin(['PASS', 'BREADTH_TOO_THIN', 'NOT_OFF_HIGH'])]
t = sqlite3.connect('file:/data/ticks.sqlite3?mode=ro', uri=True, timeout=30)
ids = dict(t.execute('SELECT symbol, id FROM tick_symbols'))
day_of = lambda s: (s + IST) // 86400

members = defaultdict(set)                       # ist day number -> symbol ids that ticked
for sid, ts in t.execute('SELECT symbol_id, minute_ts FROM tick_minute_flow'):
    members[day_of(ts)].add(sid)
for sid, d in t.execute('SELECT symbol_id, (ts_ms / 1000 + ?) / 86400 FROM ticks GROUP BY 1, 2', (IST,)):
    members[d].add(sid)
covered = {d for d, s in members.items() if len(s) > 500}
print('covered days', sorted(pd.to_datetime([d * 86400 for d in covered], unit='s').date))
raw_lo, raw_hi = t.execute('SELECT min(ts_ms), max(ts_ms) FROM ticks').fetchone()

out = []
for i, r in x.iterrows():
    d = day_of(int(r.signal_end))
    sid = ids.get(r.symbol)
    rec = {'row': i, 'member': (1 if sid in members[d] else 0) if d in covered else None,
           'spread_pct': None, 'spread_src': None, 'und_delta30': None, 'und_vol30': None}
    t0 = int(r.signal_end)
    if sid is not None and raw_lo <= t0 * 1000 <= raw_hi:
        q = t.execute("""SELECT bid, ask FROM ticks WHERE symbol_id = ? AND ts_ms >= ? AND ts_ms < ? AND bid > 0 AND ask > 0
                         ORDER BY ts_ms LIMIT 1""", (sid, t0 * 1000, (t0 + 600) * 1000)).fetchone()
        if q:
            rec['spread_pct'] = (q[1] - q[0]) / ((q[1] + q[0]) / 2) * 100; rec['spread_src'] = 'raw'
    elif sid is not None:
        q = t.execute("""SELECT avg_spread, close FROM tick_minute_flow WHERE symbol_id = ? AND minute_ts >= ? AND minute_ts < ?
                         AND avg_spread IS NOT NULL ORDER BY minute_ts LIMIT 1""", (sid, t0, t0 + 600)).fetchone()
        if q and q[1]:
            rec['spread_pct'] = q[0] / q[1] * 100; rec['spread_src'] = 'minute'
    uid = ids.get(r.spot_sym)
    if uid is not None:
        q = t.execute("""SELECT sum(delta), sum(volume) FROM tick_minute_flow WHERE symbol_id = ? AND minute_ts >= ? AND minute_ts < ?""",
                      (uid, t0 - 1800, t0)).fetchone()
        if q and q[1]:
            rec['und_delta30'], rec['und_vol30'] = q
    out.append(rec)
f = pd.DataFrame(out)
f.to_csv('/w/data/tickfeat.csv', index=False)
print(f.member.value_counts(dropna=False).to_dict(), f.spread_src.value_counts().to_dict(), 'und flow rows', f.und_vol30.notna().sum())
