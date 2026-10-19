"""DD Nifty 4-indicator long system on index spot (Fyers 1-min, Jan 2021 - Oct 2026).

Rules (from the Upsurge podcast, 8:12-26:00 and 32:00-34:00):
  long when a candle CLOSES with  close > Supertrend(10,3)  and  RSI(14) > 70
                                  and close > previous-day pivot R1  and close > upper Bollinger(20,2)
  exit when a candle closes below Supertrend (Supertrend is the initial and trailing stop).
Intraday: entries from completed bars ending by 15:00, forced exit at 15:15. One position at a time;
a new entry needs a later bar meeting the conditions. Fill: next 1-min open after the decisive bar.
Ablation as in the video: ST -> +RSI -> +R1 -> +BB. Control: random entries on bars where only
Supertrend is up, same count per year, same exit rule (30 draws).
Usage: bt_spot.py <minutes>
"""
import sys, json
import numpy as np, pandas as pd

TF = int(sys.argv[1]); W = "/w/"; IST = 19800
raw = json.load(open(W + 'data/idx1m.json'))
NAMES = {'NSE:NIFTY50-INDEX': 'NIFTY', 'NSE:NIFTYBANK-INDEX': 'BANKNIFTY', 'BSE:SENSEX-INDEX': 'SENSEX'}

def rma(x, n):
    out = np.full(len(x), np.nan)
    if len(x) < n: return out
    out[n - 1] = np.nanmean(x[:n])
    for i in range(n, len(x)): out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out

def indicators(b):
    h, l, c = b.h.to_numpy(), b.l.to_numpy(), b.c.to_numpy()
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    atr = rma(tr, 10); mid = (h + l) / 2
    ub, lb = mid + 3 * atr, mid - 3 * atr
    d = np.zeros(len(c), int); line = np.full(len(c), np.nan)
    for i in range(len(c)):
        if np.isnan(atr[i]): continue
        if i == 0 or np.isnan(atr[i - 1]): d[i] = 1; line[i] = lb[i]; continue
        if not (lb[i] > lb[i - 1] or pc[i] < lb[i - 1]): lb[i] = lb[i - 1]
        if not (ub[i] < ub[i - 1] or pc[i] > ub[i - 1]): ub[i] = ub[i - 1]
        if d[i - 1] == -1: d[i] = 1 if c[i] > ub[i] else -1
        else: d[i] = -1 if c[i] < lb[i] else 1
        line[i] = lb[i] if d[i] == 1 else ub[i]
    delta = np.diff(c, prepend=c[0]); up = np.clip(delta, 0, None); dn = np.clip(-delta, 0, None)
    au, ad = rma(up[1:], 14), rma(dn[1:], 14)
    rsi = np.r_[np.nan, 100 - 100 / (1 + au / np.where(ad == 0, np.nan, ad))]
    rsi = np.where((ad == 0) & ~np.isnan(au), 100, rsi) if False else rsi
    s = pd.Series(c); bbu = (s.rolling(20).mean() - 2 * s.rolling(20).std(ddof=0)).to_numpy()
    return d, line, rsi, bbu

trades_all, ctrl_all = [], []
rng = np.random.default_rng(11)
for sym, rows in raw.items():
    m = pd.DataFrame(rows, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
    m = m[((m.ts + IST) % 86400 >= 9 * 3600 + 15 * 60) & ((m.ts + IST) % 86400 < 15 * 3600 + 30 * 60)].reset_index(drop=True)
    m['day'] = (m.ts + IST) // 86400
    daily = m.groupby('day').agg(h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('c', 'size'))
    daily = daily[daily.n >= 300]
    P = (daily.h + daily.l + daily.c) / 3; R1 = (2 * P - daily.h)   # S1 for the put mirror
    r1_next = pd.Series(R1.to_numpy(), index=daily.index).shift(1)            # R1 for a day = from the previous full session
    open_ = m.day * 86400 - IST + 33300
    m['start'] = open_ + (m.ts - open_) // (TF * 60) * TF * 60
    b = m.groupby('start').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), day=('day', 'first'), last=('ts', 'max')).reset_index()
    b['end'] = np.minimum(b.start + TF * 60, b['last'] + 60)
    d, line, rsi, bbu = indicators(b)
    b['dir'], b['st'], b['rsi'], b['bbu'] = d, line, rsi, bbu
    b['r1'] = b.day.map(r1_next)
    clock = (b.end + IST) % 86400
    ok_time = clock <= 15 * 3600
    conds = {"ST": (b.dir == -1) & (b.c < b.st),
             "ST+RSI": (b.dir == -1) & (b.c < b.st) & (b.rsi < 30),
             "ST+RSI+R1": (b.dir == -1) & (b.c < b.st) & (b.rsi < 30) & (b.c < b.r1),
             "ALL4": (b.dir == -1) & (b.c < b.st) & (b.rsi < 30) & (b.c < b.r1) & (b.c < b.bbu)}
    ts1, op1 = m.ts.to_numpy(), m.o.to_numpy()
    hi1 = -m.l.to_numpy()
    def fill(t):                     # next 1-min open at/after t
        j = np.searchsorted(ts1, t)
        return (j, ts1[j], op1[j]) if j < len(ts1) and (ts1[j] + IST) // 86400 == (t - 1 + IST) // 86400 else (None, None, None)
    bd, bend, bday = b.dir.to_numpy(), b.end.to_numpy(), b.day.to_numpy()
    def simulate(entry_bars, setname):
        out, busy_until = [], -1
        for i in entry_bars:
            if bend[i] <= busy_until: continue
            j0, ets, epx = fill(bend[i])
            if j0 is None: continue
            # exit: first later bar closing below Supertrend, else 15:15 the same day
            k = i + 1; exit_t = None
            while k < len(b) and bday[k] == bday[i]:
                if bd[k] == 1: exit_t = bend[k]; reason = 'st_flip'; break
                if (bend[k] + IST) % 86400 >= 15 * 3600 + 15 * 60: exit_t = bend[k]; reason = 'eod'; break
                k += 1
            if exit_t is None:
                exit_t = bday[i] * 86400 - IST + 15 * 3600 + 15 * 60; reason = 'eod'
            j1, xts, xpx = fill(exit_t)
            if j1 is None: j1 = np.searchsorted(ts1, exit_t) - 1; xts, xpx = ts1[j1], m.c.to_numpy()[j1]
            mfe = hi1[j0:j1 + 1].max() + epx if j1 >= j0 else 0
            out.append({'sym': NAMES[sym], 'set': setname, 'day': int(bday[i]), 'entry_ts': int(ets), 'exit_ts': int(xts),
                        'entry': epx, 'exit': xpx, "pts": epx - xpx, "ret_bps": (1 - xpx / epx) * 1e4,
                        'mfe_pts': mfe, 'mins': (xts - ets) / 60, 'reason': reason})
            busy_until = xts
        return out
    valid = ok_time & b.r1.notna() & ~np.isnan(b.rsi) & ~np.isnan(b.bbu)
    for name, cnd in conds.items():
        trades_all += simulate(np.nonzero((cnd & valid).to_numpy())[0], name)
    # random control: entries on ST-up bars, same number per calendar year as ALL4
    all4 = [t for t in trades_all if t['sym'] == NAMES[sym] and t['set'] == 'ALL4']
    per_year = pd.Series([pd.Timestamp(t['day'] * 86400, unit='s').year for t in all4]).value_counts()
    pool = np.nonzero((conds['ST'] & valid).to_numpy())[0]
    years = pd.to_datetime(b.day * 86400, unit='s').dt.year.to_numpy()
    for draw in range(30):
        pick = []
        for y, n in per_year.items():
            cand = pool[years[pool] == y]
            if len(cand): pick += sorted(rng.choice(cand, size=min(n, len(cand)), replace=False).tolist())
        for t in simulate(sorted(pick), f'RANDOM_ST'):
            t['draw'] = draw; ctrl_all.append(t)
    print(sym, {k: sum(1 for t in trades_all if t['sym'] == NAMES[sym] and t['set'] == k) for k in conds}, flush=True)

pd.DataFrame(trades_all).to_csv(W + f'out/pe_spot_trades_{TF}m.csv', index=False)
pd.DataFrame(ctrl_all).to_csv(W + f'out/pe_spot_random_{TF}m.csv', index=False)
