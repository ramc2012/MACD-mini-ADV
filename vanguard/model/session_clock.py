"""NSE option candles are OPEN-stamped; their close is known later.

The final derivative candle became complete at 15:40 on 2026-08-03.
Earlier sessions retain their original 15:30 close. Cash CAS is a separate
15:35 clock; callers here consume exact option bars/composite decisions.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DERIVATIVES_EXTENDED_FROM = date(2026, 8, 3)


def session_close(day: date) -> time:
    return time(15, 40) if day >= DERIVATIVES_EXTENDED_FROM else time(15, 30)


def available_at_sql(stamp: str) -> str:
    """SQL equivalent of available_at for a trusted timestamp expression."""
    return f"""LEAST({stamp} + interval '30 minutes',
        ((({stamp} AT TIME ZONE 'Asia/Kolkata')::date +
          CASE WHEN ({stamp} AT TIME ZONE 'Asia/Kolkata')::date >= DATE '2026-08-03'
               THEN TIME '15:40' ELSE TIME '15:30' END)
         AT TIME ZONE 'Asia/Kolkata'))"""


def available_at(stamp: datetime) -> datetime:
    if stamp.tzinfo is None:
        raise ValueError("candle timestamp must be timezone-aware")
    local = stamp.astimezone(IST)
    if (local.weekday() >= 5 or local.minute not in (15, 45)
            or local.second or local.microsecond
            or not time(9, 15) <= local.time() <= time(15, 15)):
        raise ValueError("not an NSE 30-minute candle start")
    close = session_close(local.date())
    return min(local + timedelta(minutes=30), local.replace(hour=close.hour, minute=close.minute))


def completed(stamp: datetime, as_of: datetime) -> bool:
    return available_at(stamp) <= as_of


def timely_decision(stamp: datetime, decision_at: datetime) -> bool:
    """A historical replay made after the next close is not prospective."""
    close = available_at(stamp)
    return close <= decision_at < close + timedelta(minutes=30)
