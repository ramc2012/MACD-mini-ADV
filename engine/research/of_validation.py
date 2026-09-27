#!/usr/bin/env python3
"""Does inferred order flow predict anything, and what is "normal" for a symbol?

Two jobs, one pass over ``tick_minute_flow``:

  (a) VALIDATION.  Rank-correlate per-minute order-flow features against the
      NEXT bar's return, pooled and per symbol, split by classification
      quality.  Every figure carries its sample size and its standard error,
      because two sessions is a small sample and an information coefficient
      inside its own error bar is not a signal.

  (b) NORMALISATION.  Raw delta is not comparable between NIFTY futures and a
      Rs 2 option.  This writes, per symbol, the location/scale statistics the
      desk needs to turn any raw reading into a symbol-relative score, plus
      the sample size behind each statistic so a thin symbol can be told apart
      from a well-measured one.

THE STANDING CAVEAT.  NSE publishes price, quantity, time and the best
bid/ask.  It never publishes who was the aggressor.  Every buy_volume,
sell_volume and delta read here is an INFERENCE (Lee-Ready: quote rule first,
tick test as fallback).  Nothing here may be reported as a measured number;
the quality columns exist so the reader can weight the estimate.

THE HISTORY CAVEAT.  Rows written before the 2026-08-27 classifier fix came
from the OLD estimator: its tick test was fed quote/OI ticks (its own echo),
there was no zero-tick carry-forward, and unclassified volume was DELETED
rather than carried in a residual.  Baseline rows are stamped with the
estimator that produced their source bars, so a baseline built from legacy
bars is not mistaken for one built from the fixed classifier.

WHY THREE TARGETS.  A one-minute close-to-close return shares its anchor,
close(t), with the bar the feature was measured on.  If that close printed at
the bid, the next return is mechanically biased upward -- bid-ask bounce.  Any
feature correlated with "the last print was at the bid" (which is exactly what
a negative delta IS) then earns a spurious negative IC that has nothing to do
with prediction.  So every feature is scored against three targets:

    close  close(t+h)/close(t) - 1      standard, and bounce-contaminated
    oc     close(t+h)/open(t+1) - 1     TRADEABLE: enter at the next bar's
                                        open, so close(t) is not in the return
                                        at all and the bounce cannot leak in
    vwap   vwap(t+h)/vwap(t) - 1        bounce-damped by averaging

A feature that only works on ``close`` is a microstructure artefact.  The
column that decides anything is ``oc``.

WHERE IT RUNS.  ``ticks.sqlite3`` lives on a Docker Desktop bind mount, and
host reads of it return stale or torn pages while the container's writer is
live.  So this script, when started on the host, pipes its own source into the
container and re-runs itself there.  Nothing else is asked of the caller:

    python research/of_validation.py                     # measure, no writes
    python research/of_validation.py --write-baselines   # + refresh baselines
    python research/of_validation.py --json out.json     # machine-readable

Re-run it daily once the tick store has condensed the session (condensation is
what creates the minute rows).  ``--days`` defaults to every day carrying a
usable number of bars, so the daily refresh needs no argument.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")
# The host may run an older interpreter than the container, so this file is
# deliberately stdlib-only and 3.9-compatible: the bootstrap must always start,
# whatever python the caller happens to have.

DEFAULT_CONTAINER = "macdmini-api-1"
DEFAULT_DB = "/app/runtime/ticks.sqlite3"

# The day the classifier was fixed (zero-tick carry-forward, a real
# last_trade_price, volume conservation).  Source bars older than this were
# written by the previous estimator and are labelled as such.
CLASSIFIER_FIX_DAY = "2026-08-27"
LEGACY_ESTIMATOR = "legacy_pre_2026-08-27"
CURRENT_ESTIMATOR = "quote_rule_zero_tick"

# A day with fewer than this many minute rows is a condensation artefact (a
# handful of stray ticks), not a session.
MIN_DAY_ROWS = 5_000
# Below this many usable (bar, next-bar) pairs a per-series rank correlation is
# noise; such series are counted but excluded from the IC distribution.
MIN_SERIES_PAIRS = 60
# Expanding statistics need a floor before they mean anything.
MIN_PRIOR_BARS = 20
# Baseline statistic floors.  Below these the statistic is published as NULL,
# never as a zero or a default that would read as a measurement.
MIN_BARS_FOR_MEDIAN = 3
MIN_BARS_FOR_IQR = 8
MIN_BARS_FOR_SD = 3

BASELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS of_symbol_baseline (
    symbol            TEXT    NOT NULL,
    as_of             TEXT    NOT NULL,   -- IST date of the last session used
    symbol_id         INTEGER NOT NULL,
    instrument        TEXT    NOT NULL,   -- EQ | FUT | OPT | INDEX | OTHER
    sessions          INTEGER NOT NULL,   -- sessions behind this row
    bars              INTEGER NOT NULL,   -- ACTIVE minute bars behind this row
    -- Location / scale of the raw readings the desk sees, so any of them can
    -- be expressed as a symbol-relative score.  NULL where unmeasured.
    volume_median     REAL, volume_p25 REAL, volume_p75 REAL,
    abs_delta_median  REAL, abs_delta_p25 REAL, abs_delta_p75 REAL,
    nd_median         REAL, nd_p25 REAL, nd_p75 REAL,   -- delta / volume
    trades_median     REAL, trades_p25 REAL, trades_p75 REAL,
    delta_mean        REAL, delta_sd REAL,
    ret_sd            REAL,                             -- per-minute close/close
    spread_median     REAL, spread_bps_median REAL,
    price_median      REAL,
    -- How much of the tape the classifier could actually place.  These are the
    -- weights the desk should apply to anything derived from delta.
    quote_share       REAL,   -- quote-rule prints / prints
    classified_share  REAL,   -- (buy+sell) volume / volume
    unclassified_trade_share REAL,
    estimator         TEXT    NOT NULL,   -- classifier that wrote the source bars
    updated_at        TEXT    NOT NULL,
    PRIMARY KEY (symbol, as_of)
);
CREATE INDEX IF NOT EXISTS idx_of_symbol_baseline_asof
    ON of_symbol_baseline(as_of);
CREATE VIEW IF NOT EXISTS of_symbol_baseline_latest AS
    SELECT b.* FROM of_symbol_baseline b
    JOIN (SELECT symbol, MAX(as_of) AS as_of
            FROM of_symbol_baseline GROUP BY symbol) m
      ON m.symbol = b.symbol AND m.as_of = b.as_of;
"""


# --------------------------------------------------------------------------
# small statistics, stdlib only (the container carries no numpy)
# --------------------------------------------------------------------------

def percentile(sorted_values, q):
    """Linear-interpolated percentile of an already-sorted list."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_values[0])
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def median(sorted_values):
    return percentile(sorted_values, 0.5)


def mean_sd(values):
    n = len(values)
    if n == 0:
        return None, None
    mu = sum(values) / n
    if n < 2:
        return mu, None
    var = sum((v - mu) ** 2 for v in values) / (n - 1)
    return mu, math.sqrt(var)


def mean_se_t(values):
    """(mean, sd, se, t) treating each entry as one independent observation."""
    mu, sd = mean_sd(values)
    if mu is None:
        return None, None, None, None
    if not sd or sd <= 0 or len(values) < 2:
        return mu, sd, None, None
    se = sd / math.sqrt(len(values))
    return mu, sd, se, mu / se


def ranks(values):
    """Average ranks, ties shared."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for x, y in zip(xs, ys):
        dx = x - mx
        dy = y - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    return pearson(ranks(xs), ranks(ys))


def partial_spearman(xs, ys, zs):
    """Rank correlation of x with y once z is taken out of both.

    Used to ask whether a flow feature says anything the previous bar's return
    does not already say.
    """
    rxy = spearman(xs, ys)
    rxz = spearman(xs, zs)
    rzy = spearman(zs, ys)
    if rxy is None or rxz is None or rzy is None:
        return None
    denom = (1 - rxz ** 2) * (1 - rzy ** 2)
    if denom <= 1e-12:
        return None
    return (rxy - rxz * rzy) / math.sqrt(denom)


def uniform_ranks(values):
    """Ranks mapped into (0, 1) so series of different lengths pool cleanly."""
    n = len(values)
    return [(v - 0.5) / n for v in ranks(values)]


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def ist_day(minute_ts):
    return datetime.fromtimestamp(minute_ts, IST).strftime("%Y-%m-%d")


def instrument_class(symbol):
    tail = symbol.split(":")[-1]
    if tail.endswith("-EQ"):
        return "EQ"
    if tail.endswith("-INDEX"):
        return "INDEX"
    if tail.endswith("FUT"):
        return "FUT"
    if tail.endswith("CE") or tail.endswith("PE"):
        return "OPT"
    return "OTHER"


def usable_days(conn):
    counts = {}
    for (ts,) in conn.execute("SELECT minute_ts FROM tick_minute_flow"):
        d = ist_day(ts)
        counts[d] = counts.get(d, 0) + 1
    return sorted(d for d, n in counts.items() if n >= MIN_DAY_ROWS)


def load_series(conn, days):
    """-> {(symbol, day): [bar, ...]}, each list ordered by minute."""
    wanted = set(days)
    symbols = dict(conn.execute("SELECT id, symbol FROM tick_symbols"))
    series = {}
    sql = """SELECT symbol_id, minute_ts, open, close, vwap, volume,
                    buy_volume, sell_volume, delta, trades,
                    quote_n, mid_n, tick_n, unclassified, avg_spread
               FROM tick_minute_flow ORDER BY symbol_id, minute_ts"""
    for row in conn.execute(sql):
        (sid, ts, op, close, vwap, volume, buy, sell, delta, trades,
         quote_n, mid_n, tick_n, unclassified, spread) = row
        day = ist_day(ts)
        if day not in wanted:
            continue
        sym = symbols.get(sid)
        if sym is None:
            continue
        series.setdefault((sym, day), []).append({
            "sid": sid, "ts": ts, "open": op, "close": close, "vwap": vwap,
            "volume": volume, "buy": buy, "sell": sell, "delta": delta,
            "trades": trades, "quote_n": quote_n, "mid_n": mid_n,
            "tick_n": tick_n, "unclassified": unclassified, "spread": spread,
        })
    return series


# --------------------------------------------------------------------------
# features and targets
# --------------------------------------------------------------------------
# mode "signed" -> the feature has a direction, score it against the signed
# return.  mode "abs" -> it has none, so it can only be asked about magnitude.
FEATURES = [
    ("delta",     "signed", "raw delta (inferred buy minus sell volume)"),
    ("nd",        "signed", "delta / bar volume"),
    ("buy_share", "signed", "buy/(buy+sell) centred at 0.5"),
    ("delta_z",   "signed", "delta vs this symbol's own prior-bar mean/sd"),
    ("persist3",  "signed", "mean sign of delta over the last 3 bars"),
    ("rvol",      "abs",    "volume / prior-bar median volume"),
    ("ret_prev",  "signed", "CONTROL: the previous bar's return, no flow at all"),
]

TARGETS = {
    "close": "close(t+h)/close(t)-1   [shares close(t) with the feature bar]",
    "oc":    "close(t+h)/open(t+1)-1  [TRADEABLE, bounce-free entry]",
    # vwap is bounce-damped but NOT clean: vwap(t) averages over bar t, so any
    # feature carrying bar t's drift (ret_prev above all) correlates with
    # vwap(t+h)/vwap(t) mechanically. Read it as a cross-check on `close`, not
    # as evidence on its own.
    "vwap":  "vwap(t+h)/vwap(t)-1     [bounce-damped, but drift-contaminated]",
}

CONTROL_FEATURE = "ret_prev"


def build_pairs(bars, horizon):
    """Causal features at bar t, targets from bar t+horizon minutes.

    Only ACTIVE bars take part -- a zero-volume minute has no flow to read and
    a stale close to predict.  The target bar must sit exactly ``horizon``
    minutes later, so a gap in the tape is never silently stretched into a
    longer holding period.
    """
    active = [b for b in bars
              if b["volume"] > 0 and b["trades"] > 0 and b["close"]]
    if len(active) < MIN_PRIOR_BARS + 2:
        return []
    by_ts = {b["ts"]: b for b in active}

    prior_deltas = []
    prior_volumes = []
    out = []

    for i, bar in enumerate(active):
        delta = float(bar["delta"])
        volume = float(bar["volume"])

        # expanding statistics over STRICTLY PRIOR bars in this session
        delta_z = None
        if len(prior_deltas) >= MIN_PRIOR_BARS:
            mu, sd = mean_sd(prior_deltas)
            if sd and sd > 0:
                delta_z = (delta - mu) / sd
        rvol = None
        if len(prior_volumes) >= MIN_PRIOR_BARS:
            med = median(sorted(prior_volumes))
            if med and med > 0:
                rvol = volume / med
        prior_deltas.append(delta)
        prior_volumes.append(volume)

        signs = []
        for k in range(3):
            if i - k >= 0:
                d = active[i - k]["delta"]
                signs.append(1.0 if d > 0 else (-1.0 if d < 0 else 0.0))
        persist3 = sum(signs) / 3.0 if i >= 2 else None

        classified = bar["buy"] + bar["sell"]
        prev = active[i - 1] if i > 0 else None
        prev_ret = None
        if prev is not None and prev["ts"] == bar["ts"] - 60 and prev["close"]:
            prev_ret = bar["close"] / prev["close"] - 1.0

        nxt1 = by_ts.get(bar["ts"] + 60)
        nxth = by_ts.get(bar["ts"] + 60 * horizon)
        if nxth is None or not nxth["close"]:
            continue

        fwd_oc = None
        if nxt1 is not None and nxt1["open"]:
            fwd_oc = nxth["close"] / nxt1["open"] - 1.0
        fwd_vwap = None
        if bar["vwap"] and nxth["vwap"]:
            fwd_vwap = nxth["vwap"] / bar["vwap"] - 1.0

        out.append({
            "ts": bar["ts"],
            "close": nxth["close"] / bar["close"] - 1.0,
            "oc": fwd_oc,
            "vwap": fwd_vwap,
            "delta": delta,
            "nd": delta / volume if volume > 0 else None,
            "buy_share": (bar["buy"] / classified - 0.5) if classified > 0 else None,
            "delta_z": delta_z,
            "persist3": persist3,
            "rvol": rvol,
            "ret_prev": prev_ret,
        })
    return out


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------

def evaluate(pairs_by_series, feature, target_mode, target_key):
    """IC pooled and per symbol, a sign hit rate, and a partial IC.

    The partial IC removes the previous bar's return from both sides, which
    answers the only question that matters once short-horizon reversal is on
    the table: does the flow feature say anything price alone does not?
    """
    pooled_x, pooled_y = [], []
    series_ic = []        # (symbol, ic, n)
    series_partial = []   # (symbol, partial ic)
    hits = decided = n_rows = thin_series = 0

    for (sym, _day), pairs in pairs_by_series.items():
        xs, ys, zs = [], [], []
        for p in pairs:
            v = p[feature]
            t = p[target_key]
            if v is None or t is None:
                continue
            y = abs(t) if target_mode == "abs" else t
            xs.append(float(v))
            ys.append(y)
            zs.append(p[CONTROL_FEATURE])
            if target_mode == "signed":
                sx = 1 if v > 0 else (-1 if v < 0 else 0)
                sy = 1 if y > 0 else (-1 if y < 0 else 0)
                if sx and sy:
                    decided += 1
                    hits += 1 if sx == sy else 0
        n_rows += len(xs)
        if len(xs) < MIN_SERIES_PAIRS:
            thin_series += 1
            continue
        ic = spearman(xs, ys)
        if ic is None:
            continue
        series_ic.append((sym, ic, len(xs)))
        pooled_x.extend(uniform_ranks(xs))
        pooled_y.extend(uniform_ranks(ys))

        if feature != CONTROL_FEATURE:
            cx = [x for x, z in zip(xs, zs) if z is not None]
            cy = [y for y, z in zip(ys, zs) if z is not None]
            cz = [z for z in zs if z is not None]
            if len(cz) >= MIN_SERIES_PAIRS:
                pic = partial_spearman(cx, cy, cz)
                if pic is not None:
                    series_partial.append((sym, pic))

    def by_symbol(pairs):
        acc = {}
        for sym, val in pairs:
            acc.setdefault(sym, []).append(val)
        return {s: sum(v) / len(v) for s, v in acc.items()}

    sym_ic_map = by_symbol([(s, ic) for s, ic, _n in series_ic])
    sym_ic = list(sym_ic_map.values())
    mu, sd, se, t_stat = mean_se_t(sym_ic)

    sym_partial = list(by_symbol(series_partial).values())
    pmu, _psd, pse, pt = mean_se_t(sym_partial)

    return {
        "feature": feature,
        "target": target_key,
        "pooled_ic": pearson(pooled_x, pooled_y) if pooled_x else None,
        "pooled_n": len(pooled_x),
        "pooled_se": 1.0 / math.sqrt(len(pooled_x)) if pooled_x else None,
        "series": len(series_ic),
        "series_thin": thin_series,
        "symbols": len(sym_ic),
        "symbol_ic_mean": mu,
        "symbol_ic_sd": sd,
        "symbol_ic_se": se,
        "t_stat": t_stat,
        "frac_positive": (sum(1 for v in sym_ic if v > 0) / len(sym_ic)) if sym_ic else None,
        "partial_ic_mean": pmu,
        "partial_ic_se": pse,
        "partial_t": pt,
        "partial_symbols": len(sym_partial),
        "hit_rate": (hits / decided) if decided else None,
        "hit_n": decided,
        "hit_se": (0.5 / math.sqrt(decided)) if decided else None,
        "rows": n_rows,
        "per_symbol_ic": sym_ic_map,
    }


def contemporaneous_check(series_bars):
    """Sanity: does inferred delta agree with the SAME bar's own return?

    Not a prediction test -- the agreement is mechanical.  It is the floor:
    if delta does not line up with the bar it was measured in, the classifier
    is tracking nothing and no forward number below can mean anything.
    Measured open-to-close (inside the bar) as well as close-to-close, because
    close-to-close drags the previous bar's bid/ask bounce in with it.
    """
    out = {}
    for label in ("open_to_close", "close_to_close"):
        xs, ys, per_series = [], [], []
        by_instrument = {}
        for _key, bars in series_bars.items():
            active = [b for b in bars
                      if b["volume"] > 0 and b["trades"] > 0 and b["close"]]
            sx, sy = [], []
            for i, cur in enumerate(active):
                if label == "open_to_close":
                    if not cur["open"]:
                        continue
                    r = cur["close"] / cur["open"] - 1.0
                else:
                    if i == 0:
                        continue
                    prev = active[i - 1]
                    if prev["ts"] != cur["ts"] - 60 or not prev["close"]:
                        continue
                    r = cur["close"] / prev["close"] - 1.0
                sx.append(cur["delta"] / cur["volume"])
                sy.append(r)
            if len(sx) < MIN_SERIES_PAIRS:
                continue
            ic = spearman(sx, sy)
            if ic is None:
                continue
            per_series.append(ic)
            by_instrument.setdefault(instrument_class(_key[0]), []).append(ic)
            xs.extend(uniform_ranks(sx))
            ys.extend(uniform_ranks(sy))
        mu, sd, se, t = mean_se_t(per_series)
        groups = []
        for name in sorted(by_instrument):
            gmu, gsd, gse, gt = mean_se_t(by_instrument[name])
            groups.append({"bucket": name, "series": len(by_instrument[name]),
                           "ic_mean": gmu, "ic_sd": gsd, "ic_se": gse,
                           "t_stat": gt})
        out[label] = {
            "pooled_ic": pearson(xs, ys) if xs else None,
            "pooled_n": len(xs),
            "series": len(per_series),
            "series_ic_mean": mu,
            "series_ic_sd": sd,
            "t_stat": t,
            "by_instrument": groups,
        }
    return out


# --------------------------------------------------------------------------
# splits
# --------------------------------------------------------------------------

def bucket_label(value, edges):
    if value < edges[0]:
        return "<%g" % edges[0]
    for i in range(len(edges) - 1):
        if value < edges[i + 1]:
            return "%g-%g" % (edges[i], edges[i + 1])
    return ">=%g" % edges[-1]


def value_split(result, summaries, key, edges):
    per = result["per_symbol_ic"]
    buckets = {}
    for sym, ic in per.items():
        v = summaries[sym].get(key)
        label = "unmeasured" if v is None else bucket_label(v, edges)
        buckets.setdefault(label, []).append(ic)
    order = ["<%g" % edges[0]]
    order += ["%g-%g" % (edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
    order += [">=%g" % edges[-1], "unmeasured"]
    rows = []
    for label in order:
        if label not in buckets:
            continue
        mu, sd, se, t = mean_se_t(buckets[label])
        rows.append({"bucket": label, "symbols": len(buckets[label]),
                     "ic_mean": mu, "ic_sd": sd, "ic_se": se, "t_stat": t})
    return rows


def edge_vs_cost(result, summaries, key="instrument"):
    """Is the measured IC bigger than the spread it would have to cross?

    An order-of-magnitude comparison, NOT a backtest. Expected per-trade edge
    is approximated as |IC| x (that symbol's own per-minute return sd) -- the
    textbook rank-IC-to-return conversion at a one-sd signal -- and the cost as
    one full quoted spread, which is what a round trip crossing pays.  A ratio
    below 1 means the edge does not clear the spread even before brokerage,
    impact and the fact that the IC was measured in-sample.
    """
    buckets = {}
    for sym, ic in result["per_symbol_ic"].items():
        s = summaries[sym]
        if s["ret_sd"] is None or s["spread_bps_median"] is None:
            continue
        edge_bps = abs(ic) * s["ret_sd"] * 1e4
        cost_bps = s["spread_bps_median"]
        if cost_bps <= 0:
            continue
        buckets.setdefault(s[key], []).append(
            (edge_bps, cost_bps, edge_bps / cost_bps))
    rows = []
    for label in sorted(buckets):
        vals = buckets[label]
        rows.append({
            "bucket": label,
            "symbols": len(vals),
            "edge_bps_median": median(sorted(v[0] for v in vals)),
            "cost_bps_median": median(sorted(v[1] for v in vals)),
            "ratio_median": median(sorted(v[2] for v in vals)),
            "ratio_p90": percentile(sorted(v[2] for v in vals), 0.90),
        })
    return rows


def category_split(result, summaries, key):
    buckets = {}
    for sym, ic in result["per_symbol_ic"].items():
        buckets.setdefault(summaries[sym][key], []).append(ic)
    rows = []
    for label in sorted(buckets):
        mu, sd, se, t = mean_se_t(buckets[label])
        rows.append({"bucket": label, "symbols": len(buckets[label]),
                     "ic_mean": mu, "ic_sd": sd, "ic_se": se, "t_stat": t})
    return rows


# --------------------------------------------------------------------------
# per-symbol quality + normalisation baselines
# --------------------------------------------------------------------------

def symbol_summary(sym, days_bars):
    volumes, abs_deltas, nds, trades_l, deltas = [], [], [], [], []
    spreads, spread_bps, prices, rets = [], [], [], []
    tot_trades = tot_quote = tot_uncl = 0
    tot_volume = tot_classified = 0.0
    bars_used = 0

    for _day, bars in sorted(days_bars.items()):
        prev = None
        for bar in bars:
            if bar["volume"] <= 0 or bar["trades"] <= 0 or not bar["close"]:
                prev = None
                continue
            bars_used += 1
            volumes.append(float(bar["volume"]))
            abs_deltas.append(abs(float(bar["delta"])))
            deltas.append(float(bar["delta"]))
            nds.append(float(bar["delta"]) / float(bar["volume"]))
            trades_l.append(float(bar["trades"]))
            prices.append(float(bar["close"]))
            if bar["spread"] is not None and bar["spread"] > 0:
                spreads.append(float(bar["spread"]))
                spread_bps.append(1e4 * float(bar["spread"]) / float(bar["close"]))
            if prev is not None and prev["ts"] == bar["ts"] - 60 and prev["close"]:
                rets.append(bar["close"] / prev["close"] - 1.0)
            tot_trades += bar["trades"]
            tot_quote += bar["quote_n"]
            tot_uncl += bar["unclassified"]
            tot_volume += bar["volume"]
            tot_classified += bar["buy"] + bar["sell"]
            prev = bar

    def stats(vals):
        s = sorted(vals)
        n = len(s)
        return {
            "median": median(s) if n >= MIN_BARS_FOR_MEDIAN else None,
            "p25": percentile(s, 0.25) if n >= MIN_BARS_FOR_IQR else None,
            "p75": percentile(s, 0.75) if n >= MIN_BARS_FOR_IQR else None,
        }

    d_mean, d_sd = mean_sd(deltas) if len(deltas) >= MIN_BARS_FOR_SD else (None, None)
    _, r_sd = mean_sd(rets) if len(rets) >= MIN_BARS_FOR_SD else (None, None)
    sp, spb, pr = sorted(spreads), sorted(spread_bps), sorted(prices)

    return {
        "symbol": sym,
        "symbol_id": next(iter(days_bars.values()))[0]["sid"],
        "instrument": instrument_class(sym),
        "sessions": len(days_bars),
        "bars": bars_used,
        "volume": stats(volumes),
        "abs_delta": stats(abs_deltas),
        "nd": stats(nds),
        "trades": stats(trades_l),
        "delta_mean": d_mean,
        "delta_sd": d_sd,
        "ret_sd": r_sd,
        "spread_median": median(sp) if len(sp) >= MIN_BARS_FOR_MEDIAN else None,
        "spread_bps_median": median(spb) if len(spb) >= MIN_BARS_FOR_MEDIAN else None,
        "price_median": median(pr) if len(pr) >= MIN_BARS_FOR_MEDIAN else None,
        "quote_share": (tot_quote / tot_trades) if tot_trades else None,
        "classified_share": (tot_classified / tot_volume) if tot_volume else None,
        "unclassified_trade_share": (tot_uncl / tot_trades) if tot_trades else None,
        "total_trades": tot_trades,
        "total_volume": tot_volume,
    }


# Written explicitly rather than by position: the tuple built below has to keep
# step with the CREATE TABLE above, and a silent column shift would corrupt
# every baseline the desk reads.
BASELINE_COLUMNS = [
    "symbol", "as_of", "symbol_id", "instrument", "sessions", "bars",
    "volume_median", "volume_p25", "volume_p75",
    "abs_delta_median", "abs_delta_p25", "abs_delta_p75",
    "nd_median", "nd_p25", "nd_p75",
    "trades_median", "trades_p25", "trades_p75",
    "delta_mean", "delta_sd", "ret_sd",
    "spread_median", "spread_bps_median", "price_median",
    "quote_share", "classified_share", "unclassified_trade_share",
    "estimator", "updated_at",
]


def write_baselines(db_path, summaries, as_of, estimator):
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(BASELINE_SCHEMA)
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for s in summaries:
            if s["bars"] <= 0:
                continue
            rows.append((
                s["symbol"], as_of, s["symbol_id"], s["instrument"],
                s["sessions"], s["bars"],
                s["volume"]["median"], s["volume"]["p25"], s["volume"]["p75"],
                s["abs_delta"]["median"], s["abs_delta"]["p25"], s["abs_delta"]["p75"],
                s["nd"]["median"], s["nd"]["p25"], s["nd"]["p75"],
                s["trades"]["median"], s["trades"]["p25"], s["trades"]["p75"],
                s["delta_mean"], s["delta_sd"], s["ret_sd"],
                s["spread_median"], s["spread_bps_median"], s["price_median"],
                s["quote_share"], s["classified_share"],
                s["unclassified_trade_share"], estimator, now,
            ))
            if len(rows[-1]) != len(BASELINE_COLUMNS):
                raise AssertionError(
                    "baseline row has %d values for %d columns"
                    % (len(rows[-1]), len(BASELINE_COLUMNS)))
        conn.executemany(
            "INSERT OR REPLACE INTO of_symbol_baseline (%s) VALUES (%s)"
            % (",".join(BASELINE_COLUMNS), ",".join(["?"] * len(BASELINE_COLUMNS))),
            rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def fmt(v, nd=4, width=8):
    """Numbers only where a number was measured; never a stand-in zero."""
    if v is None:
        return "n/a".rjust(width)
    if isinstance(v, int):
        return ("%*d" % (width, v))
    return ("%*.*f" % (width, nd, v))


def report(res, out=sys.stdout):
    w = out.write
    w("\n" + "=" * 96 + "\n")
    w("ORDER-FLOW VALIDATION AND PER-SYMBOL NORMALISATION\n")
    w("=" * 96 + "\n")
    w("sessions   : %s\n" % ", ".join(res["days"]))
    w("estimator  : %s\n" % res["estimator"])
    w("universe   : %d symbols, %d symbol-sessions, %d active minute bars\n"
      % (res["n_symbols"], res["n_series"], res["n_bars"]))
    w("EVERY buy/sell/delta below is INFERRED (Lee-Ready), never reported by "
      "the exchange.\n")

    w("\n-- sanity: does inferred delta agree with its OWN bar's return? ---------\n")
    for label, c in res["contemporaneous"].items():
        w("   %-15s pooled IC %s (n=%d)   per-series mean %s sd %s  t %s  over %d series\n"
          % (label, fmt(c["pooled_ic"]), c["pooled_n"], fmt(c["series_ic_mean"]),
             fmt(c["series_ic_sd"]), fmt(c["t_stat"], 1, 6), c["series"]))
        for g in c["by_instrument"]:
            w("       %-12s series %s  IC mean %s +-SE %s  t %s\n"
              % (g["bucket"], fmt(g["series"], 0, 6), fmt(g["ic_mean"]),
                 fmt(g["ic_se"]), fmt(g["t_stat"], 1, 6)))
    w("   Mechanical agreement, not prediction. It is the floor: near zero here\n"
      "   would mean the classifier tracks nothing and nothing below could mean\n"
      "   anything either.\n")

    w("\n-- (a) PREDICTIVE VALUE ------------------------------------------------\n")
    for key, doc in TARGETS.items():
        w("   target %-6s %s\n" % (key, doc))
    for h in sorted(res["horizons"]):
        for key in TARGETS:
            block = res["horizons"][h][key]
            w("\n   horizon %d min, target %s\n" % (h, key))
            w("   %-11s%9s%9s%9s%9s%8s%8s%9s%8s%8s%8s%8s\n"
              % ("feature", "pooledIC", "+-SE", "symIC", "+-SE", "t", "frac+",
                 "partIC", "+-SE", "part t", "hit", "syms"))
            for r in block:
                w("   %-11s%s%s%s%s%s%s%s%s%s%s%s\n"
                  % (r["feature"], fmt(r["pooled_ic"], 4, 9), fmt(r["pooled_se"], 4, 9),
                     fmt(r["symbol_ic_mean"], 4, 9), fmt(r["symbol_ic_se"], 4, 9),
                     fmt(r["t_stat"], 1, 8), fmt(r["frac_positive"], 2, 8),
                     fmt(r["partial_ic_mean"], 4, 9), fmt(r["partial_ic_se"], 4, 8),
                     fmt(r["partial_t"], 1, 8), fmt(r["hit_rate"], 4, 8),
                     fmt(r["symbols"], 0, 8)))
    w("\n   pooled SE = 1/sqrt(rows) and is OPTIMISTIC: rows inside one "
      "symbol-session are not\n"
      "   independent. The honest test is symIC against its CROSS-SYMBOL SE -> "
      "column t.\n"
      "   partIC takes the previous bar's return out of both sides: it is what "
      "the flow\n"
      "   feature adds over price alone.\n")

    w("\n-- (b) DOES CLASSIFICATION QUALITY MATTER? -----------------------------\n")
    for name, rows in res["quality"].items():
        w("   split: %s\n" % name)
        w("     %-16s%8s%10s%10s%8s\n"
          % ("bucket", "symbols", "IC mean", "+-SE", "t"))
        for r in rows:
            w("     %-16s%s%s%s%s\n"
              % (r["bucket"], fmt(r["symbols"], 0, 8), fmt(r["ic_mean"], 4, 10),
                 fmt(r["ic_se"], 4, 10), fmt(r["t_stat"], 1, 8)))

    w("\n-- (c) IS THE EDGE BIGGER THAN THE SPREAD? -----------------------------\n")
    w("   %s\n" % res["edge_tag"])
    w("     %-16s%8s%12s%12s%10s%10s\n"
      % ("instrument", "symbols", "edge bps", "cost bps", "ratio med", "ratio p90"))
    for r in res["edge_vs_cost"]:
        w("     %-16s%s%s%s%s%s\n"
          % (r["bucket"], fmt(r["symbols"], 0, 8),
             fmt(r["edge_bps_median"], 3, 12), fmt(r["cost_bps_median"], 2, 12),
             fmt(r["ratio_median"], 4, 10), fmt(r["ratio_p90"], 4, 10)))
    w("   edge ~ |IC| x the symbol's own per-minute return sd; cost = one full\n"
      "   quoted spread. Order of magnitude only, and generous to the signal:\n"
      "   the IC is in-sample and brokerage and impact are not in the cost.\n")

    w("\n-- (d) NORMALISATION BASELINES -----------------------------------------\n")
    b = res["baselines"]
    w("   rows built %d, written %d, as_of %s\n"
      % (b["rows"], b["written"], b["as_of"]))
    w("   table of_symbol_baseline (view of_symbol_baseline_latest)\n")
    w("   worked example - why a raw delta cannot be compared across symbols:\n")
    w("     %-28s%7s%12s%12s%9s%9s%10s\n"
      % ("symbol", "bars", "vol med", "|delta| med", "nd p25", "nd p75", "spr bps"))
    for s in b["examples"]:
        w("     %-28s%s%s%s%s%s%s\n"
          % (s["symbol"], fmt(s["bars"], 0, 7),
             fmt(s["volume"]["median"], 0, 12), fmt(s["abs_delta"]["median"], 0, 12),
             fmt(s["nd"]["p25"], 3, 9), fmt(s["nd"]["p75"], 3, 9),
             fmt(s["spread_bps_median"], 1, 10)))
    w("\n")


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run(args):
    conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    conn.execute("PRAGMA busy_timeout=30000")
    days = args.days.split(",") if args.days else usable_days(conn)
    if not days:
        raise SystemExit("no session in the tick store carries enough minute "
                         "rows (threshold %d)" % MIN_DAY_ROWS)
    series_bars = load_series(conn, days)
    conn.close()
    if not series_bars:
        raise SystemExit("no minute rows for days %s" % ",".join(days))

    by_symbol = {}
    for (sym, day), bars in series_bars.items():
        by_symbol.setdefault(sym, {})[day] = bars
    summaries = {sym: symbol_summary(sym, d) for sym, d in by_symbol.items()}

    horizons = {}
    split_source = None
    for h in args.horizons:
        pairs = {k: v for k, v in
                 ((k, build_pairs(v, h)) for k, v in series_bars.items()) if v}
        horizons[h] = {}
        for key in TARGETS:
            block = [evaluate(pairs, f, mode, key) for f, mode, _doc in FEATURES]
            horizons[h][key] = block
            if split_source is None and key == args.split_target:
                split_source = {r["feature"]: r for r in block}
    if split_source is None:
        raise SystemExit("--split-target %s not in %s"
                         % (args.split_target, ",".join(TARGETS)))

    base = split_source[args.split_feature]
    tag = "feature=%s, target=%s, horizon=%d" % (
        args.split_feature, args.split_target, args.horizons[0])
    edge = edge_vs_cost(base, summaries)
    quality = {
        "quote coverage quote_n/trades   (%s)" % tag:
            value_split(base, summaries, "quote_share", [0.5, 0.7, 0.85, 0.95]),
        "classified share (buy+sell)/vol (%s)" % tag:
            value_split(base, summaries, "classified_share", [0.8, 0.9, 0.95, 0.99]),
        "median spread, bps of price     (%s)" % tag:
            value_split(base, summaries, "spread_bps_median", [2, 5, 20, 100]),
        "instrument class                (%s)" % tag:
            category_split(base, summaries, "instrument"),
    }

    as_of = max(days)
    estimator = LEGACY_ESTIMATOR if as_of < CLASSIFIER_FIX_DAY else CURRENT_ESTIMATOR
    ordered = sorted(summaries.values(), key=lambda s: -s["total_trades"])
    written = write_baselines(args.db, ordered, as_of, estimator) \
        if args.write_baselines else 0

    picked, seen = [], set()
    thin = sorted((s for s in ordered if s["bars"] >= 20),
                  key=lambda s: s["total_trades"])[:2]
    for s in ordered[:3] + [x for x in ordered if x["instrument"] == "OPT"][:2] + thin:
        if s["symbol"] not in seen:
            seen.add(s["symbol"])
            picked.append(s)

    if not args.keep_per_symbol:
        for blocks in horizons.values():
            for block in blocks.values():
                for r in block:
                    r.pop("per_symbol_ic", None)

    return {
        "days": days,
        "estimator": estimator,
        "n_symbols": len(by_symbol),
        "n_series": len(series_bars),
        "n_bars": sum(s["bars"] for s in summaries.values()),
        "contemporaneous": contemporaneous_check(series_bars),
        "horizons": horizons,
        "quality": quality,
        "edge_vs_cost": edge,
        "edge_tag": tag,
        "baselines": {
            "rows": sum(1 for s in ordered if s["bars"] > 0),
            "written": written,
            "as_of": as_of,
            "examples": picked,
        },
    }


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Validate inferred order flow and refresh per-symbol "
                    "normalisation baselines.")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--container", default=DEFAULT_CONTAINER)
    p.add_argument("--days", default=None,
                   help="comma-separated IST dates; default = every day with "
                        ">= %d minute rows" % MIN_DAY_ROWS)
    p.add_argument("--horizons", default="1,5")
    p.add_argument("--split-feature", default="nd",
                   help="feature whose per-symbol IC drives the quality splits")
    p.add_argument("--split-target", default="oc", choices=sorted(TARGETS),
                   help="target used for the quality splits (default: the "
                        "tradeable one)")
    p.add_argument("--write-baselines", action="store_true")
    p.add_argument("--json", default=None)
    p.add_argument("--keep-per-symbol", action="store_true")
    p.add_argument("--in-container", action="store_true",
                   help="internal: already inside the container, do not re-exec")
    a = p.parse_args(argv)
    a.horizons = [int(x) for x in a.horizons.split(",") if x.strip()]
    if not a.horizons:
        p.error("--horizons needs at least one value")
    return a


def inside_container():
    return os.path.exists("/.dockerenv")


def reexec_in_container(argv, container):
    """Pipe this file into the container and run it there.

    Host reads of the bind-mounted sqlite return stale or torn pages while the
    container's writer holds it, so the measurement has to happen inside.
    """
    with open(os.path.abspath(__file__), "rb") as fh:
        src = fh.read()
    cmd = ["docker", "exec", "-i", container, "python", "-", "--in-container"]
    sys.stderr.write("[of_validation] re-running inside %s\n" % container)
    return subprocess.run(cmd + list(argv), input=src).returncode


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv)
    if not args.in_container and not inside_container():
        return reexec_in_container(argv, args.container)
    res = run(args)
    report(res)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(res, fh, indent=2, default=str)
        sys.stderr.write("[of_validation] wrote %s\n" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
