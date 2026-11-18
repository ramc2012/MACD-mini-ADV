"""MCX option selling: a separate paper book that writes today's ATM options.

Owner, 17 Nov 2026: "implement options selling with opposite rule for buying
with margin rules as prescribed by mcx". Chosen with the owner:

* **Entries (both triggers).** (1) A premium MACD zero-cross DOWN of a selected
  ATM option itself, the mirror of the buy entry, evaluated on the forming
  bar and on the close, once per bar. (2) The opposite leg of every buy
  signal: a call's premium crossing UP writes the put of that commodity, and
  the reverse.
* **Its own book.** Own capital, one short at a time
  (``mcx_short_max_positions``), whole lots whose margin fits under
  ``mcx_short_ticket``. The buying lane is unchanged.
* **Margin, SPAN-style.** MCXCCL charges short options SPAN (the worst of 16
  scenarios of futures price and implied volatility, with the two extreme
  moves counted at a fraction, floored by a short option minimum charge) plus
  an extreme loss margin. The real parameters come from MCXCCL's daily risk
  files; this book uses editable estimates (price scan range per commodity,
  volatility scan, minimum charge, ELM) and Black-76 on the futures price.
  Margin is recomputed on every maintenance pass; free margin = equity - margin.
* **Exits (the buy rules mirrored).** Buy back on the option's own premium
  cross UP; on a short trigger on the other side of the same commodity (call
  vs put, full-size or mini), which then takes the slot; at the MACD lane's
  hard stop (premium up 30%); on the trail (armed once the premium has fallen
  30%, at 25% above its low, never above breakeven); and from the expiry exit
  time on expiry day (an MCX option devolves into futures).

Paper only: nothing here can place an order. Fills take the quote side (owner,
18 Nov 2026): a sell to open at the latest bid, a cover at the ask (the lane's
execution.estimate_fill), on a websocket price no older than
``order_quote_max_age_seconds``; an exit without a fresh price waits for the next tick.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from zoneinfo import ZoneInfo

from .models import Candle, Signal

if TYPE_CHECKING:  # pragma: no cover
    from .mcx_lane import McxContract, McxLane

IST = ZoneInfo("Asia/Kolkata")
YEAR_SECONDS = 365 * 86400
MIN_T_SECONDS = 3600
MIN_VOL, MAX_VOL = 0.005, 5.0

# Verdicts for a short trigger.
SHORT_TAKEN = "SHORT_TAKEN"
SHORT_AUTO_OFF = "SHORT_AUTO_OFF"
ALREADY_SHORT = "ALREADY_SHORT"
SHORT_FULL = "SHORT_FULL"
NO_PRICE = "NO_PRICE"
NO_FUTURES = "NO_FUTURES"
MARGIN_ABOVE_TICKET = "MARGIN_ABOVE_TICKET"
NO_MARGIN = "NO_MARGIN"
SESSION_CLOSED = "SESSION_CLOSED"
NOT_SELECTED = "NOT_SELECTED"
EXPIRY_DAY = "EXPIRY_DAY"

CROSS_DOWN = "PREMIUM_MACD_ZERO_CROSS_DOWN"
OPPOSITE_LEG = "OPPOSITE_LEG_OF_BUY"

# Exit reasons.
COVER_CROSS_UP = "SHORT_PREMIUM_CROSS_UP"
COVER_OPPOSITE = "SHORT_OPPOSITE_TRIGGER"
COVER_HARD_STOP = "SHORT_HARD_STOP"
COVER_TRAIL = "SHORT_TRAIL"
COVER_MANUAL = "MANUAL"


# ------------------------------------------------------------------ pricing

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76(futures: float, strike: float, years: float, vol: float, kind: str) -> float:
    """Undiscounted Black-76 value of an option on a futures price."""
    if futures <= 0 or strike <= 0:
        return 0.0
    intrinsic = max(futures - strike, 0.0) if kind == "CE" else max(strike - futures, 0.0)
    if years <= 0 or vol <= 0:
        return intrinsic
    root = vol * math.sqrt(years)
    d1 = (math.log(futures / strike) + 0.5 * vol * vol * years) / root
    d2 = d1 - root
    if kind == "CE":
        return futures * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - futures * _norm_cdf(-d1)


def implied_vol(premium: float, futures: float, strike: float, years: float, kind: str) -> float:
    """Volatility that prices the premium (bisection, clamped to 0.5%..500%)."""
    if premium <= black76(futures, strike, years, MIN_VOL, kind):
        return MIN_VOL
    if premium >= black76(futures, strike, years, MAX_VOL, kind):
        return MAX_VOL
    low, high = MIN_VOL, MAX_VOL
    for _ in range(80):
        mid = 0.5 * (low + high)
        if black76(futures, strike, years, mid, kind) < premium:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


@dataclass(frozen=True, slots=True)
class MarginParams:
    price_scan_pct: float
    volatility_scan_pct: float
    extreme_cover: float
    min_charge_pct: float
    elm_pct: float


def parse_scan_csv(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in (text or "").split(","):
        if ":" not in item:
            continue
        root, value = item.split(":", 1)
        try:
            number = float(value)
        except ValueError:
            continue
        if root.strip() and 0 < number <= 100:
            out[root.strip().upper()] = number
    return out


def span_margin(futures: float, strike: float, years: float, premium: float, kind: str, units: int,
                params: MarginParams) -> dict:
    """SPAN-style margin for ONE short lot: 16 scenarios + short option minimum + ELM."""
    vol = implied_vol(premium, futures, strike, years, kind)
    scan = params.price_scan_pct / 100.0 * futures
    shift = params.volatility_scan_pct / 100.0
    worst = 0.0
    for fraction in (0.0, 1 / 3, 2 / 3, 1.0):
        for sign in ((1,) if fraction == 0 else (1, -1)):
            for scenario_vol in (vol * (1 + shift), max(MIN_VOL, vol * (1 - shift))):
                moved = max(futures + sign * fraction * scan, 0.01 * futures)
                worst = max(worst, black76(moved, strike, years, scenario_vol, kind) - premium)
    for sign in (1, -1):
        moved = max(futures + sign * 2 * scan, 0.01 * futures)
        worst = max(worst, params.extreme_cover * (black76(moved, strike, years, vol, kind) - premium))
    scanning = worst * units
    minimum = params.min_charge_pct / 100.0 * futures * units
    span = max(scanning, minimum)
    elm = params.elm_pct / 100.0 * futures * units
    return {"iv": round(vol, 4), "scanning_risk": round(scanning, 2), "short_option_minimum": round(minimum, 2),
            "span": round(span, 2), "elm": round(elm, 2), "total": round(span + elm, 2)}


# --------------------------------------------------------------------- book

@dataclass(slots=True)
class ShortPosition:
    short_id: str
    symbol: str
    root: str
    option_type: str
    strike: float
    expiry: str
    lots: int
    lot_size: int
    quantity: int
    entry_price: float
    entry_at: str
    trigger: str
    margin: float              # total margin at entry
    margin_now: float
    iv: float
    last_price: float
    low_price: float
    high_price: float
    status: str = "OPEN"
    exit_price: float | None = None
    exit_at: str | None = None
    exit_reason: str | None = None
    pnl: float = 0.0

    @property
    def unrealized(self) -> float:
        return (self.entry_price - self.last_price) * self.quantity if self.status == "OPEN" else 0.0


class ShortStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS shorts (short_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
                       "entry_at TEXT NOT NULL, payload TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def save(self, position: ShortPosition) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO shorts (short_id, status, entry_at, payload) VALUES (?, ?, ?, ?)",
                       (position.short_id, position.status, position.entry_at, json.dumps(asdict(position))))

    def load(self) -> list[ShortPosition]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT payload FROM shorts ORDER BY entry_at").fetchall()
        out = []
        for (payload,) in rows:
            try:
                out.append(ShortPosition(**json.loads(payload)))
            except (TypeError, ValueError):
                continue
        return out


class McxShortBook:
    """The selling book. Driven by the lane: its ticks, bars, buy signals and maintenance pass."""

    def __init__(self, lane: "McxLane", path: str, commodity: Callable[[str], str]):
        self.lane = lane
        self.commodity = commodity
        self.store = ShortStore(path)
        rows = self.store.load()
        self.open: dict[str, ShortPosition] = {row.symbol: row for row in rows if row.status == "OPEN"}
        self.closed: list[ShortPosition] = [row for row in rows if row.status != "OPEN"]
        self.pending: dict[str, str] = {}            # symbol -> exit reason waiting for a fresh price
        self.decisions: deque[dict] = deque(maxlen=200)
        self._fired: set[tuple[str, int]] = set()
        self.errors: dict[str, str] = {}

    # ------------------------------------------------------------ settings

    @property
    def settings(self):
        return self.lane.settings

    def params_for(self, root: str) -> MarginParams:
        scans = parse_scan_csv(self.settings.mcx_short_price_scan_csv)
        scan = scans.get(self.commodity(root), scans.get(root, self.settings.mcx_short_default_price_scan_pct))
        return MarginParams(scan, self.settings.mcx_short_volatility_scan_pct, self.settings.mcx_short_extreme_cover,
                            self.settings.mcx_short_min_charge_pct, self.settings.mcx_short_elm_pct)

    # -------------------------------------------------------------- prices

    def fresh_price(self, symbol: str, now: datetime | None = None) -> float | None:
        tick = self.lane.latest.get(symbol)
        seen = self.lane._last_ws_at.get(symbol)
        if tick is None or seen is None or not tick.ltp or tick.ltp <= 0:
            return None
        age = ((now or datetime.now(UTC)) - seen).total_seconds()
        return tick.ltp if -5 <= age <= self.settings.order_quote_max_age_seconds else None

    def years_to_expiry(self, expiry: str, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        try:
            day = date.fromisoformat(expiry)
        except ValueError:
            return MIN_T_SECONDS / YEAR_SECONDS
        closes = self.lane.calendar.closes_at(day) if hasattr(self.lane.calendar, "closes_at") else time(23, 30)
        end = datetime.combine(day, closes, IST)
        return max((end - now).total_seconds(), MIN_T_SECONDS) / YEAR_SECONDS

    def margin_per_lot(self, contract: "McxContract", premium: float, now: datetime | None = None) -> dict | None:
        future = self.lane.latest.get(contract.futures_symbol)
        if future is None or not future.ltp or future.ltp <= 0 or premium <= 0 or contract.lot_size < 1:
            return None
        return span_margin(future.ltp, contract.strike, self.years_to_expiry(contract.expiry, now), premium,
                           contract.option_type, contract.lot_size, self.params_for(contract.root))

    # ------------------------------------------------------------ accounts

    def realized(self) -> float:
        return sum(row.pnl for row in self.closed)

    def unrealized(self) -> float:
        return sum(row.unrealized for row in self.open.values())

    def margin_used(self) -> float:
        return sum(row.margin_now for row in self.open.values())

    def equity(self) -> float:
        return self.settings.mcx_short_capital + self.realized() + self.unrealized()

    def free_margin(self) -> float:
        return self.equity() - self.margin_used()

    # ------------------------------------------------------------- triggers

    async def on_live_candle(self, candle: Candle) -> None:
        indicator = self.lane.strategy.indicators.get(candle.symbol)
        if indicator is None or indicator.count + 1 <= self.settings.slow_period:
            return
        value = indicator.preview(candle.close)
        previous = value.previous_macd
        if previous is not None and previous >= 0 > value.macd:
            await self._cross_down(candle.symbol, candle.timestamp, candle.close, value.macd)

    async def on_closed_candle(self, candle: Candle) -> None:
        indicator = self.lane.strategy.indicators.get(candle.symbol)
        points = self.lane.strategy.point_history.get(candle.symbol)
        if indicator is None or indicator.count <= self.settings.slow_period or not points or len(points) < 2:
            return
        previous, current = points[-2], points[-1]
        if current.timestamp == candle.timestamp and previous.macd >= 0 > current.macd:
            await self._cross_down(candle.symbol, candle.timestamp, candle.close, current.macd)

    async def _cross_down(self, symbol: str, bar: int, price: float, macd: float) -> None:
        key = (symbol, bar)
        if key in self._fired:
            return
        self._fired.add(key)
        if len(self._fired) > 5000:
            self._fired = {item for item in self._fired if item[1] >= bar - 86400}
        contract = self.lane.contracts.get(symbol)
        if contract is None:
            return
        await self.enter(contract, CROSS_DOWN, price=price, macd=macd, bar=bar)

    async def on_buy_signal(self, signal: Signal) -> None:
        """A premium cross UP: covers a short in that contract and writes the opposite leg."""
        if signal.side != "BUY":
            return
        if signal.symbol in self.open:
            await self.cover(signal.symbol, COVER_CROSS_UP)
        contract = self.lane.contracts.get(signal.symbol)
        if contract is None or contract.retained:
            return
        other = "PE" if contract.option_type == "CE" else "CE"
        leg = next((row for row in self.lane.contracts.values()
                    if row.root == contract.root and row.option_type == other and not row.retained), None)
        if leg is not None:
            await self.enter(leg, OPPOSITE_LEG, price=None, macd=signal.macd, bar=signal.evaluated_candle_timestamp,
                             source=signal.symbol)

    # ------------------------------------------------------------- entries

    async def enter(self, contract: "McxContract", trigger: str, *, price: float | None, macd: float | None,
                    bar: int | None, source: str | None = None) -> str:
        now = datetime.now(UTC)

        def decide(verdict: str, *, lots: int = 0, detail: str = "", margin: dict | None = None) -> str:
            row = {"at": now.isoformat(), "symbol": contract.symbol, "root": contract.root,
                   "option_type": contract.option_type, "trigger": trigger, "source": source, "verdict": verdict,
                   "price": price, "macd": macd, "bar": bar, "lots": lots, "detail": detail,
                   "margin_per_lot": (margin or {}).get("total")}
            self.decisions.appendleft(row)
            self.lane.events.publish("short_decision", row)
            return verdict

        if contract.retained:
            return decide(NOT_SELECTED, detail="not in today's selection; shorts use today's ATM legs only")
        if not self.lane.session_open():
            return decide(SESSION_CLOSED)
        today = now.astimezone(IST).date()
        try:
            if date.fromisoformat(contract.expiry) <= today:
                return decide(EXPIRY_DAY, detail="never written on its expiry day")
        except ValueError:
            pass
        closed = await self._cover_opposite(contract)
        note = f"bought back {', '.join(closed)} (opposite trigger)" if closed else ""
        if contract.symbol in self.open:
            return decide(ALREADY_SHORT, detail=note)
        if not self.settings.mcx_short_auto_trade:
            return decide(SHORT_AUTO_OFF, detail=note)
        if len(self.open) >= self.settings.mcx_short_max_positions:
            waiting = f"; buying back {', '.join(self.pending)} on its next fresh quote" if self.pending else ""
            return decide(SHORT_FULL, detail=f"holds {', '.join(self.open)} short{waiting}" + (f"; {note}" if note else ""))
        live = self.fresh_price(contract.symbol, now)
        if live is None:
            return decide(NO_PRICE, detail="no websocket price in the last "
                                           f"{self.settings.order_quote_max_age_seconds:g} s")
        price = live
        margin = self.margin_per_lot(contract, live, now)
        if margin is None:
            return decide(NO_FUTURES, detail=f"no price for {contract.futures_symbol}")
        per_lot = margin["total"]
        fit = int(self.settings.mcx_short_ticket // per_lot) if per_lot > 0 else 0
        if fit < 1:
            return decide(MARGIN_ABOVE_TICKET, margin=margin,
                          detail=f"one lot needs Rs {per_lot:,.0f} margin, above the Rs "
                                 f"{self.settings.mcx_short_ticket:,.0f} ticket")
        affordable = int(self.free_margin() // per_lot) if per_lot > 0 else 0
        if affordable < 1:
            return decide(NO_MARGIN, margin=margin, detail=f"free margin Rs {self.free_margin():,.0f}")
        lots = min(fit, affordable, self.settings.mcx_short_max_lots)
        fill = round(self._quote_side(contract.symbol, "SELL", live), 4)   # S1: the bid
        quantity = lots * contract.lot_size
        position = ShortPosition(
            short_id=uuid.uuid4().hex, symbol=contract.symbol, root=contract.root, option_type=contract.option_type,
            strike=contract.strike, expiry=contract.expiry, lots=lots, lot_size=contract.lot_size, quantity=quantity,
            entry_price=fill, entry_at=now.isoformat(), trigger=trigger, margin=round(per_lot * lots, 2),
            margin_now=round(per_lot * lots, 2), iv=margin["iv"], last_price=live, low_price=live, high_price=live,
        )
        self.open[contract.symbol] = position
        self.store.save(position)
        self.lane.events.publish("short_position", asdict(position))
        return decide(SHORT_TAKEN, lots=lots, margin=margin, detail=note)

    async def _cover_opposite(self, contract: "McxContract") -> list[str]:
        mine = self.commodity(contract.root)
        closed = []
        for symbol, position in list(self.open.items()):
            if self.commodity(position.root) == mine and position.option_type != contract.option_type:
                if await self.cover(symbol, COVER_OPPOSITE):
                    closed.append(symbol)
        return closed

    # --------------------------------------------------------------- exits

    def _quote_side(self, symbol: str, side: str, live: float) -> float:
        """Owner, 18 Nov 2026 (S1): the quote side of the lane's latest tick (execution.estimate_fill), else
        slippage_bps when the lane's book is not wired (tests)."""
        execution = getattr(self.lane, "execution", None)
        if execution is not None and hasattr(execution, "estimate_fill"):
            return execution.estimate_fill(symbol, side, live)
        slip = self.settings.slippage_bps / 10_000
        return live * (1 + slip) if side == "BUY" else live * (1 - slip)

    async def cover(self, symbol: str, reason: str, now: datetime | None = None) -> bool:
        """Buy a short back at the fresh price; without one, wait for the next tick."""
        position = self.open.get(symbol)
        if position is None:
            self.pending.pop(symbol, None)
            return False
        now = now or datetime.now(UTC)
        live = self.fresh_price(symbol, now)
        if live is None:
            self.pending[symbol] = reason
            return False
        fill = round(self._quote_side(symbol, "BUY", live), 4)            # S1: the ask
        position.status = "CLOSED"
        position.exit_price = fill
        position.exit_at = now.isoformat()
        position.exit_reason = reason
        position.last_price = live
        position.pnl = round((position.entry_price - fill) * position.quantity, 2)
        position.margin_now = 0.0
        del self.open[symbol]
        self.pending.pop(symbol, None)
        self.closed.append(position)
        self.store.save(position)
        self.lane.events.publish("short_position", asdict(position))
        return True

    def stop_levels(self, position: ShortPosition) -> tuple[float, float | None]:
        risk = self.lane.execution.settings
        hard = position.entry_price * (1 + risk.hard_stop_pct)
        trail = None
        if position.low_price <= position.entry_price * (1 - risk.trailing_activation_pct):
            breakeven = position.entry_price * (1 - 2 * risk.slippage_bps / 10_000)
            trail = min(position.low_price * (1 + risk.trailing_stop_pct), breakeven)
        return round(hard, 4), (round(trail, 4) if trail is not None else None)

    async def on_tick(self, symbol: str, price: float, *, tradable: bool, now: datetime | None = None) -> None:
        position = self.open.get(symbol)
        if position is None or price <= 0:
            return
        position.last_price = price
        position.low_price = min(position.low_price, price)
        position.high_price = max(position.high_price, price)
        if not tradable:
            return
        if symbol in self.pending:
            await self.cover(symbol, self.pending[symbol], now)
            return
        hard, trail = self.stop_levels(position)
        if price >= hard:
            await self.cover(symbol, f"{COVER_HARD_STOP}_{round(self.lane.execution.settings.hard_stop_pct * 100)}_PCT", now)
        elif trail is not None and price >= trail:
            await self.cover(symbol, f"{COVER_TRAIL}_{round(self.lane.execution.settings.trailing_stop_pct * 100)}_PCT", now)
        else:
            await self._exit_if_expiring(position, now)

    async def _exit_if_expiring(self, position: ShortPosition, now: datetime | None = None) -> None:
        cutoff = self.lane.flatten_time()
        local = (now or datetime.now(UTC)).astimezone(IST)
        if cutoff is None or (local.hour, local.minute) < cutoff or position.expiry > local.date().isoformat():
            return
        await self.cover(position.symbol, f"MCX_EXPIRY_FLATTEN_{cutoff[0]:02d}{cutoff[1]:02d}_IST", now)

    def settle_expired(self, today: date) -> None:
        """A short whose contract expired while the app was down is closed at its last mark."""
        for symbol, position in list(self.open.items()):
            if position.expiry >= today.isoformat():
                continue
            position.status, position.exit_price = "CLOSED", position.last_price
            position.exit_at, position.exit_reason = datetime.now(UTC).isoformat(), "EXPIRED_WHILE_OFFLINE_LAST_MARK"
            position.pnl = round((position.entry_price - position.last_price) * position.quantity, 2)
            position.margin_now = 0.0
            del self.open[symbol]
            self.pending.pop(symbol, None)
            self.closed.append(position)
            self.store.save(position)

    async def maintain(self, now: datetime | None = None) -> None:
        """Margin re-marked on the latest prices; waiting exits retried; expiry exits without a tick."""
        now = now or datetime.now(UTC)
        for symbol, position in list(self.open.items()):
            contract = self.lane.contracts.get(symbol)
            tick = self.lane.latest.get(symbol)
            premium = tick.ltp if tick is not None and tick.ltp > 0 else position.last_price
            if contract is not None:
                try:
                    margin = self.margin_per_lot(contract, premium, now)
                except Exception as exc:  # noqa: BLE001 - a pricing failure must not stop maintenance
                    self.errors["margin"] = f"{symbol}: {type(exc).__name__}: {exc}"[:200]
                    margin = None
                if margin is not None:
                    position.margin_now = round(margin["total"] * position.lots, 2)
                    position.iv = margin["iv"]
            if self.lane.session_open(now):
                if symbol in self.pending:
                    await self.cover(symbol, self.pending[symbol], now)
                elif symbol in self.open:
                    await self._exit_if_expiring(position, now)
        for position in self.open.values():
            self.store.save(position)

    # ------------------------------------------------------------- reports

    def view(self) -> dict:
        def row(position: ShortPosition) -> dict:
            data = asdict(position)
            hard, trail = self.stop_levels(position) if position.status == "OPEN" else (None, None)
            data.update(unrealized=round(position.unrealized, 2), hard_stop=hard, trail_stop=trail,
                        pending_exit=self.pending.get(position.symbol))
            return data

        today = datetime.now(IST).date().isoformat()
        return {
            "settings": {
                "auto_trade": self.settings.mcx_short_auto_trade,
                "capital": self.settings.mcx_short_capital,
                "ticket": self.settings.mcx_short_ticket,
                "max_lots": self.settings.mcx_short_max_lots,
                "max_positions": self.settings.mcx_short_max_positions,
                "price_scan_csv": self.settings.mcx_short_price_scan_csv,
                "volatility_scan_pct": self.settings.mcx_short_volatility_scan_pct,
                "extreme_cover": self.settings.mcx_short_extreme_cover,
                "min_charge_pct": self.settings.mcx_short_min_charge_pct,
                "elm_pct": self.settings.mcx_short_elm_pct,
            },
            "account": {
                "capital": self.settings.mcx_short_capital,
                "realized": round(self.realized(), 2),
                "realized_today": round(sum(r.pnl for r in self.closed if (r.exit_at or "")[:10] == today), 2),
                "unrealized": round(self.unrealized(), 2),
                "equity": round(self.equity(), 2),
                "margin_used": round(self.margin_used(), 2),
                "free_margin": round(self.free_margin(), 2),
            },
            "open": [row(position) for position in self.open.values()],
            "closed": [row(position) for position in reversed(self.closed[-100:])],
            "decisions": list(self.decisions)[:60],
            "margin_quotes": self.margin_quotes(),
            "errors": self.errors,
        }

    def margin_quotes(self) -> list[dict]:
        """Margin for one short lot of each of today's ATM legs, at the latest prices."""
        out = []
        for contract in self.lane.contracts.values():
            if contract.retained:
                continue
            tick = self.lane.latest.get(contract.symbol)
            if tick is None or not tick.ltp or tick.ltp <= 0:
                continue
            try:
                margin = self.margin_per_lot(contract, tick.ltp)
            except Exception:  # noqa: BLE001
                margin = None
            if margin is None:
                continue
            ticket = self.settings.mcx_short_ticket
            out.append({"symbol": contract.symbol, "root": contract.root, "option_type": contract.option_type,
                        "premium": tick.ltp, "lot_size": contract.lot_size, **margin,
                        "lots_in_ticket": int(ticket // margin["total"]) if margin["total"] > 0 else 0})
        return out

    def health(self) -> dict:
        return {"open": len(self.open), "closed": len(self.closed), "pending_exits": dict(self.pending),
                "margin_used": round(self.margin_used(), 2), "free_margin": round(self.free_margin(), 2),
                "decisions": len(self.decisions), "errors": self.errors}
