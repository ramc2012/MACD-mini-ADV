"""Tick to candle aggregation, aligned to the exchange session.

The live aggregator and the stored-history aggregator MUST agree on bucket
boundaries or the strategy trades bars the chart never draws. Bucketing on
``epoch % timeframe`` aligns to UTC midnight, which for a 30-minute frame puts
boundaries at 09:00 / 09:30 / 10:00 IST, while
``chart_history.aggregate_session_candles`` anchors to the 09:15 open and
produces 09:15 / 09:45 / 10:15. Both are internally consistent and mutually
useless. Everything here anchors to the session open.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import Candle, Tick

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


def session_bucket(epoch: int, timeframe_seconds: int) -> int | None:
    """Session-anchored bucket start, or None for a tick outside the session.

    Returning None (rather than a bucket) is what keeps pre-open re-broadcasts
    of yesterday's close out of today's first bar.
    """
    moment = datetime.fromtimestamp(epoch, UTC).astimezone(IST)
    if not (SESSION_OPEN <= moment.timetz().replace(tzinfo=None) < SESSION_CLOSE):
        return None
    open_at = datetime.combine(moment.date(), SESSION_OPEN, IST)
    elapsed = int((moment - open_at).total_seconds())
    return int((open_at + timedelta(seconds=(elapsed // timeframe_seconds) * timeframe_seconds)).timestamp())


class CandleAggregator:
    def __init__(self, timeframe_seconds: int):
        self.timeframe_seconds = timeframe_seconds
        self.current: dict[str, Candle] = {}
        self.rejected_out_of_order = 0
        self.rejected_off_session = 0

    def on_tick(self, tick: Tick) -> tuple[Candle | None, Candle | None]:
        """(forming candle, just-closed candle). Either may be None."""
        epoch = int(tick.timestamp.timestamp())
        bucket = session_bucket(epoch, self.timeframe_seconds)
        if bucket is None:
            self.rejected_off_session += 1
            return None, None
        candle = self.current.get(tick.symbol)
        if candle is not None and bucket < candle.timestamp:
            # A late or replayed tick from an earlier bucket. Folding it in
            # would rewrite a bar the strategy has already acted on.
            self.rejected_out_of_order += 1
            return candle, None
        closed = None
        if candle is None or candle.timestamp != bucket:
            if candle is not None:
                candle.closed = True
                closed = candle
            candle = Candle(
                symbol=tick.symbol,
                timestamp=bucket,
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp,
                volume=max(tick.volume, 0),
            )
            self.current[tick.symbol] = candle
        else:
            candle.high = max(candle.high, tick.ltp)
            candle.low = min(candle.low, tick.ltp)
            candle.close = tick.ltp
            candle.volume = max(candle.volume, tick.volume)
        return candle, closed

    def flush(self) -> list[Candle]:
        """Close every open candle — used at the session close.

        A bar is normally committed only when a tick from the NEXT bucket
        arrives. After 15:30 no such tick exists, so without this the final
        bar of the day (15:15-15:30 on a 30-minute frame) is never evaluated,
        and any setup that completed on it is silently lost.
        """
        closed: list[Candle] = []
        for candle in self.current.values():
            if not candle.closed:
                candle.closed = True
                closed.append(candle)
        self.current.clear()
        return closed
