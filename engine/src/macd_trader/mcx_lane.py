"""The MCX lane: the MACD lane's rules, applied to MCX commodity options.

What is the same as the MACD lane
---------------------------------
* **Contract.** Per underlying, the side-rounded ATM call and put of the
  nearest option series at least ``mcx_min_days_to_expiry`` days out, with the
  MACD lane's 1.5x liquidity tie-break across the two strikes either side
  (``contracts.select_liquid_contract``). Selected once a day, at the engine's
  08:45 rollover, and kept for the session.
* **Entry.** ``MACDStrategyManager`` itself, on the option PREMIUM: MACD
  (fast/slow/signal from the MACD lane's settings) crossing up through zero,
  evaluated on the forming bar and on the close, one signal per bar, with the
  MACD lane's optional KAMA / RSI(KAMA) / ROC(KAMA) confirmations. A signal
  whose bar belongs to an earlier period than now is dropped, as on the MACD
  lane.
* **Exit.** ``ExecutionManager`` itself, with the MACD lane's risk settings:
  hard stop, trailing stop armed at +activation and floored at breakeven,
  scale-in at +7.5/15/22.5%, scale-out at +30/50/75%, optional MACD
  invalidation exit. Sizing is whole lots nearest ``mcx_target_notional``.

What is different, and why
--------------------------
* **Its own book.** ``runtime/mcx_trader.sqlite3``, its own capital and
  position cap. MCX premiums are quoted per 10 g, kg, barrel or mmBtu, and one
  option lot is up to 2,500 of those units; mixing that into the MACD lane's
  equity curve would make neither book readable.
* **Its own clock.** MCX trades 09:00-23:30 IST while the US is on daylight
  time and to 23:55 while it is on standard time (November to March), and runs an evening-only session on several NSE holidays. The
  NSE session gate drops every print after 15:40, so the lane keeps its own
  09:00-anchored bars and its own holiday list.
* **Lot multiplier.** The paper book values a position as premium x units.
  MCX's instrument files count quantity in lots (a GOLD lot is "1"), so the
  units per lot come from the exchange contract specification: GOLD 100 (1 kg
  quoted per 10 g), SILVER 30 (30 kg per kg), CRUDEOIL 100 barrels, COPPER
  2,500 kg, NATURALGAS 1,250 mmBtu. ``mcx_lot_multipliers_csv`` overrides them.
* **No NSE entry gate.** The MACD lane's entry scorer was trained on NSE
  option features (index breadth, crowding, previous-session volume); none of
  it describes a commodity, so the lane takes every zero-cross the strategy
  fires, as the MACD lane does with its gate switched off.
* **Expiry.** MCX options devolve into futures at expiry. A holding in a
  contract expiring today is flattened from ``mcx_expiry_flatten_ist``
  (default 23:00). New entries never use a series inside
  ``mcx_min_days_to_expiry``.
* **Paper only, always.** The lane's execution settings force
  ``execution_mode="paper"`` whatever the engine is configured for.

Data
----
Ticks come from the engine's one Fyers websocket; the MCX contracts are added
to its subscription. When the socket has carried nothing for one of the lane's
contracts for ``mcx_rest_fallback_seconds`` during the session, the lane polls
REST quotes for those contracts (one batched request) and feeds them through
the same path, with the quote's own exchange time deciding whether it is fresh
enough to trade on. Warm-up downloads one-minute history for the selected
contracts and aggregates it on the lane's own 09:00-anchored grid, so warm-up
bars and live bars share boundaries.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .brokers import OptionChainEntry
from .config import RUNTIME_DIR, Settings
from .contracts import liquidity_window, select_liquid_contract
from .events import EventHub, json_value
from .execution import ExecutionManager
from .models import Candle, Signal, Tick, Trade
from .portfolio import Portfolio, breakeven_exit_price
from .repository import TradeRepository
from .rollover import Expiry, days_to_expiry, first_tradable
from .strategy import MACDStrategyManager

IST = ZoneInfo("Asia/Kolkata")
US_EASTERN = ZoneInfo("America/New_York")
LANE = "mcx"
EXCHANGE = "MCX"

# Units the premium is multiplied by for one lot (exchange contract specs).
DEFAULT_LOT_MULTIPLIERS: dict[str, int] = {
    "GOLD": 100,         # 1 kg, premium quoted per 10 g
    "SILVER": 30,        # 30 kg, quoted per kg
    "CRUDEOIL": 100,     # 100 barrels, quoted per barrel
    "COPPER": 2500,      # 2,500 kg, quoted per kg
    "NATURALGAS": 1250,  # 1,250 mmBtu, quoted per mmBtu
}
DISPLAY_NAMES = {
    "GOLD": "Gold", "SILVER": "Silver", "CRUDEOIL": "Crude oil",
    "COPPER": "Copper", "NATURALGAS": "Natural gas",
}

# --------------------------------------------------------------------- calendar
MCX_OPEN = time(9, 0)
MCX_EVENING_OPEN = time(17, 0)
# US daylight time (March to early November): 23:30. US standard time: 23:55.
MCX_CLOSE_SUMMER = time(23, 30)
MCX_CLOSE_WINTER = time(23, 55)

# MCX trading holidays, calendar 2026. Non-agri commodities close for the whole
# day on these dates...
MCX_CLOSED_2026: dict[date, str] = {
    date(2026, 1, 1): "New Year Day",
    date(2026, 1, 26): "Republic Day",
    date(2026, 3, 3): "Holi",
    date(2026, 3, 26): "Shri Ram Navami",
    date(2026, 3, 31): "Shri Mahavir Jayanti",
    date(2026, 4, 3): "Good Friday",
    date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
    date(2026, 5, 1): "Maharashtra Day",
    date(2026, 10, 2): "Mahatma Gandhi Jayanti",
    date(2026, 12, 25): "Christmas",
}
# ...and on these the morning session is closed but the evening session
# (17:00 to the close) trades.
MCX_EVENING_ONLY_2026: dict[date, str] = {
    date(2026, 5, 28): "Bakri Id",
    date(2026, 6, 26): "Muharram",
    date(2026, 9, 14): "Ganesh Chaturthi",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 10): "Diwali Balipratipada",
    date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev",
}


def _parse_days(csv_text: str | None) -> set[date]:
    days: set[date] = set()
    for token in (csv_text or "").replace(";", ",").split(","):
        token = token.strip()
        if token:
            try:
                days.add(date.fromisoformat(token))
            except ValueError:
                continue
    return days


class McxCalendar:
    """When MCX's non-agricultural segment trades."""

    def __init__(self, extra_closed_csv: str = "", extra_evening_only_csv: str = ""):
        self.closed = {**MCX_CLOSED_2026, **{day: "configured holiday" for day in _parse_days(extra_closed_csv)}}
        self.evening_only = {
            **MCX_EVENING_ONLY_2026,
            **{day: "configured evening-only day" for day in _parse_days(extra_evening_only_csv)},
        }

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.closed

    def opens_at(self, day: date) -> time:
        return MCX_EVENING_OPEN if day in self.evening_only else MCX_OPEN

    @staticmethod
    def closes_at(day: date) -> time:
        """23:30 while New York is on daylight time, 23:55 otherwise (November to March)."""
        noon = datetime.combine(day, time(12), US_EASTERN)
        return MCX_CLOSE_SUMMER if noon.dst() else MCX_CLOSE_WINTER

    def in_session(self, moment: datetime) -> bool:
        local = moment.astimezone(IST)
        day = local.date()
        if not self.is_trading_day(day):
            return False
        now = local.time().replace(tzinfo=None)
        return self.opens_at(day) <= now < self.closes_at(day)

    def phase(self, moment: datetime) -> str:
        local = moment.astimezone(IST)
        day = local.date()
        if not self.is_trading_day(day):
            return "holiday" if day.weekday() < 5 else "weekend"
        now = local.time().replace(tzinfo=None)
        if now < self.opens_at(day):
            return "pre-open"
        if now >= self.closes_at(day):
            return "closed"
        return "evening" if now >= MCX_EVENING_OPEN else "morning"

    def next_open(self, moment: datetime) -> datetime:
        """Now if the session is open, else the next session's open."""
        local = moment.astimezone(IST)
        if self.in_session(local):
            return local
        for offset in range(0, 15):
            candidate = local.date() + timedelta(days=offset)
            if not self.is_trading_day(candidate):
                continue
            opens = datetime.combine(candidate, self.opens_at(candidate), IST)
            if opens > local:
                return opens
        return datetime.combine(local.date() + timedelta(days=15), MCX_OPEN, IST)

    def covers_year(self, year: int) -> bool:
        return any(day.year == year for day in self.closed)


def mcx_bucket(epoch: int, timeframe_seconds: int, calendar: McxCalendar) -> int | None:
    """09:00-anchored bucket start for an in-session print, else None.

    Anchored at 09:00 even on an evening-only day: 17:00 is a whole number of
    30-minute (and 1/5/15-minute) bars after 09:00, so evening bars fall on
    the same grid on every day.
    """
    moment = datetime.fromtimestamp(epoch, UTC).astimezone(IST)
    if not calendar.in_session(moment):
        return None
    open_at = datetime.combine(moment.date(), MCX_OPEN, IST)
    elapsed = int((moment - open_at).total_seconds())
    return int((open_at + timedelta(seconds=(elapsed // timeframe_seconds) * timeframe_seconds)).timestamp())


class McxCandleAggregator:
    """CandleAggregator's rules on the MCX clock."""

    def __init__(self, timeframe_seconds: int, calendar: McxCalendar):
        self.timeframe_seconds = timeframe_seconds
        self.calendar = calendar
        self.current: dict[str, Candle] = {}
        self.rejected_out_of_order = 0
        self.rejected_off_session = 0

    def on_tick(self, tick: Tick) -> tuple[Candle | None, Candle | None]:
        bucket = mcx_bucket(int(tick.timestamp.timestamp()), self.timeframe_seconds, self.calendar)
        if bucket is None:
            self.rejected_off_session += 1
            return None, None
        candle = self.current.get(tick.symbol)
        if candle is not None and bucket < candle.timestamp:
            self.rejected_out_of_order += 1
            return candle, None
        closed = None
        if candle is None or candle.timestamp != bucket:
            if candle is not None:
                candle.closed = True
                closed = candle
            candle = Candle(tick.symbol, bucket, tick.ltp, tick.ltp, tick.ltp, tick.ltp, max(tick.volume, 0))
            self.current[tick.symbol] = candle
        else:
            candle.high = max(candle.high, tick.ltp)
            candle.low = min(candle.low, tick.ltp)
            candle.close = tick.ltp
            candle.volume = max(candle.volume, tick.volume)
        return candle, closed

    def flush(self) -> list[Candle]:
        closed = []
        for candle in self.current.values():
            if not candle.closed:
                candle.closed = True
                closed.append(candle)
        self.current.clear()
        return closed


def aggregate_minutes(minutes: list[Candle], timeframe_seconds: int, calendar: McxCalendar) -> list[Candle]:
    """Fold one-minute history into the lane's bars, on the live grid.

    Volume is summed here, not maxed: history bars carry per-minute volume,
    while the live aggregator sees the socket's cumulative day volume.
    """
    bars: dict[int, Candle] = {}
    for minute in sorted(minutes, key=lambda row: row.timestamp):
        bucket = mcx_bucket(minute.timestamp, timeframe_seconds, calendar)
        if bucket is None:
            continue
        bar = bars.get(bucket)
        if bar is None:
            bars[bucket] = Candle(minute.symbol, bucket, minute.open, minute.high, minute.low, minute.close,
                                  int(minute.volume or 0), True)
        else:
            bar.high = max(bar.high, minute.high)
            bar.low = min(bar.low, minute.low)
            bar.close = minute.close
            bar.volume += int(minute.volume or 0)
    return [bars[key] for key in sorted(bars)]


# --------------------------------------------------------------- the master

_OPTION_TICKER = re.compile(r"^(?P<root>[A-Z]+?)(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<strike>\d+(?:\.\d+)?)(?P<side>CE|PE)$")
_FUTURES_TICKER = re.compile(r"^(?P<root>[A-Z]+?)(?P<yy>\d{2})(?P<mon>[A-Z]{3})FUT$")


@dataclass(slots=True)
class McxListed:
    symbol: str
    root: str
    kind: str              # "CE", "PE" or "FUT"
    expiry: str
    strike: float = 0.0
    exchange_lot: int = 1
    token: str = ""
    underlying_token: str = ""


def _master_root(row: list[str], body: str, roots: set[str], pattern: re.Pattern) -> str | None:
    named = str(row[13] or "").strip().upper()
    named = re.sub(r"\d{2}[A-Z]{3}FUT$", "", named)
    if named in roots:
        return named
    match = pattern.match(body)
    if match and match.group("root") in roots:
        return match.group("root")
    return None


def parse_mcx_master(rows, roots: set[str]) -> list[McxListed]:
    """MCX options and futures of ``roots`` from Fyers' MCX_COM.csv rows.

    Same column layout as the NSE FO file: lot=3, expiry epoch=8, ticker=9,
    underlying=13, strike=15, option side=16, underlying FyToken=17. The
    underlying column is matched exactly, so GOLDM, SILVERM and CRUDEOILM
    never stand in for their full-size contracts.
    """
    listed: list[McxListed] = []
    for row in rows:
        if len(row) <= 16:
            continue
        ticker = str(row[9] or "").strip()
        if not ticker.startswith(EXCHANGE + ":"):
            continue
        body = ticker.split(":", 1)[1]
        side = str(row[16] or "").strip().upper()
        if side in {"CE", "PE"}:
            kind, pattern = side, _OPTION_TICKER
        elif body.endswith("FUT"):
            kind, pattern = "FUT", _FUTURES_TICKER
        else:
            continue
        root = _master_root(row, body, roots, pattern)
        if root is None:
            continue
        try:
            expiry = datetime.fromtimestamp(float(row[8]), UTC).astimezone(IST).date().isoformat()
            exchange_lot = int(float(row[3] or 1))
            strike = float(row[15] or 0) if kind != "FUT" else 0.0
        except (TypeError, ValueError, OSError):
            continue
        if kind != "FUT" and strike <= 0:
            continue
        listed.append(McxListed(
            symbol=ticker, root=root, kind=kind, expiry=expiry, strike=strike,
            exchange_lot=max(1, exchange_lot), token=str(row[0] or "").strip(),
            underlying_token=str(row[17] or "").strip() if len(row) > 17 else "",
        ))
    return listed


def lot_multipliers(override_csv: str = "") -> dict[str, int]:
    """Units per lot by root: the contract-spec table, then ``ROOT:UNITS`` overrides."""
    table = dict(DEFAULT_LOT_MULTIPLIERS)
    for token in (override_csv or "").replace(";", ",").split(","):
        root, _, units = token.partition(":")
        try:
            value = int(float(units))
        except ValueError:
            continue
        if root.strip() and value > 0:
            table[root.strip().upper()] = value
    return table


@dataclass(slots=True)
class McxContract:
    root: str
    symbol: str
    option_type: str
    strike: float
    expiry: str
    futures_symbol: str
    futures_expiry: str
    selection_price: float
    lot_size: int
    exchange_lot: int = 1
    volume: int = 0
    oi: int = 0
    retained: bool = False

    @classmethod
    def from_row(cls, row: dict) -> "McxContract":
        names = cls.__dataclass_fields__.keys()
        return cls(**{key: row[key] for key in names if key in row})


def choose_underlying(option: McxListed, futures: list[McxListed]) -> McxListed | None:
    """The futures contract an option series exercises into.

    The master's underlying FyToken names it when present. Otherwise it is the
    nearest futures series expiring on or after the option -- MCX options
    expire a few business days before their underlying futures.
    """
    if option.underlying_token:
        for row in futures:
            if row.token and row.token == option.underlying_token:
                return row
    later = sorted((row for row in futures if row.expiry >= option.expiry), key=lambda row: row.expiry)
    if later:
        return later[0]
    return min(futures, key=lambda row: row.expiry) if futures else None


def plan_selection(listed: list[McxListed], roots: list[str], min_days: int,
                   today: date | None = None) -> tuple[dict[str, dict], dict[str, str]]:
    """Per root: the option expiry to trade, its strikes, and its underlying futures."""
    plans: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for root in roots:
        options = [row for row in listed if row.root == root and row.kind in {"CE", "PE"}]
        futures = [row for row in listed if row.root == root and row.kind == "FUT"]
        if not options:
            errors[root] = "no options of this root in the MCX instrument master"
            continue
        expiries = sorted({row.expiry for row in options})
        chosen = first_tradable([Expiry(day) for day in expiries], min_days, today)
        if chosen is None:
            errors[root] = f"no option series at least {min_days} day(s) from expiry"
            continue
        series = [row for row in options if row.expiry == chosen.date]
        underlying = choose_underlying(series[0], futures)
        if underlying is None:
            errors[root] = "no futures contract to price the option series"
            continue
        plans[root] = {"expiry": chosen.date, "series": series, "futures": underlying,
                       "rolled": chosen.date != expiries[0]}
    return plans, errors


def pick_atm(series: list[McxListed], reference: float, option_type: str,
             liquidity: dict[str, tuple[int, int]]) -> tuple[McxListed, int, int] | None:
    """The MACD lane's ATM rule; the side-rounded anchor when no liquidity is known."""
    entries = []
    for row in series:
        if row.kind != option_type:
            continue
        volume, oi = liquidity.get(row.symbol, (0, 0))
        entries.append(OptionChainEntry(row.strike, row.kind, row.symbol, volume=volume, oi=oi))
    anchor, nearby = liquidity_window(entries, reference, option_type)
    if anchor is None:
        return None
    if all(max(entry.volume, entry.oi / 100) <= 0 for entry in nearby):
        pick = anchor
    else:
        pick = select_liquid_contract(entries, reference, option_type) or anchor
    by_symbol = {row.symbol: row for row in series}
    return by_symbol[pick.symbol], pick.volume, pick.oi


# ------------------------------------------------------------------ the lane

class McxLaneEvents:
    """Publishes the lane's discrete events under ``mcx_`` names.

    Portfolio, indicator and candle frames are dropped: ExecutionManager sends
    a portfolio frame on every mark change, the gateway does not coalesce
    unknown event types, and the MCX page reads the lane's REST snapshot.
    """

    FORWARDED = frozenset({"order", "trade", "signal", "position_closed", "decision"})

    def __init__(self, events: EventHub):
        self._events = events

    def publish(self, event_type: str, data) -> None:
        if event_type in self.FORWARDED:
            self._events.publish(f"mcx_{event_type}", data)

    @property
    def client_count(self) -> int:
        return self._events.client_count


def mcx_execution_settings(settings: Settings) -> Settings:
    """The MACD lane's risk rules with the MCX lane's capital, sizing and caps.

    Always paper: nothing in this lane can reach a live order API.
    """
    return settings.model_copy(update={
        "execution_mode": "paper",
        "allow_live_orders": False,
        "initial_capital": settings.mcx_initial_capital,
        "auto_trade": settings.mcx_auto_trade,
        "target_position_notional": settings.mcx_target_notional,
        "max_target_entry_lots": settings.mcx_max_entry_lots,
        "max_positions": settings.mcx_max_positions,
        "min_cash_reserve": 0.0,
    })


class McxPaperBroker:
    """The order surface of this lane's ExecutionManager. Paper fills never call it."""

    name = "mcx-paper"

    async def place_order(self, _order):
        raise PermissionError("The MCX lane is paper-only")


@dataclass(slots=True)
class Decision:
    at: str
    symbol: str
    root: str
    option_type: str
    verdict: str
    price: float
    macd: float
    bar: int | None
    lots: int = 0
    detail: str = ""
    order_id: str | None = None


# Verdicts for a signal the strategy fired.
TAKEN = "TAKEN"
AUTO_TRADE_OFF = "AUTO_TRADE_OFF"
ALREADY_HELD = "ALREADY_HELD"
NO_CASH = "NO_CASH"
ENTRY_REJECTED = "ENTRY_REJECTED"
STALE_BAR = "STALE_BAR"
DISABLED = "DISABLED"
SESSION_CLOSED = "SESSION_CLOSED"
NOT_SELECTED = "NOT_SELECTED"

REST_POLL_INTERVAL_SECONDS = 15.0
MAINTENANCE_INTERVAL_SECONDS = 15.0
SIGNAL_STALENESS_GRACE_SECONDS = 120
WARMUP_ATTEMPTS = 4
WARMUP_BACKOFF_SECONDS = (4.0, 12.0, 30.0)
WARMUP_PACING_SECONDS = 0.3


def _lane_file(settings: Settings, configured: str, name: str) -> str:
    """The lane's file: where it is configured, else beside the MACD book (as the agentic and spot lanes do)."""
    if configured != str(RUNTIME_DIR / name):
        return configured
    return str(Path(settings.database_path).parent / name)


class McxLane:
    """Select, warm, signal, trade and report the MCX options lane."""

    def __init__(self, settings: Settings, broker, events: EventHub):
        self.settings = settings
        self.events = McxLaneEvents(events)
        self.broker = broker
        self.calendar = McxCalendar(settings.mcx_holidays_csv, settings.mcx_evening_only_csv)
        self.snapshot_path = Path(_lane_file(settings, settings.mcx_contract_snapshot_path, "mcx_contracts.json"))
        self.repository = TradeRepository(_lane_file(settings, settings.mcx_database_path, "mcx_trader.sqlite3"))
        self.portfolio = Portfolio(settings.mcx_initial_capital, lane=LANE)
        self.execution = ExecutionManager(
            mcx_execution_settings(settings), McxPaperBroker(), self.portfolio, self.repository, self.events,
        )
        self.execution.set_tradable_contracts({})
        self.latest: dict[str, Tick] = {}
        self.execution.quote_source = self.latest.get
        self.contracts: dict[str, McxContract] = {}      # symbol -> today's selected or held contract
        self.futures_by_root: dict[str, str] = {}
        self.selection: dict = {"date": None, "selected_at": None, "roots": {}, "errors": {}}
        self.decisions: deque[dict] = deque(maxlen=200)
        self.decision_counts: dict[str, int] = defaultdict(int)
        self.history: dict[str, deque[Candle]] = defaultdict(lambda: deque(maxlen=500))
        self._warming: set[str] = set()
        self._warm_buffer: dict[str, list[Candle]] = defaultdict(list)
        self.warm_report: dict = {}
        self.ws_ticks = 0
        self.ws_ticks_day: str | None = None
        self.ws_ticks_today = 0
        self.rest_polls = 0
        self.rest_ticks = 0
        self.rest_error: str | None = None
        self._last_ws_at: dict[str, datetime] = {}
        self._last_poll_at = 0.0
        self._prepared_at: datetime | None = None
        self.signals_emitted = 0
        self.signals_dropped_stale = 0
        self.lane_errors: dict[str, dict] = {}
        self.error: str | None = None
        self.multiplier_warnings: dict[str, str] = {}
        self._flushed_day: str | None = None
        self._held_saved: frozenset[str] = frozenset()
        self._task: asyncio.Task | None = None
        self._allow_history()
        self._build_strategy()
        self._restore()
        self._restore_contracts()

    # ---------------------------------------------------------------- settings

    @property
    def roots(self) -> list[str]:
        chosen = [item.strip().upper() for item in self.settings.mcx_roots_csv.split(",") if item.strip()]
        return list(dict.fromkeys(chosen))

    @property
    def timeframe_seconds(self) -> int:
        return int(self.settings.mcx_timeframe_seconds or self.settings.timeframe_seconds)

    @property
    def multipliers(self) -> dict[str, int]:
        return lot_multipliers(self.settings.mcx_lot_multipliers_csv)

    def _build_strategy(self) -> None:
        self.strategy = MACDStrategyManager(self.settings, self.events, [])
        self.aggregator = McxCandleAggregator(self.timeframe_seconds, self.calendar)

    def apply_settings(self, settings: Settings) -> dict:
        """Take new settings; report what needs a reselection or a re-warm."""
        previous = self.settings
        self.settings = settings
        self.calendar = McxCalendar(settings.mcx_holidays_csv, settings.mcx_evening_only_csv)
        risk = mcx_execution_settings(settings)
        delta = settings.mcx_initial_capital - self.portfolio.initial_capital
        if delta:
            self.portfolio.initial_capital = settings.mcx_initial_capital
            self.portfolio.cash += delta
        self.execution.settings = risk
        for position in self.portfolio.positions.values():
            position.hard_stop = round(position.average_price * (1 - risk.hard_stop_pct), 4)
        self._allow_history()
        # Switched on: the warm-up that was refused can now run, so start it again from empty.
        history_allowed = settings.mcx_history_downloads and not previous.mcx_history_downloads
        timeframe_changed = (int(previous.mcx_timeframe_seconds or previous.timeframe_seconds)
                             != self.timeframe_seconds)
        # Anything that changes how an indicator is built needs fresh state and
        # a re-warm; thresholds the strategy reads per bar only need the object.
        strategy_changed = any(getattr(previous, key) != getattr(settings, key) for key in (
            "fast_period", "slow_period", "signal_period", "bb_period", "bb_deviations", "kama_period",
            "kama_fast", "kama_slow", "kama_rsi_period", "kama_roc_period"))
        if timeframe_changed or strategy_changed or history_allowed:
            self._reset_strategy()
        else:
            self.strategy.settings = settings
            self.aggregator.calendar = self.calendar
        return {
            "reselect": (previous.mcx_enabled != settings.mcx_enabled
                         or previous.mcx_roots_csv != settings.mcx_roots_csv
                         or previous.mcx_min_days_to_expiry != settings.mcx_min_days_to_expiry
                         or previous.mcx_lot_multipliers_csv != settings.mcx_lot_multipliers_csv),
            "rewarm": timeframe_changed or strategy_changed or history_allowed,
        }

    def rebind_broker(self, broker) -> None:
        self.broker = broker
        self._allow_history()

    def _allow_history(self) -> None:
        """Tell the broker whether MCX history may pass the engine-wide download hold."""
        try:
            self.broker.mcx_history_allowed = bool(self.settings.mcx_history_downloads)
        except AttributeError:      # a stand-in broker without attributes
            pass

    # ----------------------------------------------------------------- restore

    def _restore(self) -> None:
        rows = sorted(self.repository.rows("trades", 100_000), key=lambda row: row["timestamp"])
        for row in rows:
            try:
                self.portfolio.apply_trade(Trade(
                    order_id=row["order_id"], symbol=row["symbol"], side=row["side"],
                    quantity=int(row["quantity"]), price=float(row["price"]),
                    lots=int(row.get("lots", 1)), lot_size=int(row.get("lot_size", 1)),
                    fees=float(row.get("fees", 0.0)),
                    timestamp=datetime.fromisoformat(row["timestamp"]), trade_id=row["trade_id"],
                ))
            except (KeyError, ValueError, TypeError):
                continue
        stored = self.repository.load_position_excursions()
        risk = self.execution.settings
        for position in self.portfolio.positions.values():
            row = stored.get(position.position_id)
            if row:
                position.max_price = max(position.max_price, float(row["max_price"]))
                position.min_price = min(position.min_price or float(row["min_price"]), float(row["min_price"]))
                position.max_return_pct = max(position.max_return_pct, float(row["max_return_pct"]))
                position.min_return_pct = min(position.min_return_pct, float(row["min_return_pct"]))
            position.peak_price = max(position.peak_price, position.average_price, position.max_price)
            position.hard_stop = round(position.average_price * (1 - risk.hard_stop_pct), 4)
            position.entry_anchor = position.entry_anchor or position.average_price
            position.entry_stage = max(position.entry_stage, position.lots)
            if position.quantity > 0 and position.peak_price >= position.average_price * (1 + risk.trailing_activation_pct):
                floor = breakeven_exit_price(position.average_price, position.quantity, risk.slippage_bps)
                trail = position.peak_price * (1 - risk.trailing_stop_pct)
                position.trailing_stop = round(max(trail, floor), 4)

    def _load_snapshot(self) -> dict:
        try:
            return json.loads(self.snapshot_path.read_text())
        except (OSError, ValueError):
            return {}

    def _restore_contracts(self) -> None:
        """Held contracts keep their recorded expiry, futures and multiplier across restarts."""
        payload = self._load_snapshot()
        rows = {row.get("symbol"): row for row in payload.get("contracts") or [] if isinstance(row, dict)}
        rows.update({symbol: row for symbol, row in (payload.get("held") or {}).items() if isinstance(row, dict)})
        for symbol in self.portfolio.positions:
            row = rows.get(symbol)
            if row:
                try:
                    contract = McxContract.from_row(row)
                except TypeError:
                    continue
                contract.retained = True
                self.contracts[symbol] = contract
        self._apply_contracts()

    def _apply_contracts(self) -> None:
        lots = {symbol: row.lot_size for symbol, row in self.contracts.items() if row.lot_size > 0}
        held = {symbol: position.lot_size for symbol, position in self.portfolio.positions.items()
                if position.quantity > 0 and position.lot_size > 0}
        self.execution.set_tradable_contracts({**held, **lots})
        self.execution.set_contract_expiries({symbol: row.expiry for symbol, row in self.contracts.items()})

    def _save_snapshot(self) -> None:
        held = {symbol: asdict(self.contracts[symbol]) for symbol in self.portfolio.positions if symbol in self.contracts}
        payload = {
            "date": self.selection.get("date"),
            "selected_at": self.selection.get("selected_at"),
            "timeframe_seconds": self.timeframe_seconds,
            "roots": self.selection.get("roots") or {},
            "errors": self.selection.get("errors") or {},
            "contracts": [asdict(row) for row in self.contracts.values() if not row.retained],
            "held": held,
        }
        try:
            self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            self.snapshot_path.write_text(json.dumps(payload, indent=2))
            self.snapshot_path.chmod(0o600)
        except OSError as exc:
            self.error = f"contract snapshot not saved: {exc}"
        self._held_saved = frozenset(held)

    # --------------------------------------------------------------- selection

    def owns(self, symbol: str) -> bool:
        return symbol.startswith(EXCHANGE + ":")

    def option_symbols(self) -> list[str]:
        """Today's selection: the only contracts a new entry may use."""
        return [symbol for symbol, row in self.contracts.items() if not row.retained]

    def strategy_symbols(self) -> list[str]:
        """Contracts that get bars and MACD: today's selection plus every holding.

        A holding carried from an earlier selection still needs its MACD for
        the invalidation exit, as on the MACD lane.
        """
        return list(dict.fromkeys([*self.option_symbols(),
                                   *[s for s in self.portfolio.positions if s in self.contracts]]))

    def subscription_symbols(self) -> list[str]:
        """Everything the lane needs streamed: today's options, their futures, every holding."""
        if not self.settings.mcx_enabled:
            return list(dict.fromkeys(self.portfolio.positions))
        return list(dict.fromkeys([
            *self.option_symbols(), *self.futures_by_root.values(), *self.portfolio.positions,
        ]))

    def known_expiries(self) -> dict[str, str]:
        expiries = {symbol: row.expiry for symbol, row in self.contracts.items()}
        for row in self.contracts.values():
            if row.futures_symbol:
                expiries.setdefault(row.futures_symbol, row.futures_expiry)
        return expiries

    async def prepare(self, *, force: bool = False) -> list[str]:
        """Settle expired holdings, select today's contracts, reset strategy state.

        Never raises: a failure is recorded and the lane subscribes whatever it
        still has (its holdings at least), so the NSE feed is never blocked.
        """
        try:
            await self._settle_expired_holdings()
        except Exception as exc:  # noqa: BLE001
            self._lane_error("settle", exc)
        if not self.settings.mcx_enabled:
            self.contracts = {s: c for s, c in self.contracts.items() if s in self.portfolio.positions}
            for contract in self.contracts.values():
                contract.retained = True
            self.futures_by_root = {}
            self._apply_contracts()
            return self.subscription_symbols()
        try:
            await self._select(force=force)
        except Exception as exc:  # noqa: BLE001 - the NSE feed must not wait on MCX
            self.error = f"contract selection failed: {type(exc).__name__}: {exc}"[:300]
            if self.selection.get("date") != datetime.now(IST).date().isoformat() or not self.option_symbols():
                # Never trade on yesterday's strikes: keep only what is held.
                self.contracts = {s: c for s, c in self.contracts.items() if s in self.portfolio.positions}
                for contract in self.contracts.values():
                    contract.retained = True
                self.futures_by_root = {}
        self._reset_strategy()
        self._apply_contracts()
        self._save_snapshot()
        await self._mark_holdings()
        self._prepared_at = datetime.now(UTC)
        return self.subscription_symbols()

    def _reset_strategy(self) -> None:
        """Fresh MACD state; live bars are buffered until warm() replays history."""
        self._build_strategy()
        self.history.clear()
        self._warm_buffer.clear()
        self._warming = set(self.strategy_symbols())
        self.warm_report = {"state": "pending", "symbols": len(self._warming)}

    async def _select(self, *, force: bool) -> None:
        today = datetime.now(IST).date()
        saved = self._load_snapshot()
        held_symbols = set(self.portfolio.positions)
        if not force and saved.get("date") == today.isoformat():
            # A same-day restart keeps the morning's strikes, as the MACD lane does.
            rows = []
            for row in saved.get("contracts") or []:
                try:
                    contract = McxContract.from_row(row)
                except (TypeError, KeyError):
                    continue
                remaining = days_to_expiry(contract.expiry, today)
                if (contract.root in self.roots and remaining is not None
                        and remaining >= self.settings.mcx_min_days_to_expiry):
                    rows.append(contract)
            legs = {(row.root, row.option_type) for row in rows}
            if all((root, side) in legs for root in self.roots for side in ("CE", "PE")):
                roots = {root: value for root, value in (saved.get("roots") or {}).items() if root in self.roots}
                self._install(rows, roots, saved.get("errors") or {}, saved.get("selected_at"), source="saved")
                return
        reader = getattr(self.broker, "instrument_rows", None)
        if reader is None:
            raise RuntimeError("the MCX lane needs the Fyers feed (no instrument master on this broker)")
        rows = await reader(EXCHANGE)
        listed = parse_mcx_master(rows, set(self.roots))
        plans, errors = plan_selection(listed, self.roots, self.settings.mcx_min_days_to_expiry, today)
        if not plans:
            self.selection = {"date": today.isoformat(), "selected_at": datetime.now(IST).isoformat(timespec="seconds"),
                              "roots": {}, "errors": errors, "source": "master"}
            self.contracts = {s: c for s, c in self.contracts.items() if s in held_symbols}
            raise RuntimeError("no MCX root could be selected: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))
        futures = sorted({plan["futures"].symbol for plan in plans.values()})
        marks = await self.broker.quotes(futures)
        references: dict[str, float] = {}
        for root, plan in list(plans.items()):
            tick = marks.get(plan["futures"].symbol)
            if tick is None or not tick.ltp or tick.ltp <= 0:
                errors[root] = f"no price for {plan['futures'].symbol}"
                plans.pop(root)
                continue
            references[root] = float(tick.ltp)
        window: dict[str, McxListed] = {}
        for root, plan in plans.items():
            for side in ("CE", "PE"):
                entries = [OptionChainEntry(row.strike, row.kind, row.symbol) for row in plan["series"] if row.kind == side]
                for entry in liquidity_window(entries, references[root], side)[1]:
                    window[entry.symbol] = next(row for row in plan["series"] if row.symbol == entry.symbol)
        liquidity: dict[str, tuple[int, int]] = {}
        if window:
            try:
                quotes = await self.broker.quotes(sorted(window))
                liquidity = {symbol: (int(tick.volume or 0), int(tick.open_interest or 0)) for symbol, tick in quotes.items()}
            except Exception as exc:  # noqa: BLE001 - fall back to the plain side-rounded ATM
                errors["*"] = f"strike liquidity quotes failed, side-rounded ATM used: {exc}"[:200]
        multipliers = self.multipliers
        picked: list[McxContract] = []
        summary: dict[str, dict] = {}
        self.multiplier_warnings = {}
        for root, plan in plans.items():
            units = multipliers.get(root)
            if not units:
                errors[root] = "no lot multiplier for this root; set mcx_lot_multipliers_csv"
                continue
            reference = references[root]
            legs = {}
            for side in ("CE", "PE"):
                choice = pick_atm(plan["series"], reference, side, liquidity)
                if choice is None:
                    continue
                row, volume, oi = choice
                if row.exchange_lot not in {1, units}:
                    self.multiplier_warnings[root] = (
                        f"instrument master lot {row.exchange_lot} differs from the {units}-unit multiplier in use")
                legs[side] = McxContract(
                    root=root, symbol=row.symbol, option_type=side, strike=row.strike, expiry=row.expiry,
                    futures_symbol=plan["futures"].symbol, futures_expiry=plan["futures"].expiry,
                    selection_price=reference, lot_size=units, exchange_lot=row.exchange_lot,
                    volume=volume, oi=oi,
                )
            if set(legs) != {"CE", "PE"}:
                errors[root] = "the option series did not give both an ATM call and put"
                continue
            picked.extend(legs.values())
            errors.pop(root, None)
            summary[root] = {
                "expiry": plan["expiry"], "futures": plan["futures"].symbol, "futures_expiry": plan["futures"].expiry,
                "reference": reference, "rolled": plan["rolled"], "lot_multiplier": units,
                "strikes": {side: legs[side].strike for side in legs},
            }
        self._install(picked, summary, errors, datetime.now(IST).isoformat(timespec="seconds"), source="master")

    def _install(self, rows: list[McxContract], roots: dict, errors: dict, selected_at, *, source: str) -> None:
        today = datetime.now(IST).date().isoformat()
        held = {symbol: contract for symbol, contract in self.contracts.items() if symbol in self.portfolio.positions}
        self.contracts = {row.symbol: row for row in rows}
        for symbol, contract in held.items():
            if symbol not in self.contracts:
                contract.retained = True
                self.contracts[symbol] = contract
        self.futures_by_root = {row.root: row.futures_symbol for row in rows}
        self.selection = {"date": today, "selected_at": selected_at, "roots": roots, "errors": errors, "source": source}
        self.error = None if rows else self.error

    async def _mark_holdings(self) -> None:
        """Value holdings after a restart from one REST quote; never runs exits."""
        symbols = [symbol for symbol in self.portfolio.positions if symbol not in self.latest]
        quoter = getattr(self.broker, "quotes", None)
        if not symbols or quoter is None:
            return
        try:
            quotes = await quoter(symbols)
        except Exception as exc:  # noqa: BLE001
            self._lane_error("marks", exc)
            return
        for symbol, tick in quotes.items():
            if tick.ltp and tick.ltp > 0:
                self.portfolio.mark(symbol, tick.ltp)

    async def _settle_expired_holdings(self) -> None:
        """A holding whose contract expired while the app was down closes at its last mark."""
        today = datetime.now(IST).date()
        for symbol, position in list(self.portfolio.positions.items()):
            contract = self.contracts.get(symbol)
            remaining = days_to_expiry(contract.expiry, today) if contract else None
            if remaining is None or remaining >= 0 or position.quantity <= 0:
                continue
            price = position.last_price if position.last_price > 0 else position.average_price
            await self.execution.settle_expired(symbol, price, "EXPIRED_WHILE_OFFLINE_LAST_MARK")

    # ----------------------------------------------------------------- warm-up

    async def warm(self) -> dict:
        """One-minute history for today's contracts, folded onto the lane's bars."""
        symbols = sorted(self._warming)
        started = datetime.now(UTC)
        loaded, failed = 0, {}
        history = getattr(self.broker, "history_range", None)
        for symbol in symbols:
            end = datetime.now(UTC)
            start = end - timedelta(days=self.settings.mcx_warmup_days)
            # The bar forming at download time is incomplete in the history;
            # the live aggregator owns it from here.
            forming = mcx_bucket(int(end.timestamp()), self.timeframe_seconds, self.calendar)
            minutes: list[Candle] = []
            if history is not None:
                for attempt in range(WARMUP_ATTEMPTS):
                    try:
                        minutes = await history(symbol, 60, start, end)
                        break
                    except Exception as exc:  # noqa: BLE001
                        failed[symbol] = f"{type(exc).__name__}: {exc}"[:200]
                        if attempt < WARMUP_ATTEMPTS - 1:
                            limited = "429" in str(exc) or "Too Many Requests" in str(exc)
                            await asyncio.sleep(WARMUP_BACKOFF_SECONDS[attempt] if limited else 0.5 * (attempt + 1))
                await asyncio.sleep(WARMUP_PACING_SECONDS)
            if minutes:
                failed.pop(symbol, None)
                loaded += 1
            bars = aggregate_minutes(minutes, self.timeframe_seconds, self.calendar)
            self._finish_warmup(symbol, [bar for bar in bars if forming is None or bar.timestamp < forming])
        self.warm_report = {
            "state": "done", "symbols": len(symbols), "loaded": loaded, "failed": failed,
            "finished_at": datetime.now(IST).isoformat(timespec="seconds"),
            "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        }
        return self.warm_report

    def _finish_warmup(self, symbol: str, bars: list[Candle]) -> None:
        """Replay history, then the live bars that closed meanwhile, without trading on either."""
        if symbol not in self._warming:
            return
        current = self.aggregator.current.get(symbol)
        cutoff = current.timestamp if current is not None else None
        replay = [bar for bar in bars if cutoff is None or bar.timestamp < cutoff]
        last = replay[-1].timestamp if replay else -1
        # Buffered live bars only extend the history: never an earlier or the same bar twice.
        replay += [bar for bar in self._warm_buffer.pop(symbol, []) if bar.timestamp > last]
        for bar in replay:
            self.history[symbol].append(bar)
            self.strategy.on_closed_candle(bar, warmup=True)
        self._warming.discard(symbol)

    # -------------------------------------------------------------------- ticks

    def session_open(self, moment: datetime | None = None) -> bool:
        return self.calendar.in_session(moment or datetime.now(UTC))

    def feed_expected(self, moment: datetime | None = None) -> bool:
        """Should the engine's watchdog treat silence now as a dead socket?

        Only once this lane has had a websocket tick today: an account without
        MCX market data must not make the watchdog recycle the socket all evening.
        """
        moment = moment or datetime.now(UTC)
        return (self.settings.mcx_enabled and bool(self.subscription_symbols()) and self.session_open(moment)
                and self.ws_ticks_day == moment.astimezone(IST).date().isoformat() and self.ws_ticks_today > 0)

    async def on_tick(self, tick: Tick, received_at: datetime | None = None, *, source: str = "ws") -> None:
        """Every MCX print the engine receives, and every REST fallback quote."""
        received_at = received_at or datetime.now(UTC)
        exchange_at = tick.timestamp if tick.timestamp.tzinfo else tick.timestamp.replace(tzinfo=UTC)
        previous = self.latest.get(tick.symbol)
        if previous is not None:
            previous_at = previous.timestamp if previous.timestamp.tzinfo else previous.timestamp.replace(tzinfo=UTC)
            if exchange_at < previous_at or (source == "rest" and exchange_at == previous_at):
                return
        self.latest[tick.symbol] = tick
        if source == "ws":
            self.ws_ticks += 1
            self._last_ws_at[tick.symbol] = received_at
            day = received_at.astimezone(IST).date().isoformat()
            if self.ws_ticks_day != day:
                self.ws_ticks_day, self.ws_ticks_today = day, 0
            self.ws_ticks_today += 1
        else:
            self.rest_ticks += 1
        current = (exchange_at.astimezone(IST).date() == received_at.astimezone(IST).date()
                   and self.calendar.in_session(exchange_at) and self.calendar.in_session(received_at))
        # A REST quote is executable only if its own trade is recent: an
        # illiquid strike's last print can be minutes old.
        fresh_at = received_at if source == "ws" else exchange_at
        if tick.symbol in self.portfolio.positions or tick.symbol in self.execution.tradable_symbols:
            try:
                if current:
                    await self.execution.on_tick(tick.symbol, tick.ltp, fresh_at)
                    # on_tick skips the mark for a quote too old to trade on;
                    # it is still the best valuation there is.
                    self.portfolio.mark(tick.symbol, tick.ltp)
                    await self._exit_if_expiring(tick.symbol, received_at)
                else:
                    self.portfolio.mark(tick.symbol, tick.ltp)
            except Exception as exc:  # noqa: BLE001 - one bad print must not stop the feed
                self._lane_error("execution", exc, tick.symbol)
        contract = self.contracts.get(tick.symbol)
        if not current or contract is None or not self.settings.mcx_enabled:
            return
        if contract.retained and tick.symbol not in self.portfolio.positions:
            return
        candle, closed = self.aggregator.on_tick(tick)
        if closed is not None:
            await self._on_closed_bar(closed)
        if candle is not None and tick.symbol not in self._warming:
            signal = self.strategy.on_live_candle(candle)
            if signal is not None:
                await self._on_signal(signal)

    async def _on_closed_bar(self, candle: Candle) -> None:
        if candle.symbol in self._warming:
            self._warm_buffer[candle.symbol].append(candle)
            return
        self.history[candle.symbol].append(candle)
        signal = self.strategy.on_closed_candle(candle)
        point = self.strategy.points.get(candle.symbol)
        if point is not None:
            try:
                await self.execution.on_closed_bar(candle.symbol, point.macd)
            except Exception as exc:  # noqa: BLE001
                self._lane_error("invalidation", exc, candle.symbol)
        if signal is not None:
            await self._on_signal(signal)

    def _bar_is_current(self, signal: Signal) -> bool:
        bar = signal.evaluated_candle_timestamp
        if bar is None:
            return True
        return datetime.now(UTC).timestamp() - bar <= self.timeframe_seconds + SIGNAL_STALENESS_GRACE_SECONDS

    async def _on_signal(self, signal: Signal) -> None:
        if not self._bar_is_current(signal):
            self.signals_dropped_stale += 1
            self._decide(signal, STALE_BAR, detail="the signal's bar is from an earlier period")
            return
        if not self.repository.save_signal(signal):
            return
        self.signals_emitted += 1
        if signal.side != "BUY":
            return
        if not self.settings.mcx_enabled:
            self._decide(signal, DISABLED)
            return
        if signal.symbol in self.portfolio.positions:
            self._decide(signal, ALREADY_HELD)
            return
        contract = self.contracts.get(signal.symbol)
        if contract is None or contract.retained:
            self._decide(signal, NOT_SELECTED, detail="not in today's selection; entries use today's ATM legs only")
            return
        if not self.session_open():
            self._decide(signal, SESSION_CLOSED)
            return
        if not self.settings.mcx_auto_trade:
            self._decide(signal, AUTO_TRADE_OFF)
            return
        price = self.execution.last_prices.get(signal.symbol, signal.price)
        lots = self.execution.entry_lots(signal.symbol, price)
        if lots < 1:
            self._decide(signal, NO_CASH, detail="one lot is more than the lane's free cash")
            return
        try:
            order = await self.execution.submit(signal.symbol, "BUY", lots=lots, signal=signal)
        except (ValueError, RuntimeError, PermissionError) as exc:
            self._decide(signal, ENTRY_REJECTED, lots=lots, detail=str(exc)[:200])
            return
        if order.status == "FILLED":
            self._decide(signal, TAKEN, lots=lots, order_id=order.order_id)
            self._save_snapshot()
        else:
            self._decide(signal, ENTRY_REJECTED, lots=lots, detail=f"order {order.status}", order_id=order.order_id)

    def _decide(self, signal: Signal, verdict: str, *, lots: int = 0, detail: str = "",
                order_id: str | None = None) -> None:
        contract = self.contracts.get(signal.symbol)
        row = Decision(
            at=datetime.now(UTC).isoformat(), symbol=signal.symbol,
            root=contract.root if contract else "", option_type=contract.option_type if contract else signal.symbol[-2:],
            verdict=verdict, price=signal.price, macd=signal.macd, bar=signal.evaluated_candle_timestamp,
            lots=lots, detail=detail, order_id=order_id,
        )
        payload = asdict(row)
        self.decisions.appendleft(payload)
        self.decision_counts[verdict] += 1
        self.events.publish("decision", payload)

    # ------------------------------------------------------------------ expiry

    def flatten_time(self) -> tuple[int, int] | None:
        raw = (self.settings.mcx_expiry_flatten_ist or "").strip()
        if not raw:
            return None
        try:
            hour, minute = (int(part) for part in raw.split(":", 1))
        except ValueError:
            return None
        return hour, minute

    def expiring_positions(self, today: date) -> list[str]:
        expiring = []
        for symbol, position in self.portfolio.positions.items():
            contract = self.contracts.get(symbol)
            remaining = days_to_expiry(contract.expiry, today) if contract else None
            if position.quantity > 0 and remaining is not None and remaining <= 0:
                expiring.append(symbol)
        return expiring

    async def _exit_if_expiring(self, symbol: str, now: datetime) -> bool:
        cutoff = self.flatten_time()
        local = now.astimezone(IST)
        if cutoff is None or (local.hour, local.minute) < cutoff:
            return False
        if symbol not in self.expiring_positions(local.date()):
            return False
        return await self.flatten(symbol, f"MCX_EXPIRY_FLATTEN_{cutoff[0]:02d}{cutoff[1]:02d}_IST")

    async def flatten(self, symbol: str, reason: str) -> bool:
        position = self.portfolio.positions.get(symbol)
        if not position or position.quantity <= 0:
            return False
        self.execution.arm_exit(symbol, reason)
        try:
            await self.execution.submit(symbol, "SELL", position.quantity)
        except (ValueError, RuntimeError, PermissionError) as exc:
            self.execution._armed_exits.discard(symbol)
            self._lane_error("flatten", exc, symbol)
            return False
        return symbol not in self.portfolio.positions

    # ------------------------------------------------------------- maintenance

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._maintenance_loop())

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
            try:
                await self.maintain()
            except Exception as exc:  # noqa: BLE001 - this loop must survive
                self._lane_error("maintenance", exc)

    async def maintain(self, now: datetime | None = None) -> None:
        """Session close, expiry flatten, REST fallback, persistence."""
        now = now or datetime.now(UTC)
        local = now.astimezone(IST)
        today = local.date()
        if (self.calendar.is_trading_day(today) and local.time().replace(tzinfo=None) >= self.calendar.closes_at(today)
                and self._flushed_day != today.isoformat()):
            self._flushed_day = today.isoformat()
            for candle in self.aggregator.flush():
                await self._on_closed_bar(candle)
        cutoff = self.flatten_time()
        if (cutoff is not None and self.calendar.is_trading_day(today) and (local.hour, local.minute) >= cutoff
                and self.calendar.in_session(now)):
            for symbol in self.expiring_positions(today):
                await self.flatten(symbol, f"MCX_EXPIRY_FLATTEN_{cutoff[0]:02d}{cutoff[1]:02d}_IST")
        if self.calendar.in_session(now):
            await self.poll_stale_quotes(now)
        self.execution.sweep_closed_positions()
        if self.portfolio.positions or self.calendar.in_session(now):
            self.repository.save_equity_point(self.portfolio.snapshot())
        if self.portfolio.positions:
            self.repository.save_position_excursions(self.portfolio.positions.values())
        if frozenset(self.portfolio.positions) != self._held_saved:
            self._save_snapshot()

    async def poll_stale_quotes(self, now: datetime | None = None) -> int:
        """REST quotes for lane contracts the websocket has not priced lately."""
        limit = float(self.settings.mcx_rest_fallback_seconds or 0)
        quoter = getattr(self.broker, "quotes", None)
        if limit <= 0 or quoter is None:
            return 0
        now = now or datetime.now(UTC)
        clock = now.timestamp()
        if clock - self._last_poll_at < REST_POLL_INTERVAL_SECONDS:
            return 0
        # Silence is measured from the later of the last websocket tick, the
        # subscription and today's open: a socket gets `limit` seconds to speak.
        local = now.astimezone(IST)
        opened = datetime.combine(local.date(), self.calendar.opens_at(local.date()), IST)
        floor = max(opened, self._prepared_at or opened)
        stale = [symbol for symbol in self.subscription_symbols()
                 if (now - max(self._last_ws_at.get(symbol, floor), floor)).total_seconds() > limit]
        if not stale:
            return 0
        self._last_poll_at = clock
        self.rest_polls += 1
        try:
            quotes = await quoter(stale)
            self.rest_error = None
        except Exception as exc:  # noqa: BLE001
            self.rest_error = f"{type(exc).__name__}: {exc}"[:200]
            return 0
        fed = 0
        for symbol in stale:
            tick = quotes.get(symbol)
            if tick is None or not tick.ltp or tick.ltp <= 0:
                continue
            await self.on_tick(tick, now, source="rest")
            fed += 1
        return fed

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        try:
            self.repository.save_position_excursions(self.portfolio.positions.values())
        except Exception:  # noqa: BLE001
            pass
        self.repository.close()

    # ---------------------------------------------------------- manual orders

    async def manual_order(self, symbol: str, side: str, lots: int, *, order_type: str = "MARKET",
                           limit_price: float | None = None):
        if not self.owns(symbol):
            raise ValueError("The MCX lane trades MCX contracts only")
        return await self.execution.submit(symbol, side, lots=lots, order_type=order_type, limit_price=limit_price)

    # ---------------------------------------------------------------- reports

    def _lane_error(self, where: str, exc: Exception, symbol: str | None = None) -> None:
        entry = self.lane_errors.get(where, {"count": 0})
        self.lane_errors[where] = {
            "count": entry["count"] + 1,
            "last": f"{symbol + ': ' if symbol else ''}{type(exc).__name__}: {exc}"[:300],
            "at": datetime.now(UTC).isoformat(),
        }

    def settings_view(self) -> dict:
        current = self.settings
        return {
            "enabled": current.mcx_enabled,
            "auto_trade": current.mcx_auto_trade,
            "roots_csv": current.mcx_roots_csv,
            "initial_capital": current.mcx_initial_capital,
            "target_notional": current.mcx_target_notional,
            "max_entry_lots": current.mcx_max_entry_lots,
            "max_positions": current.mcx_max_positions,
            "timeframe_seconds": current.mcx_timeframe_seconds,
            "min_days_to_expiry": current.mcx_min_days_to_expiry,
            "expiry_flatten_ist": current.mcx_expiry_flatten_ist,
            "rest_fallback_seconds": current.mcx_rest_fallback_seconds,
            "lot_multipliers_csv": current.mcx_lot_multipliers_csv,
            "history_downloads": current.mcx_history_downloads,
        }

    def risk_view(self) -> dict:
        risk = self.execution.settings
        return {
            "inherited_from": "MACD lane",
            "entry_filter": self.settings.entry_filter_description,
            "macd_periods": [risk.fast_period, risk.slow_period, risk.signal_period],
            "hard_stop_pct": risk.hard_stop_pct * 100,
            "trailing_activation_profit_pct": risk.trailing_activation_pct * 100,
            "trailing_stop_pct": risk.trailing_stop_pct * 100,
            "max_trade_lots": risk.max_trade_lots,
            "scale_in_profit_pct": [7.5, 15, 22.5] if risk.max_trade_lots > 1 else [],
            "scale_out_profit_pct": [30, 50, 75] if risk.max_trade_lots > 1 else [],
            "macd_invalidation_exit": risk.macd_invalidation_exit,
            "slippage_bps": risk.slippage_bps,
            "target_position_notional": risk.target_position_notional,
            "max_positions": risk.max_positions,
            "last_exit_reasons": self.execution.exit_reasons,
        }

    def session_view(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        local = now.astimezone(IST)
        day = local.date()
        return {
            "open": self.calendar.in_session(now),
            "phase": self.calendar.phase(now),
            "opens_at": self.calendar.opens_at(day).strftime("%H:%M"),
            "closes_at": self.calendar.closes_at(day).strftime("%H:%M"),
            "next_open": self.calendar.next_open(now).isoformat(timespec="minutes"),
            "holiday": self.calendar.closed.get(day) or self.calendar.evening_only.get(day),
            "calendar_covers_year": self.calendar.covers_year(day.year),
        }

    def contract_rows(self) -> list[dict]:
        rows = []
        now = datetime.now(UTC)
        order = {root: index for index, root in enumerate(self.roots)}
        for symbol, contract in sorted(self.contracts.items(), key=lambda item: (
                order.get(item[1].root, len(order)), item[1].root, item[1].retained, item[1].option_type)):
            tick = self.latest.get(symbol)
            futures = self.latest.get(contract.futures_symbol)
            point = self.strategy.points.get(symbol)
            position = self.portfolio.positions.get(symbol)
            seen = self._last_ws_at.get(symbol)
            rows.append({
                **asdict(contract),
                "display": DISPLAY_NAMES.get(contract.root, contract.root),
                "ltp": tick.ltp if tick else None,
                "futures_ltp": futures.ltp if futures else None,
                "macd": point.macd if point else None,
                "signal_line": point.signal if point else None,
                "histogram": point.histogram if point else None,
                "bar": point.timestamp if point else None,
                "bars": len(self.history.get(symbol, ())),
                "warming": symbol in self._warming,
                "ws_age_seconds": round((now - seen).total_seconds(), 1) if seen else None,
                "evaluation": self.strategy.evaluations.get(symbol),
                "held_lots": position.lots if position else 0,
                "days_to_expiry": days_to_expiry(contract.expiry),
            })
        return rows

    def health(self) -> dict:
        return {
            "enabled": self.settings.mcx_enabled,
            "auto_trade": self.settings.mcx_auto_trade,
            "execution": "paper only",
            "roots": self.roots,
            "timeframe_seconds": self.timeframe_seconds,
            "session": self.session_view(),
            "selection": self.selection,
            "contracts": len(self.option_symbols()),
            "held": len(self.portfolio.positions),
            "subscribed": len(self.subscription_symbols()),
            "warmup": self.warm_report,
            "warming": len(self._warming),
            "ws_ticks": self.ws_ticks,
            "ws_ticks_today": self.ws_ticks_today if self.ws_ticks_day == datetime.now(IST).date().isoformat() else 0,
            "rest_fallback": {"seconds": self.settings.mcx_rest_fallback_seconds, "polls": self.rest_polls,
                              "ticks": self.rest_ticks, "error": self.rest_error},
            "signals_emitted": self.signals_emitted,
            "signals_dropped_stale": self.signals_dropped_stale,
            "decisions": dict(self.decision_counts),
            "bars_rejected_off_session": self.aggregator.rejected_off_session,
            "lot_multipliers": {root: self.multipliers.get(root) for root in self.roots},
            "multiplier_warnings": self.multiplier_warnings,
            "lane_errors": self.lane_errors,
            "error": self.error,
        }

    def snapshot(self) -> dict:
        return json_value({
            "lane": LANE,
            "settings": self.settings_view(),
            "health": self.health(),
            "risk": self.risk_view(),
            "contracts": self.contract_rows(),
            "portfolio": self.execution.portfolio_snapshot(),
            "closed_positions": self.execution.visible_closed_positions(),
            "decisions": list(self.decisions)[:60],
        })

    def book(self) -> dict:
        return json_value({
            "orders": self.repository.rows("orders", 500),
            "trades": self.repository.rows("trades", 500),
            "signals": self.repository.rows("signals", 500),
            "equity": self.repository.equity_rows(5000),
            "portfolio": self.execution.portfolio_snapshot(),
            "closed_positions": self.execution.visible_closed_positions(),
        })

