"""Market Profile (TPO) and volume profile built from the live tick stream.

Follows Steidlmayer's original construction as taught by Dalton (*Mind Over
Markets*): the session is divided into 30-minute brackets, each lettered, and
every price touched during a bracket receives that bracket's TPO. From the
resulting distribution come the statistics that actually drive decisions:

* **POC** — the price with the most TPOs (fairest price of the session)
* **Value Area** — the ~70% of TPOs around the POC (VAH / VAL)
* **Initial Balance** — the first hour's range; a reference for range extension
* **Open type** — drive / test-drive / rejection-reverse / auction
* **Day type** — normal, normal variation, trend, neutral

Prices are bucketed to the exchange tick so an option's profile has the
resolution its book actually quotes at.
"""
from __future__ import annotations

import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPEN_MINUTE = 9 * 60 + 15
BRACKET_MINUTES = 30
INITIAL_BALANCE_BRACKETS = 2          # first hour
VALUE_AREA_FRACTION = 0.70
LETTERS = string.ascii_uppercase + string.ascii_lowercase


def bracket_of(minute: int) -> int:
    """0-based 30-minute bracket index since the 09:15 open."""
    return max(0, (minute - SESSION_OPEN_MINUTE) // BRACKET_MINUTES)


def letter_for(index: int) -> str:
    return LETTERS[index] if index < len(LETTERS) else "*"


def _expand_value_area(counts: dict[float, float], poc: float,
                       levels: list[float] | None = None) -> tuple[float, float]:
    """(VAH, VAL) by the standard alternating expansion from the POC.

    One walk for the session value area and for the value area as of any
    bracket, so "developing" and "completed" are the same rule applied to
    fewer rows -- never two implementations that can disagree at the close.

    ``levels`` is ``sorted(counts)`` when the caller already holds it. The
    bracket walk computes thirteen value areas off one ladder, and re-sorting
    three thousand levels per bracket was the whole of a 16 ms snapshot.
    """
    total = sum(counts.values())
    target = total * VALUE_AREA_FRACTION
    if levels is None:
        levels = sorted(counts)
    index = levels.index(poc)
    low_i = high_i = index
    included = counts[poc]
    while included < target and (low_i > 0 or high_i < len(levels) - 1):
        below = counts[levels[low_i - 1]] if low_i > 0 else -1
        above = counts[levels[high_i + 1]] if high_i < len(levels) - 1 else -1
        if above >= below:
            high_i += 1
            included += counts[levels[high_i]]
        else:
            low_i -= 1
            included += counts[levels[low_i]]
    return levels[high_i], levels[low_i]


def _poc_of(counts: dict[float, float]) -> float | None:
    """Highest count, tie to the centre of the profile, then to price."""
    if not counts:
        return None
    centre = (min(counts) + max(counts)) / 2
    return max(counts, key=lambda level: (counts[level], -abs(level - centre), level))


@dataclass
class Profile:
    """Developing profile for one symbol for one session."""
    symbol: str
    day: str
    tick_size: float = 0.05
    tpo: dict[float, set[int]] = field(default_factory=dict)
    volume: dict[float, float] = field(default_factory=dict)
    open_price: float | None = None
    last_price: float | None = None
    high: float | None = None
    low: float | None = None
    brackets_seen: set[int] = field(default_factory=set)
    ib_high: float | None = None
    ib_low: float | None = None
    last_minute: int | None = None
    _open_window: list[float] = field(default_factory=list)
    first_bracket: int | None = None
    # bracket index -> the day type as that bracket CLOSED. The live reading
    # is recomputed on every tick, so without a latch the estimate "as of C"
    # is gone the moment D opens; a per-bracket history is what lets a reader
    # see the classification settle -- or flip -- through the session.
    day_types: dict[int, str] = field(default_factory=dict)
    _structure_version: int = 0
    _cache_version: int = -1
    _cached_poc: float | None = None
    _cached_value_area: tuple[float | None, float | None] = (None, None)
    # The bracket walk is cached against the same structure version the POC and
    # value area are. Uncached it was measured at 16.6 ms on a full-session
    # profile (3148 levels, 13 brackets) and snapshot() is called up to three
    # times per 3 s poll from `async def` routes -- i.e. tens of milliseconds of
    # event-loop blocking per cycle on the loop that also ingests live ticks.
    _va_rows_version: int = -1
    _cached_va_rows: list = field(default_factory=list)

    # -- construction --------------------------------------------------------

    def bucket(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, 2)

    def add(self, price: float, size: float, minute: int) -> None:
        level = self.bucket(price)
        index = bracket_of(minute)
        if self.first_bracket is None:
            self.first_bracket = index
        if index not in self.brackets_seen and self.brackets_seen:
            # The previous bracket has closed: latch what the day looked like
            # at that moment, before this print widens the range.
            self.day_types[max(self.brackets_seen)] = self.day_type()
        self.brackets_seen.add(index)
        rows = self.tpo.setdefault(level, set())
        if index not in rows:
            rows.add(index)
            self._structure_version += 1
        if size > 0:
            self.volume[level] = self.volume.get(level, 0.0) + size
        if self.open_price is None:
            self.open_price = price
        self.last_price = price
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)
        if index < INITIAL_BALANCE_BRACKETS:
            self.ib_high = price if self.ib_high is None else max(self.ib_high, price)
            self.ib_low = price if self.ib_low is None else min(self.ib_low, price)
        self.last_minute = minute if self.last_minute is None else max(self.last_minute, minute)
        if minute <= SESSION_OPEN_MINUTE + 15:
            self._open_window.append(price)

    # -- statistics ----------------------------------------------------------

    @property
    def poc(self) -> float | None:
        """TPO point of control, with profile-centre proximity breaking ties.

        Volume must not break a TPO-POC tie: doing so made the POC and value
        area expensive, tick-by-tick volume calculations instead of auction
        structure that changes only when a new level/bracket is touched.
        """
        if not self.tpo:
            return None
        if self._cache_version != self._structure_version:
            self._cached_poc = _poc_of({level: len(rows) for level, rows in self.tpo.items()})
        return self._cached_poc

    @property
    def vpoc(self) -> float | None:
        """Volume point of control, kept apart from the TPO POC.

        The two answer different questions -- where price spent the most TIME
        against where the most SIZE changed hands -- and a session where they
        sit far apart is itself a reading. Same tie-break as the TPO POC.
        """
        return _poc_of({level: size for level, size in self.volume.items() if size > 0})

    def value_area(self) -> tuple[float | None, float | None]:
        """VAH/VAL by the standard alternating expansion from the POC."""
        if not self.tpo:
            return None, None
        if self._cache_version == self._structure_version:
            return self._cached_value_area
        poc = self.poc
        if poc is None:
            return None, None
        counts = {level: len(rows) for level, rows in self.tpo.items()}
        self._cached_value_area = _expand_value_area(counts, poc)
        self._cache_version = self._structure_version
        return self._cached_value_area

    def value_area_at(self, bracket: int) -> tuple[float | None, float | None, float | None]:
        """(POC, VAH, VAL) counting only TPOs from brackets up to ``bracket``.

        The developing value area as it stood when that bracket closed; the
        final bracket reproduces value_area() exactly.
        """
        counts = {level: sum(1 for row in rows if row <= bracket)
                  for level, rows in self.tpo.items()}
        counts = {level: count for level, count in counts.items() if count}
        poc = _poc_of(counts)
        if poc is None:
            return None, None, None
        vah, val = _expand_value_area(counts, poc)
        return poc, vah, val

    def va_by_bracket(self) -> list[dict]:
        """The value area's history, one row per bracket seen. The last row is
        the developing value area; the completed one belongs to the PRIOR
        session and comes from the stored profile, not from here.

        One pass, not one ``value_area_at`` call per bracket: the ladder is
        sorted once and the counts accumulate forward, so the walk costs
        O(brackets x levels) with no sort inside it. The result is cached
        against ``_structure_version`` -- the same key the POC and the session
        value area use -- because the structure only changes when a new level
        or a new bracket is touched, not on every tick.
        """
        if self._va_rows_version == self._structure_version:
            return self._cached_va_rows
        rows: list[dict] = []
        if self.tpo:
            ladder = sorted(self.tpo)
            per_bracket: dict[int, list[float]] = {}
            for level, brackets in self.tpo.items():
                for index in brackets:
                    per_bracket.setdefault(index, []).append(level)
            counts: dict[float, float] = {}
            for bracket in sorted(self.brackets_seen):
                for level in per_bracket.get(bracket, ()):
                    counts[level] = counts.get(level, 0.0) + 1
                poc = _poc_of(counts)
                if poc is None:
                    rows.append({"bracket": bracket, "letter": letter_for(bracket),
                                 "poc": None, "vah": None, "val": None})
                    continue
                vah, val = _expand_value_area(
                    counts, poc, [level for level in ladder if level in counts])
                rows.append({"bracket": bracket, "letter": letter_for(bracket),
                             "poc": poc, "vah": vah, "val": val})
        self._cached_va_rows = rows
        self._va_rows_version = self._structure_version
        return rows

    def extension(self) -> dict:
        """Range extension beyond the initial balance, in IB widths.

        day_type() computes this internally and publishes only its verdict;
        the ratio is what the base-rate table is keyed on, so the number the
        verdict came from belongs on the wire beside it.
        """
        if None in (self.ib_high, self.ib_low, self.high, self.low) or self.ib_high <= self.ib_low:
            return {"up": None, "down": None, "ratio": None}
        width = self.ib_high - self.ib_low
        up = max(0.0, self.high - self.ib_high)
        down = max(0.0, self.ib_low - self.low)
        return {"up": round(up, 2), "down": round(down, 2),
                "ratio": round(max(up, down) / width, 3)}

    def poor_extremes(self) -> dict:
        """Poor highs/lows and tails, from the TPO count at the extreme rows.

        A high with two or more TPOs on its top row shows no excess: the
        auction was not rejected there, it simply stopped, and Dalton's
        reading is that it will be revisited. A tail is the opposite shape --
        a run of single-print rows at the extreme -- and needs at least two
        rows before it counts, one lone TPO being the last print of a bar
        rather than a rejection.
        """
        # An extreme made by the only observed bracket is not an auction tail:
        # every row would be a single print, yielding both a high and low tail
        # across the entire captured range. A late-joined session has the same
        # problem even after a second bracket, because the actual session
        # extremes may have occurred before capture began.
        if not self.tpo or self.partial_capture or len(self.brackets_seen) < 2:
            return {"poor_high": None, "poor_low": None, "tail_high": 0, "tail_low": 0}
        levels = sorted(self.tpo)

        def tail(sequence) -> int:
            run = 0
            for level in sequence:
                if len(self.tpo[level]) != 1:
                    break
                run += 1
            return run

        tail_high, tail_low = tail(reversed(levels)), tail(levels)
        return {
            "poor_high": len(self.tpo[levels[-1]]) >= 2,
            "poor_low": len(self.tpo[levels[0]]) >= 2,
            "tail_high": tail_high if tail_high >= 2 else 0,
            "tail_low": tail_low if tail_low >= 2 else 0,
        }

    @property
    def single_prints(self) -> list[float]:
        """Levels touched by exactly one bracket — the trace of a fast move."""
        return sorted(level for level, rows in self.tpo.items() if len(rows) == 1)

    @property
    def open_observed(self) -> bool:
        """Did we watch the 09:15-09:30 window that defines the open type?"""
        return bool(self._open_window)

    @property
    def partial_capture(self) -> bool:
        """The first observed trade arrived after the session's A bracket."""
        return self.first_bracket is not None and self.first_bracket > 0

    @property
    def ib_complete(self) -> bool:
        """Both initial-balance brackets closed, and watched from the open.

        Checking only that the first observed bracket was 0 marked the IB
        complete on the very first print of the day: at 09:41 every symbol
        reported ib_complete=True with 34 minutes of the initial balance still
        to run. The question is whether the IB *window* has closed, so it is
        answered from the latest print, not the earliest.
        """
        if self.first_bracket != 0 or self.last_minute is None:
            return False
        return bracket_of(self.last_minute) >= INITIAL_BALANCE_BRACKETS

    def open_type(self) -> str:
        """Dalton's open classification, from the first 15 minutes vs the IB.

        Distinguishes "not yet determinable" from "we were not watching":
        reporting "forming" at 15:00 because the process started at 09:44 is a
        claim the data cannot support.
        """
        if not self._open_window:
            past_window = self.last_minute is not None and self.last_minute > SESSION_OPEN_MINUTE + 15
            return "unobserved" if past_window else "forming"
        if self.ib_high is None or self.ib_low is None:
            return "forming"
        span = self.ib_high - self.ib_low
        if span <= 0:
            return "forming"
        first = self._open_window[0]
        drift = (self._open_window[-1] - first) / span
        excursion_low = (first - min(self._open_window)) / span
        excursion_high = (max(self._open_window) - first) / span
        if abs(drift) >= 0.55 and min(excursion_low, excursion_high) <= 0.12:
            return "open_drive_up" if drift > 0 else "open_drive_down"
        if abs(drift) >= 0.35:
            return "open_test_drive_up" if drift > 0 else "open_test_drive_down"
        if max(excursion_low, excursion_high) >= 0.45:
            return "open_rejection_reverse"
        return "open_auction"

    def day_type(self) -> str:
        """Range extension beyond the initial balance defines the day type.

        Only meaningful once the IB has closed. While it is still widening the
        session range and the IB are the same thing, so extension is ~0 and
        every symbol reads "normal_day" whatever it is actually doing — which
        is what the desk displayed all through the first hour. Mirror
        open_type() and distinguish "too early" from "we were not watching".
        """
        if not self.ib_complete:
            if self.first_bracket is not None and self.first_bracket > 0:
                return "unobserved"
            return "forming"
        if self.ib_high is None or self.ib_low is None or self.high is None or self.low is None:
            return "forming"
        ib_range = self.ib_high - self.ib_low
        if ib_range <= 0:
            return "forming"
        extension = ((self.high - self.ib_high) + (self.ib_low - self.low)) / ib_range
        up = self.high > self.ib_high
        down = self.low < self.ib_low
        if extension <= 0.05:
            return "normal_day"
        if up and down:
            return "neutral_day"
        if extension >= 1.0:
            return "trend_day_up" if up else "trend_day_down"
        return "normal_variation_up" if up else "normal_variation_down"

    def position(self) -> str:
        """Where the last print sits relative to value — the trade context."""
        vah, val = self.value_area()
        price = self.last_price
        if price is None or vah is None or val is None:
            return "unknown"
        if price > vah:
            return "above_value"
        if price < val:
            return "below_value"
        return "in_value"

    def snapshot(self, max_levels: int | None = 90) -> dict:
        vah, val = self.value_area()
        levels = sorted(self.tpo, reverse=True)
        levels_total = len(levels)
        single_prints = self.single_prints
        if max_levels is not None and max_levels > 0 and len(levels) > max_levels:
            step = len(levels) / max_levels
            levels = [levels[int(i * step)] for i in range(max_levels)]
        return {
            "symbol": self.symbol,
            "day": self.day,
            "tick_size": self.tick_size,
            "levels_total": levels_total,
            "levels_returned": len(levels),
            "levels_sampled": len(levels) < levels_total,
            "single_prints_total": len(single_prints),
            "single_prints_sampled": max_levels is not None and len(single_prints) > 20,
            "open": self.open_price,
            "last": self.last_price,
            "high": self.high,
            "low": self.low,
            "poc": self.poc,
            "vah": vah,
            "val": val,
            "ib_high": self.ib_high,
            "ib_low": self.ib_low,
            "brackets": len(self.brackets_seen),
            "open_type": self.open_type(),
            "open_observed": self.open_observed,
            "partial_capture": self.partial_capture,
            "day_type": self.day_type(),
            # A day type read off a partial initial balance is not comparable
            # with one read off a complete session — say so rather than imply it.
            "ib_complete": self.ib_complete,
            "first_bracket": self.first_bracket,
            "position": self.position(),
            "single_prints": single_prints if max_levels is None else single_prints[:20],
            "vpoc": self.vpoc,
            "extension": self.extension(),
            **self.poor_extremes(),
            "va_by_bracket": self.va_by_bracket(),
            "day_type_by_bracket": [
                {"bracket": bracket, "letter": letter_for(bracket), "day_type": kind}
                for bracket, kind in sorted(self.day_types.items())
            ],
            "levels": [
                {
                    "price": level,
                    "tpo": len(self.tpo[level]),
                    "letters": "".join(letter_for(i) for i in sorted(self.tpo[level])),
                    "volume": round(self.volume.get(level, 0.0)),
                }
                for level in levels
            ],
        }


class ProfileBook:
    """Profiles for every tracked symbol, rolled per IST session."""

    def __init__(self, tick_size: float = 0.05):
        self.tick_size = tick_size
        self.profiles: dict[str, Profile] = {}

    @staticmethod
    def _day_and_minute(moment: datetime) -> tuple[str, int]:
        ist = moment.astimezone(IST)
        return ist.date().isoformat(), ist.hour * 60 + ist.minute

    def on_print(self, symbol: str, price: float, size: float, moment: datetime) -> Profile:
        day, minute = self._day_and_minute(moment)
        profile = self.profiles.get(symbol)
        if profile is None or profile.day != day:
            profile = Profile(symbol, day, self.tick_size)
            self.profiles[symbol] = profile
        profile.add(price, size, minute)
        return profile

    def get(self, symbol: str) -> Profile | None:
        return self.profiles.get(symbol)
