"""Independent paper books consuming MACD BUY signals, without a Qwen dependency.

Local allocation combines a retained research ranker with current shared market
context. Rank/breadth scale tickets; low scores do not veto signals. Existing
whole-lot, cash, expiry, liquidity and MACD confirmation gates remain. Missing
quotes retry within the original deadline. Existing source-specific exits and
books are preserved. Historical research is not proof of prospective edge.
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

from .config import RUNTIME_DIR
from .rollover import stock_exit_due
from . import market_calendar, sessions
from .blast_engine import LaneEvents, OrderOnlyBroker
from .execution import ExecutionManager
from .models import Signal, Trade
from .portfolio import Portfolio, breakeven_exit_price
from .repository import TradeRepository
from .smart_model import MODEL
from . import smart_intelligence
from . import smart_grok
from .smart_grok import GROK_SOURCE

IST = ZoneInfo("Asia/Kolkata")
MACD_SOURCE = "macd"
BLAST_SOURCE = "blast"
STAGED_EXITS = (0.30, 0.50, 0.75)
FLATTEN_AT = time(15, 10)          # expiry day: out before the last-half-hour collapse
HOLD_EXIT_AT = time(15, 25)        # the hold limit closes on the Nth session's last minutes
STALE_SECONDS = 600.0              # a due entry still without a fresh quote after this is history (was 180 s:
                                   # 109 such NO_PRICE signals averaged +8.7% in the MACD lane, 3-28 Oct 2026)
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
POSITION_LIMIT = "POSITION_LIMIT"
WHALE_REPLACE_EXIT = "WHALE_PRIORITY_REPLACE"
ENTRY_REJECTED = "ENTRY_REJECTED"
QWEN_REJECTED = "QWEN_REJECTED"
QWEN_UNAVAILABLE = "QWEN_UNAVAILABLE"
QWEN_STALE = "QWEN_STALE"
WAITING_PRICE = "WAITING_PRICE"
SESSION_CLOSED = "SESSION_CLOSED"


def score(features: dict) -> float:
    """Retained exported research score; calibration and prospective edge are unproven."""
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


def score_percentile(score: float, window: list[float]) -> float:
    """The score's rank, 0..1, among recent MACD-source scores (once MIN_ROLLING_SCORES are in), else on the
    exported ranker's training quantiles -- the same two bases the old skip cut used."""
    if len(window) >= MIN_ROLLING_SCORES:
        return sum(1 for value in window if value <= score) / len(window)
    table = sorted((float(k), v) for k, v in MODEL["train_quantiles"].items())
    if score <= table[0][1]:
        return table[0][0] * (score / table[0][1] if table[0][1] > 0 else 0.0)
    for (q0, v0), (q1, v1) in zip(table, table[1:]):
        if score <= v1:
            return q0 + (q1 - q0) * ((score - v0) / (v1 - v0) if v1 > v0 else 1.0)
    return 1.0


def score_size(score: float | None, window: list[float], minimum: float) -> float:
    """Ticket multiplier from the ranker (owner, 26 Oct 2026: no score cut-off; a weaker signal buys less, never
    nothing). An unscorable signal gets the minimum."""
    minimum = min(1.0, max(0.0, minimum))
    if score is None:
        return minimum
    return minimum + (1.0 - minimum) * min(1.0, max(0.0, score_percentile(score, window)))


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
    retry_at: datetime | None = None


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
        if source == GROK_SOURCE:
            # Grok manages the exit (owner, 18 Nov 2026): its own stop/target levels plus a safety stop; no
            # trail, fixed target, staged thirds, hold limit or MACD invalidation.
            return {"hard": s.smart_grok_hard_stop_pct, "act": math.inf, "trail": s.smart_trail_pct,
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
        levels = self.lane.grok_levels.get(symbol) or {}
        if expiry and (expiry < today or (expiry == today and now.time() >= FLATTEN_AT)):
            reason = "EXPIRY_DAY_FLATTEN"
        elif expiry and stock_exit_due(symbol, expiry, now, (FLATTEN_AT.hour, FLATTEN_AT.minute)):
            reason = "STOCK_SERIES_LAST_DAY_EXIT"     # T-2 for stock options (rollover.py)
        elif price <= position.hard_stop:
            reason = f"HARD_STOP_{round(rules['hard'] * 100)}_PCT"
        elif levels.get("stop") and price <= levels["stop"]:
            reason = "GROK_STOP"
        elif levels.get("target") and price >= levels["target"]:
            reason = "GROK_TARGET"
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
    execution_class = SmartExecutionManager      # the spot lane (spot_lane.py) brings its own exits

    def __init__(self, name: str, capital: float, settings, broker, events, database_path: str, slots: int = 0):
        self.name = name
        self.capital = float(capital)
        self.slots = int(slots)          # 0: no position limit; tickets are smart_ticket_fraction of equity
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
        self.execution = self.execution_class(
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
        self.local_allocation: dict | None = None
        self._macd_confirm = lambda symbol, bucket, price: None
        self._dispersion = lambda: None
        self._entry_context = lambda item, now: {}
        self._whale = lambda symbol: None    # the underlying's live whale reading (engine.whale_interest)
        self._market_frame = lambda now: {}  # indices, VIX, breadth, agentic view (engine._smart_market_frame)
        self.grok: smart_grok.GrokDesk | None = None     # Grok picks and exits (configure_grok)
        self.grok_levels: dict[str, dict] = {}           # symbol -> Grok's stop/target premiums
        self._last_equity = 0.0
        self._last_equity_write = -1e9
        self._last_equity_value = float("nan")
        self._restore()

    def execution_settings(self, settings):
        """ExecutionManager's fields for this lane: whole-lot entries, no pyramid, no position cap."""
        return settings.model_copy(update={
            "execution_mode": "paper",
            "allow_live_orders": False,
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

    def configure_grok(self, enabled: bool, runtime_dir: str | None = None) -> None:
        """Grok mode on or off for this book; its levels for open Grok positions are restored either way."""
        if enabled and self.grok is None:
            self.grok = smart_grok.GrokDesk(self, runtime_dir)
        elif not enabled:
            self.grok = None
        with self._db_lock, self._db:
            self._db.executescript(smart_grok.CALLS_SCHEMA)
            rows = self._db.execute("SELECT * FROM smart_grok_levels").fetchall()
        open_ids = {p.position_id: s for s, p in self.portfolio.positions.items() if p.quantity > 0}
        self.grok_levels = {open_ids[r["position_id"]]: {"stop": r["stop"], "target": r["target"],
                                                         "conviction": r["conviction"], "reason": r["reason"],
                                                         "updated_at": r["updated_at"]}
                            for r in rows if r["position_id"] in open_ids}

    def set_grok_levels(self, symbol: str, stop, target, *, conviction: str | None = None, reason: str | None = None,
                        now: datetime | None = None, keep_reason: bool = False) -> dict:
        """Check and keep Grok's stop/target premiums for an open position.

        A stop at or above the current premium, or a target at or below it, is not a level (Grok closes instead):
        the previous level stays. A stop below the safety stop is dropped -- the safety stop already covers it.
        None clears that side.
        """
        position = self.portfolio.positions.get(symbol)
        if position is None or position.quantity <= 0:
            return {}
        now = now or datetime.now(UTC)
        previous = self.grok_levels.get(symbol) or {}
        price = self.execution.last_prices.get(symbol) or position.last_price or position.average_price
        floor = position.average_price * (1 - self.settings.smart_grok_hard_stop_pct)
        notes = []

        def number(value):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) and value > 0 else None

        stop, target = number(stop), number(target)
        if stop is not None and stop >= price:
            notes.append("stop at or above the premium ignored")
            stop = previous.get("stop")
        elif stop is not None and stop <= floor:
            notes.append("stop below the safety stop dropped")
            stop = None
        if target is not None and target <= price:
            notes.append("target at or below the premium ignored")
            target = previous.get("target")
        row = {"stop": None if stop is None else round(stop, 2), "target": None if target is None else round(target, 2),
               "conviction": conviction or previous.get("conviction"),
               "reason": previous.get("reason") if keep_reason and previous.get("reason") else reason,
               "updated_at": now.isoformat()}
        self.grok_levels[symbol] = row
        with self._db_lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO smart_grok_levels VALUES (?,?,?,?,?,?,?)",
                             (position.position_id, symbol, row["stop"], row["target"], row["conviction"],
                              row["reason"], row["updated_at"]))
        return {"stop": row["stop"], "target": row["target"], "notes": notes}

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
                position.entry_anchor = float(row.get('entry_anchor') or position.entry_anchor or position.average_price)
                position.entry_stage = int(row.get('entry_stage') or 0)
                position.exit_stage = int(row.get('exit_stage') or 0)
                if not position.entry_fees:
                    position.entry_fees = float(row.get('entry_fees') or 0.)
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

    def set_resolvers(self, *, macd_confirm=None, dispersion=None, entry_context=None, whale=None,
                      market_frame=None) -> None:
        if market_frame is not None:
            self._market_frame = market_frame
        if whale is not None:
            self._whale = whale
        if macd_confirm is not None:
            self._macd_confirm = macd_confirm
        if dispersion is not None:
            self._dispersion = dispersion
        if entry_context is not None:
            self._entry_context = entry_context

    def held_symbols(self) -> list[str]:
        return [symbol for symbol, position in self.portfolio.positions.items() if position.quantity > 0]

    # ----------------------------------------------------------------- intake

    def on_macd_signal(self, signal: Signal, option_type: str, features: dict | None,
                       now: datetime | None = None) -> None:
        """A MACD-lane BUY: scored now, re-checked smart_entry_delay_seconds later."""
        now = now or datetime.now(UTC)
        if signal.side != 'BUY' or option_type not in ('CE', 'PE'):
            return
        if self.grok is not None and self.grok.index_only and not smart_grok.is_index_option(signal.symbol):
            return                       # the Rs 1 L Grok book takes index signals only (owner, 18 Nov 2026)
        if any(p.source == MACD_SOURCE and p.symbol == signal.symbol and
               (p.signal.signal_id == signal.signal_id or p.bucket == signal.evaluated_candle_timestamp
                and p.bucket is not None) for p in self._pending):
            return
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
        if self.grok is not None:
            await self.grok.tick(now)
        due = [p for p in self._pending if p.due <= now and (p.retry_at is None or p.retry_at <= now)]
        if not due:
            return 0
        if (now - min(p.due for p in due)).total_seconds() < self.settings.smart_rank_seconds:
            return 0
        self._pending = [p for p in self._pending if p not in due]
        for item in due:
            item.retry_at = None
            self._allocate(item, now)
        # Whale-interesting underlyings first (owner, 3 Nov 2026), then the existing order.
        ordered = sorted(due, key=lambda p: (p.detail.get('whale') is None, p.source != MACD_SOURCE,
            -p.detail['allocation']['priority'], p.created, p.symbol))
        cut = self.cutoff()
        for item in ordered:
            try:
                await self._decide(item, now, cut)
                if item.retry_at is not None:
                    self._pending.append(item)
            except Exception as exc:  # noqa: BLE001 -- one bad entry must not stop the batch
                self.error = f"{item.symbol}: {type(exc).__name__}: {exc}"[:300]
                self._record(item, ENTRY_REJECTED, now, cut=cut, detail={"error": str(exc)[:200]})
        self.last_batch_at = now.isoformat()
        return len(ordered)

    def _allocate(self, item: Pending, now: datetime) -> None:
        """Read shared live context once per attempt; never regenerate a MACD signal/crowd tally."""
        try:
            context = self._entry_context(item, now) or {}
        except Exception as exc:
            context = {'error': type(exc).__name__}
        equity = self.portfolio.cash + sum(p.last_price * p.quantity for p in self.portfolio.positions.values())
        underlying = (self._contracts.get(item.symbol) or {}).get('spot_symbol')
        exposure = sum(p.last_price * p.quantity for symbol, p in self.portfolio.positions.items()
                       if underlying and (self._contracts.get(symbol) or self.meta.get(symbol) or {}).get('spot_symbol') == underlying)
        allocation = smart_intelligence.evaluate(
            research_percentile=score_percentile(item.score, self._scores) if item.score is not None else None,
            option_type=item.option_type, breadth=self._dispersion(), live=context.get('live'),
            exposure_fraction=exposure / equity if equity > 0 else 0.)
        allocation.update(observed_at=now.isoformat(), context=context)
        item.detail['allocation'] = allocation
        try:
            whale = self._whale(item.symbol)
        except Exception:  # noqa: BLE001 -- context only
            whale = None
        item.detail['whale'] = ({k: whale[k] for k in ("symbol", "leg", "multiple", "strength", "at", "age_minutes")}
                                if whale else None)
        self.local_allocation = allocation

    async def _make_room(self, incoming: str) -> dict | None:
        """Close the weakest holding (lowest return on its entry price) to free one slot; what was closed."""
        execution = self.execution
        pending = {order.symbol for order in execution.orders.values() if order.status == "OPEN"}
        weakest = None
        for symbol, position in self.portfolio.positions.items():
            if (position.quantity <= 0 or symbol == incoming or symbol in pending
                    or symbol in execution._armed_exits or not position.average_price):
                continue
            try:
                price = execution._fresh_price(symbol)
            except RuntimeError:
                continue                       # no executable quote: it cannot be sold now
            change = price / position.average_price - 1
            if weakest is None or change < weakest[1]:
                weakest = (symbol, change, price, position.quantity)
        if weakest is None:
            return None
        symbol, change, price, quantity = weakest
        execution.arm_exit(symbol, WHALE_REPLACE_EXIT)
        try:
            order = await execution.submit(symbol, "SELL", quantity)
        except (ValueError, RuntimeError, PermissionError) as exc:
            execution._armed_exits.discard(symbol)
            self.error = f"replace {symbol}: {type(exc).__name__}: {exc}"[:300]
            return None
        left = self.portfolio.positions.get(symbol)
        if order.status != "FILLED" or (left is not None and left.quantity > 0):
            return None
        return {"replaced": symbol, "replaced_return_pct": round(100 * change, 2), "replaced_exit_price": price}

    def _liquidity_lots(self, symbol: str, lot_size: int) -> int | None:
        contract = self._contracts.get(symbol) or {}
        liquidity = max(float(contract.get("volume") or 0), float(contract.get("oi") or 0) / 100.0)
        if liquidity <= 0 or lot_size < 1:
            return None
        return int(liquidity * (self.settings.blast_max_volume_share_pct / 100.0) // lot_size)

    async def _decide(self, item: Pending, now: datetime, cut: float) -> None:
        symbol = item.symbol
        execution = self.execution
        if symbol in self.portfolio.positions:
            return self._record(item, ALREADY_HELD, now, cut=cut)
        blocked = execution.entry_block(symbol)       # NOT_ATM / STOCK_HELD (owner, 4 Nov 2026)
        if blocked:
            execution.note_entry_block(symbol, blocked)
            return self._record(item, blocked, now, cut=cut)
        if (now - item.due).total_seconds() > STALE_SECONDS:
            return self._record(item, NO_PRICE if item.detail.get('quote_retries') else STALE, now, cut=cut,
                                detail={'reason': 'Original admission deadline elapsed; no stale fill'})
        local = now.astimezone(IST)
        if not sessions.in_session(symbol, now):          # F&O continuous to 15:40 since Aug 2026 (sessions.py)
            return self._record(item, SESSION_CLOSED, now, cut=cut)
        if symbol not in execution.tradable_symbols or execution.lot_sizes.get(symbol, 0) < 1:
            return self._record(item, NOT_TRADABLE, now, cut=cut)
        try:
            price = execution._fresh_price(symbol)
        except RuntimeError:
            item.retry_at = now + timedelta(seconds=5)
            item.detail['quote_retries'] = item.detail.get('quote_retries', 0) + 1
            if item.detail['quote_retries'] == 1:
                self._record(item, WAITING_PRICE, now, cut=cut,
                             detail={'reason': 'Waiting for a fresh shared quote; original deadline unchanged'})
            return
        if 'allocation' not in item.detail:
            self._allocate(item, now)
        size = 1.0
        if item.source == MACD_SOURCE:
            if self._macd_confirm(symbol, item.bucket, price) is not True:
                return self._record(item, MACD_FADED, now, price=price, cut=cut)
            if self.grok is not None:
                held = len(self.held_symbols())
                if held >= self.grok.slots:
                    return self._record(item, POSITION_LIMIT, now, price=price, cut=cut,
                                        detail={"held": held, "slots": self.grok.slots})
                self.grok.enqueue(item, price, now)          # Grok takes or skips it in the next batch
                return None
            base =dispersion_size(item.option_type, self._dispersion(), self.settings.smart_min_dispersion_size)
            ranked = score_size(item.score, self._scores, getattr(self.settings, "smart_min_score_size", 0.25))
            if item.detail.get('whale'):
                ranked = 1.0          # owner, 3 Nov 2026: no score cut-down on a whale-interesting underlying
            item.detail["score_size"] = round(ranked, 4)
            size = base * ranked
        else:
            base = size
        lot = execution.lot_sizes[symbol]
        estimated = self._estimate_buy(symbol, price)
        per_lot = estimated * lot
        item.detail['estimated_buy_price'] = estimated
        equity = self.portfolio.cash + sum(p.last_price * p.quantity for p in self.portfolio.positions.values())
        if self.slots:
            held = len(self.held_symbols())
            if held >= self.slots and item.detail.get('whale'):
                # Owner, 3 Nov 2026: a whale-interesting signal on a full book takes the weakest holding's slot.
                room = await self._make_room(item.symbol)
                if room:
                    item.detail.update(room)
                    held = len(self.held_symbols())
            if held >= self.slots:
                return self._record(item, POSITION_LIMIT, now, price=price, cut=cut, size=size,
                                    detail={"held": held, "slots": self.slots})
            share = self.portfolio.cash / (self.slots - held)     # available capital, shared by the free slots
        else:
            share = self.settings.smart_ticket_fraction * equity
        ticket = share * size
        item.detail["share"] = round(share, 2)
        lots = int(min(ticket, self.portfolio.cash) // per_lot)
        if lots < 1 and per_lot <= min(share, self.portfolio.cash):
            lots = 1                  # rank/breadth round to one affordable lot, within the unscaled share
        cap = self._liquidity_lots(symbol, lot)
        if lots >= 1 and cap is not None and cap < lots:
            lots = cap
            if lots < 1:
                return self._record(item, TOO_ILLIQUID, now, price=price, cut=cut, size=size, ticket=ticket)
        if lots < 1:
            decision = TOO_SMALL if per_lot > ticket and self.portfolio.cash >= per_lot else NO_CASH
            return self._record(item, decision, now, price=price, cut=cut, size=size, ticket=ticket,
                                detail={"one_lot": round(per_lot, 2)})
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
        self._store_meta(item, item.source, now, size, journal_id)

    def _estimate_buy(self, symbol: str, price: float) -> float:
        """Reserve the larger of adverse LTP slippage and the shared quoted ask.

        Budgeting only LTP used to reject otherwise affordable fills at the ask."""
        execution = self.execution
        estimated = price * (1 + self.settings.slippage_bps / 10_000)
        tick = execution.quote_source(symbol) if execution.quote_source and self.settings.paper_cross_spread else None
        bid, ask = getattr(tick, 'bid', None), getattr(tick, 'ask', None)
        if (tick and abs(float(tick.ltp) - price) <= 1e-9 and bid and ask and
                math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
            estimated = max(estimated, min(ask, price * (1 + self.settings.paper_max_half_spread_pct / 100)))
        return estimated

    async def _grok_enter(self, item: Pending, queued_price: float, pick: dict, now: datetime) -> None:
        """Buy a signal Grok took: the structural checks again, a drift check, then the fixed ticket in whole lots."""
        symbol = item.symbol
        execution = self.execution
        grok = self.grok
        if grok is None:
            return
        if symbol in self.portfolio.positions:
            return self._record(item, ALREADY_HELD, now)
        blocked = execution.entry_block(symbol)
        if blocked:
            execution.note_entry_block(symbol, blocked)
            return self._record(item, blocked, now)
        if not sessions.in_session(symbol, now):
            return self._record(item, SESSION_CLOSED, now)
        if symbol not in execution.tradable_symbols or execution.lot_sizes.get(symbol, 0) < 1:
            return self._record(item, NOT_TRADABLE, now)
        try:
            price = execution._fresh_price(symbol)
        except RuntimeError:
            return self._record(item, NO_PRICE, now, detail={"reason": "no fresh quote when Grok's answer came"})
        drift = self.settings.smart_grok_max_drift_pct
        if queued_price > 0 and price > queued_price * (1 + drift):
            return self._record(item, smart_grok.GROK_PRICE_MOVED, now, price=price,
                                detail={"queued_price": queued_price, "max_drift_pct": drift * 100})
        held = len(self.held_symbols())
        if held >= grok.slots:
            return self._record(item, POSITION_LIMIT, now, price=price, detail={"held": held, "slots": grok.slots})
        lot = execution.lot_sizes[symbol]
        per_lot = self._estimate_buy(symbol, price) * lot
        equity = self.portfolio.cash + sum(p.last_price * p.quantity for p in self.portfolio.positions.values())
        ticket = grok.ticket(equity)
        cash = self.portfolio.cash
        lots = int(min(ticket, cash) // per_lot)
        if lots < 1 and per_lot <= min(ticket * self.settings.smart_grok_one_lot_stretch, cash):
            lots = 1                  # one lot a little above the ticket is still the trade Grok chose
        cap = self._liquidity_lots(symbol, lot)
        if lots >= 1 and cap is not None and cap < lots:
            lots = cap
            if lots < 1:
                return self._record(item, TOO_ILLIQUID, now, price=price, ticket=ticket)
        if lots < 1:
            decision = TOO_SMALL if cash >= per_lot else NO_CASH
            return self._record(item, decision, now, price=price, ticket=ticket, detail={"one_lot": round(per_lot, 2)})
        try:
            order = await execution.submit(symbol, "BUY", lots=lots, signal=item.signal)
        except (ValueError, RuntimeError, PermissionError) as exc:
            return self._record(item, ENTRY_REJECTED, now, price=price, ticket=ticket, detail={"error": str(exc)[:200]})
        if order.status != "FILLED":
            return self._record(item, ENTRY_REJECTED, now, price=price, ticket=ticket, detail={"status": order.status})
        journal_id = self._record(item, TAKEN, now, price=order.fill_price, size=1.0, ticket=ticket, lots=lots,
                                  order_id=order.order_id)
        self._store_meta(item, GROK_SOURCE, now, 1.0, journal_id)
        position = self.portfolio.positions.get(symbol)
        if position is not None:          # the fill set the MACD-source stop; this book's safety stop is wider
            position.hard_stop = round(position.average_price * (1 - self.settings.smart_grok_hard_stop_pct), 4)
        levels =self.set_grok_levels(symbol, pick.get("stop_premium"), pick.get("target_premium"),
                                      conviction=pick.get("conviction"), reason=pick.get("reason"), now=now)
        if levels.get("notes"):
            self.error = f"{symbol} levels: {'; '.join(levels['notes'])}"[:300]

    def _store_meta(self, item: Pending, source: str, now: datetime, size: float, journal_id: str) -> None:
        symbol = item.symbol
        position = self.portfolio.positions.get(symbol)
        contract = self._contracts.get(symbol) or {}
        meta = {"position_id": position.position_id if position else f"{symbol}|{now.isoformat()}",
                "symbol": symbol, "source": source, "opened_at": now.isoformat(),
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
                "source": (sources.get(pid) or {}).get("source", MACD_SOURCE),
                "side": row.get("side") or "LONG", "underlying": (sources.get(pid) or {}).get("spot_symbol")})
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
            "by_source": {src: stats([t for t in trades if t["source"] == src]) for src in (MACD_SOURCE, BLAST_SOURCE, GROK_SOURCE)},
            "by_exit_reason": {k: stats(v) for k, v in sorted(by_reason.items())},
            "decisions": decisions,
            "decisions_by_source": {f"{s}:{d}": n for (s, d), n in by_source.items()},
        }

    def equity_curve(self, limit: int = 3000, since: str | None = None) -> list[dict]:
        rows = self.repository.equity_rows(limit, since)
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
            levels = self.grok_levels.get(row["symbol"])
            if levels:
                row["target_price"] = levels.get("target")
                row["stop_price"] = levels.get("stop")
                row["grok"] = levels
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
                "local_model": {"mode":"off","model":None,"required":False},
                "intelligence": {"policy":smart_intelligence.VERSION,"mode":"local","qwen_required":False,
                    "last_allocation":self.local_allocation,"weights":smart_intelligence.WEIGHTS,
                    "quote_retry_seconds":5,"admission_deadline_seconds":STALE_SECONDS,
                    "score_cutoff_active":False,"size_policy":("available cash / free slots" if self.slots else "equity ticket")
                        + (" × research rank" if self.settings.smart_min_score_size < 1 else "")
                        + " × side breadth; whole lots within the share and cash",
                    "validation":"prospective profitability not proven"},
                "rules": {
                    "entry_delay_seconds": self.settings.smart_entry_delay_seconds,
                    "skip_fraction": self.settings.smart_skip_fraction,
                    "ticket_fraction": None if self.slots else self.settings.smart_ticket_fraction,
                    "slots": self.slots or None,
                    "sizing": ("available cash / free slots x side breadth" if self.slots
                               else "ticket fraction of equity x side breadth"),
                    "macd": self.execution.exit_rules(MACD_SOURCE),
                    "blast": self.execution.exit_rules(BLAST_SOURCE) if self.settings.smart_blast_source else None,
                    "liquidity_share_pct": self.settings.blast_max_volume_share_pct,
                },
                "model": {"trained_on": MODEL["trained_on"], "walk_forward": MODEL["walk_forward"]},
                "grok": self.grok.health() if self.grok is not None else {"enabled": False}}

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
        slots = lane_slots(getattr(settings, "smart_lane_slots_csv", ""))
        for raw in str(settings.smart_lane_capitals_csv).split(","):
            raw = raw.strip()
            if not raw:
                continue
            capital = float(raw)
            name = f"smart_{lane_label(capital)}"
            self.lanes.append(SmartLane(name, capital, settings, broker, events, str(base / f"{name}.sqlite3"),
                                        slots=slots.get(capital, 0)))
            self.lanes[-1].configure_grok(grok_enabled(settings, name), str(RUNTIME_DIR))

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
        slots = lane_slots(getattr(settings, "smart_lane_slots_csv", ""))
        for lane in self.lanes:
            lane.slots = slots.get(lane.capital, 0)
            lane.apply_settings(settings)
            lane.configure_grok(grok_enabled(settings, lane.name), str(RUNTIME_DIR))

    def rebind_broker(self, broker) -> None:
        for lane in self.lanes:
            lane.broker.rebind(broker)

    def health(self) -> dict:
        return {lane.name: lane.health() for lane in self.lanes}


def lane_slots(text: str) -> dict[float, int]:
    """'100000:3,5000000:25' -> {100000.0: 3, 5000000.0: 25}; malformed pairs are ignored."""
    out: dict[float, int] = {}
    for pair in str(text or "").split(","):
        capital, _, slots = pair.partition(":")
        try:
            if int(slots) > 0:
                out[float(capital)] = int(slots)
        except ValueError:
            continue
    return out


def lane_label(capital: float) -> str:
    if capital >= 1e7 and capital % 1e7 == 0:
        return f"{int(capital // 1e7)}cr"
    if capital >= 1e5 and capital % 1e5 == 0:
        return f"{int(capital // 1e5)}l"
    if capital >= 1e3 and capital % 1e3 == 0:
        return f"{int(capital // 1e3)}k"
    return str(int(capital))


def grok_enabled(settings, name: str) -> bool:
    """Grok picks and exits for this book (owner, 18 Nov 2026: both smart books, live from 19 Nov)."""
    names = {n.strip() for n in str(getattr(settings, "smart_grok_lanes_csv", "")).split(",") if n.strip()}
    return bool(getattr(settings, "smart_grok_enabled", False)) and name in names
