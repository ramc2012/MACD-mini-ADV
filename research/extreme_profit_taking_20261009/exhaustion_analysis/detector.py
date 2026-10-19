"""Frozen research-only option-premium exhaustion detector; no app imports or I/O.

Every feature is available by its bar end. A confirmed pivot is published only
on its second right-hand bar. Full dataset prefixes produce identical outputs.
See PLAN.md for rules, limitations, execution assumptions and parameter register.
"""
import datetime as dt
import math
import statistics
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
HOLIDAYS = {dt.date(2026, 10, 2)}


def timestamp(value):
    return dt.datetime.fromisoformat(value).timestamp() if isinstance(value, str) else float(value)


def next_minute(value):
    """Next eligible minute opening, inclusive of a completed bar's end."""
    t = math.ceil(value / 60) * 60
    while True:
        local = dt.datetime.fromtimestamp(t, IST)
        if local.weekday() < 5 and local.date() not in HOLIDAYS and (9, 15) <= (local.hour, local.minute) < (15, 40):
            return t
        t += 60


def build_bars(rows, start=None, end=None):
    """Build complete session-aligned 3m bars, explicitly representing missing bars.

    Input rows begin [epoch,open,high,low,close,volume]; additional columns ignored.
    A partial 15:39 bar never becomes a three-minute observation.
    """
    if not rows:
        return []
    start = min(int(r[0]) for r in rows) if start is None else int(start)
    end = max(int(r[0]) for r in rows) + 60 if end is None else int(end)
    minutes = {}
    for row in rows:
        t = int(row[0])
        if t in minutes:
            raise ValueError(f'Duplicate minute {t}')
        minutes[t] = row
    first = dt.datetime.fromtimestamp(start, IST).date()
    last = dt.datetime.fromtimestamp(end, IST).date()
    bars = []
    day = first
    while day <= last:
        if day.weekday() < 5 and day not in HOLIDAYS:
            opening = int(dt.datetime.combine(day, dt.time(9, 15), IST).timestamp())
            for offset in range(0, 384, 3):
                t = opening + offset * 60
                if t < start or t + 180 > end:
                    continue
                selected = [minutes.get(t + i * 60) for i in range(3)]
                valid = True
                for r in selected:
                    if r is None:
                        valid = False
                        break
                    prices = [float(x) for x in r[1:5]]
                    volume = r[5]
                    if (not all(math.isfinite(x) and x > 0 for x in prices)
                            or volume is None or not math.isfinite(float(volume)) or float(volume) < 0
                            or prices[1] < max(prices[0], prices[3])
                            or prices[2] > min(prices[0], prices[3])
                            or prices[1] < prices[2]):
                        valid = False
                        break
                bar = dict(start=t, end=t + 180, session=day.isoformat(), valid=valid)
                if valid:
                    bar.update(open=float(selected[0][1]), high=max(float(r[2]) for r in selected),
                               low=min(float(r[3]) for r in selected), close=float(selected[-1][4]),
                               volume=sum(float(r[5]) for r in selected))
                bars.append(bar)
        day += dt.timedelta(days=1)
    return bars


class EMA:
    def __init__(self, period):
        self.period = period
        self.seed = []
        self.value = None

    def update(self, value):
        if self.value is None:
            self.seed.append(value)
            if len(self.seed) == self.period:
                self.value = statistics.fmean(self.seed)
        else:
            self.value += 2 / (self.period + 1) * (value - self.value)
        return self.value


class Wilder:
    def __init__(self, period):
        self.period = period
        self.seed = []
        self.value = None

    def update(self, value):
        if self.value is None:
            self.seed.append(value)
            if len(self.seed) == self.period:
                self.value = statistics.fmean(self.seed)
        else:
            self.value = (self.value * (self.period - 1) + value) / self.period
        return self.value


def enrich(bars):
    output = []
    segment = -1
    history = []
    initialized = False
    for original in bars:
        b = dict(original)
        if not b['valid']:
            b.update(count=0, segment=None, confirmed_pivot=None)
            output.append(b)
            initialized = False
            history = []
            continue
        if not initialized:
            initialized = True
            segment += 1
            ema20, ema12, ema26, signal = EMA(20), EMA(12), EMA(26), EMA(9)
            gain, loss, atr = Wilder(14), Wilder(14), Wilder(14)
        prior = history[-1] if history else None
        closes = [x['close'] for x in history[-20:]]
        mean = statistics.fmean(closes) if len(closes) == 20 else None
        sd = statistics.pstdev(closes) if mean is not None else None
        z = (b['close'] - mean) / sd if sd is not None and sd > 0 else None
        b['atr14_previous'] = atr.value
        tr = max(b['high'] - b['low'], abs(b['high'] - prior['close']), abs(b['low'] - prior['close'])) if prior else b['high'] - b['low']
        b['atr14'] = atr.update(tr)
        if prior:
            change = b['close'] - prior['close']
            avg_gain, avg_loss = gain.update(max(0, change)), loss.update(max(0, -change))
        else:
            avg_gain = avg_loss = None
        rsi = None
        if avg_gain is not None and avg_loss is not None:
            rsi = 50.0 if avg_gain == avg_loss == 0 else (100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
        e20, e12, e26 = ema20.update(b['close']), ema12.update(b['close']), ema26.update(b['close'])
        macd = e12 - e26 if e12 is not None and e26 is not None else None
        sig = signal.update(macd) if macd is not None else None
        hist = macd - sig if sig is not None else None
        volume_median = statistics.median([x['volume'] for x in history[-20:]]) if len(history) >= 20 else None
        width = b['high'] - b['low']
        b.update(count=len(history) + 1, segment=segment, ema20=e20, rsi14=rsi,
                 macd=macd, hist=hist, z=z, upper_band=mean + 2 * sd if sd is not None else None,
                 volume_ratio=b['volume'] / volume_median if volume_median is not None and volume_median > 0 else None,
                 upper_wick=(b['high'] - max(b['open'], b['close'])) / width if width > 0 else 0,
                 atr_extension=(b['close'] - e20) / b['atr14_previous'] if e20 is not None and b['atr14_previous'] is not None and b['atr14_previous'] > 0 else None,
                 confirmed_pivot=None)
        history.append(b)
        if len(history) >= 5:
            window = history[-5:]
            p = window[2]
            if all(p['high'] > x['high'] for x in window if x is not p):
                b['confirmed_pivot'] = dict(start=p['start'], end=p['end'], session=p['session'],
                    high=p['high'], low=p['low'], rsi14=p['rsi14'], hist=p['hist'],
                    index=p['count'] - 1, available_at=b['end'], segment=segment)
        output.append(b)
    return output


def residual_profit(bar, entry_price, initial_qty, sells):
    remaining = initial_qty - sum(int(s['quantity']) for s in sells if timestamp(s['timestamp']) <= bar['end'])
    net = (bar['close'] * .9995 - entry_price) * remaining - 20 - 20 * remaining / initial_qty
    return remaining > 0 and net > 0


def first_exit(bars, entry_time_epoch, final_exit_epoch, entry_price, initial_qty, sells, policy):
    """Return the first causal signal and next-minute opening time, or None.

    Caller enforces common pre-entry/holding-window quality and computes fills,
    retained partial proceeds, whole-lot quantities and standardized costs.
    """
    if policy not in ('band_failure', 'divergence_failure'):
        raise ValueError(policy)
    armed = None
    previous = None
    pivots = []
    history = []
    segment = None
    for b in bars:
        if b['end'] > final_exit_epoch:
            break
        if not b['valid']:
            armed, previous, pivots, history, segment = None, None, [], [], None
            continue
        if b['segment'] != segment:
            armed, previous, pivots, history = None, None, [], []
            segment = b['segment']
        history.append(b)
        if armed and (b['session'] != armed['session'] or b['count'] - armed['count'] > 10):
            armed = None
        pivot = b['confirmed_pivot']
        if pivot:
            pivots.append(pivot)
            if policy == 'divergence_failure' and len(pivots) >= 2:
                a, p = pivots[-2:]
                gap = p['index'] - a['index']
                rsi_div = a['rsi14'] is not None and p['rsi14'] is not None and p['rsi14'] <= a['rsi14'] - 5
                macd_div = a['hist'] is not None and p['hist'] is not None and a['hist'] > 0 and p['hist'] <= .8 * a['hist']
                armed = None  # a new consecutive pair supersedes the earlier setup
                if (p['start'] >= entry_time_epoch and 3 <= gap <= 20
                        and p['high'] >= a['high'] * 1.002 and p['rsi14'] is not None and p['rsi14'] >= 60
                        and (rsi_div or macd_div)):
                    trough = min(x['low'] for x in history if a['start'] < x['start'] < p['start'])
                    armed = dict(session=b['session'], count=b['count'], neckline=trough,
                        pivot_earlier=a, pivot_later=p, rsi_divergence=rsi_div, macd_divergence=macd_div)
        eligible = b['start'] >= entry_time_epoch and b['count'] >= 100 and b['volume'] > 0 and residual_profit(b, entry_price, initial_qty, sells)
        trigger = False
        reasons = []
        if eligible and policy == 'band_failure':
            # Evaluate an already-armed failure before a new extreme can refresh it.
            trigger = bool(armed and b['count'] > armed['count'] and b['z'] is not None
                           and b['z'] < 2 and previous and b['close'] < previous['low'])
            reasons = ['prior premium z>=2 and RSI>=70', 're-entry below prior-20 band', 'close below preceding bar low']
            if b['z'] is not None and b['z'] >= 2 and b['rsi14'] is not None and b['rsi14'] >= 70:
                armed = dict(session=b['session'], count=b['count'], armed_at=b['end'])
        elif eligible and policy == 'divergence_failure':
            trigger = bool(armed and b['close'] < armed['neckline'])
            reasons = ['confirmed higher premium high with weaker RSI or MACD histogram', 'close below intervening trough']
        if trigger:
            fill = next_minute(b['end'])
            if fill <= final_exit_epoch:
                return dict(policy=policy, decision_ts=b['end'], fill_ts=fill, reasons=reasons,
                    features={k:b.get(k) for k in ('close','z','rsi14','hist','volume_ratio','upper_wick','atr_extension')}, setup=armed)
        previous = b
    return None
