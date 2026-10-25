"""Transparent completed-session stock direction; no option-payoff inputs.

Fixed research rules, not calibrated probabilities or a profitability claim.
Callers supply aligned, completed and freshness-checked stock/benchmark closes.
"""
from __future__ import annotations
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

VERSION = 'daily-stock-direction-v1-20261017'
IST = ZoneInfo('Asia/Kolkata')

# One fixed daily observation at 15:15: the twelve completed 30m bars through
# 14:45. Do not turn a partial last bar or a stale session into today's signal.
DAILY_SQL = """
WITH chosen AS MATERIALIZED (
 SELECT DISTINCT ON (underlying,time) underlying,time,close,source,synced_at
 FROM underlying_spot_candles
 WHERE interval='30minute' AND time>=:start AND time<:as_of
   AND time + interval '30 minutes'<=:as_of
   AND synced_at<=:as_of
   AND source IN ('upstox_spot','upstox_sweep','upstox','fyers','fyers_spot')
   AND (underlying='NIFTY' OR underlying IN
     (SELECT symbol FROM fo_underlying_catalog WHERE kind='STOCK' AND fno_active IS TRUE))
   AND (time AT TIME ZONE 'Asia/Kolkata')::time BETWEEN time '09:15' AND time '14:45'
   AND extract(minute from time AT TIME ZONE 'Asia/Kolkata') IN (15,45)
   AND close>0 AND open>0 AND low>0 AND high>=greatest(open,close) AND low<=least(open,close)
 ORDER BY underlying,time,CASE source WHEN 'upstox_spot' THEN 0 WHEN 'upstox_sweep' THEN 1
    WHEN 'upstox' THEN 2 WHEN 'fyers_spot' THEN 3 ELSE 4 END,synced_at DESC
), daily AS (
 SELECT underlying,(time AT TIME ZONE 'Asia/Kolkata')::date AS session,
   (array_agg(close ORDER BY time DESC))[1]::float AS close,
   count(*) AS bars,max(synced_at) AS available_at
 FROM chosen GROUP BY underlying,(time AT TIME ZONE 'Asia/Kolkata')::date
 HAVING count(*)=12
)
SELECT * FROM daily ORDER BY underlying,session
"""

def rank_daily(rows, source_session, horizons=(1,)):
    """Use a common benchmark calendar; missing dates never compress windows."""
    series = defaultdict(dict)
    for row in rows:
        day = row['session']
        if day <= source_session:
            series[row['underlying']][day] = row
    benchmark = series.get('NIFTY', {})
    dates = sorted(benchmark)[-150:]
    if not dates or dates[-1] != source_session:
        return dict(status='blocked', blockers=['missing_current_completed_benchmark'],
                    source_session=str(source_session), version=VERSION, results=[], watchlist=[])
    universe_size=len(series)-1
    current_count=sum(source_session in history for symbol,history in series.items() if symbol!='NIFTY')
    if universe_size and current_count/universe_size < .8:
        return dict(status='blocked',blockers=['current_stock_breadth_below_80pct'],
                    source_session=str(source_session),version=VERSION,results=[],watchlist=[],
                    universe_size=universe_size,current_complete=current_count)
    output = []
    for symbol, history in sorted(series.items()):
        if symbol == 'NIFTY':
            continue
        # Keep the latest common fixed window. A recent IPO can still qualify
        # after sixty aligned sessions; holes inside that window fail closed.
        window = dates[-min(100,len(dates)):]
        if any(day not in history for day in window):
            for horizon in horizons:
                output.append(dict(instrument=symbol, eligible=False, direction='NEUTRAL',
                                   score=0, reason='missing aligned completed sessions',horizon_sessions=horizon))
            continue
        stock = [history[d]['close'] for d in window]
        market = [benchmark[d]['close'] for d in window]
        for horizon in horizons:
            result = signal(stock, market, horizon)
            output.append(dict(instrument=symbol, latest_close=stock[-1], **result,
                               source_session=str(source_session),
                               input_available_at=max(history[d]['available_at'] for d in window).isoformat()))
    output.sort(key=lambda r:(-r['score'],r['instrument'],r.get('horizon_sessions',0)))
    return dict(status='ok', blockers=[], source_session=str(source_session), version=VERSION,
                results=output, watchlist=[r for r in output if r['eligible']],
                universe_size=universe_size,current_complete=current_count)


def ema(values, period):
    result = float(values[0])
    for value in values[1:]:
        result += 2 / (period + 1) * (float(value) - result)
    return result

def signal(closes, benchmark, horizon=1):
    """Rank aligned trends using absolute AND relative momentum per horizon.

    Daily uses 5/20 sessions, weekly/monthly use 20/60. Moving-average
    agreement and multi-session direction avoid selecting a less-bad loser
    solely because its index fell faster. Neutral is a valid result.
    """
    fast, slow = (5, 20) if horizon == 1 else (20, 60)
    required = max(60, slow + 1)
    neutral = dict(version=VERSION, direction='NEUTRAL', score=0.0,
                   eligible=False, reason='insufficient aligned completed history')
    if len(closes) != len(benchmark) or len(closes) < required:
        return neutral
    try:
        prices = [float(x) for x in closes]
        index = [float(x) for x in benchmark]
    except (ValueError, TypeError):
        return {**neutral, 'reason':'invalid price history'}
    if not all(math.isfinite(x) and x > 0 for x in prices + index):
        return {**neutral, 'reason':'invalid price history'}
    ret_fast = prices[-1] / prices[-fast-1] - 1
    ret_slow = prices[-1] / prices[-slow-1] - 1
    relative = ret_slow - (index[-1] / index[-slow-1] - 1)
    ma20, ma50 = ema(prices[-100:], 20), ema(prices[-150:], 50)
    returns = [prices[i]/prices[i-1]-1 for i in range(len(prices)-20, len(prices))]
    volatility = statistics.pstdev(returns)
    long = prices[-1] > ma20 > ma50 and ret_fast > 0 and ret_slow > 0 and relative > 0
    short = prices[-1] < ma20 < ma50 and ret_fast < 0 and ret_slow < 0 and relative < 0
    side = 1 if long else -1 if short else 0
    # A bounded ordinal strength score, never an expected return/probability.
    strength = (side * (ret_fast + ret_slow + relative) /
                max(.005, volatility * math.sqrt(slow))) if side else 0
    return dict(version=VERSION, direction='LONG' if long else 'SHORT' if short else 'NEUTRAL',
                eligible=bool(side), score=round(min(100, max(0, 50+10*strength)) if side else 0, 4),
                reason='absolute/relative momentum and EMA20/50 agree' if side else 'mixed absolute/relative trend',
                momentum_fast=ret_fast, momentum_slow=ret_slow, relative_momentum=relative,
                ema20=ma20, ema50=ma50, daily_volatility=volatility,
                horizon_sessions=horizon, calibrated_expected_return=None)
