"""Recovery from outages without a human: missed-session backfill, expired-position settlement, a hung-loop watchdog.

Built after the 5, 8 and 15 Oct 2026 host crashes. Each one needed the same manual steps, and each manual
step is here:
  * missed_windows / repair_outage: the session minutes the app did not capture (host down, feed dead) are
    downloaded from broker history BEFORE warm-up, so indicators never jump across a hole. The last stored
    NIFTY 50 minute bar is the record of when capture stopped; no extra state is kept.
  * expired_positions / settlement_price: a contract that expired while the app was down cannot be sold on
    the feed. It is closed at its settlement value: intrinsic from the underlying's last close on expiry day,
    otherwise the holding remains unresolved.
  * LoopWatchdog: a wedged event loop answers nothing and restarts nothing. A thread outside the loop exits
    the process when the loop stops beating, and Docker's restart policy brings it back.
Read-only database integrity checks gate startup after a crash (engine/db_doctor.sh).
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import market_calendar

IST = ZoneInfo("Asia/Kolkata")
NIFTY = "NSE:NIFTY50-INDEX"
SESSION_OPEN = (9, 15)
SESSION_CLOSE = (15, 30)
MIN_GAP_MINUTES = 5          # a restart shorter than this is not worth a history sweep
MAX_LOOKBACK_DAYS = 10       # older holes are the warm-up's whole-session staleness rule's job
REPAIR_CONCURRENCY = 4
REPAIR_TIMEOUT_SECONDS = 15 * 60


def _session(day: date) -> tuple[datetime, datetime]:
    special = market_calendar.special_session(day)
    if special:
        return datetime.combine(day, special[0], IST), datetime.combine(day, special[1], IST)
    open_ = datetime.combine(day, datetime.min.time().replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1]), IST)
    close = datetime.combine(day, datetime.min.time().replace(hour=SESSION_CLOSE[0], minute=SESSION_CLOSE[1]), IST)
    return open_, close


def missed_windows(last_seen: datetime | None, now: datetime) -> list[tuple[datetime, datetime]]:
    """Regular-session spans between the last captured minute and now, per trading day."""
    if last_seen is None:
        return []
    start = max(last_seen.astimezone(IST) + timedelta(minutes=1), now.astimezone(IST) - timedelta(days=MAX_LOOKBACK_DAYS))
    windows = []
    day = start.date()
    while day <= now.astimezone(IST).date():
        if market_calendar.is_trading_day(day):
            open_, close = _session(day)
            lo, hi = max(open_, start), min(close, now.astimezone(IST))
            if hi > lo:
                windows.append((lo, hi))
        day += timedelta(days=1)
    return windows


def missed_minutes(windows) -> float:
    return sum((hi - lo).total_seconds() for lo, hi in windows) / 60


def last_captured(database_path: str, symbol: str = NIFTY) -> datetime | None:
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
            row = db.execute("SELECT MAX(timestamp) FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60",
                             (symbol,)).fetchone()
    except sqlite3.Error:
        return None
    return datetime.fromtimestamp(row[0], UTC) if row and row[0] else None


async def repair_outage(broker, database_path: str, symbols: list[str], now: datetime | None = None,
                        store=None) -> dict:
    """Download the missed session minutes for every symbol. Returns a report; never raises."""
    from .candle_store import store_historical_candles
    store = store or store_historical_candles
    now = now or datetime.now(IST)
    last = await asyncio.to_thread(last_captured, database_path)
    windows = missed_windows(last, now)
    report = {"at": now.isoformat(timespec="seconds"), "last_captured": last.isoformat() if last else None,
              "missed_minutes": round(missed_minutes(windows)), "windows": [(a.isoformat(), b.isoformat()) for a, b in windows],
              "symbols": 0, "rows": 0, "errors": 0, "skipped": None}
    if missed_minutes(windows) < MIN_GAP_MINUTES:
        report["skipped"] = "no missed session minutes"
        return report
    start, end = windows[0][0] - timedelta(minutes=1), min(windows[-1][1] + timedelta(minutes=1), now)
    slots = asyncio.Semaphore(REPAIR_CONCURRENCY)
    errors: list[str] = []

    async def one(symbol: str) -> None:
        async with slots:
            try:
                rows = await broker.history_range(symbol, 60, start, end)
                stored = await asyncio.to_thread(store, database_path, rows)
                report["rows"] += stored          # read the total only after the await: concurrent downloads
                report["symbols"] += 1
            except Exception as exc:  # noqa: BLE001 -- an expired or unknown symbol must not stop the sweep
                errors.append(f"{symbol}: {type(exc).__name__}: {exc}"[:160])

    try:
        await asyncio.wait_for(asyncio.gather(*(one(s) for s in dict.fromkeys(symbols))), REPAIR_TIMEOUT_SECONDS)
    except TimeoutError:
        report["skipped"] = "timed out; partial"
    report["errors"] = len(errors)
    report["error_samples"] = errors[:10]
    return report


# --------------------------------------------------------------------------- expired positions

def expired_positions(positions, today: date, after_close: bool) -> list[tuple[str, dict]]:
    """(symbol, parsed contract) for held options whose expiry has passed."""
    from .option_agent import parse_option
    out = []
    for symbol, position in positions:
        if position.quantity <= 0:
            continue
        parsed = parse_option(symbol)
        if not parsed or not parsed.get("expiry"):
            continue
        expiry = date.fromisoformat(parsed["expiry"])
        if expiry < today or (expiry == today and after_close):
            out.append((symbol, parsed))
    return out


def settlement_price(database_path: str, symbol: str, parsed: dict) -> tuple[float | None, str]:
    """Paper intrinsic settlement requires the final regular-session underlying minute.

    Missing close evidence stays unresolved; an old option premium is never settlement.
    """
    _, close = _session(date.fromisoformat(parsed["expiry"]))
    final_minute = int((close - timedelta(minutes=1)).timestamp())
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
            spot = db.execute("""SELECT close FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
                                 AND timestamp = ?""", (parsed.get("spot"), final_minute)).fetchone()
    except sqlite3.Error as exc:
        return None, f"settlement unresolved: history unavailable: {exc}"
    if spot and spot[0] and spot[0] > 0:
        value = max(0.0, (spot[0] - parsed["strike"]) * parsed["side"])
        return round(value, 2), f"paper intrinsic from final expiry-session minute {parsed['spot']} close {spot[0]}"
    return None, "settlement unresolved: final expiry-session underlying minute missing"


async def settle_expired_books(books, database_path: str, now: datetime | None = None) -> list[dict]:
    """Close every expired holding in each (lane, execution) book at its settlement value."""
    now = (now or datetime.now(IST)).astimezone(IST)
    after_close = (now.hour, now.minute) >= SESSION_CLOSE
    settled = []
    for lane, execution in books:
        for symbol, parsed in expired_positions(list(execution.portfolio.positions.items()), now.date(), after_close):
            price, basis = await asyncio.to_thread(settlement_price, database_path, symbol, parsed)
            if price is None:
                settled.append({"lane": lane, "symbol": symbol, "error": basis})
                continue
            record = await execution.settle_expired(symbol, price, "EXPIRED_SETTLED_WHILE_OFFLINE")
            if record is not None:
                settled.append({"lane": lane, "symbol": symbol, "price": price, "basis": basis,
                                "realized_pnl": record.get("realized_pnl")})
    return settled


# --------------------------------------------------------------------------- hung event loop

class LoopWatchdog:
    """Exit the process when its event loop stops turning; the container restart policy restarts it."""

    def __init__(self, role: str, stall_seconds: float = 300.0, marker_dir: str | None = None):
        self.role = role
        self.stall_seconds = stall_seconds
        self.marker_dir = marker_dir
        self.beat = time.monotonic()
        self._task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._beat())
        self._thread = threading.Thread(target=self._watch, name=f"loop-watchdog-{self.role}", daemon=True)
        self._thread.start()

    async def _beat(self) -> None:
        while True:
            self.beat = time.monotonic()
            await asyncio.sleep(1.0)

    def _watch(self) -> None:
        while True:
            time.sleep(10.0)
            stalled = time.monotonic() - self.beat
            if stalled > self.stall_seconds:
                message = f"{datetime.now(UTC).isoformat()} {self.role}: event loop stalled {stalled:.0f}s; exiting for a restart\n"
                sys.stderr.write(message)
                if self.marker_dir:
                    try:
                        with open(os.path.join(self.marker_dir, f".watchdog_exit.{self.role}"), "a") as handle:
                            handle.write(message)
                    except OSError:
                        pass
                os._exit(70)

    def status(self) -> dict:
        return {"stall_seconds": self.stall_seconds, "last_beat_age": round(time.monotonic() - self.beat, 1)}


def mark_clean_shutdown(database_path: str, role: str) -> None:
    """Tell the next start's database doctor (engine/db_doctor.sh) that this process closed its stores cleanly."""
    try:
        directory = os.path.dirname(os.path.abspath(database_path))
        with open(os.path.join(directory, f".clean_shutdown.{role}"), "w") as handle:
            handle.write(datetime.now(UTC).isoformat())
    except OSError:
        pass


def session_history_coverage(database_path: str, day: date, symbols: list[str]) -> dict:
    """Require every regular minute for each actual nightly input; read errors are unknown."""
    from .sessions import SESSION_OPEN as session_open, session_close

    lo = int(datetime.combine(day, session_open, IST).timestamp())
    expected_by_symbol = {}
    missing = {}
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
            for symbol in dict.fromkeys(symbols):
                hi = int(datetime.combine(day, session_close(symbol, day), IST).timestamp())
                expected = set(range(lo, hi, 60))
                expected_by_symbol[symbol] = len(expected)
                stamps = {r[0] for r in db.execute(
                    "SELECT timestamp FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 AND timestamp>=? AND timestamp<?",
                    (symbol, lo, hi))}
                absent = expected - stamps
                if absent:
                    missing[symbol] = len(absent)
    except sqlite3.Error as exc:
        return {"complete": False, "error": str(exc),
                "expected_minutes": max(expected_by_symbol.values(), default=0),
                "expected_minutes_by_symbol": expected_by_symbol, "missing": missing}
    return {"complete": bool(symbols) and not missing, "error": None,
            "expected_minutes": max(expected_by_symbol.values(), default=0),
            "expected_minutes_by_symbol": expected_by_symbol, "missing": missing}


def session_history_complete(database_path: str, day: date, symbol: str = NIFTY, min_bars: int = 375) -> bool:
    # Kept as a compatibility wrapper; lowered caller thresholds cannot certify completeness.
    return session_history_coverage(database_path, day, [symbol])["complete"]


PROCESS_STARTED = datetime.now(UTC)


def doctor_summary(database_path: str, role: str) -> dict | None:
    """The database doctor's latest run for this role, from backups/doctor.log (engine/db_doctor.sh)."""
    path = os.path.join(os.path.dirname(os.path.abspath(database_path)), "backups", "doctor.log")
    try:
        with open(path) as handle:
            lines = [line.rstrip() for line in handle.readlines()[-200:] if f"[{role}]" in line]
    except OSError:
        return None
    start = max((i for i, line in enumerate(lines) if "shutdown" in line or "skipped" in line), default=None)
    if start is None:
        return None
    run = lines[start:]
    stamp = lambda line: line.split(" ", 1)[0]
    return {"started": stamp(run[0]), "finished": stamp(run[-1]), "mode": run[0].split("] ", 1)[-1][:80],
            "damaged": [line.split("] ", 1)[-1][:120] for line in run if "DAMAGED" in line],
            "recovered": sum("recovered and swapped" in line for line in run)}


def boot_report(database_path: str, role: str, warmup: dict | None, warmup_report: dict | None,
                history_backfill: dict | None, status: str) -> dict:
    """Where the last start spent its time, and whether the app is ready to trade."""
    now = datetime.now(UTC)
    ready = bool(warmup) and warmup.get("required", 0) > 0 and warmup.get("with_indicators") == warmup.get("required") \
        and status == "connected"
    finished = (warmup_report or {}).get("finished_at")
    report = {"process_started": PROCESS_STARTED.isoformat(timespec="seconds"),
              "uptime_seconds": round((now - PROCESS_STARTED).total_seconds()),
              "doctor": doctor_summary(database_path, role),
              "contract_selection_seconds": ((warmup_report or {}).get("selection") or {}).get("seconds"),
              "warmup_seconds": (warmup_report or {}).get("seconds"),
              "warmup_downloads": (warmup_report or {}).get("downloaded"),
              "background_history": {k: (history_backfill or {}).get(k) for k in ("pending", "stored", "errors", "finished_at")},
              "ready": ready, "ready_at": finished if ready else None}
    if ready and finished:
        report["process_to_ready_seconds"] = round((datetime.fromisoformat(finished) - PROCESS_STARTED).total_seconds())
    return report
