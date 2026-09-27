from __future__ import annotations

from .chart_history import load_chart_history
from .contracts import OptionContract
from .models import Candle

TIMEFRAMES = {300, 900, 1800}
# Only the widest moneyness spread survives on the chart. ITM/ATM and ATM/OTM
# moved together with it and cost a third of the pane for no extra read.
RATIO_DEFINITIONS = (("ITM", "OTM"),)
RATIO_EMA_PERIOD = 5
ROLE_ORDER = {"ITM": 0, "ATM": 1, "OTM": 2}
SIDE_COLOURS = {
    ("CE", "ITM"): "#15b981",
    ("CE", "ATM"): "#4da3ff",
    ("CE", "OTM"): "#8b7cf6",
    ("PE", "ITM"): "#f15b6c",
    ("PE", "ATM"): "#ffad42",
    ("PE", "OTM"): "#d86bd6",
}


def ema_points(points: list[dict], period: int) -> list[dict]:
    """Seeded EMA over the ratio itself, on the ratio's own timestamps.

    The ratio only exists where both legs printed, so the EMA is smoothed over
    the ratio series rather than over wall-clock time; a leg that stops
    printing pauses the average instead of dragging it toward a stale value.
    """
    alpha = 2.0 / (period + 1)
    running: float | None = None
    result = []
    for point in points:
        running = point["value"] if running is None else running + alpha * (point["value"] - running)
        result.append({"time": point["time"], "value": round(running, 5)})
    return result


def build_ratio_history(
    database_path: str,
    contracts: list[OptionContract],
    timeframe_seconds: int,
    indicator_settings: dict[str, float | int],
    live_history: dict[str, list[Candle]] | None = None,
) -> dict:
    if timeframe_seconds not in TIMEFRAMES:
        raise ValueError("Ratio timeframe must be 5m, 15m or 30m")
    if not contracts:
        raise ValueError("No current-expiry contract ladder is available")

    ordered = sorted(
        contracts,
        key=lambda row: (row.option_type, ROLE_ORDER.get(row.moneyness, 9), row.strike),
    )
    series = []
    closes: dict[tuple[str, str], dict[int, float]] = {}
    for contract in ordered:
        extra = (live_history or {}).get(contract.symbol)
        loaded = load_chart_history(
            database_path,
            contract.symbol,
            timeframe_seconds,
            int(indicator_settings["fast_period"]),
            int(indicator_settings["slow_period"]),
            int(indicator_settings["signal_period"]),
            int(indicator_settings["bb_period"]),
            float(indicator_settings["bb_deviations"]),
            int(indicator_settings["kama_period"]),
            int(indicator_settings["kama_fast"]),
            int(indicator_settings["kama_slow"]),
            int(indicator_settings["kama_rsi_period"]),
            int(indicator_settings["kama_roc_period"]),
            extra,
        )
        points = [
            {"time": candle.timestamp, "value": round(candle.close, 4)}
            for candle in loaded["candles"]
            if candle.close > 0
        ]
        closes[(contract.option_type, contract.moneyness)] = {
            row["time"]: row["value"] for row in points
        }
        series.append({
            "symbol": contract.symbol,
            "side": contract.option_type,
            "moneyness": contract.moneyness,
            "strike": contract.strike,
            "label": f"{contract.option_type} {contract.moneyness} {contract.strike:g}",
            "color": SIDE_COLOURS[(contract.option_type, contract.moneyness)],
            "points": points,
        })

    ratios = []
    for side in ("CE", "PE"):
        for numerator, denominator in RATIO_DEFINITIONS:
            top = closes.get((side, numerator), {})
            bottom = closes.get((side, denominator), {})
            timestamps = sorted(top.keys() & bottom.keys())
            points = [
                {"time": timestamp, "value": round(top[timestamp] / bottom[timestamp], 5)}
                for timestamp in timestamps
                if bottom[timestamp] > 0
            ]
            ratios.append({
                "key": f"{side}_{numerator}_{denominator}",
                "side": side,
                "label": f"{side} {numerator}/{denominator}",
                "numerator": numerator,
                "denominator": denominator,
                "points": points,
                "ema_period": RATIO_EMA_PERIOD,
                "ema": ema_points(points, RATIO_EMA_PERIOD),
            })

    return {
        "spot_symbol": ordered[0].spot_symbol,
        "underlying": ordered[0].underlying,
        "expiry": ordered[0].expiry,
        "timeframe_seconds": timeframe_seconds,
        "contracts": series,
        "ratios": ratios,
        "bars": {row["symbol"]: len(row["points"]) for row in series},
    }
