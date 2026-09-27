from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .brokers import Broker, OptionChainEntry
from .rollover import first_tradable, is_tradable
from .universe import DISPLAY_BY_SPOT, INDEX_SPOTS, STOCK_SPOTS

IST = ZoneInfo("Asia/Kolkata")


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


def select_liquid_contract(entries: list[OptionChainEntry], spot: float, option_type: str) -> OptionChainEntry | None:
    """Use the original app's side-rounded ATM plus 1.5x liquidity rule."""
    side = sorted((e for e in entries if e.option_type == option_type and e.symbol), key=lambda e: e.strike)
    if not side or spot <= 0:
        return None
    anchored = [e for e in side if e.strike >= spot] if option_type == "CE" else [e for e in side if e.strike <= spot]
    anchor = (anchored[0] if option_type == "CE" else anchored[-1]) if anchored else min(side, key=lambda e: abs(e.strike - spot))
    index = side.index(anchor)
    nearby = side[max(0, index - 2): index + 3]
    liquidity = lambda e: max(e.volume, e.oi / 100)
    if liquidity(anchor) <= 0:
        return max(nearby, key=liquidity)
    better = [e for e in nearby if liquidity(e) >= liquidity(anchor) * 1.5]
    return max(better, key=liquidity) if better else anchor


def select_contract_ladder(
    entries: list[OptionChainEntry], spot: float, option_type: str,
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
    atm = select_liquid_contract(entries, spot, option_type)
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
        if is_tradable(chain.expiry, self.min_days_to_expiry):
            return chain
        target = first_tradable(list(chain.expiries or []), self.min_days_to_expiry)
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
        selected = [
            contract_from_row(row)
            for row in saved_contracts
            if row.get("spot_symbol") in spot_symbols
            and is_tradable(row.get("expiry"), self.min_days_to_expiry)
        ]
        previously_selected = {row.symbol for row in selected}
        ladder_roles = {
            row.spot_symbol: {
                (row.option_type, row.moneyness)
                for row in selected
                if not row.retained
            }
            for row in selected
        }
        required_roles = {(side, role) for side in ("CE", "PE") for role in ("ITM", "ATM", "OTM")}
        covered = {spot for spot, roles in ladder_roles.items() if required_roles.issubset(roles)}
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
        saved = saved_payload.get("spots") or {}
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
                if not is_tradable(chain.expiry, self.min_days_to_expiry):
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
                    return rows
                raise RuntimeError("option chain did not provide a complete CE/PE ITM-ATM-OTM ladder")
            except Exception as exc:
                self.errors[spot_symbol] = str(exc)
                return []

        groups = await asyncio.gather(*(one(symbol) for symbol in missing_spots))
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

    async def _resolve_lot_sizes(self, rows: list[OptionContract]) -> None:
        resolver = getattr(self.broker, "lot_sizes", None)
        sizes = await resolver([row.symbol for row in rows]) if resolver else {}
        for row in rows:
            row.lot_size = int(sizes.get(row.symbol) or row.lot_size or 0)
            if row.lot_size < 1:
                self.errors[row.symbol] = "Fyers instrument master did not provide a valid lot size; trading is blocked"

    def retain(self, symbol: str) -> None:
        if symbol in self.contracts:
            self.contracts[symbol].retained = True
            self._save(list(self.contracts.values()))

    def _load_today(self) -> dict:
        try:
            payload = json.loads(self.path.read_text())
            return payload if payload.get("date") == datetime.now(IST).date().isoformat() else {}
        except (OSError, ValueError):
            return {}

    def _save(self, rows: list[OptionContract]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "date": datetime.now(IST).date().isoformat(),
            "selected_at": datetime.now(IST).isoformat(),
            "spots": {row.spot_symbol: {"selection_price": row.selection_price} for row in rows},
            "contracts": [asdict(row) for row in rows],
        }
        self.path.write_text(json.dumps(payload, indent=2))
        self.path.chmod(0o600)
