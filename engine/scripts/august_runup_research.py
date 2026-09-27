"""Which August-series options ran more than 100%, and what the spot looked like when they started.

The question this answers is deliberately NOT "did the strategy catch it" --
it is "when a premium was about to double, what was the underlying already
doing". So episodes are found on the option's own premium series with no
reference to any signal, and every measured feature is read off the SPOT bar
that closed at the episode's onset, i.e. information available before the move.

    docker compose exec api python /app/scripts/august_runup_research.py

Writes runtime/august_runup_research.json.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app/src")

from macd_trader.chart_history import aggregate_session_candles
from macd_trader.indicators import (
    IncrementalBollingerBands, IncrementalKAMA, IncrementalMACD, IncrementalROC, IncrementalRSI,
)
from macd_trader.models import Candle

IST = ZoneInfo("Asia/Kolkata")
RUNTIME = Path("/app/runtime")
# Research reads a snapshot, never the file the live writer owns: a long
# read holds a shared lock, and the engine's indicator writes time out
# after 30s -- which during warm-up costs contracts their indicator state.
SNAPSHOT = RUNTIME / "research_snapshot.sqlite3"
DATABASE = SNAPSHOT if SNAPSHOT.exists() else RUNTIME / "historical.sqlite3"
TIMEFRAME = 1800
EXPIRY = datetime(2026, 8, 25, 15, 30, tzinfo=IST)

# A double from 0.40 to 0.85 is arithmetic, not a trade: it cannot be entered
# at size, the spread eats it, and on expiry day there are hundreds of them.
MIN_ONSET_PREMIUM = 5.0
RUNUP_THRESHOLD = 1.0          # +100%
# Contracts are ATM-ish by construction; a strike miles away is a different
# instrument entirely and its "return" is a lottery payout.
MAX_ABS_MONEYNESS = 0.20

SYMBOL = re.compile(r"^(?P<ex>NSE|BSE):(?P<root>[A-Z0-9&\-]+?)26AUG(?P<strike>\d+(?:\.\d+)?)(?P<side>CE|PE)$")


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    db.execute("PRAGMA busy_timeout=120000")
    return db


def minute_rows(db: sqlite3.Connection, symbol: str) -> list[Candle]:
    rows = db.execute(
        "SELECT timestamp,open,high,low,close,volume FROM historical_candles "
        "WHERE symbol=? AND timeframe_seconds=60 ORDER BY timestamp", (symbol,)).fetchall()
    return [Candle(symbol, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5]), True)
            for r in rows]


def bars(db: sqlite3.Connection, symbol: str) -> list[Candle]:
    return aggregate_session_candles(minute_rows(db, symbol), TIMEFRAME)


def spot_symbol(exchange: str, root: str) -> str:
    from macd_trader.universe import INDEX_SPOTS
    return INDEX_SPOTS.get(root) or f"{exchange}:{root}-EQ"


def spot_features(rows: list[Candle]) -> list[dict]:
    """Every 30-minute spot bar, annotated with what was knowable at its close."""
    macd = IncrementalMACD(12, 26, 9)
    boll = IncrementalBollingerBands(20, 2.0)
    kama = IncrementalKAMA(10, 2, 30)
    kama_rsi, kama_roc = IncrementalRSI(14), IncrementalROC(5)
    price_rsi = IncrementalRSI(14)
    ema = {span: None for span in (9, 20, 50)}
    closes: list[float] = []
    true_ranges: list[float] = []
    volumes: list[int] = []
    day_open: dict[str, float] = {}
    day_high: dict[str, float] = {}
    day_low: dict[str, float] = {}
    out: list[dict] = []
    previous_close = None

    for candle in rows:
        moment = datetime.fromtimestamp(candle.timestamp, UTC).astimezone(IST)
        day = moment.date().isoformat()
        day_open.setdefault(day, candle.open)
        day_high[day] = max(day_high.get(day, candle.high), candle.high)
        day_low[day] = min(day_low.get(day, candle.low), candle.low)

        closes.append(candle.close)
        volumes.append(candle.volume)
        true_range = max(candle.high - candle.low,
                         abs(candle.high - (previous_close or candle.close)),
                         abs(candle.low - (previous_close or candle.close)))
        true_ranges.append(true_range)
        previous_close = candle.close

        for span in ema:
            alpha = 2 / (span + 1)
            ema[span] = candle.close if ema[span] is None else ema[span] + alpha * (candle.close - ema[span])

        macd_value = macd.update(candle.close)
        band = boll.update(candle.close)
        kama_value = kama.update(candle.close)
        rsi_of_kama = kama_rsi.update(kama_value) if kama_value is not None else None
        roc_of_kama = kama_roc.update(kama_value) if kama_value is not None else None
        rsi_of_price = price_rsi.update(candle.close)
        atr = statistics.fmean(true_ranges[-14:]) if len(true_ranges) >= 14 else None
        average_volume = statistics.fmean(volumes[-20:]) if len(volumes) >= 20 else None
        session_range = (day_high[day] - day_low[day]) or None

        out.append({
            "timestamp": candle.timestamp,
            "close": candle.close,
            "ema9": ema[9], "ema20": ema[20], "ema50": ema[50],
            "sma20": statistics.fmean(closes[-20:]) if len(closes) >= 20 else None,
            "sma50": statistics.fmean(closes[-50:]) if len(closes) >= 50 else None,
            "macd": macd_value.macd, "macd_signal": macd_value.signal,
            "macd_hist": macd_value.histogram,
            "rsi14": rsi_of_price,
            "kama": kama_value, "kama_rsi": rsi_of_kama, "kama_roc": roc_of_kama,
            "bb_percent_b": (None if not band.upper or band.upper == band.lower
                             else (candle.close - band.lower) / (band.upper - band.lower)),
            "bb_width_pct": (None if not band.middle else 100 * (band.upper - band.lower) / band.middle),
            "atr_pct": (None if not atr or not candle.close else 100 * atr / candle.close),
            "atr": atr,
            "volume_ratio": (None if not average_volume else candle.volume / average_volume),
            "from_day_open_pct": 100 * (candle.close / day_open[day] - 1) if day_open[day] else None,
            "day_range_position": (None if not session_range
                                   else (candle.close - day_low[day]) / session_range),
        })
    return out


def runup_episodes(rows: list[Candle]) -> list[dict]:
    """Distinct >=100% advances that could actually have been taken.

    The first cut of this measured trough LOW to later HIGH, which reported
    NSE:INFY26AUG1140CE at +2,875% -- an opening 30-minute bar whose low was
    an illiquid 0.40 print and whose high was in the SAME bar. Nobody trades
    that. So the advance is measured from the trough bar's CLOSE, which is a
    price you could pay, to the best HIGH in a LATER bar, which is a price you
    could sell into. Same-bar spikes are excluded by construction.

    The running minimum resets at every recorded peak, so one sustained
    advance is one episode rather than one per bar inside it.
    """
    episodes: list[dict] = []
    if not rows:
        return episodes
    trough_index, trough_close = 0, rows[0].close
    best_high, best_index = None, None
    for index in range(1, len(rows)):
        candle = rows[index]
        if best_high is None or candle.high > best_high:
            best_high, best_index = candle.high, index
        if trough_close > 0 and best_high / trough_close - 1 >= RUNUP_THRESHOLD:
            onset = rows[trough_index]
            episodes.append({
                "onset_index": trough_index,
                "onset_timestamp": onset.timestamp,
                "onset_close": trough_close,
                "peak_timestamp": rows[best_index].timestamp,
                "peak_high": best_high,
                "tradable_pct": 100 * (best_high / trough_close - 1),
                "bars_to_peak": best_index - trough_index,
                # How far it kept running after first clearing +100%.
                "at_threshold_bars": index - trough_index,
            })
            trough_index, trough_close = index, candle.close
            best_high, best_index = None, None
            continue
        if candle.close < trough_close:
            trough_index, trough_close = index, candle.close
            best_high, best_index = None, None
    return episodes


HOLD_BARS = 26            # two sessions of 30-minute bars
HARD_STOP = 0.30
TRAIL_ACTIVATION = 0.30
TRAIL = 0.25


def forward_trade(rows: list[Candle], index: int) -> dict | None:
    """What the live exit rules would have produced from this bar's close.

    A hit rate answers "how often did a double start here", which is not the
    same question as "was buying here worth it": 97% of bars start no double,
    and the 30% hard stop is paid on most of them. This runs the terminal's
    ACTUAL overlay -- 30% hard stop, and after +30% a 25%-from-peak trail --
    so each condition gets an expectancy, not just a frequency.

    Intrabar order is resolved pessimistically: a bar that touches both the
    stop and a new high is treated as having hit the stop first.
    """
    entry = rows[index].close
    if entry <= 0:
        return None
    stop = entry * (1 - HARD_STOP)
    peak = entry
    trail_stop = None
    for step in range(index + 1, min(len(rows), index + 1 + HOLD_BARS)):
        bar = rows[step]
        if bar.low <= stop:
            return {"return_pct": 100 * (stop / entry - 1), "reason": "hard_stop", "bars": step - index}
        if trail_stop is not None and bar.low <= trail_stop:
            return {"return_pct": 100 * (trail_stop / entry - 1), "reason": "trail", "bars": step - index}
        if bar.high > peak:
            peak = bar.high
            if peak >= entry * (1 + TRAIL_ACTIVATION):
                trail_stop = max(trail_stop or 0.0, peak * (1 - TRAIL))
    last = rows[min(len(rows) - 1, index + HOLD_BARS)]
    if last.timestamp <= rows[index].timestamp:
        return None
    return {"return_pct": 100 * (last.close / entry - 1), "reason": "timeout", "bars": HOLD_BARS}


OVERLAY_GRID = [
    (stop, activation, trail, hold)
    for stop in (0.10, 0.15, 0.20, 0.25, 0.30)
    for activation, trail in ((0.20, 0.10), (0.20, 0.15), (0.30, 0.25))
    for hold in (13, 26)
]


def simulate(rows: list[Candle], index: int, stop_pct: float, activation: float,
             trail_pct: float, hold: int) -> float | None:
    """forward_trade with the overlay as parameters, for the sensitivity grid."""
    entry = rows[index].close
    if entry <= 0:
        return None
    stop = entry * (1 - stop_pct)
    peak, trail_stop = entry, None
    for step in range(index + 1, min(len(rows), index + 1 + hold)):
        bar = rows[step]
        if bar.low <= stop:
            return 100 * (stop / entry - 1)
        if trail_stop is not None and bar.low <= trail_stop:
            return 100 * (trail_stop / entry - 1)
        if bar.high > peak:
            peak = bar.high
            if peak >= entry * (1 + activation):
                trail_stop = max(trail_stop or 0.0, peak * (1 - trail_pct))
    last = rows[min(len(rows) - 1, index + hold)]
    return None if last.timestamp <= rows[index].timestamp else 100 * (last.close / entry - 1)


def zero_cross_bars(rows: list[Candle]) -> set[int]:
    """Bars where the OPTION's own MACD crosses up through zero.

    The all-bars population answers "is this conditioning signal pointed the
    right way", but it is not what the terminal trades. The terminal enters on
    a premium MACD upward zero-cross, so the conditions have to be re-measured
    on exactly that population before any of this can change a setting.
    """
    macd = IncrementalMACD(12, 26, 9)
    crossings: set[int] = set()
    previous = None
    for index, candle in enumerate(rows):
        value = macd.update(candle.close).macd
        if previous is not None and previous <= 0 < value:
            crossings.add(index)
        previous = value
    return crossings


def conditions(spot: dict, sign: float) -> dict[str, bool]:
    """The setup labels, direction-adjusted so CE and PE pool together.

    These are evaluated on EVERY eligible option bar, not only on the ones a
    doubling followed, because "where the winners started" is a description of
    winners, not a probability. Only trials and wins together give the rate.
    """
    atr = spot["atr"] or 0.0
    aligned = lambda value: sign * value  # noqa: E731
    percent_b = spot["bb_percent_b"]
    if percent_b is not None and sign < 0:
        percent_b = 1 - percent_b
    day_position = spot["day_range_position"]
    if day_position is not None and sign < 0:
        day_position = 1 - day_position
    rsi = spot["rsi14"]
    if rsi is not None and sign < 0:
        rsi = 100 - rsi
    kama_rsi = spot["kama_rsi"]
    if kama_rsi is not None and sign < 0:
        kama_rsi = 100 - kama_rsi
    ema20_atr = aligned(spot["close"] - spot["ema20"]) / atr if atr else 0.0
    ema9_gap = aligned(spot["ema9"] - spot["ema20"]) / atr if atr else 0.0
    return {
        "all eligible bars": True,
        "spot >= 1 ATR BELOW EMA20 (against the option)": ema20_atr <= -1.0,
        "spot >= 1 ATR ABOVE EMA20 (with the option)": ema20_atr >= 1.0,
        "spot within +-0.5 ATR of EMA20": abs(ema20_atr) <= 0.5,
        "EMA9 separated from EMA20 by >= 0.5 ATR (with)": ema9_gap >= 0.5,
        "spot MACD histogram positive (with)": aligned(spot["macd_hist"]) > 0,
        "spot MACD histogram negative (against)": aligned(spot["macd_hist"]) < 0,
        "RSI(14) <= 40 (against)": rsi is not None and rsi <= 40,
        "RSI(14) >= 55 (with)": rsi is not None and rsi >= 55,
        "KAMA-RSI >= 65 (with) -- the live gate": kama_rsi is not None and kama_rsi >= 65,
        "KAMA-RSI <= 35 (against)": kama_rsi is not None and kama_rsi <= 35,
        "Bollinger %B <= 0.2 (at the low)": percent_b is not None and percent_b <= 0.2,
        "Bollinger %B >= 0.8 (at the high)": percent_b is not None and percent_b >= 0.8,
        "lower third of the day's range (against)": day_position is not None and day_position <= 0.33,
        "upper third of the day's range (with)": day_position is not None and day_position >= 0.67,
        "spot volume >= 1.5x its 20-bar average": (spot["volume_ratio"] or 0) >= 1.5,
        "spot ATR >= 0.8% of price": (spot["atr_pct"] or 0) >= 0.8,
        "%B <= 0.2 AND volume >= 1.5x": (percent_b is not None and percent_b <= 0.2
                                         and (spot["volume_ratio"] or 0) >= 1.5),
        "%B <= 0.2 AND spot <= -1 ATR vs EMA20": (percent_b is not None and percent_b <= 0.2
                                                  and ema20_atr <= -1.0),
    }


def main() -> int:
    db = connect()
    symbols = sorted(r[0] for r in db.execute(
        "SELECT DISTINCT symbol FROM historical_candles WHERE symbol LIKE '%26AUG%'"))
    print(f"August series: {len(symbols)} contracts", flush=True)

    spot_cache: dict[str, list[dict]] = {}
    episodes: list[dict] = []
    baseline: list[dict] = []
    seen_spots: set[str] = set()
    trials: Counter = Counter()
    wins: Counter = Counter()
    traded: Counter = Counter()
    winners: Counter = Counter()
    total_return: Counter = Counter()
    profit: Counter = Counter()
    loss: Counter = Counter()
    # The same books again, restricted to premium MACD zero-cross bars.
    x_trials: Counter = Counter()
    x_wins: Counter = Counter()
    x_traded: Counter = Counter()
    x_winners: Counter = Counter()
    x_total_return: Counter = Counter()
    x_profit: Counter = Counter()
    x_loss: Counter = Counter()
    grid_trades: Counter = Counter()
    grid_winners: Counter = Counter()
    grid_return: Counter = Counter()
    grid_profit: Counter = Counter()
    grid_loss: Counter = Counter()
    scanned = skipped = 0

    for position, symbol in enumerate(symbols, 1):
        parsed = SYMBOL.match(symbol)
        if not parsed:
            skipped += 1
            continue
        option_bars = bars(db, symbol)
        if len(option_bars) < 20:
            skipped += 1
            continue
        scanned += 1
        underlying = spot_symbol(parsed["ex"], parsed["root"])
        if underlying not in spot_cache:
            spot_cache[underlying] = spot_features(bars(db, underlying))
        spot_rows = spot_cache[underlying]
        if not spot_rows:
            continue
        by_time = {row["timestamp"]: row for row in spot_rows}
        if underlying not in seen_spots:
            seen_spots.add(underlying)
            baseline.extend(row for row in spot_rows if row["ema50"] is not None)

        side = parsed["side"]
        strike = float(parsed["strike"])
        sign = 1.0 if side == "CE" else -1.0

        index_by_stamp = {bar.timestamp: position for position, bar in enumerate(option_bars)}
        crossings = zero_cross_bars(option_bars)
        onset_stamps = set()
        found = runup_episodes(option_bars)
        for episode in found:
            onset_stamps.add(episode["onset_timestamp"])
        for bar in option_bars:
            if bar.close < MIN_ONSET_PREMIUM:
                continue
            spot_row = by_time.get(bar.timestamp)
            if spot_row is None or spot_row["ema50"] is None or spot_row["atr"] in (None, 0):
                continue
            if abs((spot_row["close"] - strike) / strike * sign) > MAX_ABS_MONEYNESS:
                continue
            position = index_by_stamp[bar.timestamp]
            won = bar.timestamp in onset_stamps
            outcome = forward_trade(option_bars, position)
            books = [(trials, wins, traded, winners, total_return, profit, loss)]
            if position in crossings:
                for overlay in OVERLAY_GRID:
                    value = simulate(option_bars, position, *overlay)
                    if value is None:
                        continue
                    key = f"{int(overlay[0]*100)}|{int(overlay[1]*100)}|{int(overlay[2]*100)}|{overlay[3]}"
                    grid_trades[key] += 1
                    grid_return[key] += value
                    if value > 0:
                        grid_profit[key] += value
                        grid_winners[key] += 1
                    else:
                        grid_loss[key] += -value
                books.append((x_trials, x_wins, x_traded, x_winners,
                              x_total_return, x_profit, x_loss))
            for label, holds in conditions(spot_row, sign).items():
                if not holds:
                    continue
                for book in books:
                    counted, doubled, dealt, won_count, summed, up, down = book
                    counted[label] += 1
                    if won:
                        doubled[label] += 1
                    if outcome is None:
                        continue
                    value = outcome["return_pct"]
                    dealt[label] += 1
                    summed[label] += value
                    if value > 0:
                        up[label] += value
                        won_count[label] += 1
                    else:
                        down[label] += -value

        for episode in found:
            if episode["onset_close"] < MIN_ONSET_PREMIUM:
                continue
            spot_row = by_time.get(episode["onset_timestamp"])
            if spot_row is None or spot_row["ema50"] is None or spot_row["atr"] in (None, 0):
                continue
            moneyness = (spot_row["close"] - strike) / strike * sign
            if abs(moneyness) > MAX_ABS_MONEYNESS:
                continue
            onset = datetime.fromtimestamp(episode["onset_timestamp"], UTC).astimezone(IST)
            episodes.append({
                "symbol": symbol, "underlying": underlying, "root": parsed["root"], "side": side,
                "strike": strike,
                "onset_ist": onset.isoformat(),
                # Raw bar stamps so a later pass can line the episode up
                # against the spot and the index without re-deriving them.
                "onset_timestamp": episode["onset_timestamp"],
                "peak_timestamp": episode["peak_timestamp"],
                "onset_date": onset.date().isoformat(),
                "days_to_expiry": (EXPIRY - onset).total_seconds() / 86400,
                "onset_premium": episode["onset_close"],
                "onset_time_ist": onset.strftime("%H:%M"),
                "peak_premium": episode["peak_high"],
                "tradable_pct": episode["tradable_pct"],
                "bars_to_peak": episode["bars_to_peak"],
                "hours_to_peak": episode["bars_to_peak"] * TIMEFRAME / 3600,
                "moneyness_pct": 100 * moneyness,
                "spot": spot_row,
                # Direction-adjusted: a PE run-up needs the spot FALLING, so
                # every directional reading is flipped to "in favour of the
                # option" and CE/PE episodes can be pooled.
                "aligned": {
                    "close_vs_ema20_atr": sign * (spot_row["close"] - spot_row["ema20"]) / spot_row["atr"],
                    "close_vs_ema50_atr": sign * (spot_row["close"] - spot_row["ema50"]) / spot_row["atr"],
                    "ema9_vs_ema20_atr": sign * (spot_row["ema9"] - spot_row["ema20"]) / spot_row["atr"],
                    "close_vs_ema20_pct": sign * 100 * (spot_row["close"] / spot_row["ema20"] - 1),
                    "close_vs_sma50_pct": (None if not spot_row["sma50"] else
                                           sign * 100 * (spot_row["close"] / spot_row["sma50"] - 1)),
                    "macd_hist": sign * spot_row["macd_hist"],
                    "macd": sign * spot_row["macd"],
                    "macd_above_signal": (spot_row["macd"] - spot_row["macd_signal"]) * sign > 0,
                    "rsi14": (None if spot_row["rsi14"] is None else
                              spot_row["rsi14"] if side == "CE" else 100 - spot_row["rsi14"]),
                    "kama_rsi": (None if spot_row["kama_rsi"] is None else
                                 spot_row["kama_rsi"] if side == "CE" else 100 - spot_row["kama_rsi"]),
                    "kama_roc": (None if spot_row["kama_roc"] is None else sign * spot_row["kama_roc"]),
                    "bb_percent_b": (None if spot_row["bb_percent_b"] is None else
                                     spot_row["bb_percent_b"] if side == "CE"
                                     else 1 - spot_row["bb_percent_b"]),
                    "from_day_open_pct": (None if spot_row["from_day_open_pct"] is None
                                          else sign * spot_row["from_day_open_pct"]),
                    "day_range_position": (None if spot_row["day_range_position"] is None else
                                           spot_row["day_range_position"] if side == "CE"
                                           else 1 - spot_row["day_range_position"]),
                },
            })
        if position % 150 == 0:
            print(f"  {position}/{len(symbols)} contracts · {len(episodes)} episodes", flush=True)

    payload = {
        "generated_at": datetime.now(IST).isoformat(),
        "series": "2026-08 (expiry 2026-08-25)",
        "parameters": {
            "timeframe_seconds": TIMEFRAME,
            "runup_threshold_pct": 100 * RUNUP_THRESHOLD,
            "min_onset_premium": MIN_ONSET_PREMIUM,
            "max_abs_moneyness_pct": 100 * MAX_ABS_MONEYNESS,
        },
        "contracts_in_series": len(symbols),
        "contracts_scanned": scanned,
        "contracts_skipped": skipped,
        "spots_covered": len(seen_spots),
        "episodes": episodes,
        "condition_trials": dict(trials),
        "condition_wins": dict(wins),
        "condition_trades": dict(traded),
        "condition_winners": dict(winners),
        "condition_total_return": dict(total_return),
        "condition_profit": dict(profit),
        "condition_loss": dict(loss),
        "cross_trials": dict(x_trials),
        "cross_wins": dict(x_wins),
        "cross_trades": dict(x_traded),
        "cross_winners": dict(x_winners),
        "cross_total_return": dict(x_total_return),
        "cross_profit": dict(x_profit),
        "cross_loss": dict(x_loss),
        "overlay_grid": {
            "trades": dict(grid_trades), "winners": dict(grid_winners),
            "total_return": dict(grid_return), "profit": dict(grid_profit), "loss": dict(grid_loss),
        },
        "exit_overlay": {"hold_bars": HOLD_BARS, "hard_stop_pct": 100 * HARD_STOP,
                         "trail_activation_pct": 100 * TRAIL_ACTIVATION, "trail_pct": 100 * TRAIL},
        "baseline_bars": baseline,
    }
    out = RUNTIME / "august_runup_research.json"
    out.write_text(json.dumps(payload))
    print(f"\n{len(episodes)} episodes across {len({e['symbol'] for e in episodes})} contracts "
          f"and {len({e['root'] for e in episodes})} underlyings")
    print(f"baseline bars: {len(baseline):,}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
