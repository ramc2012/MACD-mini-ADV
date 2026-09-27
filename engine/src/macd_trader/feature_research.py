from __future__ import annotations

import json
import math
import sqlite3
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

from .chart_history import aggregate_session_candles
from .indicators import IncrementalBollingerBands, IncrementalKAMA, IncrementalMACD
from .models import Candle


IST = ZoneInfo("Asia/Kolkata")
RISK_FREE_RATE = 0.06


def normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def option_value(spot: float, strike: float, years: float, volatility: float, option_type: str) -> float:
    if min(spot, strike, years, volatility) <= 0:
        return 0.0
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (RISK_FREE_RATE + volatility * volatility / 2) * years) / (volatility * root)
    d2 = d1 - volatility * root
    discounted = strike * math.exp(-RISK_FREE_RATE * years)
    if option_type == "CE":
        return spot * normal_cdf(d1) - discounted * normal_cdf(d2)
    return discounted * normal_cdf(-d2) - spot * normal_cdf(-d1)


def implied_gamma(spot: float, strike: float, premium: float, years: float, option_type: str) -> tuple[float | None, float | None]:
    intrinsic = max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
    if years <= 0 or premium <= intrinsic or spot <= 0 or strike <= 0:
        return None, None
    low, high = 0.01, 5.0
    if option_value(spot, strike, years, high, option_type) < premium:
        return None, None
    for _ in range(48):
        mid = (low + high) / 2
        if option_value(spot, strike, years, mid, option_type) < premium:
            low = mid
        else:
            high = mid
    volatility = (low + high) / 2
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (RISK_FREE_RATE + volatility * volatility / 2) * years) / (volatility * root)
    density = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    return volatility, density / (spot * volatility * root)


def load_candles(db: sqlite3.Connection, symbol: str, timeframe: int = 1800) -> list[Candle]:
    rows = db.execute(
        "SELECT timestamp,open,high,low,close,volume FROM historical_candles "
        "WHERE symbol=? AND timeframe_seconds=60 ORDER BY timestamp", (symbol,),
    )
    raw = [Candle(symbol, row[0], row[1], row[2], row[3], row[4], row[5], True) for row in rows]
    return aggregate_session_candles(raw, timeframe)


def trade_outcome(candles: list[Candle], entry_index: int) -> dict:
    entry = candles[entry_index].open * 1.0005
    peak = entry
    trailing = None
    maximum = entry
    minimum = entry
    for candle in candles[entry_index:]:
        maximum = max(maximum, candle.high)
        minimum = min(minimum, candle.low)
        if candle.low <= entry * 0.70:
            exit_price = min(candle.open, entry * 0.70) * 0.9995
            return {
                "closed": True, "return_pct": (exit_price / entry - 1) * 100,
                "mfe_pct": (maximum / entry - 1) * 100, "mae_pct": (minimum / entry - 1) * 100,
                "exit_reason": "HARD_STOP_30_PCT",
            }
        if trailing is not None and candle.low <= trailing:
            exit_price = min(candle.open, trailing) * 0.9995
            return {
                "closed": True, "return_pct": (exit_price / entry - 1) * 100,
                "mfe_pct": (maximum / entry - 1) * 100, "mae_pct": (minimum / entry - 1) * 100,
                "exit_reason": "TRAILING_STOP_25_PCT",
            }
        peak = max(peak, candle.high)
        if peak >= entry * 1.30:
            trailing = peak * 0.75
    last = candles[-1].close
    return {
        "closed": False, "return_pct": (last / entry - 1) * 100,
        "mfe_pct": (maximum / entry - 1) * 100, "mae_pct": (minimum / entry - 1) * 100,
        "exit_reason": "OPEN",
    }


def build_events(db: sqlite3.Connection, contracts: list[dict]) -> list[dict]:
    spot_cache: dict[str, dict[int, tuple[Candle, float, float | None, float | None]]] = {}
    events: list[dict] = []
    for meta in contracts:
        spot_symbol = str(meta["spot_symbol"])
        if spot_symbol not in spot_cache:
            spot_rows = load_candles(db, spot_symbol)
            spot_macd = IncrementalMACD()
            spot_kama = IncrementalKAMA()
            previous_close = None
            mapped = {}
            for candle in spot_rows:
                macd_value = spot_macd.update(candle.close)
                old_kama = spot_kama.value
                kama_value = spot_kama.update(candle.close)
                direction_return = (candle.close / previous_close - 1) if previous_close else 0.0
                mapped[candle.timestamp] = (candle, direction_return, macd_value.macd, None if kama_value is None or old_kama is None else kama_value / old_kama - 1)
                previous_close = candle.close
            spot_cache[spot_symbol] = mapped

        candles = load_candles(db, str(meta["symbol"]))
        if len(candles) < 30:
            continue
        macd = IncrementalMACD()
        bands = IncrementalBollingerBands()
        kama = IncrementalKAMA()
        volumes: deque[int] = deque(maxlen=20)
        closes: deque[float] = deque(maxlen=4)
        previous_macd = None
        for index, candle in enumerate(candles[:-1]):
            value = macd.update(candle.close)
            bb = bands.update(candle.close)
            old_kama = kama.value
            kama_value = kama.update(candle.close)
            average_volume = mean(volumes) if volumes else 0
            volume_ratio = candle.volume / average_volume if average_volume else 0
            volumes.append(candle.volume)
            previous_closes = list(closes)
            closes.append(candle.close)
            crossed = value.previous_macd is not None and value.previous_macd <= 0 < value.macd and macd.count > 26
            if not crossed:
                previous_macd = value.macd
                continue
            spot_row = spot_cache[spot_symbol].get(candle.timestamp)
            if not spot_row or bb.upper is None or bb.lower is None or kama_value is None or old_kama is None:
                previous_macd = value.macd
                continue
            spot_candle, spot_return, spot_macd_value, spot_kama_slope = spot_row
            direction = 1 if meta["option_type"] == "CE" else -1
            expiry = datetime.fromisoformat(str(meta["expiry"])).replace(tzinfo=IST)
            signal_time = datetime.fromtimestamp(candle.timestamp, UTC).astimezone(IST)
            years = max((expiry - signal_time).total_seconds(), 1800) / (365.0 * 86400)
            iv, gamma = implied_gamma(spot_candle.close, float(meta["strike"]), candle.close, years, str(meta["option_type"]))
            width = bb.upper - bb.lower
            outcome = trade_outcome(candles, index + 1)
            row = {
                "symbol": meta["symbol"], "option_type": meta["option_type"],
                "signal_time": signal_time.isoformat(), "date": signal_time.date().isoformat(),
                "hour": signal_time.hour + signal_time.minute / 60,
                "volume_ratio": volume_ratio,
                "option_ret_1": (candle.close / previous_closes[-1] - 1) if previous_closes else 0,
                "option_ret_2": (candle.close / previous_closes[-2] - 1) if len(previous_closes) >= 2 else 0,
                "kama_slope": kama_value / old_kama - 1,
                "above_kama": candle.close / kama_value - 1,
                "bb_position": (candle.close - bb.lower) / width if width else 0.5,
                "bb_width_pct": width / bb.middle if bb.middle else 0,
                "macd_impulse": (value.macd - (previous_macd or 0)) / candle.close,
                "candle_close_location": (candle.close - candle.low) / (candle.high - candle.low) if candle.high > candle.low else 0.5,
                "candle_range_pct": (candle.high - candle.low) / candle.close,
                "spot_direction_ret_1": direction * spot_return,
                "spot_macd_direction": direction * (spot_macd_value or 0) / spot_candle.close,
                "spot_kama_direction": direction * (spot_kama_slope or 0),
                "moneyness": direction * (spot_candle.close - float(meta["strike"])) / spot_candle.close,
                "abs_moneyness": abs(spot_candle.close - float(meta["strike"])) / spot_candle.close,
                "dte": years * 365,
                "implied_volatility": iv,
                "gamma_scaled": gamma * spot_candle.close if gamma is not None else None,
                **outcome,
            }
            row["large_winner"] = row["mfe_pct"] >= 50 and row["return_pct"] > 0
            events.append(row)
            previous_macd = value.macd
    return events


def metrics(rows: list[dict]) -> dict:
    closed = [row for row in rows if row["closed"]]
    returns = [row["return_pct"] for row in closed]
    winners = [value for value in returns if value > 0]
    losers = [value for value in returns if value <= 0]
    return {
        "signals": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
        "wins": len(winners), "large_winners": sum(row["large_winner"] for row in closed),
        "win_rate_pct": round(100 * len(winners) / len(closed), 2) if closed else 0,
        "average_return_pct": round(mean(returns), 3) if returns else 0,
        "profit_factor_return": round(sum(winners) / -sum(losers), 3) if losers and sum(losers) < 0 else None,
    }


def quantiles(values: list[float]) -> list[float]:
    ordered = sorted(values)
    return sorted({ordered[int((len(ordered) - 1) * q)] for q in (0.2, 0.35, 0.5, 0.65, 0.8)}) if ordered else []


def discover(events: list[dict]) -> dict:
    closed = [row for row in events if row["closed"]]
    dates = sorted({row["date"] for row in closed})
    test_dates = set(dates[-2:])
    train = [row for row in closed if row["date"] not in test_dates]
    test = [row for row in closed if row["date"] in test_dates]
    features = [
        "volume_ratio", "option_ret_1", "option_ret_2", "kama_slope", "above_kama",
        "bb_position", "bb_width_pct", "macd_impulse", "candle_close_location", "candle_range_pct",
        "spot_direction_ret_1", "spot_macd_direction", "spot_kama_direction", "moneyness",
        "abs_moneyness", "dte", "implied_volatility", "gamma_scaled", "hour",
    ]
    feature_profiles = []
    for feature in features:
        large = [row[feature] for row in train if row["large_winner"] and row.get(feature) is not None]
        rest = [row[feature] for row in train if not row["large_winner"] and row.get(feature) is not None]
        if large and rest:
            feature_profiles.append({
                "feature": feature, "large_winner_median": round(median(large), 6),
                "other_median": round(median(rest), 6),
                "median_difference": round(median(large) - median(rest), 6),
            })

    predicates = []
    for feature in features:
        values = [float(row[feature]) for row in train if row.get(feature) is not None]
        for threshold in quantiles(values):
            predicates.append((f"{feature}>={threshold:.6g}", lambda row, f=feature, t=threshold: row.get(f) is not None and row[f] >= t))
            predicates.append((f"{feature}<={threshold:.6g}", lambda row, f=feature, t=threshold: row.get(f) is not None and row[f] <= t))

    candidates = []
    combinations = [((name, predicate),) for name, predicate in predicates]
    for left_index, left in enumerate(predicates):
        for right in predicates[left_index + 1:]:
            if left[0].split("<")[0].split(">")[0] == right[0].split("<")[0].split(">")[0]:
                continue
            combinations.append((left, right))
    for combination in combinations:
        selected_train = [row for row in train if all(predicate(row) for _, predicate in combination)]
        selected_test = [row for row in test if all(predicate(row) for _, predicate in combination)]
        if len(selected_train) < 12 or len(selected_test) < 4:
            continue
        train_metrics, test_metrics = metrics(selected_train), metrics(selected_test)
        if train_metrics["average_return_pct"] <= 0 or test_metrics["average_return_pct"] <= 0:
            continue
        score = min(train_metrics["average_return_pct"], test_metrics["average_return_pct"]) * math.sqrt(len(selected_test))
        candidates.append({
            "rule": [name for name, _ in combination], "score": round(score, 4),
            "train": train_metrics, "test": test_metrics,
        })
    candidates.sort(key=lambda row: row["score"], reverse=True)
    return {
        "split": {"training_dates": [date for date in dates if date not in test_dates], "test_dates": sorted(test_dates)},
        "baseline_train": metrics(train), "baseline_test": metrics(test),
        "large_winner_feature_medians": feature_profiles,
        "robust_candidates": candidates[:20],
    }


def main() -> None:
    runtime = Path.cwd() / "runtime"
    db = sqlite3.connect(runtime / "historical.sqlite3")
    contracts = json.loads((runtime / "atm_contracts.json").read_text())["contracts"]
    events = build_events(db, contracts)
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "method": "30-minute premium MACD zero-cross events; features frozen at signal; next-bar entry; last two trading dates held out",
        "all_events": metrics(events),
        "discovery": discover(events),
        "events": events,
    }
    target = runtime / "feature_research.json"
    target.write_text(json.dumps(result, indent=2))
    target.chmod(0o600)
    print(json.dumps({"report": str(target), "all_events": result["all_events"], "discovery": result["discovery"]}, indent=2))


if __name__ == "__main__":
    main()
