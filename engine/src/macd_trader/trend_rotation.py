"""Absolute price rotation, with two premium states and three spot states.

Descriptive, completed-bar indicators; no benchmark, broker calls or execution hooks.
The exact option symbol is loaded independently: ATM contracts are never stitched.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta, time
from zoneinfo import ZoneInfo

from . import market_calendar
from .chart_history import aggregate_session_candles
from .models import Candle
from .rrg import _ema
from .sectors import sector_of
from .sector_wheel import label
from .sessions import session_close

IST = ZoneInfo('Asia/Kolkata')
FAST, SLOW, SCALE = 5, 20, 14


def latest_completed(symbol: str, timeframe: int, now: datetime) -> tuple[int, int]:
    local = now.astimezone(IST)
    day = local.date()
    while True:
        if market_calendar.is_trading_day(day):
            opened = datetime.combine(day, time(9, 15), IST)
            closed = datetime.combine(day, session_close(symbol, day), IST)
            duration = int((closed - opened).total_seconds())
            elapsed = int((local - opened).total_seconds())
            if local >= closed:
                start = int(opened.timestamp()) + ((duration - 1) // timeframe) * timeframe
                return start, int(closed.timestamp())
            if elapsed >= timeframe:
                start = int(opened.timestamp()) + (elapsed // timeframe - 1) * timeframe
                return start, start + timeframe
        day -= timedelta(days=1)


def state(x: float, y: float, kind: str) -> str:
    if x > 0 and y > 0:
        return 'up'
    if kind == 'SPOT' and x < 0 and y < 0:
        return 'down'
    return 'fading'


def points(closes: list[tuple[int, float]], kind: str, tail: int) -> list[dict]:
    prices = [p for _, p in closes]
    fast, slow = _ema(prices, FAST), _ema(prices, SLOW)
    out = []
    for i in range(SLOW - 1, len(prices)):
        moves = [abs(prices[j] - prices[j-1]) for j in range(i-SCALE+1, i+1)]
        scale = max(sum(moves) / SCALE, prices[i] * 1e-6)
        x = (fast[i] - slow[i]) / scale
        y = (fast[i] - fast[i-3]) / (3 * scale)
        out.append({'timestamp': closes[i][0], 'x': round(x, 4), 'y': round(y, 4),
                    'state': state(x, y, kind)})
    return out[-tail:]


def _load(db, symbol, timeframe, cutoff):
    if timeframe == 86400 and symbol.endswith(('-EQ', '-INDEX')):
        try:
            daily = db.execute('SELECT day,close FROM daily_closes WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT 60',
                               (symbol, datetime.fromtimestamp(cutoff, IST).date().isoformat())).fetchall()
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc):
                raise
            daily = []
        if daily:
            values = sorted((int(datetime.combine(datetime.fromisoformat(d).date(), time(9,15), IST).timestamp()), float(p))
                            for d,p in daily if p and math.isfinite(p))
            latest_day = datetime.fromtimestamp(values[-1][0], IST).date() if values else None
            price_at = int(datetime.combine(latest_day, session_close(symbol, latest_day), IST).timestamp()) if latest_day else None
            return values, price_at
    limit = 60 * min(timeframe // 60, 400)
    source = db.execute('''SELECT timestamp,open,high,low,close,volume FROM (
        SELECT timestamp,open,high,low,close,volume FROM historical_candles
        WHERE symbol=? AND timeframe_seconds=60 AND timestamp<? ORDER BY timestamp DESC LIMIT ?)
        ORDER BY timestamp''', (symbol, cutoff, limit)).fetchall()
    candles = [Candle(symbol, int(r[0]), *[float(v) for v in r[1:5]], int(r[5] or 0), True)
               for r in source if all(v is not None and math.isfinite(v) and v > 0 for v in r[1:5])]
    return [(c.timestamp, c.close) for c in aggregate_session_candles(candles, timeframe)], candles[-1].timestamp if candles else None


def build(database_path: str, instruments: list[dict], kind: str = 'CE', timeframe: int = 1800,
          tail: int = 8, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    rows, unavailable = [], []
    with sqlite3.connect(f'file:{database_path}?mode=ro', uri=True, timeout=15) as db:
        for instrument in instruments:
            symbol = instrument['symbol']
            expected, end = latest_completed(symbol, timeframe, now)
            loaded, price_at = _load(db, symbol, timeframe, end)
            closes = [(ts,p) for ts,p in loaded if ts <= expected]
            reason = ('No stored candles' if not closes else
                      'Latest completed bar missing' if closes[-1][0] != expected else
                      'Need at least 21 completed bars' if len(closes) < SLOW+1 else None)
            if reason:
                unavailable.append({**instrument, 'reason': reason,
                                    'last_bar': closes[-1][0] if closes else None})
                continue
            path = points(closes, kind, tail)
            if len(path) < 2:
                unavailable.append({**instrument, 'reason': 'Not enough tail history'})
                continue
            head = path[-1]
            held = 0
            for point in reversed(points(closes, kind, len(closes))):
                if point['state'] != head['state']:
                    break
                held += 1
            rows.append({**instrument, 'label': instrument.get('underlying') or label(symbol),
                         'group': sector_of(instrument.get('spot_symbol') or symbol),
                         'price': closes[-1][1], 'as_of': end, 'price_at': price_at, 'tail': path,
                         'x': head['x'], 'y': head['y'], 'state': head['state'], 'held': held})
    return {'kind': kind, 'timeframe_seconds': timeframe, 'generated_at': now.isoformat(),
            'rows': rows, 'unavailable': unavailable, 'evaluated': len(instruments),
            'rules': {'fast': FAST, 'slow': SLOW, 'scale': SCALE,
                      'basis': 'Own price; completed bars; no benchmark',
                      'up': 'EMA5 above EMA20 and EMA5 rising over 3 bars',
                      'down': 'Spot only: EMA5 below EMA20 and EMA5 falling over 3 bars',
                      'fading': 'Every other valid price pattern; includes flat or falling option premiums'}}
