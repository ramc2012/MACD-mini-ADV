from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from .brokers import FyersBroker
from .config import Settings, settings as environment_settings
from .indicators import IncrementalKAMA, IncrementalMACD, IncrementalROC, IncrementalRSI
from .chart_history import aggregate_session_candles
from .models import Candle
from .settings_store import RuntimeSettingsStore
from .universe import INDEX_SPOTS, SPOT_SYMBOLS

IST = ZoneInfo("Asia/Kolkata")

# Fyers accepts minute-history requests only for bounded calendar ranges.  Keep
# the ranges below that limit, while retaining enough lookback to capture the
# entire listed life of the current monthly/weekly option universe.
SPOT_HISTORY_DAYS = 365
OPTION_LIFETIME_LOOKBACK_DAYS = 180
FYERS_MINUTE_CHUNK_DAYS = 90


class HistoricalStore:
    def __init__(self, path: str):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path = target
        self.db = sqlite3.connect(target)
        self.db.row_factory = sqlite3.Row
        # DELETE journaling is slower than WAL but is reliable on Docker Desktop
        # bind mounts, where shared-memory WAL files can intermittently return EIO.
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS historical_candles (
          symbol TEXT NOT NULL, timeframe_seconds INTEGER NOT NULL, timestamp INTEGER NOT NULL,
          open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
          volume INTEGER NOT NULL, asset_type TEXT NOT NULL, expiry TEXT,
          downloaded_at TEXT NOT NULL,
          PRIMARY KEY(symbol, timeframe_seconds, timestamp)
        );
        CREATE INDEX IF NOT EXISTS idx_historical_asset_time ON historical_candles(asset_type, timestamp);
        CREATE TABLE IF NOT EXISTS historical_indicators (
          symbol TEXT NOT NULL, timeframe_seconds INTEGER NOT NULL, timestamp INTEGER NOT NULL,
          macd REAL NOT NULL, signal REAL NOT NULL, histogram REAL NOT NULL,
          bb_middle REAL, bb_upper REAL, bb_lower REAL, bb_width REAL, kama REAL,
          PRIMARY KEY(symbol, timeframe_seconds,timestamp)
        );
        CREATE TABLE IF NOT EXISTS download_log (
          symbol TEXT NOT NULL, asset_type TEXT NOT NULL, range_from TEXT NOT NULL, range_to TEXT NOT NULL,
          rows_saved INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT, completed_at TEXT NOT NULL,
          PRIMARY KEY(symbol, asset_type, range_from, range_to)
        );
        CREATE TABLE IF NOT EXISTS backtest_runs (
          run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, parameters TEXT NOT NULL, summary TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS backtest_trades (
          trade_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, symbol TEXT NOT NULL, underlying TEXT,
          option_type TEXT, fold TEXT NOT NULL, signal_time TEXT NOT NULL, entry_time TEXT NOT NULL,
          exit_time TEXT NOT NULL, entry_price REAL NOT NULL, exit_price REAL NOT NULL,
          quantity INTEGER NOT NULL, lots INTEGER NOT NULL DEFAULT 1, lot_size INTEGER NOT NULL DEFAULT 1,
          pnl REAL NOT NULL, return_pct REAL NOT NULL, exit_reason TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_backtest_run ON backtest_trades(run_id, entry_time);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(backtest_trades)")}
        if "lots" not in columns:
            self.db.execute("ALTER TABLE backtest_trades ADD COLUMN lots INTEGER NOT NULL DEFAULT 1")
        if "lot_size" not in columns:
            self.db.execute("ALTER TABLE backtest_trades ADD COLUMN lot_size INTEGER NOT NULL DEFAULT 1")
        self.db.commit()

    def save_candles(self, rows: list[Candle], asset_type: str, expiry: str | None = None) -> int:
        now = datetime.now(UTC).isoformat()
        with self.db:
            self.db.executemany(
                """INSERT OR REPLACE INTO historical_candles
                (symbol,timeframe_seconds,timestamp,open,high,low,close,volume,asset_type,expiry,downloaded_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [(r.symbol, 60, r.timestamp, r.open, r.high, r.low, r.close, r.volume, asset_type, expiry, now) for r in rows],
            )
        return len(rows)

    def log_download(self, symbol: str, asset_type: str, start: date, end: date, count: int, status: str, detail: str = "") -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO download_log VALUES(?,?,?,?,?,?,?,?)",
                (symbol, asset_type, start.isoformat(), end.isoformat(), count, status, detail[:1000], datetime.now(UTC).isoformat()),
            )

    def download_complete(self, symbol: str, asset_type: str, start: date, end: date) -> bool:
        row = self.db.execute(
            "SELECT status FROM download_log WHERE symbol=? AND asset_type=? AND range_from=? AND range_to=?",
            (symbol, asset_type, start.isoformat(), end.isoformat()),
        ).fetchone()
        return bool(row and row["status"] == "ok")

    def candles(self, symbol: str) -> list[Candle]:
        rows = self.db.execute(
            "SELECT * FROM historical_candles WHERE symbol=? AND timeframe_seconds=60 ORDER BY timestamp", (symbol,)
        ).fetchall()
        return [Candle(symbol, row["timestamp"], row["open"], row["high"], row["low"], row["close"], row["volume"], True) for row in rows]

    def candle_count(self, symbol: str) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM historical_candles WHERE symbol=?", (symbol,)).fetchone()[0])

    def save_run(self, run_id: str, parameters: dict, summary: dict, trades: list[dict]) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO backtest_runs VALUES(?,?,?,?)",
                (run_id, datetime.now(UTC).isoformat(), json.dumps(parameters), json.dumps(summary)),
            )
            self.db.executemany(
                """INSERT OR REPLACE INTO backtest_trades
                (trade_id,run_id,symbol,underlying,option_type,fold,signal_time,entry_time,exit_time,
                 entry_price,exit_price,quantity,lots,lot_size,pnl,return_pct,exit_reason)
                VALUES(:trade_id,:run_id,:symbol,:underlying,:option_type,:fold,:signal_time,:entry_time,:exit_time,
                       :entry_price,:exit_price,:quantity,:lots,:lot_size,:pnl,:return_pct,:exit_reason)""",
                trades,
            )

    def counts(self) -> dict:
        result = {}
        for asset in ("spot", "option"):
            row = self.db.execute(
                "SELECT COUNT(*) rows, COUNT(DISTINCT symbol) symbols, MIN(timestamp) first_ts, MAX(timestamp) last_ts FROM historical_candles WHERE asset_type=?",
                (asset,),
            ).fetchone()
            result[asset] = dict(row)
        return result

    def trim(self, asset_type: str, start: date, end: date) -> None:
        first = int(datetime.combine(start, time.min, IST).timestamp())
        last = int(datetime.combine(end, time.max, IST).timestamp())
        with self.db:
            self.db.execute(
                "DELETE FROM historical_candles WHERE asset_type=? AND (timestamp<? OR timestamp>?)",
                (asset_type, first, last),
            )

    def close(self) -> None:
        self.db.close()


@dataclass(slots=True)
class OpenPosition:
    symbol: str
    underlying: str
    option_type: str
    signal_time: str
    entry_time: str
    entry_price: float
    quantity: int
    lots: int
    lot_size: int
    peak: float
    hard_stop: float
    trailing_stop: float | None = None
    fold: str = ""
    entry_anchor: float = 0.0
    total_lots: int = 1
    total_cost: float = 0.0
    realized_proceeds: float = 0.0
    exit_value: float = 0.0
    entry_stage: int = 1
    exit_stage: int = 0


class ResearchPipeline:
    def __init__(self, settings: Settings, scope: str = "full"):
        self.settings = settings
        self.scope = scope
        self.broker = FyersBroker(settings)
        self.store = HistoricalStore(settings.research_database_path)
        self.contracts = self._load_august_contracts()
        self.errors: list[dict] = []
        self.open_positions: list[dict] = []

    def _load_august_contracts(self) -> list[dict]:
        path = Path(self.settings.contract_snapshot_path)
        payload = json.loads(path.read_text()) if path.exists() else {"contracts": []}
        now = datetime.now(IST)
        marker = f"{now.year}-08"
        rows = [row for row in payload.get("contracts", []) if str(row.get("expiry", "")).startswith(marker)]
        if self.scope == "indices":
            index_symbols = set(INDEX_SPOTS.values())
            rows = [row for row in rows if row.get("spot_symbol") in index_symbols]
        return rows

    async def download(self) -> dict:
        await self.broker.connect()
        today = datetime.now(IST).date()
        completed_yesterday = today - timedelta(days=1)
        spot_start = today - timedelta(days=SPOT_HISTORY_DAYS)
        for index, symbol in enumerate(SPOT_SYMBOLS, 1):
            await self._download_chunks(symbol, "spot", spot_start, completed_yesterday, None)
            await self._download_chunks(symbol, "spot", today, today, None, force=True)
            if index % 10 == 0:
                print(f"spot {index}/{len(SPOT_SYMBOLS)}", flush=True)
        for index, contract in enumerate(self.contracts, 1):
            # Contract snapshots do not carry a listing date.  Request a
            # conservative pre-expiry window and save only the candles Fyers
            # actually has, rather than assuming the contract began in August.
            expiry = date.fromisoformat(str(contract["expiry"]))
            option_start = expiry - timedelta(days=OPTION_LIFETIME_LOOKBACK_DAYS)
            await self._download_chunks(contract["symbol"], "option", option_start, completed_yesterday, contract.get("expiry"))
            await self._download_chunks(contract["symbol"], "option", today, today, contract.get("expiry"), force=True)
            if index % 10 == 0:
                print(f"option {index}/{len(self.contracts)}", flush=True)
        # Deliberately do not trim here: the downloader is an append/backfill
        # job and a later research run must not discard historical warm-up.
        return {"coverage": self.store.counts(), "contracts": len(self.contracts), "errors": self.errors}

    async def _download_chunks(self, symbol: str, asset_type: str, start: date, end: date, expiry: str | None, force: bool = False) -> None:
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + timedelta(days=FYERS_MINUTE_CHUNK_DAYS - 1))
            if not force and self.store.download_complete(symbol, asset_type, cursor, chunk_end):
                cursor = chunk_end + timedelta(days=1)
                continue
            try:
                # Fyers date_format=1 interprets these as exchange calendar dates.
                # Keep the requested IST date unchanged instead of shifting it to
                # the previous UTC date.
                start_dt = datetime.combine(cursor, time.min, UTC)
                end_dt = datetime.combine(chunk_end, time.max, UTC)
                rows = None
                for attempt in range(5):
                    try:
                        rows = await self.broker.history_range(symbol, 60, start_dt, end_dt)
                        break
                    except Exception as exc:
                        if "429" not in str(exc) or attempt == 4:
                            raise
                        await asyncio.sleep(2 * (attempt + 1))
                assert rows is not None
                count = self.store.save_candles(rows, asset_type, expiry)
                self.store.log_download(symbol, asset_type, cursor, chunk_end, count, "ok")
            except Exception as exc:
                self.errors.append({"symbol": symbol, "from": cursor.isoformat(), "to": chunk_end.isoformat(), "error": str(exc)})
                try:
                    self.store.log_download(symbol, asset_type, cursor, chunk_end, 0, "error", str(exc))
                except sqlite3.Error:
                    pass
            cursor = chunk_end + timedelta(days=1)
            await asyncio.sleep(0.38)

    def walk_forward(self) -> dict:
        run_id = f"wf-{datetime.now(IST).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        trades: list[dict] = []
        self.open_positions = []
        contract_by_symbol = {row["symbol"]: row for row in self.contracts}
        tested_contracts = 0
        for symbol, meta in contract_by_symbol.items():
            if self.store.candle_count(symbol) < self.settings.slow_period + 2:
                continue
            tested_contracts += 1
            trades.extend(self._run_contract(symbol, meta, run_id))
        trades.sort(key=lambda row: row["entry_time"])
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for row in trades:
            equity += row["pnl"]
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity - peak)
            row["cumulative_pnl"] = round(equity, 4)
            row["drawdown"] = round(equity - peak, 4)
        fold_rows: dict[str, list[dict]] = {}
        for row in trades:
            fold_rows.setdefault(row["fold"], []).append(row)
        summary = {
            "run_id": run_id,
            "scope": self.scope,
            "method": f"chronological expanding walk-forward on {self.settings.timeframe_seconds}-second bars; fixed parameters; signal on close, entry next bar open",
            "contracts_selected": len(contract_by_symbol),
            "contracts_tested": tested_contracts,
            "trades": len(trades),
            "closed_trades": len(trades),
            "open_positions": len(self.open_positions),
            "wins": sum(row["pnl"] > 0 for row in trades),
            "win_rate_pct": round(100 * sum(row["pnl"] > 0 for row in trades) / len(trades), 2) if trades else 0,
            "net_pnl": round(sum(row["pnl"] for row in trades), 2),
            "open_unrealized_pnl": round(sum(row["unrealized_pnl"] for row in self.open_positions), 2),
            "average_return_pct": round(sum(row["return_pct"] for row in trades) / len(trades), 3) if trades else 0,
            "max_drawdown": round(max_drawdown, 2),
            "folds": [
                {"date": fold, "trades": len(rows), "net_pnl": round(sum(r["pnl"] for r in rows), 2), "wins": sum(r["pnl"] > 0 for r in rows)}
                for fold, rows in sorted(fold_rows.items())
            ],
            "exit_reasons": {reason: sum(row["exit_reason"] == reason for row in trades) for reason in sorted({row["exit_reason"] for row in trades})},
            "coverage": self.store.counts(),
        }
        parameters = {
            "timeframe_seconds": self.settings.timeframe_seconds,
            "macd": [self.settings.fast_period, self.settings.slow_period, self.settings.signal_period],
            "entry": "option premium MACD crosses upward through zero",
            "entry_filter": "rising KAMA; RSI(KAMA) >= configured minimum; ROC(KAMA) > configured minimum",
            "kama_rsi": [self.settings.kama_rsi_period, self.settings.kama_rsi_min],
            "kama_roc": [self.settings.kama_roc_period, self.settings.kama_roc_min],
            "hard_stop_pct": 30,
            "trailing_activation_profit_pct": 30,
            "trailing_stop_pct": 25,
            "max_trade_lots": self.settings.max_trade_lots,
            "scale_in_profit_pct": [7.5, 15, 22.5] if self.settings.max_trade_lots > 1 else [],
            "scale_out_profit_pct": [30, 50, 75] if self.settings.max_trade_lots > 1 else [],
            "slippage_bps_per_side": self.settings.slippage_bps,
            "initial_lots": 1,
            "quantity_rule": (
                "one exchange lot; no pyramiding or partial exits"
                if self.settings.max_trade_lots == 1
                else f"one initial lot; profit pyramids; maximum {self.settings.max_trade_lots} exchange lots"
            ),
        }
        self.store.save_run(run_id, parameters, summary, trades)
        equity_curve = [
            {"timestamp": row["exit_time"], "equity": row["cumulative_pnl"], "drawdown": row["drawdown"]}
            for row in trades
        ]
        report = {"parameters": parameters, "summary": summary, "trades": trades, "open_positions": self.open_positions, "equity_curve": equity_curve}
        report_path = Path(self.settings.research_report_path)
        if self.scope != "full":
            report_path = report_path.with_name(f"{report_path.stem}_{self.scope}_{self.settings.timeframe_seconds}s{report_path.suffix}")
        report_path.write_text(json.dumps(report, indent=2))
        report_path.chmod(0o600)
        return report

    def _run_contract(self, symbol: str, meta: dict, run_id: str) -> list[dict]:
        candles = aggregate_session_candles(self.store.candles(symbol), self.settings.timeframe_seconds)
        if len(candles) < self.settings.slow_period + 2:
            return []
        indicator = IncrementalMACD(self.settings.fast_period, self.settings.slow_period, self.settings.signal_period)
        kama = IncrementalKAMA(self.settings.kama_period, self.settings.kama_fast, self.settings.kama_slow)
        kama_rsi = IncrementalRSI(self.settings.kama_rsi_period)
        kama_roc = IncrementalROC(self.settings.kama_roc_period)
        position: OpenPosition | None = None
        pending_signal: str | None = None
        output: list[dict] = []
        slip = self.settings.slippage_bps / 10_000
        try:
            expiry_cutoff = datetime.combine(date.fromisoformat(str(meta.get("expiry", ""))), time(15, 20), IST)
        except ValueError:
            expiry_cutoff = None

        def close_position(candle: Candle, raw_price: float, reason: str) -> None:
            nonlocal position
            assert position is not None
            exit_price = round(raw_price * (1 - slip), 4)
            total_quantity = position.total_lots * position.lot_size
            final_proceeds = position.realized_proceeds + exit_price * position.quantity
            pnl = round(final_proceeds - position.total_cost, 4)
            weighted_exit = round((position.exit_value + exit_price * position.quantity) / total_quantity, 4)
            output.append({
                "trade_id": uuid4().hex, "run_id": run_id, "symbol": symbol,
                "underlying": position.underlying, "option_type": position.option_type,
                "fold": position.fold,
                "signal_time": position.signal_time, "entry_time": position.entry_time,
                "exit_time": datetime.fromtimestamp(candle.timestamp, UTC).isoformat(),
                "entry_price": position.entry_price, "exit_price": weighted_exit,
                "quantity": total_quantity, "lots": position.total_lots, "lot_size": position.lot_size, "pnl": pnl,
                "return_pct": round(pnl / position.total_cost * 100, 4),
                "exit_reason": reason,
            })
            position = None

        for candle in candles:
            candle_time = datetime.fromtimestamp(candle.timestamp, UTC).isoformat()
            market_time = datetime.fromtimestamp(candle.timestamp, UTC).astimezone(IST)
            if position and expiry_cutoff and market_time >= expiry_cutoff:
                close_position(candle, candle.close, "EXPIRY_EXIT_15_20_IST")
                pending_signal = None
            if pending_signal and position is None:
                if expiry_cutoff and market_time >= expiry_cutoff:
                    pending_signal = None
                    continue
                entry = round(candle.open * (1 + slip), 4)
                lot_size = int(meta.get("lot_size") or 0)
                if lot_size < 1:
                    pending_signal = None
                    continue
                lots = 1
                quantity = lots * lot_size
                position = OpenPosition(
                    symbol=symbol,
                    underlying=str(meta.get("underlying", "")),
                    option_type=str(meta.get("option_type", "")),
                    signal_time=pending_signal,
                    entry_time=candle_time,
                    entry_price=entry,
                    quantity=quantity,
                    lots=lots,
                    lot_size=lot_size,
                    peak=entry,
                    hard_stop=round(entry * 0.70, 4),
                    fold=datetime.fromtimestamp(candle.timestamp, UTC).astimezone(IST).date().isoformat(),
                    entry_anchor=entry,
                    total_lots=lots,
                    total_cost=entry * quantity,
                )
                pending_signal = None
            if position:
                if candle.low <= position.hard_stop:
                    close_position(candle, min(candle.open, position.hard_stop), "HARD_STOP_30_PCT")
                elif position.trailing_stop is not None and candle.low <= position.trailing_stop:
                    close_position(candle, min(candle.open, position.trailing_stop), "TRAILING_STOP_25_PCT")
                else:
                    position.peak = max(position.peak, candle.high)
                    if position.peak >= position.entry_price * (1 + self.settings.trailing_activation_pct):
                        position.trailing_stop = round(position.peak * 0.75, 4)
                    scaled_in = False
                    scale_in_levels = (0.075, 0.15, 0.225)
                    stage_index = position.entry_stage - 1
                    if position.entry_stage < self.settings.max_trade_lots and stage_index < len(scale_in_levels):
                        add_at = round(position.entry_anchor * (1 + scale_in_levels[stage_index]), 4)
                        if candle.high >= add_at:
                            fill = round(add_at * (1 + slip), 4)
                            position.quantity += position.lot_size
                            position.lots += 1
                            position.total_lots += 1
                            position.entry_stage += 1
                            position.total_cost += fill * position.lot_size
                            position.entry_price = position.total_cost / (position.total_lots * position.lot_size)
                            position.hard_stop = round(position.entry_price * 0.70, 4)
                            scaled_in = True
                    scale_out_levels = (0.30, 0.50, 0.75)
                    if not scaled_in and position.lots > 1 and position.exit_stage < len(scale_out_levels):
                        take_at = position.entry_price * (1 + scale_out_levels[position.exit_stage])
                        if candle.high >= take_at:
                            fill = round(take_at * (1 - slip), 4)
                            position.quantity -= position.lot_size
                            position.lots -= 1
                            position.realized_proceeds += fill * position.lot_size
                            position.exit_value += fill * position.lot_size
                            position.exit_stage += 1
            value = indicator.update(candle.close)
            old_kama = kama.value
            kama_value = kama.update(candle.close)
            rsi_value = kama_rsi.update(kama_value) if kama_value is not None else None
            roc_value = kama_roc.update(kama_value) if kama_value is not None else None
            qualified = (
                kama_value is not None and old_kama is not None
                and candle.close > kama_value > old_kama
                and rsi_value is not None and rsi_value >= self.settings.kama_rsi_min
                and roc_value is not None and roc_value > self.settings.kama_roc_min
            )
            if position is None and pending_signal is None and indicator.count > self.settings.slow_period:
                if (not expiry_cutoff or market_time < expiry_cutoff) and qualified and value.previous_macd is not None and value.previous_macd <= 0 < value.macd:
                    pending_signal = candle_time
        if position and candles:
            last = candles[-1]
            mark_price = round(last.close, 4)
            self.open_positions.append({
                "position_id": uuid4().hex,
                "run_id": run_id,
                "status": "OPEN",
                "symbol": position.symbol,
                "underlying": position.underlying,
                "option_type": position.option_type,
                "expiry": str(meta.get("expiry", "")),
                "fold": position.fold,
                "signal_time": position.signal_time,
                "entry_time": position.entry_time,
                "last_time": datetime.fromtimestamp(last.timestamp, UTC).isoformat(),
                "entry_price": position.entry_price,
                "last_price": mark_price,
                "quantity": position.quantity,
                "lots": position.lots,
                "lot_size": position.lot_size,
                "peak_price": position.peak,
                "hard_stop": position.hard_stop,
                "trailing_stop": position.trailing_stop,
                "total_lots_entered": position.total_lots,
                "partial_exit_proceeds": round(position.realized_proceeds, 4),
                "unrealized_pnl": round((mark_price - position.entry_price) * position.quantity, 4),
                "unrealized_return_pct": round((mark_price / position.entry_price - 1) * 100, 4),
            })
        return output

    async def close(self) -> None:
        await self.broker.close()
        self.store.close()


def load_runtime_settings() -> Settings:
    base = environment_settings.model_dump()
    persisted = RuntimeSettingsStore(environment_settings.runtime_settings_path).load()
    credentials = RuntimeSettingsStore(environment_settings.credentials_path).load()
    merged = {**base, **persisted, **credentials, "execution_mode": "paper", "allow_live_orders": False}
    # Runtime paths are environment-specific and must not leak Docker's /app
    # paths into a local research process.
    for key in ("database_path", "runtime_settings_path", "credentials_path", "research_database_path", "research_report_path", "contract_snapshot_path"):
        merged[key] = base[key]
    return Settings(**merged)


async def main_async(
    download: bool = True, scope: str = "full", timeframe: int | None = None,
    max_trade_lots: int | None = None, download_only: bool = False,
) -> dict:
    configured = load_runtime_settings()
    if timeframe:
        configured = configured.model_copy(update={"timeframe_seconds": timeframe})
    if max_trade_lots:
        configured = configured.model_copy(update={"max_trade_lots": max_trade_lots})
    pipeline = ResearchPipeline(configured, scope)
    try:
        download_result = await pipeline.download() if download else {"coverage": pipeline.store.counts()}
        if download_only:
            return {"download": download_result}
        report = pipeline.walk_forward()
        RuntimeSettingsStore(configured.credentials_path).save({
            key: getattr(configured, key) for key in (
                "fyers_client_id", "fyers_secret", "fyers_access_token", "fyers_redirect_uri"
            )
        })
        return {"download": download_result, "walk_forward": report["summary"]}
    finally:
        await pipeline.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--download-only", action="store_true", help="Backfill history without running or replacing a research report")
    parser.add_argument("--scope", choices=("full", "indices"), default="full")
    parser.add_argument("--timeframe", type=int)
    parser.add_argument("--max-trade-lots", type=int, choices=range(1, 5))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main_async(
        not args.no_download, args.scope, args.timeframe, args.max_trade_lots, args.download_only,
    )), indent=2))


if __name__ == "__main__":
    main()
