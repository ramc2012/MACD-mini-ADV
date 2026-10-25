"""Market-wide sentiment: participant positioning, PCR, volatility, breadth.

MARKET-WIDE, AND ONLY MARKET-WIDE
--------------------------------------------------------------------------
NSE's participant-wise OI file is an AGGREGATE: FII / DII / Pro / Client by
instrument class (index futures, stock futures, index calls, index puts,
stock calls, stock puts). It has no per-symbol dimension and never has. So
FII positioning can inform a market regime and can never be attributed to a
name, and this module produces exactly one row per session rather than
pretending otherwise. Anything that claims "FII buying in RELIANCE" from this
file is inventing detail the exchange does not publish.

THE FIVE FAMILIES, AND WHY EACH ONE IS HERE
--------------------------------------------------------------------------
  positioning   FII/DII/Client/Pro nets and the SESSION CHANGE in them. The
                change matters more than the level: a persistent structural
                short reads as permanent bearishness if you only look at the
                stock of positions.
  options       market-wide OI and volume put/call ratios, aggregated from
                live contract OI rather than read from
                `fo_option_chain_metrics`, whose own aggregate stopped on
                2026-08-03 while its inputs kept arriving.
  volatility    NIFTY front-series ATM IV and the median across stocks, both
                from Vanguard's own computed surface -- the broker's IV died
                on 2026-07-28 and none of this would exist otherwise.
  breadth       advances/declines and share above the 20-day mean. Sentiment
                that comes out of prices instead of out of positioning, so
                the two can disagree and be seen to disagree.
  positioning conjunction
                the long-buildup / short-buildup rollup across the universe.

THE COMPOSITE, AND ITS LIMITS
--------------------------------------------------------------------------
`sentiment_score` is a -100..+100 blend, stored ONLY alongside the
components that produced it. It is a summary for reading, not a validated
signal: no cross-sectional IC study backs it, the participant series is five
sessions long at the time of writing, and several components are
contemporaneous rather than predictive. Treated as a dashboard number it is
useful; treated as an edge it is unvalidated, and the UI says so.

    python vanguard/features/m_sentiment.py --lookback-days 120 --write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

DEFAULT_DSN = ""

INDEX_SYMBOL = "NIFTY"
# Weights for the composite. Positioning gets the most because it is the only
# family here that is a genuine flow rather than a restatement of price.
WEIGHTS = {
    "positioning": 0.30,
    "options": 0.20,
    "breadth": 0.25,
    "volatility": 0.10,
    "oi_conjunction": 0.15,
}
# Renormalising the weights over whatever families exist is correct arithmetic
# and, on its own, a trap: with one family present that family IS the score, so
# a single extreme reading becomes a full-scale composite. Measured on the
# first run — 2026-08-27 had only the options family (the NSE spot feed is an
# overnight batch, so the session had no price or IV yet) and the composite
# reported +100.0 off one input. This is the same failure as a flow_score of
# ±100 built from a single ingredient, and it gets the same gate: below this
# many families there is no composite, and the score is NULL.
MIN_FAMILIES = 3


def load_participants(connection, start: date, end: date) -> pd.DataFrame:
    return pd.read_sql(
        """SELECT dt, participant, bucket, long_contracts, short_contracts
           FROM participant_oi WHERE dt >= %(start)s AND dt <= %(end)s""",
        connection, params={"start": start, "end": end})


def load_oi(connection, start: date, end: date) -> pd.DataFrame:
    return pd.read_sql(
        """SELECT dt, symbol, ce_oi, pe_oi, oi_state, d_price_pct, close, ret_20d
           FROM oi_positioning WHERE dt >= %(start)s AND dt <= %(end)s""",
        connection, params={"start": start, "end": end})


def load_surface(connection, start: date, end: date) -> pd.DataFrame:
    return pd.read_sql(
        """SELECT dt, symbol, atm_iv, iv_percentile FROM iv_surface
           WHERE dt >= %(start)s AND dt <= %(end)s""",
        connection, params={"start": start, "end": end})


def load_volume_pcr(connection, start: date, end: date) -> pd.DataFrame:
    """Market-wide volume PCR from live contract volume."""
    return pd.read_sql(
        """SELECT dt,
                  sum(volume) FILTER (WHERE option_type = 'CE') AS ce_volume,
                  sum(volume) FILTER (WHERE option_type = 'PE') AS pe_volume
           FROM option_iv WHERE dt >= %(start)s AND dt <= %(end)s
           GROUP BY dt""",
        connection, params={"start": start, "end": end})


# --------------------------------------------------------------------------
# Pure assembly
# --------------------------------------------------------------------------
def _net(frame: pd.DataFrame, participant: str, buckets: list[str]) -> float | None:
    rows = frame[(frame["participant"] == participant) & (frame["bucket"].isin(buckets))]
    if rows.empty:
        return None
    return float(rows["long_contracts"].sum() - rows["short_contracts"].sum())


def positioning_for_session(frame: pd.DataFrame) -> dict:
    """Nets per participant. `fii_opt_index_net` is DIRECTIONAL, not a raw sum:
    long calls and SHORT puts are both bullish, so puts enter with the opposite
    sign. Summing the four legs blindly would net a bullish call position
    against a bullish put position and report neutrality."""
    out: dict = {}
    for key, participant, buckets in (
        ("fii_fut_index_net", "FII", ["fut_index"]),
        ("fii_fut_stock_net", "FII", ["fut_stock"]),
        ("dii_fut_index_net", "DII", ["fut_index"]),
        ("client_fut_index_net", "Client", ["fut_index"]),
        ("pro_fut_index_net", "Pro", ["fut_index"]),
    ):
        out[key] = _net(frame, participant, buckets)

    for key, participant in (("fii_opt_index_net", "FII"), ("client_opt_index_net", "Client")):
        calls = frame[(frame["participant"] == participant) & (frame["bucket"] == "opt_index_call")]
        puts = frame[(frame["participant"] == participant) & (frame["bucket"] == "opt_index_put")]
        if calls.empty or puts.empty:
            out[key] = None
            continue
        call_net = float(calls["long_contracts"].sum() - calls["short_contracts"].sum())
        put_net = float(puts["long_contracts"].sum() - puts["short_contracts"].sum())
        out[key] = call_net - put_net

    fut = frame[(frame["participant"] == "FII") & (frame["bucket"] == "fut_index")]
    if not fut.empty:
        total = float(fut["long_contracts"].sum() + fut["short_contracts"].sum())
        out["fii_index_long_ratio"] = (
            float(fut["long_contracts"].sum()) / total if total else None)
    else:
        out["fii_index_long_ratio"] = None
    return out


def _clip_score(value: float | None, scale: float) -> float | None:
    """Map a raw reading onto -100..+100 via a scale that means 'a big move'."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return float(np.clip(value / scale, -1.0, 1.0) * 100.0)


def build(participants: pd.DataFrame, oi: pd.DataFrame, surface: pd.DataFrame,
          volume_pcr: pd.DataFrame) -> pd.DataFrame:
    # An empty source frame can arrive with no columns at all (a query that
    # matched nothing returns an empty result set, not an empty typed frame),
    # so the session union cannot assume `dt` exists. A feed being absent is a
    # normal state here — participant_oi began only in August — and must not
    # take the whole build down with a KeyError.
    def _dates(frame: pd.DataFrame) -> set:
        return set(frame["dt"]) if ("dt" in frame.columns and not frame.empty) else set()

    sessions = sorted(_dates(oi) | _dates(participants) | _dates(surface))
    if not sessions:
        return pd.DataFrame()
    for frame in (oi, participants, surface):
        if "dt" not in frame.columns:
            frame["dt"] = pd.Series(dtype="object")

    volume_map = {r.dt: r for r in volume_pcr.itertuples()} if not volume_pcr.empty else {}
    rows = []
    for dt in sessions:
        row: dict = {"dt": dt}
        row.update(positioning_for_session(participants[participants["dt"] == dt]))

        day_oi = oi[oi["dt"] == dt]
        ce = day_oi["ce_oi"].sum(skipna=True)
        pe = day_oi["pe_oi"].sum(skipna=True)
        row["market_oi_pcr"] = float(pe / ce) if ce and ce > 0 else None

        vol_row = volume_map.get(dt)
        if vol_row is not None and vol_row.ce_volume:
            row["market_volume_pcr"] = float(vol_row.pe_volume or 0) / float(vol_row.ce_volume)
        else:
            row["market_volume_pcr"] = None

        day_surface = surface[surface["dt"] == dt]
        index_rows = day_surface[day_surface["symbol"] == INDEX_SYMBOL]["atm_iv"].dropna()
        row["index_atm_iv"] = float(index_rows.iloc[0]) if not index_rows.empty else None
        stock_iv = day_surface[day_surface["symbol"] != INDEX_SYMBOL]["atm_iv"].dropna()
        row["median_stock_iv"] = float(stock_iv.median()) if not stock_iv.empty else None
        pct = day_surface["iv_percentile"].dropna()
        row["iv_percentile"] = float(pct.median()) if not pct.empty else None

        moves = day_oi["d_price_pct"].dropna()
        row["advances"] = int((moves > 0).sum()) if not moves.empty else None
        row["declines"] = int((moves < 0).sum()) if not moves.empty else None
        row["advance_decline_ratio"] = (
            float(row["advances"] / row["declines"])
            if row["advances"] is not None and row["declines"] else None)
        row["median_ret_1d"] = float(moves.median()) if not moves.empty else None
        ret20 = day_oi["ret_20d"].dropna()
        row["pct_above_20d"] = float((ret20 > 0).mean() * 100.0) if not ret20.empty else None

        states = day_oi["oi_state"].dropna()
        row["long_buildup_count"] = int((states == "long_buildup").sum())
        row["short_buildup_count"] = int((states == "short_buildup").sum())
        row["short_covering_count"] = int((states == "short_covering").sum())
        row["long_unwind_count"] = int((states == "long_unwind").sum())
        classified = len(states)
        row["net_oi_bias"] = (
            float((row["long_buildup_count"] - row["short_buildup_count"]) / classified)
            if classified else None)
        rows.append(row)

    frame = pd.DataFrame(rows).sort_values("dt").reset_index(drop=True)
    # SESSION CHANGES SKIP GAPS. Weekends and holidays land in this frame as
    # rows with nothing in them (participant_oi and the chain both publish on
    # some non-trading days), so a plain .diff() differences against an empty
    # Saturday and returns NaN for the Monday. Measured: 2026-08-24 had a real
    # FII net, a real PCR and a real NIFTY IV and still reported only two
    # contributing families, because all three of its CHANGE columns had been
    # differenced against a weekend. Each series is differenced over the
    # sessions where it actually exists.
    for column in ("fii_fut_index_net", "market_oi_pcr", "index_atm_iv"):
        present = frame[column].notna()
        frame[f"d_{column}"] = frame.loc[present, column].diff().reindex(frame.index)
    return _score(frame)


def _score(frame: pd.DataFrame) -> pd.DataFrame:
    """Blend the families into -100..+100, renormalising over what exists.

    A missing family is dropped from the weighted sum and the remaining
    weights are renormalised -- it never contributes a silent zero, which
    would drag the composite toward neutral and read as a measured 'balanced'
    rather than as an absent input.
    """
    components: list[dict] = []
    scores: list[float | None] = []
    for row in frame.itertuples():
        parts: dict[str, float] = {}

        # Positioning: the CHANGE in FII index futures, not the level.
        change = getattr(row, "d_fii_fut_index_net", None)
        value = _clip_score(change, 50_000.0)
        if value is not None:
            parts["positioning"] = value

        # Options: a RISING put/call ratio is defensive. Sign is inverted so
        # positive always means bullish, everywhere in this table.
        d_pcr = getattr(row, "d_market_oi_pcr", None)
        value = _clip_score(-d_pcr if d_pcr is not None and not pd.isna(d_pcr) else None, 0.15)
        if value is not None:
            parts["options"] = value

        if row.median_ret_1d is not None and not pd.isna(row.median_ret_1d):
            parts["breadth"] = _clip_score(row.median_ret_1d, 1.5)

        # Volatility: rising IV is risk-off, so the sign is inverted again.
        d_iv = getattr(row, "d_index_atm_iv", None)
        value = _clip_score(-d_iv if d_iv is not None and not pd.isna(d_iv) else None, 0.03)
        if value is not None:
            parts["volatility"] = value

        if row.net_oi_bias is not None and not pd.isna(row.net_oi_bias):
            parts["oi_conjunction"] = _clip_score(row.net_oi_bias, 0.4)

        parts = {k: v for k, v in parts.items() if v is not None}
        if len(parts) >= MIN_FAMILIES:
            total_weight = sum(WEIGHTS[k] for k in parts)
            scores.append(sum(WEIGHTS[k] * v for k, v in parts.items()) / total_weight)
        else:
            scores.append(None)
        components.append({
            # The parts are recorded even when no score is: they are the
            # readings themselves, and suppressing them because the composite
            # could not be formed would throw away measurements that are fine.
            "parts": {k: round(v, 2) for k, v in parts.items()},
            "weights_used": {k: round(WEIGHTS[k] / sum(WEIGHTS[m] for m in parts), 4)
                             for k in parts} if len(parts) >= MIN_FAMILIES else {},
            "n_families": len(parts),
            "min_families": MIN_FAMILIES,
            "suppressed": len(parts) < MIN_FAMILIES,
        })
    frame = frame.copy()
    frame["sentiment_score"] = scores
    frame["sentiment_components"] = components
    return frame


COLUMNS = ("dt", "fii_fut_index_net", "fii_fut_stock_net", "fii_opt_index_net",
           "fii_index_long_ratio", "dii_fut_index_net", "client_fut_index_net",
           "client_opt_index_net", "pro_fut_index_net", "d_fii_fut_index_net",
           "market_oi_pcr", "market_volume_pcr", "d_market_oi_pcr",
           "index_atm_iv", "d_index_atm_iv", "median_stock_iv", "iv_percentile",
           "advances", "declines", "advance_decline_ratio", "pct_above_20d",
           "median_ret_1d", "long_buildup_count", "short_buildup_count",
           "short_covering_count", "long_unwind_count", "net_oi_bias",
           "sentiment_score", "sentiment_components")
BIGINT_COLUMNS = {"fii_fut_index_net", "fii_fut_stock_net", "fii_opt_index_net",
                  "dii_fut_index_net", "client_fut_index_net", "client_opt_index_net",
                  "pro_fut_index_net", "d_fii_fut_index_net"}
INT_COLUMNS = {"advances", "declines", "long_buildup_count", "short_buildup_count",
               "short_covering_count", "long_unwind_count"}


def _cell(value, column):
    if column == "dt":
        return value
    if column == "sentiment_components":
        return psycopg2.extras.Json(value)
    if value is None or pd.isna(value):
        return None
    if column in BIGINT_COLUMNS or column in INT_COLUMNS:
        return int(value)
    return float(value)


def upsert(connection, frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    rows = [tuple(_cell(row[c], c) for c in COLUMNS) for _, row in frame.iterrows()]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "dt")
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            f"""INSERT INTO market_sentiment ({", ".join(COLUMNS)}) VALUES %s
                ON CONFLICT (dt) DO UPDATE SET {updates}, computed_at = now()""",
            rows,
        )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()

    connection = psycopg2.connect(args.dsn)
    connection.autocommit = True
    try:
        end = date.today()
        start = end - timedelta(days=args.lookback_days)
        frame = build(load_participants(connection, start, end),
                      load_oi(connection, start, end),
                      load_surface(connection, start, end),
                      load_volume_pcr(connection, start, end))
        if frame.empty:
            print("no sessions assembled")
            return 1

        print(f"sessions: {len(frame)}  {frame['dt'].min()} .. {frame['dt'].max()}")
        latest = frame.iloc[-1]
        print(f"\nlatest session {latest['dt']}:")
        meta = latest["sentiment_components"]
        if latest["sentiment_score"] is not None and not pd.isna(latest["sentiment_score"]):
            print(f"  sentiment score      {latest['sentiment_score']:+.1f}")
        else:
            print(f"  sentiment score      — (only {meta['n_families']} of 5 families "
                  f"present, minimum {meta['min_families']})")
        print(f"  families contributing {meta['n_families']}/5 {meta['parts']}")
        for label, key, fmt in (
            ("FII index futures net", "fii_fut_index_net", "{:,.0f}"),
            ("  session change", "d_fii_fut_index_net", "{:+,.0f}"),
            ("FII index long ratio", "fii_index_long_ratio", "{:.3f}"),
            ("Client index futures net", "client_fut_index_net", "{:,.0f}"),
            ("market OI PCR", "market_oi_pcr", "{:.3f}"),
            ("NIFTY ATM IV", "index_atm_iv", "{:.4f}"),
            ("median stock IV", "median_stock_iv", "{:.4f}"),
            ("advances / declines", "advance_decline_ratio", "{:.2f}"),
            ("% above 20d", "pct_above_20d", "{:.1f}"),
            ("net OI bias", "net_oi_bias", "{:+.3f}"),
        ):
            value = latest[key]
            print(f"  {label:<24} " + (fmt.format(value) if value is not None and not pd.isna(value) else "—"))

        if args.write:
            print(f"\nwrote {upsert(connection, frame)} rows to market_sentiment")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
