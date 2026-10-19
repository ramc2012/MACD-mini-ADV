# Corrected 17 Oct audit: entry is next-session EOD, exact exit date and positive volume required.
# EOD prints are diagnostics, not executable quotes. Original result files are preserved.
"""Option-aware selection and a REAL-premium check.

1. New rules, judged with the same option yardstick as study.py (2021+):
   Squeeze + trend    20-day vol compressed vs 60-day (bottom 30% of vol20/vol60): options priced off the quiet
                      spell are cheap; side from 60-day RS (CE strongest / PE weakest)
   ML on option edge  walk-forward classifier for the top decile of the volatility-normalised move
                      (move / (vol20 * sqrt(H/252))), i.e. what an option buyer is paid for, per side
   CE only in uptrend the best CE rules taken only when NIFTY > its 50-day average (PE only when below)
2. Real monthly stock options (TradeBot archive, ~Jun 2025 - Feb 2026): every weekly decision, buy the ATM option of
   the nearest monthly expiry that is still alive at the exit, selected using signal-day contracts; buy at the
   next session EOD print and require an exact-date exit H sessions later. Model 1.5% cost per side.
   These are EOD diagnostics, not executable bid/ask returns. Also calibrates the yardstick
   (real ATM premium vs the 0.4 * 1.15 * vol20 * sqrt(T) estimate).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).parent))
import study as S  # noqa: E402  (features, panel, rules, scoring)

HERE = Path(__file__).parent
lines = []
def say(*a):
    t = " ".join(str(x) for x in a); print(t, flush=True); lines.append(t)

panel, NEG = S.panel, S.NEG
X_COLS = S.FEATURES + ["nifty_trend"]


def ml_edge(h):
    """Walk-forward P(top decile of vol-normalised move) per side."""
    norm = panel[f"fwd{h}"] / (panel["vol20"].clip(lower=0.1) * np.sqrt(h / 252))
    hi = norm.groupby(panel["date"]).transform(lambda s: s >= s.quantile(0.9))
    lo = norm.groupby(panel["date"]).transform(lambda s: s <= s.quantile(0.1))
    out = pd.DataFrame(index=panel.index, columns=["ce", "pe"], dtype=float)
    X = panel[X_COLS].astype(float)
    for year in range(2021, 2027):
        start = pd.Timestamp(f"{year}-01-01")
        train = panel["date"] < start - pd.Timedelta(days=int(h * 1.6) + 3)
        test = (panel["date"] >= start) & (panel["date"] < pd.Timestamp(f"{year + 1}-01-01"))
        for col, label in (("ce", hi), ("pe", lo)):
            m = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200,
                                               l2_regularization=1.0, random_state=0).fit(X[train], label[train].astype(int))
            out.loc[test, col] = m.predict_proba(X[test])[:, 1]
    return out


def all_rules(g, extra):
    r = S.rule_scores(g)
    squeeze = g["volratio"] <= g["volratio"].quantile(0.3)
    r["Squeeze + trend"] = (g["rs60"].where(squeeze, NEG), (-g["rs60"]).where(squeeze, NEG))
    if extra is not None:
        r["ML on option edge"] = (extra.loc[g.index, "ce"], extra.loc[g.index, "pe"])
    up = g["nifty_trend"].iloc[0] > 0
    for base in ("Leading (already moved)", "RS 60d", "Squeeze + trend"):
        ce, pe = r[base]
        r[f"{base} | regime-gated"] = (ce if up else ce * 0 + NEG, pe if not up else pe * 0 + NEG)
    return r


def picks_of(score):
    s = score.dropna(); s = s[s > NEG / 2]
    return s.sort_values(ascending=False).index[:10] if len(s) >= 5 else []


def proxy_eval(h):
    sub = panel[panel["date"] >= "2021-01-01"]
    extra = ml_edge(h).loc[sub.index]
    rec = []
    for t, g in sub.groupby("date"):
        for name, (ce, pe) in all_rules(g, extra).items():
            for side, sc in (("CE", ce), ("PE", pe)):
                p = picks_of(sc)
                if len(p):
                    rec.append({"date": t, "rule": name, "side": side, **S.score(p, g, side, h)})
    df = pd.DataFrame(rec)
    say(f"\n== Option yardstick, H={h}, 2021+ (weekly decisions; gated rules trade only in their regime) ==")
    say(f"   {'rule':40s} side  hit@10 wrong10%   move   opt mean  opt med  opt win  decisions")
    keep = ["Random", "Leading (already moved)", "RS 60d", "Squeeze + trend", "ML on option edge", "ML ranker (walk-forward)",
            "Improving->Leading (weekly) + volatile", "Leading (already moved) | regime-gated", "RS 60d | regime-gated", "Squeeze + trend | regime-gated"]
    for name in keep:
        for side in ("CE", "PE"):
            g = df[(df["rule"] == name) & (df["side"] == side)]
            if g.empty:
                continue
            say(f"   {name:40s} {side}   {g['hit10'].mean():5.1%}  {g['wrong_dec'].mean():6.1%}  {g['move'].mean() * 100:+5.2f}%  "
                f"{g['opt_mean'].mean() * 100:+7.1f}%  {g['opt_med'].mean() * 100:+6.1f}%  {g['opt_win'].mean():6.0%}  {len(g):5d}")
    return extra


# ---------------- real options ----------------
eod = pd.read_csv(HERE / "data" / "eod_options.csv.gz", parse_dates=["day", "expiry"])
eod["symbol"] = "NSE:" + eod["underlying"] + "-EQ"
by_key = {k: g for k, g in eod.groupby(["symbol", "option_type"])}
days_index = S.px.index


def real_trade(symbol, side, entry_day, h):
    g = by_key.get((symbol, side))
    if g is None:
        return None
    i = days_index.get_loc(entry_day)
    if i + h >= len(days_index):
        return None
    signal_day = entry_day
    # Completed signal-day features cannot buy that day's closing print.
    if i + 1 + h >= len(days_index):
        return None
    entry_day = days_index[i + 1]
    exit_day = days_index[i + 1 + h]
    today = g[g["day"] == signal_day]
    live = today[today["expiry"] >= exit_day]
    if live.empty:
        return None
    expiry = live["expiry"].min()
    live = live[live["expiry"] == expiry]
    spot = live["underlying_price"].iloc[0]
    row = live.iloc[(live["strike"] - spot).abs().argsort().iloc[0]]
    entry = g[(g["expiry"] == expiry) & (g["strike"] == row["strike"]) & (g["day"] == entry_day)]
    if entry.empty:
        return None
    row = entry.iloc[-1]
    if not np.isfinite(row["close"]) or row["close"] <= 0:
        return None
    path = g[(g["expiry"] == expiry) & (g["strike"] == row["strike"]) & (g["day"] == exit_day)]
    if path.empty:
        return None
    # A missing exit is unavailable, never the last old print before the horizon.
    exit_row = path.iloc[-1]
    exit_px = exit_row["close"]
    if not all(np.isfinite(v) for v in (exit_px, row["volume"], exit_row["volume"])) or exit_px <= 0 or row["volume"] <= 0 or exit_row["volume"] <= 0:
        return None
    est = 0.4 * max(row_vol(symbol, entry_day) * 1.15, 0.15) * np.sqrt(h / 252) * spot
    return {"ret": exit_px * (1 - 0.015) / (row["close"] * (1 + 0.015)) - 1,
            "signal_day": str(signal_day), "entry_day": str(entry_day), "exit_day": str(exit_day),
            "fill_basis": "next-session EOD diagnostic; bid/ask unavailable", "premium_pct": row["close"] / spot, "est_pct": est / spot, "iv": row["iv"],
            "vol20": row_vol(symbol, entry_day)}


def row_vol(symbol, day):
    return float(S.F["vol20"].loc[day, symbol])


def real_eval(h, extra):
    lo, hi = eod["day"].min() + pd.Timedelta(days=7), eod["day"].max() - pd.Timedelta(days=int(h * 1.5))
    sub = panel[(panel["date"] >= lo) & (panel["date"] <= hi)]
    rec, calib, coverage = [], [], {}
    for t, g in sub.groupby("date"):
        rules = all_rules(g, extra.reindex(g.index) if extra is not None else None)
        for name, (ce, pe) in rules.items():
            for side, sc in (("CE", ce), ("PE", pe)):
                for sym in g.loc[list(picks_of(sc)), "symbol"]:
                    counts = coverage.setdefault((name, side), {"selected": 0, "available": 0})
                    counts["selected"] += 1
                    tr = real_trade(sym, side, t, h)
                    if tr:
                        counts["available"] += 1
                        rec.append({"date": t, "rule": name, "side": side, "symbol": sym, **tr})
                        if name == "Random":
                            calib.append(tr)
    df = pd.DataFrame(rec)
    pd.DataFrame([{"rule": key[0], "side": key[1], **value,
                   "unavailable": value["selected"] - value["available"]}
                  for key, value in coverage.items()]).to_csv(HERE / f"coverage_eod_h{h}.csv", index=False)
    say("CAUTION: next-session EOD diagnostic, not executable bid/ask returns; no profit/time-stop evaluation.")
    say(f"\n== REAL monthly stock options, H={h} sessions, {sub['date'].min().date()} -> {sub['date'].max().date()} "
        f"({sub['date'].nunique()} weekly decisions) ==")
    if calib:
        c = pd.DataFrame(calib)
        say(f"   calibration (random picks): real ATM premium {c['premium_pct'].median() * 100:.2f}% of spot vs estimate "
            f"{c['est_pct'].median() * 100:.2f}%  (ratio {np.median(c['premium_pct'] / c['est_pct']):.2f}); "
            f"real IV / 20d realised vol {np.median(c['iv'] / c['vol20']):.2f}")
    say(f"   {'rule':40s} side  trades  mean     median   win%   avg win  avg loss  sum of returns")
    order = df.groupby("rule")["ret"].mean().sort_values(ascending=False).index
    for name in order:
        for side in ("CE", "PE"):
            g = df[(df["rule"] == name) & (df["side"] == side)]
            if len(g) < 5:
                continue
            w, l = g[g["ret"] > 0]["ret"], g[g["ret"] <= 0]["ret"]
            say(f"   {name:40s} {side}   {len(g):5d}  {g['ret'].mean() * 100:+6.1f}%  {g['ret'].median() * 100:+6.1f}%  {np.mean(g['ret'] > 0):4.0%}  "
                f"{w.mean() * 100:+6.0f}%  {l.mean() * 100:+6.0f}%  {g['ret'].sum():+7.1f}")
    df.to_csv(HERE / f"real_eod_h{h}.csv", index=False)
    return df


if __name__ == "__main__":
    for h in (10, 20):
        extra = proxy_eval(h)
        real_eval(h, ml_edge(h))
    (HERE / "report2_eod_corrected.txt").write_text("\n".join(lines) + "\n")
