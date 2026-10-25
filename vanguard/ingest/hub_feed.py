"""Vanguard's live inputs through the market-intelligence hub (2026-10-25, when Vanguard moved to MACD-mini-ADV).

Nomad Curie's backend used to write the two tables Vanguard's live pass reads, and it is retired:

  option_premium_candles  interval '30minute', source 'upstox_chain' -- one Upstox option-chain snapshot per
                          (underlying, expiry) per 30-minute bar, with greeks (was market_data/upstox_chain_builder.py)
  underlying_spot_candles interval '30minute', source 'upstox_sweep' -- 30-minute spot bars built only from
                          contiguous completed 3-minute bars (was market_data/stock_spot_sweeper.py)

Same tables, same sources, same grid, so Vanguard's code reads them unchanged. Every broker call goes through the hub
(POST /v1/request, caller "vanguard"), which owns the Upstox budget and the analytics token.

TIMING. NSE 30-minute bars are labelled by their start on the :15/:45 grid (09:15 ... 15:15; the last one is 15 minutes
wide). The chain sweep runs CHAIN_LEAD_S before each bar ends, so the snapshot labelled with a bar is its closing
reading; the spot sweep runs SPOT_LAG_S after the bar ends, when its last 3-minute bar is complete. Both land before
Vanguard's live pass (bar close + VANGUARD_LIVE_PASS_DELAY_S).

IV UNIT. Upstox reports iv in percent (27.34); option_premium_candles.iv is a fraction (0.2734). iv_fraction() converts.

    python ingest/hub_feed.py            run the loop (market hours only)
    python ingest/hub_feed.py --once     one chain sweep and one spot sweep now, then exit
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import psycopg2
from psycopg2.extras import execute_values
from psycopg2.extensions import make_dsn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from model.market_calendar import calendar, is_session  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
HUB_URL = os.environ.get("MI_HUB_URL", "http://host.docker.internal:8400")
DSN = os.environ.get("VANGUARD_DATABASE_URL")
CHAIN_SOURCE, SPOT_SOURCE, INTERVAL = "upstox_chain", "upstox_sweep", "30minute"
CHAIN_LEAD_S = int(os.environ.get("VANGUARD_CHAIN_LEAD_S", "150"))
SPOT_LAG_S = int(os.environ.get("VANGUARD_SPOT_LAG_S", "25"))
SESSION_OPEN, SESSION_CLOSE = (9, 15), (15, 30)


def log(message: str) -> None:
    print(f"{datetime.now(IST):%Y-%m-%d %H:%M:%S} IST | hub-feed | {message}", flush=True)


# ------------------------------------------------------------------ pure helpers (unit-tested)
def iv_fraction(raw) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0 or not math.isfinite(value):
        return None
    return value / 100.0


def bar_start(ts: datetime) -> datetime:
    """The NSE 30-minute bar (09:15 grid) holding `ts`, as an aware IST datetime."""
    local = ts.astimezone(IST)
    anchor = local.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1], second=0, microsecond=0)
    minutes = int((local - anchor).total_seconds() // 60)
    return anchor + timedelta(minutes=30 * (minutes // 30))


def bar_ends(day: date, derivatives=False) -> list[datetime]:
    """End times of the day's bars: 09:45, 10:15 ... 15:15, then 15:30 for the 15-minute last bar."""
    start = datetime(day.year, day.month, day.day, *SESSION_OPEN, tzinfo=IST)
    ends = [start + timedelta(minutes=30 * k) for k in range(1, 13)]
    from model.session_clock import session_close
    close = session_close(day) if derivatives else datetime.min.time().replace(hour=15, minute=30)
    return ends + [datetime.combine(day, close, IST)]


def select_expiry_universe(rows: list[tuple[str, str, date, str]], horizon: date) -> list[tuple[str, str, date]]:
    """Indices keep front + next expiry; a stock keeps the first expiry that survives a D+3 hold (Nomad's rule)."""
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row)
    selected = []
    for contracts in grouped.values():
        ordered = sorted(contracts, key=lambda r: r[2])
        if ordered[0][3] == "INDEX":
            selected.extend((s, k, e) for s, k, e, _ in ordered[:2])
        else:
            chosen = next((r for r in ordered if r[2] >= horizon), ordered[0])
            selected.append((chosen[0], chosen[1], chosen[2]))
    return selected


def third_session(start: date) -> date:
    day, sessions = start, 0
    while sessions < 3:
        day += timedelta(days=1)
        sessions += bool(is_session(day))
    return day


def chain_rows(underlying: str, expiry: date, body: dict, at: datetime, previous_volume: dict) -> list[tuple]:
    """Upstox /v2/option/chain body -> option_premium_candles rows for the bar holding `at`.

    Volume is the bar's share of the cumulative day volume (difference from this contract's previous snapshot today;
    0 for the first one). Unquoted strikes (ltp <= 0) are skipped rather than written as fake bars.
    """
    stamp = bar_start(at)
    out = []
    for strike in body.get("data") or []:
        spot = strike.get("underlying_spot_price")
        for side, option_type in (("call_options", "CE"), ("put_options", "PE")):
            leg = strike.get(side) or {}
            market, greeks = leg.get("market_data") or {}, leg.get("option_greeks") or {}
            key, ltp = leg.get("instrument_key"), market.get("ltp")
            if not key or not ltp or not math.isfinite(float(ltp)) or float(ltp) <= 0:
                continue
            cumulative = int(market.get("volume") or 0)
            prior = previous_volume.get(key)
            volume = max(0, cumulative - prior[1]) if prior and prior[0] == stamp.date() else None
            previous_volume[key] = (stamp.date(), cumulative)
            market_name = "BSE" if str(key).startswith("BSE_") else "NSE"
            out.append((stamp, underlying, market_name, expiry, float(strike.get("strike_price") or 0), option_type,
                        float(ltp), float(ltp), float(ltp), float(ltp), volume, int(market.get("oi") or 0),
                        iv_fraction(greeks.get("iv")), greeks.get("delta"), greeks.get("gamma"), greeks.get("theta"),
                        greeks.get("vega"), float(spot) if spot else None, key, None, INTERVAL, CHAIN_SOURCE))
    return out


def aggregate_30minute(candles: list[list], now: datetime) -> list[dict]:
    """Upstox 3-minute candles [ts, o, h, l, c, v, oi] -> completed 30-minute bars from contiguous 3-minute bars only
    (10 per bar, 5 for the 15:15 bar), exactly as Nomad's sweeper built them."""
    groups: dict[datetime, dict] = {}
    for c in candles or []:
        try:
            parsed = datetime.fromisoformat(str(c[0]).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                continue
            stamp = parsed.astimezone(IST)
            row = {"open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]),
                   "volume": int(c[5] or 0) if len(c) > 5 else 0}
        except (TypeError, ValueError, IndexError):
            continue
        if stamp.date() != now.astimezone(IST).date() or not all(math.isfinite(row[k]) and row[k] > 0 for k in ("open", "high", "low", "close")):
            continue
        if row["high"] < max(row["open"], row["close"]) or row["low"] > min(row["open"], row["close"]):
            continue
        if stamp + timedelta(minutes=3) > now:
            continue
        anchor = stamp.replace(hour=9, minute=15, second=0, microsecond=0)
        offset = (stamp - anchor).total_seconds() / 60
        if offset < 0 or offset >= 375 or offset % 3:
            continue
        groups.setdefault(anchor + timedelta(minutes=30 * int(offset // 30)), {})[stamp] = row
    bars = []
    for start, group in sorted(groups.items()):
        count = 5 if (start.hour, start.minute) == (15, 15) else 10
        expected = [start + timedelta(minutes=3 * i) for i in range(count)]
        if start + timedelta(minutes=3 * count) > now or set(group) != set(expected):
            continue
        ordered = [group[t] for t in expected]
        bars.append({"time": start, "open": ordered[0]["open"], "high": max(r["high"] for r in ordered),
                     "low": min(r["low"] for r in ordered), "close": ordered[-1]["close"],
                     "volume": sum(r["volume"] for r in ordered)})
    return bars


# ------------------------------------------------------------------ hub + database
def hub(http: httpx.Client, endpoint: str, *, query: dict | None = None, path: str | None = None) -> dict:
    body = {"broker": "upstox", "endpoint": endpoint, "priority": "normal", "caller": "vanguard",
            "query": query or {}}
    if path:
        body["path"] = path
    response = http.post(f"{HUB_URL}/v1/request", json=body)
    if response.status_code != 200:
        raise RuntimeError(f"Hub data request failed (HTTP {response.status_code})")
    data = response.json()
    if not isinstance(data, dict) or data.get("status") == "error":
        raise RuntimeError("Broker data response unavailable")
    return data


def refresh_contract_catalog(conn, http):
    """Exact identity/lot metadata from the already-loaded hub master; no broker download here.

    Update reference fields only. Candle coverage, sync state and retained history stay intact.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT symbol,kind FROM fo_underlying_catalog WHERE kind='INDEX' OR fno_active IS TRUE ORDER BY symbol")
        names = cur.fetchall()
    updated = 0
    for symbol, kind in names:
        exchange = "BSE" if symbol in ("SENSEX", "BANKEX") else "NSE"
        response = http.get(f"{HUB_URL}/v1/options", params={"underlying": symbol, "exchange": exchange, "expiries": 3})
        response.raise_for_status()
        contracts = []
        for row in response.json():
            key, lot = row.get("upstox"), row.get("lot_size")
            if not key or not lot or lot <= 0 or not row.get("expiry_ms") or row.get("option_type") not in ("CE", "PE"):
                continue
            expiry = datetime.fromtimestamp(row["expiry_ms"] / 1000, IST).date()
            contracts.append((key, row.get("canonical"), symbol, expiry, row["strike"], row["option_type"], lot, row.get("tick_size"), exchange))
        if contracts:
            with conn, conn.cursor() as cur:
                execute_values(cur, """INSERT INTO fo_contract_catalog
                    (instrument_key,trading_symbol,underlying,expiry,strike,option_type,lot_size,tick_size,market)
                    VALUES %s ON CONFLICT(instrument_key) DO UPDATE SET
                    lot_size=excluded.lot_size,tick_size=excluded.tick_size,updated_at=now()""", contracts, page_size=2000)
            updated += len(contracts)
    return updated


def load_universe(conn) -> tuple[list[tuple[str, str, date]], list[tuple[str, str]]]:
    """(chain pairs: symbol, Upstox underlying key, expiry) and (spot: symbol, spot instrument key)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT underlying, underlying_key, expiry, kind FROM (
                SELECT c.underlying, u.underlying_key, c.expiry, u.kind,
                       ROW_NUMBER() OVER (PARTITION BY c.underlying ORDER BY c.expiry) AS rn
                FROM (SELECT DISTINCT underlying, expiry FROM fo_contract_catalog WHERE expiry >= CURRENT_DATE) c
                JOIN fo_underlying_catalog u ON u.symbol = c.underlying
                WHERE u.underlying_key IS NOT NULL AND (u.kind='INDEX' OR u.fno_active IS TRUE)) ranked
            WHERE rn <= 2 ORDER BY underlying, expiry""")
        chains = select_expiry_universe(cur.fetchall(), third_session(datetime.now(IST).date()))
        cur.execute("""SELECT symbol, spot_instrument_key FROM fo_underlying_catalog
                       WHERE spot_instrument_key LIKE '%|%' AND (kind='INDEX' OR fno_active IS TRUE)
                       ORDER BY kind DESC, symbol""")
        spots = cur.fetchall()
    return chains, spots


def chain_sweep(conn, http: httpx.Client, chains, previous_volume: dict) -> dict:
    rows, failed = [], {}
    # Stage baselines; a rolled-back write must not consume volume from the next retry.
    staged = dict(previous_volume)
    for symbol, key, expiry in chains:
        try:
            at = datetime.now(IST)
            from model.session_clock import session_close
            if not is_session(at.date()) or not datetime.min.time().replace(hour=9, minute=15) <= at.time() < session_close(at.date()):
                failed[symbol] = "outside_derivative_session"
                continue
            body = hub(http, "option/chain", query={"instrument_key": key, "expiry_date": expiry.isoformat()})
            at = datetime.now(IST)
            per_chain = dict(staged)
            accepted = chain_rows(symbol, expiry, body, at, per_chain)
            if not accepted:
                raise ValueError("No valid quoted contracts")
            rows += accepted
            staged = per_chain
            publish_snapshot(http, symbol, expiry, body, at)
        except Exception as exc:  # noqa: BLE001 -- one bad name must not stop the sweep
            failed[symbol] = type(exc).__name__
    if rows:
        with conn, conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO option_premium_candles (time, underlying, market, expiry, strike, option_type, open, high,
                    low, close, volume, oi, iv, delta, gamma, theta, vega, underlying_price, instrument_key,
                    trading_symbol, interval, source) VALUES %s
                ON CONFLICT (instrument_key, interval, time) DO UPDATE SET close=EXCLUDED.close,
                    high=GREATEST(option_premium_candles.high, EXCLUDED.high),
                    low=LEAST(option_premium_candles.low, EXCLUDED.low),
                    volume=CASE WHEN EXCLUDED.volume IS NULL THEN option_premium_candles.volume
                                ELSE coalesce(option_premium_candles.volume,0)+EXCLUDED.volume END, oi=EXCLUDED.oi,
                    iv=EXCLUDED.iv, delta=EXCLUDED.delta, gamma=EXCLUDED.gamma, theta=EXCLUDED.theta,
                    vega=EXCLUDED.vega, underlying_price=EXCLUDED.underlying_price, synced_at=now()
                WHERE option_premium_candles.source = 'upstox_chain'""", rows, page_size=2000)
            execute_values(cur, """INSERT INTO macd_lane_ingest_state(instrument,session,cum_volume)
                VALUES %s ON CONFLICT(instrument) DO UPDATE SET session=excluded.session,cum_volume=excluded.cum_volume""",
                [(key, day, volume) for key, (day, volume) in staged.items()], page_size=2000)
        previous_volume.clear()
        previous_volume.update(staged)
    return {"pairs": len(chains), "rows": len(rows), "underlyings": len({r[1] for r in rows}), "failed": len(failed),
            "failed_sample": dict(list(failed.items())[:3])}


def spot_sweep(conn, http: httpx.Client, spots) -> dict:
    now, written, failed = datetime.now(IST), 0, {}
    for symbol, key in spots:
        try:
            body = hub(http, "historical-candle/intraday", path=f"{key}/minutes/3")
            bars = aggregate_30minute(((body.get("data") or {}).get("candles") or []), now)
        except Exception as exc:  # noqa: BLE001
            failed[symbol] = type(exc).__name__
            continue
        if not bars:
            failed[symbol] = "missing_completed_bars"
            continue
        with conn, conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO underlying_spot_candles (time, instrument_key, underlying, interval, open, high, low, close,
                    volume, oi, source) VALUES %s
                ON CONFLICT (instrument_key, interval, "time") DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high,
                    low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, source=EXCLUDED.source,
                    synced_at=NOW()
                WHERE underlying_spot_candles.source IN ('live_tick', 'upstox_sweep', 'upstox_spot')""",
                [(b["time"], key, symbol, INTERVAL, b["open"], b["high"], b["low"], b["close"], b["volume"], 0,
                  SPOT_SOURCE) for b in bars])
        written += len(bars)
    return {"names": len(spots), "bars": written, "failed": len(failed), "failed_sample": dict(list(failed.items())[:3])}


# ------------------------------------------------------------------ loop
def next_jobs(now: datetime) -> list[tuple[datetime, str]]:
    """Include recent due jobs: sleeping past a deadline must not silently discard the sweep."""
    if not is_session(now.date()):
        return []
    jobs = []
    jobs += [(end - timedelta(seconds=CHAIN_LEAD_S), "chain") for end in bar_ends(now.date(), derivatives=True)]
    jobs += [(end + timedelta(seconds=SPOT_LAG_S), "spot") for end in bar_ends(now.date())]
    return sorted(j for j in jobs if j[0] >= now - timedelta(minutes=5))


def connection():
    dsn = DSN or make_dsn(host=os.environ.get("MI_PG_HOST", "nomadcurie_db"),
        dbname=os.environ.get("LANE_SOURCE_DB", "nomadcurie"), user=os.environ["MI_PG_USER"], password=os.environ["MI_PG_PASSWORD"])
    conn = psycopg2.connect(dsn, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout='30s'")
    return conn


def publish_snapshot(http, underlying, expiry, body, at):
    """Same contract-v1 stream as the hub; the existing recorder owns persistence in md_chain."""
    if not os.environ.get("LANE_NATS"):
        return
    keys = [leg.get("instrument_key") for strike in body.get("data") or []
            for leg in (strike.get("call_options") or {}, strike.get("put_options") or {}) if leg.get("instrument_key")]
    response = http.post(f"{HUB_URL}/v1/resolve/batch", json={"upstox": keys})
    response.raise_for_status()
    canonical = response.json()
    stamp = int(at.timestamp() * 1000)
    rows = []
    for strike in body.get("data") or []:
        for field, side in (("call_options", "CE"), ("put_options", "PE")):
            leg = strike.get(field) or {}
            market, greeks = leg.get("market_data") or {}, leg.get("option_greeks") or {}
            key = leg.get("instrument_key")
            if not key:
                continue
            rows.append(dict(v=1, broker="upstox", underlying=underlying, expiry=str(expiry),
                strike=strike.get("strike_price", 0), option_type=side,
                symbol=(canonical.get(key) or {}).get("canonical") or key, snapshot_ts_ms=stamp,
                ltp=market.get("ltp"), volume=market.get("volume"), oi=market.get("oi"), prev_oi=market.get("prev_oi"),
                bid=market.get("bid_price"), bid_qty=market.get("bid_qty"), ask=market.get("ask_price"), ask_qty=market.get("ask_qty"),
                iv=greeks.get("iv"), delta=greeks.get("delta"), gamma=greeks.get("gamma"), theta=greeks.get("theta"),
                vega=greeks.get("vega"), spot=strike.get("underlying_spot_price")))
    payload = json.dumps(dict(v=1, broker="upstox", underlying=underlying, expiry=str(expiry),
        snapshot_ts_ms=stamp, rows=rows, sample_type="broker_chain_snapshot"), allow_nan=False).encode()
    host, _, port = os.environ["LANE_NATS"].removeprefix("nats://").partition(":")
    subject = "md.chain.upstox." + "".join("_" if c in ".*> \t\n\r" else c for c in underlying)
    with socket.create_connection((host, int(port or 4222)), timeout=5) as sock:
        stream = sock.makefile("rb")
        if not stream.readline().startswith(b"INFO "):
            raise RuntimeError("NATS handshake unavailable")
        sock.sendall(b'CONNECT {"verbose":false,"name":"macd-lane-chain"}\r\n' +
            f"PUB {subject} {len(payload)}\r\n".encode() + payload + b"\r\nPING\r\n")
        while True:
            reply = stream.readline()
            if reply.startswith(b"PONG"):
                break
            if reply.startswith(b"PING"):
                sock.sendall(b"PONG\r\n")
            elif not reply or reply.startswith(b"-ERR"):
                raise RuntimeError("NATS publication unavailable")


def write_status(value):
    path = Path(os.environ.get("LANE_FEED_STATUS", "/runtime/lane_feed/status.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"checked_at": datetime.now(IST).isoformat(), **value}, default=str))
    temp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    previous_volume: dict = {}
    universe_day, chains, spots = None, [], []
    completed = set()
    with httpx.Client(timeout=30) as http:
        while True:
            now = datetime.now(IST)
            if not is_session(now.date()):
                write_status({"status": "closed", "reason": "No broker calls outside a trading session"})
                if args.once:
                    return
                time.sleep(60)
                continue
            if args.once and not datetime.min.time().replace(hour=9, minute=15) <= now.time() < datetime.min.time().replace(hour=15, minute=40):
                write_status({"status": "closed", "reason": "No broker calls outside session hours"})
                return
            conn = None
            try:
                conn = connection()
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_try_advisory_lock(hashtext('macd-shadow-lane-feed'))")
                    if not cur.fetchone()[0]:
                        raise RuntimeError("Another lane feed owns the writer lock")
                if universe_day != now.date():
                    log(f"hub contract-reference rows: {refresh_contract_catalog(conn, http)}")
                    chains, spots = load_universe(conn)
                    universe_day = now.date()
                    completed.clear()
                    with conn.cursor() as cur:
                        cur.execute("SELECT instrument,session,cum_volume FROM macd_lane_ingest_state WHERE session=%s", (now.date(),))
                        previous_volume = {key: (day, volume) for key, day, volume in cur.fetchall()}
                    log(f"universe {len(chains)} chain pairs ({len({c[0] for c in chains})} underlyings), {len(spots)} spot names")
                if args.once:
                    log(f"chain sweep: {chain_sweep(conn, http, chains, previous_volume)}")
                    log(f"spot sweep: {spot_sweep(conn, http, spots)}")
                    return
                jobs = [j for j in next_jobs(now) if j not in completed]
                if not jobs:
                    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), IST)
                    conn.close()
                    time.sleep(min(3600, max(60, (tomorrow - now).total_seconds() + 5)))
                    continue
                when, job = jobs[0]
                wait = (when - datetime.now(IST)).total_seconds()
                if wait > 0:
                    conn.close()
                    time.sleep(min(wait, 900))
                    continue
                started = time.monotonic()
                result = (chain_sweep(conn, http, chains, previous_volume) if job == "chain"
                          else spot_sweep(conn, http, spots))
                completed.add((when, job))
                write_status({"status": "ok" if not result.get("failed") else "partial", "job": job, "scheduled_at": when, **result})
                log(f"{job} sweep for the bar starting {bar_start(when - timedelta(seconds=1 if job == 'chain' else SPOT_LAG_S + 1)):%H:%M}: "
                    f"{result} in {time.monotonic() - started:.0f}s")
                time.sleep(1)
            except Exception as exc:  # noqa: BLE001 -- the loop outlives a bad sweep; the next bar retries
                log(f"error: {type(exc).__name__}")
                write_status({"status": "blocked", "error": type(exc).__name__})
                time.sleep(30)
            finally:
                if conn is not None and not conn.closed:
                    conn.close()


if __name__ == "__main__":
    main()
