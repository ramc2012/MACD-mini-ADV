"""Profit booking on extreme INDEX moves, for index-option positions in the MACD lane and the smart lanes.

Research (research/extreme_moves_20261014, 14 Oct 2026; 1-min index spot 2021-2026, real NIFTY/SENSEX
option premiums 2025-26):

* When the index is 1 ATR or more BELOW the previous close, a put held from that moment lost on average:
  -9.6% within the hour (27% gained), -13% by the close, median -35% by the next close. Down extremes
  revert: after a 2-ATR fall the index bounced within 30-60 minutes and rose 0.7-0.8% the next day.
  -> book a profitable index PUT as soon as the index is ``PUT_ATR`` below the previous close.
* After an up move the index tends to extend, but the call premium does not (IV falls on rallies, theta):
  a call held from a 1-ATR up move was at a median -14% by the close and -31% by the next close; after a
  2-ATR up DAY the index gave back about 1% the next day.
  -> book a profitable index CALL at ``CALL_EXIT_AT`` when the index is ``CALL_ATR`` or more above the
  previous close, so it is not carried overnight.

"Profitable" means the mark clears the round-trip breakeven; losing positions keep the lane's own stops.
ATR = mean true range of the 14 completed sessions before today, from the stored minute bars.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .portfolio import breakeven_exit_price
from .universe import INDEX_SPOTS

IST = ZoneInfo("Asia/Kolkata")
PUT_ATR = 1.0
CALL_ATR = 1.5
CALL_EXIT_AT = time(15, 15)
ATR_SESSIONS = 14
SPOT_TO_ROOT = {spot: root for root, spot in INDEX_SPOTS.items()}


def option_root(symbol: str) -> tuple[str | None, int]:
    """(index root, +1 CE / -1 PE) for an index option symbol, else (None, 0)."""
    from .option_agent import parse_option
    parsed = parse_option(symbol)
    if not parsed or parsed["root"] not in INDEX_SPOTS:
        return None, 0
    return parsed["root"], parsed["side"]


class ExtremeBooker:
    def __init__(self, history_path: str):
        self.history_path = history_path
        self.levels: dict[str, dict] = {}          # root -> {"day", "prev_close", "atr"}
        self.last_dev: dict[str, float] = {}       # root -> latest deviation in ATRs
        self.booked: list[dict] = []               # recent bookings, newest first (for health)
        # (lane, symbol) -> (reason, dev, day). A booking waits for the option's own fresh quote: the index
        # tick that triggers it can arrive when the option's last print is older than the paper-fill limit.
        self.pending: dict[tuple[str, str], tuple[str, float, str]] = {}
        self.error: str | None = None

    # -- reference levels --------------------------------------------------
    def _load(self, root: str, today: date) -> dict | None:
        spot = INDEX_SPOTS[root]
        start = int(datetime.combine(today, time(0, 0), IST).timestamp())
        try:
            with sqlite3.connect(f"file:{self.history_path}?mode=ro", uri=True, timeout=10) as db:
                rows = db.execute(
                    """SELECT date(timestamp, 'unixepoch', '+5 hours', '+30 minutes') AS d,
                              MAX(high), MIN(low), COUNT(*),
                              (SELECT close FROM historical_candles c2 WHERE c2.symbol = c.symbol AND c2.timeframe_seconds = 60
                                 AND date(c2.timestamp, 'unixepoch', '+5 hours', '+30 minutes') =
                                     date(c.timestamp, 'unixepoch', '+5 hours', '+30 minutes')
                               ORDER BY c2.timestamp DESC LIMIT 1)
                       FROM historical_candles c
                       WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp < ? AND timestamp >= ?
                       GROUP BY d ORDER BY d""",
                    (spot, start, start - 40 * 86400)).fetchall()
        except sqlite3.Error as exc:
            self.error = f"levels {root}: {exc}"
            return None
        days = [(h, l, c) for _d, h, l, n, c in rows if n >= 300 and c]
        if len(days) < ATR_SESSIONS + 1:
            return None
        trs = [max(h - l, abs(h - pc), abs(l - pc)) for (h, l, _c), (_h, _l, pc) in zip(days[1:], days[:-1])]
        atr = sum(trs[-ATR_SESSIONS:]) / ATR_SESSIONS
        return {"day": today.isoformat(), "prev_close": days[-1][2], "atr": atr} if atr > 0 else None

    def levels_for(self, root: str, now: datetime) -> dict | None:
        today = now.astimezone(IST).date()
        cached = self.levels.get(root)
        if cached is None or cached.get("day") != today.isoformat():
            cached = self._load(root, today)
            self.levels[root] = cached or {"day": today.isoformat(), "prev_close": None, "atr": None}
        return cached if cached and cached.get("atr") else None

    # -- the rule -----------------------------------------------------------
    def reason_for(self, side: int, dev: float, now: datetime) -> str | None:
        if side < 0 and dev <= -PUT_ATR:
            return f"EXTREME_BOOK_PUT_INDEX_{PUT_ATR:g}ATR_DOWN"
        if side > 0 and dev >= CALL_ATR and now.astimezone(IST).time() >= CALL_EXIT_AT:
            return f"EXTREME_BOOK_CALL_INDEX_{CALL_ATR:g}ATR_UP_EOD"
        return None

    async def on_index_tick(self, spot_symbol: str, price: float, executions: list, now: datetime | None = None) -> list[dict]:
        """Queue qualifying index-option positions, booking at once where the option quote is fresh."""
        root = SPOT_TO_ROOT.get(spot_symbol)
        if root is None or price <= 0:
            return []
        now = now or datetime.now(IST)
        levels = self.levels_for(root, now)
        if not levels:
            return []
        dev = (price - levels["prev_close"]) / levels["atr"]
        self.last_dev[root] = round(dev, 3)
        if -PUT_ATR < dev < CALL_ATR:
            return []
        day = now.astimezone(IST).date().isoformat()
        done = []
        for execution in executions:
            for symbol, position in list(execution.portfolio.positions.items()):
                if position.quantity <= 0:
                    continue
                pos_root, side = option_root(symbol)
                if pos_root != root:
                    continue
                reason = self.reason_for(side, dev, now)
                if reason is None:
                    continue
                self.pending[(execution.portfolio.lane, symbol)] = (reason, dev, day)
                record = await self._try(execution, symbol, now)
                if record:
                    done.append(record)
        return done

    async def on_option_tick(self, symbol: str, executions: list, now: datetime | None = None) -> list[dict]:
        """A fresh print of a held option: book it if a booking is waiting for it."""
        if not self.pending:
            return []
        now = now or datetime.now(IST)
        done = []
        for execution in executions:
            if (execution.portfolio.lane, symbol) in self.pending:
                record = await self._try(execution, symbol, now)
                if record:
                    done.append(record)
        return done

    async def _try(self, execution, symbol: str, now: datetime) -> dict | None:
        key = (execution.portfolio.lane, symbol)
        reason, dev, day = self.pending[key]
        position = execution.portfolio.positions.get(symbol)
        if day != now.astimezone(IST).date().isoformat() or not position or position.quantity <= 0:
            self.pending.pop(key, None)
            return None
        try:
            mark = execution._fresh_price(symbol)
        except RuntimeError:
            return None                                    # wait for the option's next print
        floor = breakeven_exit_price(position.average_price, position.quantity, execution.settings.slippage_bps)
        if mark <= floor:
            self.pending.pop(key, None)                    # not a profit: the lane's own stops decide
            return None
        if symbol in execution._armed_exits or any(
                o.symbol == symbol and o.side == "SELL" and o.status == "OPEN" for o in execution.orders.values()):
            return None                                    # another exit is already in flight
        execution.arm_exit(symbol, reason)
        try:
            await execution.submit(symbol, "SELL", position.quantity)
        except (ValueError, RuntimeError, PermissionError) as exc:
            execution._armed_exits.discard(symbol)
            self.error = f"{execution.portfolio.lane} {symbol}: {exc}"[:200]
            return None
        self.pending.pop(key, None)
        record = {"at": now.isoformat(timespec="seconds"), "lane": execution.portfolio.lane, "symbol": symbol,
                  "reason": reason, "index_dev_atr": round(dev, 2), "exit_mark": mark,
                  "entry": round(position.average_price, 2)}
        self.booked.insert(0, record)
        del self.booked[50:]
        return record

    def health(self) -> dict:
        return {"enabled": True, "put_atr": PUT_ATR, "call_atr": CALL_ATR, "call_exit_at": CALL_EXIT_AT.strftime("%H:%M"),
                "levels": {r: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in (l or {}).items()}
                           for r, l in self.levels.items()},
                "dev_atr": self.last_dev, "pending": [f"{lane}:{sym}" for lane, sym in self.pending],
                "recent": self.booked[:10], "error": self.error}
