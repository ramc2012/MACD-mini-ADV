"""Smart lanes: paper books that trade the best rules the 2 Oct 2026 research found.

Where the rules come from
-------------------------
A signal-level replay of the MACD lane (3,298 signals, 25 Aug - 1 Oct 2026, minute bars,
current rules, unlimited cash) and a 72-combination lever grid chosen on one half of the
period and tested on the other. In a cash-limited book over 19 out-of-sample sessions:

* allocation mattered most -- first-come lost Rs 10.9 L of Rs 50 L; ranking signals and
  skipping the weakest 30% made Rs 38.6 L; sizing the weaker side down by the CE/PE MACD
  breadth (dispersion) made Rs 63.5 L;
* entering 5 minutes after the cross, only if MACD is still above zero, removed about a
  third of the trades -- the crosses that did not hold -- and roughly doubled P&L per trade;
* wider exits kept the tail that pays for the lane: 40% stop, 30% trail once +40%, all out
  at +100%, the +30/+50/+75 staged exits, MACD invalidation, a 4-session hold limit.
  With the allocation above: Rs 86.1 L, max drawdown Rs 10.9 L.
* small accounts: whole lots, no pyramiding, 10% of equity per position (about ten open);
  the study's minimum for a sustained profit was about Rs 1 L.

The blast lane contributes its best: candidates that pass its 30-minute screen (premium at
most 1.2% of spot, own-side breadth >= 50%, at least 25% off the recent high) enter as a
second source and keep the blast lane's exits (50% stop, 40% trail from +30%, no ladder).
Its liquidity cap (a share of the contract's own turnover) bounds every entry here.

Two lanes, one per capital (``smart_lane_capitals_csv``), run identical rules so the effect
of capital alone is visible. Every decision -- taken or why not -- is journalled with the
inputs it was made on.
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4
from zoneinfo import ZoneInfo

from .rollover import stock_exit_due
from . import market_calendar
from .blast_engine import LaneEvents, OrderOnlyBroker
from .execution import ExecutionManager
from .models import Signal, Trade
from .portfolio import Portfolio, breakeven_exit_price
from .repository import TradeRepository
from .smart_model import MODEL
from . import local_model

IST = ZoneInfo("Asia/Kolkata")
MACD_SOURCE = "macd"
BLAST_SOURCE = "blast"
STAGED_EXITS = (0.30, 0.50, 0.75)
FLATTEN_AT = time(15, 10)          # expiry day: out before the last-half-hour collapse
HOLD_EXIT_AT = time(15, 25)        # the hold limit closes on the Nth session's last minutes
STALE_SECONDS = 180.0              # a due entry not acted on within this is history
SCORE_WINDOW = 300                 # recent MACD-source scores the skip cut is taken from
MIN_ROLLING_SCORES = 50
EQUITY_EVERY_SECONDS = 30.0
SCHEMA = """
CREATE TABLE IF NOT EXISTS smart_journal (
  id TEXT PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL, source TEXT NOT NULL, symbol TEXT NOT NULL,
  option_type TEXT, signal_at TEXT, signal_price REAL, check_price REAL, decision TEXT NOT NULL,
  score REAL, cutoff REAL, dispersion REAL, ticket REAL, lots INTEGER, order_id TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS smart_journal_day ON smart_journal(day);
CREATE TABLE IF NOT EXISTS smart_positions (
  position_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, source TEXT NOT NULL, opened_at TEXT NOT NULL,
  expiry TEXT, strike REAL, option_type TEXT, spot_symbol TEXT, score REAL, dispersion REAL, journal_id TEXT);
"""
# Decisions. TAKEN, or why not -- ordered from "could not act" to "chose not to".
TAKEN = "TAKEN"
ALREADY_HELD = "ALREADY_HELD"
STALE = "STALE"
NOT_TRADABLE = "NOT_TRADABLE"
NO_PRICE = "NO_PRICE"
MACD_FADED = "MACD_FADED"
SKIPPED_LOW_SCORE = "SKIPPED_LOW_SCORE"
TOO_ILLIQUID = "TOO_ILLIQUID"
TOO_SMALL = "LOT_ABOVE_TICKET"
NO_CASH = "NO_CASH"
ENTRY_REJECTED = "ENTRY_REJECTED"
QWEN_REJECTED = "QWEN_REJECTED"
QWEN_UNAVAILABLE = "QWEN_UNAVAILABLE"
QWEN_STALE = "QWEN_STALE"


def score(features: dict) -> float:
    """Probability the signal's position closes in profit, from the exported trees."""
    values = [features.get(name) for name in MODEL["features"]]
    raw = MODEL["baseline"]
    for tree in MODEL["trees"]:
        k = 0
        while not tree[k][4]:
            index, threshold, left, right, _leaf, _value, missing_left = tree[k]
            value = values[index]
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                k = left if missing_left else right
            else:
                k = left if value <= threshold else right
        raw += tree[k][5]
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, raw))))


def training_cut(skip_fraction: float) -> float:
    table = {float(k): v for k, v in MODEL["train_quantiles"].items()}
    return table[min(table, key=lambda q: abs(q - skip_fraction))]


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def dispersion_size(option_type: str, point, minimum: float, min_cohort: int = 20) -> float:
    """Ticket multiplier from CE/PE MACD breadth: 1 on the stronger side, own/other share on the weaker."""
    if point is None or point.ce_eligible < min_cohort or point.pe_eligible < min_cohort:
        return 1.0
    ce, pe = point.ce_above / point.ce_eligible, point.pe_above / point.pe_eligible
    own, other = (ce, pe) if option_type == "CE" else (pe, ce)
    if own >= other or other <= 0:
        return 1.0
    return max(minimum, own / other)


def hold_until(opened_at: datetime, sessions: int) -> datetime:
    day = opened_at.astimezone(IST).date()
    for _ in range(sessions):
        day = market_calendar.next_trading_day(day)
    return datetime.combine(day, HOLD_EXIT_AT, IST)


@dataclass
class Pending:
    source: str
    symbol: str
    option_type: str
    signal: Signal
    due: datetime
    created: datetime
    bucket: int | None = None
    score: float | None = None
    features: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


class SmartExecutionManager(ExecutionManager):
    """Paper fills from the shared manager; exits chosen per position by its source."""

    def __init__(self, *args, lane: "SmartLane", **kwargs):
        super().__init__(*args, **kwargs)
        self.lane = lane

    def exit_rules(self, source: str) -> dict:
        s = self.lane.settings
        if source == BLAST_SOURCE:
            return {"hard": s.blast_hard_stop_pct, "act": s.blast_trail_activation_pct, "trail": s.blast_trail_pct,
                    "target": None, "staged": False, "hold": 0, "invalidate": False}
        return {"hard": s.smart_hard_stop_pct, "act": s.smart_trail_activation_pct, "trail": s.smart_trail_pct,
                "target": s.smart_target_pct, "staged": True, "hold": s.smart_max_hold_sessions, "invalidate": True}

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
        meta = self.lane.meta.get(symbol, {})
        rules = self.exit_rules(meta.get("source", MACD_SOURCE))
        position.hard_stop = round(position.average_price * (1 - rules["hard"]), 4)
        if price > position.peak_price:
            position.peak_price = price
        if position.peak_price >= position.average_price * (1 + rules["act"]):
            floor = breakeven_exit_price(position.average_price, position.quantity, self.settings.slippage_bps)
            trail = round(max(position.peak_price * (1 - rules["trail"]), floor), 4)
            position.trailing_stop = max(position.trailing_stop or 0.0, trail)
        now = (timestamp or datetime.now(UTC)).astimezone(IST)
        reason = None
        expiry = meta.get("expiry")
        today = now.date().isoformat()
        if expiry and (expiry < today or (expiry == today and now.time() >= FLATTEN_AT)):
            reason = "EXPIRY_DAY_FLATTEN"
        elif expiry and stock_exit_due(symbol, expiry, now, (FLATTEN_AT.hour, FLATTEN_AT.minute)):
            reason = "STOCK_SERIES_LAST_DAY_EXIT"     # T-2 for stock options (rollover.py)
        elif price <= position.hard_stop:
            reason = f"HARD_STOP_{round(rules['hard'] * 100)}_PCT"
        elif position.trailing_stop and price <= position.trailing_stop:
            reason = f"TRAILING_STOP_{round(rules['trail'] * 100)}_PCT"
        elif rules["target"] and price >= position.average_price * (1 + rules["target"]):
            reason = f"TARGET_{round(rules['target'] * 100)}_PCT"
        elif rules["hold"] and now >= hold_until(position.opened_at, rules["hold"]):
            reason = f"HOLD_LIMIT_{rules['hold']}_SESSIONS"
        if reason:
            self.arm_exit(symbol, reason)
            await self.submit(symbol, "SELL", position.quantity)
            return
        if rules["staged"] and position.lots > 1 and position.exit_stage < len(STAGED_EXITS):
            if price >= position.average_price * (1 + STAGED_EXITS[position.exit_stage]):
                stage = position.exit_stage + 1
                self.arm_exit(symbol, f"STAGED_PROFIT_EXIT_{stage}")
                await self.submit(symbol, "SELL", lots=1, _exit_stage=stage)

    async def on_closed_bar(self, symbol: str, macd: float) -> bool:
        """MACD invalidation applies to MACD-sourced positions only.

        A blast entry is a signal-line cross with MACD still below zero, so the
        zero-line test would close it on its first bar.
        """
        if self.lane.meta.get(symbol, {}).get("source", MACD_SOURCE) != MACD_SOURCE:
            return False
        return await super().on_closed_bar(symbol, macd)

    async def on_signal(self, signal: Signal) -> None:
        return None   # entries come from SmartLane, which owns the rules


class SmartLane:
    def __init__(self, name: str, capital: float, settings, broker, events, database_path: str):
        self.name = name
        self.capital = float(capital)
        self.settings = settings
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.repository = TradeRepository(database_path)
        self._db = sqlite3.connect(database_path, check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db_lock = Lock()
        with self._db_lock, self._db:
            self._db.executescript(SCHEMA)
        self.portfolio = Portfolio(self.capital, lane=name)
        self.events = LaneEvents(events, prefix=f"{name}_")
        self.broker = OrderOnlyBroker(broker)
        self.execution = SmartExecutionManager(
            self.execution_settings(settings), self.broker, self.portfolio, self.repository, self.events, lane=self)
        self.meta: dict[str, dict] = {}
        self._contracts: dict[str, dict] = {}
        self._pending: list[Pending] = []
        self._scores: list[float] = []
        self._journal_buffer: list[tuple] = []
        self.counts: dict[str, int] = {}
        self.recent: list[dict] = []
        self.error: str | None = None
        self.last_batch_at: str | None = None
        self.local_review: dict | None = None
        self._macd_confirm = lambda symbol, bucket, price: None
        self._dispersion = lambda: None
        self._last_equity = 0.0
        self._last_equity_write = -1e9
        self._last_equity_value = float("nan")
        self._restore()

    def execution_settings(self, settings):
        """ExecutionManager's fields for this lane: whole-lot entries, no pyramid, no position cap."""
        return settings.model_copy(update={
            "initial_capital": self.capital,
            "hard_stop_pct": settings.smart_hard_stop_pct,
            "trailing_activation_pct": settings.smart_trail_activation_pct,
            "trailing_stop_pct": settings.smart_trail_pct,
            "target_position_notional": 1.0,      # entries carry a signal: the lane sizes them itself
            "max_target_entry_lots": 100_000,
            "max_trade_lots": 1,                  # 1 disables pyramiding (adds gate on entry_stage < 1)
            "max_positions": 0,
            "min_cash_reserve": 0.0,
            "auto_trade": True,
            "macd_invalidation_exit": True,       # part of the tested rule set, whatever the MACD lane uses
            "macd_invalidation_max_mfe_pct": 0.10,
        })

    def apply_settings(self, settings) -> None:
        self.settings = settings
        self.execution.settings = self.execution_settings(settings)

    # ---------------------------------------------------------------- restore

    def _restore(self) -> None:
        rows = sorted(self.repository.rows("trades", 1_000_000), key=lambda row: row["timestamp"])
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
        open_ids = {position.position_id: symbol for symbol, position in self.portfolio.positions.items()}
        with self._db_lock:
            stored = self._db.execute("SELECT * FROM smart_positions").fetchall()
            recent = self._db.execute(
                "SELECT score FROM smart_journal WHERE source = ? AND score IS NOT NULL ORDER BY at DESC LIMIT ?",
                (MACD_SOURCE, SCORE_WINDOW)).fetchall()
        for row in stored:
            if row["position_id"] in open_ids:
                self.meta[row["symbol"]] = dict(row)
        self.execution.set_contract_expiries({
            symbol: row["expiry"] for symbol, row in self.meta.items() if row.get("expiry")
        })
        self._scores = [float(r["score"]) for r in reversed(recent)]
        excursions = self.repository.load_position_excursions()
        for symbol, position in self.portfolio.positions.items():
            row = excursions.get(position.position_id)
            if row:
                position.max_price = max(position.max_price, float(row["max_price"]))
                position.min_price = min(position.min_price or float(row["min_price"]), float(row["min_price"]))
                position.max_return_pct = max(position.max_return_pct, float(row["max_return_pct"]))
                position.min_return_pct = min(position.min_return_pct, float(row["min_return_pct"]))
            position.peak_price = max(position.peak_price, position.average_price, position.max_price)
            position.entry_anchor = position.entry_anchor or position.average_price
            position.entry_stage = max(position.entry_stage, position.lots)
            rules = self.execution.exit_rules(self.meta.get(symbol, {}).get("source", MACD_SOURCE))
            position.hard_stop = round(position.average_price * (1 - rules["hard"]), 4)
            if position.peak_price >= position.average_price * (1 + rules["act"]):
                floor = breakeven_exit_price(position.average_price, position.quantity, self.settings.slippage_bps)
                position.trailing_stop = round(max(position.peak_price * (1 - rules["trail"]), floor), 4)

    # ----------------------------------------------------------------- wiring

    def set_tradable_contracts(self, lot_sizes: dict[str, int]) -> None:
        held = {symbol: position.lot_size for symbol, position in self.portfolio.positions.items()
                if position.quantity > 0 and position.lot_size > 1}
        self.execution.set_tradable_contracts({**held, **lot_sizes})

    def set_contract_context(self, contracts: dict[str, dict]) -> None:
        held = {symbol: row for symbol, row in self._contracts.items() if symbol in self.portfolio.positions}
        self._contracts = {**held, **contracts}
        self.execution.set_contract_expiries({
            symbol: row.get("expiry") for symbol, row in self._contracts.items()
        })

    def set_resolvers(self, *, macd_confirm=None, dispersion=None) -> None:
        if macd_confirm is not None:
            self._macd_confirm = macd_confirm
        if dispersion is not None:
            self._dispersion = dispersion

    def held_symbols(self) -> list[str]:
        return [symbol for symbol, position in self.portfolio.positions.items() if position.quantity > 0]

    # ----------------------------------------------------------------- intake

    def on_macd_signal(self, signal: Signal, option_type: str, features: dict | None,
                       now: datetime | None = None) -> None:
        """A MACD-lane BUY: scored now, re-checked smart_entry_delay_seconds later."""
        now = now or datetime.now(UTC)
        value = None
        if features:
            try:
                value = score(features)
                self._scores.append(value)
                del self._scores[:-SCORE_WINDOW]
            except Exception as exc:  # noqa: BLE001 -- an unscorable signal is journalled, not fatal
                self.error = f"score {signal.symbol}: {type(exc).__name__}: {exc}"[:300]
        self._pending.append(Pending(
            MACD_SOURCE, signal.symbol, option_type, signal,
            due=now + timedelta(seconds=self.settings.smart_entry_delay_seconds), created=now,
            bucket=signal.evaluated_candle_timestamp, score=value, features=features or {}))

    def on_blast_candidate(self, row: dict, now: datetime | None = None) -> None:
        """A candidate that passed the blast lane's screen, whether or not that lane bought it."""
        if not self.settings.smart_blast_source:
            return
        now = now or datetime.now(UTC)
        signal = Signal(row["symbol"], "BUY", "SMART_BLAST_SCREEN", float(row["premium"]), float(row.get("macd") or 0),
                        float(row.get("signal") or 0), float(row.get("histogram") or 0),
                        evaluated_candle_timestamp=row.get("bar_timestamp"))
        self._pending.append(Pending(
            BLAST_SOURCE, row["symbol"], row.get("option_type") or row["symbol"][-2:], signal, due=now, created=now,
            bucket=row.get("bar_timestamp"),
            detail={k: row.get(k) for k in ("premium_pct", "breadth", "off_high_pct", "journal_id")}))

    def cutoff(self) -> float:
        fraction = self.settings.smart_skip_fraction
        if len(self._scores) >= MIN_ROLLING_SCORES:
            return quantile(self._scores, fraction)
        return training_cut(fraction)

    # --------------------------------------------------------------- deciding

    async def step(self, now: datetime | None = None) -> int:
        """Act on every due entry; MACD-sourced ones best-first within the rank window."""
        now = now or datetime.now(UTC)
        due = [p for p in self._pending if p.due <= now]
        if not due:
            return 0
        if (now - min(p.due for p in due)).total_seconds() < self.settings.smart_rank_seconds:
            return 0
        self._pending = [p for p in self._pending if p.due > now]
        ordered = ([p for p in due if p.source == BLAST_SOURCE]
                   + sorted((p for p in due if p.source == MACD_SOURCE), key=lambda p: -(p.score or 0.0)))
        cut = self.cutoff()
        for item in ordered:
            try:
                await self._decide(item, now, cut)
            except Exception as exc:  # noqa: BLE001 -- one bad entry must not stop the batch
                self.error = f"{item.symbol}: {type(exc).__name__}: {exc}"[:300]
                self._record(item, ENTRY_REJECTED, now, cut=cut, detail={"error": str(exc)[:200]})
        self.last_batch_at = now.isoformat()
        return len(ordered)

    def _liquidity_lots(self, symbol: str, lot_size: int) -> int | None:
        contract = self._contracts.get(symbol) or {}
        liquidity = max(float(contract.get("volume") or 0), float(contract.get("oi") or 0) / 100.0)
        if liquidity <= 0 or lot_size < 1:
            return None
        return int(liquidity * (self.settings.blast_max_volume_share_pct / 100.0) // lot_size)

    async def _decide(self, item: Pending, now: datetime, cut: float, local_review=None) -> None:
        symbol = item.symbol
        execution = self.execution
        if symbol in self.portfolio.positions:
            return self._record(item, ALREADY_HELD, now, cut=cut)
        if (now - item.due).total_seconds() > STALE_SECONDS:
            return self._record(item, STALE, now, cut=cut)
        if symbol not in execution.tradable_symbols or execution.lot_sizes.get(symbol, 0) < 1:
            return self._record(item, NOT_TRADABLE, now, cut=cut)
        try:
            price = execution._fresh_price(symbol)
        except RuntimeError:
            return self._record(item, NO_PRICE, now, cut=cut)
        size = 1.0
        if item.source == MACD_SOURCE:
            if self._macd_confirm(symbol, item.bucket, price) is not True:
                return self._record(item, MACD_FADED, now, price=price, cut=cut)
            if item.score is None or item.score < cut:
                return self._record(item, SKIPPED_LOW_SCORE, now, price=price, cut=cut)
            size = dispersion_size(item.option_type, self._dispersion(), self.settings.smart_min_dispersion_size)
        lot = execution.lot_sizes[symbol]
        per_lot = price * lot * (1 + self.settings.slippage_bps / 10_000)
        equity = self.portfolio.cash + sum(p.last_price * p.quantity for p in self.portfolio.positions.values())
        ticket = self.settings.smart_ticket_fraction * equity * size
        lots = int(min(ticket, self.portfolio.cash) // per_lot)
        cap = self._liquidity_lots(symbol, lot)
        if lots >= 1 and cap is not None and cap < lots:
            lots = cap
            if lots < 1:
                return self._record(item, TOO_ILLIQUID, now, price=price, cut=cut, size=size, ticket=ticket)
        if lots < 1:
            decision = TOO_SMALL if per_lot > ticket and self.portfolio.cash >= per_lot else NO_CASH
            return self._record(item, decision, now, price=price, cut=cut, size=size, ticket=ticket,
                                detail={"one_lot": round(per_lot, 2)})
        mode,_ = local_model.configuration()
        if mode != 'off' and local_review is None:
            try:
                review=await local_model.review_smart(item,price,execution.last_price_at[symbol])
                self.local_review=review
                item.detail['local_review']=review
            except Exception as exc:
                self.local_review={'verdict':'unavailable','reason':type(exc).__name__}
                item.detail['local_review']=self.local_review
                if mode=='filter':
                    return self._record(item,QWEN_UNAVAILABLE,datetime.now(UTC),price=price,cut=cut)
                review={'verdict':'unavailable','reference_price':price}
            if mode=='filter' and review['verdict']!='approve':
                return self._record(item,QWEN_REJECTED,datetime.now(UTC),price=price,cut=cut)
            # Every original gate, cash/lot calculation and quote is checked again after inference.
            return await self._decide(item,datetime.now(UTC),self.cutoff(),local_review=review)
        if mode=='filter' and local_review is not None:
            try:
                stale=(datetime.fromisoformat(local_review['expires_at'])<=datetime.now(UTC)
                       or abs(price/local_review['reference_price']-1)>.02)
            except (KeyError,ValueError,ZeroDivisionError):stale=True
            if stale:return self._record(item,QWEN_STALE,now,price=price,cut=cut)
        try:
            order = await execution.submit(symbol, "BUY", lots=lots, signal=item.signal)
        except (ValueError, RuntimeError, PermissionError) as exc:
            return self._record(item, ENTRY_REJECTED, now, price=price, cut=cut, size=size, ticket=ticket,
                                detail={"error": str(exc)[:200]})
        if order.status != "FILLED":
            return self._record(item, ENTRY_REJECTED, now, price=price, cut=cut, size=size, ticket=ticket,
                                detail={"status": order.status})
        journal_id = self._record(item, TAKEN, now, price=order.fill_price, cut=cut, size=size, ticket=ticket,
                                  lots=lots, order_id=order.order_id)
        position = self.portfolio.positions.get(symbol)
        contract = self._contracts.get(symbol) or {}
        meta = {"position_id": position.position_id if position else f"{symbol}|{now.isoformat()}",
                "symbol": symbol, "source": item.source, "opened_at": now.isoformat(),
                "expiry": contract.get("expiry"), "strike": contract.get("strike"), "option_type": item.option_type,
                "spot_symbol": contract.get("spot_symbol"), "score": item.score, "dispersion": size,
                "journal_id": journal_id}
        self.meta[symbol] = meta
        with self._db_lock, self._db:
            self._db.execute(
                """INSERT OR REPLACE INTO smart_positions (position_id, symbol, source, opened_at, expiry, strike,
                   option_type, spot_symbol, score, dispersion, journal_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(meta[k] for k in ("position_id", "symbol", "source", "opened_at", "expiry", "strike",
                                        "option_type", "spot_symbol", "score", "dispersion", "journal_id")))

    def _record(self, item: Pending, decision: str, now: datetime, *, price: float | None = None,
                cut: float | None = None, size: float | None = None, ticket: float | None = None,
                lots: int | None = None, order_id: str | None = None, detail: dict | None = None) -> str:
        row_id = uuid4().hex
        info = {**item.detail, **(detail or {}),'signal_id':item.signal.signal_id,
                'signal_at':item.signal.timestamp.isoformat(),'features_available_at':item.created.isoformat()}
        if item.features:
            info["features"] = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in item.features.items()}
        self._journal_buffer.append((
            row_id, now.isoformat(), now.astimezone(IST).date().isoformat(), item.source, item.symbol,
            item.option_type, item.created.isoformat(), float(item.signal.price), price, decision,
            item.score, cut if item.source == MACD_SOURCE else None, size, ticket, lots, order_id,
            json.dumps(info, default=str)))
        self.counts[decision] = self.counts.get(decision, 0) + 1
        self.recent.append({"at": now.isoformat(), "symbol": item.symbol, "source": item.source,
                            "decision": decision, "score": None if item.score is None else round(item.score, 4)})
        del self.recent[:-40]
        self.events.publish("decision", self.recent[-1])
        return row_id

    def flush_journal(self) -> int:
        rows, self._journal_buffer = self._journal_buffer, []
        if not rows:
            return 0
        with self._db_lock, self._db:
            self._db.executemany("INSERT OR REPLACE INTO smart_journal VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    async def run(self) -> None:
        """Due entries every 2 s; journal and equity points off the event loop."""
        while True:
            await asyncio.sleep(2.0)
            try:
                await self.step()
                if self._journal_buffer:
                    await asyncio.to_thread(self.flush_journal)
                loop_now = asyncio.get_running_loop().time()
                if loop_now - self._last_equity >= EQUITY_EVERY_SECONDS:
                    snapshot = self.portfolio.snapshot()
                    # An idle book (nights, weekends) would otherwise write the same
                    # point every 30 s and push the real curve out of the window.
                    if (abs(snapshot["equity"] - self._last_equity_value) >= 1.0
                            or loop_now - self._last_equity_write >= 900):
                        self._last_equity_write = loop_now
                        self._last_equity_value = snapshot["equity"]
                        await asyncio.to_thread(self.repository.save_equity_point, snapshot)
                    self._last_equity = loop_now
                    await asyncio.to_thread(self.repository.save_position_excursions,
                                            list(self.portfolio.positions.values()))
            except Exception as exc:  # noqa: BLE001 -- the lane must never take the feed down
                self.error = f"{type(exc).__name__}: {exc}"[:300]

    # ------------------------------------------------------------------ views

    def journal_rows(self, *, day: str | None = None, decision: str | None = None, limit: int = 300) -> list[dict]:
        clauses, params = [], []
        if day:
            clauses.append("day = ?"); params.append(day)
        if decision:
            clauses.append("decision = ?"); params.append(decision)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._db_lock:
            rows = self._db.execute(f"SELECT * FROM smart_journal {where} ORDER BY at DESC LIMIT ?",
                                    (*params, limit)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                detail = json.loads(item.pop("detail") or "{}")
            except ValueError:
                detail = {}
            detail.pop("features", None)
            item["detail"] = detail
            out.append(item)
        return out

    def journal_days(self, limit: int = 30) -> list[str]:
        with self._db_lock:
            return [r[0] for r in self._db.execute(
                "SELECT DISTINCT day FROM smart_journal ORDER BY day DESC LIMIT ?", (limit,)).fetchall()]

    def trades(self, limit: int = 1000) -> list[dict]:
        """Closed positions (slices grouped by position_id), newest first, tagged with their source."""
        slices = self.repository.closed_positions(lane=self.name, limit=20_000)
        with self._db_lock:
            sources = {r["position_id"]: dict(r) for r in self._db.execute("SELECT * FROM smart_positions")}
        grouped: dict[str, dict] = {}
        for row in sorted(slices, key=lambda r: r["exit_time"]):
            pid = row["position_id"]
            g = grouped.setdefault(pid, {
                "position_id": pid, "symbol": row["symbol"], "entry_time": row["entry_time"],
                "entry_price": row["entry_price"], "lots": 0, "lot_size": row["lot_size"], "pnl": 0.0,
                "cost": 0.0, "exit_time": None, "exit_reason": None, "max_return_pct": row.get("max_return_pct"),
                "min_return_pct": row.get("min_return_pct"), "slices": 0,
                "source": (sources.get(pid) or {}).get("source", MACD_SOURCE)})
            g["lots"] += int(row["lots"])
            g["pnl"] += float(row["realized_pnl"])
            g["cost"] += float(row["entry_price"]) * int(row["quantity"])
            g["exit_time"], g["exit_reason"] = row["exit_time"], row.get("exit_reason")
            g["max_return_pct"] = max(g["max_return_pct"] or 0, row.get("max_return_pct") or 0)
            g["slices"] += 1
            g["closed"] = not row.get("partial")
        out = []
        for g in grouped.values():
            g["return_pct"] = g["pnl"] / g["cost"] * 100 if g["cost"] else 0.0
            out.append(g)
        out.sort(key=lambda g: g["exit_time"] or "", reverse=True)
        return out[:limit]

    def summary(self) -> dict:
        trades = [t for t in self.trades(100_000) if t.get("closed")]
        snap = self.portfolio.snapshot()

        def stats(rows):
            wins = [t["pnl"] for t in rows if t["pnl"] > 0]
            losses = [t["pnl"] for t in rows if t["pnl"] <= 0]
            return {"trades": len(rows), "wins": len(wins), "win_rate": len(wins) / len(rows) if rows else None,
                    "net": sum(wins) + sum(losses), "gross_profit": sum(wins), "gross_loss": sum(losses),
                    "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
                    "avg_win": sum(wins) / len(wins) if wins else None,
                    "avg_loss": sum(losses) / len(losses) if losses else None}

        by_reason: dict[str, list] = {}
        for t in trades:
            by_reason.setdefault(t["exit_reason"] or "?", []).append(t)
        with self._db_lock:
            decisions = {r[0]: r[1] for r in self._db.execute(
                "SELECT decision, COUNT(*) FROM smart_journal GROUP BY decision").fetchall()}
            by_source = {(r[0], r[1]): r[2] for r in self._db.execute(
                "SELECT source, decision, COUNT(*) FROM smart_journal GROUP BY source, decision").fetchall()}
        curve = self.repository.equity_rows(5000)
        peak, max_dd = self.capital, 0.0
        for row in curve:
            equity = float(row.get("equity") or 0)
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return {
            "lane": self.name, "capital": self.capital, "equity": snap["equity"], "cash": snap["cash"],
            "return_pct": (snap["equity"] / self.capital - 1) * 100, "realized": snap["realized_pnl"],
            "unrealized": snap["unrealized_pnl"], "open_positions": len(snap["positions"]),
            "max_drawdown": max_dd, "all": stats(trades),
            "by_source": {src: stats([t for t in trades if t["source"] == src]) for src in (MACD_SOURCE, BLAST_SOURCE)},
            "by_exit_reason": {k: stats(v) for k, v in sorted(by_reason.items())},
            "decisions": decisions,
            "decisions_by_source": {f"{s}:{d}": n for (s, d), n in by_source.items()},
        }

    def equity_curve(self, limit: int = 3000) -> list[dict]:
        rows = self.repository.equity_rows(limit)
        return [{"at": r.get("timestamp") or r.get("at"), "equity": r.get("equity")} for r in rows]

    def snapshot(self) -> dict:
        snap = self.execution.portfolio_snapshot()
        for row in snap["positions"]:
            meta = self.meta.get(row["symbol"], {})
            rules = self.execution.exit_rules(meta.get("source", MACD_SOURCE))
            row["source"] = meta.get("source", MACD_SOURCE)
            row["expiry"] = meta.get("expiry")
            row["score"] = meta.get("score")
            row["dispersion"] = meta.get("dispersion")
            row["target_price"] = (round(row["average_price"] * (1 + rules["target"]), 4) if rules["target"] else None)
            try:
                opened = datetime.fromisoformat(str(row["opened_at"]))
                row["hold_until"] = hold_until(opened, rules["hold"]).isoformat() if rules["hold"] else None
            except (TypeError, ValueError):
                row["hold_until"] = None
        return {"lane": self.name, "capital": self.capital, "portfolio": snap, "health": self.health()}

    def health(self) -> dict:
        return {"lane": self.name, "capital": self.capital, "pending": len(self._pending),
                "cutoff": round(self.cutoff(), 4), "rolling_scores": len(self._scores),
                "counts": dict(self.counts), "recent": self.recent[-12:], "error": self.error,
                "last_batch_at": self.last_batch_at, "open_positions": len(self.held_symbols()),
                "local_model": {"mode":local_model.configuration()[0],"model":"qwen3.5:9b","last_review":self.local_review},
                "rules": {
                    "entry_delay_seconds": self.settings.smart_entry_delay_seconds,
                    "skip_fraction": self.settings.smart_skip_fraction,
                    "ticket_fraction": self.settings.smart_ticket_fraction,
                    "macd": self.execution.exit_rules(MACD_SOURCE),
                    "blast": self.execution.exit_rules(BLAST_SOURCE) if self.settings.smart_blast_source else None,
                    "liquidity_share_pct": self.settings.blast_max_volume_share_pct,
                },
                "model": {"trained_on": MODEL["trained_on"], "walk_forward": MODEL["walk_forward"]}}

    def sweep_closed_positions(self) -> bool:
        return self.execution.sweep_closed_positions()


class SmartLanes:
    """The set of smart lanes, one per configured capital."""

    def __init__(self, settings, broker, events):
        self.settings = settings
        self.lanes: list[SmartLane] = []
        self._tasks: list[asyncio.Task] = []
        if not settings.smart_lanes_enabled:
            return
        base = Path(settings.database_path).parent    # beside the MACD book: /app/data in Compose
        for raw in str(settings.smart_lane_capitals_csv).split(","):
            raw = raw.strip()
            if not raw:
                continue
            capital = float(raw)
            name = f"smart_{lane_label(capital)}"
            self.lanes.append(SmartLane(name, capital, settings, broker, events, str(base / f"{name}.sqlite3")))

    def get(self, name: str) -> SmartLane | None:
        return next((lane for lane in self.lanes if lane.name == name), None)

    def start(self) -> None:
        if not self._tasks:
            self._tasks = [asyncio.create_task(lane.run()) for lane in self.lanes]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []
        for lane in self.lanes:
            await asyncio.to_thread(lane.flush_journal)

    def held_symbols(self) -> list[str]:
        return [symbol for lane in self.lanes for symbol in lane.held_symbols()]

    def apply_settings(self, settings) -> None:
        self.settings = settings
        for lane in self.lanes:
            lane.apply_settings(settings)

    def rebind_broker(self, broker) -> None:
        for lane in self.lanes:
            lane.broker.rebind(broker)

    def health(self) -> dict:
        return {lane.name: lane.health() for lane in self.lanes}


def lane_label(capital: float) -> str:
    if capital >= 1e7 and capital % 1e7 == 0:
        return f"{int(capital // 1e7)}cr"
    if capital >= 1e5 and capital % 1e5 == 0:
        return f"{int(capital // 1e5)}l"
    if capital >= 1e3 and capital % 1e3 == 0:
        return f"{int(capital // 1e3)}k"
    return str(int(capital))
