"""Signal-time coverage probe only: deliberately does not read future returns."""
from pathlib import Path
import sqlite3
import json
import pandas as pd
from premium_ratio_research import epoch, IST

folder = Path(__file__).parent / "premium_ratios_august_2026"
meta = pd.read_csv(folder / "catalogue.csv")
db = sqlite3.connect(f"file:{(folder / 'snapshot-20260831.sqlite3').resolve()}?mode=ro", uri=True)
quotes = pd.read_sql_query("""SELECT symbol,timestamp,close,volume FROM historical_candles
 WHERE timeframe_seconds=60 AND timestamp>=? AND timestamp<? AND timestamp%86400=15240""",
 db, params=(epoch("2026-08-01"),epoch("2026-08-28")))
db.close()
options = quotes.merge(meta, on="symbol")
options["day"] = pd.to_datetime(options.timestamp, unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
options = options.loc[options.day <= options.expiry]
options = options.merge(quotes[["symbol","timestamp","close"]].rename(columns={"symbol":"spot_symbol","close":"spot"}), on=["spot_symbol","timestamp"])
options = options.loc[options.volume.gt(0) & options.close.gt(0)]
groups = options.groupby(["root","expiry","day","side"])
counts = groups.strike.nunique()
print("Simultaneous strike counts at 09:44:", counts.value_counts().sort_index().to_dict())
rows = []
for key, group in groups:
    group = group.sort_values("strike")
    spot = group.spot.iloc[0]
    atm_index = (group.strike-spot).abs().argmin()
    row = dict(zip(["root","expiry","day","side"], key), n=len(group), spot=spot,
        strikes=group.strike.tolist(), premiums=group.close.tolist(),
        brackets_spot=bool(group.strike.min()<spot<group.strike.max()))
    row["atm_has_two_wings"] = bool(0 < atm_index < len(group)-1)
    if row["atm_has_two_wings"]:
        a,b,c=group.strike.iloc[atm_index-1:atm_index+2]
        row["max_gap_pct"] = max(b-a,c-b)/spot*100
    rows.append(row)
summary = {"signal_time_counts": counts.value_counts().sort_index().to_dict(),
    "three_plus": [r for r in rows if r["n"]>=3],
    "bracketing_pairs": [r for r in rows if r["brackets_spot"]]}
(folder / "coverage_probe.json").write_text(json.dumps(summary,indent=2))
print("three-plus side days:", len(summary["three_plus"]))
print("bracketing side days:", len(summary["bracketing_pairs"]))
print("samples:", summary["three_plus"][:8], summary["bracketing_pairs"][:8])
