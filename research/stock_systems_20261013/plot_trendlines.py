"""Visual examples of the trendline detector exactly as tested (k=5 daily, third-touch version).

Re-runs the same swing/line logic but also records each line's anchors, touches and the bar it fired
on, then draws a few real events: candles, confirmed swing highs/lows (marked on the bar they belong
to; they became KNOWN k bars later), the line from its two anchors extended to the event, touches,
and the outcome 3 sessions later.
"""
import json
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

exec(open('/w/systems.py').read().split("def daily_ic")[0])      # frames (daily OHLC per symbol)
K = 5

def detect(o, h, l, c, k=K):
    n = len(c)
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    sh, sl, events = [], [], []
    res = sup = None; touches = {"res": [], "sup": []}
    dead = set()
    for t in range(2 * k + 1, n):
        i = t - k
        if h[i] == h[i - k:i + k + 1].max(): sh.append((i, h[i], t))      # (bar, price, known at)
        if l[i] == l[i - k:i + k + 1].min(): sl.append((i, l[i], t))
        # a broken line stays broken: never rebuild it from the same two anchors
        if len(sh) >= 2 and sh[-1][1] < sh[-2][1] and (res is None or res[2] != sh[-1][0]) and (sh[-2][0], sh[-1][0]) not in dead:
            res = (sh[-2][0], sh[-2][1], sh[-1][0], sh[-1][1], t); touches['res'] = []
        if len(sl) >= 2 and sl[-1][1] > sl[-2][1] and (sup is None or sup[2] != sl[-1][0]) and (sl[-2][0], sl[-1][0]) not in dead:
            sup = (sl[-2][0], sl[-2][1], sl[-1][0], sl[-1][1], t); touches['sup'] = []
        a = atr[t]
        if not np.isfinite(a): continue
        for kind in ('res', 'sup'):
            line = res if kind == 'res' else sup
            if line is None: continue
            i1, p1, i2, p2, born = line
            val = lambda j: p2 + (p2 - p1) / (i2 - i1) * (j - i2)
            if kind == 'res':
                if c[t] > val(t) + 0.1 * a and c[t - 1] <= val(t - 1):
                    if touches['res']: events.append(dict(t=t, kind='breakout', line=line, touches=list(touches['res']), dir=1))
                    dead.add((res[0], res[2])); res = None
                elif c[t] > val(t): dead.add((res[0], res[2])); res = None
                elif h[t] >= val(t) - 0.25 * a and c[t] < val(t):
                    if c[t] < o[t]: events.append(dict(t=t, kind='bounce_down', line=line, touches=list(touches['res']), dir=-1))
                    touches['res'].append(t)
            else:
                if c[t] < val(t) - 0.1 * a and c[t - 1] >= val(t - 1):
                    if touches['sup']: events.append(dict(t=t, kind='breakdown', line=line, touches=list(touches['sup']), dir=-1))
                    dead.add((sup[0], sup[2])); sup = None
                elif c[t] < val(t): dead.add((sup[0], sup[2])); sup = None
                elif l[t] <= val(t) + 0.25 * a and c[t] > val(t):
                    if c[t] > o[t]: events.append(dict(t=t, kind='bounce_up', line=line, touches=list(touches['sup']), dir=1))
                    touches['sup'].append(t)
    return events, sh, sl

def draw(ax, sym, df, ev, sh, sl, title):
    t = ev['t']; i1, p1, i2, p2, born = ev['line']
    lo, hi = max(0, i1 - 8), min(len(df) - 1, t + 8)
    d = df.iloc[lo:hi + 1]
    x = np.arange(lo, hi + 1)
    for xi, (o_, h_, l_, c_) in zip(x, d[['o', 'h', 'l', 'c']].to_numpy()):
        col = '#2e7d32' if c_ >= o_ else '#c62828'
        ax.plot([xi, xi], [l_, h_], color=col, lw=0.8)
        ax.add_patch(Rectangle((xi - 0.3, min(o_, c_)), 0.6, max(abs(c_ - o_), 1e-9), color=col))
    for j, p, known in sh:
        if lo <= j <= hi: ax.scatter(j, p * 1.004, marker='v', color='#6a1b9a', s=28, zorder=3)
    for j, p, known in sl:
        if lo <= j <= hi: ax.scatter(j, p * 0.996, marker='^', color='#1565c0', s=28, zorder=3)
    xs = np.array([i1, t + 3])
    ys = p2 + (p2 - p1) / (i2 - i1) * (xs - i2)
    color = '#6a1b9a' if ev['kind'] in ('breakout', 'bounce_down') else '#1565c0'
    ax.plot(xs, ys, color=color, lw=1.6, ls='-', label='trendline (2 swing anchors)')
    ax.scatter([i1, i2], [p1, p2], s=90, facecolors='none', edgecolors=color, lw=1.8, zorder=4, label='anchors')
    ax.axvline(born, color='grey', ls=':', lw=1)
    ax.text(born, ax.get_ylim()[1] if False else d.h.max() * 1.01, ' line known', fontsize=7, color='grey', va='bottom')
    for tt in ev['touches']:
        ax.scatter(tt, (d.h if color == '#6a1b9a' else d.l).get(df.index[tt], np.nan), marker='o', s=40, color='orange', zorder=4)
    yv = df.c.iloc[t]
    ax.scatter(t, yv, marker='*', s=220, color='black', zorder=5, label=ev['kind'])
    after = df.c.iloc[t + 3] if t + 3 < len(df) else np.nan
    move = (after / yv - 1) * 100
    good = (move > 0) == (ev['dir'] > 0)
    ax.set_title(f"{title}\n{sym.split(':')[1]}  {ev['kind']} on {df.index[t].date()}  ->  3 sessions later {move:+.1f}% ({'right' if good else 'wrong'} way)", fontsize=9)
    ticks = list(range(lo, hi + 1, max(1, (hi - lo) // 6)))
    ax.set_xticks(ticks); ax.set_xticklabels([df.index[j].strftime('%d %b %y') for j in ticks], fontsize=7, rotation=0)
    ax.tick_params(axis='y', labelsize=7); ax.grid(alpha=0.2)
    ax.legend(fontsize=7, loc='best')

wanted = [('NSE:RELIANCE-EQ', 'breakout'), ('NSE:HDFCBANK-EQ', 'breakdown'), (['NSE:SBIN-EQ', 'NSE:ICICIBANK-EQ', 'NSE:AXISBANK-EQ', 'NSE:LT-EQ', 'NSE:TCS-EQ', 'NSE:ITC-EQ', 'NSE:BHARTIARTL-EQ'], 'bounce_up'),
          ('NSE:INFY-EQ', 'bounce_down'), ('NSE:TATASTEEL-EQ', 'breakout'), ('NSE:NIFTY50-INDEX', 'breakout')]
fig, axes = plt.subplots(3, 2, figsize=(15, 13))
for ax, (syms, kind) in zip(axes.flat, wanted):
    for sym in (syms if isinstance(syms, list) else [syms]):
        df = frames[sym][['o', 'h', 'l', 'c']].dropna()
        df = df[df.index >= '2025-06-01']
        evs, sh, sl = detect(df.o.to_numpy(), df.h.to_numpy(), df.l.to_numpy(), df.c.to_numpy())
        cands = [e for e in evs if e['kind'] == kind and e['t'] + 3 < len(df) and e['t'] - e['line'][0] < 60]
        if cands: break
    if not cands: ax.set_title(f'{sym} {kind}: none since Jun 2025'); continue
    ev = cands[-1] if kind != 'breakout' or sym != 'NSE:TATASTEEL-EQ' else cands[len(cands) // 2]
    draw(ax, sym, df, ev, sh, sl, {'breakout': 'Breakout: first close above a falling trendline',
                                    'breakdown': 'Breakdown: first close below a rising trendline',
                                    'bounce_up': 'Bounce up: touches rising support, closes above it, green candle',
                                    'bounce_down': 'Bounce down: touches falling resistance, closes below it, red candle'}[kind])
fig.suptitle('How the trendline detector draws lines (daily bars, swing size k=5, line valid after a third touch)\n'
             '▼ swing high  ▲ swing low (each confirmed only 5 bars later)  ○ anchors  ● orange = touches  ★ signal bar  dotted = when the line became known',
             fontsize=11)
fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig('/w/out/trendline_examples.png', dpi=130)
print('saved')
