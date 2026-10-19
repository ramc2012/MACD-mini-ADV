"""Composite 'buy the dip in an uptrend / sell the pop' scores: 5-year spot deciles and real-option top-10s."""
import numpy as np, pandas as pd
exec(open('/w/systems.py').read().split("def daily_ic")[0])
pct = lambda x: x.rank(axis=1, pct=True)
uptrend = pct(C / sma200 - 1); mom12 = pct(C.shift(21) / C.shift(252) - 1)
oversold = pct(-rsi(C, 2)); loser5 = pct(-(C / C.shift(5) - 1)); low_ibs = pct(-((C - L) / rng))
COMPOSITES = {   # higher = better CALL candidate; for puts we take the LOWEST score of the PE composite
    'CE: uptrend(200dma)+oversold(RSI2)': uptrend + oversold,
    'CE: mom12-1 + 5d loser': mom12 + loser5,
    'CE: uptrend + oversold + low IBS': uptrend + oversold + low_ibs,
    'PE: 5d winner (lowest 5d-loser score)': loser5,
    'PE: overbought RSI2 + 5d winner': oversold + loser5,
    'PE: downtrend + overbought (Connors short)': uptrend + oversold,
}
start = pd.Timestamp('2022-01-01')
print('### 5-year spot check (excess return of the chosen decile vs the day\'s universe, bps; CE wants +, PE wants -)')
for h in (1, 3):
    fwd = C.shift(-h) / C - 1; exc = fwd.sub(fwd.mean(axis=1), axis=0)
    for name, s in COMPOSITES.items():
        q = s.loc[start:].rank(axis=1, pct=True)
        pick = q >= 0.9 if name.startswith('CE') else q <= 0.1
        e = exc.loc[start:].where(pick).mean(axis=1).dropna()
        yr = e.groupby(e.index.year).mean() * 1e4
        print(f"  h={h} {name:44s} mean {e.mean()*1e4:+6.1f} bps (t {e.mean()/(e.std()/np.sqrt(len(e))):+.1f}) by year " + ' '.join(f'{y}:{v:+.0f}' for y, v in yr.items()))
cand = pd.read_csv('/w/data/cands.csv'); cand['date'] = pd.to_datetime(cand.day); cand['sym'] = 'NSE:' + cand.underlying + '-EQ'
print('\n### Real options (24 Aug - 7 Oct): top-10 underlyings by composite vs the same day\'s pool (y = mean premium return D+1..D+3)')
for name, s in COMPOSITES.items():
    side = 1 if name.startswith('CE') else -1
    st = s.shift(1).stack().reset_index(); st.columns = ['date', 'sym', 'score']
    x = cand[cand.side == side].merge(st, on=['date', 'sym']).dropna(subset=['score'])
    x['key'] = x.score if side == 1 else -x.score
    rows = []
    for day, g in x.groupby('day'):
        top = g.sort_values('key', ascending=False).drop_duplicates('underlying').head(10)
        rows.append((day, top.y.mean() - g.y.mean(), (top.y > 0).mean(), top.y.mean()))
    r = pd.DataFrame(rows, columns=['day', 'ex', 'win', 'y'])
    halves = r.groupby(r.day < '2026-09-15').ex.mean()
    print(f"  {name:44s} excess {r.ex.mean()*100:+5.1f} pts (t {r.ex.mean()/(r.ex.std()/np.sqrt(len(r))):+.1f}) win {r.win.mean():.2f} top-10 mean {r.y.mean()*100:+5.1f}% | Aug-mid Sep {halves.get(True, np.nan)*100:+.1f}  later {halves.get(False, np.nan)*100:+.1f}")
ours = cand[cand.picked == 1].groupby(['day', 'side']).y.mean().sub(cand.groupby(['day', 'side']).y.mean()).dropna()
print(f"  {'our agent (17 days with picks)':44s} CE {ours.xs(1, level='side').mean()*100:+.1f} pts, PE {ours.xs(-1, level='side').mean()*100:+.1f} pts")
