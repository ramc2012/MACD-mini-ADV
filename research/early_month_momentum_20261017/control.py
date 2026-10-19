"""Control: is the stock top-bottom spread special to the early month, or plain momentum / survivorship?"""
import numpy as np, pandas as pd
from scipy import stats
import json
from pathlib import Path
raw = json.load(open("/r/data_daily.json"))
px = pd.DataFrame({s: pd.Series({pd.Timestamp(r[0], unit="s", tz="Asia/Kolkata").normalize().tz_localize(None): r[4] for r in rows})
                   for s, rows in raw.items() if s.endswith("-EQ") and len(rows) >= 200}).sort_index()
px = px[px.index >= "2019-01-01"]
month = px.index.to_period("M"); months = sorted(set(month))[:-1]
full = [c for c in px.columns if px[c].first_valid_index() <= pd.Timestamp("2019-01-10")]
lines = []
def run(label, cols, formation):
    sp = []
    for i, m in enumerate(months[2:], 2):
        days = px.index[month == m]; prev = px.index[month == months[i - 1]]; prev2 = px.index[month == months[i - 2]]
        if len(days) < 8: continue
        n = 5
        f = {"early5": px.loc[days[n - 1], cols] / px.loc[prev[-1], cols] - 1,
             "prior_month": px.loc[prev[-1], cols] / px.loc[prev2[-1], cols] - 1,
             "random": pd.Series(np.random.default_rng(i).normal(size=len(cols)), index=cols)}[formation]
        h = px.loc[days[-1], cols] / px.loc[days[n - 1], cols] - 1
        d = pd.DataFrame({"f": f, "h": h}).dropna().sort_values("f")
        sp.append(d["h"].tail(20).mean() - d["h"].head(20).mean())
    sp = np.array(sp)
    lines.append(f"{label:38s} months {len(sp)}  top-bottom {sp.mean()*100:+.2f}%/mo  t {sp.mean()/(sp.std(ddof=1)/np.sqrt(len(sp))):+.2f}")
for cols, name in ((list(px.columns), "all 210"), (full, f"listed since 2019 ({len(full)})")):
    for formation in ("early5", "prior_month", "random"):
        run(f"{name} / rank by {formation}", cols, formation)
print("\n".join(lines)); open("/r/control.txt", "w").write("\n".join(lines) + "\n")
