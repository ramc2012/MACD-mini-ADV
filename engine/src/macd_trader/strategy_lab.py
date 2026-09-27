"""August contract strategy laboratory.

Answers one question: of the profit that was theoretically available in the
stored August option history, how much can a mechanical rule actually capture?

Design decisions that matter:

* **Long premium only.** The terminal buys options; it cannot short them.
* **Signal on the underlying, express in the option.** Premium series are
  noisy (wide spreads, thin books, gamma). Every professional directional-option
  desk signals on the liquid underlying and expresses the view in the contract.
  Both variants are tested so the comparison is evidence, not assertion.
* **Walk-forward.** Parameters are chosen on the first block of sessions and
  scored on later, unseen ones. In-sample numbers are reported only to show
  the overfitting gap.
* **Costs are charged realistically.** Half the quoted bid-ask, floored at one
  ₹0.05 exchange tick, plus statutory charges (STT, exchange, SEBI, stamp, GST)
  and per-leg brokerage. A bps-of-premium slippage model understates friction by
  7-25x on typical premiums and silently manufactures winners.

Run:  python -m macd_trader.strategy_lab [--out runtime/strategy_lab.json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_START_MINUTE = 9 * 60 + 15
SESSION_END_MINUTE = 15 * 60 + 30
FORCED_EXIT_MINUTE = 15 * 60 + 20

# ---- Execution friction -----------------------------------------------------
# NSE options quote on a ₹0.05 tick grid, so a bps-of-premium slippage model
# diverges from reality as premium falls: at a ₹21 median premium, 5bps is
# ₹0.01 — under a quarter of one tick, a fill that cannot exist. Friction is
# therefore modelled per-unit as half the quoted spread, floored at one tick,
# with the spread widening for cheaper (thinner) contracts.
TICK = 0.05
BROKERAGE_PER_LEG = 20.0


def spread_pct(premium: float) -> float:
    """Quoted bid-ask as a fraction of premium, by premium bucket."""
    if premium >= 100:
        return 0.006
    if premium >= 50:
        return 0.010
    if premium >= 10:
        return 0.025
    return 0.050


def half_spread_cost(premium: float) -> float:
    """Per-unit cost of crossing half the book, floored at one tick."""
    return max(TICK, premium * spread_pct(premium)) / 2.0


def statutory_charges(entry: float, exit_price: float, lot_size: int) -> float:
    """STT (sell side, 0.1% of premium turnover), exchange txn (~0.05% both
    sides), SEBI + stamp, then 18% GST on brokerage + exchange charges."""
    buy_turnover = entry * lot_size
    sell_turnover = exit_price * lot_size
    stt = sell_turnover * 0.001
    exchange = (buy_turnover + sell_turnover) * 0.0005
    sebi = (buy_turnover + sell_turnover) * 0.000001
    stamp = buy_turnover * 0.00003
    gst = (2 * BROKERAGE_PER_LEG + exchange) * 0.18
    return stt + exchange + sebi + stamp + gst
# A contract-day is only tradable if the option actually traded: thin books
# make backtest fills fictional.
MIN_DAILY_VOLUME = 5_000
MIN_PREMIUM = 2.0          # sub-₹2 options are tick-dominated noise
MAX_NOTIONAL_PER_TRADE = 250_000.0


def load_lot_sizes(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    """(by full symbol, by underlying root).

    The runtime file is ``{"date": ..., "lot_sizes": {...}}`` and only covers
    the current ATM ladder, so historical strikes miss. Every strike of an
    underlying shares one lot size, so the root map is the reliable lookup.
    """
    if not path.exists():
        return {}, {}
    payload = json.loads(path.read_text())
    by_symbol = payload.get("lot_sizes", payload) if isinstance(payload, dict) else {}
    by_symbol = {str(k): int(v) for k, v in by_symbol.items() if isinstance(v, (int, float)) and int(v) > 0}
    by_root: dict[str, int] = {}
    for symbol, size in by_symbol.items():
        root, _ = parse_option_symbol(symbol)
        by_root.setdefault(root, size)
    return by_symbol, by_root


@dataclass
class Bar:
    minute: int          # minutes since IST midnight
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class Series:
    symbol: str
    underlying: str
    option_type: str
    lot_size: int
    bars: list[Bar] = field(default_factory=list)


@dataclass
class Trade:
    day: str
    symbol: str
    entry_minute: int
    exit_minute: int
    entry: float
    exit: float
    lot_size: int
    reason: str

    @property
    def gross(self) -> float:
        return (self.exit - self.entry) * self.lot_size

    @property
    def costs(self) -> float:
        slip = (half_spread_cost(self.entry) + half_spread_cost(self.exit)) * self.lot_size
        return slip + 2 * BROKERAGE_PER_LEG + statutory_charges(self.entry, self.exit, self.lot_size)

    @property
    def net(self) -> float:
        return self.gross - self.costs

    @property
    def return_pct(self) -> float:
        return (self.exit / self.entry - 1) * 100 if self.entry else 0.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _minute_of(timestamp: int) -> int:
    moment = datetime.fromtimestamp(timestamp, IST)
    return moment.hour * 60 + moment.minute


def parse_option_symbol(symbol: str) -> tuple[str, str]:
    """(underlying root, CE/PE) from e.g. NSE:SBIN26AUG800CE."""
    body = symbol.split(":")[-1]
    option_type = body[-2:]
    root = body.split("26AUG")[0] if "26AUG" in body else body[:-2]
    return root, option_type


def load_universe(database_path: str, expiry: str, lot_sizes: dict[str, int], lot_by_root: dict[str, int] | None = None) -> tuple[dict[str, dict[str, Series]], dict[str, dict[str, Series]]]:
    """Return (options_by_day, spots_by_day) keyed day -> symbol -> Series."""
    connection = sqlite3.connect(database_path, timeout=60)
    options: dict[str, dict[str, Series]] = defaultdict(dict)
    spots: dict[str, dict[str, Series]] = defaultdict(dict)
    # Bound the spot scan to the option window — the spot table holds ~19M rows
    # spanning months, and only the expiry's sessions are relevant here.
    window = connection.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM historical_candles WHERE asset_type='option' AND expiry=?",
        (expiry,),
    ).fetchone()
    start, end = (window or (0, 0))
    query = """
        SELECT symbol, timestamp, open, high, low, close, volume, asset_type
        FROM historical_candles
        WHERE timeframe_seconds = 60
          AND timestamp BETWEEN ? AND ?
          AND (asset_type = 'spot' OR (asset_type = 'option' AND expiry = ?))
        ORDER BY symbol, timestamp
    """
    for row in connection.execute(query, (start or 0, end or 0, expiry)):
        symbol, timestamp, open_, high, low, close, volume, asset_type = row
        minute = _minute_of(timestamp)
        if not SESSION_START_MINUTE <= minute <= SESSION_END_MINUTE:
            continue
        day = datetime.fromtimestamp(timestamp, IST).date().isoformat()
        bar = Bar(minute, timestamp, float(open_), float(high), float(low), float(close), int(volume))
        if asset_type == "option":
            bucket = options[day]
            series = bucket.get(symbol)
            if series is None:
                root, option_type = parse_option_symbol(symbol)
                lot = lot_sizes.get(symbol) or (lot_by_root or {}).get(root)
                if not lot:
                    continue  # unknown contract size — never guess a position size
                series = Series(symbol, root, option_type, int(lot))
                bucket[symbol] = series
        else:
            bucket = spots[day]
            series = bucket.get(symbol)
            if series is None:
                series = Series(symbol, symbol.split(":")[-1].replace("-EQ", "").replace("-INDEX", ""), "SPOT", 1)
                bucket[symbol] = series
        series.bars.append(bar)
    connection.close()
    return options, spots


def resample(bars: list[Bar], minutes: int) -> list[Bar]:
    if minutes <= 1:
        return bars
    buckets: dict[int, Bar] = {}
    order: list[int] = []
    for bar in bars:
        key = SESSION_START_MINUTE + ((bar.minute - SESSION_START_MINUTE) // minutes) * minutes
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = Bar(key, bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume)
            order.append(key)
        else:
            existing.high = max(existing.high, bar.high)
            existing.low = min(existing.low, bar.low)
            existing.close = bar.close
            existing.volume += bar.volume
    return [buckets[key] for key in order]


# ---------------------------------------------------------------------------
# Indicators (list-based, computed once per series)
# ---------------------------------------------------------------------------

def ema_series(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    out: list[float] = []
    running = values[0] if values else 0.0
    for index, value in enumerate(values):
        running = value if index == 0 else running + alpha * (value - running)
        out.append(running)
    return out


def rsi_series(values: list[float], period: int = 14) -> list[float]:
    out = [50.0] * len(values)
    gain = loss = 0.0
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        up, down = max(change, 0.0), max(-change, 0.0)
        if index <= period:
            gain += up / period
            loss += down / period
            continue
        gain = (gain * (period - 1) + up) / period
        loss = (loss * (period - 1) + down) / period
        out[index] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def atr_series(bars: list[Bar], period: int = 14) -> list[float]:
    out: list[float] = []
    running = 0.0
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close if index else bar.open
        true_range = max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))
        running = true_range if index == 0 else (running * (period - 1) + true_range) / period
        out.append(running)
    return out


def vwap_series(bars: list[Bar]) -> list[float]:
    out: list[float] = []
    value = volume = 0.0
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3
        volume_delta = max(0, bar.volume)
        value += typical * volume_delta
        volume += volume_delta
        out.append(value / volume if volume else bar.close)
    return out


# ---------------------------------------------------------------------------
# Theoretical ceiling
# ---------------------------------------------------------------------------

def perfect_foresight(series: Series, entry_after: int = SESSION_START_MINUTE) -> float:
    """Best single long round trip in the session (buy low, sell any later high).

    Subject to the same tradability and notional gates the strategies face, so
    the ceiling is one a real book could in principle have reached rather than
    an arithmetic fantasy on untradable contracts.
    """
    if sum(bar.volume for bar in series.bars) < MIN_DAILY_VOLUME:
        return 0.0
    best = 0.0
    lowest = None
    for bar in series.bars:
        if bar.minute < entry_after or bar.low < MIN_PREMIUM:
            continue
        if lowest is None or bar.low < lowest:
            lowest = bar.low
        if lowest and lowest * series.lot_size <= MAX_NOTIONAL_PER_TRADE:
            best = max(best, (bar.high - lowest) * series.lot_size)
    return max(0.0, best)


# ---------------------------------------------------------------------------
# Exit engine — shared by every entry rule
# ---------------------------------------------------------------------------

@dataclass
class ExitPolicy:
    hard_stop_pct: float = 0.30
    target_pct: float | None = None
    trail_activate_pct: float | None = 0.30
    trail_give_back_pct: float | None = 0.25
    atr_trail_multiple: float | None = None
    time_stop_minutes: int | None = None

    def label(self) -> str:
        bits = [f"stop{int(self.hard_stop_pct * 100)}"]
        if self.target_pct:
            bits.append(f"tgt{int(self.target_pct * 100)}")
        if self.atr_trail_multiple:
            bits.append(f"atr{self.atr_trail_multiple:g}")
        elif self.trail_activate_pct is not None:
            bits.append(f"trail{int((self.trail_give_back_pct or 0) * 100)}@{int(self.trail_activate_pct * 100)}")
        if self.time_stop_minutes:
            bits.append(f"time{self.time_stop_minutes}")
        return "·".join(bits)


def run_exit(series: Series, bars: list[Bar], entry_index: int, policy: ExitPolicy, atr: list[float] | None) -> Trade:
    entry_bar = bars[entry_index]
    entry = entry_bar.close
    peak = entry
    hard_stop = entry * (1 - policy.hard_stop_pct)
    trailing: float | None = None
    for index in range(entry_index + 1, len(bars)):
        bar = bars[index]
        peak = max(peak, bar.high)
        if policy.atr_trail_multiple and atr:
            candidate = peak - policy.atr_trail_multiple * atr[index]
            trailing = candidate if trailing is None else max(trailing, candidate)
        elif policy.trail_activate_pct is not None and peak >= entry * (1 + policy.trail_activate_pct):
            candidate = peak * (1 - (policy.trail_give_back_pct or 0.25))
            trailing = candidate if trailing is None else max(trailing, candidate)
        if bar.low <= hard_stop:
            return Trade(_day_of(bar), series.symbol, entry_bar.minute, bar.minute, entry, hard_stop, series.lot_size, "HARD_STOP")
        if trailing is not None and bar.low <= trailing:
            return Trade(_day_of(bar), series.symbol, entry_bar.minute, bar.minute, entry, trailing, series.lot_size, "TRAIL")
        if policy.target_pct and bar.high >= entry * (1 + policy.target_pct):
            return Trade(_day_of(bar), series.symbol, entry_bar.minute, bar.minute, entry, entry * (1 + policy.target_pct), series.lot_size, "TARGET")
        if policy.time_stop_minutes and bar.minute - entry_bar.minute >= policy.time_stop_minutes:
            return Trade(_day_of(bar), series.symbol, entry_bar.minute, bar.minute, entry, bar.close, series.lot_size, "TIME")
        if bar.minute >= FORCED_EXIT_MINUTE:
            return Trade(_day_of(bar), series.symbol, entry_bar.minute, bar.minute, entry, bar.close, series.lot_size, "EOD")
    last = bars[-1]
    return Trade(_day_of(last), series.symbol, entry_bar.minute, last.minute, entry, last.close, series.lot_size, "EOD")


def _day_of(bar: Bar) -> str:
    return datetime.fromtimestamp(bar.timestamp, IST).date().isoformat()


# ---------------------------------------------------------------------------
# Entry rules — each returns candidate entry indices on the given bar list
# ---------------------------------------------------------------------------

def entries_macd_zero_cross(bars: list[Bar], fast: int = 12, slow: int = 26, signal: int = 9, **_) -> list[int]:
    closes = [bar.close for bar in bars]
    if len(closes) < slow + 2:
        return []
    fast_ema, slow_ema = ema_series(closes, fast), ema_series(closes, slow)
    macd = [f - s for f, s in zip(fast_ema, slow_ema)]
    out = []
    for index in range(slow + 1, len(bars)):
        if macd[index - 1] <= 0 < macd[index]:
            out.append(index)
    return out


def entries_opening_range_breakout(bars: list[Bar], window_minutes: int = 15, **_) -> list[int]:
    """Toby Crabel / Larry Williams opening-range breakout."""
    if not bars:
        return []
    limit = SESSION_START_MINUTE + window_minutes
    opening = [bar for bar in bars if bar.minute < limit]
    if not opening:
        return []
    range_high = max(bar.high for bar in opening)
    for index, bar in enumerate(bars):
        if bar.minute >= limit and bar.close > range_high:
            return [index]
    return []


def entries_vwap_reclaim(bars: list[Bar], confirm_bars: int = 2, **_) -> list[int]:
    vwap = vwap_series(bars)
    out = []
    for index in range(confirm_bars, len(bars)):
        above_now = all(bars[index - offset].close > vwap[index - offset] for offset in range(confirm_bars))
        was_below = bars[index - confirm_bars].close <= vwap[index - confirm_bars]
        if above_now and was_below:
            out.append(index)
    return out


def entries_donchian_breakout(bars: list[Bar], lookback: int = 20, **_) -> list[int]:
    """Richard Dennis turtle-style channel breakout."""
    out = []
    for index in range(lookback, len(bars)):
        channel_high = max(bar.high for bar in bars[index - lookback:index])
        if bars[index].close > channel_high:
            out.append(index)
    return out


def entries_momentum_ignition(bars: list[Bar], volume_multiple: float = 3.0, range_multiple: float = 1.5, lookback: int = 20, **_) -> list[int]:
    out = []
    atr = atr_series(bars)
    for index in range(lookback, len(bars)):
        window = bars[index - lookback:index]
        average_volume = statistics.fmean(bar.volume for bar in window) or 1
        bar = bars[index]
        body = bar.close - bar.open
        if (bar.volume >= volume_multiple * average_volume
                and body > 0
                and (bar.high - bar.low) >= range_multiple * (atr[index] or 0.0001)):
            out.append(index)
    return out


def entries_rsi_pullback(bars: list[Bar], rsi_period: int = 2, threshold: float = 10.0, trend_period: int = 50, **_) -> list[int]:
    """Larry Connors RSI(2) pullback inside an uptrend."""
    closes = [bar.close for bar in bars]
    if len(closes) < trend_period + 2:
        return []
    rsi = rsi_series(closes, rsi_period)
    trend = ema_series(closes, trend_period)
    out = []
    for index in range(trend_period, len(bars)):
        if closes[index] > trend[index] and rsi[index - 1] < threshold <= rsi[index]:
            out.append(index)
    return out


ENTRY_RULES = {
    "macd_zero_cross": entries_macd_zero_cross,
    "opening_range_breakout": entries_opening_range_breakout,
    "vwap_reclaim": entries_vwap_reclaim,
    "donchian_breakout": entries_donchian_breakout,
    "momentum_ignition": entries_momentum_ignition,
    "rsi2_pullback": entries_rsi_pullback,
}


# ---------------------------------------------------------------------------
# Backtest driver
# ---------------------------------------------------------------------------

@dataclass
class Config:
    entry_rule: str
    signal_source: str        # "option" or "underlying"
    timeframe: int            # minutes
    policy: ExitPolicy
    params: dict = field(default_factory=dict)
    max_concurrent: int = 8
    one_trade_per_symbol_per_day: bool = True

    def label(self) -> str:
        return f"{self.entry_rule}|{self.signal_source}|{self.timeframe}m|{self.policy.label()}" + (
            "|" + ",".join(f"{k}={v}" for k, v in sorted(self.params.items())) if self.params else ""
        )


def simulate(config: Config, options_by_day: dict, spots_by_day: dict, days: list[str]) -> dict:
    rule = ENTRY_RULES[config.entry_rule]
    trades: list[Trade] = []
    for day in days:
        options = options_by_day.get(day, {})
        spots = spots_by_day.get(day, {})
        spot_by_root = {series.underlying: series for series in spots.values()}
        # Candidate (entry_minute, series, entry_index) across the universe.
        candidates: list[tuple[int, Series, list[Bar], int, list[float]]] = []
        for series in options.values():
            # Tradability gates applied to the CONTRACT-DAY, using only that
            # day's own volume/price — no forward information.
            if sum(bar.volume for bar in series.bars) < MIN_DAILY_VOLUME:
                continue
            option_bars = resample(series.bars, config.timeframe)
            if len(option_bars) < 10 or option_bars[0].close < MIN_PREMIUM:
                continue
            if config.signal_source == "underlying":
                spot = spot_by_root.get(series.underlying)
                if spot is None:
                    continue
                signal_bars = resample(spot.bars, config.timeframe)
                # A CE expresses a bullish underlying view; a PE expresses the
                # bearish one, so the PE's signal is the inverted spot series.
                if series.option_type == "PE":
                    signal_bars = [Bar(b.minute, b.timestamp, -b.open, -b.low, -b.high, -b.close, b.volume) for b in signal_bars]
            else:
                signal_bars = option_bars
            if len(signal_bars) < 10:
                continue
            indices = rule(signal_bars, **config.params)
            if not indices:
                continue
            option_atr = atr_series(option_bars)
            minute_to_index = {bar.minute: index for index, bar in enumerate(option_bars)}
            for signal_index in indices:
                minute = signal_bars[signal_index].minute
                entry_index = minute_to_index.get(minute)
                if entry_index is None or entry_index >= len(option_bars) - 1:
                    continue
                if option_bars[entry_index].minute >= FORCED_EXIT_MINUTE - config.timeframe:
                    continue
                candidates.append((minute, series, option_bars, entry_index, option_atr))
                if config.one_trade_per_symbol_per_day:
                    break
        candidates.sort(key=lambda row: row[0])
        open_until: list[int] = []
        for minute, series, option_bars, entry_index, option_atr in candidates:
            open_until = [end for end in open_until if end > minute]
            if len(open_until) >= config.max_concurrent:
                continue
            # One lot must fit the per-trade notional cap; real lots run from
            # 20 to 71,475 units, so an uncapped book would be dominated by
            # whichever contract happened to be largest.
            if option_bars[entry_index].close * series.lot_size > MAX_NOTIONAL_PER_TRADE:
                continue
            trade = run_exit(series, option_bars, entry_index, config.policy, option_atr)
            trades.append(trade)
            open_until.append(trade.exit_minute)
    return summarize(trades, days)


def summarize(trades: list[Trade], days: list[str]) -> dict:
    if not trades:
        return {"trades": 0, "net": 0.0, "gross": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
                "average_return_pct": 0.0, "max_drawdown": 0.0, "expectancy": 0.0, "days": len(days),
                "exit_mix": {}, "equity": []}
    net_values = [trade.net for trade in trades]
    wins = [value for value in net_values if value > 0]
    losses = [value for value in net_values if value <= 0]
    equity: list[float] = []
    running = 0.0
    peak = 0.0
    drawdown = 0.0
    for trade in sorted(trades, key=lambda t: (t.day, t.exit_minute)):
        running += trade.net
        equity.append(round(running, 2))
        peak = max(peak, running)
        drawdown = min(drawdown, running - peak)
    exit_mix: dict[str, int] = defaultdict(int)
    for trade in trades:
        exit_mix[trade.reason] += 1
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(trades),
        "net": round(sum(net_values), 2),
        "gross": round(sum(trade.gross for trade in trades), 2),
        "win_rate": round(100 * len(wins) / len(trades), 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else float("inf"),
        "average_return_pct": round(statistics.fmean(trade.return_pct for trade in trades), 3),
        "expectancy": round(statistics.fmean(net_values), 2),
        "max_drawdown": round(drawdown, 2),
        "days": len(days),
        "trades_per_day": round(len(trades) / max(1, len(days)), 1),
        "exit_mix": dict(exit_mix),
        "equity": equity[-250:],
    }


def build_configs() -> list[Config]:
    policies = [
        ExitPolicy(0.30, None, 0.30, 0.25, None, None),      # current production rule
        ExitPolicy(0.25, 0.50, None, None, None, None),      # fixed 2:1 target
        ExitPolicy(0.30, None, 0.15, 0.20, None, None),      # early, tight trail
        ExitPolicy(0.30, None, None, None, 2.0, None),       # Chandelier-style ATR trail
        ExitPolicy(0.30, None, None, None, 3.0, None),
        ExitPolicy(0.25, None, 0.20, 0.20, None, 60),        # trail + 60m time stop
        ExitPolicy(0.35, 0.80, 0.40, 0.30, None, None),      # let winners run
    ]
    grids = {
        "macd_zero_cross": [{}],
        "opening_range_breakout": [{"window_minutes": 15}, {"window_minutes": 30}, {"window_minutes": 60}],
        "vwap_reclaim": [{"confirm_bars": 1}, {"confirm_bars": 2}, {"confirm_bars": 3}],
        "donchian_breakout": [{"lookback": 10}, {"lookback": 20}, {"lookback": 40}],
        "momentum_ignition": [{"volume_multiple": 2.0, "range_multiple": 1.2}, {"volume_multiple": 3.0, "range_multiple": 1.5}],
        "rsi2_pullback": [{"rsi_period": 2, "threshold": 10.0, "trend_period": 50}, {"rsi_period": 2, "threshold": 25.0, "trend_period": 50}],
    }
    configs: list[Config] = []
    for rule, param_sets in grids.items():
        for params in param_sets:
            for source in ("option", "underlying"):
                for timeframe in (5, 15, 30):
                    for policy in policies:
                        configs.append(Config(rule, source, timeframe, policy, dict(params)))
    return configs


# ---------------------------------------------------------------------------
# Walk-forward orchestration
# ---------------------------------------------------------------------------

def ceiling_report(options_by_day: dict, days: list[str], max_concurrent: int) -> dict:
    """Two honest ceilings.

    ``perfect_all`` is every contract's best round trip every day — unreachable
    (it needs 700 simultaneous positions and perfect timing) but it frames the
    scale. ``perfect_capacity`` restricts to the best N contracts per day, which
    is the ceiling a book running N concurrent lots could theoretically reach.
    """
    total = 0.0
    capacity = 0.0
    per_day: dict[str, float] = {}
    for day in days:
        bests = sorted((perfect_foresight(series) for series in options_by_day.get(day, {}).values()), reverse=True)
        total += sum(bests)
        day_capacity = sum(bests[:max_concurrent])
        capacity += day_capacity
        per_day[day] = round(day_capacity, 2)
    return {
        "perfect_all_contracts": round(total, 2),
        "perfect_capacity_limited": round(capacity, 2),
        "per_day_capacity": per_day,
        "max_concurrent": max_concurrent,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="August option strategy laboratory")
    parser.add_argument("--database", default="runtime/historical.sqlite3")
    parser.add_argument("--expiry", default="2026-08-25")
    parser.add_argument("--out", default="runtime/strategy_lab.json")
    parser.add_argument("--max-concurrent", type=int, default=8)
    parser.add_argument("--train-fraction", type=float, default=0.6)
    parser.add_argument("--min-contracts", type=int, default=300,
                        help="skip early sessions where the expiry was still illiquid")
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    lot_path = Path(args.database).with_name("fyers_lot_sizes.json")
    lot_sizes, lot_by_root = load_lot_sizes(lot_path)
    print(f"lot sizes: {len(lot_sizes)} symbols / {len(lot_by_root)} underlyings", flush=True)
    print(f"loading {args.expiry} universe…", flush=True)
    options_by_day, spots_by_day = load_universe(args.database, args.expiry, lot_sizes, lot_by_root)
    all_days = sorted(options_by_day)
    # A newly listed expiry lists a handful of strikes months early; those
    # sessions are not representative of trading the contract set.
    days = [day for day in all_days if len(options_by_day[day]) >= args.min_contracts]
    print(f"{len(days)} liquid sessions of {len(all_days)} ({days[0]}→{days[-1]}), "
          f"{sum(len(options_by_day[d]) for d in days)} contract-days", flush=True)

    split = max(1, int(len(days) * args.train_fraction))
    train_days, test_days = days[:split], days[split:]
    ceiling = ceiling_report(options_by_day, days, args.max_concurrent)
    ceiling_train = ceiling_report(options_by_day, train_days, args.max_concurrent)["perfect_capacity_limited"]
    ceiling_test = ceiling_report(options_by_day, test_days, args.max_concurrent)["perfect_capacity_limited"]
    print(f"capacity ceiling: total ₹{ceiling['perfect_capacity_limited']:,.0f} "
          f"(train ₹{ceiling_train:,.0f} / test ₹{ceiling_test:,.0f})", flush=True)

    configs = build_configs()
    print(f"testing {len(configs)} configurations…", flush=True)
    results = []
    for index, config in enumerate(configs, 1):
        config.max_concurrent = args.max_concurrent
        in_sample = simulate(config, options_by_day, spots_by_day, train_days)
        if in_sample["trades"] < 5:
            continue
        out_sample = simulate(config, options_by_day, spots_by_day, test_days)
        results.append({
            "config": config.label(),
            "entry_rule": config.entry_rule,
            "signal_source": config.signal_source,
            "timeframe": config.timeframe,
            "exit_policy": config.policy.label(),
            "params": config.params,
            "in_sample": in_sample,
            "out_of_sample": out_sample,
            "capture_in": round(100 * in_sample["net"] / ceiling_train, 2) if ceiling_train else 0.0,
            "capture_out": round(100 * out_sample["net"] / ceiling_test, 2) if ceiling_test else 0.0,
        })
        if index % 50 == 0:
            print(f"  {index}/{len(configs)}…", flush=True)

    # Rank by out-of-sample net, requiring the in-sample side to be positive too
    # so a lucky test block cannot crown a broken rule.
    ranked = sorted(
        [row for row in results if row["in_sample"]["net"] > 0],
        key=lambda row: row["out_of_sample"]["net"], reverse=True,
    )
    fallback = sorted(results, key=lambda row: row["out_of_sample"]["net"], reverse=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expiry": args.expiry,
        "sessions": days,
        "train_days": train_days,
        "test_days": test_days,
        "costs": {"model": "half-spread floored at one ₹0.05 tick, by premium bucket "
                              "(<₹10: 5%, ₹10-50: 2.5%, ₹50-100: 1%, ≥₹100: 0.6%), "
                              "plus STT/exchange/SEBI/stamp/GST and ₹20 per leg",
                    "brokerage_per_leg": BROKERAGE_PER_LEG, "tick": TICK},
        "ceiling": ceiling,
        "ceiling_train": ceiling_train,
        "ceiling_test": ceiling_test,
        "configurations_tested": len(results),
        "ranked": (ranked or fallback)[:args.top],
        "worst": fallback[-5:],
        "by_rule": {},
    }
    for rule in ENTRY_RULES:
        rows = [row for row in results if row["entry_rule"] == rule]
        if rows:
            best = max(rows, key=lambda row: row["out_of_sample"]["net"])
            report["by_rule"][rule] = {
                "best_config": best["config"],
                "oos_net": best["out_of_sample"]["net"],
                "oos_trades": best["out_of_sample"]["trades"],
                "oos_win_rate": best["out_of_sample"]["win_rate"],
                "oos_profit_factor": best["out_of_sample"]["profit_factor"],
                "capture_out_pct": best["capture_out"],
            }
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(f"\nwrote {args.out}")
    print(f"\n{'rule':26s} {'src':11s} {'tf':>4s} {'OOS net':>12s} {'trades':>7s} {'win%':>6s} {'PF':>6s} {'capture%':>9s}")
    for row in report["ranked"]:
        oos = row["out_of_sample"]
        print(f"{row['entry_rule']:26s} {row['signal_source']:11s} {row['timeframe']:3d}m "
              f"{oos['net']:12,.0f} {oos['trades']:7d} {oos['win_rate']:6.1f} {oos['profit_factor']:6.2f} {row['capture_out']:9.2f}")


if __name__ == "__main__":
    main()
