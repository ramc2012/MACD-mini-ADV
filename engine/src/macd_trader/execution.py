from __future__ import annotations

import asyncio
from copy import deepcopy
import math
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from .brokers import Broker
from .config import Settings
from .events import EventHub, json_value
from .models import Order, Signal, Trade

IST = ZoneInfo("Asia/Kolkata")


def _session_day(moment: datetime) -> date:
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(IST).date()
from .portfolio import Portfolio, breakeven_exit_price
from .repository import TradeRepository


class ExecutionManager:
    def __init__(
        self,
        settings: Settings,
        broker: Broker,
        portfolio: Portfolio,
        repository: TradeRepository,
        events: EventHub,
    ):
        self.settings = settings
        self.broker = broker
        self.portfolio = portfolio
        self.repository = repository
        self.events = events
        self.orders: dict[str, Order] = {}
        if settings.execution_mode == "paper":
            self.orders = {order.order_id: order for order in repository.load_open_orders()}
        self.last_prices: dict[str, float] = {}
        self.last_price_at: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self.tradable_symbols: set[str] = set(settings.symbols)
        self.lot_sizes: dict[str, int] = {symbol: 1 for symbol in settings.symbols}
        self.exit_reasons: dict[str, str] = {}
        # Symbols whose next SELL was armed by an automated exit. A SELL that
        # arrives without one is a manual ticket and is labelled MANUAL, so a
        # stale HARD_STOP from a previous position can no longer be stamped
        # on to a hand-closed trade. exit_reasons itself is never popped: the
        # desk shows the last reason per symbol after the position is gone.
        self._armed_exits: set[str] = set()
        self.closed_positions: list[dict] = repository.closed_positions(
            lane=portfolio.lane, visible_after=datetime.now(UTC),
        )

    def arm_exit(self, symbol: str, reason: str) -> None:
        self.exit_reasons[symbol] = reason
        self._armed_exits.add(symbol)

    def visible_closed_positions(self, now: datetime | None = None) -> list[dict]:
        # visible_until is stored UTC-normalised, so a string comparison is
        # exact and spares a datetime parse per row on every portfolio publish
        # (each mark change, up to 10 Hz per held symbol).
        cutoff = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        return [row for row in self.closed_positions if row["visible_until"] > cutoff]

    def portfolio_snapshot(self) -> dict:
        snapshot = self.portfolio.snapshot()
        snapshot["closed_positions"] = self.visible_closed_positions()
        return snapshot

    def sweep_closed_positions(self) -> bool:
        """Drop records past 08:00 IST on the next trading morning from the
        desk. Rows stay in the repository. True when something was hidden."""
        before = len(self.closed_positions)
        self.closed_positions = self.visible_closed_positions()
        if len(self.closed_positions) == before:
            return False
        self.events.publish("portfolio", self.portfolio_snapshot())
        return True

    def set_tradable_symbols(self, symbols: list[str]) -> None:
        self.tradable_symbols = set(symbols)
        self.lot_sizes = {symbol: 1 for symbol in symbols}

    def set_tradable_contracts(self, lot_sizes: dict[str, int]) -> None:
        self.lot_sizes = {symbol: int(size) for symbol, size in lot_sizes.items() if int(size) > 0}
        self.tradable_symbols = set(self.lot_sizes)
        # The selector has now resolved today's contract universe. Resting
        # tickets for a rolled/expired symbol or a changed lot size must not
        # spring back to life just because their old quote appears on a feed.
        for order in self.orders.values():
            if order.status == "OPEN" and self.lot_sizes.get(order.symbol) != order.lot_size:
                self._cancel(order)
        self.expire_previous_session_orders()

    def expire_previous_session_orders(self, today: date | None = None) -> None:
        """Resting paper tickets are day orders, as on the exchange.

        A restart resumes today's tickets; one left from an earlier session
        must not fill against a later day's prices.
        """
        today = today or datetime.now(IST).date()
        for order in self.orders.values():
            if order.status == "OPEN" and _session_day(order.created_at) < today:
                self._cancel(order)

    def _cancel(self, order: Order) -> None:
        order.status = "CANCELLED"
        self.repository.save_order(order)
        self.events.publish("order", order)

    def entry_lots(self, symbol: str, price: float) -> int:
        """Choose whole lots nearest the configured entry notional.

        The result is capped by the notional-entry safety limit and currently
        available paper cash. Manual orders and pyramiding keep their smaller
        max_trade_lots ceiling. A zero target preserves one-lot entries.
        """
        lot_size = self.lot_sizes.get(symbol, 0)
        if lot_size < 1 or price <= 0:
            return 0
        per_lot = price * lot_size * (1 + self.settings.slippage_bps / 10_000)
        affordable = int(self.portfolio.cash // per_lot) if per_lot > 0 else 0
        if affordable < 1:
            return 0
        target = self.settings.target_position_notional
        desired = 1 if target <= 0 else max(1, int(target / per_lot + 0.5))
        return min(desired, self.settings.max_target_entry_lots, affordable)

    async def submit(
        self,
        symbol: str,
        side: str,
        quantity: int | None = None,
        *,
        lots: int | None = None,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        signal: Signal | None = None,
        _exit_stage: int | None = None,
    ) -> Order:
        if symbol not in self.tradable_symbols:
            raise ValueError(f"{symbol} is not on the watchlist")
        lot_size = self.lot_sizes.get(symbol, 0)
        if lot_size < 1:
            raise ValueError(f"Lot size is unavailable for {symbol}; order blocked")
        if lots is not None:
            if lots < 1:
                raise ValueError("lots must be positive")
            quantity = lots * lot_size
        if side not in {"BUY", "SELL"} or quantity is None or quantity < 1:
            raise ValueError("side must be BUY/SELL and quantity must be positive")
        if quantity % lot_size:
            raise ValueError(f"Quantity for {symbol} must be a multiple of its {lot_size}-unit lot")
        order_lots = quantity // lot_size
        if self.settings.execution_mode == "live" and not self.settings.allow_live_orders:
            raise PermissionError("Live orders are locked; set MACD_ALLOW_LIVE_ORDERS=true explicitly")
        if self.settings.execution_mode == "live" and symbol.endswith("-INDEX"):
            raise ValueError("Index spot symbols are not tradable; configure a futures or option contract")
        order = Order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            lots=order_lots,
            lot_size=lot_size,
            order_type=order_type,
            limit_price=limit_price,
            signal_id=signal.signal_id if signal else None,
        )
        async with self._lock:
            if order_type not in {"MARKET", "LIMIT"}:
                raise ValueError("order_type must be MARKET or LIMIT")
            if order_type == "LIMIT" and (limit_price is None or not math.isfinite(limit_price) or limit_price <= 0):
                raise ValueError("LIMIT orders require a finite positive limit_price")
            existing_position = self.portfolio.positions.get(symbol)
            if side == "BUY" and existing_position and existing_position.lots + order_lots > self.settings.max_trade_lots:
                raise ValueError(f"{symbol} is capped at {self.settings.max_trade_lots} lots per trade")
            if side == "BUY" and not existing_position:
                entry_cap = self.settings.max_trade_lots
                if signal is not None and self.settings.target_position_notional > 0:
                    entry_cap = max(entry_cap, self.settings.max_target_entry_lots)
                if order_lots > entry_cap:
                    raise ValueError(f"New trades are capped at {entry_cap} lots")
            if side == "SELL":
                if symbol in self._armed_exits:
                    self._armed_exits.discard(symbol)
                    # Protective exits supersede resting tickets. Otherwise
                    # a profit-taking limit can reserve the whole holding
                    # and accidentally veto its hard stop.
                    for pending in self.orders.values():
                        if pending.symbol == symbol and pending.status == "OPEN":
                            pending.status = "CANCELLED"
                            self.repository.save_order(pending)
                            self.events.publish("order", pending)
                else:
                    self.exit_reasons[symbol] = "MANUAL"
            self.orders[order.order_id] = order
            self.repository.save_order(order)
            self.events.publish("order", order)
            try:
                if self.settings.execution_mode == "paper":
                    # A live broker may be supplying quotes/candles in paper mode.
                    # Never call its order API on this path: paper execution is
                    # entirely local and cannot leak an order to the broker.
                    order.broker_order_id = f"PAPER-{order.order_id[:12]}"
                    await self._paper_fill(order, exit_stage=_exit_stage)
                else:
                    order.broker_order_id = await self.broker.place_order(order)
                    order.status = "SUBMITTED"
            except Exception:
                order.status = "REJECTED"
                self.repository.save_order(order)
                self.events.publish("order", order)
                raise
            if order.status != "FILLED":
                self.repository.save_order(order)
                self.events.publish("order", order)
        return order

    async def _paper_fill(self, order: Order, *, exit_stage: int | None = None) -> None:
        price = self._fresh_price(order.symbol)
        slip = price * self.settings.slippage_bps / 10_000
        fill_price = price + slip if order.side == "BUY" else price - slip
        fill_price = round(fill_price, 4)
        if order.order_type == "LIMIT":
            fill_price = min(fill_price, order.limit_price) if order.side == "BUY" else max(fill_price, order.limit_price)
        self._check_paper_capacity(order, order.limit_price if order.order_type == "LIMIT" else fill_price)
        if order.order_type == "LIMIT":
            if order.side == "BUY" and price > float(order.limit_price or 0):
                order.status = "OPEN"
                return
            if order.side == "SELL" and price < float(order.limit_price or 0):
                order.status = "OPEN"
                return
        existing_position = self.portfolio.positions.get(order.symbol)
        prior_position = deepcopy(existing_position) if existing_position else None
        prior_cash, prior_realized = self.portfolio.cash, self.portfolio.realized_pnl
        prior_status, prior_fill_price = order.status, order.fill_price
        prior_entry_stage = existing_position.entry_stage if existing_position else 0
        # fees stays at its 0.0 default: the lane has no brokerage setting and
        # slippage is already folded into fill_price. If one is added, pass it
        # here AND in engine._restore_portfolio, or replay diverges from live.
        trade = Trade(
            order.order_id, order.symbol, order.side, order.quantity, fill_price,
            lots=order.lots, lot_size=order.lot_size,
        )
        try:
            order.fill_price = fill_price
            order.status = "FILLED"
            closed = self.portfolio.apply_trade(trade)
            position = self.portfolio.positions.get(order.symbol)
            if order.side == "BUY" and position:
                position.peak_price = max(position.peak_price, order.fill_price)
                position.hard_stop = round(position.average_price * (1 - self.settings.hard_stop_pct), 4)
                if existing_position is None:
                    position.entry_anchor = order.fill_price
                    position.entry_stage = order.lots
                    position.exit_stage = 0
                else:
                    position.entry_stage = prior_entry_stage + order.lots
            if exit_stage is not None and position:
                position.exit_stage = exit_stage
            if closed is not None:
                closed.exit_reason = self.exit_reasons.get(order.symbol)
            portfolio_snapshot = self.portfolio_snapshot()
            if closed is not None:
                portfolio_snapshot["closed_positions"] = [json_value(closed), *portfolio_snapshot["closed_positions"]]
            self.repository.save_paper_fill(
                order, trade, self.portfolio.positions.values(), closed, portfolio_snapshot,
            )
        except Exception:
            self.portfolio.cash = prior_cash
            self.portfolio.realized_pnl = prior_realized
            if prior_position is None:
                self.portfolio.positions.pop(order.symbol, None)
            else:
                self.portfolio.positions[order.symbol] = prior_position
            order.status, order.fill_price = prior_status, prior_fill_price
            raise
        if closed is not None:
            self.closed_positions.insert(0, json_value(closed))
            self.events.publish("position_closed", closed)
        self.events.publish("order", order)
        self.events.publish("trade", trade)
        self.events.publish("portfolio", portfolio_snapshot)

    async def on_closed_bar(self, symbol: str, macd: float) -> bool:
        """Close a position whose signal has been invalidated before it worked.

        The entry is a MACD zero-cross UP, so MACD closing back below zero is
        the entry thesis failing on its own terms -- and it says so early: over
        3-10 Sep it happened before the -30% stop in 87% of hard-stopped
        positions, a median 20.8 hours ahead of it.

        The gate is what makes this safe. Acting on every dip below zero also
        cuts winners -- 29% of winning slices, giving back more profit than it
        saves -- because a healthy trade breathes through zero on its way up.
        A position that has already shown ``macd_invalidation_max_mfe_pct`` has
        earned the existing ladder (scale-outs from +30%, trailing, hard stop)
        and is left entirely alone; only one that has never worked is cut.

        Evaluated on CLOSED bars, never intrabar: the measurement was made on
        30-minute closes, and an intrabar dip through zero is noise that
        reverses inside the same bar. Returns True when a position was closed.
        """
        if not self.settings.macd_invalidation_exit or macd >= 0:
            return False
        position = self.portfolio.positions.get(symbol)
        if not position or position.quantity <= 0:
            return False
        if position.max_return_pct >= self.settings.macd_invalidation_max_mfe_pct * 100:
            return False
        self.arm_exit(symbol, "MACD_INVALIDATED")
        try:
            await self.submit(symbol, "SELL", position.quantity)
        except (ValueError, RuntimeError, PermissionError):
            # A stale or missing quote must not kill the bar-close path; the
            # hard stop is still armed and the next closed bar retries.
            self._armed_exits.discard(symbol)
            self.exit_reasons.pop(symbol, None)
            return False
        return True

    def set_quote(self, symbol: str, price: float, timestamp: datetime | None = None) -> None:
        self.last_prices[symbol] = price
        self.last_price_at[symbol] = timestamp or datetime.now(UTC)

    def _fresh_price(self, symbol: str) -> float:
        price = self.last_prices.get(symbol)
        stamp = self.last_price_at.get(symbol)
        if price is None or not math.isfinite(price) or price <= 0:
            raise RuntimeError(f"No valid live price available for {symbol}")
        age = (datetime.now(UTC) - stamp).total_seconds() if stamp is not None else None
        if age is None or age < -5 or age > self.settings.order_quote_max_age_seconds:
            raise RuntimeError(f"Live price is stale for {symbol}")
        return price

    def _check_paper_capacity(self, order: Order, price: float) -> None:
        pending = [item for item in self.orders.values() if item.status == "OPEN" and item.order_id != order.order_id]
        position = self.portfolio.positions.get(order.symbol)
        if order.side == "SELL":
            reserved = sum(item.quantity for item in pending if item.side == "SELL" and item.symbol == order.symbol)
            if order.quantity > (position.quantity if position else 0) - reserved:
                raise ValueError("Sell quantity exceeds available holdings")
        else:
            reserved = sum(item.quantity * float(item.limit_price or 0) for item in pending if item.side == "BUY")
            if price * order.quantity > self.portfolio.cash - reserved + 1e-8:
                raise ValueError("Insufficient available paper cash")
            same_symbol = sum(item.quantity for item in pending if item.side == "BUY" and item.symbol == order.symbol)
            cap = self.settings.max_trade_lots
            if not position and not same_symbol and order.signal_id and self.settings.target_position_notional > 0:
                cap = max(cap, self.settings.max_target_entry_lots)
            if (position.quantity if position else 0) + same_symbol + order.quantity > cap * order.lot_size:
                raise ValueError(f"Position is capped at {cap} lots")
            if not position and self.settings.max_positions:
                symbols = set(self.portfolio.positions) | {item.symbol for item in pending if item.side == "BUY"}
                if order.symbol not in symbols and len(symbols) >= self.settings.max_positions:
                    raise ValueError("Maximum paper positions reached")
            if self.portfolio.cash - reserved - price * order.quantity < self.settings.min_cash_reserve:
                raise ValueError("Paper cash reserve would be breached")

    async def on_tick(self, symbol: str, price: float, timestamp: datetime | None = None) -> None:
        self.set_quote(symbol, price, timestamp)
        try:
            self._fresh_price(symbol)
        except RuntimeError:
            return
        if self.portfolio.mark(symbol, price):
            self.events.publish("portfolio", self.portfolio_snapshot())
        async with self._lock:
            today = datetime.now(IST).date()
            for order in list(self.orders.values()):
                if order.symbol == symbol and order.status == "OPEN":
                    if _session_day(order.created_at) < today:
                        self._cancel(order)  # a process that ran past the close
                        continue
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
                # Same defect as the MP desk: activation 0.30 with a 0.25 trail
                # armed the stop at 1.30 * 0.75 = 0.975x entry, locking in a
                # 2.5% loss. Clamp to the round-trip breakeven.
                floor = breakeven_exit_price(
                    position.average_price, position.quantity, self.settings.slippage_bps,
                )
                trail = position.peak_price * (1 - self.settings.trailing_stop_pct)
                position.trailing_stop = round(max(trail, floor), 4)
        reason = None
        if position.hard_stop and price <= position.hard_stop:
            reason = "HARD_STOP_30_PCT"
        elif position.trailing_stop and price <= position.trailing_stop:
            reason = "TRAILING_STOP_25_PCT"
        if reason:
            self.arm_exit(symbol, reason)
            await self.submit(symbol, "SELL", position.quantity)
            return

        # Pyramid only after the original lot is profitable. Each stage adds
        # one exchange lot and the position can never exceed four lots.
        anchor = position.entry_anchor or position.average_price
        scale_in_levels = (0.075, 0.15, 0.225)
        if self.settings.auto_trade and position.entry_stage < self.settings.max_trade_lots:
            stage_index = max(0, position.entry_stage - 1)
            threshold = round(anchor * (1 + scale_in_levels[stage_index]), 4) if stage_index < len(scale_in_levels) else None
            if threshold is not None and price >= threshold:
                try:
                    await self.submit(symbol, "BUY", lots=1)
                    return
                except (ValueError, RuntimeError, PermissionError):
                    pass  # A rejected add must not suppress protective exits.

        # Bank partial profits while retaining the last lot for the existing
        # 25%-from-peak trailing stop.
        position = self.portfolio.positions.get(symbol)
        if not position:
            return
        scale_out_levels = (0.30, 0.50, 0.75)
        if position.lots > 1 and position.exit_stage < len(scale_out_levels):
            if price >= position.average_price * (1 + scale_out_levels[position.exit_stage]):
                next_stage = position.exit_stage + 1
                self.arm_exit(symbol, f"STAGED_PROFIT_EXIT_{next_stage}")
                await self.submit(symbol, "SELL", lots=1, _exit_stage=next_stage)

    async def on_signal(self, signal: Signal) -> None:
        # Spot index/equity rows also produce radar signals but are not
        # tradable contracts — only auto-enter symbols with a resolved lot
        # size, and never let a rejected entry propagate into the tick stream.
        if signal.symbol not in self.tradable_symbols or self.lot_sizes.get(signal.symbol, 0) < 1:
            return
        position = self.portfolio.positions.get(signal.symbol)
        if signal.side == "BUY" and self.settings.auto_trade and position is None:
            try:
                lots = self.entry_lots(signal.symbol, self.last_prices.get(signal.symbol, signal.price))
                if lots < 1:
                    return
                await self.submit(signal.symbol, signal.side, lots=lots, signal=signal)
            except (ValueError, RuntimeError, PermissionError):
                return

    def snapshot(self) -> dict:
        return {
            "mode": self.settings.execution_mode,
            "live_orders_unlocked": self.settings.allow_live_orders,
            "orders": self.repository.rows("orders"),
            "trades": self.repository.rows("trades"),
            "portfolio": self.portfolio_snapshot(),
            "closed_positions": self.visible_closed_positions(),
            "risk": {
                "hard_stop_pct": self.settings.hard_stop_pct * 100,
                "trailing_activation_profit_pct": self.settings.trailing_activation_pct * 100,
                "trailing_stop_pct": self.settings.trailing_stop_pct * 100,
                "entry_filter": self.settings.entry_filter_description,
                "max_trade_lots": self.settings.max_trade_lots,
                "max_positions": self.settings.max_positions,
                "min_cash_reserve": self.settings.min_cash_reserve,
                "slippage_bps": self.settings.slippage_bps,
                "target_position_notional": self.settings.target_position_notional,
                "max_target_entry_lots": self.settings.max_target_entry_lots,
                "scale_in_profit_pct": [7.5, 15, 22.5] if self.settings.max_trade_lots > 1 else [],
                "scale_out_profit_pct": [30, 50, 75] if self.settings.max_trade_lots > 1 else [],
                "last_exit_reasons": self.exit_reasons,
                "closed_positions_visible_until": "08:00 IST next trading morning",
            },
        }
