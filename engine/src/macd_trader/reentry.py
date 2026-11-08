"""MACD lane re-entry: buy a stock's ATM option again while its premium trend is still running.

Owner, 8 Nov 2026. Study: research/macd_reentry_20261108 (memory macd-reentry-study).

When the MACD lane closes a position on a stock+side (e.g. HAL CE) by its TRAILING stop or HARD stop, the trend may
still be running. While flat, the lane's contracts roll to today's ATM strike, so the re-entry is judged on that ATM
contract (the only one the lane may enter), on CLOSED 30-min bars:

    ATM premium MACD > 0, MACD higher than the two bars before it, and the bar closed above the previous bar's high

It fires once per eligibility (a second re-entry in the same run did not pay). Eligibility ends when the re-entry
fills, when any new entry on that stock+side fills, when the ATM contract's MACD closes at or below zero (the trend
is over; a fresh zero cross is then a normal entry), or after REENTRY_SESSIONS regular sessions. Exits by MACD
invalidation, expiry, extreme-move booking or by hand do not make a stock eligible, and a re-entry's own exit does
not re-arm it.

Re-entries trade in the MACD paper lane only, under their own signal kind, so the lane page can track them. The
smart lanes and the Upstox live lane do not take them (promotion to real trading is the owner's later decision).
The state lives in a small JSON file next to the lane database so a restart keeps it.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from . import market_calendar

IST = ZoneInfo("Asia/Kolkata")
REENTRY_KIND = "PREMIUM_MACD_REENTRY"
ARMING_EXITS = ("TRAILING_STOP_25_PCT", "HARD_STOP_30_PCT")
REENTRY_SESSIONS = 5
LAST_BAR_CLOSE_MINUTES = 15 * 60 + 15      # bars closing after 15:15 IST do not enter


def option_side(symbol: str) -> str | None:
    return symbol[-2:] if symbol.endswith(("CE", "PE")) else None


def stock_key(symbol: str) -> str | None:
    from .execution import contract_root
    side = option_side(symbol)
    return f"{contract_root(symbol)}|{side}" if side else None


def is_reentry_kind(kind: str | None) -> bool:
    return bool(kind) and "REENTRY" in kind


class ReentryTracker:
    def __init__(self, path: str):
        self.path = path
        self.eligible: dict[str, dict] = {}       # "HAL|CE" -> {symbol, exit_reason, exit_at, armed_on}
        self.pending: dict[str, dict] = {}        # signal_id -> {symbol, key, at}: emitted, not yet filled
        self.positions: dict[str, dict] = {}      # position_id -> {symbol, key, signal_id, at, ...}
        self.fired: int = 0
        self.cleared: dict[str, int] = {}
        self.error: str | None = None
        self.load()

    # ------------------------------------------------------------- persistence
    def load(self) -> None:
        try:
            with open(self.path) as f:
                data = json.load(f)
            self.eligible = data.get("eligible", {})
            self.positions = data.get("positions", {})
            self.pending = data.get("pending", {})
            self.fired = int(data.get("fired", 0))
            self.cleared = data.get("cleared", {})
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"eligible": self.eligible, "positions": self.positions, "pending": self.pending,
                           "fired": self.fired, "cleared": self.cleared}, f, indent=1, default=str)
            os.replace(tmp, self.path)
        except OSError as exc:
            self.error = f"save: {exc}"[:200]

    def _clear(self, key: str, why: str) -> None:
        if self.eligible.pop(key, None) is not None:
            self.cleared[why] = self.cleared.get(why, 0) + 1

    # ------------------------------------------------------------- events from the MACD lane's book
    def on_closed(self, closed) -> None:
        """A closing fill. Re-entry slices are tallied; only the LAST slice of another position can arm its stock+side."""
        key = stock_key(closed.symbol)
        if key is None:
            return
        final = not getattr(closed, "partial", False) and not getattr(closed, "remaining_quantity", 0)
        if closed.position_id in self.positions:
            row = self.positions[closed.position_id]
            row["realized_pnl"] = round(row.get("realized_pnl", 0.0) + float(closed.realized_pnl or 0), 2)
            row["sold_quantity"] = row.get("sold_quantity", 0) + int(closed.quantity or 0)
            row["sold_value"] = round(row.get("sold_value", 0.0) + float(closed.exit_price) * int(closed.quantity or 0), 2)
            if final:
                row.update(closed_at=_iso(closed.exit_time), exit_reason=closed.exit_reason,
                           max_return_pct=round(float(closed.max_return_pct or 0), 2))
                self._clear(key, "re-entry closed")
            self.save()
            return
        if not final:
            return
        elif closed.exit_reason in ARMING_EXITS:
            self.eligible[key] = {"symbol": closed.symbol, "exit_reason": closed.exit_reason,
                                  "exit_at": _iso(closed.exit_time), "exit_price": closed.exit_price,
                                  "armed_on": datetime.now(IST).date().isoformat()}
        else:
            self._clear(key, "exit not by stop/trail")
        self.save()

    def on_entry(self, symbol: str, signal_id: str | None, position_id: str, price: float) -> None:
        """A new position opened in the MACD lane. A re-entry is recorded; any entry ends the eligibility."""
        key = stock_key(symbol)
        if key is None:
            return
        pending = self.pending.pop(signal_id, None) if signal_id else None
        if pending is not None:
            self.positions[position_id] = {"symbol": symbol, "key": key, "signal_id": signal_id, "at": _iso(datetime.now(UTC)),
                                           "entry_price": price, "after": pending.get("after")}
            self._clear(key, "re-entered")
        else:
            self._clear(key, "normal entry")
        self.save()

    # ------------------------------------------------------------- the trigger
    def check(self, symbol: str, bar_start: int, bar_seconds: int, macd: list[float], highs: list[float], close: float,
              entry_symbols: set[str] | None, flat_on_stock: bool) -> dict | None:
        """Judge a CLOSED bar of ``symbol``. Returns the eligibility row when a re-entry should fire now."""
        key = stock_key(symbol)
        row = self.eligible.get(key) if key else None
        if row is None:
            return None
        if self._expired(row):
            self._clear(key, "expired")
            self.save()
            return None
        if entry_symbols is not None and symbol not in entry_symbols:
            return None                 # only today's ATM contract of that stock+side is judged
        if len(macd) < 3 or len(highs) < 2:
            return None
        if macd[-1] <= 0:
            self._clear(key, "MACD back below zero")
            self.save()
            return None
        end = datetime.fromtimestamp(bar_start + bar_seconds, IST)
        if end.hour * 60 + end.minute > LAST_BAR_CLOSE_MINUTES or not flat_on_stock:
            return None
        if macd[-1] > macd[-2] > macd[-3] and close > highs[-2]:
            return row
        return None

    def note_fired(self, signal_id: str, symbol: str, row: dict) -> None:
        self.pending[signal_id] = {"symbol": symbol, "key": stock_key(symbol), "at": _iso(datetime.now(UTC)),
                                   "after": f"{row.get('exit_reason')} on {row.get('symbol')}"}
        # one shot: drop the eligibility now so the next bar cannot fire again even if this entry fails
        self.eligible.pop(stock_key(symbol), None)
        self.fired += 1
        if len(self.pending) > 200:
            for old in sorted(self.pending, key=lambda k: self.pending[k]["at"])[:-200]:
                self.pending.pop(old, None)
        self.save()

    def _expired(self, row: dict) -> bool:
        try:
            day = date.fromisoformat(row["armed_on"])
        except (KeyError, ValueError):
            return True
        today = datetime.now(IST).date()
        sessions = 0
        while day < today and sessions <= REENTRY_SESSIONS:
            day = day.fromordinal(day.toordinal() + 1)
            if market_calendar.is_regular_trading_day(day):
                sessions += 1
        return sessions > REENTRY_SESSIONS

    # ------------------------------------------------------------- the lane page
    def snapshot(self) -> dict:
        ids = set(self.positions)
        done = [row for row in self.positions.values() if row.get("closed_at")]
        pnl = [row.get("realized_pnl", 0.0) for row in done]
        wins = [p for p in pnl if p > 0]
        losses = [p for p in pnl if p <= 0]
        returns = [100 * (row["sold_value"] / row["sold_quantity"] / row["entry_price"] - 1)
                   for row in done if row.get("sold_quantity") and row.get("entry_price")]
        return {
            "kind": REENTRY_KIND,
            "rule": ("After a TRAILING or HARD stop exit, re-enter the stock's ATM option once when its 30-min premium "
                     "MACD is above zero, higher than the two bars before, and the bar closed above the previous high"),
            "eligible": [{"key": k, **v} for k, v in sorted(self.eligible.items())],
            "position_ids": sorted(ids),
            "positions": [{"position_id": k, **v} for k, v in sorted(self.positions.items(), key=lambda kv: kv[1].get("at", ""), reverse=True)][:100],
            "open": len(ids) - len(done),
            "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
            "fired": self.fired,
            "pending": len(self.pending),
            "closed": len(done),
            "wins": len(wins),
            "realized_pnl": round(sum(pnl), 2),
            "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "cleared": dict(self.cleared),
            "error": self.error,
        }


def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(IST).isoformat(timespec="seconds")
    return str(value)
