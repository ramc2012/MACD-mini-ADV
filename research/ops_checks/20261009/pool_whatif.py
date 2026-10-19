import sqlite3, math
from collections import Counter
from macd_trader import option_agent as oa
c = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro', uri=True)
day = '2026-10-09'
prior = oa.prepare(c, day, refresh=True)
hist = oa.History(); hist.days, hist.by_day = list(prior.days), dict(prior.by_day)
hist.add(day, oa.summarise_day(c, day, oa.SPOT_SET, options=True))
cut = oa._as_of_epoch(day); profiles = oa._prior_profiles(c, day); horizon_end = oa._sessions_after(day)[-1]
under = {}
def eligible(max_lag_min, min_bars):
    n = 0; roots = set()
    for sym, s in hist.by_day[day].items():
        if not sym.endswith(('CE', 'PE')) or s['bars'] < min_bars or not s['p1445'] or not s['ts1445']: continue
        if cut - (s['ts1445'] + 60) > max_lag_min * 60: continue
        ct = oa.parse_option(sym, s.get('expiry'))
        if not ct or not ct['spot'] or ct['expiry'] <= horizon_end or s['p1445'] < oa.MIN_PREMIUM or s['notional_before'] < oa.MIN_TURNOVER: continue
        if ct['spot'] not in under: under[ct['spot']] = oa._underlying_features(ct['spot'], day, hist, profiles)
        u = under[ct['spot']]
        if not u or abs(ct['side'] * math.log(u['price'] / ct['strike'])) > oa.MAX_MONEYNESS: continue
        n += 1; roots.add(ct['root'])
    return n, len(roots)
for lag, mb in ((1, 60), (3, 60), (5, 60), (5, 30), (10, 30), (15, 20)):
    print(f'last bar within {lag:2d} min, >= {mb} bars: {eligible(lag, mb)} (contracts, underlyings)')
