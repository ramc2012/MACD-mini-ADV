"""How long does a sector's relative performance (vs NIFTY 50) last, on daily bars?

A. Decay curve: rank sectors every day by their last-L-day return relative to NIFTY; follow the top 3 minus the bottom 3
   for the next 1..60 sessions. Where the cumulative spread stops rising is how long the edge lasts. Overlapping daily
   samples, so t-stats use Newey-West with lag = horizon.
B. Month-start undercurrent: same, but formed only on the first 3 / 5 sessions of each month, followed for 40 sessions
   (through and past month end), against the same curve formed on every day.
C. Rotation (daily RRG, the app's formula: EMA-5 RS, 14-day z-scores, EMA-3 momentum): how long sectors stay in a
   quadrant, where they go next, and the next 5/10/20-session relative return by quadrant.
D. Leadership runs: once a sector enters the top 3 by 20-day relative return, how many sessions it stays there.
Stocks (today's F&O list, survivorship-biased) are run through A for comparison.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
raw = json.load(open(HERE.parent / "early_month_momentum_20261017" / "data_daily.json"))
px = pd.DataFrame({s: pd.Series({pd.Timestamp(r[0], unit="s", tz="Asia/Kolkata").normalize().tz_localize(None): r[4] for r in rows})
                   for s, rows in raw.items() if len(rows) >= 200}).sort_index()
px = px[px.index >= "2019-01-01"]
NIFTY = "NSE:NIFTY50-INDEX"
BROAD = {NIFTY, "BSE:SENSEX-INDEX", "NSE:NIFTYNXT50-INDEX", "NSE:MIDCPNIFTY-INDEX"}
SECTORS = [c for c in px.columns if c.endswith("-INDEX") and c not in BROAD]
STOCKS = [c for c in px.columns if c.endswith("-EQ")]
name = lambda s: s.split(":")[1].replace("-INDEX", "").replace("NIFTY", "") or s
lines = []
def say(*a):
    text = " ".join(str(x) for x in a); print(text); lines.append(text)

logp = np.log(px)
rel = logp.sub(logp[NIFTY], axis=0)           # log relative price vs NIFTY: differences are relative returns
say(f"daily {px.index[0].date()} -> {px.index[-1].date()} sessions {len(px)}  sectors {len(SECTORS)}: {', '.join(name(s) for s in SECTORS)}")


def nw_t(x, lag):
    x = np.asarray(x, float); x = x[~np.isnan(x)]; n = len(x)
    if n < 10: return np.nan
    e = x - x.mean(); var = e @ e / n
    for k in range(1, min(lag, n - 1) + 1):
        var += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return x.mean() / np.sqrt(var / n)


def spread_paths(cols, L, H, bucket, form_mask=None):
    """Per formation day: cumulative top-minus-bottom relative return for d = 1..H sessions ahead."""
    r = rel[cols]
    form = r - r.shift(L)
    out = []
    idx = np.where(form_mask)[0] if form_mask is not None else range(L, len(r) - 1)
    vals, fv = r.values, form.values
    for t in idx:
        if t < L or t + 1 >= len(r): continue
        f = fv[t]; ok = ~np.isnan(f) & ~np.isnan(vals[t])
        if ok.sum() < 2 * bucket: continue
        order = np.argsort(f[ok]); cols_ok = np.where(ok)[0]
        top, bot = cols_ok[order[-bucket:]], cols_ok[order[:bucket]]
        path = np.full(H, np.nan)
        end = min(H, len(r) - 1 - t)
        fut = vals[t + 1:t + 1 + end] - vals[t]
        path[:end] = np.nanmean(fut[:, top], 1) - np.nanmean(fut[:, bot], 1)
        out.append(path)
    return np.array(out)


H = 60
HORIZONS = [1, 2, 3, 5, 10, 15, 20, 30, 40, 60]
say("\n== A. Decay: rank by last-L-day relative return, top 3 minus bottom 3 sectors, cumulative over next H sessions (%) ==")
say("   L \\ H " + "".join(f"{h:>8d}" for h in HORIZONS) + "   peak (day, %)")
curves = {}
for L in (1, 3, 5, 10, 20, 60, 120):
    P = spread_paths(SECTORS, L, H, 3)
    curves[L] = P
    m = np.nanmean(P, 0) * 100
    ts = [nw_t(P[:, h - 1], h) for h in HORIZONS]
    peak = int(np.nanargmax(m)) + 1
    say(f"  {L:4d}  " + "".join(f"{m[h - 1]:+7.2f}{'*' if abs(t) >= 2 else ' '}" for h, t in zip(HORIZONS, ts)) + f"   day {peak}, {m[peak - 1]:+.2f}")
say("   * = |Newey-West t| >= 2")

say("\n   Stocks (top 20 minus bottom 20 of 210, survivorship-biased):")
for L in (5, 20, 60):
    P = spread_paths(STOCKS, L, H, 20)
    m = np.nanmean(P, 0) * 100; ts = [nw_t(P[:, h - 1], h) for h in HORIZONS]
    peak = int(np.nanargmax(m)) + 1
    say(f"  {L:4d}  " + "".join(f"{m[h - 1]:+7.2f}{'*' if abs(t) >= 2 else ' '}" for h, t in zip(HORIZONS, ts)) + f"   day {peak}, {m[peak - 1]:+.2f}")

say("\n   By period (L=20, cumulative at H=20 / H=60):")
for a, b in (("2019", "2020"), ("2021", "2022"), ("2023", "2024"), ("2025", "2026")):
    mask = (px.index >= f"{a}-01-01") & (px.index <= f"{b}-12-31")
    P = spread_paths(SECTORS, 20, H, 3, mask)
    say(f"   {a}-{b}: {np.nanmean(P[:, 19]) * 100:+.2f}% / {np.nanmean(P[:, 59]) * 100:+.2f}%  (n {len(P)})")

say("\n== B. Month-start undercurrent: formed on the first k sessions of the month, followed 40 sessions ==")
month = px.index.to_period("M")
first_of_month = np.r_[True, month[1:] != month[:-1]]
pos_in_month = pd.Series(1, index=px.index).groupby(month).cumsum().values
mstart = {}
for k in (3, 5):
    mask = pos_in_month == k                         # formation at the close of the k-th session
    P = spread_paths(SECTORS, k, 40, 3, mask)        # L = k: relative return over the month's first k sessions
    A = spread_paths(SECTORS, k, 40, 3)              # same L formed on every day, for comparison
    mstart[k] = (P, A)
    m, a = np.nanmean(P, 0) * 100, np.nanmean(A, 0) * 100
    say(f"  k={k}: months {len(P)}  cumulative top-bottom after d sessions (month-start | any day):")
    for d in (1, 3, 5, 10, 15, 20, 30, 40):
        t = nw_t(P[:, d - 1], 1)
        say(f"     d={d:2d}  {m[d - 1]:+6.2f}% (t {t:+.2f}, >0 in {np.mean(P[:, d - 1] > 0):.0%})  |  {a[d - 1]:+6.2f}%")
    say(f"     peak: day {int(np.nanargmax(m)) + 1} ({np.nanmax(m):+.2f}%)")

say("\n== C. Daily RRG quadrants (app formula) ==")
def ema(x, span): return x.ewm(span=span, adjust=False).mean()
rs = ema(100 * px[SECTORS].div(px[NIFTY], axis=0), 5)
z = lambda s, w: 100 + (s - s.rolling(w).mean()) / s.rolling(w).std(ddof=0)
ratio = z(rs, 14)
momo = ema(z(ratio.diff(), 14), 3)
quad = pd.DataFrame(np.where(ratio >= 100, np.where(momo >= 100, "L", "W"), np.where(momo >= 100, "I", "G")),
                    index=px.index, columns=SECTORS).where(ratio.notna() & momo.notna())
names = {"L": "Leading", "W": "Weakening", "G": "Lagging", "I": "Improving"}
runs = {q: [] for q in names}; trans = pd.DataFrame(0, index=list(names), columns=list(names))
for c in SECTORS:
    s = quad[c].dropna().values
    start = 0
    for i in range(1, len(s) + 1):
        if i == len(s) or s[i] != s[start]:
            runs[s[start]].append(i - start)
            if i < len(s): trans.loc[s[start], s[i]] += 1
            start = i
for q, label in names.items():
    r = np.array(runs[q])
    say(f"  {label:10s} stays median {np.median(r):.0f} sessions, mean {r.mean():.1f}, 75th pct {np.percentile(r, 75):.0f}, 90th {np.percentile(r, 90):.0f}  (runs {len(r)})")
say("  next quadrant (row -> column, share of exits):")
tp = trans.div(trans.sum(1), axis=0)
for q in names:
    say(f"    {names[q]:10s} " + "  ".join(f"{names[c][:4]} {tp.loc[q, c]:.0%}" for c in names if c != q))
say("  next-N-session relative return vs NIFTY by today's quadrant (pooled sector-days, %):")
fwd = {n: (rel[SECTORS].shift(-n) - rel[SECTORS]) * 100 for n in (5, 10, 20)}
for q in names:
    msk = quad == q
    vals = {n: fwd[n].where(msk).stack() for n in fwd}
    say(f"    {names[q]:10s} " + "  ".join(f"{n:2d}d {vals[n].mean():+.2f} (>0 {np.mean(vals[n] > 0):.0%})" for n in fwd) + f"   n {len(vals[5])}")
say("  ... and by quadrant when the tail points toward Leading (momentum rising) vs away:")
rising = momo.diff() > 0
for q in names:
    for flag, lab in ((True, "rising"), (False, "falling")):
        msk = (quad == q) & (rising == flag)
        v = fwd[10].where(msk).stack()
        lines_ = f"    {names[q]:10s} {lab:8s} 10d {v.mean():+.2f}% (>0 {np.mean(v > 0):.0%}, n {len(v)})"
        say(lines_)

say("\n== D. Leadership runs: sessions a sector stays in the top 3 by 20-day relative return ==")
r20 = rel[SECTORS] - rel[SECTORS].shift(20)
rank = r20.rank(axis=1, ascending=False)
top = (rank <= 3).where(r20.notna())
for label, flag in (("top 3", top),):
    lens = []
    for c in SECTORS:
        s = flag[c].dropna().astype(bool).values; n = 0
        for v in s:
            if v: n += 1
            elif n: lens.append(n); n = 0
        if n: lens.append(n)
    lens = np.array(lens)
    say(f"  {label}: runs {len(lens)}  median {np.median(lens):.0f}  mean {lens.mean():.1f}  75th {np.percentile(lens, 75):.0f}  90th {np.percentile(lens, 90):.0f} sessions;"
        f" {np.mean(lens >= 20):.0%} last 20+ sessions")

say("\n== Now (last session) ==")
last = px.index[-1]
for c in sorted(SECTORS, key=lambda c: -(rel[c].iloc[-1] - rel[c].iloc[-21])):
    say(f"  {name(c):10s} 5d {(rel[c].iloc[-1] - rel[c].iloc[-6]) * 100:+5.1f}%  20d {(rel[c].iloc[-1] - rel[c].iloc[-21]) * 100:+5.1f}%  60d {(rel[c].iloc[-1] - rel[c].iloc[-61]) * 100:+5.1f}%"
        f"  RRG {names.get(quad[c].iloc[-1], '?'):10s} (ratio {ratio[c].iloc[-1]:.1f}, momentum {momo[c].iloc[-1]:.1f})")

(HERE / "report.txt").write_text("\n".join(lines) + "\n")

fig, ax = plt.subplots(1, 3, figsize=(17, 5))
days = np.arange(1, H + 1)
for L in (1, 5, 20, 60, 120):
    ax[0].plot(days, np.nanmean(curves[L], 0) * 100, label=f"ranked on last {L}d")
ax[0].axhline(0, color="#888", lw=0.8); ax[0].set_title("Sectors: top 3 − bottom 3, cumulative relative return")
ax[0].set_xlabel("sessions after ranking"); ax[0].set_ylabel("%"); ax[0].legend(fontsize=8)
for k, (P, A) in mstart.items():
    ax[1].plot(np.arange(1, 41), np.nanmean(P, 0) * 100, label=f"formed on month's first {k} sessions")
    ax[1].plot(np.arange(1, 41), np.nanmean(A, 0) * 100, ls="--", label=f"formed any day (last {k}d)")
ax[1].axhline(0, color="#888", lw=0.8); ax[1].axvline(17, color="#bbb", lw=0.6, ls=":")
ax[1].set_title("Month-start winners: the undercurrent"); ax[1].set_xlabel("sessions after formation"); ax[1].legend(fontsize=8)
q_means = [[fwd[n].where(quad == q).stack().mean() for n in (5, 10, 20)] for q in names]
x = np.arange(4)
for j, n in enumerate((5, 10, 20)):
    ax[2].bar(x + (j - 1) * 0.27, [m[j] for m in q_means], 0.27, label=f"next {n}d")
ax[2].set_xticks(x, [names[q] for q in names]); ax[2].axhline(0, color="#888", lw=0.8)
ax[2].set_title("Next relative return by daily RRG quadrant"); ax[2].set_ylabel("% vs NIFTY"); ax[2].legend(fontsize=8)
fig.tight_layout(); fig.savefig(HERE / "persistence.png", dpi=110)
