"""Suitability on REAL option outcomes: the agent's own labelled stock candidates (24 Aug - 7 Oct 2026).

Each system's score is taken at the PREVIOUS close (known before 14:45). For a CE row the score is used
as is; for a PE row it is negated (a high score should mean "buy calls, not puts"). Within each
(day, side) we rank contracts by that signed score and compare the top-10 underlyings (one contract
each, the nearest to the money... here: the one the pool lists first after sorting by score) with the
whole pool of that day and side. Outcome y = mean premium return over the next three closes.
Also: does the same score pick the right SIDE? (paired CE-minus-PE outcome per underlying-day).
"""
import json
import numpy as np, pandas as pd
exec(open('/w/systems.py').read().split("def daily_ic")[0])        # panel + SYSTEMS + MAGNITUDE

cand = pd.read_csv('/w/data/cands.csv')
cand['date'] = pd.to_datetime(cand.day)
cand['sym'] = 'NSE:' + cand.underlying + '-EQ'
prev_dates = C.index
def prev_score(score):
    s = score.shift(1)                                              # previous close's value, aligned to today
    stacked = s.stack().reset_index(); stacked.columns = ['date', 'sym', 'score']
    return stacked
out, side_rows = [], []
for name, spec in {**{k: v[0] for k, v in SYSTEMS.items()}, **MAGNITUDE}.items():
    sc = prev_score(spec.rank(axis=1, pct=True) - 0.5)             # within-day percentile, centred: sign = above/below the day's median
    x = cand.merge(sc, on=['date', 'sym'], how='inner').dropna(subset=['score'])
    if x.empty: continue
    is_mag = name in MAGNITUDE
    x['signed'] = x.score if is_mag else x.score * x.side
    per_day = []
    for (day, side), g in x.groupby(['day', 'side']):
        g = g.sort_values('signed', ascending=False).drop_duplicates('underlying')
        top = g.head(10); pool = x[(x.day == day) & (x.side == side)]
        per_day.append({'day': day, 'side': side, 'excess': top.y.mean() - pool.y.mean(), 'top_y': top.y.mean(),
                        'top_win': (top.y > 0).mean(), 'ic': g.signed.corr(g.y, method='spearman')})
    p = pd.DataFrame(per_day)
    for side, lab in ((1, 'CE'), (-1, 'PE')):
        q = p[p.side == side]; dd = q.groupby('day').excess.mean()
        out.append({'system': name, 'side': lab, 'days': len(dd), 'top10_mean_y': q.top_y.mean(), 'top10_win': q.top_win.mean(),
                    'excess_vs_pool': dd.mean(), 't': dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd))) if len(dd) > 2 else np.nan,
                    'IC': q.ic.mean()})
    if not is_mag:
        # side selection: for each underlying-day, does sign(score) pick the better of its ATM-ish CE and PE?
        pair = x.sort_values('entry').groupby(['day', 'underlying', 'side']).y.mean().unstack('side').dropna()
        s0 = x.drop_duplicates(['day', 'underlying']).set_index(['day', 'underlying']).score.reindex(pair.index)
        right = np.where(s0 > 0, pair[1] > pair[-1], pair[-1] > pair[1])
        days = pd.Series(right, index=pair.index.get_level_values(0)).groupby(level=0).mean()
        side_rows.append({'system': name, 'pairs': len(pair), 'side_accuracy': right.mean(),
                          'days_above_50pct': int((days > 0.5).sum()), 'days': len(days)})
res = pd.DataFrame(out); sides = pd.DataFrame(side_rows)
res.to_csv('/w/out/options_check.csv', index=False); sides.to_csv('/w/out/options_side.csv', index=False)
pool = cand.groupby(['day', 'side']).y.mean().groupby('side').mean()
agent = cand[cand.picked == 1].groupby(['day', 'side']).y.mean().sub(cand.groupby(['day', 'side']).y.mean()).dropna()
pd.set_option('display.width', 220)
print('pool mean y: CE %.3f PE %.3f | our agent top-10 excess: CE %+.3f PE %+.3f (days %d)' % (
    pool[1], pool[-1], agent.xs(1, level='side').mean(), agent.xs(-1, level='side').mean(), agent.index.get_level_values(0).nunique()))
for lab in ('CE', 'PE'):
    print(f'\n### {lab}: top-10 by system vs the same day\'s pool (y = mean premium return D+1..D+3)')
    print(res[res.side == lab].sort_values('excess_vs_pool', ascending=False).round(3).to_string(index=False))
print('\n### Side selection (sign of the score picks CE vs PE for the same underlying-day)')
print(sides.sort_values('side_accuracy', ascending=False).round(3).to_string(index=False))
