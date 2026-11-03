"""Spot MACD lane: a positional paper book of cash equities on the MACD lane's own stock signals.

Owner, 3 Nov 2026: "implement a positional trading system with macd rules but to trade spot with ticket size of
1L". Entries are the MACD lane's EQ signals -- the stock's 30-minute MACD line crossing above zero, intrabar or on
the close, exactly as the MACD lane emits them -- bought long at a Rs 1 L ticket (whole shares), one position per
stock. Nothing is skipped on a score; cash, session and a fresh quote are the only gates.

Exits are the MACD lane's option ladder scaled to spot by 1/10 (research/spot_macd_20261103, rule OPT_10):
- hard stop 3% below the entry;
- a third out at +3%, a third at +5%, the rest at +7.5%;
- a 2.5% trail once the best is +3%, never below breakeven;
- MACD closing a bar below zero while the best was under +1% (the MACD lane's invalidation, scaled).
Owner, later on 3 Nov 2026: "start with 50L capital. hold time 3-5 days". So the book is Rs 50 L, and a position is
held 3 to 5 sessions: until 15:25 on the 3rd session after entry only the hard stop can close it (the trail keeps
tracking the best price but does not fire; thirds and the MACD exit wait), and it is out at 15:25 on the 5th.
Positions carry overnight. Paper fills ignore delivery charges (about 0.25% round trip in the research).

The research found no edge over random entries for long-only spot MACD (positive in 2025, negative since): this
lane measures it forward. Not proven.
"""
from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta

from . import sessions
from .execution import ExecutionManager
from .models import Signal
from .portfolio import breakeven_exit_price
from .smart_lane import (
    ALREADY_HELD, ENTRY_REJECTED, IST, MACD_SOURCE, NO_CASH, NO_PRICE, NOT_TRADABLE, SESSION_CLOSED, STALE,
    STALE_SECONDS, TAKEN, TOO_SMALL, WAITING_PRICE, Pending, SmartLane, hold_until,
)

LANE_NAME = "spot_macd"


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


class SpotExecutionManager(ExecutionManager):
    """Paper fills from the shared manager; the scaled MACD ladder for exits; no pyramid adds."""

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

    async def on_closed_bar(self, symbol: str, macd: float) -> bool:
        position = self.portfolio.positions.get(symbol)
        if position is not None and not self.settled(position):
            return False             # inside the minimum hold the MACD exit waits
        return await super().on_closed_bar(symbol, macd)

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
        if not position or position.quantity <= 0:
            return
        rules = self.exit_rules()
        position.hard_stop = round(position.average_price * (1 - rules["hard"]), 4)
        if price > position.peak_price:
            position.peak_price = price
        if position.peak_price >= position.average_price * (1 + rules["act"]):
            floor = breakeven_exit_price(position.average_price, position.quantity, self.settings.slippage_bps)
            trail = round(max(position.peak_price * (1 - rules["trail"]), floor), 4)
            position.trailing_stop = max(position.trailing_stop or 0.0, trail)
        now = (timestamp or datetime.now(UTC)).astimezone(IST)
        settled = self.settled(position, now)
        reason = None
        if price <= position.hard_stop:
            reason = f"HARD_STOP_{pct_label(rules['hard'])}_PCT"
        elif settled and position.trailing_stop and price <= position.trailing_stop:
            reason = f"TRAILING_STOP_{pct_label(rules['trail'])}_PCT"
        elif rules["hold"] and now >= hold_until(position.opened_at, rules["hold"]):
            reason = f"HOLD_LIMIT_{rules['hold']}_SESSIONS"
        if reason:
            self.arm_exit(symbol, reason)
            await self.submit(symbol, "SELL", position.quantity)
            return
        stages = rules["staged"]
        if settled and position.exit_stage < len(stages) and price >= position.average_price * (1 + stages[position.exit_stage]):
            stage = position.exit_stage + 1
            # Thirds of the shares bought (entry_stage holds the entry's lots, one share each); the last stage
            # and any remainder too small to split go in full.
            part = max(1, int(position.entry_stage or position.quantity) // len(stages))
            quantity = position.quantity if stage == len(stages) or part >= position.quantity else part
            self.arm_exit(symbol, f"STAGED_PROFIT_EXIT_{stage}")
            await self.submit(symbol, "SELL", quantity, _exit_stage=stage)

    async def on_signal(self, signal: Signal) -> None:
        return None   # entries come from SpotLane, which owns the rules


class SpotLane(SmartLane):
    """One paper book of cash equities. Shares SmartLane's journal, restore, trades, summary and equity views."""

    kind = "spot"
    execution_class = SpotExecutionManager

    def __init__(self, settings, broker, events, database_path: str, name: str = LANE_NAME):
        super().__init__(name, settings.spot_lane_capital, settings, broker, events, database_path)
        self._task: asyncio.Task | None = None
        self._sync_universe()

    def execution_settings(self, settings):
        """ExecutionManager's fields for this lane: share-count entries, no pyramid, no position cap."""
        return settings.model_copy(update={
            "execution_mode": "paper",
            "allow_live_orders": False,
            "initial_capital": self.capital,
            "hard_stop_pct": settings.spot_hard_stop_pct,
            "trailing_activation_pct": settings.spot_trail_activation_pct,
            "trailing_stop_pct": settings.spot_trail_pct,
            "target_position_notional": 1.0,      # entries carry a signal: the lane sizes them itself
            "max_target_entry_lots": 1_000_000_000,
            "max_trade_lots": 1,
            "max_positions": 0,
            "min_cash_reserve": 0.0,
            "auto_trade": True,
            "macd_invalidation_exit": True,
            "macd_invalidation_max_mfe_pct": settings.spot_invalidation_max_mfe_pct,
        })

    def apply_settings(self, settings) -> None:
        super().apply_settings(settings)
        self._sync_universe()

    # ----------------------------------------------------------------- wiring

    def _sync_universe(self) -> None:
        """Every configured cash symbol trades in single shares; held ones stay sellable if dropped from the list."""
        symbols = {symbol for symbol in self.settings.symbols if is_spot(symbol)}
        symbols |= {symbol for symbol, position in self.portfolio.positions.items() if position.quantity > 0}
        self.execution.set_tradable_contracts({symbol: 1 for symbol in symbols})

    def set_tradable_contracts(self, lot_sizes: dict[str, int]) -> None:
        self._sync_universe()      # the engine passes today's option lots; this book trades shares

    def set_contract_context(self, contracts: dict[str, dict]) -> None:
        return None

    def on_macd_signal(self, signal: Signal, option_type: str, features: dict | None,
                       now: datetime | None = None) -> None:
        return None                # option signals belong to the option lanes

    def on_blast_candidate(self, row: dict, now: datetime | None = None) -> None:
        return None

    def on_spot_signal(self, signal: Signal, now: datetime | None = None) -> None:
        """A MACD-lane BUY on a cash symbol: bought as soon as a fresh quote is in hand."""
        now = now or datetime.now(UTC)
        if signal.side != "BUY" or not is_spot(signal.symbol):
            return
        if any(p.symbol == signal.symbol and (p.signal.signal_id == signal.signal_id or
               p.bucket is not None and p.bucket == signal.evaluated_candle_timestamp) for p in self._pending):
            return
        self._pending.append(Pending(MACD_SOURCE, signal.symbol, "EQ", signal, due=now, created=now,
                                     bucket=signal.evaluated_candle_timestamp, detail={"kind": signal.kind}))

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

    def _estimated_buy(self, symbol: str, price: float) -> float:
        """The larger of adverse LTP slippage and the shared quoted ask (as the smart lanes reserve)."""
        execution = self.execution
        estimated = price * (1 + self.settings.slippage_bps / 10_000)
        tick = execution.quote_source(symbol) if execution.quote_source and self.settings.paper_cross_spread else None
        bid, ask = getattr(tick, "bid", None), getattr(tick, "ask", None)
        if (tick and abs(float(tick.ltp) - price) <= 1e-9 and bid and ask and
                math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
            estimated = max(estimated, min(ask, price * (1 + self.settings.paper_max_half_spread_pct / 100)))
        return estimated

    async def _decide(self, item: Pending, now: datetime, cut: float) -> None:
        symbol = item.symbol
        execution = self.execution
        if symbol in self.portfolio.positions or any(
                order.symbol == symbol and order.status == "OPEN" for order in execution.orders.values()):
            return self._record(item, ALREADY_HELD, now)
        if (now - item.due).total_seconds() > STALE_SECONDS:
            return self._record(item, NO_PRICE if item.detail.get("quote_retries") else STALE, now,
                                detail={"reason": "Admission deadline elapsed; no stale fill"})
        if not sessions.in_session(symbol, now):
            return self._record(item, SESSION_CLOSED, now)
        if symbol not in execution.tradable_symbols:
            return self._record(item, NOT_TRADABLE, now)
        try:
            price = execution._fresh_price(symbol)
        except RuntimeError:
            item.retry_at = now + timedelta(seconds=5)
            item.detail["quote_retries"] = item.detail.get("quote_retries", 0) + 1
            if item.detail["quote_retries"] == 1:
                self._record(item, WAITING_PRICE, now, detail={"reason": "Waiting for a fresh quote"})
            return
        ticket = float(self.settings.spot_lane_ticket)
        estimated = self._estimated_buy(symbol, price)
        item.detail["estimated_buy_price"] = estimated
        quantity = int(min(ticket, self.portfolio.cash) // estimated)
        if quantity < 1:
            decision = TOO_SMALL if estimated > ticket and self.portfolio.cash >= estimated else NO_CASH
            return self._record(item, decision, now, price=price, ticket=ticket,
                                detail={"one_share": round(estimated, 2), "cash": round(self.portfolio.cash, 2)})
        try:
            order = await execution.submit(symbol, "BUY", quantity, signal=item.signal)
        except (ValueError, RuntimeError, PermissionError) as exc:
            return self._record(item, ENTRY_REJECTED, now, price=price, ticket=ticket, detail={"error": str(exc)[:200]})
        if order.status != "FILLED":
            return self._record(item, ENTRY_REJECTED, now, price=price, ticket=ticket, detail={"status": order.status})
        journal_id = self._record(item, TAKEN, now, price=order.fill_price, size=1.0, ticket=ticket,
                                  lots=quantity, order_id=order.order_id)
        position = self.portfolio.positions.get(symbol)
        meta = {"position_id": position.position_id if position else f"{symbol}|{now.isoformat()}",
                "symbol": symbol, "source": MACD_SOURCE, "opened_at": now.isoformat(), "expiry": None,
                "strike": None, "option_type": "EQ", "spot_symbol": symbol, "score": None, "dispersion": 1.0,
                "journal_id": journal_id}
        self.meta[symbol] = meta
        with self._db_lock, self._db:
            self._db.execute(
                """INSERT OR REPLACE INTO smart_positions (position_id, symbol, source, opened_at, expiry, strike,
                   option_type, spot_symbol, score, dispersion, journal_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(meta[k] for k in ("position_id", "symbol", "source", "opened_at", "expiry", "strike",
                                        "option_type", "spot_symbol", "score", "dispersion", "journal_id")))

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

    def health(self) -> dict:
        snap = self.portfolio.snapshot()
        deployed = sum(p.last_price * p.quantity for p in self.portfolio.positions.values())
        rules = self.execution.exit_rules()
        return {"lane": self.name, "kind": self.kind, "capital": self.capital, "pending": len(self._pending),
                "counts": dict(self.counts), "recent": self.recent[-12:], "error": self.error,
                "last_batch_at": self.last_batch_at, "open_positions": len(self.held_symbols()),
                "cash": snap["cash"], "deployed": deployed,
                "book_full": self.portfolio.cash < self.settings.spot_lane_ticket,
                "rules": {
                    "entry": "MACD lane EQ signal: 30-min MACD(12,26,9) line crosses above zero (intrabar or close)",
                    "ticket": self.settings.spot_lane_ticket,
                    "sizing": "whole shares within the ticket and cash; no score cut-off",
                    "hard_stop_pct": rules["hard"], "trail_activation_pct": rules["act"], "trail_pct": rules["trail"],
                    "staged_exits": list(rules["staged"]), "invalidate_below_mfe_pct": rules["invalidate_below_mfe"],
                    "min_hold_sessions": rules["min_hold"], "max_hold_sessions": rules["hold"],
                    "long_only": True, "overnight": True,
                },
                "validation": "research/spot_macd_20261103: long-only spot MACD ≈ market, not proven"}
