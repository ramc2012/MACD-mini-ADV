"""Pick the next period's top-10 (buy CE) and bottom-10 (buy PE) F&O stocks: which selection rules work, judged as an
options trader would?

Every 5 sessions (weekly), each rule names 10 CE stocks and 10 PE stocks from ~200 F&O names. Scored over the next
H = 5 / 10 / 20 sessions on:
  * hit@10   share of CE picks that were among the actual 10 best performers (PE: 10 worst)   random = 10/N (~5%)
  * decile   share in the right 10% / in the WRONG 10% (a CE pick that ended in the bottom decile)
  * move     average signed move (CE: return, PE: -return)
  * option   an ATM option bought at the pick and held H sessions, estimated from the stock's own volatility:
             premium = 0.4 * IV * sqrt(H/252) * S with IV = 1.15 x 20-day realised vol (options usually price above
             realised), payoff = max(+/-R, 0) * S, minus 3% of premium for spread/fees. Reports mean, median, win %.
             This ignores early exits and the volatility smile; it is a risk-reward yardstick, not a fill.
Rules (CE side / PE side):
  Random
  Leading (already moved)    Leading quadrant by RS-Ratio            / Lagging by lowest RS-Ratio
  RS 60d                     strongest 60-day relative return        / weakest
  Improving->Leading (d)     daily RRG Improving, heading up-right   / Weakening heading down-left
  Improving->Leading (w)     same on weekly RRG                      / same
  ... + volatile             the two above restricted to the most volatile 40% (60-day realised vol)
  Volatile + RS 60d          most volatile 40%, strongest RS 60d     / weakest
  Dip in leader              volatile, 120d RS top 30%, last 5d down vs NIFTY / volatile, 120d RS bottom 30%, last 5d up
  ML ranker                  gradient-boosted P(top decile) / P(bottom decile) on all the features, walk-forward by
                             year (trained only on earlier years), the "systematic" benchmark
Main table: 2021-01 -> 2026-10 (the ML's out-of-sample years); rules also shown over 2019-2026.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, "/src")
from macd_trader.sectors import SECTOR_MAP  # noqa: E402

HERE = Path(__file__).parent
raw = json.load(open(HERE.parent / "early_month_momentum_20261017" / "data_daily.json"))
px = pd.DataFrame({s: pd.Series({pd.Timestamp(r[0], unit="s", tz="Asia/Kolkata").normalize().tz_localize(None): r[4] for r in rows})
                   for s, rows in raw.items() if len(rows) >= 200}).sort_index()
px = px[px.index >= "2018-06-01"]
NIFTY = "NSE:NIFTY50-INDEX"
STOCKS = [c for c in px.columns if c.endswith("-EQ")]
P = px[STOCKS]
lines = []
def say(*a):
    t = " ".join(str(x) for x in a); print(t, flush=True); lines.append(t)

# ---------------- features (each uses data up to its own row only) ----------------
ema = lambda x, n: x.ewm(span=n, adjust=False).mean()
z = lambda s, w: 100 + (s - s.rolling(w).mean()) / s.rolling(w).std(ddof=0)
logp = np.log(P); rel = logp.sub(np.log(px[NIFTY]), axis=0)
ret = logp.diff()
F = {}
for n in (5, 20, 60, 120):
    F[f"rs{n}"] = rel - rel.shift(n)
F["ret5"] = logp - logp.shift(5)
rsl = ema(100 * P.div(px[NIFTY], axis=0), 5)
F["x"] = z(rsl, 14); F["y"] = ema(z(F["x"].diff(), 14), 3)
F["dx3"] = F["x"] - F["x"].shift(3); F["dy3"] = F["y"] - F["y"].shift(3)
wk = px.resample("W-FRI").last()
rslw = ema(100 * wk[STOCKS].div(wk[NIFTY], axis=0), 3)
xw = z(rslw, 10); yw = ema(z(xw.diff(), 10), 2)
dxw, dyw = xw - xw.shift(2), yw - yw.shift(2)
# a week's values are known from its Friday close on
for name, frame in (("xw", xw), ("yw", yw), ("dxw", dxw), ("dyw", dyw)):
    F[name] = frame.reindex(px.index, method="ffill")
F["vol20"] = ret.rolling(20).std() * np.sqrt(252)
F["vol60"] = ret.rolling(60).std() * np.sqrt(252)
F["volratio"] = F["vol20"] / F["vol60"]
F["ext50"] = logp - np.log(P.rolling(50).mean())
lo, hi = P.rolling(60).min(), P.rolling(60).max()
F["pos60"] = (P - lo) / (hi - lo)
group = pd.Series({s: SECTOR_MAP.get(s.split(":")[1].removesuffix("-EQ"), "Other") for s in STOCKS})
F["sector_rs20"] = F["rs20"].T.groupby(group).transform("mean").T
nifty = px[NIFTY]
nifty_trend = (nifty / nifty.rolling(50).mean() - 1)
FEATURES = list(F)

H_LIST = (5, 10, 20)
dates = px.index[200::5]
dates = [d for d in dates if px.index.get_loc(d) + 20 < len(px)]
rows = []
for t in dates:
    i = px.index.get_loc(t)
    snap = pd.DataFrame({k: v.iloc[i] for k, v in F.items()})
    snap["price"] = P.iloc[i]
    for h in H_LIST:
        snap[f"fwd{h}"] = P.iloc[i + h] / P.iloc[i] - 1
    snap = snap.dropna(subset=["rs120", "vol60", "x", "y", "xw", "yw", "price"])
    snap["date"] = t; snap["nifty_trend"] = nifty_trend.iloc[i]
    rows.append(snap)
panel = pd.concat(rows).rename_axis("symbol").reset_index()
say(f"panel: {panel['date'].nunique()} weekly decision dates {panel['date'].min().date()} -> {panel['date'].max().date()}, "
    f"{panel['symbol'].nunique()} stocks, ~{panel.groupby('date').size().median():.0f} per date")


# ---------------- rules: return (ce_score, pe_score) per row; higher = pick ----------------
def quadrant(x, y):
    return np.where(x >= 100, np.where(y >= 100, "L", "W"), np.where(y >= 100, "I", "G"))

NEG = -1e9
def rule_scores(g):
    q = quadrant(g["x"], g["y"]); qw = quadrant(g["xw"], g["yw"])
    vol_ok = g["vol60"] >= g["vol60"].quantile(0.6)
    toward = (g["dx3"] + g["dy3"]); toward_w = (g["dxw"] + g["dyw"])
    rng = np.random.default_rng(int(g["date"].iloc[0].value % 2**32))
    r = {}
    r["Random"] = (pd.Series(rng.random(len(g)), g.index), pd.Series(rng.random(len(g)), g.index))
    r["Leading (already moved)"] = (g["x"].where(q == "L", NEG), (-g["x"]).where(q == "G", NEG))
    r["RS 60d"] = (g["rs60"], -g["rs60"])
    r["Improving->Leading (daily)"] = (toward.where((q == "I") & (g["dx3"] > 0), NEG), (-toward).where((q == "W") & (g["dx3"] < 0), NEG))
    r["Improving->Leading (weekly)"] = (toward_w.where((qw == "I") & (g["dxw"] > 0), NEG), (-toward_w).where((qw == "W") & (g["dxw"] < 0), NEG))
    r["Improving->Leading (daily) + volatile"] = tuple(s.where(vol_ok, NEG) for s in r["Improving->Leading (daily)"])
    r["Improving->Leading (weekly) + volatile"] = tuple(s.where(vol_ok, NEG) for s in r["Improving->Leading (weekly)"])
    r["Volatile + RS 60d"] = (g["rs60"].where(vol_ok, NEG), (-g["rs60"]).where(vol_ok, NEG))
    strong, weak = g["rs120"] >= g["rs120"].quantile(0.7), g["rs120"] <= g["rs120"].quantile(0.3)
    r["Dip in leader / rip in laggard"] = ((-g["rs5"]).where(vol_ok & strong & (g["rs5"] < 0), NEG),
                                           g["rs5"].where(vol_ok & weak & (g["rs5"] > 0), NEG))
    return r


def option_ret(move, vol20, h):
    iv = np.maximum(vol20 * 1.15, 0.15)
    prem = 0.4 * iv * np.sqrt(h / 252)
    return np.maximum(move, 0) / prem - 1 - 0.03


def score(picks, g, side, h):
    fwd = g[f"fwd{h}"]
    signed = fwd if side == "CE" else -fwd
    rank_good = signed.rank(ascending=False)
    n = len(g)
    sel = g.loc[picks]
    opt = option_ret(signed.loc[picks], sel["vol20"], h)
    return {"hit10": np.mean(rank_good.loc[picks] <= 10), "right_dec": np.mean(rank_good.loc[picks] <= n / 10),
            "wrong_dec": np.mean(rank_good.loc[picks] > n - n / 10), "move": signed.loc[picks].mean(),
            "opt_mean": opt.mean(), "opt_med": opt.median(), "opt_win": np.mean(opt > 0), "n_picks": len(picks)}


# ---------------- ML ranker, walk-forward by year ----------------
def ml_scores(panel, h):
    out = pd.DataFrame(index=panel.index, columns=["ce", "pe"], dtype=float)
    fwd = panel[f"fwd{h}"]
    dec_hi = panel.groupby("date")[f"fwd{h}"].transform(lambda s: s >= s.quantile(0.9))
    dec_lo = panel.groupby("date")[f"fwd{h}"].transform(lambda s: s <= s.quantile(0.1))
    X = panel[FEATURES + ["nifty_trend"]].astype(float)
    for year in range(2021, 2027):
        start = pd.Timestamp(f"{year}-01-01")
        train = panel["date"] < start - pd.Timedelta(days=int(h * 1.6) + 3)        # embargo: labels must be known
        test = (panel["date"] >= start) & (panel["date"] < pd.Timestamp(f"{year + 1}-01-01"))
        if test.sum() == 0:
            continue
        for col, label in (("ce", dec_hi), ("pe", dec_lo)):
            m = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200,
                                               l2_regularization=1.0, random_state=0)
            m.fit(X[train], label[train].astype(int))
            out.loc[test, col] = m.predict_proba(X[test])[:, 1]
    return out


def evaluate(panel, h, since, label, with_ml=True):
    sub = panel[panel["date"] >= since]
    ml = ml_scores(panel, h).loc[sub.index] if with_ml else None
    rec = []
    for t, g in sub.groupby("date"):
        rules = rule_scores(g)
        if with_ml:
            rules["ML ranker (walk-forward)"] = (ml.loc[g.index, "ce"], ml.loc[g.index, "pe"])
        for name, (ce, pe) in rules.items():
            for side, s in (("CE", ce), ("PE", pe)):
                s = s.dropna(); s = s[s > NEG / 2]
                if len(s) < 5:
                    continue
                picks = s.sort_values(ascending=False).index[:10]
                rec.append({"date": t, "rule": name, "side": side, "trend_up": g["nifty_trend"].iloc[0] > 0, **score(picks, g, side, h)})
    df = pd.DataFrame(rec)
    say(f"\n== {label}: H = {h} sessions, decisions from {since} (every 5 sessions) ==")
    say(f"   {'rule':40s} side  hit@10 right10% wrong10%   move   opt mean  opt med  opt win  picks  | book (CE+PE) opt mean")
    book = df.groupby(["rule", "date"])["opt_mean"].mean().groupby("rule").mean()
    order = book.sort_values(ascending=False).index
    for name in order:
        for side in ("CE", "PE"):
            g = df[(df["rule"] == name) & (df["side"] == side)]
            if g.empty:
                continue
            say(f"   {name:40s} {side}   {g['hit10'].mean():5.1%}  {g['right_dec'].mean():6.1%}  {g['wrong_dec'].mean():6.1%}  {g['move'].mean() * 100:+5.2f}%  "
                f"{g['opt_mean'].mean() * 100:+7.1f}%  {g['opt_med'].mean() * 100:+6.1f}%  {g['opt_win'].mean():6.0%}  {g['n_picks'].mean():5.1f}"
                + (f"  | {book[name] * 100:+.1f}%" if side == "CE" else ""))
    return df


def main():
    results = {}
    for h in H_LIST:
        results[h] = evaluate(panel, h, "2021-01-01", "OUT-OF-SAMPLE WINDOW (2021+)")

    say("\n== Regime: CE and PE option mean by NIFTY trend at the decision (H=10, 2021+) ==")
    df = results[10]
    for name in df["rule"].unique():
        g = df[df["rule"] == name]
        cells = []
        for side in ("CE", "PE"):
            for up in (True, False):
                v = g[(g["side"] == side) & (g["trend_up"] == up)]["opt_mean"].mean() * 100
                cells.append(f"{side} {'NIFTY>50dma' if up else 'NIFTY<50dma'} {v:+6.1f}%")
        say(f"   {name:40s} " + "  ".join(cells))

    say("\n== By year: book (CE+PE) option mean per decision, H=10 ==")
    df["year"] = df["date"].dt.year
    tab = df.groupby(["rule", "year"])["opt_mean"].mean().unstack() * 100
    for name, row in tab.iterrows():
        say(f"   {name:40s} " + "  ".join(f"{y} {v:+6.1f}%" for y, v in row.items()))

    say("\n== Rules over the full sample (2019-07+), H=10 ==")
    evaluate(panel, 10, "2019-07-01", "FULL SAMPLE", with_ml=False)

    # Current picks (last date with features) for the best rules, for a desk list.
    last = px.index[-1]; i = len(px) - 1
    snap = pd.DataFrame({k: v.iloc[i] for k, v in F.items()}).dropna(subset=["rs120", "vol60", "x", "y", "xw", "yw"])
    snap["date"] = last; snap["nifty_trend"] = nifty_trend.iloc[i]
    cur = rule_scores(snap)
    say(f"\n== Picks as of {last.date()} ==")
    for name in ("Improving->Leading (weekly) + volatile", "Improving->Leading (daily) + volatile", "Volatile + RS 60d", "Dip in leader / rip in laggard"):
        ce, pe = cur[name]
        ce = ce[ce > NEG / 2].sort_values(ascending=False).index[:10]; pe = pe[pe > NEG / 2].sort_values(ascending=False).index[:10]
        nm = lambda s: s.split(":")[1].removesuffix("-EQ")
        say(f"   {name}\n      CE: {', '.join(nm(s) for s in ce)}\n      PE: {', '.join(nm(s) for s in pe)}")

    (HERE / "report.txt").write_text("\n".join(lines) + "\n")
    panel.to_parquet(HERE / "panel.parquet") if False else None
    for h, df in results.items():
        df.to_csv(HERE / f"scores_h{h}.csv", index=False)


if __name__ == "__main__":
    main()
