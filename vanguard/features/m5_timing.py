"""M5 -- Microstructure Timing Layer.

Reimplemented independently: does not read from, import, or depend on
`backend/fractal_market_profile/`, `backend/cbe_scanner/`, or their tables
(`market_profiles`, `hourly_profiles`, `cbe_scan_results`, `cbe_scan_runs`).
Reads only `underlying_spot_candles(time, underlying, interval, open, high,
low, close, volume)` at `interval='30minute'` -- 225 underlyings back to
2021, no new collection needed (see db/migrations/001_schema.sql). Also
reads `sector_taxonomy` and `sector_rs` (M4 outputs, read-only) for the
"direction sector RS favours" leg of IGNITION.

Session bars run 09:15-15:15 IST in 30-minute steps (13 bars/session on a
full day; verified live against SAIL/MCX/GLENMARK 2026-08-25: 13 rows,
first bar timestamped 09:15 IST = 03:45 UTC, i.e. bars are start-of-bar
labelled). `underlying_spot_candles.oi` was checked live and found 0/NULL
for every row sampled (RELIANCE, TCS, NIFTY) -- this module never reads it
and needs no OI.

WHY DEVELOPING, NOT RETROSPECTIVE (doctrine #3, no look-ahead)
----------------------------------------------------------------
Every ingredient below -- value area, POC, VWAP-to-date, opening-range state
-- is computed as a *developing* session profile: at bar i, only bars
1..i of that same session are used, mirroring how MACD mini's
`market_profile.py` Profile.add() builds a profile print-by-print rather
than computing it once retrospectively over a whole finished session. Using
the full session's bars (including bars after i) to judge whether bar i is
"beyond value" would be lookahead within the session itself, which is
exactly what the spec's acceptance note ("<=1 bar delay") is testing for --
a retrospective whole-session profile would have zero delay by construction
and would not be a meaningful test at all.

sector_rs is a *daily* M4 output. Using today's own sector_rs row to judge
today's IGNITION would itself be lookahead (today's rs_z20 is only knowable
after today's daily close, same reasoning as the participant-OI archive's
same-day-fetch-after-close pattern). So the sector-direction leg uses the
most recent sector_rs row with ts < session_date -- the last *completed*
session's sector20 reading, never the in-progress one.

THE FOUR INGREDIENTS, AS BUILT (spec names these four; thresholds below are
this build's own choice since the spec does not fully specify them)
----------------------------------------------------------------------------
1. RVOL20 -- this bar's volume / trailing-20-session mean volume of the
   SAME intraday time-of-day bucket (e.g. 10:15-10:45 vs the last 20
   session's own 10:15-10:45 bars), shifted so the current session is never
   in its own trailing window. NULL until 5 prior same-time-of-day sessions
   exist (honest degrade, not a fabricated ratio off a short window).

2. Initiative vs responsive -- a session value area is built fresh each bar
   from that session's bars-so-far: a TPO-style histogram over N_BINS=30
   uniform price bins spanning the bars-so-far range, where each bar adds
   one unit of "presence" to every bin its [low, high] range covers (the
   OHTC-bar analogue of a TPO bracket touching a price level -- see
   `value_area_from_bars`). POC = bin with the most bars touching it (ties
   broken by proximity to the touched range's centre, matching MACD mini's
   tie-break). Value area = the alternating POC-outward expansion until
   >=70% of total touches are included -- the same algorithm shape as MACD
   mini's Profile.value_area(), adapted from tick-letters to bar-touches
   because only OHLC bars are available here, not ticks.
   Directional volume: this bar's volume signed by close vs. the session's
   own VWAP-to-date (typical price (H+L+C)/3, cumulative through this bar).
   initiative = close beyond the value area AND signed volume agrees with
   that direction (above VA + net-buying volume, or below VA + net-selling).
   responsive = close beyond the value area but volume does NOT agree
   (the fade/rejection signature the spec names).

3. Opening-range state -- first bar of the session (09:15-09:45 IST) fixes
   or_high/or_low. Every later bar in that session is inside/above/below
   that range; if outside, by how many ATR(14) (daily, computed from
   session-aggregated OHLC, using only the 14 sessions BEFORE the current
   one -- never the current session's own still-forming range) the close
   sits beyond the boundary. The opening bar itself is not yet classifiable
   (the range it defines has not closed) and is marked "forming", mirroring
   MACD mini's open_type()/day_type() distinction between "too early" and
   an actual reading.

4. Value-area position -- va_position = (close - va_low) / (va_high -
   va_low) for the bar's own developing value area, UNCLIPPED: inside the
   value area this is naturally in [0, 1]; outside, the same ratio already
   extends past 1 or below 0 by exactly the right amount (e.g. 1.15 = "0.15
   value-area-widths above VAH"), so no separate clipped/unclipped pair of
   columns is needed -- the spec's "clipped to [0,1] with values outside
   stored as signed distance" is exactly what this one unclipped ratio is.

COMPOSITE timing_state (this build's own thresholds -- spec gives only
IGNITION's definition in full)
----------------------------------------------------------------------------
Checked in this priority order (first match wins) so the four states are
mutually exclusive per bar:

  IGNITION    RVOL20 >= 2.0 AND initiative activity AND price beyond value
              area, in the direction that symbol's sector20's most recent
              PRIOR-session rs_z20 favours (rs_z20 > 0 favours "above";
              rs_z20 < 0 favours "below"). If sector_rs has no prior-session
              row for that sector20 (or is unsigned/0), sector confirmation
              cannot be evaluated and IGNITION does not fire on this bar --
              an honest degrade rather than assuming alignment.

  EXHAUST     RVOL20 >= 1.5 AND responsive activity (high-volume rejection
              at a value-area extreme -- climactic volume that fails to
              hold beyond value, the auction-theory exhaustion signature).
              Does not require sector alignment: exhaustion is a rejection
              regardless of which way the sector leans.

  COMPRESSION RVOL20 <= 0.7 AND this bar's range (high-low) vs. its own
              trailing-20-session same-time-of-day mean (`range_ratio`)
              <= 0.7 AND close is inside the value area. Low participation,
              tight range, price contained -- the coiled-spring inverse of
              IGNITION.

  BALANCED    Everything else, including every bar where RVOL20 is not yet
              defined (insufficient trailing history) -- the state that
              carries no directional claim.

timing_score in [0, 100] -- three additive, independently capped components
(documented so a later phase can recompute or re-weight it):
  volume component   (0-40): min(RVOL20 / 3.0, 1.0) * 40           -- 0 if RVOL20 undefined
  location component (0-30): min(|va_position - 0.5| * 2, 2) / 2 * 30
                              (0 at value-area centre, 30 at >=1 full
                              value-area-width beyond VAH/VAL)
  activity component (0/15/30): 30 if initiative, 15 if inside value
                              (neutral), 0 if responsive (a fade subtracts
                              conviction, it does not add it)

    python vanguard/features/m5_timing.py --symbols SAIL,MCX,GLENMARK --lookback-days 90
    python vanguard/features/m5_timing.py --lookback-days 45     # full sector_taxonomy universe
"""
from __future__ import annotations

# Filename entry points must resolve sibling packages just like imported modules.
import sys
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

DEFAULT_DSN = ""

IST = "Asia/Kolkata"
N_BINS = 30
VALUE_AREA_FRACTION = 0.70
RVOL_WINDOW = 20
RANGE_WINDOW = 20
ATR_WINDOW = 14
MIN_RVOL_SESSIONS = 5
MIN_ATR_SESSIONS = 5

# NSE equity session, in start-of-bar terms: the first bar starts at 09:15
# and the last at 15:15. Bars are on a :15/:45 grid because the session opens
# at 09:15, not on the hour. See load_bars() for what leaked in without this.
SESSION_FIRST_BAR_MIN = 9 * 60 + 15
SESSION_LAST_BAR_MIN = 15 * 60 + 15
SESSION_GRID_MINUTES = (15, 45)

IGNITION_RVOL = 2.0
EXHAUST_RVOL = 1.5
COMPRESSION_RVOL = 0.7
COMPRESSION_RANGE = 0.7

SPOT_CHECKS = [("SAIL", date(2026, 8, 25)), ("MCX", date(2026, 8, 10)), ("GLENMARK", date(2026, 8, 24))]


# ---------------------------------------------------------------------------
# Pure functions (offline-testable, no DB)
# ---------------------------------------------------------------------------

def value_area_from_bars(lows: list[float], highs: list[float], n_bins: int = N_BINS,
                          fraction: float = VALUE_AREA_FRACTION) -> tuple[float, float, float]:
    """Developing TPO-style value area/POC from a session's bars-so-far.

    Each bar contributes one unit of "presence" (one bracket-touch, the
    OHLC-bar analogue of a TPO letter) to every price bin its [low, high]
    range covers. Returns (va_low, va_high, poc). Degenerates gracefully to
    (lo, lo, lo) when every bar so far has zero range (e.g. a single
    print-only opening tick) -- callers must treat a zero-width value area
    as "not yet meaningful", not divide by it.
    """
    from model.shared_mp_cache import cached_json
    return tuple(cached_json("m5-fixed-bin-va-v1", [lows, highs, n_bins, fraction],
                             lambda: _value_area_from_bars(lows, highs, n_bins, fraction)))


def _value_area_from_bars(lows, highs, n_bins, fraction):
    lo, hi = min(lows), max(highs)
    if hi <= lo:
        return lo, lo, lo
    bin_width = (hi - lo) / n_bins
    counts = np.zeros(n_bins)
    for b_lo, b_hi in zip(lows, highs):
        i_lo = min(max(int((b_lo - lo) / bin_width), 0), n_bins - 1)
        i_hi = min(max(int((b_hi - lo) / bin_width), 0), n_bins - 1)
        counts[i_lo:i_hi + 1] += 1
    centre = (n_bins - 1) / 2
    poc_idx = max(range(n_bins), key=lambda i: (counts[i], -abs(i - centre)))
    total = counts.sum()
    target = total * fraction
    low_i = high_i = poc_idx
    included = counts[poc_idx]
    while included < target and (low_i > 0 or high_i < n_bins - 1):
        below = counts[low_i - 1] if low_i > 0 else -1
        above = counts[high_i + 1] if high_i < n_bins - 1 else -1
        if above >= below:
            high_i += 1
            included += counts[high_i]
        else:
            low_i -= 1
            included += counts[low_i]
    va_low = lo + low_i * bin_width
    va_high = lo + (high_i + 1) * bin_width
    poc = lo + (poc_idx + 0.5) * bin_width
    return va_low, va_high, poc


def va_position_of(close: float, va_low: float, va_high: float) -> float | None:
    """Unclipped (close-VAL)/(VAH-VAL). None only when the value area has zero width."""
    width = va_high - va_low
    if width <= 0:
        return None
    return (close - va_low) / width


def classify_beyond(close: float, va_low: float, va_high: float) -> str:
    if va_high <= va_low:
        return "inside"
    if close > va_high:
        return "above"
    if close < va_low:
        return "below"
    return "inside"


def initiative_or_responsive(beyond: str, signed_volume: float) -> tuple[bool, bool]:
    """(initiative, responsive). Both False when price is inside value."""
    if beyond == "inside":
        return False, False
    agrees = (beyond == "above" and signed_volume > 0) or (beyond == "below" and signed_volume < 0)
    return (True, False) if agrees else (False, True)


def opening_range_state(close: float, or_high: float, or_low: float,
                         atr14: float | None) -> tuple[str, float | None]:
    """(state, atr_multiple_beyond). state in {inside, above, below}."""
    if close > or_high:
        return "above", ((close - or_high) / atr14 if atr14 and atr14 > 0 else None)
    if close < or_low:
        return "below", ((close - or_low) / atr14 if atr14 and atr14 > 0 else None)
    return "inside", 0.0


def classify_timing(rvol: float | None, range_ratio: float | None, beyond: str,
                     initiative: bool, responsive: bool,
                     sector_direction: float | None) -> str:
    """First-match-wins over {IGNITION, EXHAUST, COMPRESSION, BALANCED}."""
    if rvol is not None and rvol >= IGNITION_RVOL and initiative and beyond != "inside":
        sector_agrees = (
            sector_direction is not None and sector_direction != 0
            and ((beyond == "above" and sector_direction > 0) or (beyond == "below" and sector_direction < 0))
        )
        if sector_agrees:
            return "IGNITION"
    if rvol is not None and rvol >= EXHAUST_RVOL and responsive:
        return "EXHAUST"
    if (rvol is not None and range_ratio is not None
            and rvol <= COMPRESSION_RVOL and range_ratio <= COMPRESSION_RANGE and beyond == "inside"):
        return "COMPRESSION"
    return "BALANCED"


def timing_score(rvol: float | None, va_position: float | None, initiative: bool, responsive: bool,
                  beyond: str) -> float:
    """`rvol`/`va_position` arrive as Python `None` from hand-written callers
    (this module's own unit tests) but as `numpy.nan` from the real pandas
    pipeline (`compute_timing()` calls this via `DataFrame.apply`, where an
    undefined value is always NaN, never None -- confirmed live: ~10.2% of
    stored rows had a NaN-poisoned timing_score before this fix, because
    `rvol is None` never fires on real NaN and the `else` branch computed
    `min(nan/3.0, 1.0) = nan`, which then poisoned the whole sum).
    `pd.isna()` catches both representations; it is the only correct guard.
    """
    volume_component = 0.0 if pd.isna(rvol) else min(rvol / 3.0, 1.0) * 40.0
    if pd.isna(va_position):
        location_component = 0.0
    else:
        extremity = min(abs(va_position - 0.5) * 2.0, 2.0)
        location_component = extremity / 2.0 * 30.0
    if initiative:
        activity_component = 30.0
    elif beyond == "inside":
        activity_component = 15.0
    else:  # responsive
        activity_component = 0.0
    return float(min(max(volume_component + location_component + activity_component, 0.0), 100.0))


# ---------------------------------------------------------------------------
# DB-facing pipeline
# ---------------------------------------------------------------------------

def load_bars(connection, symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """`underlying_spot_candles` genuinely carries duplicate (underlying,
    time, interval='30minute') rows from different `source` values for some
    instruments (e.g. ALUMINI has both 'fyers_mcx_cont' and
    'commodity_broker_history' rows for the same bar, with different OHLCV).
    Confirmed dormant for every symbol currently in sector_taxonomy today,
    but the pattern is real elsewhere in the table and a landmine the moment
    any Vanguard-universe symbol gains a second source feed -- an unfiltered
    SELECT would silently double a session's bar count and corrupt RVOL/VWAP.
    DISTINCT ON (symbol, time), preferring the higher-volume row (a real
    trade feed over a sparser/zero-volume duplicate), makes the result
    deterministic and defensive even though it changes nothing today.

    THE SESSION-GRID FILTER (added 2026-08-27)
    --------------------------------------------------------------------
    `underlying_spot_candles` also carries bars OUTSIDE the NSE equity
    session and, for some symbols, on a SECOND grid offset by 15 minutes
    (:00/:30 rather than the exchange's :15/:45). Both leaked straight into
    `timing`. Two consequences, both real and both silent:

      * RVOL20 and range_ratio bucket by time-of-day. An off-grid 10:00 bar
        and an on-grid 10:15 bar are different buckets, so a symbol carrying
        both had its trailing statistics computed from half as many
        observations as it appeared to have.
      * M6 evaluates `WHERE tm.ts = <one exact timestamp>`. With two grids in
        the table the evaluated universe swung between roughly 55 and 208
        symbols depending on which grid the latest bar happened to land on --
        a non-deterministic denominator underneath every skip-rate and
        coverage number the lane reports.

    Bars are start-of-bar labelled, so the session's own 13 bars start at
    09:15 through 15:15 (the last covering the 15:15-15:30 stub). Anything
    outside that window, or off the :15/:45 grid, is dropped here.

    The drop is COUNTED and returned on the frame's `.attrs` so callers can
    report it. A symbol that lives entirely on the other grid will now
    produce zero bars, and that must be loud rather than a quiet absence --
    __main__ prints the per-reason counts on every run.
    """
    query = """
        SELECT DISTINCT ON (underlying, time)
               time, underlying AS symbol, open, high, low, close, volume
        FROM underlying_spot_candles
        WHERE interval = '30minute' AND underlying = ANY(%(symbols)s)
          AND time >= %(start)s AND time < %(end)s
        ORDER BY underlying, time, volume DESC NULLS LAST
    """
    frame = pd.read_sql(query, connection, params={"symbols": symbols, "start": start, "end": end})
    if frame.empty:
        return frame
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    ist = frame["time"].dt.tz_convert(IST)
    frame["session_date"] = ist.dt.date
    frame["tod"] = ist.dt.strftime("%H:%M")

    raw_rows = len(frame)
    minutes = ist.dt.hour * 60 + ist.dt.minute
    in_session = (minutes >= SESSION_FIRST_BAR_MIN) & (minutes <= SESSION_LAST_BAR_MIN)
    on_grid = ist.dt.minute.isin(SESSION_GRID_MINUTES)
    dropped_off_hours = int((~in_session).sum())
    dropped_off_grid = int((in_session & ~on_grid).sum())
    frame = frame[in_session & on_grid].reset_index(drop=True)

    for col in ("open", "high", "low", "close"):
        frame[col] = frame[col].astype(float)
    frame["volume"] = frame["volume"].astype(float)
    frame.attrs["grid_filter"] = {
        "raw_rows": raw_rows,
        "kept": len(frame),
        "dropped_off_hours": dropped_off_hours,
        "dropped_off_grid": dropped_off_grid,
    }
    return frame


def load_sector_lookup(connection, symbols: list[str]) -> tuple[dict[str, str], pd.DataFrame]:
    taxonomy = pd.read_sql(
        "SELECT symbol, sector20 FROM sector_taxonomy WHERE symbol = ANY(%(symbols)s)",
        connection, params={"symbols": symbols},
    )
    symbol_to_sector20 = dict(zip(taxonomy["symbol"], taxonomy["sector20"]))
    sector_rs = pd.read_sql("SELECT ts, sector20, rs_z20 FROM sector_rs ORDER BY sector20, ts", connection)
    if not sector_rs.empty:
        sector_rs["ts"] = pd.to_datetime(sector_rs["ts"], utc=True).dt.date
    return symbol_to_sector20, sector_rs


def sector_direction_as_of(sector_rs: pd.DataFrame, sector20: str | None, session_date: date) -> float | None:
    """Sign of the most recent rs_z20 with ts < session_date (never the in-progress day)."""
    if sector20 is None or sector_rs.empty:
        return None
    rows = sector_rs[(sector_rs["sector20"] == sector20) & (sector_rs["ts"] < session_date)]
    if rows.empty:
        return None
    value = rows.sort_values("ts").iloc[-1]["rs_z20"]
    if pd.isna(value):
        return None
    return float(np.sign(value))


def add_rolling_seasonal_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """RVOL20 (volume) and range_ratio (high-low) vs. the same time-of-day's
    trailing same-symbol sessions, shifted so a session never sees itself."""
    df = df.sort_values(["symbol", "tod", "session_date"]).copy()
    df["bar_range"] = df["high"] - df["low"]
    grouped = df.groupby(["symbol", "tod"], sort=False)
    trailing_vol_mean = grouped["volume"].transform(
        lambda s: s.shift(1).rolling(RVOL_WINDOW, min_periods=MIN_RVOL_SESSIONS).mean())
    trailing_range_mean = grouped["bar_range"].transform(
        lambda s: s.shift(1).rolling(RANGE_WINDOW, min_periods=MIN_RVOL_SESSIONS).mean())
    df["rvol"] = df["volume"] / trailing_vol_mean
    df["range_ratio"] = df["bar_range"] / trailing_range_mean
    df.loc[trailing_vol_mean.isna() | (trailing_vol_mean <= 0), "rvol"] = np.nan
    df.loc[trailing_range_mean.isna() | (trailing_range_mean <= 0), "range_ratio"] = np.nan
    return df.sort_values(["symbol", "time"])


def add_daily_atr(df: pd.DataFrame) -> pd.DataFrame:
    """ATR(14) per symbol, aggregated to one value per session, using only
    the 14 sessions strictly BEFORE that session (no look-ahead)."""
    daily = (df.groupby(["symbol", "session_date"])
               .agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
               .reset_index().sort_values(["symbol", "session_date"]))
    prev_close = daily.groupby("symbol")["c"].shift(1)
    tr = pd.concat([
        daily["h"] - daily["l"],
        (daily["h"] - prev_close).abs(),
        (daily["l"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    daily["atr14"] = (tr.groupby(daily["symbol"])
                         .transform(lambda s: s.shift(1).rolling(ATR_WINDOW, min_periods=MIN_ATR_SESSIONS).mean()))
    return df.merge(daily[["symbol", "session_date", "atr14"]], on=["symbol", "session_date"], how="left")


def compute_session_developing(session_bars: pd.DataFrame) -> pd.DataFrame:
    """Walk one (symbol, session_date) group in time order, bar-by-bar,
    using only bars 1..i for every ingredient computed at bar i."""
    rows = []
    lows: list[float] = []
    highs: list[float] = []
    cum_pv = 0.0
    cum_vol = 0.0
    or_high = or_low = None
    for i, bar in enumerate(session_bars.itertuples(index=False)):
        lows.append(bar.low)
        highs.append(bar.high)
        typical = (bar.high + bar.low + bar.close) / 3.0
        cum_pv += typical * bar.volume
        cum_vol += bar.volume
        vwap_to_date = cum_pv / cum_vol if cum_vol > 0 else bar.close

        va_low, va_high, poc = value_area_from_bars(lows, highs)
        va_position = va_position_of(bar.close, va_low, va_high)
        beyond = classify_beyond(bar.close, va_low, va_high)
        signed_volume = bar.volume if bar.close > vwap_to_date else (
            -bar.volume if bar.close < vwap_to_date else 0.0)
        initiative, responsive = initiative_or_responsive(beyond, signed_volume)

        if i == 0:
            or_high, or_low = bar.high, bar.low
            or_state, or_atr_mult = "forming", None
        else:
            or_state, or_atr_mult = opening_range_state(bar.close, or_high, or_low, bar.atr14)

        rows.append({
            "time": bar.time, "symbol": bar.symbol, "session_date": bar.session_date, "tod": bar.tod,
            "close": bar.close, "rvol": bar.rvol, "range_ratio": bar.range_ratio,
            "va_low": va_low, "va_high": va_high, "poc": poc, "va_position": va_position,
            "beyond": beyond, "initiative": initiative, "responsive": responsive,
            "vwap_to_date": vwap_to_date, "or_state": or_state, "or_atr_mult": or_atr_mult,
        })
    return pd.DataFrame(rows)


def compute_timing(connection, symbols: list[str], start: date, end: date,
                    buffer_days: int = 60) -> pd.DataFrame:
    """Full pipeline: load bars (with a trailing buffer for RVOL/ATR history),
    compute seasonal ratios and ATR, walk each session developing, join
    sector direction, classify, and return only rows in [start, end)."""
    buffered_start = start - timedelta(days=buffer_days)
    df = load_bars(connection, symbols, buffered_start, end)
    grid_filter = df.attrs.get("grid_filter") if hasattr(df, "attrs") else None
    if df.empty:
        # An empty frame AFTER the session-grid filter is a different fact from
        # an empty query result, and the caller must be able to tell them apart
        # (e.g. every bar for this universe living on the wrong grid). Carry the
        # counts through instead of returning a bare empty frame.
        if grid_filter:
            df.attrs["grid_filter"] = grid_filter
        return df
    df = add_rolling_seasonal_ratios(df)
    df = add_daily_atr(df)

    symbol_to_sector20, sector_rs = load_sector_lookup(connection, symbols)

    # The buffer exists only for the trailing RVOL/range/ATR windows computed
    # above. Each session's developing walk reads only that session's bars, and
    # the classify/score/sector legs are per-row, so everything below runs on the
    # output window alone. Run over the whole 60-day buffer (122k rows) and
    # filtered at the end, this was ~105s of an ~118s live pass (profiled
    # 2026-09-15: 86s in the row-wise sector_direction apply, 20s in the walk).
    df = df[(df["session_date"] >= start) & (df["session_date"] < end)]
    developed = []
    for (_symbol, _session_date), group in df.groupby(["symbol", "session_date"], sort=True):
        developed.append(compute_session_developing(group.sort_values("time")))
    out = pd.concat(developed, ignore_index=True) if developed else pd.DataFrame()
    if out.empty:
        if grid_filter:
            out.attrs["grid_filter"] = grid_filter
        return out

    out["sector20"] = out["symbol"].map(symbol_to_sector20)
    out["sector_direction"] = out.apply(
        lambda r: sector_direction_as_of(sector_rs, r["sector20"], r["session_date"]), axis=1)

    out["timing_state"] = out.apply(
        lambda r: classify_timing(r["rvol"], r["range_ratio"], r["beyond"], r["initiative"], r["responsive"],
                                   r["sector_direction"]),
        axis=1)
    out["timing_score"] = out.apply(
        lambda r: timing_score(r["rvol"], r["va_position"], r["initiative"], r["responsive"], r["beyond"]),
        axis=1)
    if grid_filter:
        out.attrs["grid_filter"] = grid_filter

    out = out[(out["session_date"] >= start) & (out["session_date"] < end)]
    return out.sort_values(["symbol", "time"]).reset_index(drop=True)


def upsert_timing(connection, rows: pd.DataFrame) -> int:
    payload = [
        (row.time.to_pydatetime(), row.symbol, row.timing_state,
         None if pd.isna(row.timing_score) else float(row.timing_score),
         None if pd.isna(row.rvol) else float(row.rvol),
         None if pd.isna(row.va_position) else float(row.va_position))
        for row in rows.itertuples(index=False)
    ]
    if not payload:
        return 0
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO timing (ts, symbol, timing_state, timing_score, rvol, va_position) VALUES %s
               ON CONFLICT (symbol, ts) DO UPDATE SET
                 timing_state = EXCLUDED.timing_state, timing_score = EXCLUDED.timing_score,
                 rvol = EXCLUDED.rvol, va_position = EXCLUDED.va_position""",
            payload,
        )
    return len(payload)


def run_spot_check(result: pd.DataFrame, symbol: str, session_date: date) -> str:
    session = result[(result["symbol"] == symbol) & (result["session_date"] == session_date)].sort_values("time")
    if session.empty:
        return f"{symbol} {session_date}: NO BARS FOUND for this session in the queried window"
    ignitions = session[session["timing_state"] == "IGNITION"]
    lines = [f"{symbol} {session_date}: {len(session)} bars, "
             f"timing_state sequence = {list(session['timing_state'])}"]
    if ignitions.empty:
        lines.append(f"  IGNITION did NOT fire this session (spec's acceptance claim NOT confirmed)")
    else:
        first = ignitions.iloc[0]
        bar_index = session.index.get_loc(first.name)
        lines.append(
            f"  IGNITION first fires at bar #{bar_index} ({first['tod']} IST, {first['time']}): "
            f"rvol={first['rvol']:.2f} va_position={first['va_position']:.3f} "
            f"beyond={first['beyond']} sector_direction={first['sector_direction']} "
            f"score={first['timing_score']:.1f}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="", help="comma-separated; default = full sector_taxonomy universe")
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--buffer-days", type=int, default=60,
                         help="extra calendar days fetched before --lookback-days for RVOL20/ATR14 warm-up")
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    parser.add_argument("--write", action="store_true", help="upsert into the `timing` table")
    parser.add_argument("--no-spot-check", action="store_true")
    args = parser.parse_args()

    connection = psycopg2.connect(args.dsn)
    connection.autocommit = True

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        with connection.cursor() as cursor:
            cursor.execute("SELECT symbol FROM sector_taxonomy ORDER BY symbol")
            symbols = [r[0] for r in cursor.fetchall()]
    # Spot-check symbols must be present regardless of the requested universe.
    for symbol, _ in SPOT_CHECKS:
        if symbol not in symbols:
            symbols.append(symbol)

    end = date.today() + timedelta(days=1)
    start = date.today() - timedelta(days=args.lookback_days)
    if not args.no_spot_check:
        earliest_check = min(d for _, d in SPOT_CHECKS)
        start = min(start, earliest_check)

    print(f"symbols: {len(symbols)}  window: {start} .. {end}  buffer: {args.buffer_days}d")
    result = compute_timing(connection, symbols, start, end, buffer_days=args.buffer_days)
    if result.empty:
        print("no bars found for this symbol/date window -- nothing computed")
        connection.close()
        return 1

    grid = result.attrs.get("grid_filter")
    if grid:
        print(f"\nsession-grid filter: kept {grid['kept']:,} of {grid['raw_rows']:,} raw 30m rows "
              f"(dropped {grid['dropped_off_hours']:,} outside 09:15-15:15 IST, "
              f"{grid['dropped_off_grid']:,} off the :15/:45 grid)")
        if grid["kept"] == 0:
            print("  WARNING: every row was filtered out. If this universe's bars genuinely")
            print("  live on another grid, that is a data question, not a threshold to relax.")

    print(f"\ntiming rows computed: {len(result)}")
    print(f"date range: {result['session_date'].min()} .. {result['session_date'].max()}")
    print(f"symbols covered: {result['symbol'].nunique()} of {len(symbols)} requested")
    print("\ntiming_state distribution:")
    print(result["timing_state"].value_counts().to_string())

    if args.write:
        written = upsert_timing(connection, result)
        print(f"\nwrote {written} rows to timing")

    if not args.no_spot_check:
        print("\n--- spot checks (spec acceptance note) ---")
        for symbol, session_date in SPOT_CHECKS:
            print(run_spot_check(result, symbol, session_date))

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
