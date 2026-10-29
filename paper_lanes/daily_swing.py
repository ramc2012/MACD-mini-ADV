"""CBE daily trend policy. Pure completed-bar calculations; no broker or order access."""
import math

VERSION = 'cbe-daily-trend-v1-20261029'
MIN_BARS = 250


def ema(values, period):
    value = sum(values[:period]) / period
    for close in values[period:]:
        value += 2 / (period + 1) * (close - value)
    return value


def signal(bars, benchmark):
    """Bars must be complete, chronological and session-aligned with benchmark."""
    if len(bars) < MIN_BARS or len(bars) != len(benchmark):
        return {'eligible': False, 'reason': 'Need 250 aligned completed daily sessions'}
    if any(not all(isinstance(b.get(k), (float, int)) and math.isfinite(b[k]) and b[k] > 0
                   for k in ('open', 'high', 'low', 'close')) or b['high'] < max(b['open'], b['close'])
                   or b['low'] > min(b['open'], b['close']) for b in bars):
        return {'eligible': False, 'reason': 'Invalid daily OHLC'}
    prices = [b['close'] for b in bars]
    if any(not isinstance(v,(float,int)) or not math.isfinite(v) or v <= 0 for v in benchmark):
        return {'eligible': False, 'reason': 'Invalid benchmark daily closes'}
    if any(abs(b / a - 1) > .30 for a,b in zip(prices,prices[1:])):
        return {'eligible': False, 'reason': 'Large daily discontinuity; corporate-action/price-basis review required'}
    e20, e50, e200 = [ema(prices, p) for p in (20,50,200)]
    prior50 = ema(prices[:-10],50)
    momentum = prices[-1] / prices[-64] - 1
    relative = momentum - (benchmark[-1] / benchmark[-64] - 1)
    breakout = max(b['high'] for b in bars[-56:-1])
    tr = [max(b['high']-b['low'], abs(b['high']-a['close']), abs(b['low']-a['close']))
          for a,b in zip(bars,bars[1:])]
    atr = sum(tr[-20:]) / 20
    trend = prices[-1] > e20 > e50 > e200 and e50 > prior50 and momentum > 0 and relative > 0
    eligible = trend and prices[-1] > breakout and atr > 0
    return {'strategy_version': VERSION, 'holding_mode':'daily_trend', 'horizon_sessions': None,
            'eligible':eligible, 'direction':'LONG' if trend else 'NEUTRAL',
            'score':max(0,relative)/(atr/prices[-1]) if atr>0 else 0,
            'reason':'Daily trend and 55-session closing breakout' if eligible else 'Waiting for daily trend, relative strength and 55-session breakout',
            'ema20':e20,'ema50':e50,'ema200':e200,'ema50_10_sessions_ago':prior50,
            'momentum_63':momentum,'relative_63':relative,'breakout_55':breakout,
            'atr20':atr,'latest_close':prices[-1], 'previous_close':prices[-2],
            'previous_ema50':ema(prices[:-1],50), 'highest_close_20':max(prices[-20:])}


def exit_rule(row, previous_stop):
    """Return a nondecreasing EOD trail and an optional next-session exit signal."""
    fields = ('latest_close','previous_close','ema50','previous_ema50','ema200','atr20')
    if any(not isinstance(row.get(k),(float,int)) or not math.isfinite(row[k]) or row[k]<=0 for k in fields):
        return previous_stop, None
    reason = ('DAILY_TREND_BREAK' if row['ema50'] <= row['ema200'] else
              'DAILY_EMA50_EXIT' if row['latest_close'] < row['ema50'] and row['previous_close'] < row['previous_ema50'] else
              'DAILY_ATR_EXIT' if row['latest_close'] <= previous_stop else None)
    # Apply a new close-based trail prospectively. Never use today's updated stop to exit on today's low.
    return max(previous_stop, row['latest_close']-3*row['atr20']), reason
