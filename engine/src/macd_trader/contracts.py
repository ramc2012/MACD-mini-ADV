from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .brokers import Broker, OptionChainEntry
from .rollover import Expiry, first_tradable, is_stock_spot, is_tradable
from .universe import DISPLAY_BY_SPOT, INDEX_SPOTS, STOCK_SPOTS

IST = ZoneInfo("Asia/Kolkata")

# Index spots also stream a ladder in the NEXT listed series. Near a weekly
# expiry the front series expires inside a 1-3 session hold, and the option
# agent (which never holds across expiry) then had no index contract to trade:
# SENSEX had none on most September days. These rows are analysis-only and
# carry "NEXT-" moneyness, so no strategy, desk map, ratio ladder or
# dispersion cohort (all of which read ITM/ATM/OTM) can pick them up.
NEXT_SERIES_SPOTS = frozenset(INDEX_SPOTS.values())
NEXT_PREFIX = "NEXT-"


@dataclass(slots=True)
class OptionContract:
    underlying: str
    spot_symbol: str
    option_type: str
    symbol: str
    strike: float
    expiry: str
    selection_price: float
    volume: int = 0
    oi: int = 0
    retained: bool = False
    lot_size: int = 0
    moneyness: str = "ATM"
    analysis_only: bool = False
    selected_on: str = ""            # IST day its ladder was chosen; "" for a holding restored from elsewhere


def contract_from_row(row: dict) -> OptionContract:
    """Load both legacy ATM-only snapshots and the current six-leg ladder."""
    return OptionContract(**{
        **row,
        "lot_size": int(row.get("lot_size") or 0),
        "moneyness": str(row.get("moneyness") or "ATM").upper(),
        "analysis_only": bool(row.get("analysis_only", False)),
    })


def contract_is_live(expiry: str | None, today=None) -> bool:
    """Has this contract's expiry not yet passed?

    The saved-selection fast path reused whatever was in atm_contracts.json
    whenever every requested spot was covered, without ever asking whether the
    contracts were still tradable. A weekly index option picked on Monday
    stayed selected after it expired: on 21 Aug the NIFTY slots still held the
    18-Aug pair and SENSEX the 20-Aug pair, so those underlyings had no live
    ATM CE/PE at all. An unparseable expiry is treated as dead rather than
    trusted -- refetching a chain is cheap, trading a dead contract is not.

    This is the question to ask about a contract already HELD -- an open
    position is never rolled into a different instrument. For choosing what to
    select next, ask :func:`macd_trader.rollover.is_tradable` instead, which
    also refuses a contract that expires today.
    """
    return is_tradable(expiry, min_days=0, today=today)


def liquidity_window(
    entries: list[OptionChainEntry], spot: float, option_type: str,
) -> tuple[OptionChainEntry | None, list[OptionChainEntry]]:
    """(side-rounded anchor strike, the anchor and two strikes either side of it).

    These are the only strikes whose liquidity the ATM rule reads, so a
    selector without a chain needs quotes for just these.
    """
    side = sorted((e for e in entries if e.option_type == option_type and e.symbol), key=lambda e: e.strike)
    if not side or spot <= 0:
        return None, []
    anchored = [e for e in side if e.strike >= spot] if option_type == "CE" else [e for e in side if e.strike <= spot]
    anchor = (anchored[0] if option_type == "CE" else anchored[-1]) if anchored else min(side, key=lambda e: abs(e.strike - spot))
    index = side.index(anchor)
    return anchor, side[max(0, index - 2): index + 3]


PREFER_STREAMED_SHARE = 2 / 3    # a streamed strike within this share of the best liquidity keeps the ATM slot


def select_liquid_contract(entries: list[OptionChainEntry], spot: float, option_type: str,
                           prefer: set[str] | frozenset[str] = frozenset()) -> OptionChainEntry | None:
    """Use the original app's side-rounded ATM plus 1.5x liquidity rule.

    ``prefer`` (an intraday refresh: the contracts already on the feed) keeps a strike that already has a
    premium history when its liquidity is comparable, so the momentum series is not restarted for nothing.
    """
    anchor, nearby = liquidity_window(entries, spot, option_type)
    if anchor is None:
        return None
    liquidity = lambda e: max(e.volume, e.oi / 100)
    if liquidity(anchor) <= 0:
        choice = max(nearby, key=liquidity)
    else:
        better = [e for e in nearby if liquidity(e) >= liquidity(anchor) * 1.5]
        choice = max(better, key=liquidity) if better else anchor
    if prefer and choice.symbol not in prefer:
        known = [e for e in nearby if e.symbol in prefer and liquidity(e) > 0
                 and liquidity(e) >= liquidity(choice) * PREFER_STREAMED_SHARE]
        if known:
            choice = min(known, key=lambda e: (abs(e.strike - spot), -liquidity(e)))
    return choice


def select_contract_ladder(
    entries: list[OptionChainEntry], spot: float, option_type: str,
    prefer: set[str] | frozenset[str] = frozenset(),
) -> list[tuple[str, OptionChainEntry]]:
    """Return one ITM, ATM and OTM contract around the selected ATM strike.

    The ATM leg intentionally keeps the desk's established side-rounding and
    liquidity rule.  Adjacent listed strikes then define moneyness: lower is
    ITM for calls / OTM for puts, and higher is OTM for calls / ITM for puts.
    """
    side = sorted(
        (entry for entry in entries if entry.option_type == option_type and entry.symbol),
        key=lambda entry: entry.strike,
    )
    atm = select_liquid_contract(entries, spot, option_type, prefer)
    if atm is None:
        return []
    index = next((idx for idx, entry in enumerate(side) if entry.symbol == atm.symbol), -1)
    if index < 0:
        return []
    indices = (
        {"ITM": index - 1, "ATM": index, "OTM": index + 1}
        if option_type == "CE"
        else {"ITM": index + 1, "ATM": index, "OTM": index - 1}
    )
    return [
        (moneyness, side[row_index])
        for moneyness, row_index in indices.items()
        if 0 <= row_index < len(side)
    ]


class ContractSelector:
    def __init__(self, broker: Broker, snapshot_path: str, min_days_to_expiry: int = 1):
        self.broker = broker
        self.path = Path(snapshot_path)
        # How close to expiry a contract may be and still be selected. 1 skips
        # the expiry day itself; 0 restores the old "any unexpired contract".
        self.min_days_to_expiry = max(0, int(min_days_to_expiry))
        self.contracts: dict[str, OptionContract] = {}
        self.errors: dict[str, str] = {}
        self.new_symbols: set[str] = set()
        self.rolled: dict[str, str] = {}
        self.next_series_errors: dict[str, str] = {}
        # Which path settled each spot on the last build, and why any fell back.
        self.master_errors: dict[str, str] = {}
        self.selection_source: dict = {}
        # The day this selection was made. Saving the file (a retained holding, an intraday refresh) keeps it, so a
        # pre-open tick on a held contract can no longer re-date yesterday's ladders as today's (28 Oct 2026: the
        # whole library had been carried since 26 Oct, centred on stale prices, with the index weeklies expired).
        self.selection_date: str | None = None
        self.refreshes: list[dict] = []

    def retained_contracts(self, symbols: set[str]) -> list[OptionContract]:
        """Restore open contracts even when their selection day has passed."""
        if not symbols:
            return []
        try:
            payload = json.loads(self.path.read_text())
            rows = payload.get("contracts") or []
        except (OSError, ValueError, TypeError):
            return []
        retained: list[OptionContract] = []
        for row in rows:
            if row.get("symbol") not in symbols:
                continue
            contract = contract_from_row(row)
            contract.retained = True
            retained.append(contract)
        return retained

    async def restore_held_contracts(self, positions: dict[str, int]) -> list[OptionContract]:
        """Restore every open contract, including rows lost from an old snapshot."""
        restored = self.retained_contracts(set(positions))
        restored_symbols = {row.symbol for row in restored}
        missing = set(positions) - restored_symbols
        if not missing:
            return restored
        details = await self.broker.option_contract_metadata(sorted(missing))
        for symbol in sorted(missing):
            row = details.get(symbol)
            if row is None:
                self.errors[symbol] = "held contract is absent from the Fyers instrument master"
                continue
            spot_symbol = INDEX_SPOTS.get(row.underlying) or STOCK_SPOTS.get(row.underlying)
            if not spot_symbol:
                exchange = symbol.split(":", 1)[0]
                spot_symbol = f"{exchange}:{row.underlying}-EQ"
            restored.append(OptionContract(
                underlying=row.underlying,
                spot_symbol=spot_symbol,
                option_type=row.option_type,
                symbol=row.symbol,
                strike=row.strike,
                expiry=row.expiry,
                selection_price=0.0,
                retained=True,
                lot_size=int(positions.get(symbol) or row.lot_size),
            ))
        return restored

    async def _roll_chain(self, spot_symbol: str, chain, semaphore) -> object:
        """Advance to the next series when the front one expires too soon.

        Fyers always answers options-chain-v3 with the FRONT expiry unless a
        specific one is asked for, so on an expiry day every ATM selection was
        a contract with hours left: premium collapsing to intrinsic, MACD
        computed on that collapse, and nothing to hold overnight. Re-request
        the nearest expiry that clears the threshold instead. The extra call
        happens only on the days it is needed.
        """
        stock = is_stock_spot(spot_symbol)          # stock options leave a series on its T-2 (rollover.py)
        if is_tradable(chain.expiry, self.min_days_to_expiry, stock=stock):
            return chain
        target = first_tradable(list(chain.expiries or []), self.min_days_to_expiry, stock=stock)
        if target is None or not target.token:
            return chain
        rolled = await self.broker.option_chain(spot_symbol, target.token)
        await asyncio.sleep(0.35)
        if not rolled.entries:
            # An empty next series is worse than a short-dated one; keep what
            # the exchange actually quoted and let the caller reject it.
            return chain
        self.rolled[spot_symbol] = f"{chain.expiry} -> {rolled.expiry}"
        return rolled

    async def build(
        self, spot_symbols: list[str], retained: list[OptionContract] | None = None,
    ) -> list[OptionContract]:
        retained_by_symbol = {row.symbol: row for row in (retained or [])}
        saved_payload = self._load_today()
        saved_contracts = saved_payload.get("contracts") or []
        self.refreshes = list(saved_payload.get("refreshes") or [])
        selected = [
            contract_from_row(row)
            for row in saved_contracts
            if row.get("spot_symbol") in spot_symbols
            and is_tradable(row.get("expiry"), self.min_days_to_expiry, stock=is_stock_spot(str(row.get("spot_symbol") or "")))
        ]
        previously_selected = {row.symbol for row in selected}
        self.selection_date = datetime.now(IST).date().isoformat()
        # Each spot's OWN ladder: pooling every spot's roles let a spot whose front legs had expired (only its
        # NEXT- series left) pass as covered and never be selected again.
        ladder_roles: dict[str, set] = {}
        today = self.selection_date
        for row in selected:
            # A leg chosen today still fills its role once a position in it makes it "retained".
            if not row.retained or row.selected_on == today:
                ladder_roles.setdefault(row.spot_symbol, set()).add((row.option_type, row.moneyness))
        required_roles = {(side, role) for side in ("CE", "PE") for role in ("ITM", "ATM", "OTM")}
        next_roles = {(side, NEXT_PREFIX + "ATM") for side in ("CE", "PE")}
        covered = {
            spot for spot, roles in ladder_roles.items()
            if required_roles.issubset(roles) and (spot not in NEXT_SERIES_SPOTS or next_roles.issubset(roles))
        }
        missing_spots = [symbol for symbol in spot_symbols if symbol not in covered]
        if selected and not missing_spots:
            selected.extend(row for symbol, row in retained_by_symbol.items() if symbol not in {item.symbol for item in selected})
            await self._resolve_lot_sizes(selected)
            self.contracts = {row.symbol: row for row in selected}
            self.new_symbols = set()
            self._save(selected)
            return selected
        # A legacy same-day ATM-only row makes this spot "missing" for the new
        # ladder. Drop that stale selection before appending its six fresh
        # rows, otherwise the snapshot accumulates duplicate ATM records.
        selected = [row for row in selected if row.spot_symbol in covered]
        # A spot being selected again has no usable ladder, so its saved reference is no better than its old legs:
        # take a fresh quote. Covered spots keep theirs, so a same-day restart does not move their ladders.
        saved = {spot: row for spot, row in (saved_payload.get("spots") or {}).items() if spot in covered}
        semaphore = asyncio.Semaphore(1)

        async def one(spot_symbol: str) -> list[OptionContract]:
            try:
                chain = None
                preopen = None
                for attempt in range(5):
                    try:
                        async with semaphore:
                            chain = await self.broker.option_chain(spot_symbol)
                            await asyncio.sleep(0.35)
                            chain = await self._roll_chain(spot_symbol, chain, semaphore)
                            preopen = None if saved.get(spot_symbol) else await self.broker.preopen_price(spot_symbol)
                            await asyncio.sleep(0.35)
                        break
                    except Exception as exc:
                        if "429" not in str(exc) or attempt == 4:
                            raise
                        await asyncio.sleep(2 * (attempt + 1))
                assert chain is not None
                if not is_tradable(chain.expiry, self.min_days_to_expiry, stock=is_stock_spot(spot_symbol)):
                    raise RuntimeError(
                        f"no expiry at least {self.min_days_to_expiry} day(s) out "
                        f"(nearest listed: {chain.expiry or 'unknown'})"
                    )
                reference = float(saved.get(spot_symbol, {}).get("selection_price") or preopen or chain.spot_price)
                rows: list[OptionContract] = []
                for side in ("CE", "PE"):
                    for moneyness, pick in select_contract_ladder(chain.entries, reference, side):
                        rows.append(OptionContract(
                            DISPLAY_BY_SPOT.get(spot_symbol, spot_symbol), spot_symbol, side,
                            pick.symbol, pick.strike, chain.expiry, reference, pick.volume, pick.oi,
                            moneyness=moneyness, analysis_only=moneyness != "ATM",
                        ))
                roles = {(row.option_type, row.moneyness) for row in rows}
                if required_roles.issubset(roles):
                    if spot_symbol in NEXT_SERIES_SPOTS:
                        rows.extend(await self._next_series(spot_symbol, chain, reference, semaphore))
                    return rows
                raise RuntimeError("option chain did not provide a complete CE/PE ITM-ATM-OTM ladder")
            except Exception as exc:
                self.errors[spot_symbol] = str(exc)
                return []

        started = time.monotonic()
        local = await self._select_from_master(missing_spots, saved)
        chain_spots = [symbol for symbol in missing_spots if symbol not in local]
        groups = [*local.values(), *await asyncio.gather(*(one(symbol) for symbol in chain_spots))]
        self.selection_source = {
            "at": datetime.now(IST).isoformat(timespec="seconds"),
            "master": len(local), "chain": len(chain_spots),
            "seconds": round(time.monotonic() - started, 1),
            "fallbacks": dict(self.master_errors),
        }
        for group in groups:
            for row in group:
                row.selected_on = self.selection_date
        selected.extend(row for group in groups for row in group)
        selected.extend(row for symbol, row in retained_by_symbol.items() if symbol not in {item.symbol for item in selected})
        # Persist the expensive chain selection before the separate instrument
        # master lookup so a temporary master rate limit does not discard it.
        self._save(selected)
        await self._resolve_lot_sizes(selected)
        self.contracts = {row.symbol: row for row in selected}
        self.new_symbols = {
            row.symbol for row in selected
            if row.symbol not in previously_selected and row.symbol not in retained_by_symbol
        }
        self._save(selected)
        return selected

    async def _select_from_master(self, spot_symbols: list[str], saved: dict,
                                  prefer: set[str] | frozenset[str] = frozenset()) -> dict[str, list[OptionContract]]:
        """The same ladders as the chain path, from the instrument master and two quote sweeps.

        The chain path spent two to four REST calls per underlying -- a chain,
        a roll chain near expiry, a pre-open history request and a NEXT-series
        chain for indices -- about 430 paced calls, five minutes every morning.
        Everything it read is available more cheaply: the strikes and expiries
        from the day's master file, the reference from one batched spot quote,
        and liquidity only for the five strikes per side the ATM rule reads
        (batched quotes, previous-session volume before the open, as the chain
        reported). The rules are unchanged: side-rounded ATM, the 1.5x
        liquidity tie-break, adjacent ITM/OTM, the expiry roll and the NEXT
        series. Any spot this cannot settle returns to the chain path.
        """
        self.master_errors = {}
        loader = getattr(self.broker, "option_master", None)
        if loader is None or not spot_symbols:
            return {}
        exchanges = sorted({symbol.split(":", 1)[0] for symbol in spot_symbols if ":" in symbol})
        try:
            listed = await loader(exchanges)
        except Exception as exc:  # noqa: BLE001 - the chain path is the fallback
            self.master_errors["*"] = f"instrument master: {exc}"[:200]
            return {}
        if not listed:
            return {}
        series: dict[tuple[str, str], dict[str, list[OptionChainEntry]]] = {}
        lots: dict[str, int] = {}
        for row in listed:
            key = (row.symbol.split(":", 1)[0], row.underlying)
            series.setdefault(key, {}).setdefault(row.expiry, []).append(
                OptionChainEntry(row.strike, row.option_type, row.symbol))
            lots[row.symbol] = row.lot_size
        references = {symbol: float(saved.get(symbol, {}).get("selection_price") or 0) for symbol in spot_symbols}
        unpriced = [symbol for symbol, price in references.items() if price <= 0]
        if unpriced:
            try:
                marks = await self.broker.quotes(unpriced)
            except Exception as exc:  # noqa: BLE001
                self.master_errors["*"] = f"spot quotes: {exc}"[:200]
                return {}
            for symbol in unpriced:
                tick = marks.get(symbol)
                if tick is not None and tick.ltp > 0:
                    references[symbol] = float(tick.ltp)
        plans: dict[str, tuple] = {}
        window: dict[str, OptionChainEntry] = {}
        for spot_symbol in spot_symbols:
            root = DISPLAY_BY_SPOT.get(spot_symbol)
            listed_series = series.get((spot_symbol.split(":", 1)[0], root)) if root else None
            reference = references.get(spot_symbol, 0.0)
            if not listed_series:
                self.master_errors[spot_symbol] = "not in the instrument master"
                continue
            if reference <= 0:
                self.master_errors[spot_symbol] = "no reference price"
                continue
            expiries = sorted(listed_series)
            chosen = first_tradable([Expiry(day) for day in expiries], self.min_days_to_expiry,
                                    stock=is_stock_spot(spot_symbol))
            if chosen is None:
                self.master_errors[spot_symbol] = f"no expiry at least {self.min_days_to_expiry} day(s) out"
                continue
            front = chosen.date
            later = [day for day in expiries if day > front]
            following = later[0] if spot_symbol in NEXT_SERIES_SPOTS and later else None
            plans[spot_symbol] = (reference, expiries[0], front, following)
            for day in filter(None, (front, following)):
                for side in ("CE", "PE"):
                    window.update((entry.symbol, entry) for entry in
                                  liquidity_window(listed_series[day], reference, side)[1])
        if not plans:
            return {}
        try:
            marks = await self.broker.quotes(sorted(window))
        except Exception as exc:  # noqa: BLE001
            self.master_errors["*"] = f"strike quotes: {exc}"[:200]
            return {}
        # A sweep that came back mostly empty would silently turn the
        # liquidity tie-break into "lowest strike in the window"; the chain is
        # the better answer then.
        if len(marks) < 0.5 * len(window):
            self.master_errors["*"] = f"strike quotes covered {len(marks)} of {len(window)} strikes"
            return {}
        for symbol, entry in window.items():
            tick = marks.get(symbol)
            if tick is not None:
                entry.ltp = float(tick.ltp or 0)
                entry.volume = int(tick.volume or 0)
                entry.oi = int(tick.open_interest or 0)
        required = {(side, role) for side in ("CE", "PE") for role in ("ITM", "ATM", "OTM")}
        result: dict[str, list[OptionContract]] = {}
        for spot_symbol, (reference, nearest, front, following) in plans.items():
            listed_series = series[(spot_symbol.split(":", 1)[0], DISPLAY_BY_SPOT[spot_symbol])]
            name = DISPLAY_BY_SPOT.get(spot_symbol, spot_symbol)
            rows: list[OptionContract] = []
            for day, prefix in ((front, ""), (following, NEXT_PREFIX)):
                if day is None:
                    continue
                for side in ("CE", "PE"):
                    for moneyness, pick in select_contract_ladder(listed_series[day], reference, side, prefer):
                        rows.append(OptionContract(
                            name, spot_symbol, side, pick.symbol, pick.strike, day, reference,
                            pick.volume, pick.oi, lot_size=lots.get(pick.symbol, 0),
                            moneyness=prefix + moneyness, analysis_only=bool(prefix) or moneyness != "ATM",
                        ))
            if not required.issubset({(row.option_type, row.moneyness) for row in rows}):
                self.master_errors[spot_symbol] = "master did not give a complete CE/PE ITM-ATM-OTM ladder"
                continue
            if front != nearest:
                self.rolled[spot_symbol] = f"{nearest} -> {front}"
            if spot_symbol in NEXT_SERIES_SPOTS:
                if following:
                    self.next_series_errors.pop(spot_symbol, None)
                else:
                    self.next_series_errors[spot_symbol] = f"no series listed after {front}"
            self.errors.pop(spot_symbol, None)
            result[spot_symbol] = rows
        return result

    async def _next_series(self, spot_symbol: str, chain, reference: float, semaphore) -> list[OptionContract]:
        """The same ITM/ATM/OTM ladder one series further out, analysis-only.

        A failure here is recorded, never raised: the front ladder is what
        the strategies need, and it must not be lost to an optional extra.
        """
        later = sorted((row for row in (chain.expiries or []) if row.token and str(row.date) > str(chain.expiry)),
                       key=lambda row: str(row.date))
        if not later:
            self.next_series_errors[spot_symbol] = f"no series listed after {chain.expiry}"
            return []
        try:
            async with semaphore:
                nxt = await self.broker.option_chain(spot_symbol, later[0].token)
                await asyncio.sleep(0.35)
        except Exception as exc:  # noqa: BLE001 - optional
            self.next_series_errors[spot_symbol] = str(exc)[:200]
            return []
        rows = []
        for side in ("CE", "PE"):
            for moneyness, pick in select_contract_ladder(nxt.entries, reference, side):
                rows.append(OptionContract(
                    DISPLAY_BY_SPOT.get(spot_symbol, spot_symbol), spot_symbol, side,
                    pick.symbol, pick.strike, nxt.expiry, reference, pick.volume, pick.oi,
                    moneyness=NEXT_PREFIX + moneyness, analysis_only=True,
                ))
        self.next_series_errors.pop(spot_symbol, None)
        return rows

    async def _resolve_lot_sizes(self, rows: list[OptionContract]) -> None:
        resolver = getattr(self.broker, "lot_sizes", None)
        sizes = await resolver([row.symbol for row in rows]) if resolver else {}
        for row in rows:
            row.lot_size = int(sizes.get(row.symbol) or row.lot_size or 0)
            if row.lot_size < 1:
                self.errors[row.symbol] = "Fyers instrument master did not provide a valid lot size; trading is blocked"

    def retain(self, symbol: str) -> None:
        # Called on every tick of a held contract: write only when the flag changes.
        row = self.contracts.get(symbol)
        if row is not None and not row.retained:
            row.retained = True
            self._save(list(self.contracts.values()))

    async def refresh(self, spot_prices: dict[str, float], last_trade: dict[str, datetime], now: datetime,
                      inactive_seconds: int = 900, max_steps: float = 2.0) -> dict[str, dict]:
        """Re-select a spot's ladder when its ATM contract is not trading or spot has left it (owner, 28 Oct 2026).

        A spot qualifies when either ATM leg has had no traded volume for ``inactive_seconds`` or the live spot is
        ``max_steps`` strikes or more from the ATM strike. Its ladder is then chosen again exactly as the morning
        one is (instrument master, live quotes for the strikes around the CURRENT spot, side-rounded ATM with the
        1.5x liquidity tie-break), and replaces the old ladder only if an ATM leg changes. Held contracts stay.
        Returns {spot: {"reasons": [...], "from": {...}, "to": {...}}} for every replaced ladder.
        """
        ladders: dict[str, dict[str, dict[str, OptionContract]]] = {}
        # Today's ladder legs; a held leg counts too (it is replaced for new entries, and stays subscribed).
        for row in sorted(self.contracts.values(), key=lambda r: not r.retained):
            if row.moneyness in ("ITM", "ATM", "OTM") and (not row.retained or row.selected_on == self.selection_date):
                ladders.setdefault(row.spot_symbol, {}).setdefault(row.option_type, {})[row.moneyness] = row
        due: dict[str, list[str]] = {}
        for spot, sides in ladders.items():
            price = spot_prices.get(spot) or 0.0
            if price <= 0:
                continue
            reasons = []
            for side, legs in sides.items():
                atm = legs.get("ATM")
                if atm is None:
                    continue
                traded = last_trade.get(atm.symbol)
                if traded is None or (now - traded).total_seconds() >= inactive_seconds:
                    idle = "no trade today" if traded is None else f"no trade for {int((now - traded).total_seconds() // 60)} min"
                    reasons.append(f"{side} ATM {atm.strike:g} {idle}")
                steps = [abs(atm.strike - leg.strike) for key, leg in legs.items() if key != "ATM" and leg.strike != atm.strike]
                step = min(steps) if steps else 0.0
                if step > 0 and abs(price - atm.strike) >= max_steps * step:
                    reasons.append(f"{side} ATM {atm.strike:g} is {abs(price - atm.strike) / step:.1f} strikes from spot {price:g}")
            if reasons:
                due[spot] = reasons
        if not due:
            return {}
        fresh = await self._select_from_master(sorted(due), {spot: {"selection_price": spot_prices[spot]} for spot in due},
                                               prefer=set(self.contracts))
        changed: dict[str, dict] = {}
        for spot, rows in fresh.items():
            old_atm = {side: legs["ATM"].symbol for side, legs in ladders[spot].items() if "ATM" in legs}
            new_atm = {row.option_type: row.symbol for row in rows if row.moneyness == "ATM"}
            if new_atm and new_atm != old_atm:
                changed[spot] = {"reasons": due[spot], "from": old_atm, "to": new_atm, "rows": rows}
        if not changed:
            return {}
        # The replaced ladder stays on the feed until the close, as context only (no new entries): its premium
        # history carries on, so the strike is ready again if spot comes back. Held legs stay as they are.
        new_symbols = {row.symbol for item in changed.values() for row in item["rows"]}
        kept = []
        for row in self.contracts.values():
            if row.spot_symbol in changed and not row.retained and row.symbol not in new_symbols:
                if not row.moneyness.startswith(("NEXT-", "PREV-")):
                    row.moneyness = "PREV-" + row.moneyness
                row.analysis_only = True
            if row.symbol not in new_symbols:
                kept.append(row)
        kept_symbols = {row.symbol for row in kept}
        added = [row for item in changed.values() for row in item["rows"] if row.symbol not in kept_symbols]
        for row in added:
            row.selected_on = self.selection_date or now.astimezone(IST).date().isoformat()
        self.contracts = {row.symbol: row for row in [*kept, *added]}
        self.new_symbols = {row.symbol for row in added}
        at = now.astimezone(IST).isoformat(timespec="seconds")
        self.refreshes = [*self.refreshes, *({"at": at, "spot": spot, "reasons": item["reasons"],
                                               "from": item["from"], "to": item["to"]} for spot, item in changed.items())][-200:]
        self._save(list(self.contracts.values()))
        return {spot: {k: v for k, v in item.items() if k != "rows"} for spot, item in changed.items()}

    def _load_today(self) -> dict:
        try:
            payload = json.loads(self.path.read_text())
            return payload if payload.get("date") == datetime.now(IST).date().isoformat() else {}
        except (OSError, ValueError):
            return {}

    def _save(self, rows: list[OptionContract]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "date": self.selection_date or datetime.now(IST).date().isoformat(),
            "selected_at": datetime.now(IST).isoformat(),
            # Today's ladder rows set each spot's reference. A held contract
            # carried from an earlier day keeps ITS selection price, and
            # letting it win here (it is appended last) made a same-day
            # restart re-centre that spot's ladder on a stale price: 26 of
            # 1 Oct's 428 ATM legs would have moved.
            "spots": {
                row.spot_symbol: {"selection_price": row.selection_price}
                for row in sorted(rows, key=lambda row: not row.retained)
            },
            "contracts": [asdict(row) for row in rows],
            "refreshes": self.refreshes,
        }
        self.path.write_text(json.dumps(payload, indent=2))
        self.path.chmod(0o600)
