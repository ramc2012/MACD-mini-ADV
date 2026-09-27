"""Offline, leakage-aware August option-premium ratio study. See adjacent PLAN.md.

Requires only numpy/pandas. Never imports the trading engine or a broker.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
PATTERN = re.compile(r"^(NSE|BSE):(.+?)(26AUG|268\d{2})(\d+(?:\.\d+)?)(CE|PE)$")
INDEX_SPOTS = {"NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
               "SENSEX": "BSE:SENSEX-INDEX", "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
               "FINNIFTY": "NSE:FINNIFTY-INDEX"}
TIMEFRAMES = (5, 15, 30)
HORIZONS = (30, 60)
RATIO_FEATURES = ["log_itm_atm", "log_atm_otm", "dlog_itm_atm", "dlog_atm_otm"]
PAIR_FEATURES = ["log_itm_otm", "dlog_itm_otm"]
BASE_FEATURES = ["spot_ret_tf", "spot_ret30", "spot_ret60", "spot_range30",
                 "session_return", "session_fraction", "dte", "atm_moneyness",
                 "itm_gap", "otm_gap", "atm_ret_tf"]


def epoch(day: str, hour: int = 0, minute: int = 0) -> int:
    return int(datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=IST).timestamp())


def parse_contract(symbol: str, monthly: dict[str, str]) -> dict | None:
    m = PATTERN.match(symbol)
    if not m:
        return None
    exchange, root, code, strike, side = m.groups()
    expiry = monthly.get(exchange) if code == "26AUG" else f"2026-08-{code[-2:]}"
    if not expiry:
        return None
    try:
        datetime.fromisoformat(expiry)
    except ValueError:
        return None
    return {"symbol": symbol, "exchange": exchange, "root": root, "side": side,
            "strike": float(strike), "expiry": expiry,
            "spot_symbol": INDEX_SPOTS.get(root, f"{exchange}:{root}-EQ")}


def clean_minutes(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.assign(day=pd.Series(dtype=str)).set_index("timestamp")
    frame = frame.drop_duplicates("timestamp").sort_values("timestamp")
    prices = frame[["open", "high", "low", "close"]]
    valid = np.isfinite(prices).all(axis=1) & prices.gt(0).all(axis=1)
    valid &= (frame.high >= frame[["open", "close", "low"]].max(axis=1))
    valid &= (frame.low <= frame[["open", "close", "high"]].min(axis=1))
    valid &= frame.volume.ge(0) & frame.timestamp.mod(60).eq(0)
    frame = frame.loc[valid].copy()
    local = pd.to_datetime(frame.timestamp, unit="s", utc=True).dt.tz_convert(IST)
    minute = local.dt.hour * 60 + local.dt.minute
    frame = frame.loc[(minute >= 555) & (minute < 930) & (local.dt.dayofweek < 5)].copy()
    frame["day"] = pd.to_datetime(frame.timestamp, unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
    return frame.set_index("timestamp")


def aggregate_day(raw: pd.DataFrame, opening: int, minutes: int) -> pd.DataFrame:
    """Bar index is its decision time, never its opening timestamp."""
    if raw.empty:
        return pd.DataFrame()
    keys = opening + ((raw.index.to_numpy() - opening) // (minutes * 60) + 1) * minutes * 60
    tmp = raw.assign(decision=keys, source_ts=raw.index)
    out = tmp.groupby("decision").agg(open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
        source_count=("close", "count"), last_ts=("source_ts", "last"), last_volume=("volume", "last"))
    out["coverage"] = out.source_count / minutes
    return out.loc[(out.index <= opening + 375 * 60) & out.last_ts.eq(out.index - 60)]


def select_ladder(meta: list[dict], minutes: dict[str, pd.DataFrame], selection_ts: int,
                  spot: float, side: str) -> dict[str, dict] | None:
    candidates = []
    for row in meta:
        raw = minutes.get(row["symbol"])
        if row["side"] != side or raw is None or selection_ts not in raw.index:
            continue
        current = raw.loc[selection_ts]
        if current.close > 0 and current.volume > 0:
            candidates.append(row)
    if len(candidates) < 3:
        return None
    atm = min(candidates, key=lambda c: (abs(c["strike"] - spot), c["strike"]))
    below = [r for r in candidates if r["strike"] < atm["strike"]]
    above = [r for r in candidates if r["strike"] > atm["strike"]]
    if not below or not above or abs(atm["strike"] / spot - 1) > .02:
        return None
    lower, upper = max(below, key=lambda r: r["strike"]), min(above, key=lambda r: r["strike"])
    if not lower["strike"] < spot < upper["strike"]:
        return None
    if max(atm["strike"] - lower["strike"], upper["strike"] - atm["strike"]) / spot > .02:
        return None
    return {"itm": lower if side == "CE" else upper, "atm": atm, "otm": upper if side == "CE" else lower}


def ratio_frame(legs: dict[str, pd.DataFrame], minutes: int) -> pd.DataFrame:
    merged = pd.concat({role: bars for role, bars in legs.items()}, axis=1, join="inner")
    if merged.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=merged.index)
    for role in ("itm", "atm", "otm"):
        out[f"{role}_close"] = merged[(role, "close")]
    out["coverage"] = merged.loc[:, [(r, "coverage") for r in legs]].min(axis=1)
    out["min_premium"] = out[[f"{r}_close" for r in legs]].min(axis=1)
    out["fresh"] = merged.loc[:, [(r, "last_volume") for r in legs]].gt(0).all(axis=1)
    out["itm_atm"] = out.itm_close / out.atm_close
    out["atm_otm"] = out.atm_close / out.otm_close
    out["itm_otm"] = out.itm_close / out.otm_close
    adjacent = out.index.to_series().diff().eq(minutes * 60)
    for name in ("itm_atm", "atm_otm", "itm_otm"):
        out[f"log_{name}"] = np.log(out[name])
        out[f"dlog_{name}"] = out[f"log_{name}"].diff().where(adjacent)
    out["atm_ret_tf"] = (out.atm_close / out.atm_close.shift(1) - 1).where(adjacent)
    out["pair_coverage"] = np.minimum(out.coverage, out.coverage.shift(1))
    out["pair_min_premium"] = np.minimum(out.min_premium, out.min_premium.shift(1))
    out["pair_fresh"] = out.fresh & out.fresh.shift(1, fill_value=False)
    return out


def select_pair(meta: list[dict], minutes: dict[str, pd.DataFrame], selection_ts: int,
                spot: float, side: str) -> dict[str, dict] | None:
    available = []
    for row in meta:
        raw = minutes.get(row["symbol"])
        if row["side"] != side or raw is None or selection_ts not in raw.index:
            continue
        current = raw.loc[selection_ts]
        if current.close > 0 and current.volume > 0:
            available.append(row)
    lower = [r for r in available if r["strike"] < spot]
    upper = [r for r in available if r["strike"] > spot]
    if not lower or not upper:
        return None
    low, high = max(lower, key=lambda r: r["strike"]), min(upper, key=lambda r: r["strike"])
    if max(abs(low["strike"] / spot - 1), abs(high["strike"] / spot - 1)) > .02:
        return None
    # ATM is explicitly a proxy for the nearer of the two quoted legs. It is
    # used ONLY for controls/trade outcomes, never to synthesize other ratios.
    return {"itm": low if side == "CE" else high, "otm": high if side == "CE" else low,
            "atm": min((low, high), key=lambda r: (abs(r["strike"] - spot), r["strike"]))}


def pair_ratio_frame(legs: dict[str, pd.DataFrame], minutes: int, proxy_role: str) -> pd.DataFrame:
    merged = pd.concat(legs, axis=1, join="inner")
    if merged.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=merged.index)
    out["itm_otm"] = merged[("itm", "close")] / merged[("otm", "close")]
    out["log_itm_otm"] = np.log(out.itm_otm)
    adjacent = out.index.to_series().diff().eq(minutes * 60)
    out["dlog_itm_otm"] = out.log_itm_otm.diff().where(adjacent)
    proxy = merged[(proxy_role, "close")]
    out["atm_ret_tf"] = (proxy / proxy.shift(1) - 1).where(adjacent)
    coverage = merged.loc[:, [(r, "coverage") for r in legs]].min(axis=1)
    premium = merged.loc[:, [(r, "close") for r in legs]].min(axis=1)
    fresh = merged.loc[:, [(r, "last_volume") for r in legs]].gt(0).all(axis=1)
    out["pair_coverage"] = np.minimum(coverage, coverage.shift(1))
    out["pair_min_premium"] = np.minimum(premium, premium.shift(1))
    out["pair_fresh"] = fresh & fresh.shift(1, fill_value=False)
    return out


def forward_return(raw: pd.DataFrame, decision: int, horizon: int, *, complete: bool = True) -> float:
    end = decision + (horizon - 1) * 60
    if decision not in raw.index or end not in raw.index:
        return np.nan
    path = raw.loc[decision:end]
    if complete and len(path) != horizon:
        return np.nan
    if path.iloc[0].volume <= 0 or path.iloc[-1].volume <= 0:
        # Index spot series genuinely have zero volume; caller sets it to 1.
        return np.nan
    return 100 * (float(raw.loc[end, "close"]) / float(raw.loc[decision, "open"]) - 1)


def split_dates(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(np.select([frame.day <= "2026-08-13", frame.day <= "2026-08-18"],
                               ["train", "validation"], default="test"), index=frame.index)


def build_observations(db: sqlite3.Connection, outdir: Path, pair_only: bool = False) -> tuple[pd.DataFrame, dict]:
    start, end = epoch("2026-08-01"), epoch("2026-08-28")
    print("Cataloguing August option contracts", flush=True)
    catalogue = pd.read_sql_query("""SELECT symbol, MIN(expiry) expiry_min, MAX(expiry) expiry_max,
        MIN(timestamp) first_ts, MAX(timestamp) last_ts, COUNT(*) minute_rows
        FROM historical_candles WHERE asset_type='option' AND timeframe_seconds=60
        AND timestamp>=? AND timestamp<? GROUP BY symbol""", db, params=(start, end))
    monthly = {}
    for exchange in ("NSE", "BSE"):
        dates = catalogue.loc[catalogue.symbol.str.startswith(exchange + ":") &
            catalogue.symbol.str.contains("26AUG") & catalogue.expiry_min.notna(), "expiry_min"]
        dates = sorted(set(dates))
        if len(dates) == 1:
            monthly[exchange] = dates[0]
    meta = [parsed for symbol in catalogue.symbol if (parsed := parse_contract(symbol, monthly))]
    meta_df = pd.DataFrame(meta)
    if meta_df.empty:
        raise RuntimeError("No unambiguously dated August contracts")
    meta_df = meta_df.merge(catalogue, on="symbol")
    meta_df.to_csv(outdir / "catalogue.csv", index=False)
    groups = list(meta_df.groupby(["exchange", "root", "expiry"]))
    audit = {"pair_only": pair_only, "monthly_expiries": monthly, "catalogue_contracts": len(meta_df),
             "roots": int(meta_df.root.nunique()), "expiries": sorted(meta_df.expiry.unique()),
             "catalogue_august_minute_rows": int(meta_df.minute_rows.sum()),
             "metadata_missing_contracts": int(meta_df.expiry_min.isna().sum()),
             "metadata_conflicts": meta_df.loc[meta_df.expiry_min.notna() &
                 ((meta_df.expiry_min != meta_df.expiry) | (meta_df.expiry_max != meta_df.expiry)),
                 ["symbol", "expiry", "expiry_min", "expiry_max"]].to_dict("records")}
    counters = Counter()
    observations, ladders = [], []
    for group_number, ((exchange, root, expiry), group) in enumerate(groups, 1):
        records = group.to_dict("records")
        spot_symbol = records[0]["spot_symbol"]
        last = min(end, epoch(expiry) + 86400)
        symbols = group.symbol.tolist() + [spot_symbol]
        # Index-backed symbol/time bounds; the production database is never read here.
        frames = {}
        for symbol in symbols:
            raw = pd.read_sql_query("""SELECT timestamp,open,high,low,close,volume
                FROM historical_candles WHERE symbol=? AND timeframe_seconds=60
                AND timestamp>=? AND timestamp<? ORDER BY timestamp""", db, params=(symbol, start, last))
            counters["raw_rows_read"] += len(raw)
            clean = clean_minutes(raw)
            counters["invalid_or_offsession_rows"] += len(raw) - len(clean)
            frames[symbol] = clean
        spot_all = frames.pop(spot_symbol)
        if spot_all.empty:
            counters["root_expiries_missing_spot"] += 1
            continue
        for day, spot in spot_all.groupby("day"):
            counters["root_expiry_days"] += 1
            opening = epoch(day, 9, 15)
            selection = opening + 29 * 60
            closing = opening + 375 * 60
            if selection not in spot.index:
                counters["days_missing_selection_spot"] += 1
                continue
            spot_price = float(spot.loc[selection, "close"])
            raw_day = {s: f.loc[f.day.eq(day)] for s, f in frames.items()}
            for side in ("CE", "PE"):
                counters["side_days_considered"] += 1
                chosen = (select_pair if pair_only else select_ladder)(records, raw_day, selection, spot_price, side)
                if chosen is None:
                    counters["side_days_no_causal_ladder"] += 1
                    continue
                counters["side_days_selected"] += 1
                selection_info = {"day": day, "root": root, "expiry": expiry, "side": side,
                    "selection_time": selection + 60, "spot": spot_price, "atm_is_nearest_quoted_proxy": pair_only,
                    **{f"{role}_symbol": row["symbol"] for role, row in chosen.items()},
                    **{f"{role}_strike": row["strike"] for role, row in chosen.items()}}
                ladders.append(selection_info)
                atm_raw = raw_day[chosen["atm"]["symbol"]]
                dte = (datetime.fromisoformat(expiry) - datetime.fromisoformat(day)).days
                label_spot = spot.assign(volume=1)
                for tf in TIMEFRAMES:
                    legs = {r: aggregate_day(raw_day[c["symbol"]], opening, tf) for r, c in chosen.items() if not pair_only or r != "atm"}
                    if any(f.empty for f in legs.values()):
                        continue
                    proxy_role = "itm" if chosen["atm"]["symbol"] == chosen["itm"]["symbol"] else "otm"
                    ratios = pair_ratio_frame(legs, tf, proxy_role) if pair_only else ratio_frame(legs, tf)
                    for decision, ratio in ratios.iterrows():
                        if decision < selection + 60 or decision + 30 * 60 > closing:
                            continue
                        counters[f"candidate_rows_{tf}m"] += 1
                        if not ratio.pair_fresh or ratio.pair_coverage < .8 or ratio.pair_min_premium < 5:
                            continue
                        if not np.isfinite(ratio.dlog_itm_otm):
                            continue
                        now, prior = decision - 60, decision - tf * 60 - 60
                        if now not in spot.index or prior not in spot.index:
                            continue
                        past = spot.loc[opening:now]
                        def trailing_return(minutes):
                            before = decision - minutes * 60 - 60
                            if before < opening:
                                return float(spot.loc[now, "close"] / spot.iloc[0].open - 1)
                            if before not in spot.index:
                                return np.nan
                            return float(spot.loc[now, "close"] / spot.loc[before, "close"] - 1)
                        recent = spot.loc[max(opening, decision - 30 * 60):now]
                        price = float(spot.loc[now, "close"])
                        row = dict(selection_info, timeframe=tf, decision=int(decision), dte=dte,
                            coverage=float(ratio.pair_coverage), min_premium=float(ratio.pair_min_premium),
                            spot_ret_tf=price / float(spot.loc[prior, "close"]) - 1,
                            spot_ret30=trailing_return(30), spot_ret60=trailing_return(60),
                            spot_range30=(float(recent.high.max()) - float(recent.low.min())) / price,
                            session_return=price / float(past.iloc[0].open) - 1,
                            session_fraction=(decision - opening) / (375 * 60),
                            atm_moneyness=chosen["atm"]["strike"] / price - 1,
                            itm_gap=abs(chosen["itm"]["strike"] - chosen["atm"]["strike"]) / price,
                            otm_gap=abs(chosen["otm"]["strike"] - chosen["atm"]["strike"]) / price)
                        names = PAIR_FEATURES + ["itm_otm", "atm_ret_tf"] if pair_only else RATIO_FEATURES + ["itm_atm", "atm_otm", "itm_otm", "dlog_itm_otm", "atm_ret_tf"]
                        for name in names:
                            row[name] = float(ratio[name])
                        row["fixed_score"] = (-1 if side == "CE" else 1) * row["dlog_itm_otm"]
                        for horizon in HORIZONS:
                            row[f"spot_forward_{horizon}"] = forward_return(label_spot, decision, horizon) if decision + horizon * 60 <= closing else np.nan
                            row[f"option_gross_{horizon}"] = forward_return(atm_raw, decision, horizon) if decision + horizon * 60 <= closing else np.nan
                        observations.append(row)
        if group_number % 20 == 0:
            print(f"{group_number}/{len(groups)} root/expiries; {len(observations)} observations", flush=True)
    frame = pd.DataFrame(observations)
    if frame.empty:
        audit["counts"] = dict(counters)
        audit["observations_complete"] = audit["observations_80pct"] = 0
        return frame, audit
    frame["split"] = split_dates(frame)
    audit["counts"] = dict(counters)
    audit["observations_80pct"] = len(frame)
    audit["observations_complete"] = int(frame.coverage.ge(.999999).sum())
    audit["dates"] = sorted(frame.day.unique())
    audit["eligible_roots"] = int(frame.root.nunique())
    audit["ladder_days"] = len(ladders)
    frame.to_csv(outdir / "observations.csv.gz", index=False)
    pd.DataFrame(ladders).to_csv(outdir / "ladders.csv", index=False)
    frame.groupby(["timeframe", "side", "split"]).agg(rows=("decision", "size"),
        days=("day", "nunique"), symbols=("root", "nunique"), minimum_date=("day", "min"),
        maximum_date=("day", "max")).reset_index().to_csv(outdir / "coverage.csv", index=False)
    return frame, audit


def ridge_fit(train: pd.DataFrame, features: list[str], target: str) -> dict:
    x = train[features].to_numpy(float)
    low, high = np.quantile(x, [.01, .99], axis=0)
    x = np.clip(x, low, high)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-12] = 1
    x = (x - mean) / scale
    y = train[target].to_numpy(float)
    target_mean = float(y.mean())
    coef = np.linalg.solve(x.T @ x + 10 * np.eye(len(features)), x.T @ (y - target_mean))
    return dict(features=features, low=low, high=high, mean=mean, scale=scale, coef=coef, target_mean=target_mean)


def ridge_predict(model: dict, frame: pd.DataFrame) -> np.ndarray:
    x = np.clip(frame[model["features"]].to_numpy(float), model["low"], model["high"])
    return (x - model["mean"]) / model["scale"] @ model["coef"] + model["target_mean"]


def rank_corr(x, y) -> float:
    x, y = pd.Series(np.asarray(x, float)), pd.Series(np.asarray(y, float))
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 3 or x.nunique() < 2 or y.nunique() < 2:
        return float("nan")
    return float(x.rank().corr(y.rank()))


def day_ci(values) -> tuple[float, float]:
    values = np.asarray(values, float)
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(20260831)
    draws = rng.choice(values, (2000, len(values)), replace=True).mean(axis=1)
    return tuple(float(v) for v in np.quantile(draws, [.025, .975]))


def metrics(frame: pd.DataFrame, scores: np.ndarray, target: str) -> dict:
    temp = frame[["day", "root", target]].copy()
    temp["score"] = scores
    temp["hit"] = (np.sign(temp.score) == np.sign(temp[target])).astype(float)
    temp = temp.loc[temp[target].ne(0)]
    if temp.empty:
        return {"n": 0}
    daily_hit = temp.groupby("day").hit.mean()
    daily_ic = temp.groupby("day").apply(lambda g: rank_corr(g.score, g[target]), include_groups=False)
    hit_ci, ic_ci = day_ci(daily_hit), day_ci(daily_ic)
    return dict(n=len(temp), days=temp.day.nunique(), symbols=temp.root.nunique(),
        target_up_pct=100 * temp[target].gt(0).mean(), predicted_up_pct=100 * temp.score.gt(0).mean(),
        hit_pct=100 * temp.hit.mean(), equal_day_hit_pct=100 * daily_hit.mean(),
        hit_ci_low=100 * hit_ci[0], hit_ci_high=100 * hit_ci[1],
        spearman=rank_corr(temp.score, temp[target]), daily_ic=daily_ic.mean(),
        daily_ic_ci_low=ic_ci[0], daily_ic_ci_high=ic_ci[1],
        signed_spot_bps=(np.sign(temp.score) * temp[target] * 100).mean())


def trade_metrics(frame: pd.DataFrame, scores, horizon: int, threshold: float, lane: str) -> tuple[dict, pd.DataFrame]:
    temp = frame.copy()
    temp["score"] = scores
    if lane in ("CE", "PE"):
        temp["trade_side"] = lane
        temp["gross"] = temp[f"option_gross_{horizon}"]
        align = 1 if lane == "CE" else -1
        valid = temp.score * align > threshold
    else:
        temp["trade_side"] = np.where(temp.score > 0, "CE", "PE")
        temp["gross"] = np.where(temp.score > 0, temp[f"ce_option_gross_{horizon}"], temp[f"pe_option_gross_{horizon}"])
        valid = temp.score.abs() > threshold
    # 09:45 + fixed multiples of horizon: selection does not depend on future eligibility.
    offset = temp.decision - temp.day.map(lambda d: epoch(d, 9, 45))
    valid &= offset.ge(0) & offset.mod(horizon * 60).eq(0)
    # Multiple archived expiries may exist for an index: choose the nearest
    # eligible one using signal-time information, BEFORE checking its outcome.
    candidates = temp.loc[valid].sort_values("expiry").drop_duplicates(["root", "day", "decision", "trade_side"])
    temp = candidates.loc[candidates.gross.notna()].copy()
    if temp.empty:
        return {"candidates": len(candidates), "n": 0}, temp
    for cost in (.5, 1., 2.):
        temp[f"net_{cost:g}"] = temp.gross - cost
    daily = temp.groupby("day")["net_1"].mean()
    ci = day_ci(daily)
    by_symbol = temp.groupby("root")["net_1"].sum()
    positive = by_symbol.clip(lower=0)
    worst_without = [temp.loc[temp.root.ne(root), "net_1"].mean() for root in by_symbol.index]
    result = dict(candidates=len(candidates), n=len(temp), days=temp.day.nunique(), symbols=temp.root.nunique(),
        gross_pct=temp.gross.mean(), net_05_pct=temp["net_0.5"].mean(),
        net_1_pct=temp.net_1.mean(), net_2_pct=temp.net_2.mean(), win_1_pct=100 * temp.net_1.gt(0).mean(),
        equal_day_net_1_pct=daily.mean(), daily_net_ci_low=ci[0], daily_net_ci_high=ci[1],
        positive_dates=int(daily.gt(0).sum()), largest_positive_symbol_share_pct=float(100 * positive.max() / positive.sum()) if positive.sum() else np.nan,
        worst_leave_one_symbol_out_net_1_pct=float(np.nanmin(worst_without)) if len(by_symbol) > 1 else np.nan)
    return result, temp


def panel_for(frame: pd.DataFrame, lane: str, ratio_features=RATIO_FEATURES) -> tuple[pd.DataFrame, list[str], list[str]]:
    if lane != "BOTH":
        return frame.loc[frame.side.eq(lane)].copy(), BASE_FEATURES, ratio_features
    keys = ["root", "expiry", "day", "timeframe", "decision", "split"]
    common = ["spot_ret_tf", "spot_ret30", "spot_ret60", "spot_range30", "session_return", "session_fraction", "dte"]
    separate = ["atm_moneyness", "itm_gap", "otm_gap", "atm_ret_tf"]
    label_cols = [f"spot_forward_{h}" for h in HORIZONS]
    ce = frame.loc[frame.side.eq("CE")].copy()
    pe = frame.loc[frame.side.eq("PE")].copy()
    columns = ratio_features + separate + ["fixed_score", "coverage", "min_premium"] + [f"option_gross_{h}" for h in HORIZONS]
    ce = ce[keys + common + label_cols + columns].rename(columns={c: f"ce_{c}" for c in columns})
    pe = pe[keys + columns].rename(columns={c: f"pe_{c}" for c in columns})
    panel = ce.merge(pe, on=keys, validate="one_to_one")
    panel["coverage"] = panel[["ce_coverage", "pe_coverage"]].min(axis=1)
    panel["min_premium"] = panel[["ce_min_premium", "pe_min_premium"]].min(axis=1)
    panel["fixed_score"] = (panel.ce_fixed_score + panel.pe_fixed_score) / 2
    return panel, common + [f"{s}_{c}" for s in ("ce", "pe") for c in separate], [f"{s}_{c}" for s in ("ce", "pe") for c in ratio_features]


def evaluate(frame: pd.DataFrame, outdir: Path, ratio_features=RATIO_FEATURES) -> dict:
    results, trades, lifts, correlations, trade_details = [], [], [], [], []
    frozen = {}
    for quality, minimum_coverage in (("complete", .999999), ("80pct", .8)):
        subset = frame.loc[frame.coverage.ge(minimum_coverage)]
        for tf in TIMEFRAMES:
            for lane in ("CE", "PE", "BOTH"):
                panel, base, ratios = panel_for(subset.loc[subset.timeframe.eq(tf)], lane, ratio_features)
                if panel.empty:
                    continue
                for horizon in HORIZONS:
                    target = f"spot_forward_{horizon}"
                    usable = panel.replace([np.inf, -np.inf], np.nan).dropna(subset=base + ratios + [target])
                    train = usable.loc[usable.split.eq("train")]
                    identity = dict(quality=quality, timeframe=tf, lane=lane, horizon=horizon)
                    if len(train) < 100 or train.day.nunique() < 4:
                        results.append(dict(identity, model="insufficient_training", split="train", n=len(train), days=train.day.nunique()))
                        continue
                    models = {"price_baseline": ridge_fit(train, base, target), "ratios_only": ridge_fit(train, ratios, target),
                              "price_plus_ratios": ridge_fit(train, base + ratios, target)}
                    score_functions = {name: (lambda data, m=model: ridge_predict(m, data)) for name, model in models.items()}
                    score_functions["ratio_compression"] = lambda data: data.fixed_score.to_numpy()
                    score_functions["spot_momentum"] = lambda data: data.spot_ret30.to_numpy()
                    score_functions["spot_reversal"] = lambda data: -data.spot_ret30.to_numpy()
                    nonflat = train.loc[train[target].ne(0), target]
                    majority_direction = 1 if nonflat.gt(0).mean() >= .5 else -1
                    score_functions["train_majority"] = lambda data, sign=majority_direction: np.full(len(data), sign, dtype=float)
                    thresholds = {name: float(np.quantile(np.abs(fn(train)), .75)) for name, fn in score_functions.items()}
                    frozen[f"{quality}_{tf}_{lane}_{horizon}"] = {"train_n": len(train), "train_dates": sorted(train.day.unique()),
                        "thresholds": thresholds, "majority_direction": majority_direction,
                        "models": {name: {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in model.items()} for name, model in models.items()}}
                    for split in ("train", "validation", "test"):
                        test = usable.loc[usable.split.eq(split)]
                        if test.empty:
                            continue
                        for name, fn in score_functions.items():
                            scores = fn(test)
                            results.append(dict(identity, split=split, model=name, **metrics(test, scores, target)))
                            tm, detail = trade_metrics(test, scores, horizon, thresholds[name], lane)
                            trades.append(dict(identity, split=split, model=name, **tm))
                            if quality == "complete" and split == "test" and not detail.empty:
                                detail = detail.assign(**identity, model=name)
                                trade_details.append(detail[["quality", "timeframe", "lane", "horizon", "model", "root", "day", "expiry", "decision", "trade_side", "gross", "net_1", "dte", "min_premium"]])
                        if split == "test":
                            a, b = ridge_predict(models["price_plus_ratios"], test), ridge_predict(models["price_baseline"], test)
                            for sensitivity, mask in (("all", test.dte.ge(0)), ("exclude_expiry_day", test.dte.gt(0)),
                                ("dte_ge_2", test.dte.ge(2)), ("premium_ge_10", test.min_premium.ge(10))):
                                selection = mask & test[target].ne(0)
                                piece = test.loc[selection].copy()
                                if piece.empty:
                                    continue
                                piece["lift"] = (np.sign(a[selection]) == np.sign(piece[target])).astype(float) - (np.sign(b[selection]) == np.sign(piece[target])).astype(float)
                                daily = piece.groupby("day").lift.mean()
                                ci = day_ci(daily)
                                lifts.append(dict(identity, sensitivity=sensitivity, n=len(piece), days=piece.day.nunique(),
                                    equal_day_hit_lift_pp=100*daily.mean(), ci_low=100*ci[0], ci_high=100*ci[1]))
                            for feature in ratios + ["fixed_score"]:
                                correlations.append(dict(identity, feature=feature, n=len(test),
                                    same_bar_spearman=rank_corr(test[feature], test.spot_ret_tf),
                                    forward_spearman=rank_corr(test[feature], test[target])))
    pd.DataFrame(results).to_csv(outdir / "direction_metrics.csv", index=False)
    pd.DataFrame(trades).to_csv(outdir / "trade_metrics.csv", index=False)
    pd.DataFrame(lifts).to_csv(outdir / "incremental_lift.csv", index=False)
    pd.DataFrame(correlations).to_csv(outdir / "feature_correlations.csv", index=False)
    if trade_details:
        details = pd.concat(trade_details, ignore_index=True)
        details.to_csv(outdir / "test_trade_observations.csv", index=False)
        details.groupby(["timeframe", "lane", "horizon", "model", "day"]).agg(n=("gross", "size"), gross_pct=("gross", "mean"), net_1_pct=("net_1", "mean")).reset_index().to_csv(outdir / "test_by_date.csv", index=False)
        details.groupby(["timeframe", "lane", "horizon", "model", "root"]).agg(n=("gross", "size"), gross_pct=("gross", "mean"), net_1_pct=("net_1", "mean")).reset_index().to_csv(outdir / "test_by_symbol.csv", index=False)
    (outdir / "frozen_models.json").write_text(json.dumps(frozen, indent=2))
    return {"direction_rows": results, "trade_rows": trades, "lifts": lifts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-observations", action="store_true")
    parser.add_argument("--two-leg", action="store_true", help="Separate prespecified fallback: actual bracketing ITM/OTM pairs only")
    args = parser.parse_args()
    if args.database.name in {"historical.sqlite3", "macd_trader.sqlite3", "mp_trader.sqlite3"}:
        raise SystemExit("Use an isolated snapshot, never a production database")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.reuse_observations:
        frame = pd.read_csv(args.output / "observations.csv.gz")
        audit = json.loads((args.output / "audit.json").read_text())
    else:
        db = sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True)
        try:
            frame, audit = build_observations(db, args.output, args.two_leg)
        finally:
            db.close()
        audit["snapshot"] = str(args.database.resolve())
        audit["created_at"] = datetime.now(IST).isoformat()
        (args.output / "audit.json").write_text(json.dumps(audit, indent=2))
    print(json.dumps(audit, indent=2), flush=True)
    if frame.empty:
        print("Full-ladder coverage gate failed: no forward evaluation performed.", flush=True)
        return
    if args.two_leg != audit.get("pair_only", False):
        raise SystemExit("Observation cache does not match requested two-leg/full-ladder mode")
    results = evaluate(frame, args.output, PAIR_FEATURES if args.two_leg else RATIO_FEATURES)
    print(f"Finished {len(results['direction_rows'])} model/split comparisons in {args.output}", flush=True)


if __name__ == "__main__":
    main()
