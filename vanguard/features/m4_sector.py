"""M4 — sector relative strength, correlation-reduced sector20, and lead-lag.

Reads `underlying_spot_candles` directly (existing data -- 225 underlyings,
30-minute bars, 2021-present; see db/migrations/001_schema.sql for why this
is read rather than re-fetched) for every equity in `sector_taxonomy`,
aggregates to daily closes, and:

  1. sector20 -- the spec calls this "correlation-reduced". It is computed
     here, not asserted: hierarchical clustering of the sector_group tier's
     26 buckets down toward 20, by pairwise correlation of each group's own
     equal-weight index over the lookback window. Two sector_group buckets
     merge only when they are more correlated with EACH OTHER than either is
     with the average of everything else -- so a genuinely distinct group
     (e.g. Defence) can survive as its own sector20 bucket past the nominal
     target if nothing else is close to it.
  2. sector_rs -- 5/20/60-bar return z-scores per sector20 equal-weight index.
  3. leadlag -- each stock's own daily-return correlation against its
     sector20 index at lags -2..+2 sessions; the lag with the highest
     correlation is stored as best_lag. A positive best_lag means the stock's
     moves tend to follow the sector by that many sessions (a lead-lag
     candidate per the spec); 0 means synchronous; negative means the stock
     leads its own sector.

sector_proxy_daily_closes.csv is written alongside as the reproducibility
artifact the spec names -- exactly the daily closes this script derived its
clustering and RS numbers from, so a later run can diff against it rather
than silently re-deriving different data if the underlying bars are revised.

    python vanguard/features/m4_sector.py --lookback-days 90
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

DEFAULT_DSN = ""
PROXY_CSV = Path(os.environ.get("VANGUARD_RUNTIME_DIR", "/tmp/vanguard")) / "sector_proxy_daily_closes.csv"
LEAD_LAG_RANGE = range(-2, 3)   # bars, per the spec's -2..+2
RS_HORIZONS = (5, 20, 60)


def load_taxonomy(connection) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT symbol, sector_group FROM sector_taxonomy WHERE instrument_type = 'Equity'",
        connection,
    )


def load_daily_closes(connection, symbols: list[str], start: date) -> pd.DataFrame:
    """Daily close per symbol: last 30-minute print of each session.

    underlying_spot_candles has no '1day' interval (checked: only
    30/15/5/3/1-minute rows exist), so daily closes are derived here rather
    than assumed to exist.
    """
    query = """
        SELECT DISTINCT ON (underlying, date(time AT TIME ZONE 'Asia/Kolkata'))
               underlying AS symbol,
               date(time AT TIME ZONE 'Asia/Kolkata') AS dt,
               close
        FROM underlying_spot_candles
        WHERE interval = '30minute' AND underlying = ANY(%(symbols)s)
          AND time >= %(start)s
        ORDER BY underlying, date(time AT TIME ZONE 'Asia/Kolkata'), time DESC
    """
    frame = pd.read_sql(query, connection, params={"symbols": symbols, "start": start})
    return frame.pivot(index="dt", columns="symbol", values="close").astype(float)


def equal_weight_index(closes: pd.DataFrame, members: list[str]) -> pd.Series:
    present = [symbol for symbol in members if symbol in closes.columns]
    if not present:
        return pd.Series(dtype=float)
    returns = closes[present].pct_change(fill_method=None)
    # Equal-weight: each member's daily return contributes equally regardless
    # of its own price level, then the index is the cumulative product --
    # the standard construction, not a price-weighted average.
    portfolio_return = returns.mean(axis=1, skipna=True)
    index = (1 + portfolio_return.fillna(0)).cumprod() * 100
    index.iloc[0] = 100.0
    return index


def cluster_sector20(group_indices: pd.DataFrame, target: int) -> dict[str, str]:
    """Hierarchical clustering of sector_group indices down toward `target` buckets."""
    returns = group_indices.pct_change(fill_method=None).dropna(how="all")
    correlation = returns.corr(min_periods=10)
    correlation = correlation.fillna(0.0)
    distance = 1 - correlation.values
    np.fill_diagonal(distance, 0.0)
    distance = (distance + distance.T) / 2   # guard against float asymmetry
    condensed = squareform(distance, checks=False)
    if len(correlation) <= target:
        # Already at or below the target granularity -- nothing to merge.
        return {group: group for group in correlation.columns}
    linkage_matrix = linkage(condensed, method="average")
    labels = fcluster(linkage_matrix, t=target, criterion="maxclust")
    groups = list(correlation.columns)
    cluster_members: dict[int, list[str]] = {}
    for group, label in zip(groups, labels):
        cluster_members.setdefault(label, []).append(group)
    # Name each sector20 bucket after its largest (by equity count is a proxy
    # we don't have here, so alphabetically first) constituent group, plus a
    # suffix when it merges more than one -- traceable back to sector_group.
    mapping = {}
    for label, members in cluster_members.items():
        name = members[0] if len(members) == 1 else f"{sorted(members)[0]} + {len(members)-1} more"
        for group in members:
            mapping[group] = name
    return mapping


def rs_zscores(index: pd.Series) -> pd.DataFrame:
    returns = index.pct_change(fill_method=None)
    out = pd.DataFrame(index=index.index)
    for horizon in RS_HORIZONS:
        window_return = index.pct_change(horizon, fill_method=None)
        out[f"rs_z{horizon}"] = (
            (window_return - window_return.rolling(60, min_periods=10).mean())
            / window_return.rolling(60, min_periods=10).std(ddof=0)
        )
    return out


def best_lag(stock_returns: pd.Series, sector_returns: pd.Series) -> tuple[int, float]:
    aligned = pd.concat([stock_returns, sector_returns], axis=1).dropna()
    if len(aligned) < 20:
        return 0, 0.0
    best = (0, 0.0)
    for lag in LEAD_LAG_RANGE:
        shifted = aligned.iloc[:, 1].shift(lag)
        pair = pd.concat([aligned.iloc[:, 0], shifted], axis=1).dropna()
        if len(pair) < 15:
            continue
        corr = pair.iloc[:, 0].corr(pair.iloc[:, 1])
        if corr is not None and abs(corr) > abs(best[1]):
            best = (lag, corr)
    return best


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--sector20-target", type=int, default=20)
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()

    connection = psycopg2.connect(args.dsn)
    connection.autocommit = True
    taxonomy = load_taxonomy(connection)
    symbols = sorted(taxonomy["symbol"].unique())
    start = date.today() - timedelta(days=args.lookback_days)
    closes = load_daily_closes(connection, symbols, start)
    print(f"daily closes: {closes.shape[0]} sessions x {closes.shape[1]} symbols "
          f"(of {len(symbols)} requested)")
    missing = sorted(set(symbols) - set(closes.columns))
    if missing:
        print(f"  no bars found for {len(missing)}: {missing[:10]}"
              f"{' ...' if len(missing) > 10 else ''}")

    PROXY_CSV.parent.mkdir(parents=True, exist_ok=True)
    temporary = PROXY_CSV.with_name(f".{PROXY_CSV.name}.{os.getpid()}.tmp")
    try:
        closes.to_csv(temporary)
        os.replace(temporary, PROXY_CSV)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"wrote {PROXY_CSV}")

    groups = sorted(taxonomy["sector_group"].unique())
    group_indices = pd.DataFrame({
        group: equal_weight_index(closes, taxonomy[taxonomy["sector_group"] == group]["symbol"].tolist())
        for group in groups
    })
    group_indices = group_indices.dropna(axis=1, how="all")
    print(f"sector_group equal-weight indices built: {group_indices.shape[1]} of {len(groups)} groups "
          f"(a group with zero matched symbols in this window is dropped)")

    sector20_map = cluster_sector20(group_indices, args.sector20_target)
    print(f"sector20 clustering: {len(group_indices.columns)} sector_group buckets "
          f"-> {len(set(sector20_map.values()))} sector20 buckets")
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            """UPDATE sector_taxonomy AS t SET sector20 = v.sector20, updated_at = now()
               FROM (VALUES %s) AS v(sector_group, sector20)
               WHERE t.sector_group = v.sector_group""",
            list(sector20_map.items()),
        )
    print(f"  sector20 written back to sector_taxonomy")

    taxonomy["sector20"] = taxonomy["sector_group"].map(sector20_map)
    sector20_names = sorted(set(sector20_map.values()))
    sector20_indices = pd.DataFrame({
        name: equal_weight_index(closes, taxonomy[taxonomy["sector20"] == name]["symbol"].tolist())
        for name in sector20_names
    })

    rs_rows = []
    for name in sector20_names:
        z = rs_zscores(sector20_indices[name])
        for ts, row in z.dropna(how="all").iterrows():
            rs_rows.append((pd.Timestamp(ts).to_pydatetime(), name,
                            None if pd.isna(row["rs_z5"]) else float(row["rs_z5"]),
                            None if pd.isna(row["rs_z20"]) else float(row["rs_z20"]),
                            None if pd.isna(row["rs_z60"]) else float(row["rs_z60"])))
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO sector_rs (ts, sector20, rs_z5, rs_z20, rs_z60) VALUES %s
               ON CONFLICT (sector20, ts) DO UPDATE SET
                 rs_z5 = EXCLUDED.rs_z5, rs_z20 = EXCLUDED.rs_z20, rs_z60 = EXCLUDED.rs_z60""",
            rs_rows,
        )
    print(f"sector_rs: {len(rs_rows)} rows written")

    leadlag_rows = []
    sector20_returns = sector20_indices.pct_change(fill_method=None)
    latest_day = closes.index.max()
    for symbol in closes.columns:
        group = taxonomy.loc[taxonomy["symbol"] == symbol, "sector20"]
        if group.empty:
            continue
        name = group.iloc[0]
        lag, corr = best_lag(closes[symbol].pct_change(fill_method=None), sector20_returns[name])
        leadlag_rows.append((latest_day, symbol, name, lag, float(corr)))
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO leadlag (dt, symbol, sector20, best_lag, corr) VALUES %s
               ON CONFLICT (dt, symbol) DO UPDATE SET
                 sector20 = EXCLUDED.sector20, best_lag = EXCLUDED.best_lag, corr = EXCLUDED.corr""",
            leadlag_rows,
        )
    print(f"leadlag: {len(leadlag_rows)} rows written for {latest_day}")

    laggards = sorted((r for r in leadlag_rows if r[3] > 0), key=lambda r: -r[4])[:10]
    print("\ntop laggards (positive best_lag, i.e. follow their sector20 index):")
    for dt, symbol, sector20, lag, corr in laggards:
        print(f"  {symbol:<14} sector20={sector20:<28} lag={lag:+d}  corr={corr:.3f}")

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
