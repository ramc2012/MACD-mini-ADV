"""Does the option premium confirm the spot's new extreme, or refuse to?

The Vtrender/Shai reading: a spot low that the put premium does not confirm is
a low nobody is paying to protect against, and that is where reversals live.
Symmetrically for a high the call premium will not follow.

Measuring it needs care, because a raw premium comparison is contaminated
three ways. Theta means the same spot level is worth less later in the day.
IV moves the whole surface. Delta means the premium's response to a given spot
move is not constant. So confirmation is measured two ways that do not need an
IV model at all:

  structural   at a new session extreme in spot, is the option ALSO at a new
               session extreme? This is the read a profile trader takes off
               the chart.
  relative     premium now vs premium at the PREVIOUS spot extreme. Spot
               extended further, so an option that is worth less than it was
               at the shallower extreme has actively refused to confirm.

Both arms -- confirmed and not-confirmed -- are carried through to forward
returns, because "reversals happen here" is only interesting against the rate
at which they happen everywhere else.

    docker compose exec api python /app/scripts/premium_confirmation_research.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app/src")

from macd_trader.models import Candle  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
RUNTIME = Path("/app/runtime")
SNAPSHOT = RUNTIME / "research_snapshot.sqlite3"
DATABASE = SNAPSHOT if SNAPSHOT.exists() else RUNTIME / "historical.sqlite3"

BAR_SECONDS = 300                  # 5 minutes: a session extreme is an intraday idea
BARS_PER_HOUR = 3600 // BAR_SECONDS
MIN_BARS_BEFORE_EVENT = 6          # let the first half hour build a reference
MIN_EXTENSION_PCT = 0.10           # a new extreme must clear the old one by this
EVENT_COOLDOWN_BARS = 6            # one reading per direction per half hour
MIN_PREMIUM = 5.0
FORWARD_HORIZONS = (6, 12, 24)     # 30m, 1h, 2h
# Match the paper desk's 25 bps adverse fill on both entry and exit. Brokerage
# is not included because the historical catalogue does not retain lot size.
SLIPPAGE_BPS_PER_LEG = 25.0
SYMBOL = re.compile(r"^(?P<ex>NSE|BSE):(?P<root>[A-Z0-9&\-]+?)26AUG(?P<strike>\d+(?:\.\d+)?)(?P<side>CE|PE)$")


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    db.execute("PRAGMA busy_timeout=120000")
    return db


def bars_for(db, symbol: str, day: str) -> list[Candle]:
    """5-minute regular-session bars for one symbol on one day."""
    rows = db.execute(
        """SELECT timestamp,open,high,low,close,volume FROM historical_candles
           WHERE symbol=? AND timeframe_seconds=60
             AND date(timestamp,'unixepoch','+5 hours','+30 minutes')=?
           ORDER BY timestamp""", (symbol, day)).fetchall()
    buckets: dict[int, Candle] = {}
    for ts, o, h, l, c, v in rows:
        moment = datetime.fromtimestamp(ts, UTC).astimezone(IST)
        if not (9, 15) <= (moment.hour, moment.minute) < (15, 30):
            continue
        key = ts - (ts % BAR_SECONDS)
        bar = buckets.get(key)
        if bar is None:
            buckets[key] = Candle(symbol, key, o, h, l, c, int(v), True)
        else:
            bar.high = max(bar.high, h)
            bar.low = min(bar.low, l)
            bar.close = c
            bar.volume += int(v)
    return [buckets[k] for k in sorted(buckets)]


def spot_symbol(exchange: str, root: str) -> str:
    from macd_trader.universe import INDEX_SPOTS
    return INDEX_SPOTS.get(root) or f"{exchange}:{root}-EQ"


def forward_trade(rows: list[Candle | None], index: int, stop_pct: float,
                  activation: float, trail_pct: float, hold: int) -> float | None:
    """Clock-aligned, costed option return over a complete forward path.

    Dropping missing bars and then taking the next ``hold`` available bars can
    silently turn a two-hour test into a much longer trade and can hide a stop
    crossing inside the gap.  A path with any missing five-minute candle is
    therefore unavailable, not profitable by assumption.
    """
    if index < 0 or index + hold >= len(rows):
        return None
    path = rows[index:index + hold + 1]
    if any(bar is None for bar in path):
        return None
    complete = [bar for bar in path if bar is not None]
    if any(right.timestamp - left.timestamp != BAR_SECONDS
           for left, right in zip(complete, complete[1:])):
        return None
    entry = complete[0].close * (1 + SLIPPAGE_BPS_PER_LEG / 10_000)
    if entry <= 0:
        return None
    stop = entry * (1 - stop_pct)
    peak, trail_stop = entry, None
    exit_price = complete[-1].close
    for bar in complete[1:]:
        if bar.low <= stop:
            exit_price = stop
            break
        if trail_stop is not None and bar.low <= trail_stop:
            exit_price = trail_stop
            break
        if bar.high > peak:
            peak = bar.high
            if peak >= entry * (1 + activation):
                trail_stop = max(trail_stop or 0.0, peak * (1 - trail_pct))
    exit_price *= 1 - SLIPPAGE_BPS_PER_LEG / 10_000
    return 100 * (exit_price / entry - 1)


def main() -> int:
    db = connect()
    print(f"reading {DATABASE.name}", flush=True)
    catalogue: dict[tuple[str, str, str], list[tuple[float, str]]] = defaultdict(list)
    for symbol, day, count in db.execute(
        """SELECT symbol, date(timestamp,'unixepoch','+5 hours','+30 minutes') d, count(*)
           FROM historical_candles WHERE symbol LIKE '%26AUG%'
           GROUP BY symbol, d HAVING count(*) >= 120"""):
        parsed = SYMBOL.match(symbol)
        if parsed:
            catalogue[(parsed["ex"], parsed["root"], day)].append((float(parsed["strike"]), symbol))

    pairs = defaultdict(dict)
    for (exchange, root, day), rows in catalogue.items():
        for strike, symbol in rows:
            pairs[(exchange, root, day)].setdefault(symbol[-2:], []).append((strike, symbol))

    events: list[dict] = []
    spot_cache: dict[tuple[str, str], list[Candle]] = {}
    done = 0
    for key, sides in pairs.items():
        exchange, root, day = key
        if "CE" not in sides or "PE" not in sides:
            continue
        done += 1
        underlying = spot_symbol(exchange, root)
        cache_key = (underlying, day)
        if cache_key not in spot_cache:
            spot_cache[cache_key] = bars_for(db, underlying, day)
        spot = spot_cache[cache_key]
        if len(spot) < MIN_BARS_BEFORE_EVENT + max(FORWARD_HORIZONS):
            continue
        opening = spot[0].open
        # ATM at the open, chosen independently per side, as the desk does.
        call_strike, call_symbol = min(sides["CE"], key=lambda row: abs(row[0] - opening))
        put_strike, put_symbol = min(sides["PE"], key=lambda row: abs(row[0] - opening))
        calls = {bar.timestamp: bar for bar in bars_for(db, call_symbol, day)}
        puts = {bar.timestamp: bar for bar in bars_for(db, put_symbol, day)}
        if not calls or not puts:
            continue
        call_series = [calls.get(bar.timestamp) for bar in spot]
        put_series = [puts.get(bar.timestamp) for bar in spot]

        session_low = session_high = None
        call_peak = put_peak = 0.0
        last_low_event = last_high_event = -99
        premium_at_low: float | None = None
        premium_at_high: float | None = None
        # A put/call premium ratio reduces common theta and parallel IV moves,
        # but does not literally cancel skew, moneyness or unequal theta.
        ratio_at_low: float | None = None
        ratio_at_high: float | None = None
        # The REVERSAL leg's own premium at the previous extreme. "The ratio
        # moved against the extreme" mechanically implies this leg already
        # outperformed, and buying an already-extended premium is separately
        # known to be the worst thing on this dataset -- so the control has to
        # exist or the two effects cannot be told apart.
        reversal_at_low: float | None = None
        reversal_at_high: float | None = None

        for index, bar in enumerate(spot):
            call_bar, put_bar = call_series[index], put_series[index]
            if call_bar:
                call_peak = max(call_peak, call_bar.high)
            if put_bar:
                put_peak = max(put_peak, put_bar.high)
            previous_low, previous_high = session_low, session_high
            session_low = bar.low if session_low is None else min(session_low, bar.low)
            session_high = bar.high if session_high is None else max(session_high, bar.high)
            if index < MIN_BARS_BEFORE_EVENT or previous_low is None:
                continue

            for direction in ("low", "high"):
                if direction == "low":
                    extended = bar.low < previous_low * (1 - MIN_EXTENSION_PCT / 100)
                    fresh = index - last_low_event >= EVENT_COOLDOWN_BARS
                    option_bar, option_series, peak_before = put_bar, put_series, put_peak
                    reference = premium_at_low
                    opposite_series = call_series
                else:
                    extended = bar.high > previous_high * (1 + MIN_EXTENSION_PCT / 100)
                    fresh = index - last_high_event >= EVENT_COOLDOWN_BARS
                    option_bar, option_series, peak_before = call_bar, call_series, call_peak
                    reference = premium_at_high
                    opposite_series = put_series
                if not (extended and fresh and option_bar) or option_bar.close < MIN_PREMIUM:
                    continue

                # STRUCTURAL: is the option at a new session high on this bar?
                # peak_before already includes this bar, so compare to the peak
                # excluding it.
                prior_peak = 0.0
                for earlier in option_series[:index]:
                    if earlier:
                        prior_peak = max(prior_peak, earlier.high)
                structural = option_bar.high >= prior_peak > 0

                # RELATIVE: worth more than at the previous, shallower extreme?
                relative = None if reference in (None, 0) else option_bar.close / reference

                forward: dict[str, float | None] = {}
                for horizon in FORWARD_HORIZONS:
                    target = min(len(spot) - 1, index + horizon)
                    if target > index:
                        move = 100 * (spot[target].close / bar.close - 1)
                        # Direction-adjusted: a REVERSAL after a new low is a
                        # rise, after a new high is a fall.
                        forward[f"spot_{horizon}"] = move if direction == "low" else -move
                    else:
                        forward[f"spot_{horizon}"] = None
                # Tradable: buy the option that profits from the reversal --
                # the CALL after a refused low, the PUT after a refused high.
                # Keep the option path aligned to the spot clock. A missing bar
                # makes the forward return unavailable rather than extending
                # the holding period or assuming no stop was touched.
                reversal_return = None
                here = opposite_series[index]
                if here is not None and here.close >= MIN_PREMIUM:
                    reversal_return = forward_trade(
                        opposite_series, index, 0.15, 0.20, 0.10, 24,
                    )

                # And the CONTINUATION leg -- the option that confirms the
                # extreme. If a refused extreme keeps going rather than turning,
                # this is the leg that pays, and it is cheap precisely BECAUSE
                # it refused to confirm.
                continuation_return = None
                continuation_by_overlay: dict[str, float] = {}
                if option_bar.close >= MIN_PREMIUM:
                    continuation_return = forward_trade(
                        option_series, index, 0.15, 0.20, 0.10, 24,
                    )
                    if continuation_return is not None:
                        # The overlay above was chosen by earlier work; a single
                        # favourable exit rule is not a finding, so carry the
                        # whole grid and let the write-up show the spread.
                        for stop, activation, trail, hold in (
                            (0.30, 0.30, 0.25, 24), (0.30, 0.30, 0.25, 78),
                            (0.25, 0.20, 0.15, 24), (0.20, 0.20, 0.10, 24),
                            (0.15, 0.20, 0.10, 12), (0.15, 0.20, 0.10, 78),
                            (0.10, 0.20, 0.10, 24),
                        ):
                            value = forward_trade(option_series, index, stop, activation, trail, hold)
                            if value is not None:
                                continuation_by_overlay[
                                    f"{int(stop*100)}|{int(activation*100)}|{int(trail*100)}|{hold}"] = value

                put_now = put_bar.close if put_bar else None
                call_now = call_bar.close if call_bar else None
                ratio = (put_now / call_now) if (put_now and call_now) else None
                previous_ratio = ratio_at_low if direction == "low" else ratio_at_high
                ratio_change = (ratio / previous_ratio) if (ratio and previous_ratio) else None
                reversal_now = call_now if direction == "low" else put_now
                reversal_before = reversal_at_low if direction == "low" else reversal_at_high
                reversal_change = ((reversal_now / reversal_before)
                                   if (reversal_now and reversal_before) else None)

                events.append({
                    "underlying": root, "day": day, "direction": direction,
                    "bar": index,
                    "confirmed_structural": bool(structural),
                    "relative": relative,
                    "spot_extension_pct": abs(100 * (bar.low / previous_low - 1)) if direction == "low"
                                          else abs(100 * (bar.high / previous_high - 1)),
                    "option_premium": option_bar.close,
                    "put_premium": put_now, "call_premium": call_now,
                    "put_call_ratio": ratio, "ratio_change": ratio_change,
                    "reversal_change": reversal_change,
                    "minute_of_session": index,
                    "reversal_option_return": reversal_return,
                    "continuation_option_return": continuation_return,
                    "continuation_by_overlay": continuation_by_overlay,
                    "round_trip_slippage_bps": 2 * SLIPPAGE_BPS_PER_LEG,
                    **forward,
                })
                if direction == "low":
                    last_low_event, premium_at_low = index, option_bar.close
                    ratio_at_low = ratio or ratio_at_low
                    reversal_at_low = reversal_now or reversal_at_low
                else:
                    last_high_event, premium_at_high = index, option_bar.close
                    ratio_at_high = ratio or ratio_at_high
                    reversal_at_high = reversal_now or reversal_at_high
        if done % 500 == 0:
            print(f"  {done} underlying-days · {len(events)} events", flush=True)

    out = RUNTIME / "premium_confirmation.json"
    out.write_text(json.dumps(events))
    print(f"\nunderlying-days processed: {done:,}")
    print(f"events: {len(events):,}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
