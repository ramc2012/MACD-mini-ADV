"""Market breadth analytics every 15 minutes, stored for an instant page load (owner, 18 Nov 2026: "make the app compute
day high, low, RRG, other analytics every 15 mins and maintain in DB so that opening the page load them instantly with
refresh if kept open").

At start-up (after a short warm-up) and then 45 s after every 15-minute bar of the NSE session (09:30 ... 15:30, plus
15:45 once trading ends at 15:40), each default view is recomputed with ``force`` and stored:

* day highs and day lows (one answer carries both), the ATM heat map, the IV scanner,
* trend rotation for ATM CE, ATM PE and spots at 30 min, 1 h and 1 day,
* the sector wheel and the sector coil (YTD, month ends, own and NIFTY-relative),
* RRG at 30 min, 1 h and 1 day (the page's window 14, tail 15) in its own rrg_snapshots table.

Pages read the stored answer at once (engine._stale_while_revalidate) and keep polling while open; a view with other
parameters is computed on its first request and stored the same way. Read-only: nothing here trades.
"""
from __future__ import annotations

import asyncio
import time as clock
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import market_calendar

IST = ZoneInfo("Asia/Kolkata")
SLOT_MINUTES = 15
SETTLE_SECONDS = 45              # after the bar boundary, so the 15-minute bar is closed and stored
FIRST_SLOT = time(9, 30)
LAST_SLOT = time(15, 45)
STARTUP_DELAY_SECONDS = 90
RRG_VIEWS = ((1800, 14, 15), (3600, 14, 15), (86400, 14, 15))
TREND_VIEWS = tuple((kind, frame) for kind in ("CE", "PE", "SPOT") for frame in (1800, 3600, 86400))
COIL_VIEWS = (("YTD", "M", "own"), ("YTD", "M", "relative"))


def slot_of(now: datetime) -> datetime | None:
    """The session's 15-minute slot ``now`` falls in, once SETTLE_SECONDS past its boundary; None outside them."""
    local = now.astimezone(IST)
    if not market_calendar.is_trading_day(local.date()):
        return None
    start = local.replace(minute=local.minute - local.minute % SLOT_MINUTES, second=0, microsecond=0)
    if (local - start).total_seconds() < SETTLE_SECONDS or not FIRST_SLOT <= start.time() <= LAST_SLOT:
        return None
    return start


def views(engine, rrg) -> list[tuple[str, object]]:
    """(name, coroutine factory) for every default view, cheapest first."""
    out: list[tuple[str, object]] = [
        ("day_highs", lambda: engine.day_highs(force=True)),
        ("atm_heatmap", lambda: engine.atm_heatmap(force=True)),
        ("sector_wheel", lambda: engine.sector_wheel(force=True)),
    ]
    out += [(f"sector_coil:{b}:{s}:{m}", (lambda b=b, s=s, m=m: engine.sector_coil(b, s, m, force=True))) for b, s, m in COIL_VIEWS]
    out += [(f"trend_rotation:{k}:{f}", (lambda k=k, f=f: engine.trend_rotation(k, f, force=True))) for k, f in TREND_VIEWS]
    out += [(f"rrg:{t}:{w}:{n}", (lambda t=t, w=w, n=n: rrg(f"{t}:{w}:{n}", t, w, n))) for t, w, n in RRG_VIEWS]
    out.append(("iv_scanner", lambda: engine.iv_scanner(force=True)))
    return out


async def refresh_all(engine, rrg, reason: str) -> dict:
    report = engine.__dict__.setdefault("analytics_report", {"runs": 0, "views": {}})
    started = datetime.now(UTC)
    for name, factory in views(engine, rrg):
        t0 = clock.monotonic()
        try:
            await factory()
            report["views"][name] = {"at": datetime.now(UTC).isoformat(), "seconds": round(clock.monotonic() - t0, 2),
                                     "error": None}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- one view failing must not stop the others
            report["views"][name] = {"at": datetime.now(UTC).isoformat(), "seconds": round(clock.monotonic() - t0, 2),
                                     "error": f"{type(exc).__name__}: {exc}"[:300]}
    report.update(runs=report["runs"] + 1, last_reason=reason, last_started=started.isoformat(),
                  last_finished=datetime.now(UTC).isoformat(),
                  last_seconds=round((datetime.now(UTC) - started).total_seconds(), 1))
    return report


async def run(engine, rrg, *, startup_delay: float = STARTUP_DELAY_SECONDS) -> None:
    """Forever: one pass after start-up, then one per session slot."""
    await asyncio.sleep(startup_delay)
    await refresh_all(engine, rrg, "start-up")
    done: datetime | None = slot_of(datetime.now(UTC))
    while True:
        await asyncio.sleep(15)
        slot = slot_of(datetime.now(UTC))
        if slot is not None and slot != done:
            done = slot
            await refresh_all(engine, rrg, f"{slot:%H:%M} IST slot")


def next_slot(now: datetime) -> str | None:
    """The next scheduled refresh, for the status line (ISO, IST)."""
    local = now.astimezone(IST)
    for days in range(0, 8):
        day = local.date() + timedelta(days=days)
        if not market_calendar.is_trading_day(day):
            continue
        moment = datetime.combine(day, FIRST_SLOT, IST)
        while moment.time() <= LAST_SLOT:
            due = moment + timedelta(seconds=SETTLE_SECONDS)
            if due > local:
                return due.isoformat()
            moment += timedelta(minutes=SLOT_MINUTES)
    return None
