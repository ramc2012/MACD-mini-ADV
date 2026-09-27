"""The blast lane: a third book that hunts large option moves.

Why it exists
-------------
The MACD lane enters on a premium MACD zero-cross and manages the trade with a
scale-out ladder. Measured over 54,480 liquid zero-crosses (Jun-Sep 2026) that
combination is close to a coin flip: the lane's own 311 closed positions came
to a mean of -0.16% per position, with 76% of the month's rupees produced by
five trades. The moves are in the tail, and three things about the tail do not
fit the MACD lane's shape:

1. **The entry is the wrong event.** Inside the screen below, 98% of
   qualifying candidates (10,544 of 10,739) are signal-line crosses with MACD
   still below zero, and only 192 are zero-crosses. A contract 30% off its own
   high has MACD far below zero; by the time MACD reaches zero it has already
   rallied out of the screen. The MACD lane structurally cannot buy these.

2. **The selector is premium relative to SPOT, not premium.** Walk-forward on
   8 unseen sessions: contracts at or below 1.2% of spot doubled within two
   sessions 14.6% of the time against 7.9% for all candidates unfiltered, and
   the full screen reached 17.5%. Raw rupee premium looked predictive in an earlier cut
   and was not -- it was this variable in disguise, and it made cheap contracts
   on cheap underlyings look special when what matters is the ratio.

3. **The exit is the wrong shape.** Mean forward excursion on screened
   candidates is near +100%; the +30/+50/+75 ladder converts that into roughly
   nothing. A -50% stop with a 40%-from-peak trail keeps the tail.

What the walk-forward does NOT establish is the rupee edge: the full screen
came to +5.7% per candidate with a 95% interval of [-1.4, +18.1] on 57 picks,
permutation p=0.080. That is the right sign and an unproven size. So the lane
ships with ``blast_auto_trade`` OFF: it journals every candidate it evaluates,
takes nothing, and accumulates the sample that decides the question. Turning it
on is a deliberate act, exactly like ``macd_invalidation_exit``.

The journal is the point
------------------------
Every evaluated candidate is written to ``blast_journal`` with the rule inputs
and the verdict -- taken or the reason it was not -- and each one that clears
the premium gate is then watched forward for ``blast_journal_horizon_hours`` so
its excursion is recorded whether or not the lane bought it. Rejected rows are
the control group; without them there is no way to tell which legs of the
screen are earning their place.

It runs on one-minute bars
--------------------------
The study measured MACD(12,26,9) on one-minute premium bars, a 750-bar (two
session) recent high and breadth read off one-minute MACD. The lane therefore
keeps its own one-minute bars and MACD rather than borrowing the MACD lane's
strategy-timeframe state: fed 30-minute bars, the same screen judged a
different cross, and a 120-bar history minimum became nine sessions, so on
15 Sep 2026 65% of candidates were refused as NO_HISTORY and the one that
passed was a 30-minute cross the research never tested.

It never asks the broker for data
---------------------------------
Everything the screen reads is already in the process: one-minute bars from
the engine's own tick aggregation, their history from the minute bars the
candle writer has already stored locally, the underlying's price from the tick
stream, and breadth from the lane's own MACD state. The lane is handed an
``OrderOnlyBroker`` that raises on any attribute but ``place_order`` -- so a
history or quote request from here is an error, not a quiet extra REST call --
and its bar history is strictly incremental: a replayed bar it has already
folded in is ignored rather than appended twice. Held contracts keep ticking
because the engine subscribes them on the websocket it already runs; their
valuation after a restart comes from quotes the engine fetched for its own
books, never from a request made on this lane's behalf.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections import deque
from contextlib import suppress
from threading import Lock
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from .brokers import Broker
from .config import Settings
from .events import EventHub, json_value
from .execution import ExecutionManager
from .indicators import IncrementalMACD
from .models import Candle, IndicatorPoint, Signal, Trade
from .portfolio import Portfolio, breakeven_exit_price
from .repository import TradeRepository

IST = ZoneInfo("Asia/Kolkata")
LANE = "blast"
SIGNAL_KIND = "BLAST_PREMIUM_MACD_SIGNAL_CROSS_UP"
# The bar and MACD the screen was measured on. Deliberately not the strategy
# timeframe or the MACD lane's periods: changing those must not change this.
BAR_SECONDS = 60
MACD_PERIODS = (12, 26, 9)
# Extra stored bars read beyond the lookback so the EMAs have converged by the
# time the first live bar is judged.
MACD_SEED_BARS = 250
# A contract's MACD counts toward breadth only if its newest bar is this
# recent. One-minute bars close on the next tick, so an exact "same minute"
# cohort would be a handful of names at the moment any bar is judged.
BREADTH_FRESH_SECONDS = 300
# A minute bar closes when the contract's next tick arrives. When that tick
# comes long after the minute ended, the cross is history, not an entry.
LATE_BAR_GRACE_SECONDS = 120
# Verdicts. Everything except TAKEN is a rejection, and the rejections are
# ordered from "could not evaluate" to "evaluated and declined" so a journal
# scan can separate missing data from a real veto.
NO_SPOT = "NO_SPOT"
NO_BREADTH = "NO_BREADTH"
NO_HISTORY = "NO_HISTORY"
PREMIUM_TOO_RICH = "PREMIUM_TOO_RICH"
BREADTH_TOO_THIN = "BREADTH_TOO_THIN"
NOT_OFF_HIGH = "NOT_OFF_HIGH"
TOO_ILLIQUID = "TOO_ILLIQUID"
ALREADY_HELD = "ALREADY_HELD"
AUTO_TRADE_OFF = "AUTO_TRADE_OFF"
TAKEN = "TAKEN"


class BrokerAccessDenied(PermissionError):
    """Raised when anything in the blast lane reaches for broker data."""


class OrderOnlyBroker:
    """The only broker surface this lane is allowed to hold.

    ExecutionManager needs ``place_order`` and nothing else; in paper mode it
    does not even need that. Every other attribute -- ``history``, ``quotes``,
    ``history_range``, ``stream`` -- raises, which turns the lane's data policy
    from a convention into something a test can assert.
    """

    def __init__(self, broker):
        self._broker = broker

    def rebind(self, broker) -> None:
        """Follow the engine when it rebuilds its broker on a feed reconfigure."""
        self._broker = broker

    @property
    def name(self) -> str:
        return getattr(self._broker, "name", "unknown")

    async def place_order(self, order):
        return await self._broker.place_order(order)

    def __getattr__(self, attribute: str):
        raise BrokerAccessDenied(
            f"the blast lane may not call broker.{attribute}: it reads only data the engine already holds"
        )


class LaneEvents:
    """Publishes every event of this lane under a ``blast_`` name.

    ExecutionManager publishes ``order``, ``trade``, ``portfolio`` and
    ``position_closed`` -- the MACD lane's own event names. Handed the shared
    hub directly, a blast fill would arrive in the MACD lane's order and trade
    tabs and replace its portfolio on screen. The auction desk avoids the same
    collision by publishing ``mp_*`` names; this is that convention applied
    without forking the shared execution code.
    """

    def __init__(self, events: EventHub, prefix: str = "blast_"):
        self._events = events
        self._prefix = prefix

    def publish(self, event_type: str, data) -> None:
        self._events.publish(f"{self._prefix}{event_type}", data)

    @property
    def client_count(self) -> int:
        return self._events.client_count


def blast_execution_settings(settings: Settings) -> Settings:
    """The lane's risk parameters expressed in the fields ExecutionManager reads.

    A copy rather than a proxy so nothing here can write back into the shared
    Settings the MACD lane is using. ``max_trade_lots = 1`` is what disables
    pyramiding: ExecutionManager gates each add on ``entry_stage <
    max_trade_lots`` and an entry sized by notional already sets entry_stage to
    the number of lots it bought. Scale-outs are suppressed separately, by
    BlastExecutionManager.on_tick not implementing them.
    """
    return settings.model_copy(update={
        "initial_capital": settings.blast_initial_capital,
        "hard_stop_pct": settings.blast_hard_stop_pct,
        "trailing_activation_pct": settings.blast_trail_activation_pct,
        "trailing_stop_pct": settings.blast_trail_pct,
        "target_position_notional": settings.blast_target_notional,
        "max_target_entry_lots": settings.blast_max_entry_lots,
        "max_positions": settings.blast_max_positions,
        "max_trade_lots": 1,
        "auto_trade": settings.blast_auto_trade,
        "min_cash_reserve": 0.0,
    })


class BlastExecutionManager(ExecutionManager):
    """Stop-and-trail only. No ladder, no pyramid.

    The base class's on_tick does five things: mark, fill resting orders, run
    protective exits, pyramid, and scale out. Only the first three belong to
    this lane, and rather than bolt two more flags onto the shared manager the
    tail is simply not implemented here. Entries arrive from BlastEngine, so
    on_signal is a no-op as well -- the base class would enter on any BUY
    signal it was handed, and this lane's entry has to pass the screen first.
    """

    async def on_tick(self, symbol: str, price: float, timestamp: datetime | None = None) -> None:
        self.set_quote(symbol, price, timestamp)
        try:
            self._fresh_price(symbol)
        except RuntimeError:
            return
        if self.portfolio.mark(symbol, price):
            self.events.publish("portfolio", self.portfolio_snapshot())
        async with self._lock:
            for order in list(self.orders.values()):
                if order.symbol == symbol and order.status == "OPEN":
                    try:
                        await self._paper_fill(order)
                    except (ValueError, RuntimeError):
                        order.status = "REJECTED"
                        self.repository.save_order(order)
                        self.events.publish("order", order)
        position = self.portfolio.positions.get(symbol)
        if not position or position.quantity <= 0:
            return
        if price > position.peak_price:
            position.peak_price = price
            if position.peak_price >= position.average_price * (1 + self.settings.trailing_activation_pct):
                # Floored at the round-trip breakeven for the same reason the
                # other two lanes are: a 40% trail armed at +30% would sit at
                # 1.30 * 0.60 = 0.78x entry and turn a winner into a 22% loss.
                floor = breakeven_exit_price(
                    position.average_price, position.quantity, self.settings.slippage_bps,
                )
                trail = position.peak_price * (1 - self.settings.trailing_stop_pct)
                position.trailing_stop = round(max(trail, floor), 4)
        reason = None
        if position.hard_stop and price <= position.hard_stop:
            reason = f"BLAST_HARD_STOP_{round(self.settings.hard_stop_pct * 100)}_PCT"
        elif position.trailing_stop and price <= position.trailing_stop:
            reason = f"BLAST_TRAILING_STOP_{round(self.settings.trailing_stop_pct * 100)}_PCT"
        if reason:
            self.arm_exit(symbol, reason)
            await self.submit(symbol, "SELL", position.quantity)

    async def on_signal(self, signal: Signal) -> None:
        """Entries come from BlastEngine.on_closed_bar, which owns the screen."""
        return None


class BlastEngine:
    """Screen, journal and (optionally) trade the blast setup.

    The lane keeps its own one-minute bars and MACD(12,26,9) per contract --
    the resolution the screen was measured at -- independent of the MACD
    lane's strategy timeframe. Tests may still hand ``on_closed_bar`` an
    explicit IndicatorPoint to stage a cross.
    """

    def __init__(self, database_path: str, settings: Settings, broker: Broker, events: EventHub):
        self.settings = settings
        self.events = LaneEvents(events)
        self.broker = OrderOnlyBroker(broker)
        self.repository = TradeRepository(database_path)
        self.portfolio = Portfolio(settings.blast_initial_capital, lane=LANE)
        self.execution = BlastExecutionManager(
            blast_execution_settings(settings), self.broker, self.portfolio, self.repository, self.events,
        )
        # symbol -> (macd, signal) of the previously closed bar.
        self._previous: dict[str, tuple[float, float]] = {}
        # Newest bar folded into each window, and newest bar that set
        # _previous. These are what make ingestion incremental: the engine
        # replays stored history on every connect -- startup, the daily
        # contract re-selection, a feed reconfigure -- and without them each
        # replay re-appended bars the window already held, out of order.
        self._last_bar: dict[str, int] = {}
        self._previous_bar: dict[str, int] = {}
        # The lane's own one-minute MACD per symbol, and its newest value.
        self._macd: dict[str, IncrementalMACD] = {}
        self._points: dict[str, IndicatorPoint] = {}
        self.late_bars_skipped = 0
        self.warmed_symbols = 0
        self.last_warm_at: str | None = None
        # Verdict rows nobody is watching are written in batches off the event
        # loop; one-minute crosses across the whole band are thousands a day.
        self._pending_journal: list[dict] = []
        self._journal_lock = Lock()
        self.last_candidate_at: str | None = None
        # symbol -> rolling window of closed-bar highs, the "own recent high"
        # the screen measures distance from.
        self._highs: dict[str, deque[float]] = {}
        self._spot_symbols: dict[str, str] = {}
        self._spot_price = lambda _symbol: None
        self._breadth = self.side_breadth
        self._contracts: dict[str, dict] = {}
        # journal_id -> live excursion watcher for a candidate already written,
        # plus a symbol index: track() runs on every tick of every watched
        # contract, so it must never scan the whole watcher set.
        self._watchers: dict[str, dict] = {}
        self._watched_by_symbol: dict[str, set[str]] = {}
        self.evaluated = 0
        self.taken = 0
        self.rejections: dict[str, int] = {}
        self.error: str | None = None
        self._restore()
        self._restore_contract_context()
        self._rehydrate_watchers()

    # ---------------------------------------------------------------- wiring

    def set_tradable_contracts(self, lot_sizes: dict[str, int]) -> None:
        self.execution.set_tradable_contracts(lot_sizes)

    def set_contract_context(self, contracts: dict[str, dict]) -> None:
        """symbol -> {spot_symbol, option_type, strike, expiry}.

        Merged, not replaced: the engine passes today's selection, and a
        contract this lane still holds may have rolled out of it. Dropping its
        context would lose the expiry the lane flattens it on.
        """
        held = set(self.portfolio.positions)
        merged = {symbol: row for symbol, row in self._contracts.items() if symbol in held}
        merged.update(contracts)
        self._contracts = merged
        self._spot_symbols = {
            symbol: row["spot_symbol"] for symbol, row in merged.items() if row.get("spot_symbol")
        }

    def rebind_broker(self, broker) -> None:
        self.broker.rebind(broker)

    def set_resolvers(self, *, spot_price, breadth=None) -> None:
        """``spot_price(spot_symbol) -> float|None``; ``breadth('CE'|'PE')`` overrides the lane's own."""
        self._spot_price = spot_price
        self._breadth = breadth or self.side_breadth

    def side_breadth(self, option_type: str) -> float | None:
        """Share of this side's contracts whose one-minute premium MACD is above zero.

        Read across every contract the lane knows (the whole tradable band),
        counting only those with a bar in the last BREADTH_FRESH_SECONDS so a
        contract that stopped ticking cannot freeze its MACD into the ratio.
        """
        points = [
            point for symbol, point in self._points.items()
            if symbol in self._contracts
            and (self._contracts[symbol].get("option_type") or symbol[-2:]) == option_type
        ]
        if not points:
            return None
        newest = max(point.timestamp for point in points)
        fresh = [point for point in points if point.timestamp >= newest - BREADTH_FRESH_SECONDS]
        if len(fresh) < self.settings.blast_min_breadth_cohort:
            return None
        return sum(1 for point in fresh if point.macd > 0) / len(fresh)

    @property
    def lookback_bars(self) -> int:
        """Length of the recent-high window, in one-minute bars."""
        return max(30, int(self.settings.blast_high_lookback_bars))

    # ------------------------------------------------------------- restoring

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

    def _restore_contract_context(self) -> None:
        """Recover held contracts' expiry and underlying from this lane's own table.

        The contract selector only knows today's band, so after a restart a
        position in a contract that has since rolled out would otherwise have
        no expiry at all -- and nothing would flatten it before it stopped
        ticking.
        """
        stored = self.repository.load_blast_contracts()
        for symbol in self.portfolio.positions:
            if symbol in stored and symbol not in self._contracts:
                self._contracts[symbol] = stored[symbol]
        self._spot_symbols = {
            symbol: row["spot_symbol"] for symbol, row in self._contracts.items() if row.get("spot_symbol")
        }

    def _record_context(self, symbol: str) -> None:
        context = self._contracts.get(symbol)
        if context:
            self.repository.save_blast_contract(symbol, context)

    def _rehydrate_watchers(self) -> None:
        """Resume forward tracking of candidates journalled before the restart.

        Without this every restart truncates the shadow sample to whatever
        happened to be watched at the time, which would bias the recorded
        excursions towards short, uneventful windows.
        """
        now = datetime.now(UTC)
        for row in self.repository.blast_journal_unresolved(now.isoformat(), limit=5_000):
            self._watchers[row["id"]] = {
                "symbol": row["symbol"],
                "entry": float(row["premium"] or 0.0),
                "high": float(row["watch_high"] or row["premium"] or 0.0),
                "low": float(row["watch_low"] or row["premium"] or 0.0),
                "until": row["watch_until"],
                "dirty": False,
            }
            self._watched_by_symbol.setdefault(row["symbol"], set()).add(row["id"])

    # --------------------------------------------------------- bar ingestion

    def observe_warmup(self, candle: Candle, point: IndicatorPoint | None = None) -> None:
        """Fold a stored one-minute bar into the window and the lane's MACD.

        Safe to call repeatedly with the same bars: see _remember.
        """
        self._remember(candle, point)

    async def warm_from_store(self, database_path: str, symbols) -> int:
        """Seed bars and MACD from minute bars already stored on this machine.

        Reads ``historical_candles`` -- the table the engine's candle writer
        and earlier history downloads fill -- never the broker. Only bars newer
        than the ones already held are read, so a re-warm on the daily contract
        re-selection costs a few rows, not the whole window again.
        """
        wanted = list(dict.fromkeys(symbols))
        since = {symbol: self._last_bar.get(symbol, -1) for symbol in wanted}
        limit = self.lookback_bars + MACD_SEED_BARS
        stored = await asyncio.to_thread(read_stored_minute_bars, database_path, since, limit)
        for symbol in wanted:
            for candle in stored.get(symbol, ()):
                self._remember(candle)
        self.warmed_symbols = sum(1 for symbol in wanted if self._highs.get(symbol))
        self.last_warm_at = datetime.now(UTC).isoformat()
        return self.warmed_symbols

    def reset_history(self) -> None:
        """Forget bar history and MACD state."""
        self._highs.clear()
        self._previous.clear()
        self._last_bar.clear()
        self._previous_bar.clear()
        self._macd.clear()
        self._points.clear()

    def _indicator(self, candle: Candle) -> IndicatorPoint:
        macd = self._macd.get(candle.symbol)
        if macd is None:
            macd = self._macd[candle.symbol] = IncrementalMACD(*MACD_PERIODS)
        value = macd.update(candle.close)
        point = IndicatorPoint(candle.symbol, candle.timestamp, value.macd, value.signal, value.histogram)
        self._points[candle.symbol] = point
        return point

    def _remember(self, candle: Candle, point: IndicatorPoint | None = None) -> tuple[bool, IndicatorPoint | None]:
        """Fold one closed bar in, exactly once. Returns (was new, its MACD point).

        A bar at or before the newest one already held is a replay: it adds
        nothing to the window and does not advance the MACD. Without an
        explicit point the lane computes its own from the bar's close.
        """
        symbol = candle.symbol
        fresh = candle.timestamp > self._last_bar.get(symbol, -1)
        if fresh:
            window = self._highs.get(symbol)
            if window is None or window.maxlen != self.lookback_bars:
                window = deque(window or (), maxlen=self.lookback_bars)
                self._highs[symbol] = window
            window.append(candle.high)
            self._last_bar[symbol] = candle.timestamp
            if point is None:
                point = self._indicator(candle)
        if point is not None and candle.timestamp >= self._previous_bar.get(symbol, -1):
            self._previous[symbol] = (point.macd, point.signal)
            self._previous_bar[symbol] = candle.timestamp
        return fresh, point

    async def on_minute_bar(self, candle: Candle, closed_at: datetime) -> dict | None:
        """A live one-minute bar from the engine's aggregator.

        ``closed_at`` is the exchange time of the tick that closed it. A bar
        closed long after its minute ended still advances the window and MACD,
        but is not judged: its cross happened while nothing was tradable.
        """
        if candle.symbol not in self._contracts:
            return None
        lateness = closed_at.timestamp() - (candle.timestamp + BAR_SECONDS)
        if lateness > LATE_BAR_GRACE_SECONDS:
            if self._remember(candle)[0]:
                self.late_bars_skipped += 1
            return None
        return await self.on_closed_bar(candle)

    async def on_closed_bar(self, candle: Candle, point: IndicatorPoint | None = None) -> dict | None:
        """Evaluate one closed bar. Returns the journal row when one was written."""
        if not self.settings.blast_enabled:
            self._remember(candle, point)
            return None
        previous = self._previous.get(candle.symbol)
        # The lookback window must exclude the bar being evaluated: the screen
        # asks how far the premium is below its RECENT high, and a breakout bar
        # is its own high.
        window = self._highs.get(candle.symbol)
        recent_high = max(window) if window else None
        bars = len(window) if window else 0
        fresh, point = self._remember(candle, point)
        if not fresh:
            return None  # a bar already folded in is never judged a second time
        if previous is None or point is None:
            return None
        crossed = previous[0] <= previous[1] and point.macd > point.signal
        if not crossed:
            return None
        try:
            return await self._evaluate(candle, point, recent_high, bars)
        except Exception as exc:  # noqa: BLE001 -- a lane fault must not kill the feed
            self.error = f"{type(exc).__name__}: {exc}"
            return None

    async def _evaluate(self, candle: Candle, point: IndicatorPoint,
                        recent_high: float | None, bars: int) -> dict | None:
        symbol = candle.symbol
        premium = candle.close
        contract = self._contracts.get(symbol)
        if contract is None or premium <= 0:
            return None  # not a tradable contract of ours; nothing to journal
        option_type = contract.get("option_type") or ("CE" if symbol.endswith("CE") else "PE")
        spot_symbol = self._spot_symbols.get(symbol)
        spot = self._spot_price(spot_symbol) if spot_symbol else None
        breadth = self._breadth(option_type)
        premium_pct = (premium / spot * 100.0) if spot else None
        off_high_pct = ((premium / recent_high - 1.0) * 100.0) if recent_high else None

        reason = None
        # A contract already in the book is not a screen decision at all: the
        # lane never pyramids, and the position's own excursion is the record
        # that matters. Judged last, a holding's re-signals were filed under
        # whichever leg they happened to fail, which buried 76% of the
        # BREADTH_TOO_THIN comparison group in rows the lane already owned and
        # made the control groups unreadable.
        if symbol in self.portfolio.positions:
            reason = ALREADY_HELD
        elif not spot:
            reason = NO_SPOT
        elif breadth is None:
            reason = NO_BREADTH
        elif bars < min(self.lookback_bars, self.settings.blast_min_lookback_bars):
            reason = NO_HISTORY
        elif premium_pct > self.settings.blast_max_premium_pct:
            reason = PREMIUM_TOO_RICH
        elif breadth < self.settings.blast_min_breadth:
            reason = BREADTH_TOO_THIN
        elif off_high_pct > -self.settings.blast_min_off_high_pct:
            reason = NOT_OFF_HIGH
        elif self._liquidity_lots(symbol) == 0:
            reason = TOO_ILLIQUID
        elif not self.settings.blast_auto_trade:
            reason = AUTO_TRADE_OFF

        row = {
            "id": uuid4().hex,
            "at": datetime.now(UTC).isoformat(),
            "day": datetime.now(IST).date().isoformat(),
            "symbol": symbol,
            "bar_timestamp": candle.timestamp,
            "spot_symbol": spot_symbol,
            "option_type": option_type,
            "premium": premium,
            "spot": spot,
            "premium_pct": premium_pct,
            "breadth": breadth,
            "off_high_pct": off_high_pct,
            "high_ref": recent_high,
            "lookback_bars": bars,
            "macd": point.macd,
            "signal": point.signal,
            "histogram": point.histogram,
            "taken": 0,
            "reason": reason or TAKEN,
            "order_id": None,
            "strike": contract.get("strike"),
            "expiry": contract.get("expiry"),
        }

        signal = Signal(
            symbol, "BUY", SIGNAL_KIND, premium, point.macd, point.signal, point.histogram,
            evaluated_candle_timestamp=candle.timestamp,
        )
        if reason is None:
            self.repository.save_signal(signal)
            order = await self._enter(signal)
            if order is None:
                row["reason"] = "ENTRY_REJECTED"
            else:
                row["taken"] = 1
                row["order_id"] = order.order_id
                self.taken += 1

        self.evaluated += 1
        self.rejections[row["reason"]] = self.rejections.get(row["reason"], 0) + 1
        self.last_candidate_at = row["at"]
        self._journal(row)
        self.events.publish("candidate", row)
        return row

    def _liquidity_lots(self, symbol: str) -> int | None:
        """Lots this contract's own turnover will carry, or None when unknown.

        The reading is the ladder's own -- max(volume, oi/100) off the
        selection-time chain -- so the screen and the strike selector agree on
        what "liquid" means. Unknown liquidity leaves the entry uncapped: a
        missing field is a data gap, not a verdict, and refusing on it would
        silently stop the lane.
        """
        contract = self._contracts.get(symbol) or {}
        liquidity = max(float(contract.get("volume") or 0), float(contract.get("oi") or 0) / 100.0)
        lot_size = self.execution.lot_sizes.get(symbol, 0)
        if liquidity <= 0 or lot_size < 1:
            return None
        return int(liquidity * (self.settings.blast_max_volume_share_pct / 100.0) // lot_size)

    async def _enter(self, signal: Signal):
        price = self.execution.last_prices.get(signal.symbol, signal.price)
        try:
            lots = self.execution.entry_lots(signal.symbol, price)
            cap = self._liquidity_lots(signal.symbol)
            if cap is not None:
                lots = min(lots, cap)
            if lots < 1:
                return None
            order = await self.execution.submit(signal.symbol, "BUY", lots=lots, signal=signal)
        except (ValueError, RuntimeError, PermissionError) as exc:
            self.error = f"entry rejected: {exc}"
            return None
        self._record_context(signal.symbol)
        return order

    async def manual_order(self, symbol: str, side: str, lots: int, *,
                           order_type: str = "MARKET", limit_price: float | None = None):
        """A hand-placed ticket against this lane's book. Screen bypassed, risk overlay not."""
        order = await self.execution.submit(symbol, side, lots=lots, order_type=order_type, limit_price=limit_price)
        if side == "BUY":
            self._record_context(symbol)
        return order

    # -------------------------------------------------------------- journal

    def _journal(self, row: dict) -> None:
        """Persist the verdict, and start watching the ones worth comparing.

        Only candidates that clear the premium gate are watched forward. That
        is the comparison set the screen's remaining legs have to justify
        themselves against; watching every signal-line cross in the universe
        would be tens of thousands of live trackers for a question nobody
        asked. A contract already held is excluded too: its excursion is
        already tracked on the position, and watching it again both doubles
        the per-tick work and counts one holding many times over.
        """
        watch_until = None
        interesting = row["reason"] not in {NO_SPOT, NO_BREADTH, NO_HISTORY, PREMIUM_TOO_RICH, ALREADY_HELD}
        if interesting and len(self._watchers) < self.settings.blast_journal_max_watchers:
            until = datetime.now(UTC) + timedelta(hours=self.settings.blast_journal_horizon_hours)
            watch_until = until.isoformat()
            self._watchers[row["id"]] = {
                "symbol": row["symbol"], "entry": row["premium"],
                "high": row["premium"], "low": row["premium"],
                "until": watch_until, "dirty": False,
            }
            self._watched_by_symbol.setdefault(row["symbol"], set()).add(row["id"])
        row["watch_until"] = watch_until
        if watch_until is not None:
            # A watched row is updated in place by flush_watchers, so it must exist first.
            self.repository.save_blast_journal(row)
        else:
            with self._journal_lock:
                self._pending_journal.append(row)

    def flush_journal(self) -> int:
        """Write buffered verdict rows in one commit. Safe from a worker thread."""
        with self._journal_lock:
            rows, self._pending_journal = self._pending_journal, []
        if rows:
            try:
                self.repository.save_blast_journal_many(rows)
            except Exception:
                with self._journal_lock:
                    self._pending_journal[:0] = rows
                raise
        return len(rows)

    def track(self, symbol: str, price: float) -> None:
        """Fold a tick into every open watcher on this symbol."""
        keys = self._watched_by_symbol.get(symbol)
        if price <= 0 or not keys:
            return
        for key in keys:
            watcher = self._watchers[key]
            if price > watcher["high"]:
                watcher["high"] = price
                watcher["dirty"] = True
            if price < watcher["low"]:
                watcher["low"] = price
                watcher["dirty"] = True

    def flush_watchers(self, now: datetime | None = None) -> int:
        """Write excursions back; finalise and drop the expired ones."""
        self.flush_journal()
        stamp = (now or datetime.now(UTC)).isoformat()
        updates: list[tuple] = []
        done: list[str] = []
        for key, watcher in self._watchers.items():
            expired = watcher["until"] is not None and watcher["until"] <= stamp
            if not watcher["dirty"] and not expired:
                continue
            entry = watcher["entry"] or 0.0
            mfe = ((watcher["high"] / entry - 1.0) * 100.0) if entry > 0 else 0.0
            mae = ((watcher["low"] / entry - 1.0) * 100.0) if entry > 0 else 0.0
            updates.append((key, watcher["high"], watcher["low"], mfe, mae, 1 if expired else 0))
            watcher["dirty"] = False
            if expired:
                done.append(key)
        if updates:
            self.repository.update_blast_journal_excursions(updates)
        for key in done:
            watcher = self._watchers.pop(key, None)
            if watcher:
                bucket = self._watched_by_symbol.get(watcher["symbol"])
                if bucket is not None:
                    bucket.discard(key)
                    if not bucket:
                        self._watched_by_symbol.pop(watcher["symbol"], None)
        return len(updates)

    def journal_rows(self, *, day: str | None = None, taken_only: bool = False,
                     reason: str | None = None, limit: int = 500) -> list[dict]:
        self.flush_journal()
        return self.repository.blast_journal(day=day, taken_only=taken_only, reason=reason, limit=limit)

    def journal_days(self, limit: int = 30) -> list[str]:
        self.flush_journal()
        return self.repository.blast_journal_days(limit)

    def journal_summary(self, day: str | None = None) -> dict:
        self.flush_journal()
        return self.repository.blast_journal_summary(day=day)

    # -------------------------------------------------------------- runtime

    async def on_tick(self, symbol: str, price: float, timestamp: datetime | None = None,
                      *, session_open: bool = True) -> None:
        self.track(symbol, price)
        if not self.settings.blast_enabled:
            return
        if session_open:
            await self.execution.on_tick(symbol, price, timestamp)
        else:
            # Off-session re-broadcasts of the previous close are a valid mark
            # but never a valid trigger, exactly as on the MACD lane.
            self.execution.set_quote(symbol, price, timestamp)
            self.portfolio.mark(symbol, price)

    def mark_from(self, quotes: dict) -> int:
        """Value held positions from quotes the engine already has.

        The engine's startup refresh REST-quotes its own two books; this lane
        takes whatever that call and the tick stream have already put in
        memory and requests nothing for itself. A holding with no such quote
        keeps its last mark until its first websocket tick.
        """
        marked = 0
        for symbol in list(self.portfolio.positions):
            price = getattr(quotes.get(symbol), "ltp", None)
            if price and price > 0:
                self.portfolio.mark(symbol, price)
                marked += 1
        return marked

    def expiring_positions(self, today: date) -> list[str]:
        """Held symbols whose OWN recorded expiry is today or already past."""
        expiring = []
        for symbol, position in self.portfolio.positions.items():
            if position.quantity <= 0:
                continue
            raw = (self._contracts.get(symbol) or {}).get("expiry")
            try:
                expiry = date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue
            if expiry <= today:
                expiring.append(symbol)
        return expiring

    async def exit_if_expiring(self, symbol: str, now: datetime) -> bool:
        """Flatten an expiring holding from 15:20 IST, as the MACD lane does."""
        if symbol not in self.portfolio.positions:
            return False
        local = now.astimezone(IST)
        if (local.hour, local.minute) < (15, 20) or symbol not in self.expiring_positions(local.date()):
            return False
        return await self.flatten(symbol, "EXPIRY_EXIT_15_20_IST")

    async def flatten(self, symbol: str, reason: str) -> bool:
        position = self.portfolio.positions.get(symbol)
        if not position or position.quantity <= 0:
            return False
        self.execution.arm_exit(symbol, reason)
        try:
            await self.execution.submit(symbol, "SELL", position.quantity)
        except (ValueError, RuntimeError, PermissionError) as exc:
            self.error = f"flatten {symbol}: {exc}"
            return False
        return True

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        risk = blast_execution_settings(settings)
        capital_delta = settings.blast_initial_capital - self.portfolio.initial_capital
        if capital_delta:
            self.portfolio.initial_capital = settings.blast_initial_capital
            self.portfolio.cash += capital_delta
        self.execution.settings = risk
        for position in self.portfolio.positions.values():
            position.hard_stop = round(position.average_price * (1 - risk.hard_stop_pct), 4)

    def settings_view(self) -> dict:
        """The editable settings, keyed exactly as PUT /api/blast/settings accepts them."""
        current = self.settings
        return {
            "enabled": current.blast_enabled,
            "auto_trade": current.blast_auto_trade,
            "initial_capital": current.blast_initial_capital,
            "max_positions": current.blast_max_positions,
            "target_notional": current.blast_target_notional,
            "max_premium_pct": current.blast_max_premium_pct,
            "min_breadth": current.blast_min_breadth,
            "min_off_high_pct": current.blast_min_off_high_pct,
            "hard_stop_pct": current.blast_hard_stop_pct,
            "trail_activation_pct": current.blast_trail_activation_pct,
            "trail_pct": current.blast_trail_pct,
        }

    def health(self) -> dict:
        return {
            "enabled": self.settings.blast_enabled,
            "auto_trade": self.settings.blast_auto_trade,
            "evaluated_since_start": self.evaluated,
            "taken_since_start": self.taken,
            "watching": len(self._watchers),
            "open_positions": len(self.portfolio.positions),
            "symbols_with_history": len(self._highs),
            "bar_seconds": BAR_SECONDS,
            "warmed_symbols": self.warmed_symbols,
            "last_warm_at": self.last_warm_at,
            "late_bars_skipped": self.late_bars_skipped,
            "last_candidate_at": self.last_candidate_at,
            "broker_access": "orders only",
            "error": self.error,
        }

    def snapshot(self) -> dict:
        return json_value({
            "lane": LANE,
            "enabled": self.settings.blast_enabled,
            "auto_trade": self.settings.blast_auto_trade,
            "settings": self.settings_view(),
            "health": self.health(),
            "screen": {
                "entry": "MACD(12,26,9) crosses its signal line on a closed one-minute premium bar (zero-cross NOT required)",
                "bar_seconds": BAR_SECONDS,
                "max_premium_pct_of_spot": self.settings.blast_max_premium_pct,
                "min_side_breadth": self.settings.blast_min_breadth,
                "min_off_recent_high_pct": self.settings.blast_min_off_high_pct,
                "recent_high_lookback_bars": self.lookback_bars,
                "max_volume_share_pct": self.settings.blast_max_volume_share_pct,
                "journal_horizon_hours": self.settings.blast_journal_horizon_hours,
            },
            "risk": {
                "hard_stop_pct": self.settings.blast_hard_stop_pct * 100,
                "trailing_activation_profit_pct": self.settings.blast_trail_activation_pct * 100,
                "trailing_stop_pct": self.settings.blast_trail_pct * 100,
                "scale_out_profit_pct": [],
                "scale_in_profit_pct": [],
                "target_position_notional": self.settings.blast_target_notional,
                "max_positions": self.settings.blast_max_positions,
                "last_exit_reasons": self.execution.exit_reasons,
            },
            "counters": {
                "evaluated": self.evaluated,
                "taken": self.taken,
                "verdicts": self.rejections,
                "watching": len(self._watchers),
                "symbols_with_history": len(self._highs),
            },
            "portfolio": self.execution.portfolio_snapshot(),
            "closed_positions": self.execution.visible_closed_positions(),
            # Orders, trades, signals and the journal itself are deliberately
            # NOT in here. This frame is re-sent to every client on connect and
            # on every resync, and the other lane's copy of those three lists
            # is what once made the snapshot a 24 MB message. /api/blast/book
            # and /api/blast/journal serve them on demand instead.
            "journal_summary": self.journal_summary(datetime.now(IST).date().isoformat()),
            "error": self.error,
        })

    def sweep_closed_positions(self) -> bool:
        return self.execution.sweep_closed_positions()

    async def stop(self) -> None:
        with suppress(Exception):
            self.flush_journal()
        with suppress(Exception):
            self.flush_watchers()
            self.repository.save_position_excursions(self.portfolio.positions.values())
        self.repository.close()


def read_stored_minute_bars(database_path: str, since: dict[str, int], limit: int) -> dict[str, list[Candle]]:
    """The newest ``limit`` stored one-minute bars per symbol, newer than ``since``.

    Read-only against the local history database; a missing file or table is
    simply no history.
    """
    result: dict[str, list[Candle]] = {}
    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error:
        return result
    try:
        for symbol, after in since.items():
            try:
                rows = connection.execute(
                    "SELECT timestamp, open, high, low, close, volume FROM historical_candles"
                    " WHERE symbol=? AND timeframe_seconds=? AND timestamp>?"
                    " ORDER BY timestamp DESC LIMIT ?",
                    (symbol, BAR_SECONDS, after, limit),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    return result
                raise
            if rows:
                result[symbol] = [
                    Candle(symbol, int(ts), float(o), float(h), float(low), float(c), int(v or 0), True)
                    for ts, o, h, low, c, v in reversed(rows)
                ]
    finally:
        connection.close()
    return result
