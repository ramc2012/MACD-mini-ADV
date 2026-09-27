from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Tick:
    symbol: str
    ltp: float
    volume: int = 0
    timestamp: datetime = field(default_factory=utc_now)
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    # Microstructure fields (Fyers SymbolUpdate, non-lite). They are optional
    # because the simulation broker and warm-up paths do not supply them.
    # bid/ask enable quote-rule aggressor classification, which is what makes
    # order flow real rather than a tick-rule guess.
    bid: float | None = None
    ask: float | None = None
    bid_qty: int | None = None
    ask_qty: int | None = None
    last_qty: int | None = None
    total_buy_qty: int | None = None
    total_sell_qty: int | None = None
    open_interest: int | None = None
    avg_trade_price: float | None = None


@dataclass(slots=True)
class Candle:
    symbol: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    closed: bool = False


@dataclass(slots=True)
class IndicatorPoint:
    symbol: str
    timestamp: int
    macd: float
    signal: float
    histogram: float
    bb_middle: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    bb_width: float | None = None
    kama: float | None = None
    kama_rsi: float | None = None
    kama_roc: float | None = None


@dataclass(slots=True)
class Signal:
    symbol: str
    side: Literal["BUY", "SELL"]
    kind: str
    price: float
    macd: float
    signal: float
    histogram: float
    timestamp: datetime = field(default_factory=utc_now)
    signal_id: str = field(default_factory=lambda: uuid4().hex)
    # The bar being evaluated, distinct from `timestamp`, which is the exact
    # wall-clock time at which the paper signal was emitted.
    evaluated_candle_timestamp: int | None = None


@dataclass(slots=True)
class Order:
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    lots: int = 1
    lot_size: int = 1
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: float | None = None
    status: str = "NEW"
    fill_price: float | None = None
    broker_order_id: str | None = None
    signal_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    order_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(slots=True)
class Trade:
    order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    price: float
    lots: int = 1
    lot_size: int = 1
    fees: float = 0.0
    timestamp: datetime = field(default_factory=utc_now)
    trade_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(slots=True)
class Position:
    symbol: str
    quantity: int
    average_price: float
    last_price: float
    opened_at: datetime = field(default_factory=utc_now)
    peak_price: float = 0.0
    hard_stop: float = 0.0
    trailing_stop: float | None = None
    lot_size: int = 1
    entry_anchor: float = 0.0
    entry_stage: int = 1
    exit_stage: int = 0
    # Excursion since open (MFE/MAE): raw price extremes plus running extremes
    # of the instantaneous return, so a pyramid that moves average_price does
    # not rewrite the path already travelled. peak_price stays separate: it is
    # session-gated and arms the trailing stop; these are valuation-only and
    # also see startup and off-session marks.
    max_price: float = 0.0
    min_price: float = 0.0
    max_return_pct: float = 0.0
    min_return_pct: float = 0.0
    # Entry-leg brokerage still parked in the open quantity, attributed
    # pro-rata to each closing slice (MP statistics() does the same). Always
    # 0.0 on the MACD lane today — see ExecutionManager._paper_fill.
    entry_fees: float = 0.0

    @property
    def lots(self) -> int:
        return abs(self.quantity) // self.lot_size if self.lot_size > 0 else 0

    @property
    def unrealized_pnl(self) -> float:
        return (self.last_price - self.average_price) * self.quantity

    @property
    def position_id(self) -> str:
        # Stable across restarts: opened_at is replayed from the first fill's
        # timestamp, which round-trips exactly through isoformat.
        return f"{self.symbol}|{self.opened_at.isoformat()}"

    @property
    def return_pct(self) -> float:
        if self.average_price <= 0:
            return 0.0
        direction = 1.0 if self.quantity >= 0 else -1.0
        return direction * (self.last_price / self.average_price - 1.0) * 100.0

    def track_excursion(self, price: float) -> None:
        """Fold one print into MFE/MAE. Idempotent for a repeated price."""
        if price <= 0 or self.average_price <= 0:
            return
        if self.max_price <= 0 or self.min_price <= 0:
            self.max_price = self.min_price = price
        self.max_price = max(self.max_price, price)
        self.min_price = min(self.min_price, price)
        direction = 1.0 if self.quantity >= 0 else -1.0
        ret = direction * (price / self.average_price - 1.0) * 100.0
        self.max_return_pct = max(self.max_return_pct, ret)
        self.min_return_pct = min(self.min_return_pct, ret)

    def reset_excursion(self, price: float) -> None:
        self.max_price = self.min_price = price
        self.max_return_pct = self.min_return_pct = 0.0

    def payload(self) -> dict:
        value = asdict(self)
        value["lots"] = self.lots
        value["unrealized_pnl"] = self.unrealized_pnl
        value["return_pct"] = self.return_pct
        value["position_id"] = self.position_id
        return value


@dataclass(slots=True)
class ClosedPosition:
    """One closing slice of a paper position.

    A staged exit produces one record per SELL fill (``partial`` until the
    last lot goes); consumers group them by ``position_id``. Fees are the
    slice's own share — entry brokerage pro-rata by quantity plus the exit
    leg — so the records bridge to the cash-basis realized_pnl the same way
    MP statistics() documents.
    """
    position_id: str
    symbol: str
    lane: str
    side: Literal["LONG", "SHORT"]
    quantity: int
    lots: int
    lot_size: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    gross_pnl: float
    fees: float
    realized_pnl: float
    return_pct: float
    max_price: float
    min_price: float
    max_return_pct: float
    min_return_pct: float
    exit_reason: str | None
    partial: bool
    remaining_quantity: int
    exit_trade_id: str
    # 08:00 IST on the next weekday after the exit; the desk hides the record
    # past this moment but the repository keeps it for exit research.
    visible_until: datetime
    closed_id: str = field(default_factory=lambda: uuid4().hex)
