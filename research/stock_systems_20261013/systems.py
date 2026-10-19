"""Known stock-selection systems, tested on our F&O stock universe (Fyers daily, 2021 -> Oct 2026).

Each system is a cross-sectional score at the close of day t (a stand-in for our 14:45 decision; the
option agent decides 45 minutes before the close). Two questions per system and horizon h = 1, 2, 3:
  DIRECTION  -- does a high score mean the stock beats the day's universe average (excess return)?
                Top decile = what we would buy CALLS on, bottom decile = PUTS.
  MAGNITUDE  -- does a high score mean a bigger absolute move (what an option buyer is paid for)?
Daily Spearman IC averaged over days, t-stat over days (h > 1 overlaps: read its t-stat as optimistic),
decile excess returns, and per-year IC signs for stability.
Caveat: the universe is TODAY's F&O list (survivorship bias), and close-to-close returns, not options.
"""
import json
import numpy as np, pandas as pd

D1 = json.load(open('/w/data/daily_2021_2024.json'))
D2 = json.load(open('/w/data/daily_2024_2026.json'))['bars']
frames = {}
for sym in set(D1) | set(D2):
    rows = list(D1.get(sym, [])) + list(D2.get(sym, []))
    if not rows: continue
    df = pd.DataFrame(rows, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
    df['date'] = pd.to_datetime(df.ts + 19800, unit='s').dt.normalize()
    frames[sym] = df.drop_duplicates('date').set_index('date').sort_index()
INDEX = {'NSE:NIFTY50-INDEX', 'NSE:NIFTYBANK-INDEX', 'BSE:SENSEX-INDEX', 'NSE:MIDCPNIFTY-INDEX'}
stocks = sorted(s for s in frames if s not in INDEX)
W = {k: pd.DataFrame({s: frames[s][k] for s in stocks}) for k in ('o', 'h', 'l', 'c', 'v')}
O, H, L, C, V = W['o'], W['h'], W['l'], W['c'], W['v']
nifty = frames['NSE:NIFTY50-INDEX'].c.reindex(C.index)
ret = C.pct_change(fill_method=None)
print('panel', C.shape, C.index.min().date(), '->', C.index.max().date(), flush=True)

def rsi(close, n):
    d = close.diff(); up = d.clip(lower=0); dn = (-d).clip(lower=0)
    au = up.ewm(alpha=1 / n, adjust=False).mean(); ad = dn.ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + au / ad.replace(0, np.nan))

sma200 = C.rolling(200).mean()
rng = (H - L).replace(0, np.nan)
tr = pd.concat([H - L, (H - C.shift()).abs(), (L - C.shift()).abs()]).groupby(level=0).max()
atr5, atr50 = tr.rolling(5).mean(), tr.rolling(50).mean()
SYSTEMS = {  # name: (score, family, what the literature claims)
    'mom_12_1': (C.shift(21) / C.shift(252) - 1, 'momentum', 'Jegadeesh-Titman 12-1: winners keep winning'),
    'mom_6_1': (C.shift(21) / C.shift(126) - 1, 'momentum', '6-month momentum skipping the last month'),
    'mom_3m': (C / C.shift(63) - 1, 'momentum', '3-month price momentum'),
    'rs_20d_vs_nifty': (C / C.shift(20) - 1).sub(nifty / nifty.shift(20) - 1, axis=0) if True else None,
    'high_52w': (C / H.rolling(252).max(), 'momentum', 'George-Hwang: near the 52-week high outperforms'),
    'trend_200dma': (C / sma200 - 1, 'trend', 'above the 200-day average outperforms'),
    'donchian_55_pos': ((C - L.rolling(55).min()) / (H.rolling(55).max() - L.rolling(55).min()), 'trend', 'turtle-style breakout position'),
    'reversal_1d': (-ret, 'reversal', '1-day losers bounce'),
    'reversal_5d': (-(C / C.shift(5) - 1), 'reversal', 'Lehmann/Jegadeesh 1-week reversal'),
    'rsi2_oversold': (-rsi(C, 2), 'reversal', 'Connors RSI(2): low RSI(2) bounces'),
    'rsi2_oversold_uptrend': ((-rsi(C, 2)).where(C > sma200), 'reversal', 'RSI(2) bounce only above the 200-day'),
    'ibs_low': (-((C - L) / rng), 'reversal', 'internal bar strength: closing near the low bounces'),
    'boll_pctb_low': (-((C - C.rolling(20).mean()) / (2 * C.rolling(20).std())), 'reversal', 'Bollinger %b reversion'),
    'gap_reversal': (-(O / C.shift() - 1), 'reversal', 'overnight gap fades'),
    'intraday_reversal': (-(C / O - 1), 'reversal', 'today\'s open-to-close move fades'),
    'low_vol_60d': (-ret.rolling(60).std(), 'low risk', 'Ang et al. / BAB: low volatility outperforms'),
    'max_effect': (-ret.rolling(21).max(), 'low risk', 'Bali MAX: lottery stocks underperform'),
    'volume_shock': (np.log(V / V.shift().rolling(20).mean()), 'attention', 'Gervais: high-volume stocks outperform'),
}
SYSTEMS['rs_20d_vs_nifty'] = (SYSTEMS['rs_20d_vs_nifty'], 'momentum', 'relative strength vs NIFTY, 20 days')
MAGNITUDE = {  # score that should predict a BIG move, either way
    'vol_contraction_atr5_50': -(atr5 / atr50),
    'nr7_narrowest_range': (rng == rng.rolling(7).min()).astype(float).where(rng.notna()),
    'volume_shock_abs': np.log(V / V.shift().rolling(20).mean()),
    'recent_abs_move': ret.abs(),
    'realised_vol_20d': ret.rolling(20).std(),
}

def daily_ic(score, target):
    s, t = score.rank(axis=1), target.rank(axis=1)
    ok = s.notna() & t.notna()
    s, t = s.where(ok), t.where(ok)
    n = ok.sum(axis=1)
    sc, tc = s.sub(s.mean(axis=1), axis=0), t.sub(t.mean(axis=1), axis=0)
    ic = (sc * tc).sum(axis=1) / np.sqrt((sc ** 2).sum(axis=1) * (tc ** 2).sum(axis=1))
    return ic[n >= 50]

def deciles(score, target):
    q = score.rank(axis=1, pct=True)
    top = target.where(q >= 0.9).mean(axis=1); bot = target.where(q <= 0.1).mean(axis=1)
    return top, bot

rows, mag_rows = [], []
start = pd.Timestamp('2022-01-01')        # 2021 is the warm-up year for 12-month and 200-day signals
for h in (1, 2, 3):
    fwd = C.shift(-h) / C - 1
    excess = fwd.sub(fwd.mean(axis=1), axis=0)
    for name, (score, fam, claim) in SYSTEMS.items():
        sc = score.loc[start:]
        ic = daily_ic(sc, excess.loc[start:]).dropna()
        top, bot = deciles(sc, excess.loc[start:])
        topraw, botraw = deciles(sc, fwd.loc[start:])
        yearly = ic.groupby(ic.index.year).mean()
        rows.append({'system': name, 'family': fam, 'h': h, 'IC': ic.mean(), 't': ic.mean() / (ic.std() / np.sqrt(len(ic))),
                     'top_excess_bps': top.mean() * 1e4, 'bottom_excess_bps': bot.mean() * 1e4,
                     'top_up_rate': (topraw > 0).mean(), 'bottom_down_rate': (botraw < 0).mean(),
                     'years_same_sign': int((np.sign(yearly) == np.sign(ic.mean())).sum()), 'years': len(yearly),
                     **{f'IC_{y}': v for y, v in yearly.items()}, 'claim': claim})
    absfwd = fwd.abs()
    for name, score in MAGNITUDE.items():
        ic = daily_ic(score.loc[start:], absfwd.loc[start:]).dropna()
        yearly = ic.groupby(ic.index.year).mean()
        mag_rows.append({'predictor': name, 'h': h, 'IC_abs_move': ic.mean(), 't': ic.mean() / (ic.std() / np.sqrt(len(ic))),
                         'years_same_sign': int((np.sign(yearly) == np.sign(ic.mean())).sum()), 'years': len(yearly)})
res = pd.DataFrame(rows); mag = pd.DataFrame(mag_rows)
res.to_csv('/w/out/systems_direction.csv', index=False); mag.to_csv('/w/out/systems_magnitude.csv', index=False)
# regime split: market (NIFTY) up vs down months
fwd1 = C.shift(-1) / C - 1; exc1 = fwd1.sub(fwd1.mean(axis=1), axis=0)
mret = nifty.resample('ME').last().pct_change()
up_months = set(mret[mret > 0].index.to_period('M'));
reg = []
for name, (score, fam, _c) in SYSTEMS.items():
    ic = daily_ic(score.loc[start:], exc1.loc[start:]).dropna()
    per = ic.index.to_period('M')
    reg.append({'system': name, 'IC_up_months': ic[[p in up_months for p in per]].mean(), 'IC_down_months': ic[[p not in up_months for p in per]].mean()})
pd.DataFrame(reg).to_csv('/w/out/systems_regime.csv', index=False)
pd.set_option('display.width', 250)
for h in (1, 3):
    print(f'\n### DIRECTION, h={h} (excess over the day\'s universe average), 2022 -> Oct 2026')
    x = res[res.h == h].sort_values('t', ascending=False)
    print(x[['system', 'family', 'IC', 't', 'top_excess_bps', 'bottom_excess_bps', 'top_up_rate', 'bottom_down_rate', 'years_same_sign', 'years']].round(4).to_string(index=False))
print('\n### MAGNITUDE (IC vs absolute forward return)')
print(mag.round(4).to_string(index=False))
print('\n### Regime: IC (h=1) in NIFTY up months vs down months')
print(pd.DataFrame(reg).round(4).to_string(index=False))
