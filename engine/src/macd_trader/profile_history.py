"""Session, weekly and monthly auction profiles — the desk's long memory.

The intraday desk rebuilds its world from ticks every morning and forgets it
every evening. That is workable for a lane that flattens at 15:20 and useless
for one that holds for days: a positional read needs where value sat last week,
which prior point of control has never been traded back to, and whether value
has been migrating up or down. None of that survives a restart today.

This module keeps three tiers, all volume-based:

* ``session_profiles`` — one row per symbol per session: the auction's shape
  (POC, value area, initial balance, day/open type) and its flow (buy/sell
  volume, delta, imbalance).
* ``period_profiles`` — weekly and monthly composites, built by merging the
  sessions' ladders rather than by averaging their statistics. A composite POC
  is where the period actually traded most, which is not the mean of the daily
  POCs.
* ``MarketReference`` — the handful of levels a session needs at 09:15,
  assembled from the two tiers above.

**One basis, deliberately.** The live intraday profile is TPO-based: its POC is
the price that printed in the most 30-minute brackets. Everything here is
volume-based, from the tick ladder. The two disagree, and a positional lane
that silently mixed a TPO POC from one day with a volume POC from another would
be comparing different measurements. The intraday desk keeps its TPO view; this
tier is uniformly volume, and every row records the source it was built from.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
VALUE_AREA_FRACTION = 0.7
# A prior POC counts as revisited once price trades through it, not merely near
# it. One tick of tolerance absorbs the ladder's own rounding.
REVISIT_TOLERANCE_TICKS = 1
LADDER = "ladder"
CANDLES = "candles"
# An index has no traded volume — the live feed reports 0, correctly, because
# nothing trades the index itself. A volume profile of it is therefore not a
# thing that exists. Time-at-price is, and it is what Market Profile was
# originally built on: each minute contributes one unit at its close. Stored
# under its own source name so a TPO POC is never mistaken for a volume POC.
CANDLES_TPO = "candles_tpo"

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_profiles (
  symbol TEXT NOT NULL,
  day TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  poc REAL, vah REAL, val REAL,
  ib_high REAL, ib_low REAL,
  volume INTEGER NOT NULL DEFAULT 0,
  buy_volume INTEGER NOT NULL DEFAULT 0,
  sell_volume INTEGER NOT NULL DEFAULT 0,
  cumulative_delta INTEGER NOT NULL DEFAULT 0,
  imbalance REAL,
  trades INTEGER NOT NULL DEFAULT 0,
  day_type TEXT,
  open_type TEXT,
  single_prints TEXT,
  value_migration TEXT,
  levels INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL,
  built_at TEXT NOT NULL,
  PRIMARY KEY (symbol, day)
);
CREATE INDEX IF NOT EXISTS idx_session_profiles_day ON session_profiles(day);
CREATE TABLE IF NOT EXISTS period_profiles (
  symbol TEXT NOT NULL,
  period TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  sessions INTEGER NOT NULL DEFAULT 0,
  open REAL, high REAL, low REAL, close REAL,
  poc REAL, vah REAL, val REAL,
  volume INTEGER NOT NULL DEFAULT 0,
  buy_volume INTEGER NOT NULL DEFAULT 0,
  sell_volume INTEGER NOT NULL DEFAULT 0,
  cumulative_delta INTEGER NOT NULL DEFAULT 0,
  naked_pocs TEXT,
  built_at TEXT NOT NULL,
  PRIMARY KEY (symbol, period, period_start)
);
"""


def ensure_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Auction shape
# ---------------------------------------------------------------------------

def value_area_from_levels(
    levels: dict[float, float], fraction: float = VALUE_AREA_FRACTION,
) -> tuple[float | None, float | None, float | None]:
    """(POC, VAH, VAL) by the standard alternating expansion from the POC.

    Deliberately the same walk as the live intraday profile, over volume
    instead of bracket counts, so a session row and the composite that contains
    it are built by one rule. Ties break toward the centre of the profile, then
    toward the higher price — an arbitrary but fixed choice, because a POC that
    moved with dict ordering would make every stored row unreproducible.
    """
    populated = {price: size for price, size in levels.items() if size > 0}
    if not populated:
        return None, None, None
    centre = (min(populated) + max(populated)) / 2
    poc = max(populated, key=lambda price: (populated[price], -abs(price - centre), price))
    total = sum(populated.values())
    target = total * fraction
    prices = sorted(populated)
    index = prices.index(poc)
    low_i = high_i = index
    included = populated[poc]
    while included < target and (low_i > 0 or high_i < len(prices) - 1):
        below = populated[prices[low_i - 1]] if low_i > 0 else -1.0
        above = populated[prices[high_i + 1]] if high_i < len(prices) - 1 else -1.0
        if above >= below:
            high_i += 1
            included += populated[prices[high_i]]
        else:
            low_i -= 1
            included += populated[prices[low_i]]
    return poc, prices[high_i], prices[low_i]


def value_migration(current: tuple[float | None, float | None],
                    prior: tuple[float | None, float | None]) -> str:
    """How this session's value area sits against the previous one.

    The classic four readings. "Higher"/"lower" mean value has fully left the
    prior range — the strongest positional signal the profile gives — while
    "overlapping_higher"/"overlapping_lower" mean it has shifted but still
    shares prices with yesterday.
    """
    vah, val = current
    prior_vah, prior_val = prior
    if None in (vah, val, prior_vah, prior_val):
        return "unknown"
    if val > prior_vah:
        return "higher"
    if vah < prior_val:
        return "lower"
    if vah > prior_vah and val > prior_val:
        return "overlapping_higher"
    if vah < prior_vah and val < prior_val:
        return "overlapping_lower"
    if vah >= prior_vah and val <= prior_val:
        return "engulfing"
    return "inside"


def day_type(high: float | None, low: float | None,
             vah: float | None, val: float | None) -> str:
    """Value-area width against the session range.

    A narrow value area inside a wide range is a trend day: price kept moving
    and only accepted in one pocket. A value area covering most of the range is
    a balanced day. The thresholds are the conventional thirds.
    """
    if None in (high, low, vah, val) or high <= low:
        return "unknown"
    coverage = (vah - val) / (high - low)
    if coverage <= 0.35:
        return "trend_day"
    if coverage <= 0.60:
        return "normal_variation_day"
    return "balanced_day"


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def session_bounds(day: str) -> tuple[int, int]:
    opened = datetime.combine(date.fromisoformat(day), time(9, 15), IST)
    return int(opened.timestamp()), int((opened + timedelta(hours=6, minutes=15)).timestamp())


def ladder_levels(ticks: sqlite3.Connection, symbol: str, day: str) -> dict[float, list[float]]:
    """price -> [buy, sell] for one session, from the condensed tick ladder."""
    rows = ticks.execute(
        """SELECT l.price, l.buy_volume, l.sell_volume
           FROM tick_session_ladder l JOIN tick_symbols s ON s.id = l.symbol_id
           WHERE s.symbol = ? AND l.day = ?""",
        (symbol, day),
    ).fetchall()
    return {float(price): [float(buy), float(sell)] for price, buy, sell in rows}


def minute_flow(ticks: sqlite3.Connection, symbol: str, day: str) -> list[tuple]:
    """(minute_ts, open, high, low, close, volume, buy, sell, trades) for a session."""
    start, end = session_bounds(day)
    return ticks.execute(
        """SELECT f.minute_ts, f.open, f.high, f.low, f.close, f.volume,
                  f.buy_volume, f.sell_volume, f.trades
           FROM tick_minute_flow f JOIN tick_symbols s ON s.id = f.symbol_id
           WHERE s.symbol = ? AND f.minute_ts >= ? AND f.minute_ts < ?
           ORDER BY f.minute_ts""",
        (symbol, start, end),
    ).fetchall()


def candle_minutes(history: sqlite3.Connection, symbol: str, day: str) -> list[tuple]:
    """Fallback source: durable minute bars, which carry no aggressor side."""
    start, end = session_bounds(day)
    return history.execute(
        """SELECT timestamp, open, high, low, close, volume FROM historical_candles
           WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?
           ORDER BY timestamp""",
        (symbol, start, end),
    ).fetchall()


# ---------------------------------------------------------------------------
# Session tier
# ---------------------------------------------------------------------------

@dataclass
class SessionProfile:
    symbol: str
    day: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    poc: float | None = None
    vah: float | None = None
    val: float | None = None
    ib_high: float | None = None
    ib_low: float | None = None
    volume: int = 0
    buy_volume: int = 0
    sell_volume: int = 0
    trades: int = 0
    single_prints: list[float] = field(default_factory=list)
    levels: int = 0
    source: str = LADDER
    # (ts, open, high, low, close, volume), ordered. Not stored — it is the
    # working series the session measurements below are read off.
    minutes: list[tuple] = field(default_factory=list)

    @property
    def cumulative_delta(self) -> int:
        return self.buy_volume - self.sell_volume

    @property
    def imbalance(self) -> float | None:
        traded = self.buy_volume + self.sell_volume
        return round(self.cumulative_delta / traded, 5) if traded else None

    @property
    def day_type(self) -> str:
        return day_type(self.high, self.low, self.vah, self.val)

    def position_of(self, price: float) -> str:
        if self.vah is None or self.val is None:
            return "unknown"
        if price > self.vah:
            return "above_value"
        if price < self.val:
            return "below_value"
        return "inside_value"


def session_levels(ticks: sqlite3.Connection | None, history: sqlite3.Connection | None,
                   symbol: str, day: str) -> tuple[dict[float, float], str, dict]:
    """(price -> size, source, extras) for one session.

    Shared by the session tier and the period composites so a week's POC is
    computed over the same distribution its days were. Without this the
    composite silently came out empty for anything with no tick ladder — every
    index, which has no traded volume to build one from.
    """
    if ticks is not None:
        ladder = ladder_levels(ticks, symbol, day)
        rows = minute_flow(ticks, symbol, day)
        if ladder and rows:
            levels = {price: buy + sell for price, (buy, sell) in ladder.items()}
            return levels, LADDER, {
                "buy_volume": int(sum(buy for buy, _ in ladder.values())),
                "sell_volume": int(sum(sell for _, sell in ladder.values())),
                "minutes": [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows],
                "trades": int(sum(r[8] or 0 for r in rows)),
            }
    if history is not None:
        rows = candle_minutes(history, symbol, day)
        if rows:
            traded = any(row[5] for row in rows)
            levels: dict[float, float] = {}
            for _ts, _o, _h, _l, close, volume in rows:
                if not close:
                    continue
                price = round(float(close), 2)
                levels[price] = levels.get(price, 0.0) + (float(volume) if traded else 1.0)
            return levels, (CANDLES if traded else CANDLES_TPO), {"minutes": list(rows)}
    return {}, LADDER, {}


def build_session(ticks: sqlite3.Connection | None, history: sqlite3.Connection | None,
                  symbol: str, day: str) -> SessionProfile | None:
    """One session's volume profile, preferring the tick ladder.

    The ladder is the only source that knows which side was the aggressor. Its
    absence does not make the session worthless — the shape is still real — so
    minute bars are used as a fallback and the row records which it was. A
    candle-built row has no buy/sell split at all rather than a guessed one.
    """
    profile = SessionProfile(symbol=symbol, day=day)
    levels, source, extras = session_levels(ticks, history, symbol, day)
    if not levels:
        return None
    profile.source = source
    profile.buy_volume = extras.get("buy_volume", 0)
    profile.sell_volume = extras.get("sell_volume", 0)
    profile.trades = extras.get("trades", 0)
    profile.minutes = extras.get("minutes", [])
    _apply_minutes(profile, profile.minutes, day)
    profile.volume = int(sum(levels.values()))
    profile.levels = len(levels)
    profile.poc, profile.vah, profile.val = value_area_from_levels(levels)
    # A single print is a level the auction passed through without accepting.
    # At minute resolution that is a level carrying a negligible share of the
    # session's volume, which is the volume-profile analogue of a lone TPO.
    if profile.volume:
        threshold = profile.volume / max(len(levels), 1) * 0.1
        profile.single_prints = sorted(p for p, v in levels.items() if v <= threshold)
    return profile


def _apply_minutes(profile: SessionProfile, rows: list[tuple], day: str) -> None:
    """Fill OHLC and the initial balance from an ordered minute series."""
    opened = int(datetime.combine(date.fromisoformat(day), time(9, 15), IST).timestamp())
    for ts, open_price, high, low, close, _volume in rows:
        ts, high, low = int(ts), _f(high), _f(low)
        open_price, close = _f(open_price), _f(close)
        if profile.open is None and open_price:
            profile.open = open_price
        if close:
            profile.close = close
        if high is not None:
            profile.high = high if profile.high is None else max(profile.high, high)
        if low is not None:
            profile.low = low if profile.low is None else min(profile.low, low)
        # Initial balance is the first hour, the conventional two brackets.
        if ts < opened + 3600:
            if high is not None:
                profile.ib_high = high if profile.ib_high is None else max(profile.ib_high, high)
            if low is not None:
                profile.ib_low = low if profile.ib_low is None else min(profile.ib_low, low)


def _f(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def save_sessions(connection: sqlite3.Connection, profiles: list[SessionProfile],
                  migrations: dict[str, str] | None = None,
                  open_types: dict[str, str] | None = None) -> int:
    if not profiles:
        return 0
    ensure_tables(connection)
    stamp = datetime.now(UTC).isoformat()
    with connection:
        connection.executemany(
            """INSERT OR REPLACE INTO session_profiles
               (symbol, day, open, high, low, close, poc, vah, val, ib_high, ib_low,
                volume, buy_volume, sell_volume, cumulative_delta, imbalance, trades,
                day_type, open_type, single_prints, value_migration, levels, source, built_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (p.symbol, p.day, p.open, p.high, p.low, p.close, p.poc, p.vah, p.val,
                 p.ib_high, p.ib_low, p.volume, p.buy_volume, p.sell_volume,
                 p.cumulative_delta, p.imbalance, p.trades, p.day_type,
                 (open_types or {}).get(f"{p.symbol}:{p.day}"),
                 json.dumps(p.single_prints[:60]),
                 (migrations or {}).get(f"{p.symbol}:{p.day}", "unknown"),
                 p.levels, p.source, stamp)
                for p in profiles
            ],
        )
    return len(profiles)


# ---------------------------------------------------------------------------
# Period tier
# ---------------------------------------------------------------------------

def period_key(day: str, period: str) -> str:
    moment = date.fromisoformat(day)
    if period == "week":
        return (moment - timedelta(days=moment.weekday())).isoformat()
    if period == "month":
        return moment.replace(day=1).isoformat()
    raise ValueError(f"unknown period {period!r}")


def build_period(ticks: sqlite3.Connection | None, connection: sqlite3.Connection,
                 symbol: str, period: str, start: str, days: list[str],
                 history: sqlite3.Connection | None = None) -> dict | None:
    """A composite profile over several sessions.

    Built by merging the sessions' ladders, not by averaging their statistics:
    the composite POC is the price the period actually traded most, which is
    rarely the mean of the daily POCs and is the level a positional trade is
    actually leaning on.
    """
    levels: dict[float, float] = {}
    buy = sell = 0.0
    for day in days:
        # Same distribution the day itself was measured on, whatever its
        # source: a composite built only from ladders is empty for every
        # instrument that has no traded volume of its own.
        day_levels, _source, extras = session_levels(ticks, history, symbol, day)
        for price, size in day_levels.items():
            levels[price] = levels.get(price, 0.0) + size
        buy += extras.get("buy_volume", 0)
        sell += extras.get("sell_volume", 0)
    rows = connection.execute(
        f"""SELECT day, open, high, low, close FROM session_profiles
            WHERE symbol = ? AND day IN ({",".join("?" for _ in days)}) ORDER BY day""",
        (symbol, *days),
    ).fetchall()
    if not rows:
        return None
    highs = [r[2] for r in rows if r[2] is not None]
    lows = [r[3] for r in rows if r[3] is not None]
    poc, vah, val = value_area_from_levels(levels)
    return {
        "symbol": symbol, "period": period, "period_start": start, "period_end": rows[-1][0],
        "sessions": len(rows), "open": rows[0][1], "close": rows[-1][4],
        "high": max(highs) if highs else None, "low": min(lows) if lows else None,
        "poc": poc, "vah": vah, "val": val,
        "volume": int(sum(levels.values())), "buy_volume": int(buy), "sell_volume": int(sell),
        "cumulative_delta": int(buy - sell),
        "naked_pocs": naked_pocs(connection, symbol, upto=rows[-1][0]),
    }


def naked_pocs(connection: sqlite3.Connection, symbol: str, upto: str,
               lookback: int = 40, limit: int = 12) -> list[float]:
    """Prior session POCs that price has not traded back through.

    An untested point of control is where an auction was cut short, and it
    stays a magnet until it is revisited. A POC is retired the moment any later
    session's range covers it — so this is walked forward, newest last, and a
    level survives only if no subsequent session reached it.
    """
    rows = connection.execute(
        """SELECT day, poc, high, low FROM session_profiles
           WHERE symbol = ? AND day <= ? AND poc IS NOT NULL
           ORDER BY day DESC LIMIT ?""",
        (symbol, upto, lookback),
    ).fetchall()
    rows.reverse()
    naked: list[tuple[str, float]] = []
    for day, poc, high, low in rows:
        if high is not None and low is not None:
            tolerance = REVISIT_TOLERANCE_TICKS * 0.05
            naked = [(d, level) for d, level in naked
                     if not (low - tolerance <= level <= high + tolerance)]
        naked.append((day, float(poc)))
    # The newest POC is the session just closed; it has had no chance to be
    # revisited and is not yet evidence of anything.
    return [level for _day, level in reversed(naked[:-1])][:limit]


def save_periods(connection: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    ensure_tables(connection)
    stamp = datetime.now(UTC).isoformat()
    with connection:
        connection.executemany(
            """INSERT OR REPLACE INTO period_profiles
               (symbol, period, period_start, period_end, sessions, open, high, low, close,
                poc, vah, val, volume, buy_volume, sell_volume, cumulative_delta,
                naked_pocs, built_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (r["symbol"], r["period"], r["period_start"], r["period_end"], r["sessions"],
                 r["open"], r["high"], r["low"], r["close"], r["poc"], r["vah"], r["val"],
                 r["volume"], r["buy_volume"], r["sell_volume"], r["cumulative_delta"],
                 json.dumps(r["naked_pocs"]), stamp)
                for r in rows
            ],
        )
    return len(rows)


# ---------------------------------------------------------------------------
# What a session starts with
# ---------------------------------------------------------------------------

@dataclass
class MarketReference:
    """Everything a session needs to know at 09:15 that predates it.

    This is the answer to "carry forward knowledge, not ticks": a few dozen
    numbers per symbol, loaded once, instead of replaying millions of prints
    that have already been distilled into exactly these levels.
    """
    symbol: str
    as_of: str
    prior_day: dict | None = None
    week: dict | None = None
    month: dict | None = None
    naked_pocs: list[float] = field(default_factory=list)
    value_migration: str = "unknown"
    sessions_available: int = 0

    def levels(self) -> dict[str, float]:
        """Flat name -> price map of every reference level, for gating and display."""
        out: dict[str, float] = {}
        for label, row in (("pd", self.prior_day), ("week", self.week), ("month", self.month)):
            for key in ("poc", "vah", "val", "high", "low"):
                value = (row or {}).get(key)
                if value is not None:
                    out[f"{label}_{key}"] = float(value)
        return out

    def location(self, price: float) -> dict[str, str]:
        """Where price sits against each timeframe's value area."""
        out = {}
        for label, row in (("day", self.prior_day), ("week", self.week), ("month", self.month)):
            vah, val = (row or {}).get("vah"), (row or {}).get("val")
            if vah is None or val is None:
                out[label] = "unknown"
            elif price > vah:
                out[label] = "above_value"
            elif price < val:
                out[label] = "below_value"
            else:
                out[label] = "inside_value"
        return out

    def alignment(self, price: float) -> str:
        """One word for whether the timeframes agree about location."""
        seen = {value for value in self.location(price).values() if value != "unknown"}
        if not seen:
            return "unknown"
        if len(seen) == 1:
            return f"aligned_{seen.pop()}"
        return "conflicted"


def load_reference(database_path: str, symbol: str, as_of: str) -> MarketReference:
    """Assemble one symbol's references for a session starting on ``as_of``.

    Everything is strictly before ``as_of``: a reference that included the
    session it is about to inform would be looking at its own answer.
    """
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        ensure_tables(connection)
        reference = MarketReference(symbol=symbol, as_of=as_of)
        prior = connection.execute(
            """SELECT * FROM session_profiles WHERE symbol = ? AND day < ?
               ORDER BY day DESC LIMIT 2""",
            (symbol, as_of),
        ).fetchall()
        if prior:
            reference.prior_day = dict(prior[0])
            reference.value_migration = prior[0]["value_migration"] or "unknown"
            reference.naked_pocs = naked_pocs(connection, symbol, upto=prior[0]["day"])
        reference.sessions_available = connection.execute(
            "SELECT COUNT(*) FROM session_profiles WHERE symbol = ? AND day < ?",
            (symbol, as_of),
        ).fetchone()[0]
        for period, attribute in (("week", "week"), ("month", "month")):
            row = connection.execute(
                """SELECT * FROM period_profiles
                   WHERE symbol = ? AND period = ? AND period_start <= ?
                   ORDER BY period_start DESC LIMIT 1""",
                (symbol, period, as_of),
            ).fetchone()
            if row:
                setattr(reference, attribute, dict(row))
        return reference
    finally:
        connection.close()


def stored_days(connection: sqlite3.Connection, symbol: str | None = None) -> list[str]:
    if symbol:
        rows = connection.execute(
            "SELECT DISTINCT day FROM session_profiles WHERE symbol = ? ORDER BY day", (symbol,))
    else:
        rows = connection.execute("SELECT DISTINCT day FROM session_profiles ORDER BY day")
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Session measurements — the base-rate study fields
# ---------------------------------------------------------------------------
# Market Profile's published statistics (how often the initial balance breaks,
# how far price extends past it, whether the 80% rule completes) are all
# measured on ES and NQ. Nothing equivalent exists for NIFTY, so the numbers
# traders quote are borrowed from a different market. These fields are what a
# NIFTY-specific version of that table is computed from.
#
# Kept in their own table because, unlike the auction shape, they depend on the
# PREVIOUS session: they can be rebuilt without rewriting the profiles, and a
# missing prior day leaves them null rather than silently wrong.

BRACKET_MINUTES = 30
IB_BRACKETS = 2

MEASUREMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_base_rates (
  symbol TEXT NOT NULL,
  day TEXT NOT NULL,
  regime_id TEXT NOT NULL,
  expiry_day INTEGER NOT NULL DEFAULT 0,
  ib_width REAL,
  ib_broken_up INTEGER, ib_broken_down INTEGER,
  break_side TEXT,
  first_break_bracket TEXT,
  extension_up REAL, extension_down REAL, extension_ratio REAL,
  range_ib_ratio REAL,
  opened_outside_prior_value INTEGER,
  returned_to_prior_value INTEGER,
  rule80_triggered INTEGER, rule80_completed INTEGER,
  gap INTEGER, gap_filled INTEGER, gap_fill_bracket TEXT,
  built_at TEXT NOT NULL,
  PRIMARY KEY (symbol, day)
);
CREATE INDEX IF NOT EXISTS idx_base_rates_regime ON session_base_rates(regime_id);
"""


def bracket_letter(index: int) -> str:
    """A-L for the regular brackets, M for the closing auction window."""
    return "M" if index >= 12 else chr(ord("A") + max(0, index))


def bracket_of(timestamp: int, day: str) -> int:
    opened = int(datetime.combine(date.fromisoformat(day), time(9, 15), IST).timestamp())
    return max(0, (int(timestamp) - opened) // (BRACKET_MINUTES * 60))


def measure_session(profile: SessionProfile, prior: dict | None) -> dict:
    """Everything the base-rate table counts, for one session.

    Returns nulls rather than guesses wherever the inputs are missing: a
    session with no initial balance cannot have broken it, and a session with
    no prior day cannot have opened outside its value.
    """
    from .regimes import is_expiry_day, regime_id_for

    out: dict = {
        "symbol": profile.symbol, "day": profile.day,
        "regime_id": regime_id_for(profile.day),
        "expiry_day": 1 if is_expiry_day(profile.day) else 0,
    }
    ib_high, ib_low = profile.ib_high, profile.ib_low
    if ib_high is None or ib_low is None or ib_high <= ib_low:
        return out
    width = ib_high - ib_low
    out["ib_width"] = round(width, 4)

    broke_up = profile.high is not None and profile.high > ib_high
    broke_down = profile.low is not None and profile.low < ib_low
    out["ib_broken_up"] = int(broke_up)
    out["ib_broken_down"] = int(broke_down)
    out["break_side"] = ("both" if broke_up and broke_down else
                         "up" if broke_up else "down" if broke_down else "none")
    out["extension_up"] = round(profile.high - ib_high, 4) if broke_up else 0.0
    out["extension_down"] = round(ib_low - profile.low, 4) if broke_down else 0.0
    out["extension_ratio"] = round(max(out["extension_up"], out["extension_down"]) / width, 4)
    if profile.high is not None and profile.low is not None:
        out["range_ib_ratio"] = round((profile.high - profile.low) / width, 4)

    # The initial balance is the first two brackets, so a break can only be
    # observed from C onward. Set explicitly so a session that never broke has
    # the same row shape as one that did.
    out["first_break_bracket"] = None
    for timestamp, _open, high, low, _close, _volume in profile.minutes:
        index = bracket_of(timestamp, profile.day)
        if index < IB_BRACKETS:
            continue
        if (high is not None and high > ib_high) or (low is not None and low < ib_low):
            out["first_break_bracket"] = bracket_letter(index)
            break

    prior_vah = (prior or {}).get("vah")
    prior_val = (prior or {}).get("val")
    prior_high = (prior or {}).get("high")
    prior_low = (prior or {}).get("low")
    if profile.open is None:
        return out

    if prior_vah is not None and prior_val is not None:
        outside = profile.open > prior_vah or profile.open < prior_val
        out["opened_outside_prior_value"] = int(outside)
        if outside:
            reached = _first_bracket_inside(profile, prior_val, prior_vah)
            out["returned_to_prior_value"] = int(reached is not None)
            # The 80% rule needs ACCEPTANCE, not a touch: two consecutive
            # brackets closing inside the prior value area.
            triggered = _acceptance_bracket(profile, prior_val, prior_vah)
            out["rule80_triggered"] = int(triggered is not None)
            if triggered is not None:
                far = prior_val if profile.open > prior_vah else prior_vah
                out["rule80_completed"] = int(
                    _touched_after(profile, triggered, far, rising=far == prior_vah))

    if prior_high is not None and prior_low is not None:
        gapped = profile.open > prior_high or profile.open < prior_low
        out["gap"] = int(gapped)
        if gapped:
            edge = prior_high if profile.open > prior_high else prior_low
            bracket = _touch_bracket(profile, edge, rising=profile.open < prior_low)
            out["gap_filled"] = int(bracket is not None)
            if bracket is not None:
                out["gap_fill_bracket"] = bracket_letter(bracket)
    return out


def _first_bracket_inside(profile: SessionProfile, low: float, high: float) -> int | None:
    for timestamp, _o, bar_high, bar_low, _c, _v in profile.minutes:
        if bar_high is None or bar_low is None:
            continue
        if bar_low <= high and bar_high >= low:
            return bracket_of(timestamp, profile.day)
    return None


def _acceptance_bracket(profile: SessionProfile, low: float, high: float) -> int | None:
    """First bracket after two consecutive ones closing inside [low, high]."""
    closes: dict[int, float] = {}
    for timestamp, _o, _h, _l, close, _v in profile.minutes:
        if close:
            closes[bracket_of(timestamp, profile.day)] = float(close)
    inside = sorted(index for index, close in closes.items() if low <= close <= high)
    for first, second in zip(inside, inside[1:]):
        if second == first + 1:
            return second
    return None


def _touched_after(profile: SessionProfile, bracket: int, level: float, rising: bool) -> bool:
    for timestamp, _o, high, low, _c, _v in profile.minutes:
        if bracket_of(timestamp, profile.day) < bracket:
            continue
        if rising and high is not None and high >= level:
            return True
        if not rising and low is not None and low <= level:
            return True
    return False


def _touch_bracket(profile: SessionProfile, level: float, rising: bool) -> int | None:
    for timestamp, _o, high, low, _c, _v in profile.minutes:
        if rising and high is not None and high >= level:
            return bracket_of(timestamp, profile.day)
        if not rising and low is not None and low <= level:
            return bracket_of(timestamp, profile.day)
    return None


def save_measurements(connection: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    connection.executescript(MEASUREMENT_SCHEMA)
    columns = ["symbol", "day", "regime_id", "expiry_day", "ib_width", "ib_broken_up",
               "ib_broken_down", "break_side", "first_break_bracket", "extension_up",
               "extension_down", "extension_ratio", "range_ib_ratio",
               "opened_outside_prior_value", "returned_to_prior_value",
               "rule80_triggered", "rule80_completed", "gap", "gap_filled",
               "gap_fill_bracket"]
    stamp = datetime.now(UTC).isoformat()
    with connection:
        connection.executemany(
            f"""INSERT OR REPLACE INTO session_base_rates ({",".join(columns)}, built_at)
                VALUES ({",".join("?" for _ in columns)}, ?)""",
            [tuple(row.get(name) for name in columns) + (stamp,) for row in rows],
        )
    return len(rows)
