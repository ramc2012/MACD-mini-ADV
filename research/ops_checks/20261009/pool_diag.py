"""Read-only: why did the 9 Oct agent pool shrink? Replays build_candidates' filters with counters."""
import sqlite3, math, json
from collections import Counter
from statistics import median
from macd_trader import option_agent as oa
c = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro', uri=True)
def diag(day):
    prior = oa.prepare(c, day, refresh=True)
    hist = oa.History(); hist.days, hist.by_day = list(prior.days), dict(prior.by_day)
    hist.add(day, oa.summarise_day(c, day, oa.SPOT_SET, options=True))
    s_all = hist.by_day[day]; profiles = oa._prior_profiles(c, day)
    horizon_end = oa._sessions_after(day)[-1]
    why = Counter(); roots_ok = Counter(); under = {}
    opts = {k: v for k, v in s_all.items() if k.endswith(('CE', 'PE'))}
    for sym, s in opts.items():
        if s['bars'] < oa.MIN_BARS: why['bars<60'] += 1; continue
        if not s['p1445'] or not oa._snapshot_complete(s.get('ts1445'), oa._as_of_epoch(day)): why['no 14:44 bar'] += 1; continue
        ct = oa.parse_option(sym, s.get('expiry'))
        if not ct or not ct['spot']: why['unparsed'] += 1; continue
        if ct['expiry'] <= horizon_end: why[f'expires by {horizon_end}'] += 1; continue
        if s['p1445'] < oa.MIN_PREMIUM: why['premium<2'] += 1; continue
        if s['notional_before'] < oa.MIN_TURNOVER: why['turnover<5L'] += 1; continue
        sp = ct['spot']
        if sp not in under: under[sp] = oa._underlying_features(sp, day, hist, profiles)
        if not under[sp]: why['no spot features'] += 1; continue
        mny = ct['side'] * math.log(under[sp]['price'] / ct['strike'])
        if abs(mny) > oa.MAX_MONEYNESS: why['moneyness>8%'] += 1; continue
        why['ELIGIBLE'] += 1; roots_ok[ct['root']] += 1
    spots_today = sum(1 for k in s_all if not k.endswith(('CE', 'PE')))
    spot_fail = sum(1 for v in under.values() if not v)
    return {'day': day, 'option_symbols_with_bars': len(opts), 'spot_symbols_with_bars': spots_today,
            'underlyings_seen': len(under), 'underlyings_without_features': spot_fail,
            'reasons': dict(why.most_common()), 'eligible_roots': len(roots_ok)}
for d in ('2026-10-01', '2026-10-07', '2026-10-09'):
    print(json.dumps(diag(d)))
