"""Session order flow of each option's underlying, recorded at every paper entry. Measured, never read by a rule.

research/auction_orderflow_20261015 (7-9 sessions): buying an option in the direction of its underlying's
session cumulative delta was consistently worse -- MACD lane -15.7 pp per entry (t -2.4), desk trades -7.6 pp,
desk signals -1.9 pp -- while short-window flow did not predict the next 30-60 minutes at all. This logs the
reading on every lane's entries so a contrarian "don't chase session CVD" filter can be judged on a real sample.

The engine has no flow tracker of its own (the desk's lives in another process), so this runs the desk's
OrderFlowTracker on underlying ticks only: stock spots and index futures. Index spots carry no volume or quotes;
an index option is read through its index future, as the desk does.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .orderflow import OrderFlowTracker
from .rollover import futures_root

IST = ZoneInfo("Asia/Kolkata")
INDEX_FUTURE_PREFIX = {"NIFTY": "NSE:NIFTY", "BANKNIFTY": "NSE:BANKNIFTY", "SENSEX": "BSE:SENSEX",
                       "MIDCPNIFTY": "NSE:MIDCPNIFTY"}


def is_underlying(symbol: str) -> bool:
    return symbol.endswith("-EQ") or symbol.endswith("FUT")


class EntryFlow:
    def __init__(self, snapshots=None) -> None:
        self.tracker = OrderFlowTracker()
        # The desk's published flow (live_features.FlowSnapshots), read first; this process's own tracker is the
        # fallback until the two are shown to agree on live sessions.
        self.snapshots = snapshots
        self.reads = {"desk": 0, "local": 0}
        self.day: str | None = None
        self.prints = 0
        self.error: str | None = None
        self._partial_symbols: set[str] = set()
        self._interval_started: dict[str, str] = {}

    def _roll_day(self, now: datetime) -> None:
        day = now.astimezone(IST).date().isoformat()
        if day != self.day:
            self.tracker.reset()
            self.day = day
            self._partial_symbols.clear()
            self._interval_started.clear()

    def _snapshot(self, symbol: str, now: datetime) -> dict | None:
        published = self.snapshots.get(symbol, now.timestamp() * 1000) if self.snapshots is not None else None
        return published if published is not None and published.get("day") == self.day else None

    def _use_snapshot(self, symbol: str) -> None:
        # Local ticks were skipped while the desk covered this symbol. A later fallback starts a new baseline;
        # neither the old local CVD nor the cumulative-volume jump across that gap is a session measurement.
        self.tracker.reset(symbol)
        self._partial_symbols.add(symbol)
        self._interval_started.pop(symbol, None)

    def on_tick(self, tick, now: datetime | None = None, *, use_snapshots: bool = True) -> None:
        """Use the desk per symbol while fresh; otherwise observe a clearly labelled local interval."""
        if not is_underlying(tick.symbol):
            return
        moment = now or datetime.now(IST)
        self._roll_day(moment)
        if use_snapshots and self._snapshot(tick.symbol, moment) is not None:
            self._use_snapshot(tick.symbol)
            return
        if tick.symbol not in self.tracker.states:
            self._interval_started[tick.symbol] = tick.timestamp.isoformat()
        if self.tracker.on_tick(tick) is not None:
            self.prints += 1

    def _underlying(self, parsed: dict, now: datetime, use_snapshots: bool) -> str | None:
        root = parsed["root"]
        prefix = INDEX_FUTURE_PREFIX.get(root)
        if prefix is None:
            return parsed.get("spot")
        # The live series is the one ticking; with two series listed near expiry take the busier.
        exchange = prefix.partition(":")[0]
        candidates = {symbol: state.total_volume for symbol, state in self.tracker.states.items()
                      if futures_root(symbol) == (exchange, root)}
        if use_snapshots and self.snapshots is not None:
            for symbol in self.snapshots.latest:
                if futures_root(symbol) == (exchange, root):
                    published = self._snapshot(symbol, now)
                    if published is not None:
                        candidates[symbol] = published.get("session_total_volume") or published.get("session_classified_volume") or 0
        candidates = [(volume, symbol) for symbol, volume in candidates.items()]
        return max(candidates)[1] if candidates else None

    def context(self, option_symbol: str, now: datetime | None = None, *, use_snapshots: bool = True) -> dict | None:
        """The underlying's session flow, signed by the option's side (+ = flow agrees with the trade)."""
        from .option_agent import parse_option
        try:
            moment = now or datetime.now(IST)
            self._roll_day(moment)
            parsed = parse_option(option_symbol)
            if not parsed:
                return None
            underlying = self._underlying(parsed, moment, use_snapshots)
            out = {"underlying": underlying, "side": parsed["side"], "flow_day": self.day}
            published = self._snapshot(underlying, moment) if use_snapshots and underlying else None
            if published is not None:
                self._use_snapshot(underlying)
                classified = published.get("session_classified_volume") or 0
                share = published["session_cvd"] / classified if classified else None
                self.reads["desk"] += 1
                return {**out, "source": "desk", "flow_scope": "session", "session_cvd": published["session_cvd"],
                        "session_classified_volume": classified,
                        "session_total_volume": published.get("session_total_volume"),
                        "session_cvd_share_signed": round(share * parsed["side"], 5) if share is not None else None,
                        "underlying_price": published.get("last_price"), "prints": published.get("trades")}
            state = self.tracker.states.get(underlying) if underlying else None
            self.reads["local"] += 1
            if state is None:
                return {**out, "source": "local", "session_cvd": None}
            classified = state.buy_volume + state.sell_volume
            share = state.cumulative_delta / classified if classified else None
            if underlying in self._partial_symbols:
                return {**out, "source": "local", "flow_scope": "fallback_interval", "session_cvd": None,
                        "session_classified_volume": None, "session_total_volume": None,
                        "session_cvd_share_signed": None, "interval_cvd": state.cumulative_delta,
                        "interval_classified_volume": classified, "interval_total_volume": state.total_volume,
                        "interval_cvd_share_signed": round(share * parsed["side"], 5) if share is not None else None,
                        "interval_started_at": self._interval_started.get(underlying),
                        "underlying_price": state.last_price, "prints": state.trades}
            return {**out, "source": "local", "session_cvd": state.cumulative_delta,
                    "session_classified_volume": classified,
                    "session_total_volume": state.total_volume,
                    "session_cvd_share_signed": round(share * parsed["side"], 5) if share is not None else None,
                    "underlying_price": state.last_price, "prints": state.trades}
        except Exception as exc:  # noqa: BLE001 -- a measurement must never block an entry
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            return None

    def health(self) -> dict:
        return {"day": self.day, "underlyings": len(self.tracker.states), "prints": self.prints, "error": self.error,
                "partial_underlyings": len(self._partial_symbols & self.tracker.states.keys()),
                "reads": dict(self.reads),
                "published": self.snapshots.status() if self.snapshots is not None else None}
