"""End-of-day close of near-the-money monthly stock options from the TradeBot archive (read-only)."""
import glob
import pandas as pd
out = []
for f in sorted(glob.glob("/d/option_candles/*.parquet")):
    d = pd.read_parquet(f, columns=["time", "underlying", "expiry", "strike", "option_type", "close", "iv", "underlying_price", "volume"])
    d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert("Asia/Kolkata")
    d["day"] = d["time"].dt.date
    d = d[(d["strike"] / d["underlying_price"] - 1).abs() <= 0.12]
    d = d.sort_values("time").groupby(["underlying", "expiry", "strike", "option_type", "day"], as_index=False).last()
    out.append(d)
    print(f, len(d), flush=True)
pd.concat(out).to_csv("/o/eod_options.csv.gz", index=False)
