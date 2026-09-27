"""Repair a trading day's minute candles from Fyers history.

The live writer only records what the socket delivers. When the socket is dead
the bars are simply never written, and nothing notices afterwards — on 19 Aug
2026 the feed missed 09:15-09:43 and 546 of 667 contracts recorded their first
bar at 09:44, while Fyers itself held the full session all along.

    python scripts/backfill_session.py 2026-08-19 [--deadline HH:MM]

Writes with INSERT OR REPLACE on (symbol, timeframe, timestamp), so re-running
is safe and a later official download still wins. Requests are throttled: this
shares a rate limit with the live engine.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import pathlib
import sqlite3
import sys
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")
RUNTIME = pathlib.Path(__file__).resolve().parents[1] / "runtime"
HISTORY_URL = "https://api-t1.fyers.in/data/history"
CONCURRENCY = 4
PER_REQUEST_PAUSE = 0.12
# A fully missed session stores no symbols of its own; look this many
# calendar days either side for the universe that was actually live.
LOOKASIDE_DAYS = 5
MIN_EXPECTED_SYMBOLS = 200


def asset_type(symbol: str) -> str:
    if symbol.endswith("-INDEX"):
        return "index"
    if symbol.endswith("-EQ"):
        return "stock"
    return "option"


def load_auth() -> str:
    creds = json.loads((RUNTIME / "credentials.json").read_text())
    return f"{creds['fyers_client_id']}:{creds['fyers_access_token']}"


def symbols_on(db: sqlite3.Connection, day: dt.date) -> set[str]:
    lo = int(dt.datetime.combine(day, dt.time(0, 0), IST).timestamp())
    return {r[0] for r in db.execute(
        "SELECT DISTINCT symbol FROM historical_candles WHERE timestamp BETWEEN ? AND ?",
        (lo, lo + 86_400))}


def load_symbols(db: sqlite3.Connection, day: dt.date) -> list[str]:
    """Every symbol that was live around that day, plus the current snapshot.

    A day the socket missed ENTIRELY has no stored symbols of its own, so
    asking the store "what did you see that day" returned nothing and the
    repair silently covered only whatever atm_contracts.json happened to hold.
    Fall back to the nearest sessions that do have rows: the universe on the
    trading days either side of the gap is what was tradable inside it.
    """
    rows = symbols_on(db, day)
    for offset in range(1, LOOKASIDE_DAYS + 1):
        if len(rows) >= MIN_EXPECTED_SYMBOLS:
            break
        rows |= symbols_on(db, day - dt.timedelta(days=offset))
        rows |= symbols_on(db, day + dt.timedelta(days=offset))
    snapshot = RUNTIME / "atm_contracts.json"
    if snapshot.exists():
        try:
            data = json.loads(snapshot.read_text())
            entries = data.get("contracts", data) if isinstance(data, dict) else data
            for item in (entries.values() if isinstance(entries, dict) else entries):
                sym = item.get("symbol") if isinstance(item, dict) else item
                if isinstance(sym, str):
                    rows.add(sym)
        except (ValueError, AttributeError):
            pass
    return sorted(rows)


async def fetch(client: httpx.AsyncClient, auth: str, symbol: str, day: str) -> list[list]:
    for attempt in range(4):
        try:
            response = await client.get(HISTORY_URL, params={
                "symbol": symbol, "resolution": "1", "date_format": "1",
                "range_from": day, "range_to": day, "cont_flag": "1",
            }, headers={"Authorization": auth}, timeout=30)
        except httpx.HTTPError:
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        if response.status_code == 429:
            await asyncio.sleep(2.5 * (attempt + 1))
            continue
        if response.status_code != 200:
            return []
        payload = response.json()
        return payload.get("candles", []) if payload.get("s") == "ok" else []
    return []


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("day")
    parser.add_argument("--deadline", default=None, help="stop by this IST time, e.g. 08:55")
    args = parser.parse_args()
    day = dt.date.fromisoformat(args.day)
    deadline = None
    if args.deadline:
        hh, mm = (int(x) for x in args.deadline.split(":"))
        deadline = dt.datetime.combine(dt.date.today(), dt.time(hh, mm), IST)

    lo = int(dt.datetime.combine(day, dt.time(9, 15), IST).timestamp())
    hi = int(dt.datetime.combine(day, dt.time(15, 30), IST).timestamp())

    db = sqlite3.connect(str(RUNTIME / "historical.sqlite3"), timeout=60)
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("PRAGMA synchronous=FULL")
    symbols = load_symbols(db, day)
    before = db.execute(
        "SELECT COUNT(*) FROM historical_candles WHERE timeframe_seconds=60 AND timestamp BETWEEN ? AND ?",
        (lo, hi)).fetchone()[0]
    print(f"{day}: {len(symbols)} symbols, {before:,} bars already stored")

    auth = load_auth()
    stamp = dt.datetime.now(dt.UTC).isoformat()
    gate = asyncio.Semaphore(CONCURRENCY)
    written = failed = skipped = 0
    done = 0

    async with httpx.AsyncClient() as client:
        async def worker(symbol: str) -> None:
            nonlocal written, failed, skipped, done
            if deadline and dt.datetime.now(IST) >= deadline:
                skipped += 1
                return
            async with gate:
                bars = await fetch(client, auth, symbol, args.day)
                await asyncio.sleep(PER_REQUEST_PAUSE)
            if not bars:
                failed += 1
            else:
                rows = [(symbol, 60, int(b[0]), float(b[1]), float(b[2]), float(b[3]),
                         float(b[4]), int(b[5]), asset_type(symbol), None, stamp)
                        for b in bars if lo <= int(b[0]) <= hi]
                if rows:
                    db.executemany(
                        """INSERT OR REPLACE INTO historical_candles
                           (symbol, timeframe_seconds, timestamp, open, high, low, close,
                            volume, asset_type, expiry, downloaded_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
                    written += len(rows)
            done += 1
            if done % 100 == 0:
                db.commit()
                print(f"   {done}/{len(symbols)} symbols · {written:,} bars written", flush=True)

        await asyncio.gather(*(worker(s) for s in symbols))

    db.commit()
    after = db.execute(
        "SELECT COUNT(*) FROM historical_candles WHERE timeframe_seconds=60 AND timestamp BETWEEN ? AND ?",
        (lo, hi)).fetchone()[0]
    minutes = db.execute(
        "SELECT COUNT(DISTINCT timestamp) FROM historical_candles WHERE timeframe_seconds=60 AND timestamp BETWEEN ? AND ?",
        (lo, hi)).fetchone()[0]
    db.close()
    print(f"\n{day}: {before:,} -> {after:,} bars (+{after - before:,})")
    print(f"   session minutes covered: {minutes}/375 · no-data symbols: {failed} · skipped(deadline): {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
