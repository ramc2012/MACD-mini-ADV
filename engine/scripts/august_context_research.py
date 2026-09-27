"""Indicator CONTEXT, not indicator value, on the August 2026 series.

The first pass asked what RSI, MACD and %B READ at the moment a premium began
doubling. That cannot distinguish the two situations a trader actually cares
about: a MACD crossing up from below zero (a reversal) and one crossing up from
above it (a continuation) report the same "MACD above signal". Neither can a
level tell you whether the low the spot just printed was a HIGHER low or a
LOWER one, whether momentum diverged from price while it happened, or whether
the move was a clean trend or noise that happened to end up in the same place.

So this measures structure:

  crossover PLACE   where in the MACD's own range the cross happened, and how
                    fresh it is
  divergence        regular (reversal) and hidden (continuation), from
                    CONFIRMED swing pivots only
  swing structure   higher-high/higher-low vs lower-high/lower-low, and breaks
                    of structure
  trend line        20-bar regression slope in ATR and its R^2, so a trend can
                    be told apart from a drift
  regime            Kaufman efficiency ratio, ATR percentile, Bollinger squeeze

Every feature is read at a bar's close from data available at that close. Swing
pivots are the trap here: a pivot at bar p is only visible once bar p+k has
printed, so a pivot is admitted only when p + PIVOT_K <= t. Without that the
divergence numbers are lookahead and mean nothing.

    docker compose exec api python /app/scripts/august_context_research.py
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter

sys.path.insert(0, "/app/scripts")
sys.path.insert(0, "/app/src")

from august_runup_research import (  # noqa: E402
    MAX_ABS_MONEYNESS, MIN_ONSET_PREMIUM, OVERLAY_GRID, RUNTIME, SYMBOL,
    bars, connect, forward_trade, runup_episodes, simulate, spot_symbol, zero_cross_bars,
)
from macd_trader.indicators import IncrementalMACD, IncrementalRSI  # noqa: E402
from macd_trader.models import Candle  # noqa: E402

# Bars either side of a swing pivot. 2 is the standard fractal; it also keeps
# the confirmation lag to one hour on a 30-minute chart.
PIVOT_K = 2
REGRESSION_BARS = 20
EFFICIENCY_BARS = 10
REGIME_BARS = 100
DONCHIAN_BARS = 20
FRESH_CROSS_BARS = 3


def linear_fit(values: list[float]) -> tuple[float, float, float]:
    """Slope per bar, fitted value at the last point, and R^2."""
    n = len(values)
    if n < 3:
        return 0.0, values[-1] if values else 0.0, 0.0
    mean_x = (n - 1) / 2
    mean_y = statistics.fmean(values)
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    sxy = sum((i - mean_x) * (values[i] - mean_y) for i in range(n))
    if sxx == 0:
        return 0.0, mean_y, 0.0
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    fitted_last = intercept + slope * (n - 1)
    total = sum((value - mean_y) ** 2 for value in values)
    residual = sum((values[i] - (intercept + slope * i)) ** 2 for i in range(n))
    r_squared = 0.0 if total == 0 else max(0.0, 1 - residual / total)
    return slope, fitted_last, r_squared


def efficiency_ratio(closes: list[float]) -> float:
    """Kaufman's ER: net travel over gross travel. 1 = clean trend, 0 = noise."""
    if len(closes) < 2:
        return 0.0
    gross = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return 0.0 if gross == 0 else abs(closes[-1] - closes[0]) / gross


def percentile_rank(window: list[float], value: float) -> float:
    if not window:
        return 0.5
    return sum(1 for item in window if item <= value) / len(window)


def confirmed_pivots(rows: list[Candle], k: int = PIVOT_K) -> tuple[list, list]:
    """Swing highs and lows as (confirm_index, pivot_index, price).

    confirm_index is the first bar at which the pivot is knowable. Nothing
    downstream may look at a pivot before its confirm_index.
    """
    highs, lows = [], []
    for i in range(k, len(rows) - k):
        window = rows[i - k: i + k + 1]
        centre = rows[i]
        if centre.high >= max(row.high for row in window):
            highs.append((i + k, i, centre.high))
        if centre.low <= min(row.low for row in window):
            lows.append((i + k, i, centre.low))
    return highs, lows


def context_series(rows: list[Candle]) -> list[dict]:
    """Structural context for every bar of a 30-minute series."""
    macd_state = IncrementalMACD(12, 26, 9)
    rsi_state = IncrementalRSI(14)
    highs, lows = confirmed_pivots(rows)
    high_cursor = low_cursor = 0
    live_highs: list[tuple[int, float]] = []   # (pivot_index, price)
    live_lows: list[tuple[int, float]] = []
    rsi_at: dict[int, float] = {}
    macd_at: dict[int, float] = {}

    closes: list[float] = []
    true_ranges: list[float] = []
    atr_history: list[float] = []
    previous_close = None
    previous_hist = None
    last_cross: dict | None = None
    hist_streak_up = hist_streak_down = 0
    out: list[dict] = []

    for index, candle in enumerate(rows):
        closes.append(candle.close)
        true_range = max(candle.high - candle.low,
                         abs(candle.high - (previous_close or candle.close)),
                         abs(candle.low - (previous_close or candle.close)))
        true_ranges.append(true_range)
        previous_close = candle.close
        atr = statistics.fmean(true_ranges[-14:]) if len(true_ranges) >= 14 else None
        if atr:
            atr_history.append(atr)

        macd_value = macd_state.update(candle.close)
        rsi_value = rsi_state.update(candle.close)
        rsi_at[index] = rsi_value if rsi_value is not None else 50.0
        macd_at[index] = macd_value.macd

        # --- crossover place -------------------------------------------
        difference = macd_value.macd - macd_value.signal
        if previous_hist is not None and (previous_hist <= 0 < difference):
            last_cross = {"index": index, "direction": 1, "macd": macd_value.macd}
        elif previous_hist is not None and (previous_hist >= 0 > difference):
            last_cross = {"index": index, "direction": -1, "macd": macd_value.macd}
        if previous_hist is not None:
            if difference > previous_hist:
                hist_streak_up, hist_streak_down = hist_streak_up + 1, 0
            elif difference < previous_hist:
                hist_streak_down, hist_streak_up = hist_streak_down + 1, 0
        previous_hist = difference

        # --- admit pivots this bar confirms ----------------------------
        while high_cursor < len(highs) and highs[high_cursor][0] <= index:
            live_highs.append((highs[high_cursor][1], highs[high_cursor][2]))
            high_cursor += 1
        while low_cursor < len(lows) and lows[low_cursor][0] <= index:
            live_lows.append((lows[low_cursor][1], lows[low_cursor][2]))
            low_cursor += 1

        higher_high = lower_high = higher_low = lower_low = None
        if len(live_highs) >= 2:
            (_, previous_high), (_, latest_high) = live_highs[-2], live_highs[-1]
            higher_high, lower_high = latest_high > previous_high, latest_high < previous_high
        if len(live_lows) >= 2:
            (_, previous_low), (_, latest_low) = live_lows[-2], live_lows[-1]
            higher_low, lower_low = latest_low > previous_low, latest_low < previous_low

        # --- divergence on the last two confirmed pivots ---------------
        regular_bull = hidden_bull = regular_bear = hidden_bear = False
        macd_regular_bull = macd_regular_bear = False
        if len(live_lows) >= 2:
            (first_index, first_price), (second_index, second_price) = live_lows[-2], live_lows[-1]
            first_rsi, second_rsi = rsi_at.get(first_index, 50.0), rsi_at.get(second_index, 50.0)
            first_macd, second_macd = macd_at.get(first_index, 0.0), macd_at.get(second_index, 0.0)
            if second_price < first_price and second_rsi > first_rsi:
                regular_bull = True
            if second_price > first_price and second_rsi < first_rsi:
                hidden_bull = True
            if second_price < first_price and second_macd > first_macd:
                macd_regular_bull = True
        if len(live_highs) >= 2:
            (first_index, first_price), (second_index, second_price) = live_highs[-2], live_highs[-1]
            first_rsi, second_rsi = rsi_at.get(first_index, 50.0), rsi_at.get(second_index, 50.0)
            first_macd, second_macd = macd_at.get(first_index, 0.0), macd_at.get(second_index, 0.0)
            if second_price > first_price and second_rsi < first_rsi:
                regular_bear = True
            if second_price < first_price and second_rsi > first_rsi:
                hidden_bear = True
            if second_price > first_price and second_macd < first_macd:
                macd_regular_bear = True

        # --- trend line and regime -------------------------------------
        window = closes[-REGRESSION_BARS:]
        slope, fitted, r_squared = linear_fit(window)
        efficiency = efficiency_ratio(closes[-(EFFICIENCY_BARS + 1):])
        donchian = rows[max(0, index - DONCHIAN_BARS + 1): index + 1]
        top = max(row.high for row in donchian)
        floor = min(row.low for row in donchian)
        span = (top - floor) or None

        out.append({
            "index": index,
            "timestamp": candle.timestamp,
            "close": candle.close,
            "atr": atr,
            "macd": macd_value.macd,
            "macd_above_zero": macd_value.macd > 0,
            "hist": difference,
            "hist_streak_up": hist_streak_up,
            "hist_streak_down": hist_streak_down,
            "cross_direction": (last_cross or {}).get("direction"),
            "cross_age": None if last_cross is None else index - last_cross["index"],
            "cross_macd": (last_cross or {}).get("macd"),
            "rsi": rsi_value,
            "higher_high": higher_high, "lower_high": lower_high,
            "higher_low": higher_low, "lower_low": lower_low,
            "last_swing_high": live_highs[-1][1] if live_highs else None,
            "last_swing_low": live_lows[-1][1] if live_lows else None,
            "regular_bull": regular_bull, "hidden_bull": hidden_bull,
            "regular_bear": regular_bear, "hidden_bear": hidden_bear,
            "macd_regular_bull": macd_regular_bull, "macd_regular_bear": macd_regular_bear,
            "slope_atr": (slope / atr) if atr else 0.0,
            "r_squared": r_squared,
            "residual_atr": ((candle.close - fitted) / atr) if atr else 0.0,
            "efficiency": efficiency,
            "atr_percentile": percentile_rank(atr_history[-REGIME_BARS:], atr) if atr else 0.5,
            "donchian_position": None if not span else (candle.close - floor) / span,
        })
    return out


def context_conditions(spot: dict, option: dict, sign: float) -> dict[str, bool]:
    """Structural setup labels, direction-adjusted so CE and PE pool together."""
    bullish = sign > 0

    def favour(flag_up, flag_down):
        return bool(flag_up if bullish else flag_down)

    structure_with = favour(spot["higher_high"] and spot["higher_low"],
                            spot["lower_high"] and spot["lower_low"])
    structure_against = favour(spot["lower_high"] and spot["lower_low"],
                               spot["higher_high"] and spot["higher_low"])
    swing_high, swing_low = spot["last_swing_high"], spot["last_swing_low"]
    broke_with = favour(swing_high is not None and spot["close"] > swing_high,
                        swing_low is not None and spot["close"] < swing_low)
    broke_against = favour(swing_low is not None and spot["close"] < swing_low,
                           swing_high is not None and spot["close"] > swing_high)

    cross_in_favour = spot["cross_direction"] == (1 if bullish else -1)
    fresh = (spot["cross_age"] is not None and spot["cross_age"] <= FRESH_CROSS_BARS)
    cross_macd = spot["cross_macd"]
    # A cross that happens on the far side of zero is a REVERSAL cross; one on
    # the near side is a continuation. Same "MACD above signal", different trade.
    reversal_cross = (cross_in_favour and fresh and cross_macd is not None
                      and (cross_macd < 0 if bullish else cross_macd > 0))
    continuation_cross = (cross_in_favour and fresh and cross_macd is not None
                          and (cross_macd > 0 if bullish else cross_macd < 0))

    slope = spot["slope_atr"] * sign
    residual = spot["residual_atr"] * sign
    donchian = spot["donchian_position"]
    if donchian is not None and not bullish:
        donchian = 1 - donchian
    option_slope = option["slope_atr"]
    option_rsi = option["rsi"] or 50.0

    return {
        "all eligible bars": True,

        # --- crossover PLACE ---------------------------------------
        "fresh MACD cross in favour (<=3 bars)": cross_in_favour and fresh,
        "  ... from the FAR side of zero (reversal)": reversal_cross,
        "  ... from the NEAR side of zero (continuation)": continuation_cross,
        "MACD already beyond zero in favour": spot["macd_above_zero"] == bullish,
        "histogram building >=3 bars in favour": (spot["hist_streak_up"] if bullish
                                                  else spot["hist_streak_down"]) >= 3,

        # --- divergence --------------------------------------------
        "regular divergence in favour (reversal)": favour(spot["regular_bull"], spot["regular_bear"]),
        "  ... confirmed on MACD too": favour(spot["macd_regular_bull"], spot["macd_regular_bear"]),
        "hidden divergence in favour (continuation)": favour(spot["hidden_bull"], spot["hidden_bear"]),
        "regular divergence AGAINST": favour(spot["regular_bear"], spot["regular_bull"]),

        # --- swing structure ---------------------------------------
        "swing structure with the option (HH+HL)": structure_with,
        "swing structure against (LH+LL)": structure_against,
        "break of structure in favour": broke_with,
        "break of structure against": broke_against,
        "structure against BUT regular divergence in favour": (
            structure_against and favour(spot["regular_bull"], spot["regular_bear"])),

        # --- trend line --------------------------------------------
        "trend line sloping with, R2 >= 0.6": slope >= 0.05 and spot["r_squared"] >= 0.6,
        "trend line sloping with, R2 < 0.3 (drift)": slope >= 0.05 and spot["r_squared"] < 0.3,
        "trend line sloping against, R2 >= 0.6": slope <= -0.05 and spot["r_squared"] >= 0.6,
        "price >= 1 ATR below its own trend line": residual <= -1.0,
        "price >= 1 ATR above its own trend line": residual >= 1.0,

        # --- regime ------------------------------------------------
        "efficiency ratio >= 0.5 (clean trend)": spot["efficiency"] >= 0.5,
        "efficiency ratio <= 0.2 (chop)": spot["efficiency"] <= 0.2,
        "ATR in its own top quartile": spot["atr_percentile"] >= 0.75,
        "ATR in its own bottom quartile (squeeze)": spot["atr_percentile"] <= 0.25,
        "Donchian(20) position <= 0.2 in favour": donchian is not None and donchian <= 0.2,
        "Donchian(20) position >= 0.8 in favour": donchian is not None and donchian >= 0.8,

        # --- the option's own structure ----------------------------
        "premium making higher lows": bool(option["higher_low"]),
        "premium making lower lows": bool(option["lower_low"]),
        "premium MACD beyond zero": option["macd_above_zero"],
        "premium trend line rising, R2 >= 0.6": option_slope >= 0.05 and option["r_squared"] >= 0.6,
        "premium regular bullish divergence": bool(option["regular_bull"]),
        "premium RSI <= 40": option_rsi <= 40,
        "premium RSI >= 60": option_rsi >= 60,
        "premium efficiency ratio >= 0.5": option["efficiency"] >= 0.5,

        # --- combinations worth a look -----------------------------
        "reversal cross AND regular divergence in favour": (
            reversal_cross and favour(spot["regular_bull"], spot["regular_bear"])),
        "continuation cross AND structure with": continuation_cross and structure_with,
        "BOS in favour AND efficiency >= 0.5": broke_with and spot["efficiency"] >= 0.5,
        "premium higher lows AND spot structure with": bool(option["higher_low"]) and structure_with,
    }


def main() -> int:
    db = connect()
    symbols = sorted(r[0] for r in db.execute(
        "SELECT DISTINCT symbol FROM historical_candles WHERE symbol LIKE '%26AUG%'"))
    print(f"August series: {len(symbols)} contracts", flush=True)

    spot_cache: dict[str, list[dict]] = {}
    books = {
        name: {"trials": Counter(), "wins": Counter(), "trades": Counter(),
               "winners": Counter(), "total": Counter(), "profit": Counter(),
               "loss": Counter(),
               # Sum of squares, so an uplift can be reported with a standard
               # error. Per-trade dispersion here is tens of percent; without
               # this a "+1.5% edge" on 900 trades cannot be told from noise.
               "square": Counter()}
        for name in ("all", "cross")
    }
    grid = {"trades": Counter(), "winners": Counter(), "total": Counter(),
            "profit": Counter(), "loss": Counter()}
    best_examples: list[dict] = []
    # The exit sweep compares the SAME trades under different rules, so it is a
    # PAIRED comparison and far tighter than the unpaired condition contrasts.
    # Capture the per-trade difference so the two can be reported on the same
    # footing instead of being eyeballed against each other.
    paired = {"n": 0, "sum": 0.0, "square": 0.0, "better": 0}
    scanned = 0

    for position, symbol in enumerate(symbols, 1):
        parsed = SYMBOL.match(symbol)
        if not parsed:
            continue
        option_bars = bars(db, symbol)
        if len(option_bars) < 40:
            continue
        scanned += 1
        underlying = spot_symbol(parsed["ex"], parsed["root"])
        if underlying not in spot_cache:
            spot_cache[underlying] = context_series(bars(db, underlying))
        spot_rows = spot_cache[underlying]
        if not spot_rows:
            continue
        spot_by_time = {row["timestamp"]: row for row in spot_rows}
        option_rows = context_series(option_bars)
        crossings = zero_cross_bars(option_bars)
        onsets = {episode["onset_timestamp"] for episode in runup_episodes(option_bars)}

        side = parsed["side"]
        strike = float(parsed["strike"])
        sign = 1.0 if side == "CE" else -1.0

        for index, candle in enumerate(option_bars):
            if candle.close < MIN_ONSET_PREMIUM:
                continue
            spot_row = spot_by_time.get(candle.timestamp)
            if spot_row is None or spot_row["atr"] in (None, 0):
                continue
            if abs((spot_row["close"] - strike) / strike * sign) > MAX_ABS_MONEYNESS:
                continue
            option_row = option_rows[index]
            if option_row["atr"] in (None, 0):
                continue
            won = candle.timestamp in onsets
            outcome = forward_trade(option_bars, index)
            labels = context_conditions(spot_row, option_row, sign)
            names = ["all"] + (["cross"] if index in crossings else [])
            for name in names:
                book = books[name]
                for label, holds in labels.items():
                    if not holds:
                        continue
                    book["trials"][label] += 1
                    if won:
                        book["wins"][label] += 1
                    if outcome is None:
                        continue
                    value = outcome["return_pct"]
                    book["trades"][label] += 1
                    book["total"][label] += value
                    book["square"][label] += value * value
                    if value > 0:
                        book["profit"][label] += value
                        book["winners"][label] += 1
                    else:
                        book["loss"][label] += -value
            if index in crossings:
                live = simulate(option_bars, index, 0.30, 0.30, 0.25, 26)
                tight = simulate(option_bars, index, 0.15, 0.20, 0.10, 13)
                if live is not None and tight is not None:
                    difference = tight - live
                    paired["n"] += 1
                    paired["sum"] += difference
                    paired["square"] += difference * difference
                    paired["better"] += 1 if difference > 0 else 0
                for overlay in OVERLAY_GRID:
                    value = simulate(option_bars, index, *overlay)
                    if value is None:
                        continue
                    key = "|".join(str(int(item * 100)) for item in overlay[:3]) + f"|{overlay[3]}"
                    for label, holds in labels.items():
                        if not holds:
                            continue
                        combined = f"{label}@@{key}"
                        grid["trades"][combined] += 1
                        grid["total"][combined] += value
                        if value > 0:
                            grid["profit"][combined] += value
                            grid["winners"][combined] += 1
                        else:
                            grid["loss"][combined] += -value
            if won and outcome and outcome["return_pct"] > 60:
                best_examples.append({
                    "symbol": symbol, "timestamp": candle.timestamp,
                    "return_pct": outcome["return_pct"],
                    "labels": sorted(k for k, v in labels.items() if v and k != "all eligible bars"),
                })
        if position % 200 == 0:
            print(f"  {position}/{len(symbols)} contracts", flush=True)

    payload = {
        "contracts_scanned": scanned,
        "pivot_k": PIVOT_K,
        "books": {name: {key: dict(counter) for key, counter in book.items()}
                  for name, book in books.items()},
        "grid": {key: dict(counter) for key, counter in grid.items()},
        "paired_exit": paired,
        "examples": sorted(best_examples, key=lambda row: -row["return_pct"])[:60],
    }
    out = RUNTIME / "august_context_research.json"
    out.write_text(json.dumps(payload))
    print(f"\nscanned {scanned} contracts")
    print(f"eligible bars: {books['all']['trials']['all eligible bars']:,} "
          f"(zero-cross {books['cross']['trials']['all eligible bars']:,})")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
