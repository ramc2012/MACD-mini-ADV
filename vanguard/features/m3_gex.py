"""M3 -- dealer gamma exposure (GEX) regime engine, per index AND per stock.

REIMPLEMENTED INDEPENDENTLY. Does not import, call, or read from
backend/analysis/dex_gex_oos_filter_analysis.py or anything under
backend/auction_intelligence/ -- those are live-trading-engine internals and
this module is a deliberate fresh build, per the project's own decision.
The standard GEX formula/sign convention below is common industry
terminology, not lifted from that code.

Reads ONLY from `option_premium_candles` (gamma, oi per contract -- already
collected, no new options ingestion needed) plus two read-only joins:
`fo_underlying_catalog.lot_size` for the contract multiplier, and
`underlying_spot_candles.close` for spot price, because
`option_premium_candles.underlying_price` turned out to be stale (see
"GENUINE DATA GAP" below) -- verified live, not assumed.

--------------------------------------------------------------------------
SIGN CONVENTION (state explicitly, doctrine requires it)
--------------------------------------------------------------------------
Per-contract dollar gamma exposure:

    gex_contract = gamma * OI * lot_size * spot**2 * 0.01

"SqueezeMetrics-style": call-side contracts contribute *positively*, put-side
contracts contribute *negatively* to net dealer gamma --

    net_gex(t) = sum(gex_contract for calls) - sum(gex_contract for puts)

Interpretation under this convention: net_gex > 0 ("dealers net long gamma")
implies dealer hedging flow is mean-reverting (buy dips / sell rips),
dampening realized volatility; net_gex < 0 ("dealers net short gamma")
implies hedging flow is trend-amplifying, raising realized volatility. The
inverse convention (puts positive, calls negative) exists in some published
GEX implementations and is NOT used here. This choice is applied
consistently everywhere in this module -- to net_gex, to the regime
percentile ranking, and to the cumulative sum used for gamma_flip_level.

--------------------------------------------------------------------------
GENUINE DATA GAP -- verified live 2026-08-26, not assumed either way
--------------------------------------------------------------------------
`option_premium_candles.gamma` is NOT a complete per-contract field across
the whole option chain, and its coverage has degraded sharply over time:

  - Through ~2026-06-22, gamma was populated across the FULL chain for most
    underlyings: e.g. NIFTY on 2026-05-04 had gamma on 251 rows at a single
    snapshot -- 49 distinct strikes across 6 expiries. On that day, 90
    distinct underlyings (stocks + indices) had usable full-chain gamma.
  - From ~2026-06-23 onward, coverage collapsed to a handful of near-the-
    money strikes per day, with many days at ZERO for a given underlying
    (e.g. NIFTY: 2026-08-10, 08-11, 08-17, 08-19, 08-24, 08-26 all have
    gamma on 0 rows). By late August, only the 4 large index underlyings
    (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY) have ANY gamma at all on a given
    day (checked 08-21 and 08-25: exactly 4 distinct underlyings each);
    every individual STOCK's gamma stream effectively stopped by
    2026-07-28 (last date any stock symbol shows a non-null gamma value).
  - `option_premium_candles.underlying_price` stopped being populated
    entirely after 2026-07-30 (16.5M/26.3M rows have it non-null all-time,
    zero of the last ~4 weeks). Spot is read from `underlying_spot_candles`
    instead (nearest close at or before the GEX snapshot time -- no
    look-ahead) rather than assumed absent or fabricated.

This module does NOT pretend the chain is complete. Per-day, per-underlying
GEX is computed from whatever gamma-populated rows exist at that
underlying's LATEST print of the day (see `load_snapshot_rows()`). On the
dense pre-collapse era this is a genuine near-full-chain read (NIFTY alone
carried up to ~90K gamma-populated rows in a single day pre-2026-06-22 --
every strike, every expiry, every 1-minute bar). On/after the collapse it is
a truncated, near-ATM-only read (as few as 2 strikes) -- the honest number
computable from what NSE/the upstream collector actually delivered, not an
extrapolation. Recent-period net_gex and gamma_flip_level should be read as
directionally indicative at best, not a full-book figure; this module's own
report (run `__main__`) prints exactly how many distinct strikes went into
each period so a reader is never misled about coverage silently.

PRODUCTION-SAFETY NOTE ON QUERY SHAPE: an earlier version of this module
issued one unbounded query (`WHERE gamma IS NOT NULL`, no time filter) that
forced a full scan of all ~26M rows / ~2 years of `option_premium_candles`
plus a per-row LATERAL spot join -- confirmed live to still be running
after 9 minutes against the SAME Postgres instance the live trading engine
uses, which is exactly the kind of shared-resource risk the project's "never
disrupt the live system" rule exists to prevent. It was killed
(`pg_cancel_backend`, confirmed no query left running) rather than left to
finish. The fix, used below: (1) a `--lookback-days` window (default 150
calendar days, comfortably >60 trading sessions -- enough for
TRAILING_WINDOW's percentile calc) that lets the hypertable prune whole
chunks outside the window before any aggregation runs; (2) a single-level
`GROUP BY underlying, day -> MAX(time)` (a few seconds over the windowed
data) instead of a two-level "busiest snapshot" aggregation; (3) the small
resulting (underlying, day, ts) target list is written to a session-local
TEMP TABLE (never touches a live-app table) and joined back against
`option_premium_candles`/`underlying_spot_candles` using their existing
indexes, rather than self-joining a multi-million-row CTE. Verified live:
full run of the 150-day-windowed pipeline completes in well under a minute.

Given this, PER-STOCK current regime (as of the most recent trading day) is
NULL for essentially every stock -- there is no live per-stock gamma feed
right now to classify. The historical per-stock and per-index series
(through the dense era) is real and is what `regime`'s trailing-percentile
history is built from. This is reported plainly in __main__, not hidden.

--------------------------------------------------------------------------
Regime bucketing
--------------------------------------------------------------------------
STRONG_NEG/NEG/NEUTRAL/POS/STRONG_POS from net_gex(t)'s percentile rank
against that SAME symbol's own trailing 60-session net_gex history
(itself included), cut at the 20/40/60/80th percentiles. A session needs at
least 5 trailing observations (itself included) to get a regime bucket;
below that the bucket is NULL (insufficient own-history to rank against --
doctrine #5: degrade honestly rather than bucket against 1-2 points).

--------------------------------------------------------------------------
gamma_flip_level
--------------------------------------------------------------------------
Per snapshot: signed gex_contract summed by strike (across both option
types and every expiry present at that snapshot -- this module reads the
whole term structure, not just the front month; a later phase could split
by expiry if that turns out to matter more), strikes sorted ascending,
cumulative-summed from the lowest strike upward. The strike interval where
the cumulative sum crosses zero is linearly interpolated. If the observed
strike range never crosses zero (all one sign, or fewer than 2 strikes),
gamma_flip_level is stored NULL -- never extrapolated beyond observed data.

    python vanguard/features/m3_gex.py                        # last 150 days, all underlyings
    python vanguard/features/m3_gex.py --symbol NIFTY          # one symbol only
    python vanguard/features/m3_gex.py --lookback-days 400     # wider window (heavier query)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

DEFAULT_DSN = ""
TRAILING_WINDOW = 60
MIN_HISTORY_FOR_REGIME = 5
PERCENTILE_CUTS = (0.20, 0.40, 0.60, 0.80)
REGIME_LABELS = ("STRONG_NEG", "NEG", "NEUTRAL", "POS", "STRONG_POS")


def load_snapshot_rows(connection, symbol: str | None, lookback_days: int) -> pd.DataFrame:
    """One row per (underlying, calendar day, contract) at that day's LATEST
    gamma-populated print, joined to lot_size and the nearest-prior spot
    close, restricted to the trailing `lookback_days` window.

    Three-stage query, each stage scoped to keep this DB-load-friendly on a
    shared production instance (see the module docstring's
    PRODUCTION-SAFETY NOTE for why this replaced an earlier unbounded
    single-query version):
      1. Aggregate MAX(time) per (underlying, day) over the windowed data
         only -- lets the hypertable prune chunks outside the window before
         any per-row work happens.
      2. Write that small target list to a session-local TEMP TABLE (never
         touches a live-app table) and index it.
      3. Join the temp table back to `option_premium_candles` (contract
         rows) and, via a LATERAL join, to `underlying_spot_candles` (spot
         price, since `underlying_price` is stale -- see module docstring).
    """
    symbol_filter = "AND underlying = %(symbol)s" if symbol else ""
    params = {"lookback_days": lookback_days}
    if symbol:
        params["symbol"] = symbol

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT underlying, date(time AT TIME ZONE 'Asia/Kolkata') AS day, MAX(time) AS ts
            FROM option_premium_candles o
            WHERE (gamma IS NOT NULL
                   -- SECOND GAMMA SOURCE. `option_premium_candles.gamma` died
                   -- for the EQUITY universe in August (210 names carried it in
                   -- July, 1 in August) while the indices kept theirs -- which
                   -- is why `regime` held only BANKNIFTY/FINNIFTY/MIDCPNIFTY/
                   -- NIFTY and M6's regime leg, which joins per SYMBOL, was
                   -- unpassable for every stock. `option_iv` carries gamma
                   -- SOLVED from premiums for 213 names, so a target is built
                   -- whenever either source can price the session.
                   OR EXISTS (SELECT 1 FROM option_iv v
                              WHERE v.symbol = o.underlying
                                AND v.dt = date(o.time AT TIME ZONE 'Asia/Kolkata')))
              AND time > now() - (%(lookback_days)s || ' days')::interval
              {symbol_filter}
            GROUP BY underlying, date(time AT TIME ZONE 'Asia/Kolkata')
            """,
            params,
        )
        targets = cursor.fetchall()
        if not targets:
            return pd.DataFrame()

        # Session-local temp table (never a live-app table). Connection runs
        # autocommit=True, so ON COMMIT DROP would vanish the table before
        # the next statement -- instead it lives for the connection's
        # lifetime and is cleaned up when the connection closes (or
        # explicitly dropped below, defensively, in case this is ever
        # called twice on one connection).
        cursor.execute("DROP TABLE IF EXISTS m3_targets")
        cursor.execute("CREATE TEMP TABLE m3_targets (underlying text, day date, ts timestamptz)")
        psycopg2.extras.execute_values(
            cursor, "INSERT INTO m3_targets (underlying, day, ts) VALUES %s", targets
        )
        cursor.execute("CREATE INDEX ON m3_targets (underlying, ts)")
        cursor.execute("ANALYZE m3_targets")

        cursor.execute(
            """
            SELECT t.underlying, t.day, t.ts,
                   o.expiry, o.strike, o.option_type,
                   -- The vendor's own gamma WINS where it exists, so index
                   -- behaviour is byte-identical to before this join was added;
                   -- the solved value only fills where the vendor stopped.
                   COALESCE(o.gamma, v.gamma) AS gamma,
                   o.oi,
                   c.lot_size,
                   s.close AS spot
            FROM m3_targets t
            JOIN option_premium_candles o
              ON o.underlying = t.underlying AND o.time = t.ts
            LEFT JOIN option_iv v
              ON v.symbol = o.underlying AND v.dt = t.day
             AND v.expiry = o.expiry AND v.strike = o.strike
             AND v.option_type = o.option_type
            LEFT JOIN fo_underlying_catalog c ON c.symbol = t.underlying
            LEFT JOIN LATERAL (
                SELECT close FROM underlying_spot_candles sc
                WHERE sc.underlying = t.underlying AND sc.time <= t.ts
                ORDER BY sc.time DESC LIMIT 1
            ) s ON true
            -- Was an inner-join condition (`o.gamma IS NOT NULL`) before the
            -- second source existed. It has to be a post-COALESCE filter now,
            -- or contracts priced by NEITHER source arrive with a NULL gamma
            -- and silently become zero-GEX rows in the sum.
            WHERE COALESCE(o.gamma, v.gamma) IS NOT NULL
            """
        )
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()

    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    frame["n_strikes"] = frame.groupby(["underlying", "day"])["strike"].transform("nunique")
    for col in ("gamma", "oi", "lot_size", "spot", "strike"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def signed_gex(rows: pd.DataFrame) -> pd.Series:
    """Per-row dollar gamma exposure, signed per the module's stated
    convention: calls positive, puts negative."""
    magnitude = rows["gamma"] * rows["oi"] * rows["lot_size"] * (rows["spot"] ** 2) * 0.01
    sign = np.where(rows["option_type"].str.upper().isin(("CE", "CALL", "C")), 1.0, -1.0)
    return magnitude * sign


def gamma_flip_level(rows: pd.DataFrame) -> float | None:
    """Interpolated strike where cumulative signed GEX (summed low-to-high
    strike) crosses zero. NULL if it never crosses within observed strikes."""
    by_strike = (
        rows.assign(gex=signed_gex(rows))
        .groupby("strike", as_index=False)["gex"].sum()
        .sort_values("strike")
    )
    if len(by_strike) < 2:
        return None
    cumulative = by_strike["gex"].cumsum().to_numpy()
    strikes = by_strike["strike"].to_numpy()
    signs = np.sign(cumulative)
    if signs[0] == 0:
        return float(strikes[0])
    for i in range(1, len(cumulative)):
        if signs[i] == 0:
            return float(strikes[i])
        if signs[i] != signs[i - 1] and signs[i - 1] != 0:
            k1, k2 = strikes[i - 1], strikes[i]
            c1, c2 = cumulative[i - 1], cumulative[i]
            return float(k1 + (k2 - k1) * (-c1) / (c2 - c1))
    return None


def build_daily_series(snapshot_rows: pd.DataFrame) -> pd.DataFrame:
    """One row per (symbol, day): ts, net_gex, gamma_flip_level, n_strikes.

    `oi` can be NULL even though the source query already required
    gamma IS NOT NULL -- confirmed live (FINNIFTY 2026-08-04 09:15 UTC: both
    of its 2 gamma-populated contract rows have oi NULL). Pandas' `.sum()`
    defaults to skipna=True, so summing signed_gex() over a row with NaN oi
    silently drops that row's contribution -- and when EVERY row in a
    session is NaN-oi, the sum over an all-NaN series is 0.0, not NaN,
    fabricating a "neutral, no dealer bias" reading from zero real OI
    information (a doctrine #5 violation; confirmed live in 8 sessions
    outright, plus 46 more rows silently dropped from otherwise-partial
    sums). Rows with NULL oi are filtered out BEFORE computing anything, and
    a session is skipped (not zeroed) if no valid-oi row survives -- the
    same shape as the existing lot_size/spot skip immediately below.
    """
    records = []
    dropped_oi_null_rows = 0
    skipped_all_oi_null_sessions = 0
    for (symbol, day), group in snapshot_rows.groupby(["underlying", "day"]):
        if group["lot_size"].isna().all() or group["spot"].isna().all():
            continue  # doctrine #5: no silent zero -- skip rather than fabricate multiplier/spot
        valid = group[group["oi"].notna()]
        dropped_oi_null_rows += len(group) - len(valid)
        if valid.empty:
            skipped_all_oi_null_sessions += 1
            continue  # doctrine #5: no silent zero -- skip rather than fabricate net_gex=0.0
        net_gex = float(signed_gex(valid).sum())
        flip = gamma_flip_level(valid)
        records.append({
            "symbol": symbol,
            "day": day,
            "ts": group["ts"].iloc[0],
            "net_gex": net_gex,
            "gamma_flip_level": flip,
            "n_strikes": int(valid["strike"].nunique()),
        })
    frame = pd.DataFrame.from_records(records)
    if dropped_oi_null_rows or skipped_all_oi_null_sessions:
        print(f"  NULL oi: dropped {dropped_oi_null_rows} contract-row(s) from partial sessions, "
              f"skipped {skipped_all_oi_null_sessions} session(s) with zero valid-oi rows entirely")
    if frame.empty:
        return frame
    return frame.sort_values(["symbol", "day"]).reset_index(drop=True)


def percentile_rank_trailing(values: pd.Series) -> pd.Series:
    """Trailing-window percentile rank of each value against itself + up to
    TRAILING_WINDOW-1 preceding values of the SAME symbol, in chronological
    order -- no look-ahead: index i only ever sees values[<=i]."""
    out = pd.Series(index=values.index, dtype=float)
    array = values.to_numpy()
    for i in range(len(array)):
        window = array[max(0, i - TRAILING_WINDOW + 1): i + 1]
        if len(window) < MIN_HISTORY_FOR_REGIME:
            out.iloc[i] = np.nan
            continue
        out.iloc[i] = float((window <= array[i]).sum()) / len(window)
    return out


def bucket_regime(percentile: float) -> str | None:
    if pd.isna(percentile):
        return None
    lo, mid_lo, mid_hi, hi = PERCENTILE_CUTS
    if percentile <= lo:
        return REGIME_LABELS[0]
    if percentile <= mid_lo:
        return REGIME_LABELS[1]
    if percentile <= mid_hi:
        return REGIME_LABELS[2]
    if percentile <= hi:
        return REGIME_LABELS[3]
    return REGIME_LABELS[4]


def compute_regime(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()
    daily["percentile"] = daily.groupby("symbol")["net_gex"].transform(percentile_rank_trailing)
    daily["regime"] = daily["percentile"].apply(bucket_regime)
    return daily


def upsert(connection, daily: pd.DataFrame) -> int:
    """net_gex and gamma_flip_level are raw/unnormalized (doctrine #1 does
    not forbid storing them -- the spec's own Section 4 schema names them
    explicitly as informational/diagnostic columns -- but a MODEL INPUT must
    be normalized). gex_percentile (the trailing-window percentile already
    computed for bucketing, previously discarded before storage) is that
    normalized column; a later phase consuming this table as a feature
    should read gex_percentile, not net_gex.
    """
    payload = [
        (row.ts.to_pydatetime() if hasattr(row.ts, "to_pydatetime") else row.ts,
         row.symbol,
         row.net_gex,
         row.regime,
         row.gamma_flip_level if pd.notna(row.gamma_flip_level) else None,
         row.percentile if pd.notna(row.percentile) else None)
        for row in daily.itertuples()
    ]
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO regime (ts, symbol, net_gex, regime, gamma_flip_level, gex_percentile)
               VALUES %s
               ON CONFLICT (symbol, ts) DO UPDATE SET
                 net_gex = EXCLUDED.net_gex,
                 regime = EXCLUDED.regime,
                 gamma_flip_level = EXCLUDED.gamma_flip_level,
                 gex_percentile = EXCLUDED.gex_percentile""",
            payload,
        )
    return len(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None, help="restrict to one underlying, e.g. NIFTY")
    parser.add_argument("--lookback-days", type=int, default=150,
                         help="calendar-day window (default 150, >60 trading sessions -- "
                              "see PRODUCTION-SAFETY NOTE in the module docstring)")
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    parser.add_argument("--dry-run", action="store_true", help="compute and print, do not write")
    args = parser.parse_args()

    connection = psycopg2.connect(args.dsn)
    connection.autocommit = True

    print(f"loading gamma-populated snapshot rows from option_premium_candles "
          f"(last {args.lookback_days} days) ...")
    snapshot_rows = load_snapshot_rows(connection, args.symbol, args.lookback_days)
    print(f"  {len(snapshot_rows)} contract rows across "
          f"{snapshot_rows[['underlying', 'day']].drop_duplicates().shape[0]} (symbol, day) snapshots, "
          f"{snapshot_rows['underlying'].nunique()} distinct underlyings")
    if snapshot_rows.empty:
        print("no gamma data found at all -- nothing to compute. Exiting without writing.")
        connection.close()
        return 1

    missing_join = snapshot_rows[snapshot_rows["lot_size"].isna() | snapshot_rows["spot"].isna()]
    if not missing_join.empty:
        bad_symbols = sorted(missing_join["underlying"].unique())
        print(f"  {len(missing_join)} rows dropped for missing lot_size or spot join "
              f"(symbols: {bad_symbols[:10]}{' ...' if len(bad_symbols) > 10 else ''})")

    daily = build_daily_series(snapshot_rows)
    print(f"daily (symbol, day) GEX observations: {len(daily)}")
    if daily.empty:
        print("nothing survived the lot_size/spot join -- nothing to compute.")
        connection.close()
        return 1

    date_min, date_max = daily["day"].min(), daily["day"].max()
    print(f"date range: {date_min} to {date_max}")

    # Coverage era split -- report plainly, doctrine #5.
    dense = daily[daily["n_strikes"] >= 15]
    sparse = daily[daily["n_strikes"] < 15]
    print(f"  dense (>=15 strikes at snapshot) sessions: {len(dense)} "
          f"({dense['day'].min() if not dense.empty else '-'} to {dense['day'].max() if not dense.empty else '-'})")
    print(f"  sparse (<15 strikes, truncated near-ATM read) sessions: {len(sparse)} "
          f"({sparse['day'].min() if not sparse.empty else '-'} to {sparse['day'].max() if not sparse.empty else '-'})")

    regime = compute_regime(daily)

    if args.dry_run:
        print("\n--dry-run: not writing to the database")
    else:
        written = upsert(connection, regime)
        print(f"\nregime: {written} rows written")

    print(f"\nregime distribution ({len(regime)} total rows, includes NULL for insufficient trailing history):")
    counts = regime["regime"].fillna("NULL (insufficient history)").value_counts()
    for label, count in counts.items():
        print(f"  {label:<28} {count}")

    per_symbol_sessions = regime.groupby("symbol")["day"].count().sort_values(ascending=False)
    print(f"\nsymbols covered: {regime['symbol'].nunique()}; "
          f"most-covered: {per_symbol_sessions.head(5).to_dict()}")

    # Spec's own acceptance check: NIFTY around 2026-08-10..08-13 vs a NEG/STRONG_NEG claim.
    nifty = regime[regime["symbol"] == "NIFTY"].set_index("day")
    print("\nAug 10-13 2026 NIFTY spot-check (spec claims NEG regimes; verifying, not assuming):")
    for day in (date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13)):
        if day not in nifty.index:
            print(f"  {day}: NO ROW -- zero gamma-populated contracts that day (verified: "
                  f"NIFTY had 0 non-null-gamma rows on {day} in option_premium_candles). "
                  f"Cannot confirm or deny the spec's claim for this date.")
            continue
        r = nifty.loc[day]
        print(f"  {day}: net_gex={r['net_gex']:.3e}  percentile={r['percentile']:.3f}  "
              f"regime={r['regime']}  n_strikes={r['n_strikes']} (snapshot ts={r['ts']})")

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
