"""Full minute history for the watchlist's contracts (owner, 18 Nov 2026: "make watchlist and warm them with full
available history").

A newly listed series (December, after the November last day at E-5) has almost no stored minutes: the warm-up
seeded its indicators from a few sessions of chain marks. This downloads each contract's whole listed life (Fyers
serves up to 100 days of 1-minute bars per request) and writes only the minutes the store lacks -- the same rule as
the feed-gap repair, so it runs under the recovery hold's exemption (self_heal.history_hold_exempt) without
rewriting anything already stored. The next warm-up replays the full history.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
FULL_DAYS = 95            # under Fyers' 100-day limit for 1-minute history; a 3-month series is listed ~90 days
PACE_SECONDS = 0.5


def _stored(database_path: str, symbol: str) -> set[int]:
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30) as db:
        return {row[0] for row in db.execute(
            "SELECT timestamp FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60", (symbol,))}


async def fill(broker, database_path: str, symbols: dict[str, str | None], report: dict, store=None,
               days: int = FULL_DAYS, pace: float = PACE_SECONDS) -> dict:
    """Download ``symbols`` (symbol -> expiry) one at a time, store the missing minutes; updates ``report`` live."""
    from .candle_store import store_historical_candles
    from .self_heal import history_hold_exempt
    store = store or store_historical_candles
    report.update(started_at=datetime.now(IST).isoformat(timespec="seconds"), symbols=len(symbols), done=0,
                  filled=0, rows=0, nothing_new=0, errors={}, finished_at=None, first_minute=None)
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    with history_hold_exempt():
        for symbol, expiry in symbols.items():
            for attempt in range(3):
                try:
                    bars = await broker.history_range(symbol, 60, start, end)
                    have = await asyncio.to_thread(_stored, database_path, symbol)
                    new = [bar for bar in bars if bar.symbol == symbol and bar.timestamp not in have]
                    if new:
                        report["rows"] += await asyncio.to_thread(store, database_path, new, expiry)
                        report["filled"] += 1
                        first = min(bar.timestamp for bar in new)
                        report["first_minute"] = min(report["first_minute"] or first, first)
                    else:
                        report["nothing_new"] += 1
                    report["errors"].pop(symbol, None)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- one contract must not stop the sweep
                    report["errors"][symbol] = f"{type(exc).__name__}: {exc}"[:160]
                    await asyncio.sleep(65.0 if "429" in str(exc) else 3.0)
            report["done"] += 1
            await asyncio.sleep(pace)
    if report["first_minute"]:
        report["first_minute"] = datetime.fromtimestamp(report["first_minute"], IST).isoformat(timespec="minutes")
    report["finished_at"] = datetime.now(IST).isoformat(timespec="seconds")
    return report
