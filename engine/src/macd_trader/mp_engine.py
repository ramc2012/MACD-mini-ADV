"""Market-Profile + Order-Flow trading module.

A self-contained desk that runs alongside the MACD strategy and shares only
the broker tick stream. It keeps its **own order book, trade book, portfolio
and equity curve** in a separate SQLite file, so its results never mix with
the MACD book.

The thesis is auction-theory orthodox (Steidlmayer/Dalton) with order flow as
the trigger, which is how a real profile desk actually trades:

  * **Responsive buying at value** — price probes below the value area low,
    sellers are absorbed there (heavy sell aggression, no downside progress),
    price reclaims VAL. Fade the failed probe.
  * **Initiative range extension** — price extends beyond the initial balance
    with cumulative delta confirming. Join the initiating buyer.
  * **Failed-low divergence** — price makes a lower low while cumulative delta
    makes a higher low: the selling has no conviction behind it.

Long premium only, because the terminal buys options and cannot short them.
A CE is bought to express an up-move in the underlying's option, a PE for a
down-move; both are long-premium trades in their own contract, so every rule
below is expressed on the contract's own auction.
"""
from __future__ import annotations

from .market_calendar import is_trading_day

import asyncio
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from .directional import Bias, SessionTape, TapeBook, evaluate as evaluate_directional
from .footprint import FootprintBook, confidence_grade
from .candles import SESSION_CLOSE, SESSION_OPEN
from .market_profile import IST, Profile, ProfileBook, SESSION_OPEN_MINUTE, letter_for
from .models import Order, Signal, Trade
from .ofi import OFITracker
from .orderflow import FlowState, OrderFlowTracker
from .portfolio import CLOSED_VISIBLE_UNTIL, Portfolio, breakeven_exit_price, closed_visible_until
from .profile_history import day_type as value_day_type, value_migration
from .regimes import is_expiry_day, regime_id_for
from .repository import TradeRepository
from .universe import desk_underlying

FORCED_EXIT_MINUTE = 15 * 60 + 20
MIN_BRACKETS_BEFORE_ENTRY = 2       # let the initial balance form
MIN_PREMIUM = 5.0
# A reclaim only counts if the probe below value was recent; and one signal
# per symbol per cooldown keeps a standing condition from re-firing.
PROBE_WINDOW_SECONDS = 900
SIGNAL_COOLDOWN_SECONDS = 600
# Per-position notional ceiling. The desk trades OPTIONS only: a bullish read
# buys the ATM call, a bearish read the ATM put, so direction never requires a
# short and the paper book never goes negative.
DEFAULT_MAX_NOTIONAL_PER_TRADE = 100_000.0
MAX_ORDER_MARK_AGE_SECONDS = 10.0
# A closed position stays on the Positions tab until this hour IST the morning
# after its exit, so the evening review and the pre-open one see the same rows.
CLOSED_VISIBLE_UNTIL_HOUR = CLOSED_VISIBLE_UNTIL.hour
# Per-position MFE/MAE. Held in entry_state, not on the Position dataclass:
# Position is shared with the MACD lane and serialised positionally there.
EXCURSION_KEYS = ("max_price", "min_price", "max_return_pct", "min_return_pct", "max_at", "min_at")
# Trading days a year, for the VIX-implied one-day range (spot × VIX% / √252).
TRADING_DAYS = 252
# The stored prior-day/week/month reference is a sqlite read, and MPEngine
# .snapshot() runs on the FastAPI event loop every three seconds. Sixty seconds
# is far finer than the thing it describes: the reference is written by the
# night job and cannot change during a session.
CONTEXT_TTL_SECONDS = 60.0
# OFI keeps two 4000-deep deques and a per-minute dict per symbol -- about 1 MB
# at saturation, against 208 KB for an OrderFlowState. With MP_SYMBOLS_CSV
# unset the desk's universe is the whole ~670-name feed including the analysis
# -only option ladder, so a state per subscribed symbol is ~600 MB of RSS for
# books nobody is reading. States are kept for the contracts actually being
# watched, and the dict is pruned back to them when it drifts past this.
OFI_MAX_STATES = 48
# India VIX symbol for the session badge's implied range. engine.py builds
# MPEngine and is owned by another branch, so the setting arrives by env
# (Settings' own MACD_ prefix) with config's default behind it; the attribute
# stays writable for a caller that has the Settings object.
DEFAULT_VIX_SYMBOL = os.environ.get("MACD_VIX_SYMBOL", "NSE:INDIAVIX-INDEX")


def session_view(profile: Profile, *, flow: FlowState | None = None,
                 context: dict | None = None, vix: float | None = None,
                 reference_price: float | None = None, day: str,
                 expiring: bool = False, snapshot: dict | None = None) -> dict:
    """The session badge strip: everything Part 5 asks the reader to know
    about the day before reading a single cluster.

    Two day-type taxonomies exist in this codebase and they disagree by
    construction -- the live one reads range extension past the initial
    balance, the stored one (base rates, setups, the Auction page) reads how
    much of the range the value area covers. Both are published under their
    own names rather than merged: a reader comparing today with a base-rate
    table needs the stored taxonomy, one watching the IB break needs the live
    one, and a single blended word would answer neither.

    ``context`` is the stored prior-day/week/month reference (auction_views
    .context) for the symbol's underlying; ``vix`` the India VIX last print;
    ``reference_price`` the price the VIX range is taken around -- the
    underlying's, never an option premium's.

    ``snapshot`` is ``profile.snapshot()`` when the caller already holds one.
    Every field read below is ladder-independent, so the caller's full snapshot
    answers exactly as the one-level one did -- and a profile snapshot is not
    free enough to take twice per poll on the loop that ingests ticks.
    """
    snap = snapshot if snapshot is not None else profile.snapshot(max_levels=1)
    partial_capture = snap.get("partial_capture", profile.partial_capture)
    prior = (context or {}).get("prior_day") or {}
    prior_levels = {key: prior.get(key) for key in ("poc", "vah", "val", "high", "low", "close")}
    relationship = (None if partial_capture else value_migration(
        (snap["vah"], snap["val"]), (prior.get("vah"), prior.get("val"))))
    open_price = snap["open"]
    open_location = None
    if not partial_capture and open_price is not None and prior.get("high") is not None and prior.get("low") is not None:
        if open_price > prior["high"] or open_price < prior["low"]:
            open_location = "outside_range"
        elif prior.get("vah") is not None and prior.get("val") is not None:
            open_location = ("above_value" if open_price > prior["vah"]
                             else "below_value" if open_price < prior["val"] else "inside_value")
        else:
            open_location = "inside_range"
    if flow is not None and flow.trades:
        classified_share = ((flow.total_volume - flow.unclassified_volume) / flow.total_volume
                            if flow.total_volume else None)
        quote_share = flow.methods.get("quote", 0) / flow.trades
        sided = flow.buy_volume + flow.sell_volume
        quality = {
            "grade": confidence_grade(classified_share, quote_share)[0],
            "prints": flow.trades,
            "quote_share": round(quote_share, 3),
            "depth_tick_share": round(flow.quotes_seen / flow.trades, 3),
            "unclassified_share": round(flow.unclassified / flow.trades, 3),
            # Over the SIDED volume (buy + sell), not the whole tape: the
            # "unknown" verdict carries confidence 0.0, so a total-volume mean
            # was coverage in disguise -- and unclassified_share, two keys up,
            # is the measurement that actually answers coverage.
            "mean_confidence": (round(flow.conf_volume / sided, 3) if sided else None),
        }
    else:
        quality = {"grade": None, "prints": 0, "quote_share": None, "depth_tick_share": None,
                   "unclassified_share": None, "mean_confidence": None}
    underlying = desk_underlying(profile.symbol) or "NIFTY"
    vix_range = (round(reference_price * vix / 100.0 / TRADING_DAYS ** 0.5, 2)
                 if reference_price and vix else None)
    return {
        "symbol": profile.symbol,
        "day": day,
        "open_type": snap["open_type"],
        "open_observed": snap["open_observed"],
        "partial_capture": partial_capture,
        "open_location": open_location,
        "day_type_ib": snap["day_type"],
        "day_type_va": ("unobserved" if partial_capture else value_day_type(
            snap["high"], snap["low"], snap["vah"], snap["val"])),
        "day_type_bracket": letter_for(max(profile.brackets_seen)) if profile.brackets_seen else None,
        "day_type_by_bracket": snap["day_type_by_bracket"],
        "ib_complete": snap["ib_complete"],
        "ib_high": snap["ib_high"], "ib_low": snap["ib_low"],
        "extension": snap["extension"],
        "value_relationship": relationship,
        "prior_day": prior_levels,
        "prior_day_date": prior.get("day"),
        "vix": vix,
        "vix_implied_range": vix_range,
        "vix_reference_price": reference_price if vix_range is not None else None,
        "expiry_day": is_expiry_day(day, underlying),
        "expiring": expiring,
        "regime_id": regime_id_for(day),
        "poor_high": snap["poor_high"], "poor_low": snap["poor_low"],
        "tail_high": snap["tail_high"], "tail_low": snap["tail_low"],
        "quality": quality,
    }


@dataclass
class MPSettings:
    """Independent risk/behaviour settings for this desk."""
    enabled: bool = False
    auto_trade: bool = False
    initial_capital: float = 1_000_000.0
    max_positions: int = 4
    max_trades_per_day: int = 12
    hard_stop_pct: float = 0.25
    trail_activation_pct: float = 0.20
    trail_pct: float = 0.20
    min_imbalance: float = 0.25
    slippage_bps: float = 25.0
    brokerage_per_leg: float = 20.0
    allow_overnight_carry: bool = False
    max_notional_per_trade: float = DEFAULT_MAX_NOTIONAL_PER_TRADE

    def to_dict(self) -> dict:
        return {key: getattr(self, key) for key in self.__annotations__}


class MPEngine:
    def __init__(self, database_path: str, lot_sizes: dict[str, int] | None = None,
                 settings: MPSettings | None = None, events=None,
                 history_database_path: str | None = None):
        self.settings = settings or MPSettings()
        self.events = events
        self.repository = TradeRepository(database_path)
        self.portfolio = Portfolio(self.settings.initial_capital)
        self.profiles = ProfileBook()
        self.flow = OrderFlowTracker()
        # Book-based order-flow imbalance (level 1), fed from the same ticks.
        # Existed as a module for weeks and was instantiated nowhere live; the
        # Auction page could only draw it for sessions five days cold.
        self.ofi = OFITracker()
        # Per-bar clusters for whichever symbols are being looked at.
        self.footprints = FootprintBook()
        self.lot_sizes: dict[str, int] = dict(lot_sizes or {})
        # Underlying -> its ATM CE/PE, and the instruments whose auction is
        # actually read. Empty scope means "every symbol", the old behaviour.
        self.option_map: dict[str, dict[str, str]] = {}
        self.underlying_of: dict[str, str] = {}
        self.expiring_symbols: set[str] = set()
        self.directional_scope: list[str] = []
        self._directional_set: set[str] = set()
        # Opening range, session VWAP and the rolling channel the textbook
        # setups need; the profile carries structure but not these.
        self.tapes = TapeBook()
        # Everything the feed is subscribed to, distinct from what has actually
        # traded today. Pre-open only a handful of instruments carry a current
        # timestamp, so "tracked" alone reads like the universe went missing.
        self.universe: list[str] = []
        self._universe_set: set[str] = set()
        self.orders: dict[str, Order] = {}
        self.last_prices: dict[str, float] = {}
        self.last_price_at: dict[str, datetime] = {}
        self.signals: list[Signal] = []
        self.entry_state: dict[str, dict] = {}     # symbol -> risk state
        self._setup_state: dict[str, dict] = {}    # symbol -> edge-trigger marks
        self.live_setups: dict[str, tuple] = {}    # symbol -> (setup, reason) as of last tick
        self.session_day: str | None = None
        self.trades_today = 0
        self.prints_seen = 0
        self.stale_ticks = 0
        self.off_session_ticks = 0
        self.ticks_seen = 0
        self.last_tick_at: datetime | None = None
        self.last_print_at: datetime | None = None
        self.backfilled_rows = 0
        self.restored_symbols = 0
        self._last_state_save = 0.0
        self.state_rows_saved = 0
        self.state_saved_at: str | None = None
        self.state_save_error: str | None = None
        self._state_save_task: asyncio.Task | None = None
        self.errors_total = 0
        self.last_error: str | None = None
        self.last_error_at: str | None = None
        self.history_database_path = history_database_path
        # underlying -> (monotonic stamp, auction_views.context payload).
        self._context_cache: dict[str, tuple[float, dict | None]] = {}
        self.vix_symbol = DEFAULT_VIX_SYMBOL
        self.last_signal_reason: dict[str, str] = {}
        self.last_order_rejection: str | None = None
        self._lock = asyncio.Lock()
        # Positions closed since 08:00 IST this morning, newest first. Durable
        # in mp_closed_positions so neither a restart nor the date-keyed
        # session reset can drop a row before its retention clock runs out.
        self.closed_positions: list[dict] = []
        self._restore()

    # -- book restore --------------------------------------------------------

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
        for position in self.portfolio.positions.values():
            position.peak_price = max(position.peak_price, position.average_price)
            position.hard_stop = round(position.average_price * (1 - self.settings.hard_stop_pct), 4)
        # Read here, not in restore_state: that only runs on the first tick of
        # the day, and the page must show the rows from process start.
        # UTC, because that is how save_mp_closed_position normalises the
        # column and the repository compares it as a STRING. Passing an IST
        # stamp made "2026-09-11T02:30:00+00:00" > "2026-09-11T07:36:31+05:30"
        # compare False on the same calendar date, so a restart silently lost
        # every closed row from midnight IST onward -- the desk showed an
        # empty book all morning while the rows sat intact in the database.
        self.closed_positions = self.repository.mp_closed_positions(
            datetime.now(UTC).isoformat(timespec="seconds"))

    def set_universe(self, symbols: list[str]) -> None:
        """Scope the desk. This is a FILTER, not just a label.

        ``universe`` was previously only reported in the snapshot while on_tick
        accepted every symbol the engine forwarded, so the desk tracked the
        whole 667-name feed no matter what it was told to watch.
        """
        self.universe = list(symbols)
        self._universe_set = set(self.universe)

    def set_lot_sizes(self, lot_sizes: dict[str, int]) -> None:
        self.lot_sizes = {symbol: int(size) for symbol, size in lot_sizes.items() if int(size) > 0}

    def set_option_map(self, mapping: dict[str, dict[str, str]]) -> None:
        """underlying symbol -> {"CE": option symbol, "PE": option symbol}.

        The auction is read on the UNDERLYING -- a market profile of an option
        premium is not a market profile of anything -- and the resulting view is
        expressed in that underlying's ATM contract.
        """
        self.option_map = {
            symbol: {side: contract for side, contract in sides.items() if contract}
            for symbol, sides in mapping.items()
        }
        # Reverse lookup so an OPEN option position can be exited on its
        # underlying's auction rather than on a profile of its own premium.
        self.underlying_of = {
            contract: symbol
            for symbol, sides in self.option_map.items() for contract in sides.values()
        }

    def set_position_underlyings(self, mapping: dict[str, str]) -> None:
        """Retain the auction source for carried contracts no longer at-the-money."""
        self.underlying_of.update(mapping)
        for symbol, underlying in mapping.items():
            state = self.entry_state.setdefault(symbol, {})
            state.setdefault("underlying", underlying)
            state.setdefault("option_type", "PE" if symbol.endswith("PE") else "CE")

    def set_expiring_symbols(self, symbols: set[str]) -> None:
        self.expiring_symbols = set(symbols)

    def set_directional_scope(self, symbols: list[str]) -> None:
        """The instruments whose auction the desk actually reads."""
        self.directional_scope = list(dict.fromkeys(symbols))
        self._directional_set = set(self.directional_scope)

    # -- tick ingestion ------------------------------------------------------

    # Profiles can be rebuilt from durable candles; ORDER FLOW cannot — candles
    # carry no aggressor side, so a restart previously zeroed cumulative delta,
    # footprint and absorption irrecoverably. These snapshots close that gap.
    STATE_SAVE_SECONDS = 60
    STATE_FOOTPRINT_LEVELS = 300      # keep the meaningful volume nodes, bound the size

    def _maybe_save_state(self) -> None:
        """Throttled snapshot from the tick path.

        ``_last_state_save`` and ``STATE_SAVE_SECONDS`` were both defined, but
        save_state() had no caller anywhere in the engine — the table was still
        empty after 571k prints, so every restart silently discarded cumulative
        delta, footprint and absorption for the whole session. Mirrors the
        equity-point throttle in TradingEngine._process_tick.
        """
        now = time.monotonic()
        if now - self._last_state_save < self.STATE_SAVE_SECONDS:
            return
        self._last_state_save = now
        if self._state_save_task and not self._state_save_task.done():
            return
        day = self.session_day
        payload = self._state_payload()
        self._state_save_task = asyncio.create_task(self._save_state_async(day, payload))

    def _state_payload(self) -> dict:
        """Copy mutable live state on the event loop for threaded persistence."""
        if not self.session_day:
            return {}
        profiles = {}
        for symbol, profile in self.profiles.profiles.items():
            if not profile.tpo:
                continue
            open_window = profile._open_window
            open_summary = ([open_window[0], min(open_window), max(open_window), open_window[-1]]
                            if open_window else [])
            profiles[symbol] = {
                "tpo": {str(price): sorted(rows) for price, rows in profile.tpo.items()},
                "volume": {str(price): value for price, value in profile.volume.items()},
                "open": profile.open_price, "last": profile.last_price,
                "high": profile.high, "low": profile.low,
                "ib_high": profile.ib_high, "ib_low": profile.ib_low,
                "first_bracket": profile.first_bracket, "last_minute": profile.last_minute,
                "open_window": open_summary,
                "day_types": {str(bracket): kind for bracket, kind in profile.day_types.items()},
            }
        flows = {}
        for symbol, state in self.flow.states.items():
            if not state.trades:
                continue
            levels = sorted(state.volume_at_price.items(),
                            key=lambda row: -(row[1][0] + row[1][1]))[:self.STATE_FOOTPRINT_LEVELS]
            flows[symbol] = {
                "cumulative_delta": state.cumulative_delta,
                "buy_volume": state.buy_volume, "sell_volume": state.sell_volume,
                # Without these a restart resets coverage to "100% classified"
                # and the desk silently over-states its own confidence for the
                # rest of the session.
                "total_volume": state.total_volume,
                "unclassified_volume": state.unclassified_volume,
                "last_trade_price": state.last_trade_price, "last_side": state.last_side,
                "trades": state.trades, "last_price": state.last_price,
                "last_cum_volume": state.last_cum_volume,
                "methods": dict(state.methods), "quotes_seen": state.quotes_seen,
                "unclassified": state.unclassified,
                # The weighted leg and the prior-book memory: without them a
                # restart drops the confidence-weighted CVD to zero and
                # classifies the first print after restore against no book.
                "weighted_delta": state.weighted_delta, "conf_volume": state.conf_volume,
                "prior_bid": state.prior_bid, "prior_ask": state.prior_ask,
                "prior_tbq": state.prior_tbq, "prior_tsq": state.prior_tsq,
                # Ten minutes of closed speed windows, the same bounded-tail
                # treatment cvd_curve and recent get: enough to clear the
                # minimum pool immediately after a restart, and small enough
                # that 600 traded contracts do not double the checkpoint.
                "speed_samples": list(state.speed_samples)[-60:],
                "volume_at_price": {str(p): list(v) for p, v in levels},
                "cvd_curve": list(state.cvd_curve)[-240:],
                "recent": [[p.timestamp, p.price, p.size, p.side, p.method, p.confidence]
                           for p in list(state.recent)[-60:]],
            }
        tapes = {}
        for symbol, tape in self.tapes.tapes.items():
            if self._directional_set and symbol not in self._directional_set:
                continue
            tapes[symbol] = {
                "day": tape.day, "open_price": tape.open_price,
                "or_high": tape.or_high, "or_low": tape.or_low,
                "vwap_value": tape.vwap_value, "vwap_volume": tape.vwap_volume,
                "closes": list(tape.closes), "highs": list(tape.highs),
                "lows": list(tape.lows), "fast_ema": tape.fast_ema,
                "slow_ema": tape.slow_ema, "fast_ema_base": tape.fast_ema_base,
                "slow_ema_base": tape.slow_ema_base,
                "last_minute": tape.last_minute, "minutes_seen": tape.minutes_seen,
            }
        return {
            "prints_seen": self.prints_seen,
            "stale_ticks": self.stale_ticks,
            "off_session_ticks": self.off_session_ticks,
            "ticks_seen": self.ticks_seen,
            "trades_today": self.trades_today,
            "entry_state": {key: dict(value) for key, value in self.entry_state.items()},
            "setup_state": {key: dict(value) for key, value in self._setup_state.items()},
            "position_risk": {
                symbol: {
                    "peak_price": position.peak_price,
                    "hard_stop": position.hard_stop,
                    "trailing_stop": position.trailing_stop,
                    "entry_anchor": position.entry_anchor,
                }
                for symbol, position in self.portfolio.positions.items()
            },
            "profiles": profiles,
            "flows": flows,
            "tapes": tapes,
        }

    async def _save_state_async(self, day: str, payload: dict) -> None:
        try:
            self.state_rows_saved = await asyncio.to_thread(
                self.repository.save_session_state, day, payload,
            )
            self.state_saved_at = datetime.now(IST).isoformat(timespec="seconds")
            self.state_save_error = None
        except Exception as exc:  # noqa: BLE001 — persistence must not kill the feed
            self.state_save_error = str(exc)

    async def flush_state(self) -> None:
        task = self._state_save_task
        if task:
            await task

    def save_state(self) -> int:
        """Persist today's profiles and flow so a restart resumes, not restarts."""
        if not self.session_day:
            return 0
        return self.repository.save_session_state(self.session_day, self._state_payload())

    def restore_state(self, day: str) -> int:
        """Reload today's profiles and flow after a restart. Returns symbols restored."""
        from .orderflow import Print

        payload = self.repository.load_session_state(day)
        if not payload:
            return 0
        for symbol, row in (payload.get("profiles") or {}).items():
            profile = Profile(symbol, day, self.profiles.tick_size)
            profile.tpo = {float(p): set(v) for p, v in (row.get("tpo") or {}).items()}
            profile.volume = {float(p): v for p, v in (row.get("volume") or {}).items()}
            profile.brackets_seen = {b for rows in profile.tpo.values() for b in rows}
            profile.open_price, profile.last_price = row.get("open"), row.get("last")
            profile.high, profile.low = row.get("high"), row.get("low")
            profile.ib_high, profile.ib_low = row.get("ib_high"), row.get("ib_low")
            profile.first_bracket, profile.last_minute = row.get("first_bracket"), row.get("last_minute")
            profile._open_window = list(row.get("open_window") or [])
            profile.day_types = {int(b): str(kind) for b, kind in (row.get("day_types") or {}).items()}
            profile._structure_version = sum(len(rows) for rows in profile.tpo.values())
            self.profiles.profiles[symbol] = profile
        for symbol, row in (payload.get("flows") or {}).items():
            state = FlowState(symbol)
            state.cumulative_delta = row.get("cumulative_delta", 0.0)
            state.buy_volume = row.get("buy_volume", 0.0)
            state.sell_volume = row.get("sell_volume", 0.0)
            # A payload written before these fields existed carries NO coverage
            # measurement. Synthesising total = buy+sell would report "100%
            # classified" for a session where the unclassified size was in fact
            # discarded and is unrecoverable — asserting a number nobody
            # measured, which is the exact failure this work exists to remove.
            # Leave it 0; snapshot() then publishes classified_share = None and
            # the desk shows no confidence figure rather than a false one.
            state.total_volume = row.get("total_volume", 0.0)
            state.unclassified_volume = row.get("unclassified_volume", 0.0)
            state.last_trade_price = row.get("last_trade_price")
            state.last_side = int(row.get("last_side", 0) or 0)
            state.trades = int(row.get("trades", 0))
            state.last_price = row.get("last_price")
            state.last_cum_volume = row.get("last_cum_volume")
            state.methods = dict(row.get("methods") or {"quote": 0, "mid": 0, "tick": 0})
            state.quotes_seen = int(row.get("quotes_seen", 0))
            state.unclassified = int(row.get("unclassified", 0))
            state.weighted_delta = float(row.get("weighted_delta", 0.0) or 0.0)
            state.conf_volume = float(row.get("conf_volume", 0.0) or 0.0)
            state.prior_bid, state.prior_ask = row.get("prior_bid"), row.get("prior_ask")
            state.prior_tbq, state.prior_tsq = row.get("prior_tbq"), row.get("prior_tsq")
            state.speed_samples.extend(tuple(sample) for sample in (row.get("speed_samples") or []))
            state.volume_at_price = {float(p): list(v) for p, v in (row.get("volume_at_price") or {}).items()}
            state.cvd_curve.extend(tuple(v) for v in (row.get("cvd_curve") or []))
            state.recent.extend(Print(*v) for v in (row.get("recent") or []))
            self.flow.states[symbol] = state
        for symbol, row in (payload.get("tapes") or {}).items():
            tape = SessionTape(day=str(row.get("day") or day))
            tape.open_price = float(row.get("open_price") or 0.0)
            tape.or_high = row.get("or_high")
            tape.or_low = row.get("or_low")
            tape.vwap_value = float(row.get("vwap_value") or 0.0)
            tape.vwap_volume = float(row.get("vwap_volume") or 0.0)
            closes = [float(value) for value in (row.get("closes") or [])]
            tape.closes.extend(closes)
            tape.highs.extend(float(value) for value in (row.get("highs") or closes))
            tape.lows.extend(float(value) for value in (row.get("lows") or closes))
            tape.fast_ema = row.get("fast_ema")
            tape.slow_ema = row.get("slow_ema")
            tape.fast_ema_base = row.get("fast_ema_base")
            tape.slow_ema_base = row.get("slow_ema_base")
            tape.last_minute = int(row.get("last_minute", -1))
            tape.minutes_seen = int(row.get("minutes_seen", len(closes)))
            self.tapes.tapes[symbol] = tape
        self.prints_seen = int(payload.get("prints_seen", 0))
        self.stale_ticks = int(payload.get("stale_ticks", 0))
        self.off_session_ticks = int(payload.get("off_session_ticks", 0))
        self.ticks_seen = int(payload.get("ticks_seen", 0))
        self.trades_today = int(payload.get("trades_today", 0))
        self.entry_state = dict(payload.get("entry_state") or {})
        self._setup_state = dict(payload.get("setup_state") or {})
        self.state_saved_at = payload.get("_saved_at")
        self.state_rows_saved = int(payload.get("_saved_bytes", 0))
        for symbol, risk in (payload.get("position_risk") or {}).items():
            position = self.portfolio.positions.get(symbol)
            if position is None:
                continue
            position.peak_price = max(float(risk.get("peak_price", 0.0)), position.average_price)
            position.hard_stop = float(risk.get("hard_stop", position.hard_stop))
            trailing = risk.get("trailing_stop")
            position.trailing_stop = float(trailing) if trailing is not None else None
            position.entry_anchor = float(risk.get("entry_anchor", position.average_price))
        self.restored_symbols = len(payload.get("profiles") or {})
        return self.restored_symbols

    def backfill_session(self, *, include_profiles: bool = True,
                         tape_symbols: set[str] | None = None,
                         now: datetime | None = None) -> int:
        """Rebuild today's TPO/volume profile from the durable 1-minute candles.

        A restart (or an earlier fault) otherwise loses the morning
        irrecoverably: the open window and initial balance cannot be
        re-observed once the clock has passed them. Order flow is NOT
        reconstructed — candles carry no bid/ask, so aggressor side is
        unknowable after the fact and is left to resume from live ticks.
        """
        if not self.history_database_path:
            return 0
        import sqlite3
        from datetime import time as dtime

        # `now` is injectable so this is testable without the wall clock
        # deciding the outcome: with the real clock the window is empty before
        # 09:15 and the whole function silently returns nothing.
        moment = (now or datetime.now(IST)).astimezone(IST)
        start = datetime.combine(moment.date(), dtime(9, 15), IST)
        cutoff = moment.replace(second=0, microsecond=0)
        wanted_tapes = set(tape_symbols or ())
        rows = 0
        try:
            connection = sqlite3.connect(self.history_database_path, timeout=15)
            try:
                where = "timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?"
                params: list = [int(start.timestamp()), int(cutoff.timestamp())]
                if not include_profiles:
                    if not wanted_tapes:
                        return 0
                    placeholders = ",".join("?" for _ in wanted_tapes)
                    where += f" AND symbol IN ({placeholders})"
                    params.extend(sorted(wanted_tapes))
                cursor = connection.execute(
                    f"""SELECT symbol, timestamp, open, high, low, close, volume
                         FROM historical_candles WHERE {where} ORDER BY timestamp""",
                    params,
                )
                for symbol, timestamp, open_price, high, low, close, volume in cursor:
                    moment = datetime.fromtimestamp(int(timestamp), UTC)
                    if include_profiles:
                        self.profiles.on_print(str(symbol), float(close), float(volume or 0), moment)
                    if symbol in wanted_tapes:
                        local = moment.astimezone(IST)
                        self.tapes.on_bar(
                            str(symbol), local.date().isoformat(), float(open_price),
                            float(high), float(low), float(close), float(volume or 0),
                            local.hour * 60 + local.minute,
                        )
                    rows += 1
            finally:
                connection.close()
        except sqlite3.Error:
            return 0
        self.backfilled_rows = rows
        return rows

    async def on_tick(self, tick) -> None:
        if tick.ltp <= 0:
            return
        if self._universe_set and tick.symbol not in self._universe_set:
            return
        # Session identity comes from the WALL CLOCK, never from the tick.
        # Illiquid contracts re-broadcast a previous session's last trade, so
        # their exchange timestamp is yesterday; keying the session off that
        # made every such tick roll the session and wipe every profile, then
        # the next live tick rolled it back — the desk was resetting hundreds
        # of times a second.
        now_ist = datetime.now(IST)
        day = now_ist.date().isoformat()
        if self.session_day != day:
            carried_entry_state = {
                symbol: dict(self.entry_state.get(symbol, {}))
                for symbol in self.portfolio.positions
            }
            self.session_day = day
            self.profiles.profiles.clear()
            self.flow.reset()
            self.ofi.reset()
            self.footprints.reset()
            self.entry_state.clear()
            self._setup_state.clear()
            self.live_setups.clear()
            self.trades_today = 0
            self.prints_seen = 0
            self.stale_ticks = 0
            self.off_session_ticks = 0
            self.ticks_seen = 0
            self.last_tick_at = None
            self.last_print_at = None
            # Closed-today rows live by their own clock (08:00 IST the next
            # trading morning), not by the session identity, so they are pruned here
            # rather than cleared with the auction state.
            self._prune_closed(now_ist)
            # Restore first: it carries order flow, which candles cannot.  The
            # directional tape was introduced later than the checkpoint
            # format, so rebuild any missing tape even when profile/flow state
            # restored successfully.
            restored = self.restore_state(day)
            # A new session resets its auction, not the identity and risk
            # context of positions selectively carried from the prior day.
            for symbol, state in carried_entry_state.items():
                self.entry_state.setdefault(symbol, state)
            missing_tapes = self._directional_set - self.tapes.tapes.keys()
            if restored == 0 or missing_tapes:
                self.backfill_session(
                    include_profiles=(restored == 0), tape_symbols=set(missing_tapes),
                )
        moment = tick.timestamp.astimezone(IST)
        if moment.date().isoformat() != day:
            # Stale re-broadcast of an earlier session's last trade: it is not
            # a print in today's auction and must not enter today's profile.
            self.stale_ticks += 1
            return

        in_session = (
            is_trading_day(moment.date())
            and SESSION_OPEN <= moment.timetz().replace(tzinfo=None) < SESSION_CLOSE
        )
        if not in_session:
            self.off_session_ticks += 1
            if self.portfolio.mark(tick.symbol, tick.ltp):
                self._publish("mp_portfolio", self._portfolio_view())
            return

        self.ticks_seen += 1
        self.last_tick_at = datetime.now(UTC)
        self.last_prices[tick.symbol] = tick.ltp
        self.last_price_at[tick.symbol] = self.last_tick_at
        if not self.settings.enabled and tick.symbol not in self.portfolio.positions:
            return

        print_row = self.flow.on_tick(tick)
        marked = self.portfolio.mark(tick.symbol, tick.ltp)
        self._track_excursion(tick.symbol, tick.ltp, self.last_tick_at)
        if marked:
            self._publish("mp_portfolio", self._portfolio_view())
        # TPO is TIME at price, not volume at price. Indices stream no volume
        # at all (the feed's index payload carries 8 fields, none of them
        # volume or depth), so gating the profile on a flow print silently
        # denied NIFTY/BANKNIFTY/SENSEX a profile — the classic market-profile
        # instrument. Build structure from every tick; volume is the overlay.
        self.profiles.on_print(tick.symbol, tick.ltp, print_row.size if print_row else 0, tick.timestamp)
        if not self._directional_set or tick.symbol in self._directional_set:
            self.tapes.on_price(tick.symbol, moment.date().isoformat(), tick.ltp,
                                print_row.size if print_row else 0,
                                moment.hour * 60 + moment.minute)
        self.footprints.on_quote(tick.symbol, tick)
        # Every in-session update, prints or not: a quote that never trades
        # is still a change in the book, which is exactly what OFI measures --
        # but only for contracts someone is actually reading (see _ofi_tracked).
        if self._ofi_tracked(tick.symbol):
            self.ofi.on_tick(tick)
            if len(self.ofi.states) > OFI_MAX_STATES:
                self.ofi.prune(set(self.footprints.bars) | self._directional_set)
        minute = moment.hour * 60 + moment.minute
        await self._manage_position(tick.symbol, tick.ltp, minute)
        if not self.settings.enabled:
            self.last_error = None
            self.last_error_at = None
            return
        if print_row is None:
            self.last_error = None
            self.last_error_at = None
            return
        self.prints_seen += 1
        self.last_print_at = datetime.now(UTC)
        # Method and confidence travel with the print. Without the method every
        # live bar published methods=None and the zero-tick suppression gate
        # could never fire on a bar the desk had actually captured.
        self.footprints.on_print(tick.symbol, print_row.timestamp, print_row.price,
                                 print_row.size, print_row.side,
                                 print_row.method, print_row.confidence)

        self._maybe_save_state()
        # The auction state machine advances on TICKS, always. auto_trade gates
        # only order placement. Previously the machine ran solely inside the
        # auto-trade branch, so while observing no transition was ever latched
        # and the read paths re-reported every standing condition as a fresh
        # break. Logging signals either way is what makes observing mode useful
        # for judging the setups before risking the book.
        bias, reason = self.evaluate(tick.symbol, tick.ltp, minute, commit=True)
        self.live_setups[tick.symbol] = ((bias.setup if bias else None), reason)
        self.last_signal_reason[tick.symbol] = reason
        if bias:
            # The signal names the OPTION that expresses the view, so the book
            # and the signal log refer to the same instrument.
            contract = self.contract_for(tick.symbol, bias.option_type) or tick.symbol
            signal = Signal(contract, "BUY", f"MP_{bias.setup.upper()}_{bias.option_type}",
                            self.last_prices.get(contract, tick.ltp), 0.0, 0.0, 0.0)
            self.signals.insert(0, signal)
            self.signals = self.signals[:200]
            self.repository.save_signal(signal)
            self._publish("mp_signal", {**_as_dict(signal), "setup": bias.setup,
                                        "direction": bias.direction, "underlying": tick.symbol,
                                        "reason": reason})
            if self.settings.auto_trade:
                await self._enter(tick.symbol, bias, signal)
        self.last_error = None
        self.last_error_at = None

    def record_error(self, exc: Exception) -> None:
        """Expose isolated desk faults instead of silently losing the module."""
        self.errors_total += 1
        self.last_error = f"{type(exc).__name__}: {exc}"
        self.last_error_at = datetime.now(UTC).isoformat(timespec="seconds")

    # -- strategy ------------------------------------------------------------

    def evaluate(self, symbol: str, price: float, minute: int, *, commit: bool = False):
        """(Bias | None, human reason) for the current auction state.

        ``commit`` decides whether edge-trigger marks persist. The read paths
        (snapshot, leaderboard) MUST pass commit=False: the UI polls them every
        few seconds for hundreds of symbols, and a mutating read consumed the
        very edge triggers and cooldowns the trading path depends on — a
        browser refresh could silently cancel a real entry.
        """
        if self.directional_scope and symbol not in self.directional_scope:
            return None, "not in the desk's directional scope"
        profile = self.profiles.get(symbol)
        state = self.flow.states.get(symbol)
        tape = self.tapes.get(symbol)
        if profile is None or tape is None:
            return None, "no profile"
        if len(profile.brackets_seen) < MIN_BRACKETS_BEFORE_ENTRY:
            return None, "initial balance still forming"
        if minute >= FORCED_EXIT_MINUTE - 15:
            return None, "too late in session"
        vah, val = profile.value_area()
        if vah is None or val is None or profile.ib_high is None:
            return None, "value area undefined"

        marks = (self._setup_state.setdefault(symbol, {}) if commit
                 else dict(self._setup_state.get(symbol, {})))
        now = time.time()
        if now - marks.get("last_signal_at", -1e9) < SIGNAL_COOLDOWN_SECONDS:
            return None, "cooling down after the last signal"

        # Probe bookkeeping lives outside evaluate() so a probe that started
        # while cooling down is still remembered when the cooldown lifts.
        probe = marks.setdefault("probe", {})
        if price < val:
            probe["below"] = now
            probe["below_extreme"] = min(probe.get("below_extreme", price), price)
        if price > vah:
            probe["above"] = now
            probe["above_extreme"] = max(probe.get("above_extreme", price), price)
        for side in ("below", "above"):
            started = probe.get(side)
            if started is not None and now - started > PROBE_WINDOW_SECONDS:
                probe.pop(side, None)
                probe.pop(f"{side}_extreme", None)

        bias = evaluate_directional(
            price=price, marks=marks, tape=tape, profile=profile, flow_state=state,
            absorption=self.flow.absorption(symbol), divergence=self.flow.divergence(symbol),
            min_imbalance=self.settings.min_imbalance, probe_window_open=probe,
        )
        if bias is None:
            return None, "no setup"
        marks["last_signal_at"] = now
        return bias, bias.reason

    def contract_for(self, underlying: str, option_type: str) -> str | None:
        return (self.option_map.get(underlying) or {}).get(option_type)

    def target_quantity(self, symbol: str, price: float) -> int:
        """Largest whole-lot quantity inside the configured notional target."""
        lot = self.lot_sizes.get(symbol, 0)
        if lot <= 0 or price <= 0:
            return 0
        per_lot = price * (1 + self.settings.slippage_bps / 10_000) * lot
        target_lots = int(self.settings.max_notional_per_trade // per_lot)
        cash_for_premium = max(0.0, self.portfolio.cash - self.settings.brokerage_per_leg)
        affordable_lots = int(cash_for_premium // per_lot)
        return lot * min(target_lots, affordable_lots)

    async def _enter(self, underlying: str, bias: Bias, signal: Signal) -> None:
        """Express a view on the underlying by buying its ATM CE or PE.

        Every rejection here is recorded. The previous version returned
        silently when the lot size was unknown, which is exactly what happened
        to every desk signal ever generated: the desk watches futures and
        equities, ``set_lot_sizes`` was only ever fed the option contracts, so
        ``lot`` was 0 and 500 signals produced no orders and no explanation.
        """
        symbol = self.contract_for(underlying, bias.option_type)
        if symbol is None:
            self.last_order_rejection = f"no {bias.option_type} contract mapped for {underlying}"
            return
        if symbol in self.portfolio.positions:
            self.last_order_rejection = f"already holding {symbol}"
            return
        if len(self.portfolio.positions) >= self.settings.max_positions:
            self.last_order_rejection = "max_positions reached"
            return
        if self.trades_today >= self.settings.max_trades_per_day:
            self.last_order_rejection = "max_trades_per_day reached"
            return
        price = self.last_prices.get(symbol)
        if price is None:
            self.last_order_rejection = f"no live price for {symbol}"
            return
        if price < MIN_PREMIUM:
            self.last_order_rejection = f"{symbol} premium {price:.2f} below the {MIN_PREMIUM:.0f} floor"
            return
        lot = self.lot_sizes.get(symbol, 0)
        if lot <= 0:
            self.last_order_rejection = f"no exchange lot size for {symbol}"
            return
        quantity = self.target_quantity(symbol, price)
        per_lot = price * lot * (1 + self.settings.slippage_bps / 10_000)
        if quantity <= 0 and per_lot > self.settings.max_notional_per_trade:
            self.last_order_rejection = (
                f"{symbol} lot notional {per_lot:,.0f} exceeds the "
                f"{self.settings.max_notional_per_trade:,.0f} cap")
            return
        if quantity <= 0:
            self.last_order_rejection = "insufficient paper cash"
            return
        await self.submit(symbol, "BUY", quantity, signal=signal, note=bias.setup)

    def _track_excursion(self, symbol: str, price: float, at: datetime) -> None:
        """Max/min price and return since entry (MFE/MAE) for an open position.

        Fed from the in-session mark only, the same prices peak_price and the
        stops see; pre-open re-broadcasts and the REST startup marks are not
        excursions the trade could have been exited on. Seeds from the book
        when the state carries no excursion yet, which is the window between a
        restart and the first restore_state, and a carried position.
        """
        position = self.portfolio.positions.get(symbol)
        if position is None or position.quantity <= 0 or position.average_price <= 0:
            return
        state = self.entry_state.setdefault(symbol, {})
        stamp = at.isoformat(timespec="seconds")
        if state.get("max_price") is None:
            state["max_price"] = max(position.peak_price, position.average_price)
            state["max_at"] = state.get("entered_at") or stamp
        if state.get("min_price") is None:
            state["min_price"] = position.average_price
            state["min_at"] = state.get("entered_at") or stamp
        if price > state["max_price"]:
            state["max_price"], state["max_at"] = price, stamp
        if price < state["min_price"]:
            state["min_price"], state["min_at"] = price, stamp
        entry = position.average_price
        state["max_return_pct"] = round((state["max_price"] / entry - 1) * 100, 4)
        state["min_return_pct"] = round((state["min_price"] / entry - 1) * 100, 4)

    def _portfolio_view(self) -> dict:
        """Portfolio.snapshot() plus per-position MFE/MAE from entry_state."""
        view = self.portfolio.snapshot()
        for row in view["positions"]:
            state = self.entry_state.get(row["symbol"], {})
            for key in EXCURSION_KEYS:
                row[key] = state.get(key)
        return view

    async def _manage_position(self, symbol: str, price: float, minute: int) -> None:
        position = self.portfolio.positions.get(symbol)
        if not position or position.quantity <= 0:
            return
        if price > position.peak_price:
            position.peak_price = price
            if price >= position.average_price * (1 + self.settings.trail_activation_pct):
                # Never arm the trail below the round-trip breakeven: at
                # activation peak*(1-trail) is 0.96x entry, so a trade that ran
                # +20% and reversed was closed for a 4% loss by the mechanism
                # meant to protect its profit.
                floor = breakeven_exit_price(
                    position.average_price, position.quantity,
                    self.settings.slippage_bps, self.settings.brokerage_per_leg,
                )
                trail = position.peak_price * (1 - self.settings.trail_pct)
                position.trailing_stop = round(max(trail, floor), 4)
        reason = None
        if position.hard_stop and price <= position.hard_stop:
            reason = "MP_HARD_STOP"
        elif position.trailing_stop and price <= position.trailing_stop:
            reason = "MP_TRAIL"
        else:
            if minute >= FORCED_EXIT_MINUTE:
                allowed, detail = self.carry_decision(symbol)
                self._record_carry_decision(symbol, allowed, detail)
                if not allowed:
                    reason = "MP_EOD"
            # Auction logic for the exit too, read on the UNDERLYING. A long
            # put is closed when SELLERS get absorbed at a low, which is the
            # mirror of a long call being closed when buyers are absorbed at a
            # high -- keying both to the option's own premium profile would
            # have asked the wrong question of the wrong instrument.
            if reason is None:
                source = self.underlying_of.get(symbol, symbol)
                state = self.flow.states.get(source)
                profile = self.profiles.get(source)
                held = self.entry_state.get(symbol, {}).get("option_type", "CE")
                if state and profile:
                    absorption = self.flow.absorption(source)
                    exhausted = ("buyers_absorbed" if held == "CE" else "sellers_absorbed")
                    at_extreme = ("above_value" if held == "CE" else "below_value")
                    if profile.position() == at_extreme and absorption.get("side") == exhausted:
                        reason = "MP_BUYERS_ABSORBED" if held == "CE" else "MP_SELLERS_ABSORBED"
        if reason:
            self.entry_state.setdefault(symbol, {})["exit_reason"] = reason
            await self.submit(symbol, "SELL", position.quantity, note=reason)

    # -- execution (paper, this desk's own book) -----------------------------

    async def submit(self, symbol: str, side: str, quantity: int, *,
                     signal: Signal | None = None, note: str = "") -> Order | None:
        lot = self.lot_sizes.get(symbol, 0)
        if quantity <= 0:
            self.last_order_rejection = "quantity must be positive"
            return None
        price = self.last_prices.get(symbol)
        if price is None:
            self.last_order_rejection = "no live price for symbol"
            return None
        async with self._lock:
            self.last_order_rejection = None
            if lot <= 0 or quantity % lot:
                self.last_order_rejection = "quantity must use the current exchange lot size"
                return None
            position = self.portfolio.positions.get(symbol)
            if side == "BUY":
                local = datetime.now(IST)
                if not self.settings.enabled:
                    self.last_order_rejection = "desk is disabled"
                    return None
                if not (is_trading_day(local.date()) and SESSION_OPEN <= local.timetz().replace(tzinfo=None) < SESSION_CLOSE):
                    self.last_order_rejection = "new entries are blocked outside the regular session"
                    return None
                mark_at = self.last_price_at.get(symbol)
                if mark_at is None or (datetime.now(UTC) - mark_at).total_seconds() > MAX_ORDER_MARK_AGE_SECONDS:
                    self.last_order_rejection = "live mark is stale"
                    return None
                estimated_fill = price * (1 + self.settings.slippage_bps / 10_000)
                required_cash = estimated_fill * quantity + self.settings.brokerage_per_leg
                if required_cash > self.portfolio.cash:
                    self.last_order_rejection = "insufficient paper cash"
                    return None
            elif side == "SELL":
                if position is None or position.quantity <= 0 or quantity > position.quantity:
                    self.last_order_rejection = "naked or oversized sells are not allowed"
                    return None
            else:
                self.last_order_rejection = "side must be BUY or SELL"
                return None
            order = Order(
                symbol=symbol, side=side, quantity=int(quantity),
                lots=max(1, int(quantity) // lot), lot_size=lot,
                order_type="MARKET", signal_id=signal.signal_id if signal else None,
            )
            order.broker_order_id = f"MPPAPER-{order.order_id[:12]}"
            self.orders[order.order_id] = order
            self.repository.save_order(order)
            slip = price * self.settings.slippage_bps / 10_000
            fill = price + slip if side == "BUY" else price - slip
            order.fill_price = round(fill, 4)
            order.status = "FILLED"
            trade = Trade(order.order_id, symbol, side, order.quantity, order.fill_price,
                          lots=order.lots, lot_size=lot,
                          fees=self.settings.brokerage_per_leg)
            # apply_trade mutates the pre-trade Position on a partial sell and
            # drops it from the book on a flat one, so the held quantity must
            # be read before it and the record cut before `position` is rebound.
            held_before = position.quantity if position else 0
            self.portfolio.apply_trade(trade)
            if side == "SELL" and position is not None:
                self._record_closed(position, held_before, trade, order, note,
                                    flat=symbol not in self.portfolio.positions)
            position = self.portfolio.positions.get(symbol)
            if side == "BUY" and position:
                position.peak_price = max(position.peak_price, order.fill_price)
                position.hard_stop = round(position.average_price * (1 - self.settings.hard_stop_pct), 4)
                position.entry_anchor = order.fill_price
                self.trades_today += 1
                entered_at = datetime.now(UTC).isoformat()
                self.entry_state[symbol] = {
                    "setup": note, "entered_at": entered_at,
                    "option_type": "PE" if symbol.endswith("PE") else "CE",
                    "underlying": self.underlying_of.get(symbol),
                    # Kept so the closed record can charge the same pro-rata
                    # entry fee statistics() does, and the two surfaces agree.
                    # Read this symbol's PREVIOUS entry state, which the very
                    # next statement overwrites, so adding to a position keeps
                    # the fees already paid for it. `state` here was a NameError
                    # that aborted every BUY after apply_trade but before
                    # save_trade: on 4 Sep the book took 6 orders and recorded
                    # 0 fills, and the restart rebuilt an empty day from them.
                    "entry_fees": float(
                        self.entry_state.get(symbol, {}).get("entry_fees", 0.0)
                    ) + trade.fees,
                    "max_price": order.fill_price, "min_price": order.fill_price,
                    "max_return_pct": 0.0, "min_return_pct": 0.0,
                    "max_at": entered_at, "min_at": entered_at,
                }
            self.repository.save_order(order)
            self.repository.save_trade(trade)
            snapshot = self._portfolio_view()
            self.repository.save_equity_point(snapshot)
            self._publish("mp_order", _as_dict(order))
            self._publish("mp_trade", _as_dict(trade))
            self._publish("mp_portfolio", snapshot)
        return order

    def _publish(self, event: str, payload) -> None:
        if self.events is not None:
            self.events.publish(event, payload)

    # -- read models ---------------------------------------------------------

    def snapshot(self, symbol: str | None = None) -> dict:
        tracked = sorted(self.profiles.profiles)
        # "Auto (most active)" must mean most active. Alphabetical order put
        # BSE:SENSEX-INDEX first — an index, which streams no volume, so the
        # order-flow panel opened permanently empty.
        if symbol in self.profiles.profiles:
            focus = symbol
        elif tracked:
            # Default to an instrument whose auction the directional desk
            # actually reads.  Choosing the busiest option profile made the
            # headline setup permanently say "not in scope" even while the
            # engine was trading its underlying.
            candidates = [name for name in tracked if name in self._directional_set] or tracked
            focus = max(candidates, key=lambda name: (
                self.flow.states[name].trades if name in self.flow.states else 0,
                len(self.profiles.profiles[name].tpo),
            ))
        else:
            focus = None
        profile_snapshot = self.profiles.get(focus).snapshot() if focus else None
        return {
            "enabled": self.settings.enabled,
            "settings": self.settings.to_dict(),
            "session_day": self.session_day,
            "prints_seen": self.prints_seen,
            "stale_ticks": self.stale_ticks,
            "off_session_ticks": self.off_session_ticks,
            "backfilled_rows": self.backfilled_rows,
            "restored_symbols": self.restored_symbols,
            # Surfaced so a persistence layer that is silently doing nothing
            # cannot look identical to one that is working.
            "state_saved_at": self.state_saved_at,
            "state_rows_saved": self.state_rows_saved,
            "state_save_error": self.state_save_error,
            "classification": self.classification_health(),
            "health": self.health(),
            "trades_today": self.trades_today,
            "tracked_symbols": tracked,
            "subscribed_symbols": self.universe,
            "subscribed_count": len(self.universe),
            "focus": focus,
            # ONE profile snapshot per poll, handed to both readers. It used to
            # be taken here and again inside session_view.
            "profile": profile_snapshot,
            "flow": self.flow.snapshot(focus) if focus else None,
            # Read the setup the TICK path computed — never recompute here, or
            # the display and the trading decision can disagree.
            "setup": (lambda pair: {"setup": pair[0], "reason": pair[1]})(
                self.live_setups.get(focus, (None, "no tick yet"))) if focus else None,
            "session": self.session_view(focus, snapshot=profile_snapshot) if focus else None,
            "portfolio": self._portfolio_view(),
            "entry_state": self.entry_state,
            "closed_positions": self.closed_view(),
            "closed_positions_retention": {"until_hour_ist": CLOSED_VISIBLE_UNTIL_HOUR},
        }

    def session_view(self, symbol: str, context: dict | None = None,
                     vix: float | None = None,
                     snapshot: dict | None = None) -> dict | None:
        """The badge strip for one symbol; None before its first print.

        ``context`` is keyed by the UNDERLYING (see ``underlying_of``): an
        option contract rarely has a stored session of its own, and its
        prior-day value is its underlying's. The VIX range is taken around
        that underlying's price for the same reason.
        """
        profile = self.profiles.get(symbol)
        if profile is None:
            return None
        reference = self.underlying_of.get(symbol, symbol)
        day = self.session_day or datetime.now(IST).date().isoformat()
        if context is None:
            context = self.session_context(reference, day)
        if vix is None:
            vix = self.vix_price()
        return session_view(
            profile, flow=self.flow.states.get(symbol), context=context, vix=vix,
            reference_price=self.last_prices.get(reference),
            day=day,
            expiring=symbol in self.expiring_symbols,
            snapshot=snapshot,
        )

    def vix_price(self) -> float | None:
        """India VIX's last print, or None until one arrives.

        None is the honest answer before the first tick: a badge showing a
        range around a VIX nobody quoted would be a number the desk invented.
        """
        if not self.vix_symbol:
            return None
        return self.last_prices.get(self.vix_symbol)

    def session_context(self, underlying: str, day: str) -> dict | None:
        """The stored prior-day/week/month reference, cached for a minute.

        snapshot() used to call session_view with context=None, so the AUCTION
        tab's badge strip permanently read "value unknown" with no open-location
        badge -- three badges rendered as measured-and-unknown when in fact
        nobody had looked.

        Returns what the cache holds IMMEDIATELY and refreshes behind it. The
        read costs 7-16 ms against the live history database (naked POCs walk
        back 40 sessions), and snapshot() runs on the FastAPI event loop, so a
        synchronous refresh would stall live tick ingestion once a minute per
        underlying. The strip is therefore one poll -- three seconds -- behind
        on the first read of a session, and exact after it. The TTL stamp is
        claimed BEFORE the read starts so a 3 s poll cannot queue a second read
        behind the first, and a failure caches None rather than hammering a
        broken database. Off the loop (tests, scripts) it reads inline.
        """
        if not self.history_database_path or not underlying:
            return None
        now = time.monotonic()
        cached = self._context_cache.get(underlying)
        if cached is not None and now - cached[0] < CONTEXT_TTL_SECONDS:
            return cached[1]
        standing = cached[1] if cached else None
        self._context_cache[underlying] = (now, standing)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            self._context_cache[underlying] = (now, self._read_context(underlying, day))
            return self._context_cache[underlying][1]
        loop.create_task(self._refresh_context(underlying, day, now))
        return standing

    def _read_context(self, underlying: str, day: str) -> dict | None:
        try:
            from .auction_views import context as auction_context
            return auction_context(self.history_database_path, underlying, day,
                                   self.last_prices.get(underlying))
        except Exception:  # noqa: BLE001 -- a reference read must not break the desk
            return None

    async def _refresh_context(self, underlying: str, day: str, stamp: float) -> None:
        payload = await asyncio.to_thread(self._read_context, underlying, day)
        self._context_cache[underlying] = (stamp, payload)

    def ofi_view(self, symbol: str, timeframe_seconds: int = 60) -> dict | None:
        """Live level-1 OFI with a per-bar series aligned to the footprint."""
        return self.ofi.snapshot(symbol, time.time(), timeframe_seconds)

    def _ofi_tracked(self, symbol: str) -> bool:
        """Is anyone reading this contract's book?

        OFI is only ever displayed for the symbol whose footprint is open, and
        the footprint book is itself bounded at MAX_DETAIL_SYMBOLS. Feeding a
        state for every subscribed contract cost ~1 MB each against a container
        sitting at 196 MiB, for curves nothing draws.
        """
        return symbol in self.footprints.bars or symbol in self._directional_set

    def health(self) -> dict:
        now = datetime.now(UTC)

        def age(stamp: datetime | None) -> float | None:
            return round(max(0.0, (now - stamp).total_seconds()), 1) if stamp else None

        local = now.astimezone(IST)
        session_open = (
            is_trading_day(local.date())
            and SESSION_OPEN <= local.timetz().replace(tzinfo=None) < SESSION_CLOSE
        )
        classification = self.classification_health()
        blockers = []
        if not self.settings.enabled:
            blockers.append("desk disabled")
        if not session_open:
            blockers.append("regular session closed")
        if session_open and age(self.last_tick_at) is not None and age(self.last_tick_at) > 10:
            blockers.append("market ticks stale")
        if session_open and self.state_saved_at is None:
            blockers.append("no durable session checkpoint")
        if classification["prints"] < 100:
            blockers.append("insufficient classified prints")
        if classification["prints"] >= 100 and classification["quote_share"] < 0.5:
            blockers.append("quote-rule coverage below 50%")
        missing_tapes = self._directional_set - self.tapes.tapes.keys()
        if session_open and missing_tapes:
            blockers.append(f"session tape missing for {len(missing_tapes)} instrument(s)")
        if self.state_save_error:
            blockers.append("state persistence failed")
        if self.last_error:
            blockers.append("desk processing error")
        return {
            "mode": "paper",
            "enabled": self.settings.enabled,
            "auto_trade": self.settings.auto_trade,
            "session_open": session_open,
            "ready_for_auto_paper": not blockers,
            "blockers": blockers,
            "ticks_seen": self.ticks_seen,
            "last_tick_age_seconds": age(self.last_tick_at),
            "last_print_age_seconds": age(self.last_print_at),
            "off_session_ticks": self.off_session_ticks,
            "errors_total": self.errors_total,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "persistence": {
                "saved_at": self.state_saved_at,
                "bytes": self.state_rows_saved,
                "error": self.state_save_error,
            },
        }

    def carry_decision(self, symbol: str) -> tuple[bool, str]:
        """Allow overnight risk only when auction location and flow agree.

        A call must finish above value with buy imbalance; a put must finish
        below value with sell imbalance. Expiring contracts are never carried.
        """
        if not self.settings.allow_overnight_carry:
            return False, "selective overnight carry is disabled"
        if symbol in self.expiring_symbols:
            return False, "contract expires today"
        entry = self.entry_state.get(symbol, {})
        held = entry.get("option_type") or ("PE" if symbol.endswith("PE") else "CE")
        source = entry.get("underlying") or self.underlying_of.get(symbol)
        if not source:
            return False, "underlying auction source is unavailable"
        profile = self.profiles.get(source)
        flow = self.flow.states.get(source)
        if profile is None or flow is None:
            return False, "closing profile or order flow is unavailable"
        location = profile.position()
        imbalance = flow.imbalance
        required_location = "above_value" if held == "CE" else "below_value"
        flow_confirms = (
            imbalance >= self.settings.min_imbalance
            if held == "CE"
            else imbalance <= -self.settings.min_imbalance
        )
        if location != required_location:
            return False, f"{held} auction closed {location}, not {required_location}"
        if not flow_confirms:
            direction = "buy" if held == "CE" else "sell"
            return False, f"{direction} imbalance {imbalance:+.3f} lacks confirmation"
        return True, f"{held} {location} with imbalance {imbalance:+.3f}"

    def _record_carry_decision(self, symbol: str, allowed: bool, reason: str) -> None:
        self.entry_state.setdefault(symbol, {})["carry"] = {
            "allowed": allowed,
            "reason": reason,
            "evaluated_at": datetime.now(UTC).isoformat(),
        }

    async def close_positions(self, note: str = "MP_EOD_FAILSAFE") -> None:
        """Close remaining positions unless the closing MP+OF permits carry."""
        for symbol, position in list(self.portfolio.positions.items()):
            if position.quantity <= 0:
                continue
            allowed, detail = self.carry_decision(symbol)
            self._record_carry_decision(symbol, allowed, detail)
            if not allowed:
                self.entry_state.setdefault(symbol, {})["exit_reason"] = note
                await self.submit(symbol, "SELL", position.quantity, note=note)

    def _record_closed(self, before, held_before: int, trade: Trade, order: Order,
                       note: str, *, flat: bool) -> None:
        """Freeze the round trip the moment it closes.

        Portfolio.apply_trade deletes a flat position outright, so this is
        the only point where entry, exit, reason and excursion coexist. The
        P&L charges the exit fee plus the pro-rata slice of the entry fee,
        the basis statistics() uses for the Trades tab; the portfolio's
        realized_pnl is cash-basis and differs by the open positions' entry
        fees, as documented in test_mp_statistics_reconciliation.
        """
        state = self.entry_state.get(trade.symbol, {})
        entry = float(before.average_price)
        qty = int(trade.quantity)
        # A position opened before entry_fees existed (or restored across a day
        # boundary) still paid the entry leg; charge what the desk charges rather
        # than nothing, so the closed row and the Trades tab agree.
        entry_fees = float(state.get("entry_fees", self.settings.brokerage_per_leg))
        entry_fee_share = entry_fees * qty / held_before if held_before else 0.0
        pnl = (trade.price - entry) * qty - trade.fees - entry_fee_share
        exit_at = trade.timestamp.astimezone(IST)
        max_price = float(state.get("max_price") or max(before.peak_price, entry))
        min_price = float(state.get("min_price") or entry)
        record = {
            "id": trade.trade_id, "order_id": order.order_id,
            "symbol": trade.symbol, "setup": state.get("setup"),
            "option_type": state.get("option_type") or ("PE" if trade.symbol.endswith("PE") else "CE"),
            "underlying": state.get("underlying"),
            "entry_time": before.opened_at.isoformat(), "entry_price": entry,
            "exit_time": exit_at.isoformat(timespec="seconds"),
            "exit_price": float(trade.price),
            "exit_reason": state.get("exit_reason") or note or "MP_MANUAL",
            "quantity": qty, "lots": order.lots, "lot_size": order.lot_size,
            "pnl": round(pnl, 4),
            "return_pct": round((trade.price / entry - 1) * 100, 4) if entry else 0.0,
            "fees": round(trade.fees + entry_fee_share, 4),
            "max_price": max_price, "min_price": min_price,
            "max_return_pct": round((max_price / entry - 1) * 100, 4) if entry else 0.0,
            "min_return_pct": round((min_price / entry - 1) * 100, 4) if entry else 0.0,
            "max_at": state.get("max_at"), "min_at": state.get("min_at"),
            "partial": not flat,
            "closed_day": exit_at.date().isoformat(),
            "visible_until": self._visible_until(exit_at).isoformat(timespec="seconds"),
        }
        self.closed_positions.insert(0, record)
        self.closed_positions = self.closed_positions[:200]
        with suppress(Exception):   # a persistence fault must not reject the fill
            self.repository.save_mp_closed_position(record)
        self._publish("mp_closed_position", record)
        if flat:
            # The position is gone from the book; its identity now lives in
            # the closed record. Leaving it made statistics() count it as open.
            self.entry_state.pop(trade.symbol, None)
        elif trade.symbol in self.entry_state:
            # The remainder still owes only the entry fee not yet charged, and
            # a stop's reason must not label a later manual exit.
            state["entry_fees"] = entry_fees - entry_fee_share
            state.pop("exit_reason", None)

    @staticmethod
    def _visible_until(exit_at: datetime) -> datetime:
        # Deliberately the MACD lane's helper rather than a second copy: this
        # one stamped the next CALENDAR day, so a Friday exit vanished on
        # Saturday morning while the other lane held its own until Monday.
        return closed_visible_until(exit_at)

    def _prune_closed(self, now: datetime | None = None) -> None:
        """Drop closed rows past 08:00 IST of the next weekday after their exit.

        The in-memory filter compares parsed datetimes and so is timezone
        exact, but the DELETE behind it is a string comparison against a
        UTC-normalised column. Handing that an IST stamp made it delete rows
        that were still inside their window; it is given UTC for the same
        reason _restore is.
        """
        moment = (now or datetime.now(IST)).astimezone(IST)
        kept = [row for row in self.closed_positions
                if datetime.fromisoformat(row["visible_until"]) > moment]
        if len(kept) != len(self.closed_positions):
            self.closed_positions = kept
            with suppress(Exception):   # persistence must not kill the read path
                self.repository.purge_mp_closed_positions(
                    moment.astimezone(UTC).isoformat(timespec="seconds"))

    def closed_view(self) -> list[dict]:
        self._prune_closed()
        return list(self.closed_positions)

    async def stop(self) -> None:
        """Drain background persistence, take a final snapshot, then close."""
        await self.flush_state()
        await asyncio.to_thread(self.save_state)
        self.repository.close()

    def classification_health(self) -> dict:
        """Desk-wide aggressor-classification quality.

        ``quote_share`` near zero means the broker is not sending depth and
        every delta number is a tick-rule inference — worth knowing before
        trusting a divergence signal.
        """
        totals = {"quote": 0, "mid": 0, "tick": 0}
        prints = depth = unclassified = 0
        for state in self.flow.states.values():
            for key, value in state.methods.items():
                totals[key] = totals.get(key, 0) + value
            prints += state.trades
            depth += state.quotes_seen
            unclassified += state.unclassified
        return {
            "prints": prints,
            "methods": totals,
            "quote_share": round(totals["quote"] / prints, 3) if prints else 0.0,
            "depth_tick_share": round(depth / prints, 3) if prints else 0.0,
            "unclassified_share": round(unclassified / prints, 3) if prints else 0.0,
        }

    def statistics(self) -> dict:
        """Round-trip statistics for this desk's own book."""
        rows = sorted(self.repository.rows("trades", 100_000), key=lambda r: r["timestamp"])
        inventory: dict[str, list] = {}
        closed: list[dict] = []
        for row in rows:
            symbol, side = row["symbol"], row["side"]
            qty, price = int(row["quantity"]), float(row["price"])
            if side == "BUY":
                held = inventory.setdefault(symbol, [0, 0.0, row["timestamp"], 0.0])
                held[0] += qty
                held[1] += qty * price
                held[3] += float(row.get("fees", 0.0))
                continue
            held = inventory.get(symbol)
            if not held or held[0] <= 0:
                continue
            closing = min(held[0], qty)
            entry = held[1] / held[0]
            entry_fee = held[3] * closing / held[0]
            exit_fee = float(row.get("fees", 0.0)) * closing / qty if qty else 0.0
            closed.append({
                "symbol": symbol, "entry": entry, "exit": price, "quantity": closing,
                "pnl": (price - entry) * closing - entry_fee - exit_fee,
                "return_pct": (price / entry - 1) * 100 if entry else 0.0,
                "entry_time": held[2], "exit_time": row["timestamp"],
            })
            held[0] -= closing
            held[1] = entry * held[0]
            held[3] -= entry_fee
            if held[0] <= 0:
                inventory.pop(symbol, None)
        # Entry fees still parked in the FIFO inventory: every OPEN position paid
        # its entry fee at fill time, but `closed` only ever charges the pro-rata
        # slice belonging to a round trip that actually closed. So `net` below is
        # a round-trip figure that legitimately excludes them, while the
        # portfolio's realized_pnl is cash-basis and has already subtracted every
        # rupee that left the account. Reporting only `net` made the two surfaces
        # disagree with no way to see why (measured 2026-08-27: net 21,112.691 vs
        # realized_pnl 21,032.691 — exactly the 4 open positions' Rs 20 entry
        # fees). Surfacing the bridge is the difference between a reconciliation
        # and a discrepancy.
        open_entry_fees = sum(held[3] for held in inventory.values())
        wins = [r["pnl"] for r in closed if r["pnl"] > 0]
        losses = [-r["pnl"] for r in closed if r["pnl"] < 0]
        gross_win, gross_loss = sum(wins), sum(losses)
        by_setup: dict[str, dict] = {}
        for symbol, state in self.entry_state.items():
            setup = state.get("setup") or "unknown"
            bucket = by_setup.setdefault(setup, {"setup": setup, "open": 0})
            bucket["open"] += 1
        return {
            "round_trips": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": (len(wins) / len(closed) * 100) if closed else 0.0,
            "gross_profit": gross_win,
            "gross_loss": -gross_loss,
            "net": gross_win - gross_loss,
            "profit_factor": (gross_win / gross_loss) if gross_loss else (float("inf") if gross_win else 0.0),
            "expectancy": ((gross_win - gross_loss) / len(closed)) if closed else 0.0,
            "average_win": (gross_win / len(wins)) if wins else 0.0,
            "average_loss": (-gross_loss / len(losses)) if losses else 0.0,
            "best": max((r["pnl"] for r in closed), default=0.0),
            "worst": min((r["pnl"] for r in closed), default=0.0),
            "open_by_setup": list(by_setup.values()),
            # The bridge between this view and the portfolio's cash-basis
            # realized_pnl: net - open_position_entry_fees == realized_pnl.
            "open_position_entry_fees": open_entry_fees,
            "net_cash_basis": gross_win - gross_loss - open_entry_fees,
            "round_trip_rows": closed[-200:],
        }

    def leaderboard(self, limit: int = 25) -> list[dict]:
        """Symbols ranked by how constructive their auction currently reads."""
        rows = []
        for symbol, profile in self.profiles.profiles.items():
            state = self.flow.states.get(symbol)
            if state is None or profile.last_price is None:
                continue
            setup, reason = self.live_setups.get(symbol, (None, "no tick yet"))
            vah, val = profile.value_area()
            absorption = self.flow.absorption(symbol)
            rows.append({
                "symbol": symbol,
                "last": profile.last_price,
                "open": profile.open_price,
                "high": profile.high,
                "low": profile.low,
                "poc": profile.poc,
                "vah": vah,
                "val": val,
                "ib_high": profile.ib_high,
                "ib_low": profile.ib_low,
                "brackets": len(profile.brackets_seen),
                "position": profile.position(),
                "day_type": profile.day_type(),
                "open_type": profile.open_type(),
                "imbalance": round(state.imbalance, 3),
                "cumulative_delta": round(state.cumulative_delta, 1),
                "buy_volume": state.buy_volume,
                "sell_volume": state.sell_volume,
                "trades": state.trades,
                "absorption": absorption.get("side"),
                "lot_size": self.lot_sizes.get(symbol, 0),
                "setup": setup,
                "reason": reason,
            })
        rows.sort(key=lambda row: (row["setup"] is None, -row["imbalance"]))
        return rows[:limit]


def _now_minute() -> int:
    now = datetime.now(IST)
    return now.hour * 60 + now.minute


def _as_dict(obj) -> dict:
    from dataclasses import asdict
    payload = asdict(obj)
    for key, value in list(payload.items()):
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
    return payload
