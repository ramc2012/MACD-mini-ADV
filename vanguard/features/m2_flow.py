"""M2 -- Options Informed-Flow Scanner.

Reads three existing live tables directly (no new collector -- see
db/migrations/001_schema.sql's inventory): `option_premium_candles` (per-
contract IV/delta/OI/volume), `fo_option_chain_metrics` (chain-level OI-PCR)
and `underlying_spot_candles` (stock volume, and a spot-price fallback for
ATM selection). Universe is `sector_taxonomy` (Equity rows only).

WHY "AS-OF" IS NOT TODAY (doctrine #3 + #5, verified live 2026-08-26)
----------------------------------------------------------------------
Two of the three source tables are stale, confirmed by direct query rather
than assumed:

  * `fo_option_chain_metrics.time` tops out at 2026-08-03 22:00 UTC (its
    own PK/synced_at rows keep getting touched by a backfill job -- one row
    seen with synced_at=2026-08-26 -- but no *new* `time` value has been
    written past 2026-08-03; 23 days stale as of this run). This is a
    stopped collector, not a quiet-market-hours read.
  * `option_premium_candles.iv` / `.delta` (the per-contract greeks the spec
    needs for IVS and SKEW) go stale even earlier for the *equity* universe:
    every one of the 210 sector_taxonomy equities lost fresh greeks by
    2026-07-28 at the latest (a handful -- RELIANCE, TCS, SBIN, HDFCBANK,
    ICICIBANK, IREDA, KALYANKJIL, SAMMAANCAP -- stopped even earlier, back
    to 2026-06-30). Zero equity names have any `iv` row after 2026-07-28.
    `close`/`oi`/`volume` on the same table keep updating through today --
    only the greeks computation stopped, not the raw feed.

Per doctrine #5 ("if an input isn't currently live/fresh, verify with a
real query first ... do not fabricate or substitute silently"), this module
does NOT force ts=today and report every ingredient NULL for that reason.
Instead it uses "the true max(time) found" (the task's own instruction):
AS_OF_DATE = the newest calendar session for which ANY equity in the
universe still has `iv` data = 2026-07-28, verified live. 200 of 210 names
actually have a chain on that exact date; the other 10 get IVS/SKEW/O-S
stored NULL for this run, per-name, rather than reusing older stale rows
under a false "current" timestamp. `fo_option_chain_metrics` easily covers
2026-07-28 (its own data runs to 2026-08-03), so PCR-OI is computed as of
the same session for consistency.

Caveat worth flagging: 2026-07-28 is itself the last Tuesday of July, i.e.
NSE's monthly stock-options expiry day. The front-month contract is thin by
end of day on its own expiry (see live-run report for per-symbol strike
counts) -- this is a real property of the freshest available session, not
an artifact of this module.

INGREDIENT 4 (delta-OI conjunction): LIVE since 2026-08-28, on option open
interest. It was NULL for every name always, on the reasoning that the spec
frames the conjunction on stock FUTURES OI and no such source exists in this
schema -- `underlying_spot_candles.oi` is 0/NULL for every stock row (only
index rows carry it, and those are spot, not futures) and
`index_futures_candles` covers only BANKNIFTY/NIFTY/SENSEX. All of that is
still true. What was wrong was concluding the ingredient was therefore
uncomputable: `option_premium_candles.oi` is populated on EVERY row (24.3M
rows, 217 underlyings, back to 2024-04-24) and answers the same question the
conjunction asks -- whether positions are being opened or closed into the
move. It now yields ~207 of 210 names every session. See `load_oi_eod` and
`build_oi_series`, and note the rollover guard: front-expiry OI collapses at
each expiry, so delta_oi is measured only across sessions sharing a front
contract.

COMPOSITE NORMALIZATION (documented per doctrine's "renormalize, don't
treat NULL as 0" requirement)
----------------------------------------------------------------------
Every ingredient is first mapped onto a common [-1, 1] scale before
weighting, so the weighted sum can be rescaled to [-100, 100] by a single
multiply:
  * ivs_z, skew_z, pcr_z: z-scores, unbounded in principle -- clipped to
    [-3, 3] (the conventional "extreme" cap) then divided by 3.
  * os_pctile: a [0, 1] percentile rank -- centered via (p - 0.5) * 2.
  * oi_state: pre-mapped by the spec itself to {-1, -0.5, 0, +1} -- used
    as-is (already in range).
flow_score = 100 * sum(weight_i * component_i) / sum(weight_i for i not
NULL), i.e. weights (30/25/20/15/10 for IVS/SKEW/O-S/OI/PCR) are
renormalized to sum to 1 over whatever ingredients are actually present for
that name -- NULL never silently contributes 0 to a 100%-weight sum. A name
with zero non-NULL ingredients gets flow_score = NULL, not 0.

    python vanguard/features/m2_flow.py
    python vanguard/features/m2_flow.py --as-of 2026-07-28 --lookback-days 130
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

DEFAULT_DSN = ""

Z_CLIP = 3.0            # cap on raw z-scores before mapping to [-1, 1]
IVS_SKEW_Z_WINDOW = 20  # sessions, per spec
PCR_Z_WINDOW = 20       # sessions, per spec
OS_PCTILE_WINDOW = 60   # sessions, per spec
NEAR_ATM_STRIKES = 2    # +-2 strikes, per spec
WEIGHTS = {"ivs": 30, "skew": 25, "os": 20, "oi": 15, "pcr": 10}
OI_STATE_SCORE = {
    "short_buildup": -1.0, "long_unwind": -0.5,
    "short_covering": 0.0, "long_buildup": 1.0,
}


# --------------------------------------------------------------------------
# Ingredient 4 -- delta-OI conjunction. Live on option CE+PE open interest
# (see module docstring). Kept as a pure function of (delta_oi, delta_price)
# so it stays independently testable and stays correct whatever supplies
# those two numbers -- build_oi_series is what now feeds it real inputs.
# --------------------------------------------------------------------------
def classify_oi_state(delta_oi: float | None, delta_price: float | None) -> str | None:
    if delta_oi is None or delta_price is None or delta_oi == 0 or delta_price == 0:
        return None
    if delta_oi > 0 and delta_price > 0:
        return "long_buildup"
    if delta_oi < 0 and delta_price > 0:
        return "short_covering"
    if delta_oi < 0 and delta_price < 0:
        return "long_unwind"
    return "short_buildup"  # delta_oi > 0 and delta_price < 0


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------
def load_universe(connection) -> list[str]:
    frame = pd.read_sql(
        "SELECT symbol FROM sector_taxonomy WHERE instrument_type = 'Equity'", connection)
    return sorted(frame["symbol"].unique())


def find_as_of_date(connection, symbols: list[str]) -> date:
    """The true freshest session that can actually produce an ingredient.

    Per the module docstring: doctrine #5 forbids assuming "today" is
    available. This is the live query that establishes what actually is.

    ANCHORED ON OPEN INTEREST, not on `option_premium_candles.iv`. That column
    died for the equity universe on 2026-07-28, and anchoring here meant the
    whole lane kept re-deriving 2026-07-28 for a month -- M6's flow_fresh leg
    allows 3 sessions and was being handed 23, so every candidate died at that
    leg (measured 2026-08-28: 2,431 failures per session, exactly the 187
    scored symbols x 13 bars). Nothing downstream was broken; this date was.

    `option_premium_candles.oi` is populated on EVERY row, for ~210 names, back
    to 2024-04-24, and it feeds two ingredients on its own (PCR and the
    delta-OI conjunction). So it, not iv, is the honest floor for "is there
    anything to compute". IV now arrives separately via `iv_surface` (computed
    from premiums by features/m_implied_vol.py rather than sourced from a
    broker), and it is allowed to be missing for a session without stalling
    the lane -- a score built from fewer ingredients is still recorded as such
    in n_ingredients, which is exactly what that column is for.
    """
    query = """
        SELECT max(date(time AT TIME ZONE 'Asia/Kolkata'))
        FROM option_premium_candles
        WHERE underlying = ANY(%(symbols)s) AND oi IS NOT NULL
    """
    with connection.cursor() as cursor:
        cursor.execute(query, {"symbols": symbols})
        (as_of,) = cursor.fetchone()
    if as_of is None:
        raise RuntimeError("no equity in the universe has any open interest -- cannot proceed")
    return as_of


def load_chain_eod(connection, symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """End-of-session chain: last 30-minute print of each (underlying, expiry,
    strike, option_type) per trading day. interval='30minute' only (the
    interval greeks are actually populated under; verified live).

    The live table genuinely holds exact-timestamp duplicates for the same
    key (10,256 such groups on one spot-checked day alone), commonly one row
    with real iv/delta and a sibling with NULLs. `ORDER BY time DESC` alone
    has no tiebreak between them, so which one `rn=1` picked was whichever
    order Postgres happened to return -- not guaranteed stable, and verified
    to actually drift run-to-run (two runs of the identical command against
    the identical historical session returned different non-NULL counts and
    different top-flow-score names). Preferring the non-NULL iv row as the
    explicit tiebreak makes the result deterministic AND picks the row that
    actually has the data this module needs.
    """
    query = """
        WITH ranked AS (
            SELECT underlying, expiry, strike, option_type, iv, delta,
                   underlying_price, volume,
                   date(time AT TIME ZONE 'Asia/Kolkata') AS dt,
                   ROW_NUMBER() OVER (
                       PARTITION BY underlying, expiry, strike, option_type,
                                    date(time AT TIME ZONE 'Asia/Kolkata')
                       ORDER BY time DESC, (iv IS NULL) ASC, (delta IS NULL) ASC) AS rn
            FROM option_premium_candles
            WHERE underlying = ANY(%(symbols)s) AND interval = '30minute'
              AND time >= %(start)s AND time < %(end)s
        )
        SELECT underlying, expiry, strike, option_type, iv, delta,
               underlying_price, volume, dt
        FROM ranked WHERE rn = 1
    """
    frame = pd.read_sql(query, connection, params={
        "symbols": symbols, "start": start, "end": end + timedelta(days=1)})
    for col in ("iv", "delta", "underlying_price", "strike"):
        frame[col] = frame[col].astype(float)
    frame["volume"] = frame["volume"].astype(float)
    return frame


def load_daily_spot(connection, symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """Daily close + summed volume per session -- ATM fallback and O/S denominator."""
    query = """
        SELECT underlying, date(time AT TIME ZONE 'Asia/Kolkata') AS dt,
               (array_agg(close ORDER BY time DESC))[1] AS close,
               sum(volume) AS volume
        FROM underlying_spot_candles
        WHERE interval = '30minute' AND underlying = ANY(%(symbols)s)
          AND time >= %(start)s AND time < %(end)s
        GROUP BY underlying, date(time AT TIME ZONE 'Asia/Kolkata')
    """
    frame = pd.read_sql(query, connection, params={
        "symbols": symbols, "start": start, "end": end + timedelta(days=1)})
    frame["close"] = frame["close"].astype(float)
    frame["volume"] = frame["volume"].astype(float)
    return frame


def load_oi_eod(connection, symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """Front-expiry CE/PE open interest per session, from option_premium_candles.

    REPLACES fo_option_chain_metrics, which this used to read. That table
    stopped updating on 2026-08-03, and even while it was alive it carried only
    7-11 of the 210 universe names per session with a mostly-NULL `oi_pcr` --
    so PCR, the one ingredient that never needed IV, was itself starved.
    `option_premium_candles.oi` is populated on every row (24.3M rows, 217
    underlyings, back to 2024-04-24) and yields 207 of 210 names every session.

    Returns one row per (underlying, dt): the front expiry, its summed CE and
    PE open interest at the last print of the session, and the derived
    put/call OI ratio. `expiry` is returned deliberately -- see
    build_oi_series for why a day-over-day OI change is only meaningful when
    it is measured on the SAME contract.
    """
    query = """
        WITH front AS (
            SELECT underlying, date(time AT TIME ZONE 'Asia/Kolkata') AS dt,
                   MIN(expiry) AS expiry
            FROM option_premium_candles
            WHERE interval = '30minute' AND oi IS NOT NULL
              AND underlying = ANY(%(symbols)s)
              AND time >= %(start)s AND time < %(end)s
              AND expiry >= date(time AT TIME ZONE 'Asia/Kolkata')
            GROUP BY 1, 2
        ), last_print AS (
            -- One row per contract per session: its FINAL open interest. OI is
            -- a stock, not a flow, so it is the closing level that matters --
            -- summing intraday rows would multiply-count the same contract.
            SELECT o.underlying, date(o.time AT TIME ZONE 'Asia/Kolkata') AS dt,
                   o.option_type, o.strike,
                   (array_agg(o.oi ORDER BY o.time DESC))[1] AS oi,
                   (array_agg(o.close ORDER BY o.time DESC))[1] AS px
            FROM option_premium_candles o
            JOIN front f
              ON f.underlying = o.underlying
             AND f.dt = date(o.time AT TIME ZONE 'Asia/Kolkata')
             AND f.expiry = o.expiry
            WHERE o.interval = '30minute' AND o.oi IS NOT NULL AND o.close IS NOT NULL
              AND o.time >= %(start)s AND o.time < %(end)s
            GROUP BY 1, 2, 3, 4
        )
        SELECT l.underlying, l.dt, f.expiry,
               SUM(l.oi) FILTER (WHERE l.option_type = 'CE') AS ce_oi,
               SUM(l.oi) FILTER (WHERE l.option_type = 'PE') AS pe_oi,
               -- OI-WEIGHTED premium per side, not a plain mean: a chain has
               -- many far-OTM strikes trading at a few paise, and averaging
               -- them flat would let the tail dominate a number that is meant
               -- to say "what is the money in this side actually worth". The
               -- weighting puts it where the open interest is.
               SUM(l.px * l.oi) FILTER (WHERE l.option_type = 'CE')
                 / NULLIF(SUM(l.oi) FILTER (WHERE l.option_type = 'CE'), 0) AS ce_px,
               SUM(l.px * l.oi) FILTER (WHERE l.option_type = 'PE')
                 / NULLIF(SUM(l.oi) FILTER (WHERE l.option_type = 'PE'), 0) AS pe_px
        FROM last_print l
        JOIN front f ON f.underlying = l.underlying AND f.dt = l.dt
        GROUP BY 1, 2, 3
    """
    frame = pd.read_sql(query, connection, params={
        "symbols": symbols, "start": start, "end": end + timedelta(days=1)})
    if frame.empty:
        return pd.DataFrame(columns=["underlying", "dt", "expiry", "ce_oi", "pe_oi",
                                     "ce_px", "pe_px", "total_oi", "oi_pcr"])
    for col in ("ce_oi", "pe_oi", "ce_px", "pe_px"):
        frame[col] = frame[col].astype(float)
    frame["total_oi"] = frame["ce_oi"] + frame["pe_oi"]
    # A zero call-side leaves the ratio undefined rather than infinite.
    frame["oi_pcr"] = np.where(frame["ce_oi"] > 0, frame["pe_oi"] / frame["ce_oi"], np.nan)
    return frame


def load_iv_surface_eod(connection, symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """Per-session IVS and 25-delta skew from `iv_surface`.

    The IV here is COMPUTED from option premiums by features/m_implied_vol.py,
    not read from a broker column -- which is why it exists at all for sessions
    after 2026-07-28, and why it can cover history the live chain never can.

    Measured 2026-08-28: `ivs` is present for ~160 of the 210 universe names on
    the latest session and 91% of rows overall; `skew_25d` is present for
    roughly 10% and is frequently absent entirely, because a 25-delta strike on
    BOTH wings is exactly what a thin single-stock chain does not have. Skew is
    therefore expected to be NULL for most names and is left NULL rather than
    approximated from whatever strikes happen to exist.
    """
    query = """
        SELECT symbol AS underlying, dt, ivs, skew_25d AS skew
        FROM iv_surface
        WHERE symbol = ANY(%(symbols)s) AND dt >= %(start)s AND dt <= %(end)s
    """
    frame = pd.read_sql(query, connection, params={
        "symbols": symbols, "start": start, "end": end})
    if frame.empty:
        return pd.DataFrame(columns=["underlying", "dt", "ivs", "skew"])
    for col in ("ivs", "skew"):
        frame[col] = frame[col].astype(float)
    return frame


def build_surface_series(surface: pd.DataFrame) -> pd.DataFrame:
    """Per (underlying, dt): ivs/skew and their trailing z-scores.

    Separate from build_session_ingredients on purpose. That function derives
    ivs and skew from the raw chain's `iv` column, which has been NULL for the
    equity universe since 2026-07-28 -- so for every recent session it produces
    no row at all, and the z-score has nothing to stand on. `iv_surface` is the
    computed replacement and covers those sessions, so the two cannot share a
    path. The O/S ratio still comes from the chain, because it needs per-
    contract delta and volume rather than a surface summary.
    """
    if surface.empty:
        return pd.DataFrame(columns=["underlying", "dt", "ivs", "skew", "ivs_z", "skew_z"])
    out = []
    for underlying, group in surface.sort_values("dt").groupby("underlying"):
        group = group.set_index("dt")
        out.append(pd.DataFrame({
            "underlying": underlying, "dt": group.index,
            "ivs": group["ivs"].values, "skew": group["skew"].values,
            "ivs_z": _zscore(group["ivs"], IVS_SKEW_Z_WINDOW, min_periods=5).values,
            "skew_z": _zscore(group["skew"], IVS_SKEW_Z_WINDOW, min_periods=5).values,
        }))
    return pd.concat(out, ignore_index=True)


def build_oi_series(oi: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    """Per (underlying, dt): pcr_z, and the delta-OI conjunction (ingredient 4).

    INGREDIENT 4, finally wired. It was NULL for every name always, because the
    spec frames it on stock FUTURES open interest and no such source exists
    anywhere in this schema. Option CE+PE open interest does exist, completely,
    and carries the same information the conjunction is asking for: whether
    positions are being opened or closed into the move.

    THE ROLLOVER GUARD is the whole difficulty. Front-expiry OI collapses to
    near zero the session a contract expires and the next one becomes front, so
    a naive diff() reports a colossal "unwind" on every rollover -- a fake
    signal, on a schedule, for every name at once. delta_oi is therefore
    computed ONLY across consecutive sessions whose front expiry is unchanged;
    across a roll it is None and the ingredient simply does not contribute.
    """
    if oi.empty:
        return pd.DataFrame(columns=["underlying", "dt", "oi_pcr", "pcr_change",
                                     "pcr_z", "oi_state", "ce_state", "pe_state"])
    close_map = {(r.underlying, r.dt): r.close for r in spot.itertuples()}
    out = []
    for underlying, group in oi.sort_values("dt").groupby("underlying"):
        group = group.set_index("dt")
        change = group["oi_pcr"].diff(1)
        pcr_z = _zscore(change, PCR_Z_WINDOW, min_periods=5)

        same_contract = group["expiry"] == group["expiry"].shift(1)

        delta_oi = group["total_oi"].diff(1).where(same_contract)
        closes = pd.Series([close_map.get((underlying, dt)) for dt in group.index],
                           index=group.index, dtype="float64")
        delta_price = closes.diff(1)

        def states_from(d_oi_series, d_px_series):
            return [
                classify_oi_state(
                    None if pd.isna(d_oi) else float(d_oi),
                    None if pd.isna(d_px) else float(d_px),
                )
                for d_oi, d_px in zip(d_oi_series, d_px_series)
            ]

        # PER-SIDE MOMENTUM, each leg measured on ITS OWN open interest and ITS
        # OWN premium -- not on the underlying's spot.
        #
        # The combined `oi_state` above answers "are positions being opened or
        # closed into the underlying's move". It cannot answer the question a
        # ticket actually needs, which is whether THIS CONTRACT is being
        # accumulated or abandoned. A call can bleed while the underlying drifts
        # up (theta, an IV crush, or simply nobody paying up for it), and the
        # combined reading cannot see that -- it sums CE and PE OI together and
        # then measures against spot, so both sides always share one verdict.
        #
        # Measured 2026-08-28, the two sides are NOT mirror images: 15 names had
        # both sides short_covering and 11 had both long_unwind -- states a
        # mirrored model cannot even express -- while 18 showed calls building
        # against puts unwinding and 5 the reverse. Selecting the option from
        # the underlying's direction discards all of that.
        ce_state = states_from(group["ce_oi"].diff(1).where(same_contract),
                               group["ce_px"].diff(1).where(same_contract))
        pe_state = states_from(group["pe_oi"].diff(1).where(same_contract),
                               group["pe_px"].diff(1).where(same_contract))

        out.append(pd.DataFrame({
            "underlying": underlying, "dt": group.index,
            "oi_pcr": group["oi_pcr"].values, "pcr_change": change.values,
            "pcr_z": pcr_z.values,
            "oi_state": states_from(delta_oi, delta_price),
            "ce_state": ce_state, "pe_state": pe_state,
        }))
    return pd.concat(out, ignore_index=True)


# --------------------------------------------------------------------------
# Per-session, per-symbol ingredient extraction
# --------------------------------------------------------------------------
def _session_ivs_skew_os(group: pd.DataFrame, spot_close: float | None) -> dict:
    """One (underlying, dt) chain snapshot -> raw ivs, skew, delta-weighted volume."""
    front_expiry = group["expiry"].min()
    chain = group[group["expiry"] == front_expiry]

    price_candidates = chain["underlying_price"].dropna()
    atm_price = float(price_candidates.median()) if not price_candidates.empty else spot_close

    ivs = np.nan
    if atm_price is not None:
        strikes = sorted(chain["strike"].unique())
        if strikes:
            atm_strike = min(strikes, key=lambda s: abs(s - atm_price))
            idx = strikes.index(atm_strike)
            near = set(strikes[max(0, idx - NEAR_ATM_STRIKES): idx + NEAR_ATM_STRIKES + 1])
            near_chain = chain[chain["strike"].isin(near)]
            ce_iv = near_chain.loc[near_chain["option_type"] == "CE", "iv"].dropna()
            pe_iv = near_chain.loc[near_chain["option_type"] == "PE", "iv"].dropna()
            if not ce_iv.empty and not pe_iv.empty:
                ivs = float(ce_iv.mean() - pe_iv.mean())

    skew = np.nan
    puts = chain[(chain["option_type"] == "PE") & chain["delta"].notna() & chain["iv"].notna()]
    calls = chain[(chain["option_type"] == "CE") & chain["delta"].notna() & chain["iv"].notna()]
    if not puts.empty and not calls.empty:
        put_row = puts.loc[(puts["delta"] - (-0.25)).abs().idxmin()]
        call_row = calls.loc[(calls["delta"] - 0.25).abs().idxmin()]
        skew = float(put_row["iv"] - call_row["iv"])

    weighted = chain[chain["delta"].notna()]
    os_raw = np.nan
    if len(weighted) >= 2:
        os_raw = float((weighted["delta"].abs() * weighted["volume"]).sum())

    return {"ivs": ivs, "skew": skew, "os_raw": os_raw,
            "n_strikes": len(chain["strike"].unique()),
            "n_delta_rows": len(weighted)}


def build_session_ingredients(chain: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    spot_close = {(r.underlying, r.dt): r.close for r in spot.itertuples()}
    rows = []
    for (underlying, dt), group in chain.groupby(["underlying", "dt"]):
        result = _session_ivs_skew_os(group, spot_close.get((underlying, dt)))
        result.update(underlying=underlying, dt=dt)
        rows.append(result)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Rolling statistics (causal -- each row uses only itself and prior rows)
# --------------------------------------------------------------------------
def _zscore(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    mean = series.rolling(window, min_periods=min_periods).mean()
    std = series.rolling(window, min_periods=min_periods).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)


def _rolling_percentile(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """Percentile rank of the window's LAST value among the window's VALID values.

    Two real bugs lived here. First: when the current (last) value is itself
    NaN, `values <= values[-1]` is False for every comparison under NumPy's
    NaN semantics, so the mean of an all-False array silently returned 0.0 --
    the single most bearish possible reading -- for a name with no data at
    all, a doctrine #5 fabrication (confirmed live: 113/210 stored rows hit
    this, 20 of them driving flow_score all the way to -100.0 from nothing).
    Second: even with a real current value, `.mean()` divides by the RAW
    window length, so any NaN gap earlier in the window (common -- most
    names have chain data on well under half of sessions) silently deflated
    every percentile computed from a gappy window.
    """
    def pct_of_last(values: np.ndarray) -> float:
        current = values[-1]
        if np.isnan(current):
            return np.nan
        valid = values[~np.isnan(values)]
        if valid.size == 0:
            return np.nan
        return float((valid <= current).mean())
    return series.rolling(window, min_periods=min_periods).apply(pct_of_last, raw=True)


def compute_time_series(ingredients: pd.DataFrame, daily_volume: pd.DataFrame,
                         pcr: pd.DataFrame) -> pd.DataFrame:
    volume_map = {(r.underlying, r.dt): r.volume for r in daily_volume.itertuples()}
    out = []
    for underlying, group in ingredients.sort_values("dt").groupby("underlying"):
        group = group.set_index("dt")
        os_ratio = pd.Series(
            {dt: (row["os_raw"] / volume_map.get((underlying, dt)))
                  if pd.notna(row["os_raw"]) and volume_map.get((underlying, dt))
                  else np.nan
             for dt, row in group.iterrows()})
        frame = pd.DataFrame({
            "ivs": group["ivs"], "skew": group["skew"], "os_ratio": os_ratio,
            "n_strikes": group["n_strikes"], "n_delta_rows": group["n_delta_rows"],
        })
        frame["ivs_z"] = _zscore(frame["ivs"], IVS_SKEW_Z_WINDOW, min_periods=5)
        frame["skew_z"] = _zscore(frame["skew"], IVS_SKEW_Z_WINDOW, min_periods=5)
        frame["os_pctile"] = _rolling_percentile(frame["os_ratio"], OS_PCTILE_WINDOW, min_periods=10)
        frame["underlying"] = underlying
        out.append(frame.reset_index().rename(columns={"index": "dt"}))
    ivs_skew_os = pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    pcr_out = []
    for underlying, group in pcr.sort_values("dt").groupby("underlying"):
        group = group.set_index("dt")
        change = group["oi_pcr"].diff(1)
        pcr_z = _zscore(change, PCR_Z_WINDOW, min_periods=5)
        pcr_out.append(pd.DataFrame({
            "underlying": underlying, "dt": group.index,
            "oi_pcr": group["oi_pcr"].values, "pcr_change": change.values,
            "pcr_z": pcr_z.values,
        }))
    pcr_series = pd.concat(pcr_out, ignore_index=True) if pcr_out else pd.DataFrame(
        columns=["underlying", "dt", "oi_pcr", "pcr_change", "pcr_z"])

    return ivs_skew_os, pcr_series


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------
def _component(z: float | None, kind: str) -> float | None:
    if z is None or (isinstance(z, float) and np.isnan(z)):
        return None
    if kind == "z":
        return float(np.clip(z, -Z_CLIP, Z_CLIP) / Z_CLIP)
    if kind == "pctile":
        return float((z - 0.5) * 2)
    if kind == "oi":
        return float(z)
    raise ValueError(kind)


def compute_flow_score(ivs_z, skew_z, os_pctile, oi_state, pcr_z) -> tuple[float | None, dict]:
    components = {
        "ivs": (_component(ivs_z, "z"), WEIGHTS["ivs"]),
        "skew": (_component(skew_z, "z"), WEIGHTS["skew"]),
        "os": (_component(os_pctile, "pctile"), WEIGHTS["os"]),
        "oi": (_component(OI_STATE_SCORE.get(oi_state), "oi") if oi_state else None, WEIGHTS["oi"]),
        "pcr": (_component(pcr_z, "z"), WEIGHTS["pcr"]),
    }
    available = {k: (v, w) for k, (v, w) in components.items() if v is not None}
    if not available:
        return None, {"weights_used": {}, "n_ingredients": 0}
    total_weight = sum(w for _, w in available.values())
    score = sum(v * w for v, w in available.values()) / total_weight * 100
    return float(score), {
        "weights_used": {k: w / total_weight for k, (_, w) in available.items()},
        "n_ingredients": len(available),
    }


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS features_flow (
    ts          TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    ivs         DOUBLE PRECISION,
    ivs_z       DOUBLE PRECISION,
    skew        DOUBLE PRECISION,
    skew_z      DOUBLE PRECISION,
    os_pctile   DOUBLE PRECISION,
    oi_state    TEXT,
    -- Per-side momentum: is THIS contract being accumulated or abandoned.
    -- The combined oi_state cannot answer that -- it sums CE and PE OI and
    -- measures against spot, so both sides always share one verdict.
    ce_state    TEXT,
    pe_state    TEXT,
    pcr_z       DOUBLE PRECISION,
    flow_score  DOUBLE PRECISION,
    -- How many of the five ingredients actually contributed. A +/-100 built
    -- from one ingredient is not the same reading as one built from five,
    -- and M6's flow_fresh leg refuses the former. Computed since the first
    -- build; stored only since migration 006 (it used to be thrown away
    -- after the report was printed).
    n_ingredients INTEGER,
    PRIMARY KEY (symbol, ts)
);
SELECT create_hypertable('features_flow', 'ts', if_not_exists => TRUE);
ALTER TABLE features_flow ADD COLUMN IF NOT EXISTS n_ingredients INTEGER;
ALTER TABLE features_flow ADD COLUMN IF NOT EXISTS ce_state TEXT;
ALTER TABLE features_flow ADD COLUMN IF NOT EXISTS pe_state TEXT;
"""


def apply_schema(connection) -> None:
    with connection.cursor() as cursor:
        if os.environ.get("VANGUARD_SCHEMA_MANAGED") == "true":
            # MACD's market-only role cannot ALTER legacy tables. Fail if provisioning is incomplete.
            cursor.execute("SELECT ts,symbol,n_ingredients,ce_state,pe_state FROM features_flow LIMIT 0")
            return
        cursor.execute(CREATE_TABLE_SQL)


def upsert_features(connection, rows: list[tuple]) -> int:
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO features_flow
               (ts, symbol, ivs, ivs_z, skew, skew_z, os_pctile, oi_state, pcr_z,
                flow_score, n_ingredients, ce_state, pe_state)
               VALUES %s
               ON CONFLICT (symbol, ts) DO UPDATE SET
                 ivs = EXCLUDED.ivs, ivs_z = EXCLUDED.ivs_z,
                 skew = EXCLUDED.skew, skew_z = EXCLUDED.skew_z,
                 os_pctile = EXCLUDED.os_pctile, oi_state = EXCLUDED.oi_state,
                 pcr_z = EXCLUDED.pcr_z, flow_score = EXCLUDED.flow_score,
                 n_ingredients = EXCLUDED.n_ingredients,
                 ce_state = EXCLUDED.ce_state, pe_state = EXCLUDED.pe_state""",
            rows,
        )
    return len(rows)


def _nn(value):
    return None if value is None or (isinstance(value, float) and np.isnan(value)) else float(value)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def run(dsn: str, as_of: date | None, lookback_days: int,
        backfill_from: date | None = None) -> dict:
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        apply_schema(connection)
        symbols = load_universe(connection)
        if as_of is None:
            as_of = find_as_of_date(connection, symbols)
        start = as_of - timedelta(days=lookback_days)

        chain = load_chain_eod(connection, symbols, start, as_of)
        spot = load_daily_spot(connection, symbols, start, as_of)
        oi_raw = load_oi_eod(connection, symbols, start, as_of)
        surface_raw = load_iv_surface_eod(connection, symbols, start, as_of)

        # The chain path now contributes ONLY the O/S ratio: its ivs/skew read
        # `option_premium_candles.iv`, which is dead for the equity universe
        # after 2026-07-28. Those two ingredients come from iv_surface instead.
        ingredients = build_session_ingredients(chain, spot)
        ivs_skew_os, _unused_pcr = compute_time_series(
            ingredients, spot, pd.DataFrame(columns=["underlying", "dt", "oi_pcr"]))
        surface_series = build_surface_series(surface_raw)
        oi_series = build_oi_series(oi_raw, spot)

        # BACKFILL. The rolling series above are already computed for EVERY
        # session in the lookback -- the single-date version just threw all but
        # one row away. Emitting the whole range costs one extra pass over a
        # frame already in memory, where re-running per date would reload 130
        # days of chain data once per session written.
        emit_dates = [as_of]
        if backfill_from is not None:
            available = sorted({d for d in oi_series["dt"].unique()
                                if backfill_from <= d <= as_of})
            emit_dates = available or [as_of]

        written_total = 0
        report_rows: list[dict] = []
        for emit in emit_dates:
            written_total += _emit_session(
                connection, symbols, emit, ivs_skew_os, surface_series, oi_series,
                report_rows if emit == as_of else None)
        return {"as_of": as_of, "ts": report_rows[0]["ts"] if report_rows else None, "symbols": symbols,
                "written": written_total, "report_rows": report_rows,
                "sessions": len(emit_dates)}
    finally:
        connection.close()


def _emit_session(connection, symbols, as_of, ivs_skew_os, surface_series,
                  oi_series, report_rows) -> int:
    """Build and upsert one session's rows. Split out of run() so a backfill
    can walk the range without recomputing the rolling series each time."""
    if True:
        as_of_os = ivs_skew_os[ivs_skew_os["dt"] == as_of].set_index("underlying") \
            if not ivs_skew_os.empty else pd.DataFrame()
        as_of_ivs = surface_series[surface_series["dt"] == as_of].set_index("underlying") \
            if not surface_series.empty else pd.DataFrame()
        as_of_pcr = oi_series[oi_series["dt"] == as_of].set_index("underlying") \
            if not oi_series.empty else pd.DataFrame()

        # session close timestamp actually observed on AS_OF_DATE (real print
        # time, not a synthesized midnight) -- the latest raw print time for
        # any universe symbol that day.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT max(time) FROM option_premium_candles "
                "WHERE underlying = ANY(%(symbols)s) "
                "AND date(time AT TIME ZONE 'Asia/Kolkata') = %(dt)s",
                {"symbols": symbols, "dt": as_of},
            )
            (ts,) = cursor.fetchone()

        rows_out = []
        for symbol in symbols:
            ivs_row = as_of_ivs.loc[symbol] if symbol in as_of_ivs.index else None
            os_row = as_of_os.loc[symbol] if symbol in as_of_os.index else None
            pcr_row = as_of_pcr.loc[symbol] if symbol in as_of_pcr.index else None

            ivs_z = float(ivs_row["ivs_z"]) if ivs_row is not None and pd.notna(ivs_row["ivs_z"]) else None
            skew_z = float(ivs_row["skew_z"]) if ivs_row is not None and pd.notna(ivs_row["skew_z"]) else None
            os_pctile = float(os_row["os_pctile"]) if os_row is not None and pd.notna(os_row["os_pctile"]) else None
            pcr_z = float(pcr_row["pcr_z"]) if pcr_row is not None and pd.notna(pcr_row["pcr_z"]) else None
            # Ingredient 4, live at last: the delta-OI conjunction, built from
            # option CE+PE open interest rather than the stock futures OI the
            # spec assumes and this schema has never had.
            oi_state = (pcr_row["oi_state"]
                        if pcr_row is not None and pd.notna(pcr_row["oi_state"]) else None)
            ce_state = (pcr_row["ce_state"]
                        if pcr_row is not None and pd.notna(pcr_row["ce_state"]) else None)
            pe_state = (pcr_row["pe_state"]
                        if pcr_row is not None and pd.notna(pcr_row["pe_state"]) else None)

            flow_score, meta = compute_flow_score(ivs_z, skew_z, os_pctile, oi_state, pcr_z)

            rows_out.append((
                ts, symbol,
                _nn(ivs_row["ivs"]) if ivs_row is not None else None, ivs_z,
                _nn(ivs_row["skew"]) if ivs_row is not None else None, skew_z,
                os_pctile, oi_state, pcr_z, flow_score, meta["n_ingredients"],
                ce_state, pe_state,
            ))
            if report_rows is None:
                continue
            report_rows.append({
                "ts": ts,
                "symbol": symbol, "ivs": _nn(ivs_row["ivs"]) if ivs_row is not None else None,
                "ivs_z": ivs_z, "skew": _nn(ivs_row["skew"]) if ivs_row is not None else None,
                "skew_z": skew_z, "os_pctile": os_pctile,
                "oi_pcr": _nn(pcr_row["oi_pcr"]) if pcr_row is not None else None, "pcr_z": pcr_z,
                "oi_state": oi_state, "ce_state": ce_state, "pe_state": pe_state,
                "flow_score": flow_score, "n_ingredients": meta["n_ingredients"],
                # n_strikes describes the CHAIN snapshot, so it comes from the
                # chain frame -- the iv_surface frame has no such column.
                "n_strikes": int(os_row["n_strikes"]) if os_row is not None and pd.notna(os_row["n_strikes"]) else None,
            })

        return upsert_features(connection, rows_out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                         help="override the auto-detected freshest session (YYYY-MM-DD)")
    parser.add_argument("--lookback-days", type=int, default=130,
                         help="calendar days of chain history to pull (needs >=60 trading sessions "
                              "for the O/S percentile window)")
    parser.add_argument("--backfill-from", type=date.fromisoformat, default=None,
                         help="also write every session from this date up to --as-of")
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()

    result = run(args.dsn, args.as_of, args.lookback_days, args.backfill_from)
    if args.backfill_from is not None:
        print(f"backfill: wrote {result['written']:,} rows across "
              f"{result['sessions']} sessions "
              f"({args.backfill_from} .. {result['as_of']})")
        return 0
    report = pd.DataFrame(result["report_rows"])
    n = len(report)

    print(f"AS_OF_DATE = {result['as_of']}  (true max session with equity OPEN INTEREST; "
          f"see find_as_of_date for why this is no longer anchored on iv)")
    print(f"ts stored  = {result['ts']}  (real observed print time on AS_OF_DATE)")
    print(f"universe   = {n} names (sector_taxonomy, instrument_type='Equity')")
    print(f"rows written to features_flow: {result['written']}")
    print()

    for col, label in [("ivs_z", "IVS"), ("skew_z", "SKEW"), ("os_pctile", "O/S ratio"),
                        ("pcr_z", "PCR-OI shift")]:
        have = report[col].notna().sum()
        print(f"{label:<14} non-NULL for {have}/{n} names "
              f"({'NULL for ' + str(n - have) + ' -- see below' if have < n else 'fully covered'})")
    have_oi = report['oi_state'].notna().sum()
    print(f"{'delta-OI conj':<14} non-NULL for {have_oi}/{n} names "
          f"(option CE+PE open interest; NULL across an expiry roll by design)")
    print(f"{'flow_score':<14} non-NULL for {report['flow_score'].notna().sum()}/{n} names")
    print()

    missing_ivs = report[report["ivs_z"].isna()]["symbol"].tolist()
    if missing_ivs:
        print(f"names without a usable IVS z-score on {result['as_of']} "
              f"(surface or warm-up missing; other ingredients may be present): {missing_ivs}")
    print()

    top = report.reindex(report["flow_score"].abs().sort_values(ascending=False).index).head(5)
    print("5 example rows (including the highest |flow_score| names):")
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(top.to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
