"""Regime breaks in the NSE index-derivatives rule set.

Between November 2024 and August 2026 the exchange changed lot sizes, freeze
quantities, futures tick size, weekly expiry weekday, session end and position
limits. Any statistic computed across one of those dates mixes two different
markets: a "stacked imbalance" count from before 15 April 2025 was measured on
0.05 ticks and is not comparable with one measured on 0.10, and an expiry-day
pinning rate from before 1 September 2025 was measured on Thursdays.

So every session profile carries the regime it was recorded under, and every
base-rate table is split by it. The alternative — a single blended number — is
worse than having no number, because it looks authoritative.

Facts and effective dates are from the blueprint's Part 2, sourced to NSE and
SEBI circulars. ``UNVERIFIED`` marks the one item the blueprint itself flags as
unconfirmed, so nothing downstream can quietly treat it as settled.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Regime:
    regime_id: str
    start: str
    summary: str
    nifty_lot: int
    banknifty_lot: int
    nifty_freeze_units: int
    banknifty_freeze_units: int
    nifty_futures_tick: float
    banknifty_futures_tick: float
    nifty_weekly_expiry: str | None
    session_end: str
    notes: str = ""

    @property
    def start_date(self) -> date:
        return date.fromisoformat(self.start)

    def lot_size(self, underlying: str) -> int:
        return self.banknifty_lot if underlying.upper().startswith("BANKNIFTY") else self.nifty_lot

    def freeze_units(self, underlying: str) -> int:
        return (self.banknifty_freeze_units if underlying.upper().startswith("BANKNIFTY")
                else self.nifty_freeze_units)

    def freeze_lots(self, underlying: str) -> int:
        """Largest whole-lot order the exchange accepts.

        Not a rounding detail: a repeated print of exactly this size is the
        tick-level signature of a participant who wants more than one order can
        carry, which is the whale detector's cleanest input.
        """
        lot = self.lot_size(underlying)
        return self.freeze_units(underlying) // lot if lot else 0

    def futures_tick(self, underlying: str) -> float:
        return (self.banknifty_futures_tick if underlying.upper().startswith("BANKNIFTY")
                else self.nifty_futures_tick)


# Ordered oldest first. Each entry begins on the date its rule change took
# effect and runs until the next one starts.
REGIMES: tuple[Regime, ...] = (
    Regime(
        regime_id="pre-2024-11", start="2000-01-01",
        summary="Before the November 2024 contract-value and weekly-expiry overhaul",
        nifty_lot=25, banknifty_lot=15,
        nifty_freeze_units=1800, banknifty_freeze_units=900,
        nifty_futures_tick=0.05, banknifty_futures_tick=0.05,
        nifty_weekly_expiry="thursday", session_end="15:30",
        notes="Lot and freeze values here are approximate; this era predates the archive.",
    ),
    Regime(
        regime_id="2024-11-contract-value", start="2024-11-20",
        summary="Minimum contract value 15 lakh; one weekly expiry per exchange",
        nifty_lot=75, banknifty_lot=35,
        nifty_freeze_units=1800, banknifty_freeze_units=900,
        nifty_futures_tick=0.05, banknifty_futures_tick=0.05,
        nifty_weekly_expiry="thursday", session_end="15:30",
        notes="BANKNIFTY/FINNIFTY/MIDCPNIFTY weeklies end; +2% ELM on expiry-day short options.",
    ),
    Regime(
        regime_id="2025-04-tick", start="2025-04-15",
        summary="Index-futures tick size raised to 0.10 (NIFTY) / 0.20 (BANKNIFTY)",
        nifty_lot=75, banknifty_lot=35,
        nifty_freeze_units=1800, banknifty_freeze_units=900,
        nifty_futures_tick=0.10, banknifty_futures_tick=0.20,
        nifty_weekly_expiry="thursday", session_end="15:30",
        notes="NSE/FAOP/67135. Footprint row width and imbalance ratios change here.",
    ),
    Regime(
        regime_id="2025-09-tuesday", start="2025-09-01",
        summary="NIFTY weekly expiry moves to Tuesday; all NSE monthlies last Tuesday",
        nifty_lot=75, banknifty_lot=35,
        nifty_freeze_units=1800, banknifty_freeze_units=900,
        nifty_futures_tick=0.10, banknifty_futures_tick=0.20,
        nifty_weekly_expiry="tuesday", session_end="15:30",
        notes="Weekday seasonality and expiry-day pinning statistics break here.",
    ),
    Regime(
        regime_id="2025-12-position-limits", start="2025-12-08",
        summary="FutEq position limits, intraday snapshots, F&O pre-open for futures",
        nifty_lot=75, banknifty_lot=35,
        nifty_freeze_units=1800, banknifty_freeze_units=900,
        nifty_futures_tick=0.10, banknifty_futures_tick=0.20,
        nifty_weekly_expiry="tuesday", session_end="15:30",
        notes="SEBI CIR/2025/79 and /122. The 09:15 'open' changes meaning once a "
              "futures call auction precedes it, which affects open-type classification.",
    ),
    Regime(
        regime_id="2026-01-lots", start="2026-01-01",
        summary="Lot sizes cut to 65 / 30",
        nifty_lot=65, banknifty_lot=30,
        nifty_freeze_units=1800, banknifty_freeze_units=600,
        nifty_futures_tick=0.10, banknifty_futures_tick=0.20,
        nifty_weekly_expiry="tuesday", session_end="15:30",
        notes="NSE/FAOP/70616. Lot-multiple and freeze-size print thresholds move.",
    ),
    Regime(
        regime_id="2026-07-freeze", start="2026-07-01",
        summary="Freeze quantities re-set to 1,800 / 600 units",
        nifty_lot=65, banknifty_lot=30,
        nifty_freeze_units=1800, banknifty_freeze_units=600,
        nifty_futures_tick=0.10, banknifty_futures_tick=0.20,
        nifty_weekly_expiry="tuesday", session_end="15:30",
        notes="NSE/FAOP/74942.",
    ),
    Regime(
        regime_id="2026-08-session", start="2026-08-03",
        summary="F&O session extended to 15:40 alongside the cash closing auction",
        nifty_lot=65, banknifty_lot=30,
        nifty_freeze_units=1800, banknifty_freeze_units=600,
        nifty_futures_tick=0.10, banknifty_futures_tick=0.20,
        nifty_weekly_expiry="tuesday", session_end="15:40",
        notes="Closing Auction Session 15:15-15:35. The last bracket changes length, "
              "so closing-spike and bracket-M statistics do not cross this date.",
    ),
)

_STARTS = [regime.start for regime in REGIMES]


def regime_for(day: str | date) -> Regime:
    """The rule set in force on an IST calendar date."""
    key = day.isoformat() if isinstance(day, date) else str(day)
    index = bisect_right(_STARTS, key) - 1
    return REGIMES[max(0, index)]


def regime_id_for(day: str | date) -> str:
    return regime_for(day).regime_id


def spans_a_break(start: str, end: str) -> list[str]:
    """Regime boundaries strictly inside a date range.

    Used to refuse, or at least to label, any statistic asked for over a window
    that is not one market.
    """
    return [regime.start for regime in REGIMES if start < regime.start <= end]


def group_by_regime(days: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for day in sorted(days):
        grouped.setdefault(regime_id_for(day), []).append(day)
    return grouped


def is_expiry_day(day: str | date, underlying: str = "NIFTY") -> bool:
    """Weekly expiry for NIFTY, monthly last-weekday expiry for both.

    Only as good as the weekday rule — it does not know about exchange
    holidays that shift an expiry, so a session tagged here as an expiry may in
    truth be the day before one. Callers that need certainty should use the
    contract master; this is for splitting base rates, where a handful of
    misclassified sessions is visible in the counts.
    """
    moment = day if isinstance(day, date) else date.fromisoformat(str(day))
    regime = regime_for(moment)
    weekday = {"tuesday": 1, "thursday": 3}.get(regime.nifty_weekly_expiry or "", 1)
    if moment.weekday() != weekday:
        return False
    if underlying.upper().startswith("BANKNIFTY") and regime.start >= "2024-11-20":
        # Weeklies were discontinued: only the last such weekday of the month.
        return _is_last_weekday_of_month(moment, weekday)
    return True


def _is_last_weekday_of_month(moment: date, weekday: int) -> bool:
    from datetime import timedelta
    following = moment + timedelta(days=7)
    return following.month != moment.month
