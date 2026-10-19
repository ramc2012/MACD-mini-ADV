"""One row per index per session, as of 14:45 IST, 2021 -> 12 Oct 2026, from Fyers 1-min spot.

Features: the live index model's directional features that can be computed from spot (index spot has
no volume, so VWAP/spike features are 0 live as well), plus multi-timeframe trend/extreme features,
gap, last hour, ATR-scaled moves, VIX. Labels: the index lane's own plan from the 14:45 price
(stop 1.0 x ATR14, target 1.5 x ATR14 on spot, exit at the third session's close) for BOTH sides,
and close-to-close returns r1..r3. Minute order inside a minute is unknown: a minute that touches
both stop and target counts as the stop (as index_trades._outcome does per session, but finer).
"""
import json
import numpy as np, pandas as pd

IST = 19800
A = json.load(open('/r/dd_nifty_4ind_20261009/data/idx1m.json'))
X = json.load(open('/w/data/idx_extra.json'))
SYMS = {'NIFTY': 'NSE:NIFTY50-INDEX', 'BANKNIFTY': 'NSE:NIFTYBANK-INDEX', 'SENSEX': 'BSE:SENSEX-INDEX', 'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX'}

def minutes(sym):
    rows = list(A.get(sym, [])) + list(X.get(sym, []))
    m = pd.DataFrame(rows, columns=['ts', 'o', 'h', 'l', 'c', 'v']).drop_duplicates('ts').sort_values('ts')
    clock = (m.ts + IST) % 86400
    m = m[(clock >= 9 * 3600 + 15 * 60) & (clock < 15 * 3600 + 30 * 60)].copy()
    m['day'] = (m.ts + IST) // 86400
    m['clock'] = (m.ts + IST) % 86400
    return m.reset_index(drop=True)

def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()

def rsi(c, n=14):
    c = np.asarray(c, float)
    if len(c) < n + 2: return np.nan
    d = np.diff(c); up = np.clip(d, 0, None); dn = np.clip(-d, 0, None)
    au, ad = up[:n].mean(), dn[:n].mean()
    for i in range(n, len(d)):
        au = (au * (n - 1) + up[i]) / n; ad = (ad * (n - 1) + dn[i]) / n
    return 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)

def trend(c):
    if len(c) < 55: return 0
    e20, e50 = ema(c, 20)[-1], ema(c, 50)[-1]
    return 1 if c[-1] > e20 > e50 else -1 if c[-1] < e20 < e50 else 0

vix = pd.DataFrame(X['VIX_D'], columns=['ts', 'o', 'h', 'l', 'c', 'v'])
vix['day'] = (vix.ts + IST) // 86400
vix = vix.drop_duplicates('day').set_index('day').c

CUT = 14 * 3600 + 45 * 60
data = {}
for name, sym in SYMS.items():
    m = minutes(sym)
    days = m.groupby('day').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('c', 'size'))
    days = days[days.n >= 300]
    data[name] = (m, days)
nifty_close = data['NIFTY'][1].c

rows = []
for name, (m, days) in data.items():
    dlist = list(days.index)
    by_day = {d: g for d, g in m.groupby('day')}
    closes = days.c.to_numpy(); highs = days.h.to_numpy(); lows = days.l.to_numpy()
    tr = np.maximum(highs - lows, np.maximum(abs(highs - np.r_[closes[0], closes[:-1]]), abs(lows - np.r_[closes[0], closes[:-1]])))
    for i, d in enumerate(dlist):
        if i < 60 or i + 3 >= len(dlist): continue
        g = by_day[d]; before = g[g.clock < CUT]
        if len(before) < 300 or before.clock.iloc[-1] < CUT - 120: continue
        p = before.c.iloc[-1]; o = before.o.iloc[0]
        prev = closes[i - 1]; atr = tr[i - 14:i].mean()
        hist_c = np.append(closes[:i], p)
        # 30-min and 60-min bars over the last 25 sessions up to 14:45
        recent = pd.concat([by_day[x] for x in dlist[max(0, i - 25):i]] + [before])
        b30 = recent.groupby([recent.day, recent.clock.sub(33300) // 1800]).c.last().to_numpy()
        b60 = recent.groupby([recent.day, recent.clock.sub(33300) // 3600]).c.last().to_numpy()
        b15 = before.groupby(before.clock.sub(33300) // 900).c.last().to_numpy()
        macd = ema(b30, 12) - ema(b30, 26); hist = macd - ema(macd, 9)
        rets = np.diff(np.log(before.c.to_numpy())); rv = rets.std()
        prior_rv = np.median([np.diff(np.log(by_day[x].c.to_numpy())).std() for x in dlist[i - 10:i]])
        h20, l20 = max(highs[i - 20:i].max(), before.h.max()), min(lows[i - 20:i].min(), before.l.min())
        p1345 = g[g.clock < CUT - 3600].c.iloc[-1]
        wk = pd.Series(hist_c, index=pd.to_datetime(list(dlist[:i]) + [d], unit='D')).resample('W-FRI').last().dropna().to_numpy()
        streak = 0
        for k in range(i - 1, 0, -1):
            s = np.sign(closes[k] - closes[k - 1])
            if streak == 0: streak = s
            elif np.sign(streak) == s: streak += s
            else: break
        f = {
            'u_ret_intraday': p / o - 1, 'u_ret_1d': p / prev - 1, 'u_ret_5d': p / closes[i - 5] - 1, 'u_ret_20d': p / closes[i - 20] - 1,
            'u_day_range_pos': (p - before.l.min()) / (before.h.max() - before.l.min()) - 0.5 if before.h.max() > before.l.min() else 0,
            'u_range20_pos': (p - l20) / (h20 - l20) - 0.5 if h20 > l20 else 0,
            'u_macd_hist': hist[-1] / p, 'u_rv_ratio': rv / prior_rv - 1 if prior_rv else 0,
            'gap': o / prev - 1, 'last_hour': p / p1345 - 1, 'move_atr': (p - prev) / atr, 'atr_pct': atr / p,
            'dist_ema20_atr': (p - ema(hist_c, 20)[-1]) / atr, 'rsi_d': rsi(hist_c[-120:]), 'rsi_h': rsi(b60[-120:]),
            'rsi_15': rsi(b15) if len(b15) >= 16 else 50.0, 'trend_d': trend(hist_c), 'trend_w': trend(wk), 'trend_h': trend(b60),
            'vix': vix.get(d - 1, np.nan), 'vix_chg5': (vix.get(d - 1, np.nan) / vix.get(dlist[i - 6], np.nan) - 1) if dlist[i - 6] in vix.index else np.nan,
            'dow': pd.Timestamp(d, unit='D').weekday(), 'streak': streak,
        }
        # labels: the lane's ATR plan, both sides, scanned minute by minute from 14:45 to D+3 close
        path = pd.concat([g[g.clock >= CUT]] + [by_day[x] for x in dlist[i + 1:i + 4]])
        lab = {'r1': closes[i + 1] / p - 1, 'r2': closes[i + 2] / p - 1, 'r3': closes[i + 3] / p - 1}
        PLANS = {"": (1.5, 1.0, 3), "_t1s1": (1.0, 1.0, 3), "_t075s1": (0.75, 1.0, 3), "_t1s075": (1.0, 0.75, 3), "_d1t075s075": (0.75, 0.75, 1), "_t2s1": (2.0, 1.0, 3)}
        for suffix, (tm, sm, hz) in PLANS.items():
          path_h = pd.concat([g[g.clock >= CUT]] + [by_day[x] for x in dlist[i + 1:i + 1 + hz]])
          for side, tag in ((1, "ce"), (-1, "pe")):
            stop, tgt = p - side * sm * atr, p + side * tm * atr
            path = path_h
            hit_stop = (path.l <= stop) if side > 0 else (path.h >= stop)
            hit_tgt = (path.h >= tgt) if side > 0 else (path.l <= tgt)
            js = np.argmax(hit_stop.to_numpy()) if hit_stop.any() else 10**9
            jt = np.argmax(hit_tgt.to_numpy()) if hit_tgt.any() else 10**9
            if jt < js: out, ret = "target", tm * atr / p
            elif js < 10**9: out, ret = "stop", -sm * atr / p
            else: out, ret = "time", side * (closes[i + hz] / p - 1)
            lab[f"{tag}{suffix}_path"] = out; lab[f"{tag}{suffix}_ret"] = ret
        rows.append({'und': name, 'day': pd.Timestamp(d, unit='D').date().isoformat(), 'spot': p, 'atr': atr, **f, **lab})
df = pd.DataFrame(rows)
# relative strength vs NIFTY over 5 sessions
n5 = df[df.und == 'NIFTY'].set_index('day').u_ret_5d
df['u_rs_5d'] = df.u_ret_5d - df.day.map(n5).fillna(0)
df.to_csv('/w/out/index_days.csv', index=False)
print(df.groupby('und').agg(n=('day', 'size'), first=('day', 'min'), last=('day', 'max')).to_string())
print(df[['ce_path', 'pe_path']].apply(pd.Series.value_counts).to_string())
