"""Can relative strength pick the month's top ABSOLUTE performers?

For every month (Feb 2019 -> Sep 2026) and every signal, pick the top k names (3 of 13 sectors; 20 of the stocks) at a
decision point, then score the picks against what actually happened that month in absolute (rupee) returns:
  hit@k      share of picks that finished in the month's actual top k            (random = k / N)
  #1 caught  share of months whose single best performer was among the picks      (random = k / N)
  bottom@k   share of picks that finished in the month's actual bottom k         (random = k / N)
  return     picks' average absolute return vs the equal-weight universe and NIFTY 50; share of months > 0
Decision points:
  M0  last close before the month: picks held the whole month (prior month-end close -> month-end close)
  D3  close of the month's 3rd session: picks scored on the rest of the month (and on the whole month's ranking)
Signals use only data up to the decision point. Cross-sectionally, ranking on relative return = ranking on absolute
return (NIFTY is common to all), so "top absolute performer" and "top relative performer" are the same names; the
absolute level of the picks' return still depends on the market, which is shown separately (up vs down months, and a
simple NIFTY-trend filter).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).parent
raw = json.load(open(HERE.parent / "early_month_momentum_20261017" / "data_daily.json"))
px = pd.DataFrame({s: pd.Series({pd.Timestamp(r[0], unit="s", tz="Asia/Kolkata").normalize().tz_localize(None): r[4] for r in rows})
                   for s, rows in raw.items() if len(rows) >= 200}).sort_index()
px = px[px.index >= "2018-06-01"]
NIFTY = "NSE:NIFTY50-INDEX"
BROAD = {NIFTY, "BSE:SENSEX-INDEX", "NSE:NIFTYNXT50-INDEX", "NSE:MIDCPNIFTY-INDEX"}
SECTORS = [c for c in px.columns if c.endswith("-INDEX") and c not in BROAD]
STOCKS = [c for c in px.columns if c.endswith("-EQ")]
OLD_STOCKS = [c for c in STOCKS if px[c].first_valid_index() <= pd.Timestamp("2019-01-10")]
lines = []
def say(*a):
    t = " ".join(str(x) for x in a); print(t); lines.append(t)

rel = np.log(px).sub(np.log(px[NIFTY]), axis=0)
ema = lambda x, n: x.ewm(span=n, adjust=False).mean()
rs = ema(100 * px.div(px[NIFTY], axis=0), 5)
zs = lambda s, w: 100 + (s - s.rolling(w).mean()) / s.rolling(w).std(ddof=0)
rrg_x = zs(rs, 14); rrg_y = ema(zs(rrg_x.diff(), 14), 3)
nifty_trend = px[NIFTY] > px[NIFTY].rolling(50).mean()

month = px.index.to_period("M")
months = [m for m in sorted(set(month)) if m >= pd.Period("2019-02", "M")]
months = months[:-1]                                   # the current month is unfinished

def signals(t, cols, first_day=None, base_day=None):
    """Signal scores at row t (higher = pick). Only data up to t."""
    i = px.index.get_loc(t)
    r = lambda n: rel[cols].iloc[i] - rel[cols].iloc[i - n]
    out = {"RS 5d": r(5), "RS 20d": r(20), "RS 60d": r(60), "RS 120d": r(120),
           # persistent: positive on 5/20/60, ranked by 20d; names failing any get pushed to the bottom
           "All-green 5/20/60, by 20d": r(20).where((r(5) > 0) & (r(20) > 0) & (r(60) > 0), r(20) - 1e3),
           "RRG RS-Ratio": rrg_x[cols].iloc[i],
           "RRG Leading, by RS-Ratio": rrg_x[cols].iloc[i].where((rrg_x[cols].iloc[i] >= 100) & (rrg_y[cols].iloc[i] >= 100), rrg_x[cols].iloc[i] - 1e3),
           "Blend 20d+60d ranks": r(20).rank() + r(60).rank()}
    vol = np.log(px[cols]).diff().iloc[i - 59:i + 1].std()
    out["High volatility 60d (control)"] = vol
    # Volatile names produce the month's extremes both ways; RS picks the side: among the most volatile 40%,
    # the strongest by 60-day relative return (everyone else is pushed to the bottom).
    hi_vol = vol >= vol.quantile(0.6)
    out["Volatile 40%, then RS 60d"] = r(60).where(hi_vol, r(60) - 1e3)
    out["RS 60d / volatility"] = r(60) / vol
    out["RS 120d / volatility"] = r(120) / vol
    if base_day is not None:
        out["First 3 sessions of month"] = rel[cols].loc[t] - rel[cols].loc[base_day]
    out["Random (baseline)"] = pd.Series(np.random.default_rng(i).random(len(cols)), index=cols)
    return out


def evaluate(cols, k, label, point):
    rec = []
    for m in months:
        days = px.index[month == m]
        prev = px.index[month == (m - 1)]
        if len(days) < 6 or len(prev) == 0:
            continue
        base = prev[-1]
        if point == "M0":
            t, start = base, base
        else:
            t, start = days[2], days[2]
        end = days[-1]
        full = (px.loc[end, cols] / px.loc[base, cols] - 1).dropna()        # the month's actual absolute returns
        hold = (px.loc[end, cols] / px.loc[start, cols] - 1).dropna()       # what a pick made from the decision point
        sig = signals(t, cols, base_day=base if point == "D3" else None)
        n_actual = full.rank(ascending=False)
        nifty_hold = px.loc[end, NIFTY] / px.loc[start, NIFTY] - 1
        for name, s in sig.items():
            s = s.dropna(); s = s[s.index.isin(full.index)]
            if len(s) < 2 * k:
                continue
            picks = s.sort_values(ascending=False).index[:k]
            target = full if point == "M0" else hold
            tr = target.rank(ascending=False)
            rec.append({"month": str(m), "signal": name,
                        "hit": np.mean(tr[picks] <= k), "bottom": np.mean(tr[picks] > len(tr) - k),
                        "best": float(target.idxmax() in picks),
                        "whole_month_hit": np.mean(n_actual[picks] <= k),
                        "pick_ret": hold[picks].mean(), "univ_ret": hold.mean(), "nifty_ret": nifty_hold,
                        "trend_up": bool(nifty_trend.loc[t]), "n": len(target)})
    df = pd.DataFrame(rec)
    base_rate = k / df["n"].median()
    say(f"\n== {label}: pick top {k} of ~{int(df['n'].median())}, decision {'before the month' if point == 'M0' else 'after 3 sessions (scored on the rest of the month)'}  (random hit = {base_rate:.0%}) ==")
    say(f"  {'signal':30s} {'top-bot':>7s} {'hit@k':>6s} {'#1 in picks':>11s} {'bottom@k':>8s} {'pick ret':>9s} {'vs avg':>7s} {'vs NIFTY':>8s} {'>avg mo':>7s} {'>0 mo':>6s}  t(vs avg)")
    order = df.groupby("signal")["hit"].mean().sort_values(ascending=False).index
    for name in order:
        g = df[df["signal"] == name]
        ex = g["pick_ret"] - g["univ_ret"]
        t = ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))
        say(f"  {name:30s} {(g['hit'].mean() - g['bottom'].mean()) * 100:+6.1f}p {g['hit'].mean():6.0%} {g['best'].mean():11.0%} {g['bottom'].mean():8.0%} {g['pick_ret'].mean() * 100:+8.2f}% {ex.mean() * 100:+6.2f}% "
            f"{(g['pick_ret'] - g['nifty_ret']).mean() * 100:+7.2f}% {np.mean(ex > 0):7.0%} {np.mean(g['pick_ret'] > 0):6.0%}  {t:+.2f}")
    return df


def market_split(df, signal, label):
    g = df[df["signal"] == signal]
    up, down = g[g["nifty_ret"] > 0], g[g["nifty_ret"] <= 0]
    say(f"  {label} [{signal}]  NIFTY-up months ({len(up)}): picks {up['pick_ret'].mean() * 100:+.2f}% vs avg {up['univ_ret'].mean() * 100:+.2f}%, >0 {np.mean(up['pick_ret'] > 0):.0%}"
        f"  |  NIFTY-down months ({len(down)}): picks {down['pick_ret'].mean() * 100:+.2f}% vs avg {down['univ_ret'].mean() * 100:+.2f}%, >0 {np.mean(down['pick_ret'] > 0):.0%}")
    on = g[g["trend_up"]]
    say(f"     with NIFTY-trend filter (hold picks only when NIFTY > its 50-day average at the decision, else cash): "
        f"{len(on)} of {len(g)} months invested, avg/month over all months {(on['pick_ret'].sum() / len(g)) * 100:+.2f}% vs always-in {g['pick_ret'].mean() * 100:+.2f}%; "
        f"invested months >0 {np.mean(on['pick_ret'] > 0):.0%}")


def by_year(df, signal, label):
    g = df[df["signal"] == signal].copy(); g["year"] = g["month"].str[:4]
    rnd = df[df["signal"] == "Random (baseline)"].copy(); rnd["year"] = rnd["month"].str[:4]
    cells = []
    for y, gy in g.groupby("year"):
        cells.append(f"{y} hit {gy['hit'].mean():.0%} ex {((gy['pick_ret'] - gy['univ_ret']).mean() * 100):+.1f}%")
    say(f"  {label} [{signal}] by year: " + " | ".join(cells))


say(f"daily {px.index[0].date()} -> {px.index[-1].date()}; months scored {len(months)} (Feb 2019 - {months[-1]}); sectors {len(SECTORS)}, stocks {len(STOCKS)} ({len(OLD_STOCKS)} listed since 2019)")
results = {}
results["sec_m0"] = evaluate(SECTORS, 3, "SECTORS", "M0")
results["sec_d3"] = evaluate(SECTORS, 3, "SECTORS", "D3")
results["stk_m0"] = evaluate(STOCKS, 20, "STOCKS (all 210, survivorship-biased)", "M0")
results["old_m0"] = evaluate(OLD_STOCKS, 20, "STOCKS listed since 2019", "M0")
results["old_d3"] = evaluate(OLD_STOCKS, 20, "STOCKS listed since 2019", "D3")

say("\n== Real returns: does picking by RS make money, and when? ==")
for key, label in (("sec_m0", "Sectors M0"), ("old_m0", "Stocks M0")):
    df = results[key]
    for best in ("RS 60d", "Volatile 40%, then RS 60d"):
      market_split(df, best, label)
      by_year(df, best, label)
    best = "RS 120d"
    market_split(df, best, label)
    by_year(df, best, label)
    market_split(df, "Random (baseline)", label)

say("\n== Whole-month ranking when picking after 3 sessions (does D3 find the month's top performers, counting the 3 sessions?) ==")
for key, label in (("sec_d3", "Sectors"), ("old_d3", "Stocks since 2019")):
    g = results[key].groupby("signal")["whole_month_hit"].mean().sort_values(ascending=False)
    say(f"  {label}: " + " | ".join(f"{s} {v:.0%}" for s, v in g.items()))

# Significance of the best sector signal's hit rate vs random (binomial on picks)
for key in ("sec_m0", "old_m0"):
    df = results[key]
    for s in ("RS 20d", "RS 60d", "RS 60d / volatility", "RS 120d / volatility", "High volatility 60d (control)", "RRG Leading, by RS-Ratio"):
        g = df[df["signal"] == s]
        k = 3 if key.startswith("sec") else 20
        hits = int(round(g["hit"].sum() * k)); n = len(g) * k
        p = stats.binomtest(hits, n, k / g["n"].median(), alternative="greater").pvalue
        say(f"  {key} {s:28s} hits {hits}/{n} = {hits / n:.1%} vs random {k / g['n'].median():.1%}  one-sided p {p:.3f}")

(HERE / "report.txt").write_text("\n".join(lines) + "\n")
for key, df in results.items():
    df.to_csv(HERE / f"{key}.csv", index=False)
