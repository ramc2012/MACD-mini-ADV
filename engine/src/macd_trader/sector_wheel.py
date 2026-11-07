"""Sector wheel: own and NIFTY-relative returns over rolling 1/5/20/60-session windows, from daily closes.

The windows follow research/sector_persistence_20261017: sector relative strength persists for roughly two to four
weeks on daily bars (top-3 minus bottom-3 peaks 13-22 sessions after ranking), and month-start leaders (the best
relative performers over a month's first 3 sessions) out-ran the laggards for about 13 sessions before fading. So
each row carries the 5-, 20- and 60-session picture together, and the month-start leaders are flagged with how far
into that typical run they are.

Daily closes live in ``daily_closes`` (historical database), refreshed once per completed session: first from the
engine's own stored minute bars (``fill_from_minutes``, no download), then from the broker's daily history for any name
still behind. During the recovery hold the broker step is skipped. During the session the live price stands in for
today's close.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import market_calendar
from .sectors import sector_of

IST = ZoneInfo("Asia/Kolkata")
BENCHMARK = "NSE:NIFTY50-INDEX"
WINDOWS = {"1D": 1, "5D": 5, "20D": 20, "60D": 60}
MONTH_START_SESSIONS = 3
TYPICAL_RUN_SESSIONS = 13
BROAD = {"NSE:NIFTY50-INDEX", "BSE:SENSEX-INDEX", "NSE:NIFTYNXT50-INDEX", "NSE:MIDCPNIFTY-INDEX", "NSE:INDIAVIX-INDEX"}
LABELS = {
    "NSE:NIFTY50-INDEX": "NIFTY 50", "NSE:NIFTYBANK-INDEX": "Bank", "BSE:SENSEX-INDEX": "Sensex",
    "NSE:MIDCPNIFTY-INDEX": "Midcap Select", "NSE:NIFTYIT-INDEX": "IT", "NSE:NIFTYAUTO-INDEX": "Auto",
    "NSE:NIFTYPHARMA-INDEX": "Pharma", "NSE:NIFTYFMCG-INDEX": "FMCG", "NSE:NIFTYMETAL-INDEX": "Metal",
    "NSE:NIFTYENERGY-INDEX": "Energy", "NSE:NIFTYREALTY-INDEX": "Realty", "NSE:FINNIFTY-INDEX": "Fin Nifty",
    "NSE:NIFTYNXT50-INDEX": "Next 50", "NSE:NIFTYPSUBANK-INDEX": "PSU Bank", "NSE:NIFTYPVTBANK-INDEX": "Pvt Bank",
    "NSE:NIFTYMEDIA-INDEX": "Media", "NSE:NIFTYINFRA-INDEX": "Infra",
}
SCHEMA = """CREATE TABLE IF NOT EXISTS daily_closes (
  symbol TEXT NOT NULL, day TEXT NOT NULL, close REAL NOT NULL, PRIMARY KEY(symbol, day))"""


def label(symbol: str) -> str:
    return LABELS.get(symbol) or symbol.split(":", 1)[-1].removesuffix("-EQ").removesuffix("-INDEX")


def last_completed_session(now: datetime) -> date:
    local = now.astimezone(IST)
    day = local.date()
    if not (market_calendar.is_trading_day(day) and local.time() >= time(15, 35)):
        day -= timedelta(days=1)
        while not market_calendar.is_trading_day(day):
            day -= timedelta(days=1)
    return day


def stale_symbols(database_path: str, symbols: list[str], now: datetime) -> list[str]:
    """Symbols whose stored daily closes stop before the last completed session."""
    want = last_completed_session(now).isoformat()
    with sqlite3.connect(database_path, timeout=30) as db:
        db.execute(SCHEMA)
        latest = dict(db.execute("SELECT symbol, MAX(day) FROM daily_closes GROUP BY symbol").fetchall())
    return [symbol for symbol in symbols if (latest.get(symbol) or "") < want]


def store(database_path: str, symbol: str, candles) -> int:
    rows = {datetime.fromtimestamp(c.timestamp, IST).date().isoformat(): float(c.close) for c in candles if c.close}
    with sqlite3.connect(database_path, timeout=30) as db:
        db.execute(SCHEMA)
        db.executemany("INSERT OR REPLACE INTO daily_closes VALUES (?,?,?)", [(symbol, d, c) for d, c in rows.items()])
    return len(rows)


CLOSE_CUTOFF_SECONDS = 15 * 3600 + 30 * 60      # a minute bar starting at or after 15:30 IST is not the session close


def fill_from_minutes(database_path: str, symbols: list[str], now: datetime, days: int = 400) -> int:
    """Store a daily close for every completed session that has stored 1-minute bars but no daily row yet.

    The close is the last bar starting before 15:30 IST. That is within a few hundredths of a percent of the official
    close for indices; cash stocks' stored bars end at 15:14 (by design), so theirs is the 15:14 price. Rows already
    present (broker daily bars, or the 2019-2026 seed) are kept: INSERT OR IGNORE.
    """
    last = last_completed_session(now).isoformat()
    since = int((now - timedelta(days=days)).timestamp())
    rows: list[tuple[str, str, float]] = []
    with sqlite3.connect(database_path, timeout=30) as db:
        db.execute(SCHEMA)
        try:
            for symbol in symbols:
                # SQLite returns the bare column (close) from the row holding MAX(timestamp) in each group.
                rows += [(symbol, day, close) for day, close, _ in db.execute(
                    """SELECT date(timestamp + 19800, 'unixepoch') AS day, close, MAX(timestamp) FROM historical_candles
                       WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? AND (timestamp + 19800) % 86400 < ?
                       GROUP BY day""", (symbol, since, CLOSE_CUTOFF_SECONDS)) if day <= last and close]
        except sqlite3.OperationalError:          # no minute store in this database
            return 0
        before = db.total_changes
        db.executemany("INSERT OR IGNORE INTO daily_closes VALUES (?,?,?)", rows)
        return db.total_changes - before


async def refresh(broker, database_path: str, symbols: list[str], now: datetime | None = None, pacing: float = 0.25) -> dict:
    """Fill closes from stored minute bars, then download ~a year of daily bars for every name still stale (one
    request each; Fyers allows 366 days per call). Under the recovery hold the download is skipped (``held``)."""
    now = now or datetime.now(IST)
    filled = await asyncio.to_thread(fill_from_minutes, database_path, symbols, now)
    stale = await asyncio.to_thread(stale_symbols, database_path, symbols, now)
    report = {"filled_from_minutes": filled, "stale": len(stale), "stored": 0, "errors": [], "held": False}
    for symbol in stale:
        try:
            candles = await broker.history_range(symbol, 86_400, now - timedelta(days=360), now)
            report["stored"] += await asyncio.to_thread(store, database_path, symbol, candles)
        except Exception as exc:  # noqa: BLE001 -- one bad symbol must not stop the rest
            if "downloads disabled" in str(exc):
                report["held"] = True             # recovery hold: say it once, not once per symbol
                break
            report["errors"].append(f"{symbol}: {type(exc).__name__}: {exc}"[:160])
        await asyncio.sleep(pacing)
    return report


def _series(db: sqlite3.Connection, symbol: str, today: date, live: float | None) -> list[tuple[str, float]]:
    rows = [(d, c) for d, c in db.execute("SELECT day, close FROM daily_closes WHERE symbol = ? ORDER BY day", (symbol,))]
    rows = [(d, c) for d, c in rows if d <= today.isoformat()]
    if live:
        if rows and rows[-1][0] == today.isoformat():
            rows[-1] = (rows[-1][0], live)
        elif market_calendar.is_trading_day(today):
            rows.append((today.isoformat(), live))
    return rows


def _change(series: list[tuple[str, float]], n: int) -> float | None:
    if len(series) <= n or not series[-1 - n][1]:
        return None
    return series[-1][1] / series[-1 - n][1] - 1


def build(database_path: str, symbols: list[str], live: dict[str, float], now: datetime | None = None) -> dict:
    now = (now or datetime.now(IST)).astimezone(IST)
    in_session = market_calendar.is_trading_day(now.date()) and time(9, 15) <= now.time() < time(15, 30)
    today = now.date() if in_session else last_completed_session(now)
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
        try:
            series = {s: _series(db, s, today, live.get(s) if in_session else None) for s in dict.fromkeys([BENCHMARK, *symbols])}
        except sqlite3.OperationalError:          # table not created yet: first refresh still running
            series = {}
    bench = series.get(BENCHMARK) or []
    # Benchmark dates define the same endpoint and lookback for every name.
    dates = [d for d, _ in bench]
    def aligned_change(values, n):
        prices = dict(values)
        if len(dates) <= n or dates[-1] != today.isoformat():
            return None
        end, start = prices.get(dates[-1]), prices.get(dates[-1-n])
        return end / start - 1 if end and start else None
    bench_change = {k: aligned_change(bench, n) for k, n in WINDOWS.items()}
    rows = []
    for symbol, s in series.items():
        if len(s) < 2:
            continue
        own = {k: aligned_change(s, n) for k, n in WINDOWS.items()}
        rel = {k: (None if own[k] is None or bench_change[k] is None else (1 + own[k]) / (1 + bench_change[k]) - 1) for k in WINDOWS}
        kind = "benchmark" if symbol == BENCHMARK else "broad" if symbol in BROAD else "sector" if symbol.endswith("-INDEX") else "stock"
        rows.append({"symbol": symbol, "label": label(symbol), "kind": kind,
                     "group": sector_of(symbol) if kind == "stock" else kind, "price": s[-1][1], "as_of": s[-1][0], "stale": s[-1][0] != today.isoformat(),
                     "own": {k: None if v is None else round(v * 100, 2) for k, v in own.items()},
                     "relative": {k: None if v is None else round(v * 100, 2) for k, v in rel.items()}})
    # Month-start leaders among the sector indices: relative return over this month's first sessions.
    month_days = [d for d, _ in bench if d[:7] == today.isoformat()[:7]]
    month_start = None
    if len(month_days) >= MONTH_START_SESSIONS:
        formed = month_days[MONTH_START_SESSIONS - 1]
        before = [d for d, _ in bench if d < month_days[0]]
        if before:
            base_day = before[-1]
            closes = lambda s: dict(series.get(s) or [])
            bench_map = closes(BENCHMARK)
            scores = []
            for row in rows:
                if row["kind"] != "sector":
                    continue
                c = closes(row["symbol"])
                if base_day in c and formed in c and base_day in bench_map and formed in bench_map:
                    scores.append(((c[formed] / c[base_day]) / (bench_map[formed] / bench_map[base_day]) - 1, row))
            scores.sort(key=lambda item: item[0], reverse=True)
            since = len([d for d in month_days if d > formed])
            for rank, (score, row) in enumerate(scores, 1):
                row["month_start"] = {"rank": rank, "of": len(scores), "relative": round(score * 100, 2)}
            month_start = {"formed_on": formed, "sessions_since": since, "typical_run": TYPICAL_RUN_SESSIONS,
                           "leaders": [row["symbol"] for _, row in scores[:3]], "laggards": [row["symbol"] for _, row in scores[-3:]]}
    return {"as_of": today.isoformat(), "live": in_session, "windows": list(WINDOWS), "benchmark": BENCHMARK,
            "stale_symbols": [s for s in dict.fromkeys([BENCHMARK, *symbols])
                              if not series.get(s) or series[s][-1][0] != today.isoformat()],
            "benchmark_change": {k: None if v is None else round(v * 100, 2) for k, v in bench_change.items()},
            "month_start": month_start, "rows": rows}
