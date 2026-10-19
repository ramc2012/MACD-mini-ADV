"""RS vs volatility picks: excess over the equal-weight average in NIFTY-up and NIFTY-down months, and in 2025-26."""
import numpy as np
import pandas as pd
for f in ("sec_m0", "old_m0", "old_d3"):
    d = pd.read_csv(f"/r/rs_monthly_picks_20261017/{f}.csv")
    print("==", f)
    for s in ("RS 60d", "RS 120d", "Volatile 40%, then RS 60d", "High volatility 60d (control)", "Random (baseline)"):
        g = d[d.signal == s]; up = g[g.nifty_ret > 0]; dn = g[g.nifty_ret <= 0]; recent = g[g.month >= "2025"]
        ex = lambda x: (x.pick_ret - x.univ_ret).mean() * 100
        print(f"  {s:30s} up-months ex {ex(up):+.2f}%  down-months ex {ex(dn):+.2f}% (picks >0 {np.mean(dn.pick_ret > 0):.0%})  2025-26 ex {ex(recent):+.2f}%")
