"""The decision matrix's grades, recorded at every paper entry of every lane. Measured, never read by a rule.

research/matrix_applicability_20261022: on the underlyings the agentic lane's matrix ranks the rest of the day
weakly but consistently (indices A > C every year 2021-26; stocks D clearly worst), yet on the other lanes' own
multi-day option trades the grade at entry did not rank results over 3.5 weeks, and a "skip NIFTY grade D"
veto looked promising on 11-13 days. This logs, beside the entry's order-flow reading (entry_context.py):
  own    the option's underlying (stock, or the index for an index option): intraday and swing grade + score
  nifty  NIFTY's grades for the same side (the market's setup)
so sizing or vetoing by grade can be judged later on a real sample. IV richness is left out (as in the study).
Reads the research store read-only; daily inputs are cached per symbol per day; a failure returns None.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

from . import agentic_features as feat
from . import agentic_matrix as mx

IST = ZoneInfo("Asia/Kolkata")
NIFTY = "NSE:NIFTY50-INDEX"
INDEX_SPOTS = {"NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX", "SENSEX": "BSE:SENSEX-INDEX",
               "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX", "FINNIFTY": "NSE:FINNIFTY-INDEX"}


class EntryGrader:
    def __init__(self, history_path: str | None):
        self.history_path = history_path
        self._lock = Lock()
        self._day: str | None = None
        self._daily: dict[str, list] = {}           # spot -> daily_ohlc rows, for self._day
        self._mas: dict[str, tuple] = {}            # spot -> (ma20, ma50) from daily_closes
        self._scorecard: dict[str, str] = {}        # index root -> "CE" / "PE" (the latest index-scorecard run)
        self.error: str | None = None
        self.graded = 0

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.history_path}?mode=ro", uri=True, timeout=5)

    def _roll_day(self, db: sqlite3.Connection, today: str) -> None:
        if self._day == today:
            return
        self._day, self._daily, self._mas, self._scorecard = today, {}, {}, {}
        try:
            run = db.execute("SELECT MAX(run_id) FROM agent_index_trades").fetchone()
            if run and run[0] is not None:
                for underlying, side in db.execute("SELECT underlying, side FROM agent_index_trades WHERE run_id=?", (run[0],)):
                    if side in (1, -1):
                        self._scorecard[str(underlying)] = "CE" if side == 1 else "PE"
        except sqlite3.Error:
            pass

    def _ma(self, db: sqlite3.Connection, spot: str) -> tuple:
        if spot not in self._mas:
            closes = [r[0] for r in db.execute(
                "SELECT close FROM daily_closes WHERE symbol=? AND day >= ? ORDER BY day",
                (spot, (date.today() - timedelta(days=120)).isoformat())) if r[0]]
            self._mas[spot] = (sum(closes[-20:]) / 20 if len(closes) >= 20 else None,
                               sum(closes[-50:]) / 50 if len(closes) >= 50 else None)
        return self._mas[spot]

    def _cell(self, db: sqlite3.Connection, spot: str, side: str, now: datetime, root: str | None) -> dict | None:
        if spot not in self._daily:
            self._daily[spot] = feat.daily_ohlc(db, spot, now.date())
        inputs = feat.matrix_inputs(db, spot, now, self._daily[spot])
        if not inputs:
            return None
        lo, _ = feat._bounds(now.date())
        last = db.execute("""SELECT close FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=?
                             AND timestamp<=? ORDER BY timestamp DESC LIMIT 1""", (spot, lo, int(now.timestamp()))).fetchone()
        price = float(last[0]) if last else None
        if price is None:
            return None
        ma20, ma50 = self._ma(db, spot)
        trend20 = 0 if not ma20 else (1 if price > ma20 else -1)
        trend50 = 0 if not ma50 else (1 if price > ma50 else -1)
        clock = now.strftime("%H:%M")
        intraday = mx.evaluate(inputs, side, clock, price, trend=trend20)
        atr_pct = inputs["atr_points"] / price * 100 if inputs.get("atr_points") else None
        gap = ((price / ma20 - 1) * 100 / atr_pct) if ma20 and atr_pct else None
        swing = mx.evaluate_swing(inputs, side, price, trend20=trend20, trend50=trend50,
                                  scorecard_call=self._scorecard.get(root) if root else None,
                                  intraday_grade=intraday["grade"], ma20_gap_atr=gap)
        return {"symbol": spot, "price": price, "intraday": intraday["grade"], "intraday_score": intraday["score"],
                "swing": swing["grade"], "swing_score": swing["score"],
                "open_type": inputs.get("open_type"), "ib_atr": inputs.get("ib_atr")}

    def grade(self, option_symbol: str, now: datetime | None = None) -> dict | None:
        """{"own": {...}, "nifty": {...}} for an option entry, or None when it cannot be graded."""
        if not self.history_path:
            return None
        from .option_agent import parse_option
        try:
            parsed = parse_option(option_symbol)
            if not parsed:
                return None
            now = (now or datetime.now(IST)).astimezone(IST)
            side = "CE" if parsed["side"] > 0 else "PE"
            root = parsed["root"]
            spot = INDEX_SPOTS.get(root) or parsed.get("spot") or f"NSE:{root}-EQ"
            with self._lock, self._connect() as db:
                self._roll_day(db, now.date().isoformat())
                own = self._cell(db, spot, side, now, root if root in INDEX_SPOTS else None)
                market = own if spot == NIFTY else self._cell(db, NIFTY, side, now, "NIFTY")
            self.graded += 1
            return {"side": side, "own": own, "nifty": market, "graded_at": now.strftime("%H:%M")}
        except Exception as exc:  # noqa: BLE001 -- a measurement must never block an entry
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            return None

    def health(self) -> dict:
        return {"day": self._day, "graded": self.graded, "cached_underlyings": len(self._daily), "error": self.error}
