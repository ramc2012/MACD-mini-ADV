"""The Market Profile / order-flow desk, as one surface two processes share.

DeskMixin holds everything the auction desk, its tick capture, the whale
tracker and the nightly memory need from an engine. TradingEngine uses it
when it runs every lane in one process (role "all"). DeskEngine (desk_app)
uses the same code in its own process (role "desk"), fed by the tick bus and
by the contract context the strategy process serves, so both layouts run
exactly the same desk logic.

What a host must provide: settings, events, mp, tick_store, whale_live,
latest_ticks, contract_selector.contracts, futures_rollover, all_symbols,
broker.option_chain / broker.quotes, rollover_errors, and the chain/nightly
status fields initialised by ``init_desk_state``.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from . import market_calendar, nightly, oi_capture, whale
from .self_heal import session_history_coverage
from .rollover import days_to_expiry, is_futures_symbol, stock_entry_closed
from .models import Tick
from .universe import INDEX_SPOTS, desk_underlying

# Index instruments (futures, options, spots) by symbol prefix; no F&O stock begins with these.
INDEX_OPTION_PREFIXES = ("NSE:NIFTY", "NSE:BANKNIFTY", "BSE:SENSEX", "NSE:MIDCPNIFTY")

IST = ZoneInfo("Asia/Kolkata")
# Fyers rate-limits /data/quotes per endpoint. The futures-OI call is once a
# minute for two symbols, but the client retries a 429 four times with backoff,
# so a limited minute spends four more calls keeping the bucket full — the
# refusal sustains itself. Futures OI is one input to Layer C's divergence and
# is worth far less than the quota, so a 429 stands the call down for a while
# instead, doubling up to half an hour.
QUOTES_BACKOFF_SECONDS = 300.0
QUOTES_BACKOFF_MAX_SECONDS = 1800.0
NIGHTLY_MAX_ATTEMPTS = 60
# Minute coverage of thin names (PAGEIND, BOSCHLTD ...) is never complete: a minute bar exists only when the
# contract trades. Waiting for "complete" kept 19 Oct's nightly waiting for good. After this time the nightly runs
# on what it has and records the coverage as partial.
NIGHTLY_DEADLINE = (21, 30)
# Raw ticks are condensed outside market hours; check periodically.
TICK_MAINTENANCE_INTERVAL_SECONDS = 900
# The option agent summarises the earlier sessions at 14:30 (they do not change
# during the day), emits after the 14:45 feature cutoff and learns once the
# evening's bars are in. A minute bar is written only when the contract's next
# tick arrives, so at 14:45:15 thin contracts had no 14:44 bar yet; 14:47 lets
# those bars land (features still stop at 14:45).
AGENT_PREPARE_AT = (14, 30, 0)
AGENT_EMIT_AT = (14, 47, 0)
AGENT_LEARN_AFTER = (16, 5)
AGENT_RETRY_SECONDS = 60.0
# Scheduled and HTTP jobs mutate the same candidates and model archive.
AGENT_JOB_LOCK = asyncio.Lock()


def desk_settings(settings, *, enabled: bool):
    """The auction desk's own settings, from the engine settings (mp_*)."""
    from .mp_engine import MPSettings

    return MPSettings(
        enabled=enabled,
        auto_trade=settings.mp_auto_trade,
        initial_capital=settings.mp_initial_capital,
        max_positions=settings.mp_max_positions,
        max_trades_per_day=settings.mp_max_trades_per_day,
        hard_stop_pct=settings.mp_hard_stop_pct,
        trail_activation_pct=settings.mp_trail_activation_pct,
        trail_pct=settings.mp_trail_pct,
        min_imbalance=settings.mp_min_imbalance,
        slippage_bps=settings.mp_slippage_bps,
        brokerage_per_leg=settings.mp_brokerage_per_leg,
        allow_overnight_carry=settings.mp_allow_overnight_carry,
        max_notional_per_trade=settings.mp_max_notional_per_trade,
    )


def _engine():
    """Session clocks live in the engine module, which tests patch; resolve
    them there at call time (a top-level import would be circular)."""
    from . import engine
    return engine


class DeskMixin:
    def init_desk_state(self) -> None:
        self.nightly_report: dict | None = None
        self._nightly_done: str | None = None
        self._nightly_attempts = 0
        self._nightly_caught_up: set[str] = set()
        self.chain_status: dict = {"day": None, "snapshots": 0, "last_at": None, "error": None,
                                   "whale_error": None, "windows": 0, "alerts_today": 0,
                                   "history_days": 0, "composite": {}}
        self.whale_alert_queue: list[dict] = []
        self.archive_report: dict | None = None
        self._quotes_blocked_until = 0.0
        self._quotes_backoff = QUOTES_BACKOFF_SECONDS
        self.agent_status: dict = {"emitted_day": None, "emit": None, "learned_day": None, "learn": None,
                                   "backfill": None, "error": None}
        self._agent_emit_retry_after = 0.0

    async def _desk_on_tick(self, tick, analysis_only: bool) -> None:
        """The desk's share of one accepted tick, in the engine's order."""
        self.whale_live.on_tick(tick)
        if self.settings.tick_capture_enabled and not analysis_only:
            # Buffered in memory, flushed off the loop. Wrapped because capture
            # must never be the reason the feed stops.
            try:
                self.tick_store.add(tick)
            except Exception:  # noqa: BLE001
                pass
        if not analysis_only:
            try:
                await self.mp.on_tick(tick)
            except Exception as exc:  # noqa: BLE001 — a desk fault must not kill the feed
                self.mp.record_error(exc)
            shadow = getattr(self, "mp_index", None)
            if shadow is not None:
                try:
                    await shadow.on_tick(tick)
                except Exception as exc:  # noqa: BLE001 — nor may its shadow
                    shadow.record_error(exc)

    def configure_desk(self, lot_sizes: dict[str, int]) -> None:
        """Hand the desk today's contracts, after a (re)selection."""
        self.mp.set_lot_sizes(lot_sizes)
        self.mp.set_option_map(self.desk_option_map())
        self.mp.set_position_underlyings(self.desk_position_underlyings())
        self.mp.set_expiring_symbols({
            symbol for symbol, contract in self.contract_selector.contracts.items()
            if (days_to_expiry(contract.expiry) is not None and days_to_expiry(contract.expiry) <= 0)
            or stock_entry_closed(symbol, contract.expiry)      # a stock option on/after its T-2 last day
        })
        self.mp.set_directional_scope(self.mp_spot_symbols())
        self.mp.set_universe(self.mp_universe())
        shadow = getattr(self, "mp_index", None)
        if shadow is not None:
            # The same desk restricted to index reads: its scope is the index futures, its universe those
            # futures plus their options. Everything else (lots, contracts, expiries) is the main desk's.
            scope = [symbol for symbol in self.mp_spot_symbols() if is_futures_symbol(symbol)]
            shadow.set_lot_sizes(lot_sizes)
            shadow.set_option_map(self.desk_option_map())
            shadow.set_position_underlyings(self.desk_position_underlyings())
            shadow.set_expiring_symbols(set(self.mp.expiring_symbols))
            shadow.set_directional_scope(scope)
            shadow.set_universe([symbol for symbol in self.mp_universe()
                                 if symbol in scope or symbol.startswith(INDEX_OPTION_PREFIXES)])

    def mp_index_shadow_view(self) -> dict | None:
        """Health and record of the index-only shadow desk (no round-trip rows)."""
        shadow = getattr(self, "mp_index", None)
        if shadow is None:
            return None
        stats = {key: value for key, value in shadow.statistics().items() if key != "round_trip_rows"}
        return {"health": shadow.health(), "statistics": stats, "scope": sorted(shadow.directional_scope or []),
                "open_positions": len(shadow.portfolio.positions), "trades_today": shadow.trades_today}

    def expiring_desk_positions(self) -> list[str]:
        return [symbol for symbol, position in self.mp.portfolio.positions.items()
                if symbol in self.mp.expiring_symbols and position.quantity > 0]

    async def _flatten_expiring_desk(self, symbols: list[str]) -> None:
        """Close the desk's expiring holdings against a fresh live mark."""
        for symbol in symbols:
            position = self.mp.portfolio.positions.get(symbol)
            if not position or position.quantity <= 0:
                continue
            key = f"mp_expiry:{symbol}"
            price = self.mp.last_prices.get(symbol)
            stamp = self.mp.last_price_at.get(symbol)
            age = (datetime.now(UTC) - stamp).total_seconds() if stamp else None
            if (price is None or not math.isfinite(price) or price <= 0 or age is None
                    or age < -5 or age > self.settings.order_quote_max_age_seconds):
                self.rollover_errors[key] = "expiring position needs a fresh live mark"
                continue
            self.mp.entry_state.setdefault(symbol, {})["exit_reason"] = "MP_EXPIRY_DAY_FLATTEN"
            try:
                order = await self.mp.submit(
                    symbol, "SELL", position.quantity, note="MP_EXPIRY_DAY_FLATTEN")
                if order is None or symbol in self.mp.portfolio.positions:
                    raise RuntimeError(self.mp.last_order_rejection or "expiry exit has not filled")
            except Exception as exc:  # noqa: BLE001 - retry only this holding next pass
                self.rollover_errors[key] = str(exc)
            else:
                self.rollover_errors.pop(key, None)

    def _expiry_flatten_time(self) -> tuple[int, int] | None:
        raw = str(getattr(self.settings, "expiry_flatten_ist", "") or "").strip()
        try:
            hour, minute = (int(part) for part in raw.split(":", 1))
        except ValueError:
            return None
        return (hour, minute) if 0 <= hour < 24 and 0 <= minute < 60 else None

    def mp_universe(self) -> list[str]:
        """The auction desk's scope: its own spots plus their ATM contracts.

        The MACD lane runs on the full universe and must not be narrowed by
        this; the desk is scoped separately because its trade-by-trade feed is
        capped at roughly 15 instruments. An empty mp_symbols_csv means the
        desk watches everything the feed carries, as before.
        """
        spots = self.mp_spot_symbols()
        if not spots:
            return list(self.all_symbols)
        allowed = set(spots)
        # Options are keyed to the SPOT, but the desk may be watching the
        # future, so match on the shared underlying name rather than the
        # symbol: NSE:NIFTY26AUGFUT and NSE:NIFTY50-INDEX are both NIFTY.
        underlyings = {name for name in (desk_underlying(item) for item in spots) if name}
        for symbol, contract in self.contract_selector.contracts.items():
            if (getattr(contract, "spot_symbol", None) in allowed
                    or getattr(contract, "underlying", None) in underlyings) \
                    and (not getattr(contract, "analysis_only", False) or getattr(contract, "retained", False)):
                allowed.add(symbol)
        return [symbol for symbol in self.all_symbols if symbol in allowed]

    def mp_configured_symbols(self) -> list[str]:
        """Exactly what the desk setting names, before any series rollover."""
        return [item.strip() for item in self.settings.mp_symbols_csv.split(",") if item.strip()]

    def mp_spot_symbols(self) -> list[str]:
        """The instruments the desk is scoped to, before options are added.

        Futures entries are mapped to whichever series is live today. Without
        this the setting pinned NSE:NIFTY26AUGFUT and friends, and the day
        after that series expired the desk subscribed to symbols the feed no
        longer carries -- silently, because an unknown symbol is not an error,
        it is just no ticks.
        """
        return [self.futures_rollover.get(item, item) for item in self.mp_configured_symbols()]

    def desk_option_map(self) -> dict[str, dict[str, str]]:
        """Each desk instrument -> the ATM CE/PE that expresses a view on it.

        The desk reads the auction on an index future or an equity, neither of
        which it can size inside a 1-lakh cap, and expresses the read in that
        underlying's option instead. Contracts are keyed to the SPOT, so the
        join is on the shared underlying name: NSE:NIFTY26SEPFUT and
        NSE:NIFTY50-INDEX are both NIFTY.
        """
        by_underlying: dict[str, dict[str, str]] = {}
        for symbol, contract in self.contract_selector.contracts.items():
            name = getattr(contract, "underlying", None)
            side = getattr(contract, "option_type", None)
            if name and side and (
                getattr(contract, "moneyness", "ATM") == "ATM"
                or getattr(contract, "retained", False)
            ):
                choices = by_underlying.setdefault(name, {})
                # A carried strike remains subscribed for management, but new
                # signals must keep using today's selected ATM contract.
                if side not in choices or not getattr(contract, "retained", False):
                    choices[side] = symbol
        mapping: dict[str, dict[str, str]] = {}
        for item in self.mp_spot_symbols():
            name = desk_underlying(item)
            if name and by_underlying.get(name):
                mapping[item] = dict(by_underlying[name])
        return mapping

    def desk_position_underlyings(self) -> dict[str, str]:
        """Auction sources for MP positions retained beyond their entry day."""
        source_by_name = {
            name: symbol
            for symbol in self.mp_spot_symbols()
            if (name := desk_underlying(symbol))
        }
        mapping: dict[str, str] = {}
        for symbol in self.mp.portfolio.positions:
            contract = self.contract_selector.contracts.get(symbol)
            source = source_by_name.get(getattr(contract, "underlying", None)) if contract else None
            if source:
                mapping[symbol] = source
        return mapping

    def archive(self):
        """The market-data archive (archive.py), or None when its folder is not mounted -- then nothing is
        deleted from the hot stores, which is the safe failure."""
        from .archive import Archive
        archive = Archive(getattr(self.settings, "archive_dir", "") or "/nonexistent")
        return archive if archive.available() else None

    def _archive_pass(self) -> dict:
        """Seal finished days into the archive (worker thread; outside market hours)."""
        from . import archive as arc
        archive = self.archive()
        if archive is None:
            return {"error": f"archive folder {getattr(self.settings, 'archive_dir', '')} is not mounted; "
                             "the hot stores keep everything"}
        return arc.seal_pending(archive, history_path=self.settings.research_database_path,
                                ticks_path=self.settings.tick_database_path if self.settings.tick_capture_enabled else "",
                                today=datetime.now(IST).date(),
                                tick_retention_days=getattr(self.settings, "tick_archive_retention_days", 120))

    async def _tick_maintenance_loop(self) -> None:
        """Outside market hours: condense finished days, seal them into the archive, then trim the hot stores.

        Order matters. Condensing writes the minute flow and ladder (the raw ticks stay); the archive pass seals
        ticks, flow, ladders, bars, chains and OI; only then may ``purge_raw`` / ``prune_flow`` delete, and only
        what the archive covers. Condensing a full day streams millions of rows, so none of this runs during
        the session. The VACUUM afterwards returns the deleted rows' pages to the filesystem.
        """
        while True:
            await asyncio.sleep(TICK_MAINTENANCE_INTERVAL_SECONDS)
            if _engine().regular_session_open() or _engine().preopen_window():
                continue
            try:
                await asyncio.to_thread(self.tick_store.condense_pending)        # condense only; no archive yet
                self.archive_report = await asyncio.to_thread(self._archive_pass)
                archive = self.archive()
                await asyncio.to_thread(self.tick_store.purge_raw, None, archive)
                pruned = await asyncio.to_thread(self.tick_store.prune_flow, None, archive)
                if (self.tick_store.last_purge or {}).get("raw_ticks") or pruned["minute_rows"] or pruned["ladder_rows"]:
                    await asyncio.to_thread(self.tick_store.vacuum)
            except Exception as exc:  # noqa: BLE001 — maintenance must not die
                self.tick_store.last_error = f"tick maintenance failed: {exc}"

    def nightly_symbols(self) -> list[str]:
        """Underlyings only: spots, indices, the desk's futures."""
        return sorted({*self.settings.symbols, *self.mp_spot_symbols()})

    def whale_symbols(self) -> list[str]:
        return [s for s in self.mp_spot_symbols() if is_futures_symbol(s)]

    def _missed_nightly_day(self, now: datetime) -> str | None:
        """The previous session when its nightly never ran (a stuck wait, a crash, a restart): no session profiles."""
        import sqlite3
        previous = market_calendar.previous_trading_day(now.date()).isoformat()
        caught = self.__dict__.setdefault("_nightly_caught_up", set())
        if previous in caught:
            return None
        try:
            with sqlite3.connect(f"file:{self.settings.research_database_path}?mode=ro", uri=True, timeout=30) as db:
                done = db.execute("SELECT 1 FROM session_profiles WHERE day = ? LIMIT 1", (previous,)).fetchone()
                # A session lost to an outage has no minutes until the engine's outage repair downloads them (it
                # needs the broker login): wait for them rather than writing the day off with nothing in it.
                opened = int(datetime.fromisoformat(f"{previous}T09:15:00").replace(tzinfo=IST).timestamp())
                minutes = db.execute("""SELECT COUNT(*) FROM historical_candles WHERE symbol = 'NSE:NIFTY50-INDEX'
                                        AND timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?""",
                                     (opened, opened + 22_500)).fetchone()[0]
        except sqlite3.Error:
            return None
        if done:
            caught.add(previous)
            return None
        return previous if minutes >= 300 else None

    async def _run_nightly(self, day: str, coverage: dict) -> dict:
        report = await asyncio.to_thread(
            nightly.run, self.settings.tick_database_path, self.settings.research_database_path, day,
            self.nightly_symbols(), self.whale_symbols(), dict(self.mp.lot_sizes), self.archive())
        return {**report, "coverage_complete": bool(coverage.get("complete")),
                "partial": not coverage.get("complete"), "missing_symbols": len(coverage.get("missing") or {})}

    async def _nightly_loop(self) -> None:
        """Once per session, after the 15:40 close, turn the day into memory; catch up a session that was missed.

        Never during market hours. A session whose minute coverage is still incomplete at NIGHTLY_DEADLINE runs
        anyway and is recorded as partial; a missed previous session is caught up at once (its data will not
        improve by waiting).
        """
        while True:
            await asyncio.sleep(60)
            now = datetime.now(IST)
            today = now.date().isoformat()
            trading_today = market_calendar.is_trading_day(now.date())
            if trading_today and (9, 0) <= (now.hour, now.minute) < (15, 50):
                continue
            missed = await asyncio.to_thread(self._missed_nightly_day, now)
            if missed:
                self.__dict__.setdefault("_nightly_caught_up", set()).add(missed)
                try:
                    coverage = await asyncio.to_thread(session_history_coverage, self.settings.research_database_path,
                                                       date.fromisoformat(missed), self.nightly_symbols())
                    self.nightly_report = {**await self._run_nightly(missed, coverage), "caught_up": True}
                except Exception as exc:  # noqa: BLE001 — memory must not kill the feed
                    self.nightly_report = {"day": missed, "error": str(exc), "caught_up": True}
                continue
            if not trading_today or self._nightly_done == today or (now.hour, now.minute) < (15, 50):
                continue
            coverage = await asyncio.to_thread(session_history_coverage,
                self.settings.research_database_path, now.date(), self.nightly_symbols())
            if not coverage["complete"] and (now.hour, now.minute) < NIGHTLY_DEADLINE:
                self.nightly_report = {"day": today, "waiting": "session minute history incomplete",
                                       "runs_anyway_at": "%02d:%02d" % NIGHTLY_DEADLINE, "quality": coverage, "partial": True}
                continue
            try:
                self.nightly_report = await self._run_nightly(today, coverage)
                self._nightly_done = today
                self._nightly_attempts = 0
            except Exception as exc:  # noqa: BLE001 — memory must not kill the feed
                # A locked database (a research job holding the file) is the
                # ordinary failure here. Try again next minute rather than
                # writing the day off; give up only after an hour of it.
                self._nightly_attempts += 1
                self.nightly_report = {"day": today, "error": str(exc),
                                       "attempts": self._nightly_attempts}
                if self._nightly_attempts >= NIGHTLY_MAX_ATTEMPTS:
                    self._nightly_done = today

    async def _agent_loop(self) -> None:
        """The option agent's day: picks after the 14:45 cutoff, learning after the close.

        Learning first completes the forward minute bars of every unlabelled
        candidate (a strike that drifted away from ATM is no longer streamed,
        and a label missing exactly where the underlying moved would bias the
        model), then labels whatever has closed and re-judges the models.
        """
        while True:
            await asyncio.sleep(5)
            now = datetime.now(IST)
            if not market_calendar.is_trading_day(now.date()):
                continue
            try:
                if await self._agent_step(now):
                    self.agent_status["error"] = None
            except Exception as exc:  # noqa: BLE001 — the agent must not kill the desk
                self.agent_status["error"] = f"{type(exc).__name__}: {exc}"[:300]

    async def _agent_step(self, now: datetime) -> bool:
        from . import option_agent
        today = now.date().isoformat()
        clock = (now.hour, now.minute, now.second)
        did_work = False
        async with AGENT_JOB_LOCK:
            if self.agent_status.get("prepared_day") != today and AGENT_PREPARE_AT <= clock < AGENT_EMIT_AT:
                did_work = True
                self.agent_status["prepared_day"] = today
                started = time.monotonic()
                await asyncio.to_thread(self._agent_job, lambda h: option_agent.prepare(h, today))
                self.agent_status["prepare_seconds"] = round(time.monotonic() - started, 1)
            if (self.agent_status["emitted_day"] != today and AGENT_EMIT_AT <= clock < (15, 30, 0)
                    and time.monotonic() >= self._agent_emit_retry_after):
                did_work = True
                from .agent_equity import known_lots
                lots = known_lots(dict(self.mp.lot_sizes), self.settings.contract_snapshot_path)
                started = time.monotonic()
                self._agent_emit_retry_after = started + AGENT_RETRY_SECONDS
                self.agent_status["emit"] = await asyncio.to_thread(
                    self._agent_job, lambda h: option_agent.emit(h, lots=lots))
                self.agent_status["emit_seconds"] = round(time.monotonic() - started, 1)
                if self.agent_status["emit"].get("status") in {"emitted", "already_emitted"}:
                    self.agent_status["emitted_day"] = today
            if (self.agent_status["learned_day"] != today and clock >= (*AGENT_LEARN_AFTER, 0)
                    and self._nightly_done == today):
                self.agent_status["learned_day"] = today
                did_work = True
                self.agent_status["backfill"] = await self._agent_backfill(today)
                self.agent_status["learn"] = await asyncio.to_thread(self._agent_job, option_agent.learn)
        return did_work

    def _agent_job(self, fn):
        import sqlite3
        history = sqlite3.connect(self.settings.research_database_path, timeout=120)
        try:
            return fn(history)
        finally:
            history.close()

    async def _agent_backfill(self, today: str) -> dict:
        from . import option_agent
        first, symbols = await asyncio.to_thread(
            self._agent_job, lambda h: option_agent.pending_symbols(h, today))
        stored = failed = 0
        strategy = getattr(self, "strategy", None)
        if strategy is None or not symbols:
            return {"symbols": len(symbols), "rows": 0, "failed": 0}
        for symbol in symbols:
            try:
                stored += (await strategy.backfill_minutes(symbol, first)).get("rows", 0)
            except Exception:  # noqa: BLE001 — one contract must not stop the rest
                failed += 1
            await asyncio.sleep(0.3)
        return {"symbols": len(symbols), "rows": stored, "failed": failed, "since": first}

    async def _chain_loop(self) -> None:
        """Option-chain snapshots every minute during the session.

        Layer B of the whale tracker scores per-strike OI builds against a
        20-day distribution for the same window of the day. That distribution
        does not exist until this has run for weeks, so the collector starts
        now and the scorer waits for it.
        """
        self.whale_live.watch(self._whale_futures())
        while True:
            await asyncio.sleep(60)
            # Re-read every minute so a series rollover or a reconfigure
            # reaches the OFI states without a restart.
            self.whale_live.watch(self._whale_futures())
            if _engine().fo_session_open():
                await self._chain_minute(whale.snapshot_stamp())

    def _whale_futures(self) -> list[str]:
        """The futures the tracker scores — not every desk future: SENSEX has
        no chain collected and no lot in the regime, so a minute row for it
        would be a flow leg beside nothing."""
        return [symbol for symbol in self._whale_roots().values() if symbol]

    async def _chain_minute(self, stamp: int) -> None:
        """One collector tick: snapshot each root's chain, then run the
        tracker's minute over the futures leg."""
        # 'snapshots' and 'windows' are counted for the day, the way
        # 'alerts_today' is; a process that stays up across sessions would
        # otherwise show a lifetime total beside a per-day one.
        day = whale.day_of(stamp)
        if self.chain_status["day"] != day:
            self.chain_status.update({"day": day, "snapshots": 0, "windows": 0, "alerts_today": 0})
        roots = self._whale_roots()
        # The extra roots are snapshot for their OI record only; the whale tracker scores NIFTY/BANKNIFTY.
        chain_roots = [*roots, *(root for root in getattr(self.settings, "chain_extra_roots", ())
                                 if root in INDEX_SPOTS and root not in roots)]
        for root in chain_roots:
            try:
                # The chain is keyed to the INDEX. The desk watches the
                # future, and the future's own symbol is not a chain root.
                chain = await self.broker.option_chain(INDEX_SPOTS[root])
                await asyncio.to_thread(self._store_chain, stamp, root, chain)
                self.chain_status["snapshots"] += 1
                self.chain_status["last_at"] = datetime.now(UTC).isoformat()
                self.chain_status["error"] = None
            except Exception as exc:  # noqa: BLE001
                self.chain_status["error"] = str(exc)
        futures = {root: symbol for root, symbol in roots.items() if symbol}
        open_interest = await self._futures_open_interest(stamp)
        if not futures:
            return
        now = time.monotonic()
        if now < self._quotes_blocked_until:
            quotes, quotes_error = {}, (
                f"futures OI stood down for "
                f"{int(self._quotes_blocked_until - now)}s after a quotes rate limit")
        else:
            try:
                # One batched call: the socket carries no OI, the quotes REST
                # does, and per-symbol calls would spend the rate limit twice.
                quotes = await self.broker.quotes(list(futures.values()))
            except Exception as exc:  # noqa: BLE001
                quotes, quotes_error = {}, str(exc)
                if "429" in quotes_error:
                    self._quotes_blocked_until = now + self._quotes_backoff
                    self._quotes_backoff = min(self._quotes_backoff * 2,
                                               QUOTES_BACKOFF_MAX_SECONDS)
            else:
                quotes_error = None
                self._quotes_backoff = QUOTES_BACKOFF_SECONDS
        for symbol in futures.values():
            # /quotes has no OI; the depth read supplies the futures leg's.
            row = open_interest.get(symbol)
            if row:
                quote = quotes.get(symbol)
                quotes[symbol] = (Tick(symbol, row.get("ltp") or 0.0, row.get("volume") or 0, open_interest=row["oi"])
                                  if quote is None else replace(quote, open_interest=row["oi"]))
        flow_states = {symbol: self.mp.flow.states.get(symbol) for symbol in futures.values()}
        try:
            payload, problem = await asyncio.to_thread(
                self._whale_minute, stamp, futures, flow_states, quotes)
            self.events.publish("whale", payload)
            self.chain_status["whale_error"] = problem or quotes_error
        except Exception as exc:  # noqa: BLE001
            self.chain_status["whale_error"] = str(exc)

    def _oi_futures(self) -> list[str]:
        """Every index future the desk watches, for the minute OI record."""
        return [symbol for symbol in self.mp_spot_symbols()
                if is_futures_symbol(symbol) and desk_underlying(symbol) in INDEX_SPOTS]

    async def _futures_open_interest(self, stamp: int) -> dict[str, dict]:
        """One depth read per index future (Fyers takes one symbol per depth call), stored in oi_minutes."""
        rows: dict[str, dict] = {}
        if time.monotonic() < self._quotes_blocked_until:
            return rows
        for symbol in self._oi_futures():
            try:
                row = await self.broker.open_interest(symbol)
            except Exception as exc:  # noqa: BLE001 — a missed minute of OI must not cost the chain
                self.chain_status["oi_error"] = str(exc)[:200]
                if "429" in str(exc):
                    break
                continue
            if row:
                rows[symbol] = row
        if rows:
            try:
                await asyncio.to_thread(self._store_oi, stamp, rows)
                self.chain_status.update({"oi_symbols": len(rows), "oi_error": None})
            except Exception as exc:  # noqa: BLE001
                self.chain_status["oi_error"] = str(exc)[:200]
        return rows

    def _store_oi(self, stamp: int, rows: dict[str, dict]) -> None:
        import sqlite3
        connection = sqlite3.connect(self.settings.research_database_path, timeout=30)
        try:
            oi_capture.save_oi(connection, stamp, rows)
        finally:
            connection.close()

    async def _stock_chain_loop(self) -> None:
        """Every F&O stock's chain, a few strikes around the money, once per sweep window.

        The calls are spaced out rather than burst: the engine process makes every Fyers call for both
        processes, and a 200-call burst would collide with its own quotes and history traffic.
        """
        from .universe import STOCK_SPOTS
        while True:
            minutes = self.settings.stock_chain_sweep_minutes
            if minutes <= 0 or not _engine().fo_session_open():
                await asyncio.sleep(60)
                continue
            started = time.monotonic()
            stored = failed = 0
            for name, spot in STOCK_SPOTS.items():
                if not _engine().fo_session_open():
                    break
                if time.monotonic() < self._quotes_blocked_until:
                    await asyncio.sleep(oi_capture.STOCK_CALL_GAP_SECONDS)
                    continue
                try:
                    stored += await self._stock_chain(name, spot)
                except Exception as exc:  # noqa: BLE001 — one stock must not stop the sweep
                    failed += 1
                    self.chain_status["stock_chain_error"] = f"{name}: {exc}"[:200]
                    if "429" in str(exc):
                        self._quotes_blocked_until = time.monotonic() + self._quotes_backoff
                await asyncio.sleep(oi_capture.STOCK_CALL_GAP_SECONDS)
            self.chain_status.update({"stock_chain_rows": stored, "stock_chain_failed": failed,
                                      "stock_chain_at": datetime.now(UTC).isoformat()})
            await asyncio.sleep(max(1.0, minutes * 60 - (time.monotonic() - started)))

    async def _stock_chain(self, name: str, spot: str) -> int:
        stamp = whale.snapshot_stamp()
        # The sweep is the lowest class in the hub's budget (when routed through it): marks and index chains first.
        chain = await self.broker.option_chain(spot, strikecount=oi_capture.STOCK_STRIKES, priority="bulk")
        chains = [chain]
        token = oi_capture.next_expiry_token(chain, chain.expiry, oi_capture.NEXT_SERIES_WITHIN_DAYS,
                                             datetime.now(IST).date())
        if token:
            await asyncio.sleep(oi_capture.STOCK_CALL_GAP_SECONDS)
            chains.append(await self.broker.option_chain(spot, expiry_token=token,
                                                         strikecount=oi_capture.STOCK_STRIKES, priority="bulk"))
        for one in chains:
            await asyncio.to_thread(self._store_chain, stamp, name, one)
        return sum(len(one.entries) for one in chains)

    def _whale_roots(self) -> dict[str, str | None]:
        """Chain root -> the desk future for it, for the roots the tracker covers."""
        roots: dict[str, str | None] = {}
        for spot in self.mp_spot_symbols():
            root = desk_underlying(spot)
            if root not in ("NIFTY", "BANKNIFTY"):
                continue
            roots.setdefault(root, None)
            if is_futures_symbol(spot):
                roots[root] = spot
        return roots

    def _store_chain(self, stamp: int, root: str, chain) -> None:
        import sqlite3
        # timeout=30 IS the busy timeout; a PRAGMA busy_timeout=5000 after it
        # only lowers it, which cost a whole 100-strike snapshot at 12:06 IST on
        # 4 Sep (a 140s gap in an otherwise clean 63s cadence) when the nightly
        # and dispersion writers held historical.sqlite3 past five seconds.
        # DELETE journaling gives no reader concurrency, so waiting is the only
        # option -- and this runs in a worker thread, so it costs a thread and
        # not the tick loop.
        connection = sqlite3.connect(self.settings.research_database_path, timeout=30)
        try:
            whale.save_chain(connection, stamp, root, chain.expiry, chain.spot_price, chain.entries,
                             fp=getattr(chain, "fp", 0.0), vix=getattr(chain, "vix", None))
        finally:
            connection.close()

    def _whale_minute(self, stamp: int, futures: dict[str, str], flow_states: dict,
                      quotes: dict) -> tuple[dict, str | None]:
        """Layers B-E for one minute: sample the futures leg, re-run Layer A over
        the trailing slice, evaluate each root's window. Returns the payload and
        the Layer A problem if there was one — a tick archive that cannot be
        opened must not cost the chain evaluation."""
        import sqlite3
        history = sqlite3.connect(self.settings.research_database_path, timeout=30)
        try:
            whale.ensure_schema(history)
            day = whale.day_of(stamp)
            self.whale_live.sample(history, stamp, flow_states, quotes)
            problem = None
            if self.settings.tick_capture_enabled:
                try:
                    ticks = sqlite3.connect(f"file:{self.settings.tick_database_path}?mode=ro",
                                            uri=True, timeout=30)
                    try:
                        gaps = whale.feed_gaps(ticks, day)
                        for symbol in futures.values():
                            whale.save_events(history, day,
                                              whale.detect_recent(ticks, symbol, day, stamp * 1000, gaps))
                        # The name list: every contract that traded in the
                        # trailing slice, every few minutes (the nightly pass
                        # redoes the whole day).
                        if (stamp // 60) % whale.NAMES_SCAN_EVERY_MINUTES == 0:
                            from . import whale_tuning
                            since = stamp * 1000 - whale.A_LIVE_LOOKBACK_MS
                            universe = whale.traded_symbols(ticks, day, since)
                            tuned = whale_tuning.load_params(history)
                            scanned = [event for event in whale.scan(
                                ticks, day, universe, since_ms=since, lots=dict(self.mp.lot_sizes), gaps=gaps,
                                params={s: p for s, p in tuned.items() if not p.get("disabled")})
                                if not tuned.get(event.symbol, {}).get("disabled")]
                            whale.save_events(history, day, scanned)
                            whale.record_entries(history, day)
                            whale_tuning.save_features(history, day, scanned)
                            whale_tuning.label_outcomes(history, day)
                            self.chain_status["names_scanned"] = len(universe)
                    finally:
                        ticks.close()
                except sqlite3.Error as exc:
                    problem = f"layer A: {exc}"
            out: dict[str, dict] = {}
            for root, fut_symbol in futures.items():
                out[root] = whale.evaluate_window(
                    history, root, fut_symbol, stamp, flow_signs=self._option_flow_signs(root),
                    eod_score=whale.prior_eod_score(history, root, day))
                if out[root].get("alert_id"):
                    self.whale_alert_queue.append(out[root])
            self.chain_status.update({
                "windows": self.chain_status["windows"] + sum(
                    1 for row in out.values() if row.get("status") == "ok"),
                "alerts_today": history.execute(
                    "SELECT COUNT(*) FROM whale_alerts WHERE day = ?", (day,)).fetchone()[0],
                "history_days": max((row.get("history", {}).get("days", 0) for row in out.values()),
                                    default=0),
                "composite": {root: row.get("composite_decayed") for root, row in out.items()},
            })
            return out, problem
        finally:
            history.close()

    def _option_flow_signs(self, root: str) -> dict[str, int]:
        """Sign of the classified delta over the window for the option legs the
        desk actually streams. Every other strike falls back to the premium
        proxy inside strike_windows, and says so."""
        cutoff = time.time() - whale.WINDOW_SECONDS
        signs: dict[str, int] = {}
        for contract in self.contract_selector.contracts.values():
            if getattr(contract, "underlying", None) != root:
                continue
            state = self.mp.flow.states.get(contract.symbol)
            if state is None:
                continue
            delta = sum(row.size * row.side for row in list(state.recent) if row.timestamp >= cutoff)
            if delta:
                signs[contract.symbol] = 1 if delta > 0 else -1
        return signs
