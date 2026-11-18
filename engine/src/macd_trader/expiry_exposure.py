"""Expiry exposure (owner, 18 Nov 2026): every lane's stock options and stock futures against the physical-settlement
margin calendar of their series.

NSE stock derivatives settle by delivery. From E-4 (four sessions before expiry) the clearing corporation levies a
delivery margin on in-the-money long options and on futures and short options: 10% / 25% / 45% / 70% of the
delivery margin on E-4 / E-3 / E-2 / E-1, and brokers block it at the START of the day (Upstox; several ask earlier
or square off). Index options are cash settled and are not listed here.

Per held contract: its series' E-5..E-1 days, today's stage, whether it is in the money now (calls above, puts below
the strike; futures always), the contract value at the live spot, and a rough delivery-margin estimate:
DELIVERY_RATE of the contract value (stock VaR + ELM is roughly 15-30%; the broker's figure governs) times the
stage's share. Measurement only: nothing here exits or rolls a position (rollover.stock_exit_due does, at the
configured ``stock_option_exit_sessions``).
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import market_calendar, rollover
from .universe import INDEX_SPOTS

IST = ZoneInfo("Asia/Kolkata")
DELIVERY_RATE = 0.20
STAGES = {4: 10, 3: 25, 2: 45, 1: 70}


def sessions_before(expiry: date, today: date) -> int | None:
    """Trading sessions from ``today`` to ``expiry`` (0 on expiry day), or None once expired."""
    if today > expiry:
        return None
    n, cursor = 0, expiry
    while cursor > today and n < 60:
        cursor = market_calendar.previous_trading_day(cursor)
        n += 1
    return n


def stage_pct(sessions: int | None) -> int:
    return STAGES.get(sessions, 0) if sessions is not None else 0


def contract_row(lane: str, symbol: str, quantity: float, average: float, last: float | None, spot: float | None,
                 today: date) -> dict | None:
    from .option_agent import parse_option
    option = rollover.is_stock_option(symbol)
    root = rollover.futures_root(symbol)
    future = (not option) and root is not None and root[1] not in INDEX_SPOTS
    if not (option or future):
        return None
    if option:
        parsed = parse_option(symbol)
        if not parsed:
            return None
        expiry, strike, call = date.fromisoformat(parsed["expiry"]), parsed["strike"], parsed["side"] == 1
    else:
        body = symbol.split(":", 1)[-1]
        try:
            month = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"].index(body[-6:-3]) + 1
            year = 2000 + int(body[-8:-6])
        except (ValueError, IndexError):
            return None
        from .option_agent import _last_weekday, _trading_on_or_before
        expiry = _trading_on_or_before(_last_weekday(year, month, 1))
        strike, call = None, None
    sessions = sessions_before(expiry, today)
    short = quantity < 0
    itm = True if future else (spot is not None and ((spot > strike) if call else (spot < strike)))
    liable = future or short or itm
    lot_value = abs(quantity) * spot if spot else None
    pct_today = stage_pct(sessions) if liable else 0
    return {"lane": lane, "symbol": symbol, "kind": "FUT" if future else ("CE" if call else "PE"),
            "quantity": quantity, "average_price": average, "last_price": last, "strike": strike, "spot": spot,
            "expiry": expiry.isoformat(), "sessions_to_expiry": sessions,
            "last_day": rollover.stock_last_day(expiry).isoformat() if option and rollover.STOCK_EXIT_SESSIONS else None,
            "itm": itm, "liable": liable, "stage_pct": pct_today,
            "contract_value": round(lot_value, 2) if lot_value else None,
            "margin_today": round(lot_value * DELIVERY_RATE * pct_today / 100, 2) if lot_value else None,
            "margin_e1": round(lot_value * DELIVERY_RATE * 0.70, 2) if lot_value and liable else 0.0}


def report(lanes: list[tuple[str, object]], spot_price, now: datetime | None = None) -> dict:
    """``lanes``: (name, execution) pairs; ``spot_price(symbol) -> float | None`` for underlying spots."""
    from .option_agent import parse_option
    now = (now or datetime.now(IST)).astimezone(IST)
    today = now.date()
    rows = []
    for lane, execution in lanes:
        portfolio = getattr(execution, "portfolio", None)
        for symbol, position in list(getattr(portfolio, "positions", {}).items()):
            parsed = parse_option(symbol)
            spot_symbol = parsed["spot"] if parsed else None
            if spot_symbol is None and rollover.is_futures_symbol(symbol):
                root = rollover.futures_root(symbol)
                spot_symbol = f"{root[0]}:{root[1]}-EQ" if root else None
            row = contract_row(lane, symbol, float(position.quantity), float(position.average_price),
                               float(position.last_price or 0) or None,
                               spot_price(spot_symbol) if spot_symbol else None, today)
            if row:
                rows.append(row)
    rows.sort(key=lambda r: (r["sessions_to_expiry"] if r["sessions_to_expiry"] is not None else 99, r["lane"], r["symbol"]))
    per_lane: dict[str, dict] = {}
    for r in rows:
        s = per_lane.setdefault(r["lane"], {"lane": r["lane"], "positions": 0, "liable": 0, "margin_today": 0.0, "margin_e1": 0.0})
        s["positions"] += 1
        s["liable"] += int(r["liable"])
        s["margin_today"] += r["margin_today"] or 0
        s["margin_e1"] += r["margin_e1"] or 0
    from .strategy_builder import delivery_schedule
    expiries = sorted({r["expiry"] for r in rows})
    return {"as_of": now.isoformat(timespec="seconds"), "delivery_rate": DELIVERY_RATE, "stages": STAGES,
            "schedules": {e: delivery_schedule(e) for e in expiries},
            "stock_option_exit_sessions": rollover.STOCK_EXIT_SESSIONS, "rows": rows,
            "lanes": [{**v, "margin_today": round(v["margin_today"], 2), "margin_e1": round(v["margin_e1"], 2)}
                      for v in per_lane.values()]}
