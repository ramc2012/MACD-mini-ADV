from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

from .chart_history import aggregate_session_candles
from .models import Candle
from .universe import FNO_STOCKS, INDEX_SPOTS, STOCK_SPOTS


IST = ZoneInfo("Asia/Kolkata")
TIMEFRAME_SECONDS = 1800
TRAIN_END = "2026-08-13"
TEST_DATES = {"2026-08-14", "2026-08-17"}


def event_key(row: dict) -> tuple[str, int]:
    return row["symbol"], int(datetime.fromisoformat(row["signal_time"]).timestamp())


def load_raw(db: sqlite3.Connection, symbol: str, start: int | None = None, end: int | None = None) -> list[Candle]:
    sql = (
        "SELECT timestamp,open,high,low,close,volume FROM historical_candles "
        "WHERE symbol=? AND timeframe_seconds=60"
    )
    params: list[object] = [symbol]
    if start is not None:
        sql += " AND timestamp>=?"
        params.append(start)
    if end is not None:
        sql += " AND timestamp<=?"
        params.append(end)
    sql += " ORDER BY timestamp"
    return [
        Candle(symbol, int(ts), float(o), float(h), float(l), float(c), int(v), True)
        for ts, o, h, l, c, v in db.execute(sql, params)
    ]


def ema(values: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1)
    result: list[float] = []
    value: float | None = None
    for price in values:
        value = price if value is None else value + alpha * (price - value)
        result.append(value)
    return result


def rolling_mean(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= period:
            total -= values[index - period]
        result.append(total / period if index + 1 >= period else None)
    return result


def rolling_atr(candles: list[Candle], period: int = 14) -> list[float | None]:
    ranges: list[float] = []
    previous: float | None = None
    for candle in candles:
        ranges.append(
            candle.high - candle.low if previous is None
            else max(candle.high - candle.low, abs(candle.high - previous), abs(candle.low - previous))
        )
        previous = candle.close
    return rolling_mean(ranges, period)


def rolling_z(values: list[float], period: int = 20) -> list[float | None]:
    result: list[float | None] = []
    logs = [math.log1p(max(value, 0.0)) for value in values]
    for index, value in enumerate(logs):
        prior = logs[max(0, index - period):index]
        if len(prior) < period:
            result.append(None)
            continue
        average = mean(prior)
        variance = mean([(item - average) ** 2 for item in prior])
        result.append((value - average) / math.sqrt(variance) if variance > 1e-12 else 0.0)
    return result


def series_features(candles: list[Candle]) -> tuple[dict[int, dict], dict[int, int]]:
    closes = [row.close for row in candles]
    ema9, ema20, ema50 = ema(closes, 9), ema(closes, 20), ema(closes, 50)
    atr14 = rolling_atr(candles)
    volume_z20 = rolling_z([row.volume for row in candles])
    result: dict[int, dict] = {}
    index_by_time: dict[int, int] = {}
    for index, candle in enumerate(candles):
        index_by_time[candle.timestamp] = index
        atr = atr14[index] or max(candle.close * 0.01, 1e-9)
        previous = candles[index - 1] if index else candle
        prior5 = candles[max(0, index - 5):index]
        prior10 = candles[max(0, index - 10):index]
        body = (candle.close - candle.open) / atr
        range_atr = (candle.high - candle.low) / atr
        close_location = (
            (candle.close - candle.low) / (candle.high - candle.low)
            if candle.high > candle.low else 0.5
        )
        result[candle.timestamp] = {
            "close": candle.close,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "ema9": ema9[index],
            "ema20": ema20[index],
            "ema50": ema50[index],
            "atr14": atr,
            "close_vs_ema9_atr": (candle.close - ema9[index]) / atr,
            "close_vs_ema20_atr": (candle.close - ema20[index]) / atr,
            "ema9_vs_20_atr": (ema9[index] - ema20[index]) / atr,
            "ema20_vs_50_atr": (ema20[index] - ema50[index]) / atr,
            "ema20_slope5_atr": (
                (ema20[index] - ema20[index - 5]) / atr if index >= 5 else 0.0
            ),
            "reclaim_ema20": index > 0 and previous.close <= ema20[index - 1] and candle.close > ema20[index],
            "pullback_hold_ema20": candle.low <= ema20[index] * 1.005 and candle.close > ema20[index],
            "breakout_5": bool(prior5) and candle.close > max(row.high for row in prior5),
            "breakout_10": bool(prior10) and candle.close > max(row.high for row in prior10),
            "breakdown_5": bool(prior5) and candle.close < min(row.low for row in prior5),
            "breakdown_10": bool(prior10) and candle.close < min(row.low for row in prior10),
            "higher_lows_3": index >= 2 and candles[index - 2].low < previous.low < candle.low,
            "lower_highs_3": index >= 2 and candles[index - 2].high > previous.high > candle.high,
            "inside_bar": index > 0 and candle.high <= previous.high and candle.low >= previous.low,
            "bullish_engulfing": (
                index > 0 and previous.close < previous.open and candle.close > candle.open
                and candle.open <= previous.close and candle.close >= previous.open
            ),
            "bearish_engulfing": (
                index > 0 and previous.close > previous.open and candle.close < candle.open
                and candle.open >= previous.close and candle.close <= previous.open
            ),
            "body_atr": body,
            "range_atr": range_atr,
            "close_location": close_location,
            "volume_z20": volume_z20[index],
            "price_impact": body * max(volume_z20[index] or 0.0, 0.0),
            "volume_impulse": (
                volume_z20[index] is not None and volume_z20[index] >= 1.0
                and body > 0.15 and close_location >= 0.65
            ),
            "absorption_proxy": (
                volume_z20[index] is not None and volume_z20[index] >= 1.5
                and abs(body) <= 0.2 and range_atr <= 0.8
            ),
        }
    return result, index_by_time


def profile(rows: list[Candle], bin_size: float | None = None) -> dict | None:
    if not rows or sum(row.volume for row in rows) <= 0:
        return None
    low, high = min(row.low for row in rows), max(row.high for row in rows)
    if bin_size is None:
        bin_size = max((high - low) / 30.0, max(high, 1.0) * 0.00025)
    base = math.floor(low / bin_size) * bin_size
    buckets: dict[int, float] = defaultdict(float)
    for row in rows:
        typical = (row.high + row.low + row.close) / 3.0
        buckets[int(round((typical - base) / bin_size))] += max(row.volume, 0)
    if not buckets:
        return None
    poc_index = max(buckets, key=buckets.get)
    selected = {poc_index}
    accumulated = buckets[poc_index]
    target = sum(buckets.values()) * 0.70
    left, right = poc_index - 1, poc_index + 1
    minimum_index, maximum_index = min(buckets), max(buckets)
    while accumulated < target and (left >= minimum_index or right <= maximum_index):
        if left < minimum_index:
            chosen = right
        elif right > maximum_index:
            chosen = left
        else:
            left_volume = buckets.get(left, 0.0)
            right_volume = buckets.get(right, 0.0)
            chosen = left if left_volume >= right_volume else right
        selected.add(chosen)
        accumulated += max(buckets.get(chosen, 0.0), 0.0)
        if chosen == left:
            left -= 1
        else:
            right += 1
    level = lambda index: base + (index + 0.5) * bin_size
    return {
        "poc": level(poc_index),
        "val": level(min(selected)) - bin_size / 2,
        "vah": level(max(selected)) + bin_size / 2,
        "low": low,
        "high": high,
        "bin_size": bin_size,
    }


def underlying_from_option(symbol: str) -> str | None:
    if symbol.startswith("BSE:SENSEX"):
        return "SENSEX"
    body = symbol.split(":", 1)[-1]
    candidates = list(INDEX_SPOTS) + list(FNO_STOCKS)
    matches = [name for name in candidates if body.startswith(name)]
    return max(matches, key=len) if matches else None


def spot_symbol_for_option(symbol: str) -> str | None:
    underlying = underlying_from_option(symbol)
    if underlying in INDEX_SPOTS:
        return INDEX_SPOTS[underlying]
    return STOCK_SPOTS.get(underlying or "")


def add_option_features(db: sqlite3.Connection, events: list[dict]) -> dict[tuple[str, int], dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in events:
        grouped[row["symbol"]].append(row)
    output: dict[tuple[str, int], dict] = {}
    for position, (symbol, rows) in enumerate(grouped.items(), 1):
        candles = aggregate_session_candles(load_raw(db, symbol), TIMEFRAME_SECONDS)
        features, indexes = series_features(candles)
        for row in rows:
            key = event_key(row)
            signal = key[1]
            if signal not in features or signal not in indexes or indexes[signal] + 1 >= len(candles):
                continue
            value = features[signal].copy()
            entry_index = indexes[signal] + 1
            value.update(simulate_exit(candles, entry_index))
            value["entry_time"] = candles[entry_index].timestamp
            output[key] = value
        if position % 75 == 0:
            print(f"option features {position}/{len(grouped)}", flush=True)
    return output


def add_spot_and_profile_features(db: sqlite3.Connection, events: list[dict]) -> dict[tuple[str, int], dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in events:
        spot = spot_symbol_for_option(row["symbol"])
        if spot:
            grouped[spot].append(row)
    output: dict[tuple[str, int], dict] = {}
    for position, (symbol, rows) in enumerate(grouped.items(), 1):
        earliest = min(event_key(row)[1] for row in rows) - 8 * 86400
        latest = max(event_key(row)[1] for row in rows) + 1800
        raw = load_raw(db, symbol, earliest, latest)
        candles = aggregate_session_candles(raw, TIMEFRAME_SECONDS)
        features, indexes = series_features(candles)
        raw_by_day: dict[str, list[Candle]] = defaultdict(list)
        for candle in raw:
            moment = datetime.fromtimestamp(candle.timestamp, UTC).astimezone(IST)
            if (moment.hour, moment.minute) >= (9, 15) and (moment.hour, moment.minute) < (15, 30):
                raw_by_day[moment.date().isoformat()].append(candle)
        dates = sorted(raw_by_day)
        previous_day = {dates[index]: dates[index - 1] for index in range(1, len(dates))}
        profiles = {day: profile(day_rows) for day, day_rows in raw_by_day.items()}
        for row in rows:
            key = event_key(row)
            signal = key[1]
            current = features.get(signal)
            index = indexes.get(signal)
            if not current or index is None:
                continue
            direction = 1 if row["option_type"] == "CE" else -1
            day = row["date"]
            prior = profiles.get(previous_day.get(day, ""))
            current_minutes = [item for item in raw_by_day.get(day, []) if item.timestamp < signal + 1800]
            developing = profile(current_minutes, prior["bin_size"] if prior else None)
            session_open = current_minutes[0].open if current_minutes else current["open"]
            first_hour = current_minutes[:60]
            ib_high = max((item.high for item in first_hour), default=current["high"])
            ib_low = min((item.low for item in first_hour), default=current["low"])
            spot_atr = max(current["atr14"], 1e-9)
            spot_row = {
                "spot_close_vs_ema20_dir": direction * current["close_vs_ema20_atr"],
                "spot_ema9_vs_20_dir": direction * current["ema9_vs_20_atr"],
                "spot_ema20_vs_50_dir": direction * current["ema20_vs_50_atr"],
                "spot_ema20_slope5_dir": direction * current["ema20_slope5_atr"],
                "spot_breakout_5_dir": current["breakout_5"] if direction > 0 else current["breakdown_5"],
                "spot_breakout_10_dir": current["breakout_10"] if direction > 0 else current["breakdown_10"],
                "spot_three_bar_trend_dir": current["higher_lows_3"] if direction > 0 else current["lower_highs_3"],
                "spot_engulfing_dir": current["bullish_engulfing"] if direction > 0 else current["bearish_engulfing"],
                "spot_body_atr_dir": direction * current["body_atr"],
                "spot_volume_z20": current["volume_z20"],
                "spot_volume_impulse_dir": (
                    current["volume_z20"] is not None and current["volume_z20"] >= 1.0
                    and direction * current["body_atr"] > 0.15
                    and (current["close_location"] >= 0.65 if direction > 0 else current["close_location"] <= 0.35)
                ),
            }
            if prior:
                favourable_value_edge = prior["vah"] if direction > 0 else prior["val"]
                spot_row.update({
                    "profile_prev_poc_dir": direction * (current["close"] - prior["poc"]) / spot_atr,
                    "profile_outside_value_dir": direction * (current["close"] - favourable_value_edge) / spot_atr,
                    "profile_inside_prior_value": prior["val"] <= current["close"] <= prior["vah"],
                    "profile_open_outside_dir": (
                        session_open > prior["vah"] if direction > 0 else session_open < prior["val"]
                    ),
                    "profile_value_rejection_dir": (
                        min(item.low for item in current_minutes) <= prior["vah"] and current["close"] > prior["vah"]
                        if direction > 0 else
                        max(item.high for item in current_minutes) >= prior["val"] and current["close"] < prior["val"]
                    ),
                })
            if developing:
                spot_row["profile_developing_poc_dir"] = direction * (current["close"] - developing["poc"]) / spot_atr
            signal_clock = datetime.fromtimestamp(signal, UTC).astimezone(IST)
            spot_row["profile_initial_balance_break_dir"] = (
                signal_clock.hour > 10 or (signal_clock.hour == 10 and signal_clock.minute >= 15)
            ) and (current["close"] > ib_high if direction > 0 else current["close"] < ib_low)
            output[key] = spot_row
        if position % 40 == 0:
            print(f"spot/profile features {position}/{len(grouped)}", flush=True)
    return output


def simulate_exit(candles: list[Candle], entry_index: int) -> dict:
    entry = candles[entry_index].open * 1.0005
    hard_stop = entry * 0.85
    peak = entry
    trailing: float | None = None
    minimum = entry
    maximum = entry
    for candle in candles[entry_index:]:
        minimum = min(minimum, candle.low)
        maximum = max(maximum, candle.high)
        if candle.low <= hard_stop:
            exit_price = min(candle.open, hard_stop) * 0.9995
            return outcome(entry, exit_price, candle.timestamp, "HARD_STOP_15", minimum, maximum)
        if trailing is not None and candle.low <= trailing:
            exit_price = min(candle.open, trailing) * 0.9995
            return outcome(entry, exit_price, candle.timestamp, "TRAIL_10_AFTER_20", minimum, maximum)
        peak = max(peak, candle.high)
        if peak >= entry * 1.20:
            trailing = peak * 0.90
    exit_price = candles[-1].close * 0.9995
    return outcome(entry, exit_price, candles[-1].timestamp, "MARK_TO_LAST", minimum, maximum)


def outcome(entry: float, exit_price: float, exit_time: int, reason: str, minimum: float, maximum: float) -> dict:
    return {
        "entry_price": entry,
        "exit_price": exit_price,
        "exit_time": exit_time,
        "exit_reason": reason,
        "candidate_return_pct": (exit_price / entry - 1) * 100,
        "candidate_mae_pct": (minimum / entry - 1) * 100,
        "candidate_mfe_pct": (maximum / entry - 1) * 100,
    }


def metrics(rows: list[dict]) -> dict:
    values = [row["candidate_return_pct"] for row in rows]
    winners = [value for value in values if value > 0]
    losers = [value for value in values if value <= 0]
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for row in sorted(rows, key=lambda item: item["entry_time"]):
        equity += row["candidate_return_pct"]
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return {
        "trades": len(rows),
        "wins": len(winners),
        "win_rate_pct": round(100 * len(winners) / len(rows), 2) if rows else 0.0,
        "average_return_pct": round(mean(values), 3) if values else 0.0,
        "median_return_pct": round(median(values), 3) if values else 0.0,
        "sum_return_pct": round(sum(values), 3),
        "profit_factor": round(sum(winners) / -sum(losers), 3) if losers and sum(losers) < 0 else None,
        "max_drawdown_sum_pct": round(drawdown, 3),
    }


def date_blocks(rows: list[dict], count: int = 4) -> list[list[dict]]:
    dates = sorted({row["date"] for row in rows})
    blocks: list[list[dict]] = []
    for block in range(count):
        selected = set(dates[block * len(dates) // count:(block + 1) * len(dates) // count])
        blocks.append([row for row in rows if row["date"] in selected])
    return blocks


def analyse_rules(train: list[dict], test: list[dict]) -> list[dict]:
    numeric = [
        "option_close_vs_ema20_atr", "option_ema9_vs_20_atr", "option_ema20_vs_50_atr",
        "option_ema20_slope5_atr", "option_volume_z20", "option_price_impact",
        "spot_close_vs_ema20_dir", "spot_ema9_vs_20_dir", "spot_ema20_vs_50_dir",
        "spot_ema20_slope5_dir", "spot_body_atr_dir", "spot_volume_z20",
        "profile_prev_poc_dir", "profile_outside_value_dir", "profile_developing_poc_dir",
    ]
    boolean = [
        "option_reclaim_ema20", "option_pullback_hold_ema20", "option_breakout_5", "option_breakout_10",
        "option_higher_lows_3", "option_inside_bar", "option_bullish_engulfing", "option_volume_impulse",
        "option_absorption_proxy", "spot_breakout_5_dir", "spot_breakout_10_dir",
        "spot_three_bar_trend_dir", "spot_engulfing_dir", "spot_volume_impulse_dir",
        "profile_inside_prior_value", "profile_open_outside_dir", "profile_value_rejection_dir",
        "profile_initial_balance_break_dir", "synchronised_volume_impulse",
    ]
    predicates: list[tuple[str, object]] = []
    for feature in numeric:
        values = sorted(float(row[feature]) for row in train if row.get(feature) is not None)
        if len(values) < 20:
            continue
        for quantile in (0.25, 0.50, 0.75):
            threshold = values[int((len(values) - 1) * quantile)]
            predicates.append((f"{feature}>={threshold:.6g}", lambda row, f=feature, t=threshold: row.get(f) is not None and row[f] >= t))
            predicates.append((f"{feature}<={threshold:.6g}", lambda row, f=feature, t=threshold: row.get(f) is not None and row[f] <= t))
        predicates.append((f"{feature}>=0", lambda row, f=feature: row.get(f) is not None and row[f] >= 0))
        predicates.append((f"{feature}<=0", lambda row, f=feature: row.get(f) is not None and row[f] <= 0))
    for feature in ("option_volume_z20", "spot_volume_z20"):
        for threshold in (1.0, 2.0):
            predicates.append((f"{feature}>={threshold:g}", lambda row, f=feature, t=threshold: row.get(f) is not None and row[f] >= t))
    for threshold in (2.0, 2.5):
        predicates.append((f"profile_prev_poc_dir<={threshold:g}", lambda row, t=threshold: row.get("profile_prev_poc_dir") is not None and row["profile_prev_poc_dir"] <= t))
    predicates.append(("spot_ema9_vs_20_dir>=0.5", lambda row: row.get("spot_ema9_vs_20_dir") is not None and row["spot_ema9_vs_20_dir"] >= 0.5))
    for feature in boolean:
        predicates.append((feature, lambda row, f=feature: row.get(f) is True))

    baseline = metrics(train)
    results: list[dict] = []
    for name, predicate in predicates:
        selected_train = [row for row in train if predicate(row)]
        selected_test = [row for row in test if predicate(row)]
        if len(selected_train) < 20 or len(selected_test) < 2:
            continue
        train_metrics = metrics(selected_train)
        test_metrics = metrics(selected_test)
        blocks = [metrics([row for row in block if predicate(row)]) for block in date_blocks(train)]
        positive_blocks = sum(item["trades"] >= 3 and item["average_return_pct"] > 0 for item in blocks)
        results.append({
            "rule": name,
            "train": train_metrics,
            "test": test_metrics,
            "positive_train_blocks": positive_blocks,
            "train_blocks": blocks,
            "average_return_lift_train_pct_points": round(
                train_metrics["average_return_pct"] - baseline["average_return_pct"], 3
            ),
            "holdout_positive": test_metrics["average_return_pct"] > 0,
        })
    results.sort(
        key=lambda item: (
            item["positive_train_blocks"], item["holdout_positive"],
            item["train"]["profit_factor"] or 0.0, item["train"]["average_return_pct"],
        ),
        reverse=True,
    )
    return results


def main() -> None:
    runtime = Path.cwd() / "runtime"
    payload = json.loads((runtime / "feature_research.json").read_text())
    events = payload["events"]
    db = sqlite3.connect(runtime / "historical.sqlite3")
    try:
        option = add_option_features(db, events)
        spot = add_spot_and_profile_features(db, events)
    finally:
        db.close()

    enriched: list[dict] = []
    for row in events:
        key = event_key(row)
        option_row = option.get(key)
        if not option_row:
            continue
        result = {k: v for k, v in row.items()}
        result.update({f"option_{key}": value for key, value in option_row.items()})
        for name in (
            "entry_price", "exit_price", "exit_time", "exit_reason", "candidate_return_pct",
            "candidate_mae_pct", "candidate_mfe_pct", "entry_time",
        ):
            result[name] = result.pop(f"option_{name}")
        result.update(spot.get(key, {}))
        result["synchronised_volume_impulse"] = bool(
            result.get("option_volume_impulse") and result.get("spot_volume_impulse_dir")
        )
        enriched.append(result)

    selected = [
        row for row in enriched
        if row.get("moneyness") is not None and row["moneyness"] <= -0.01
        and row.get("implied_volatility") is not None and row["implied_volatility"] <= 0.25
    ]
    executable: list[dict] = []
    occupied_until: dict[str, int] = {}
    for row in sorted(selected, key=lambda item: (item["entry_time"], item["symbol"])):
        if row["entry_time"] <= occupied_until.get(row["symbol"], -1):
            continue
        executable.append(row)
        occupied_until[row["symbol"]] = row["exit_time"]

    train = [row for row in executable if row["date"] <= TRAIN_END]
    test = [row for row in executable if row["date"] in TEST_DATES]
    rules = analyse_rules(train, test)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "method": {
            "signal_universe": "30-minute premium MACD zero-cross events from feature_research.json",
            "entry_cohort": "moneyness <= -1% and estimated IV <= 25%; next 30-minute bar open",
            "exit": "15% hard stop; after +20% MFE trail 10% below peak; conservative within-bar ordering",
            "overlap": "one position per option contract at a time",
            "train": f"through {TRAIN_END}",
            "holdout": sorted(TEST_DATES),
            "market_profile": "spot one-minute OHLCV proxy; each bar's volume assigned to typical-price bucket; 70% value area",
            "whale_flow": "proxy only: within-symbol log-volume z-score plus price impact; no trade tape or order book",
        },
        "coverage": {
            "source_events": len(events),
            "enriched_events": len(enriched),
            "selected_before_overlap": len(selected),
            "executable": len(executable),
        },
        "baseline": {"train": metrics(train), "test": metrics(test), "train_blocks": [metrics(block) for block in date_blocks(train)]},
        "rules_tested": len(rules),
        "rules": rules,
        "executable_trades": [
            {key: value for key, value in row.items() if key not in {"large_winner"}}
            for row in executable
        ],
    }
    target = runtime / "pattern_profile_research.json"
    target.write_text(json.dumps(report, indent=2))
    target.chmod(0o600)
    print(json.dumps({
        "report": str(target), "coverage": report["coverage"], "baseline": report["baseline"],
        "top_rules": rules[:15],
    }, indent=2))


if __name__ == "__main__":
    main()
