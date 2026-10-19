"""Does following early performance give an edge?

Month: formation = return over the first N sessions of the month (from the prior month's last close); holding = from
the close of session N to the month's last close. Measured three ways:
  * cross-sectional rank IC (Spearman) between formation and holding returns, per month;
  * top minus bottom bucket of the formation ranking, held for the rest of the month (equal weight, before costs);
  * time series: does NIFTY 50's early-month sign predict the sign of the rest of its month?
Day (intraday): first hour (09:15-10:15) vs 10:15-close, on the stored 1-minute bars, same IC / spread.

Universes: sector & broad indices (13 context indices + Bank, Midcap Select, Sensex) and today's F&O stocks
(survivorship-biased: names that dropped out of F&O are missing, which flatters long-only momentum a little).
"""
import json, sqlite3, sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).parent
raw = json.load(open(HERE / "data_daily.json"))
closes = {}
for sym, rows in raw.items():
    if len(rows) < 200:
        continue
    s = pd.Series({pd.Timestamp(r[0], unit="s", tz="Asia/Kolkata").normalize().tz_localize(None): r[4] for r in rows})
    closes[sym] = s.sort_index()
px = pd.DataFrame(closes).sort_index()
px = px[px.index >= "2019-01-01"]
INDEX = [c for c in px.columns if c.endswith("-INDEX") and c != "NSE:NIFTY50-INDEX"]
STOCKS = [c for c in px.columns if c.endswith("-EQ")]
out_lines = []
def say(*a):
    line = " ".join(str(x) for x in a); print(line); out_lines.append(line)

say(f"daily closes {px.index[0].date()} -> {px.index[-1].date()}  sessions {len(px)}  indices {len(INDEX)}  stocks {len(STOCKS)}")
month = px.index.to_period("M")
months = sorted(set(month))


def month_table(cols, n):
    """Per month: formation return (first n sessions) and holding return (rest of month), one row per symbol."""
    recs = []
    for i, m in enumerate(months[1:], 1):
        days = px.index[month == m]
        prev = px.index[month == months[i - 1]]
        if len(days) < n + 3 or len(prev) == 0:
            continue
        if m == months[-1] and days[-1] < px.index[-1]:
            pass
        base, form_end, end = px.loc[prev[-1], cols], px.loc[days[n - 1], cols], px.loc[days[-1], cols]
        f, h = form_end / base - 1, end / form_end - 1
        ok = f.notna() & h.notna()
        recs.append(pd.DataFrame({"month": str(m), "symbol": f.index[ok], "form": f[ok].values, "hold": h[ok].values}))
    return pd.concat(recs, ignore_index=True)


def cross_section(cols, label, n, bucket):
    t = month_table(cols, n)
    t = t[t["month"] < str(months[-1])]               # the current month is not finished
    ics, spreads, top_ex = [], [], []
    for m, g in t.groupby("month"):
        if len(g) < max(6, 2 * bucket):
            continue
        ics.append(stats.spearmanr(g["form"], g["hold"])[0])
        g = g.sort_values("form")
        spreads.append(g["hold"].tail(bucket).mean() - g["hold"].head(bucket).mean())
        top_ex.append(g["hold"].tail(bucket).mean() - g["hold"].mean())
    ics, spreads, top_ex = map(np.array, (ics, spreads, top_ex))
    tt = lambda x: x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 else float("nan")
    say(f"  {label:8s} N={n}  months {len(ics):3d}  IC mean {ics.mean():+.3f} (t {tt(ics):+.2f}, >0 in {np.mean(ics > 0):.0%})"
        f"  top-bottom {spreads.mean() * 100:+.2f}%/mo (t {tt(spreads):+.2f}, >0 {np.mean(spreads > 0):.0%})"
        f"  top-vs-avg {top_ex.mean() * 100:+.2f}%/mo")
    return t


say("\n== Month: first N sessions -> rest of month (cross-sectional) ==")
tables = {}
for n in (1, 3, 5, 10):
    tables[("IDX", n)] = cross_section(INDEX, "indices", n, 3)
    tables[("STK", n)] = cross_section(STOCKS, "stocks", n, 20)

say("\n  by year (N=5): IC mean | top-bottom %/mo")
for key, label, b in ((("IDX", 5), "indices", 3), (("STK", 5), "stocks", 20)):
    t = tables[key]
    row = []
    for y, gy in t.groupby(t["month"].str[:4]):
        ic = [stats.spearmanr(g["form"], g["hold"])[0] for _, g in gy.groupby("month") if len(g) >= 6]
        sp = [(lambda g: g["hold"].tail(b).mean() - g["hold"].head(b).mean())(g.sort_values("form")) for _, g in gy.groupby("month") if len(g) >= 6]
        row.append(f"{y} {np.mean(ic):+.2f}|{np.mean(sp) * 100:+.1f}")
    say(f"  {label:8s} " + "  ".join(row))

say("\n== Month: NIFTY 50 time series (does the early-month sign repeat?) ==")
nifty = px["NSE:NIFTY50-INDEX"]
for n in (1, 3, 5, 10):
    recs = []
    for i, m in enumerate(months[1:-1], 1):
        days = px.index[month == m]; prev = px.index[month == months[i - 1]]
        if len(days) < n + 3:
            continue
        f = nifty[days[n - 1]] / nifty[prev[-1]] - 1; h = nifty[days[-1]] / nifty[days[n - 1]] - 1
        recs.append((f, h))
    a = np.array(recs)
    same = np.mean(np.sign(a[:, 0]) == np.sign(a[:, 1]))
    base = max(np.mean(a[:, 1] > 0), np.mean(a[:, 1] < 0))
    follow = np.sign(a[:, 0]) * a[:, 1]
    say(f"  N={n:2d}  months {len(a)}  same sign {same:.0%} (always-the-majority-side {base:.0%})  follow-the-start avg {follow.mean() * 100:+.2f}%/mo"
        f"  (t {follow.mean() / (follow.std(ddof=1) / np.sqrt(len(follow))):+.2f})  long-only avg {a[:, 1].mean() * 100:+.2f}%")

say("\n== This month (Oct 2026): first 3 / 5 sessions vs since then ==")
cur = str(months[-1])
for key, label in ((("IDX", 3), "indices N=3"), (("IDX", 5), "indices N=5"), (("STK", 5), "stocks N=5")):
    t = month_table(INDEX if key[0] == "IDX" else STOCKS, key[1])
    g = t[t["month"] == cur].sort_values("form")
    if len(g) < 6:
        continue
    ic = stats.spearmanr(g["form"], g["hold"])[0]
    b = 3 if key[0] == "IDX" else 20
    say(f"  {label}: IC {ic:+.2f}  top-bottom so far {(g['hold'].tail(b).mean() - g['hold'].head(b).mean()) * 100:+.2f}%")
    if key[0] == "IDX":
        for _, r in g.iloc[::-1].iterrows():
            say(f"    {r['symbol'].split(':')[1].replace('-INDEX', ''):12s} first {key[1]} sessions {r['form'] * 100:+5.1f}%   since {r['hold'] * 100:+5.1f}%")

# Intraday: first hour -> rest of day, on stored 1-minute bars (optional: needs the app database).
db_path = sys.argv[1] if len(sys.argv) > 1 else None
if db_path:
    say("\n== Day: first hour (09:15-10:15) -> 10:15-close, stored 1-minute bars ==")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    syms = INDEX + STOCKS + ["NSE:NIFTY50-INDEX"]
    rows = []
    for sym in syms:
        q = db.execute("""SELECT timestamp, open, close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
                          AND timestamp >= strftime('%s','2026-08-01')""", (sym,)).fetchall()
        if not q:
            continue
        d = pd.DataFrame(q, columns=["ts", "open", "close"])
        d["t"] = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        d["day"] = d["t"].dt.date; d["hm"] = d["t"].dt.hour * 60 + d["t"].dt.minute
        for day, g in d.groupby("day"):
            first = g[g["hm"] == 555]; at = g[g["hm"] <= 614]; last = g[g["hm"] <= 929]
            if first.empty or at.empty or len(last) < 300:
                continue
            o, c1, c2 = first["open"].iloc[0], at["close"].iloc[-1], last["close"].iloc[-1]
            rows.append((sym, day, c1 / o - 1, c2 / c1 - 1))
    t = pd.DataFrame(rows, columns=["symbol", "day", "form", "hold"])
    for label, cols, b in (("indices", INDEX, 3), ("stocks", STOCKS, 20)):
        ics, sp = [], []
        for day, g in t[t["symbol"].isin(cols)].groupby("day"):
            if len(g) < max(6, 2 * b):
                continue
            ics.append(stats.spearmanr(g["form"], g["hold"])[0]); g = g.sort_values("form")
            sp.append(g["hold"].tail(b).mean() - g["hold"].head(b).mean())
        ics, sp = np.array(ics), np.array(sp)
        if len(ics) > 2:
            say(f"  {label:8s} sessions {len(ics)}  IC {ics.mean():+.3f} (t {ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))):+.2f})"
                f"  top-bottom {sp.mean() * 100:+.3f}%/day (t {sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp))):+.2f}, >0 {np.mean(sp > 0):.0%})")
    n = t[t["symbol"] == "NSE:NIFTY50-INDEX"]
    if len(n) > 5:
        same = np.mean(np.sign(n["form"]) == np.sign(n["hold"]))
        f = np.sign(n["form"]) * n["hold"]
        say(f"  NIFTY 50 sessions {len(n)}  first-hour sign repeats {same:.0%}  follow avg {f.mean() * 100:+.3f}%/day (t {f.mean() / (f.std(ddof=1) / np.sqrt(len(f))):+.2f})")

(HERE / "report.txt").write_text("\n".join(out_lines) + "\n")
