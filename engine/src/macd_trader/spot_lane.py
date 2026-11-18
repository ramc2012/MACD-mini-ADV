"""Spot MACD lane: a positional paper book of stock futures, long and short, on the stock's own 30-min MACD.

Owner, 3 Nov 2026: "implement a positional trading system with macd rules but to trade spot with ticket size of
1L"; later that day: "start with 50L capital. hold time 3-5 days"; 4 Nov 2026: "it is long and short ... in futures
for both long and shorts with exchange specfied lot size".

Entries. The cash stock's 30-minute MACD(12,26,9) line crossing zero, intrabar or on the close (the MACD lane's own
zero-cross rule, read on the stock): crossing up opens a LONG, crossing down a SHORT. The trade is the stock's
future -- spot_lane_lots exchange lots (the Fyers master's lot size) of the nearest series that expires after the
hold limit -- because a cash short cannot be carried overnight. One position per stock; an opposite cross while
held does not reverse it (the position runs its own exits). Nothing is skipped on a score; free margin, session
and a fresh futures quote are the only gates.

Capacity is margin, not notional: a position blocks spot_futures_margin_pct of its notional (an estimate of SPAN +
exposure, not the exchange's figure), and a new one needs that much free margin (equity less margin in use).

Exits are the MACD lane's option ladder scaled by 1/10, mirrored for shorts (research/spot_macd_20261103, OPT_10):
- hard stop 3% against the entry;
- a third out at 3%, 5% and 7.5% in favour, in whole lots -- a position of fewer lots than stages has no staged
  exits (one lot cannot be split) and rides the trail instead;
- a 2.5% trail once the best is 3% in favour, never worse than breakeven;
- MACD closing a bar back across zero against the position while the best was under 1% (the MACD lane's
  invalidation, scaled).
Held 3 to 5 sessions: until 15:25 on the 3rd session after entry only the hard stop can close a position (the trail
keeps tracking the best price but does not fire; thirds and the MACD exit wait), and it is out at 15:25 on the 5th.
Paper fills ignore futures charges.

The research found no edge over random entries for long-only spot MACD; the short side was not studied. This lane
measures both forward. Not proven.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from . import sessions
from .execution import ExecutionManager, ExitRetryWait
from .models import Signal
from .portfolio import breakeven_exit_price
from .rollover import to_date
from .smart_lane import (
    ALREADY_HELD, ENTRY_REJECTED, IST, MACD_SOURCE, NO_CASH, NO_PRICE, NOT_TRADABLE, SESSION_CLOSED, STALE,
    STALE_SECONDS, TAKEN, WAITING_PRICE, Pending, SmartLane, hold_until,
)

LANE_NAME = "spot_macd"
LONG, SHORT = "LONG", "SHORT"
FIRED_MEMORY = 5_000          # (stock, bar, side) crosses remembered so a bar fires once per side


def staged_levels(text: str) -> tuple[float, ...]:
    """'0.03,0.05,0.075' -> (0.03, 0.05, 0.075); malformed or non-positive parts are dropped."""
    out = []
    for part in str(text or "").split(","):
        try:
            value = float(part)
        except ValueError:
            continue
        if math.isfinite(value) and value > 0:
            out.append(value)
    return tuple(sorted(out))


def pct_label(fraction: float) -> str:
    return f"{fraction * 100:g}"


def is_spot(symbol: str) -> bool:
    return sessions.segment(symbol) == "cash"


def cash_root(symbol: str) -> tuple[str, str] | None:
    """'NSE:SBIN-EQ' -> ('NSE', 'SBIN'); None for anything but a cash symbol."""
    exchange, _, body = symbol.upper().partition(":")
    for suffix in ("-EQ", "-BE"):
        if body.endswith(suffix) and exchange:
            return exchange, body[: -len(suffix)]
    return None


def macd_cross(previous: float | None, macd: float | None) -> str | None:
    """'BUY' when MACD crosses up through zero, 'SELL' when it crosses down, else None."""
    if previous is None or macd is None:
        return None
    if previous <= 0 < macd:
        return "BUY"
    if previous >= 0 > macd:
        return "SELL"
    return None


@dataclass(frozen=True, slots=True)
class FutureLeg:
    """The futures contract a stock is traded in today."""

    symbol: str        # NSE:SBIN26NOVFUT
    underlying: str    # NSE:SBIN-EQ
    expiry: str        # ISO date
    lot_size: int


def pick_series(series, deadline: date):
    """The nearest listed series (FuturesSeries-like rows) expiring after ``deadline`` with a usable lot size."""
    rows = sorted((row for row in series if int(getattr(row, "lot_size", 0) or 0) > 0
                   and (to_date(getattr(row, "expiry", None)) or date.min) > deadline),
                  key=lambda row: str(row.expiry))
    return rows[0] if rows else None


def direction(position) -> int:
    return 1 if position.quantity > 0 else -1


def short_breakeven(average_price: float, slippage_bps: float) -> float:
    """Price at which buying a short back nets zero: the cover pays slippage on the way out."""
    return average_price / (1 + max(0.0, slippage_bps) / 10_000)


class SpotExecutionManager(ExecutionManager):
    """Paper futures fills from the shared manager, both sides; the scaled MACD ladder for exits; no adds."""

    def __init__(self, *args, lane: "SpotLane", **kwargs):
        super().__init__(*args, **kwargs)
        self.lane = lane

    def exit_rules(self, _source: str = MACD_SOURCE) -> dict:
        s = self.lane.settings
        return {"hard": s.spot_hard_stop_pct, "act": s.spot_trail_activation_pct, "trail": s.spot_trail_pct,
                "target": None, "staged": staged_levels(s.spot_staged_exits_csv), "hold": s.spot_max_hold_sessions,
                "min_hold": min(s.spot_min_hold_sessions, s.spot_max_hold_sessions or s.spot_min_hold_sessions),
                "invalidate_below_mfe": s.spot_invalidation_max_mfe_pct}

    def settled(self, position, now: datetime | None = None) -> bool:
        """Past the minimum hold: the whole ladder may act (before it, only the hard stop)."""
        sessions_ = self.exit_rules()["min_hold"]
        if not sessions_:
            return True
        now = (now or datetime.now(UTC)).astimezone(IST)
        return now >= hold_until(position.opened_at, sessions_)

    def protect(self, position) -> None:
        """Hard stop against the entry, the favourable extreme, and the trail once it has armed."""
        rules = self.exit_rules()
        d = direction(position)
        position.hard_stop = round(position.average_price * (1 - d * rules["hard"]), 4)
        best = position.peak_price if position.peak_price > 0 else position.average_price
        if d * (best / position.average_price - 1) >= rules["act"]:
            if d > 0:
                floor = breakeven_exit_price(position.average_price, position.quantity, self.settings.slippage_bps)
                trail = round(max(best * (1 - rules["trail"]), floor), 4)
                position.trailing_stop = max(position.trailing_stop or trail, trail)
            else:
                floor = short_breakeven(position.average_price, self.settings.slippage_bps)
                trail = round(min(best * (1 + rules["trail"]), floor), 4)
                position.trailing_stop = min(position.trailing_stop or trail, trail)

    # ------------------------------------------------------------- orders

    async def submit(self, symbol: str, side: str, quantity: int | None = None, *, lots: int | None = None,
                     order_type: str = "MARKET", limit_price: float | None = None, signal: Signal | None = None,
                     _exit_stage: int | None = None):
        """The shared submit, with a short's BUY-to-cover treated as the exit it is (back-off, held-symbol rules)."""
        position = self.portfolio.positions.get(symbol)
        closing = position is not None and position.quantity != 0 and (side == "SELL") == (position.quantity > 0)
        automated = closing and symbol in self._armed_exits
        if automated and time.monotonic() < self._exit_blocked_until.get(symbol, 0.0):
            self._armed_exits.discard(symbol)
            raise ExitRetryWait(f"{symbol}: exit backing off after {self.exit_failures[symbol]['count']} rejection(s)")
        try:
            order = await self._submit(symbol, side, quantity, position=position, held_exit=closing, lots=lots,
                                       order_type=order_type, limit_price=limit_price, signal=signal,
                                       _exit_stage=_exit_stage)
        except Exception as exc:
            if automated:
                self._exit_failed(symbol, exc)
            raise
        if closing and side == "BUY":
            self._armed_exits.discard(symbol)
            if order.status == "FILLED":
                self._exit_blocked_until.pop(symbol, None)
                self.exit_failures.pop(symbol, None)
        return order

    def _check_paper_capacity(self, order, price: float) -> None:
        """Futures, not cash: an exit is bounded by the position; an entry needs free margin, one per contract."""
        position = self.portfolio.positions.get(order.symbol)
        held = position.quantity if position else 0
        closing = held != 0 and (order.side == "SELL") == (held > 0)
        resting = sum(item.quantity for item in self.orders.values()
                      if item.status == "OPEN" and item.order_id != order.order_id
                      and item.symbol == order.symbol and item.side == order.side)
        if closing:
            if order.quantity > abs(held) - resting:
                raise ValueError("Exit quantity exceeds the open position")
            return
        if held:
            raise ValueError(f"{order.symbol} is already held; one position per stock, no adds")
        needed = price * order.quantity * self.lane.margin_rate()
        if needed > self.lane.free_margin() + 1e-6:
            raise ValueError("Insufficient free margin")

    async def _paper_fill(self, order, *, exit_stage: int | None = None) -> None:
        """The shared fill; then a short gets the short's stop and keeps its best price through partial covers."""
        before = self.portfolio.positions.get(order.symbol)
        kept = ((before.peak_price, before.entry_stage, before.entry_anchor)
                if before is not None and before.quantity < 0 else None)
        await super()._paper_fill(order, exit_stage=exit_stage)
        after = self.portfolio.positions.get(order.symbol)
        if order.status != "FILLED" or after is None or after.quantity >= 0:
            return
        if kept is not None:
            after.peak_price, after.entry_stage, after.entry_anchor = kept
        else:
            after.peak_price = after.entry_anchor = after.average_price
            after.entry_stage = order.lots
            after.exit_stage = exit_stage or 0
            after.trailing_stop = None
        self.protect(after)

    # ------------------------------------------------------------- exits

    async def on_closed_bar(self, symbol: str, macd: float) -> bool:
        """MACD closing back across zero against the position while it never worked. ``symbol`` is the stock: the
        MACD is read on the stock, the position is its future. Waits for the minimum hold."""
        future = self.lane.held_future(symbol)
        position = self.portfolio.positions.get(future) if future else None
        if position is None or position.quantity == 0 or not self.settings.macd_invalidation_exit:
            return False
        d = direction(position)
        if d * macd >= 0 or not self.settled(position):
            return False
        if position.max_return_pct >= self.settings.macd_invalidation_max_mfe_pct * 100:
            return False
        self.arm_exit(future, "MACD_INVALIDATED")
        try:
            await self.submit(future, "SELL" if d > 0 else "BUY", abs(position.quantity))
        except (ValueError, RuntimeError, PermissionError):
            self._armed_exits.discard(future)
            self.exit_reasons.pop(future, None)
            return False
        return True

    async def on_tick(self, symbol: str, price: float, timestamp: datetime | None = None) -> None:
        self.set_quote(symbol, price, timestamp)
        if symbol not in self.portfolio.positions:
            return                 # every tick on the feed passes here; entries are market orders, never resting
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
        if not position or position.quantity == 0:
            return
        rules = self.exit_rules()
        d = direction(position)
        best = position.peak_price if position.peak_price > 0 else position.average_price
        position.peak_price = max(best, price) if d > 0 else min(best, price)
        self.protect(position)
        against = (lambda level: price <= level) if d > 0 else (lambda level: price >= level)
        now = (timestamp or datetime.now(UTC)).astimezone(IST)
        settled = self.settled(position, now)
        exit_side = "SELL" if d > 0 else "BUY"
        reason = None
        if against(position.hard_stop):
            reason = f"HARD_STOP_{pct_label(rules['hard'])}_PCT"
        elif settled and position.trailing_stop and against(position.trailing_stop):
            reason = f"TRAILING_STOP_{pct_label(rules['trail'])}_PCT"
        elif rules["hold"] and now >= hold_until(position.opened_at, rules["hold"]):
            reason = f"HOLD_LIMIT_{rules['hold']}_SESSIONS"
        if reason:
            self.arm_exit(symbol, reason)
            await self.submit(symbol, exit_side, abs(position.quantity))
            return
        stages = rules["staged"]
        part = max(position.entry_stage, position.lots) // len(stages) if stages else 0
        if (settled and part >= 1 and position.exit_stage < len(stages)
                and d * (price / position.average_price - 1) >= stages[position.exit_stage] - 1e-9):
            stage = position.exit_stage + 1
            # Thirds of the lots entered, whole lots only; the last stage takes what is left.
            lots = position.lots if stage == len(stages) or part >= position.lots else part
            self.arm_exit(symbol, f"STAGED_PROFIT_EXIT_{stage}")
            await self.submit(symbol, exit_side, lots=lots, _exit_stage=stage)

    async def on_signal(self, signal: Signal) -> None:
        return None   # entries come from SpotLane, which owns the rules


class SpotLane(SmartLane):
    """One paper book of stock futures. Shares SmartLane's journal, restore, trades, summary and equity views."""

    kind = "spot"
    execution_class = SpotExecutionManager

    def __init__(self, settings, broker, events, database_path: str, name: str = LANE_NAME):
        self.futures: dict[str, FutureLeg] = {}
        super().__init__(name, settings.spot_lane_capital, settings, broker, events, database_path)
        self._task: asyncio.Task | None = None
        self._fired: dict[tuple[str, int | None, str], None] = {}
        self.futures_error: str | None = None
        self.futures_resolved_at: str | None = None
        self._sync_universe()

    def execution_settings(self, settings):
        """ExecutionManager's fields for this lane: lot entries sized by the lane, no pyramid, no position cap."""
        return settings.model_copy(update={
            "execution_mode": "paper",
            "allow_live_orders": False,
            "initial_capital": self.capital,
            "hard_stop_pct": settings.spot_hard_stop_pct,
            "trailing_activation_pct": settings.spot_trail_activation_pct,
            "trailing_stop_pct": settings.spot_trail_pct,
            "target_position_notional": 1.0,      # entries carry a signal: the lane sizes them itself
            "max_target_entry_lots": 1_000_000,
            # A short's cover is a BUY against a held position: the shared lot cap must not read it as an add.
            "max_trade_lots": 1_000_000,
            "max_positions": 0,
            "min_cash_reserve": 0.0,
            "auto_trade": True,
            "macd_invalidation_exit": True,
            "macd_invalidation_max_mfe_pct": settings.spot_invalidation_max_mfe_pct,
        })

    def apply_settings(self, settings) -> None:
        super().apply_settings(settings)
        self._sync_universe()

    # ----------------------------------------------------------------- margin

    def margin_rate(self) -> float:
        return float(self.settings.spot_futures_margin_pct)

    def exposure(self) -> float:
        return sum(abs(p.quantity) * p.last_price for p in self.portfolio.positions.values())

    def margin_used(self) -> float:
        return self.exposure() * self.margin_rate()

    def free_margin(self) -> float:
        return self.portfolio.snapshot()["equity"] - self.margin_used()

    # ----------------------------------------------------------------- wiring

    def held_symbols(self) -> list[str]:
        return [symbol for symbol, position in self.portfolio.positions.items() if position.quantity != 0]

    def feed_symbols(self) -> list[str]:
        """Every stock's chosen future (entries need a fresh futures quote) plus what is held."""
        return list(dict.fromkeys([*(leg.symbol for leg in self.futures.values()), *self.held_symbols()]))

    def underlying_of(self, future: str) -> str | None:
        meta = self.meta.get(future) or {}
        if meta.get("spot_symbol"):
            return meta["spot_symbol"]
        return next((leg.underlying for leg in self.futures.values() if leg.symbol == future), None)

    def held_future(self, stock: str) -> str | None:
        return next((symbol for symbol in self.held_symbols() if self.underlying_of(symbol) == stock), None)

    async def resolve_futures(self, broker, now: datetime | None = None) -> dict[str, FutureLeg]:
        """Pick each stock's series from the Fyers master: the nearest one expiring after the hold limit.

        ``broker`` is the engine's market-data broker; the lane's own is order-only."""
        now = (now or datetime.now(UTC)).astimezone(IST)
        stocks = sorted({symbol for symbol in self.settings.symbols if is_spot(symbol)})
        wanted = {f"{root[0]}:{root[1]}-FUT": stock for stock in stocks if (root := cash_root(stock))}
        try:
            listed = await broker.futures_series(list(wanted))
        except Exception as exc:  # noqa: BLE001 -- keep yesterday's choice rather than trade nothing
            self.futures_error = f"futures series lookup failed: {type(exc).__name__}: {exc}"[:300]
            return self.futures
        deadline = hold_until(now, max(1, int(self.settings.spot_max_hold_sessions or 1))).date()
        legs: dict[str, FutureLeg] = {}
        for request, stock in wanted.items():
            row = pick_series(listed.get(request) or [], deadline)
            if row is not None:
                legs[stock] = FutureLeg(str(row.symbol), stock, str(row.expiry)[:10], int(row.lot_size))
        missing = len(wanted) - len(legs)
        self.futures_error = (f"{missing} of {len(wanted)} stocks have no futures series after "
                              f"{deadline.isoformat()}" if missing else None)
        if legs or not self.futures:
            self.futures = legs
        self.futures_resolved_at = now.isoformat(timespec="seconds")
        self._sync_universe()
        return self.futures

    def _sync_universe(self) -> None:
        """Today's futures at their exchange lots; held ones stay exitable if the choice has moved on."""
        futures = getattr(self, "futures", {})
        lots = {leg.symbol: leg.lot_size for leg in futures.values()}
        lots |= {symbol: position.lot_size for symbol, position in self.portfolio.positions.items()
                 if position.quantity != 0 and position.lot_size > 0}
        self.execution.set_tradable_contracts(lots)
        self.execution.set_contract_expiries({leg.symbol: leg.expiry for leg in futures.values()})

    def set_tradable_contracts(self, lot_sizes: dict[str, int]) -> None:
        self._sync_universe()      # the engine passes today's option lots; this book trades futures

    def set_contract_context(self, contracts: dict[str, dict]) -> None:
        return None

    def on_macd_signal(self, signal: Signal, option_type: str, features: dict | None,
                       now: datetime | None = None) -> None:
        return None                # option signals belong to the option lanes

    def on_blast_candidate(self, row: dict, now: datetime | None = None) -> None:
        return None

    # ------------------------------------------------------------------ intake

    def on_macd(self, symbol: str, previous: float | None, macd: float | None, price: float, bucket: int | None,
                *, intrabar: bool) -> Signal | None:
        """A stock's MACD moved (forming bar or close): a zero cross becomes this lane's signal, once per bar and side."""
        if not is_spot(symbol):
            return None
        side = macd_cross(previous, macd)
        if side is None or (side == "SELL" and not self.settings.spot_short_enabled):
            return None
        key = (symbol, bucket, side)
        if key in self._fired:
            return None
        self._fired[key] = None
        while len(self._fired) > FIRED_MEMORY:
            del self._fired[next(iter(self._fired))]
        kind = f"SPOT_MACD_{'UP' if side == 'BUY' else 'DOWN'}{'_INTRABAR' if intrabar else ''}"
        return Signal(symbol, side, kind, float(price), float(macd), 0.0, 0.0, evaluated_candle_timestamp=bucket)

    def admit(self, signal: Signal, now: datetime | None = None) -> None:
        """Queue a cross for the next batch: bought or sold as soon as a fresh futures quote is in hand."""
        now = now or datetime.now(UTC)
        side = LONG if signal.side == "BUY" else SHORT
        if any(p.symbol == signal.symbol and p.option_type == side and
               (p.signal.signal_id == signal.signal_id or p.bucket is not None and p.bucket == signal.evaluated_candle_timestamp)
               for p in self._pending):
            return
        self._pending.append(Pending(MACD_SOURCE, signal.symbol, side, signal, due=now, created=now,
                                     bucket=signal.evaluated_candle_timestamp, detail={"kind": signal.kind, "side": side}))

    def cutoff(self) -> float:
        return 0.0                 # no score, no cut-off

    # --------------------------------------------------------------- deciding

    async def step(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        due = [p for p in self._pending if p.due <= now and (p.retry_at is None or p.retry_at <= now)]
        if not due:
            return 0
        self._pending = [p for p in self._pending if p not in due]
        for item in sorted(due, key=lambda p: (p.created, p.symbol)):
            item.retry_at = None
            try:
                await self._decide(item, now, 0.0)
                if item.retry_at is not None:
                    self._pending.append(item)
            except Exception as exc:  # noqa: BLE001 -- one bad entry must not stop the batch
                self.error = f"{item.symbol}: {type(exc).__name__}: {exc}"[:300]
                self._record(item, ENTRY_REJECTED, now, detail={"error": str(exc)[:200]})
        self.last_batch_at = now.isoformat()
        return len(due)

    def _estimated_fill(self, symbol: str, side: str, price: float) -> float:
        """The quote-side price the paper fill will take (execution.estimate_fill, S1)."""
        return self.execution.estimate_fill(symbol, side, price)

    async def _decide(self, item: Pending, now: datetime, cut: float) -> None:
        stock, side = item.symbol, item.option_type
        execution = self.execution
        leg = self.futures.get(stock)
        if leg is None:
            return self._record(item, NOT_TRADABLE, now, detail={"reason": "No futures series chosen for this stock"})
        future = leg.symbol
        item.detail.update({"future": future, "lot_size": leg.lot_size, "expiry": leg.expiry})
        if self.held_future(stock) or future in self.portfolio.positions or any(
                order.symbol == future and order.status == "OPEN" for order in execution.orders.values()):
            return self._record(item, ALREADY_HELD, now)
        if (now - item.due).total_seconds() > STALE_SECONDS:
            return self._record(item, NO_PRICE if item.detail.get("quote_retries") else STALE, now,
                                detail={"reason": "Admission deadline elapsed; no stale fill"})
        if not sessions.in_session(future, now):
            return self._record(item, SESSION_CLOSED, now)
        if future not in execution.tradable_symbols:
            return self._record(item, NOT_TRADABLE, now)
        try:
            price = execution._fresh_price(future)
        except RuntimeError:
            item.retry_at = now + timedelta(seconds=5)
            item.detail["quote_retries"] = item.detail.get("quote_retries", 0) + 1
            if item.detail["quote_retries"] == 1:
                self._record(item, WAITING_PRICE, now, detail={"reason": "Waiting for a fresh futures quote"})
            return
        order_side = "BUY" if side == LONG else "SELL"
        lots = max(1, int(self.settings.spot_lane_lots))
        estimated = self._estimated_fill(future, order_side, price)
        notional = estimated * lots * leg.lot_size
        margin = notional * self.margin_rate()
        free = self.free_margin()
        item.detail.update({"estimated_fill": round(estimated, 2), "notional": round(notional, 2),
                            "margin": round(margin, 2)})
        if margin > free:
            return self._record(item, NO_CASH, now, price=price, ticket=notional,
                                detail={"free_margin": round(free, 2)})
        try:
            order = await execution.submit(future, order_side, lots=lots, signal=item.signal)
        except (ValueError, RuntimeError, PermissionError) as exc:
            return self._record(item, ENTRY_REJECTED, now, price=price, ticket=notional, detail={"error": str(exc)[:200]})
        if order.status != "FILLED":
            return self._record(item, ENTRY_REJECTED, now, price=price, ticket=notional, detail={"status": order.status})
        journal_id = self._record(item, TAKEN, now, price=order.fill_price, size=1.0, ticket=notional,
                                  lots=lots, order_id=order.order_id)
        position = self.portfolio.positions.get(future)
        meta = {"position_id": position.position_id if position else f"{future}|{now.isoformat()}",
                "symbol": future, "source": MACD_SOURCE, "opened_at": now.isoformat(), "expiry": leg.expiry,
                "strike": None, "option_type": side, "spot_symbol": stock, "score": None, "dispersion": 1.0,
                "journal_id": journal_id}
        self.meta[future] = meta
        with self._db_lock, self._db:
            self._db.execute(
                """INSERT OR REPLACE INTO smart_positions (position_id, symbol, source, opened_at, expiry, strike,
                   option_type, spot_symbol, score, dispersion, journal_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(meta[k] for k in ("position_id", "symbol", "source", "opened_at", "expiry", "strike",
                                        "option_type", "spot_symbol", "score", "dispersion", "journal_id")))

    # ----------------------------------------------------------------- restore

    def _restore(self) -> None:
        """The shared replay; shorts then get the short's stop, best price and trail (the replay assumes longs)."""
        super()._restore()
        for position in self.portfolio.positions.values():
            if position.quantity >= 0:
                continue
            position.peak_price = min(position.average_price, position.min_price or position.average_price)
            position.trailing_stop = None
            self.execution.protect(position)

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await asyncio.to_thread(self.flush_journal)

    # ------------------------------------------------------------------ views

    def snapshot(self) -> dict:
        out = super().snapshot()
        rate = self.margin_rate()
        for row in out["portfolio"]["positions"]:
            row["side"] = LONG if row["quantity"] > 0 else SHORT
            row["underlying"] = self.underlying_of(row["symbol"])
            row["notional"] = round(abs(row["quantity"]) * row["last_price"], 2)
            row["margin"] = round(row["notional"] * rate, 2)
        return out

    def health(self) -> dict:
        snap = self.portfolio.snapshot()
        rules = self.execution.exit_rules()
        held = [self.portfolio.positions[s] for s in self.held_symbols()]
        used, free = self.margin_used(), snap["equity"] - self.margin_used()
        return {"lane": self.name, "kind": self.kind, "capital": self.capital, "pending": len(self._pending),
                "counts": dict(self.counts), "recent": self.recent[-12:], "error": self.error,
                "last_batch_at": self.last_batch_at, "open_positions": len(held),
                "long_positions": sum(p.quantity > 0 for p in held), "short_positions": sum(p.quantity < 0 for p in held),
                "cash": snap["cash"], "equity": snap["equity"], "exposure": self.exposure(),
                "margin_used": used, "free_margin": free, "deployed": used,
                "book_full": free <= 0,
                "futures": {"chosen": len(self.futures), "resolved_at": self.futures_resolved_at,
                            "error": self.futures_error,
                            "expiries": sorted({leg.expiry for leg in self.futures.values()})},
                "rules": {
                    "entry": "The stock's 30-min MACD(12,26,9) line crossing zero (intrabar or close): up opens a long, "
                             "down a short" + ("" if self.settings.spot_short_enabled else " (shorts off)"),
                    "instrument": "the stock's future: the nearest series expiring after the hold limit",
                    "lots": int(self.settings.spot_lane_lots),
                    "margin_pct": self.margin_rate(),
                    "sizing": f"{int(self.settings.spot_lane_lots)} exchange lot(s) per signal; needs free margin "
                              f"(≈{pct_label(self.margin_rate())}% of notional); no score cut-off",
                    "hard_stop_pct": rules["hard"], "trail_activation_pct": rules["act"], "trail_pct": rules["trail"],
                    "staged_exits": list(rules["staged"]), "invalidate_below_mfe_pct": rules["invalidate_below_mfe"],
                    "min_hold_sessions": rules["min_hold"], "max_hold_sessions": rules["hold"],
                    "long_only": not self.settings.spot_short_enabled, "overnight": True,
                },
                "validation": "research/spot_macd_20261103: long-only spot MACD ≈ market, not proven; "
                              "the short side was not studied"}
