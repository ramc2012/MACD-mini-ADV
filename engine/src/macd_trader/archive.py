"""The permanent market-data archive: one Parquet file (zstd) per dataset per IST day, outside Docker's disk.

Layout under the archive root (a host folder, so a Docker reset cannot take it):

    <dataset>/day=YYYY-MM-DD/<part>.parquet        the rows
    <dataset>/day=YYYY-MM-DD/<part>.json           sidecar: rows, sha256, bytes, min/max time, per-symbol rows,
                                                   source, producer version, written_at
    bars_1m/_symbols.json                          per-symbol row totals over every sealed day (for pruning)

``part`` is "data" for the day as the live stores held it; "recovered-<source>" parts hold rows that survive only
in an old copy (an expired contract's bars, raw ticks from a crash backup). DuckDB / pandas read a dataset with the
hive glob ``<dataset>/day=*/*.parquet``.

The archive is the record; the SQLite stores are the hot window. Nothing may be deleted from a hot store until
``covers`` confirms the archive holds at least as many rows for it (the callers check before every delete). Sizes
measured 24 Oct 2026 (research/storage_20261024): ticks 10 bytes/row (SQLite 117), bars 6 (166), chains 15.5 (194).

Raw ticks are kept ``tick_retention_days`` (120) in the archive; every other dataset is kept forever.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
VERSION = 1
BATCH_ROWS = 200_000          # rows per Arrow batch / Parquet row group: bounds memory to ~100 MB per seal
ZSTD_LEVEL = 3

# column -> Arrow type name; the order is the file's column order
SCHEMAS: dict[str, list[tuple[str, str]]] = {
    "ticks": [("symbol", "string"), ("ts_ms", "int64"), ("ltp", "float64"), ("cum_volume", "int64"),
              ("last_qty", "int64"), ("bid", "float64"), ("ask", "float64"), ("bid_qty", "int64"),
              ("ask_qty", "int64"), ("oi", "int64"), ("tbq", "int64"), ("tsq", "int64")],
    "bars_1m": [("symbol", "string"), ("timestamp", "int64"), ("open", "float64"), ("high", "float64"),
                ("low", "float64"), ("close", "float64"), ("volume", "int64"), ("asset_type", "string"),
                ("expiry", "string")],
    "chains": [("ts", "int64"), ("underlying", "string"), ("expiry", "string"), ("strike", "float64"),
               ("option_type", "string"), ("symbol", "string"), ("ltp", "float64"), ("volume", "int64"),
               ("oi", "int64"), ("spot", "float64"), ("bid", "float64"), ("ask", "float64"), ("prev_oi", "int64"),
               ("fp", "float64"), ("vix", "float64")],
    "oi_minutes": [("ts", "int64"), ("symbol", "string"), ("oi", "int64"), ("prev_oi", "int64"), ("ltp", "float64"),
                   ("volume", "int64")],
    "flow_1m": [("symbol", "string"), ("minute_ts", "int64"), ("open", "float64"), ("high", "float64"),
                ("low", "float64"), ("close", "float64"), ("volume", "int64"), ("buy_volume", "int64"),
                ("sell_volume", "int64"), ("delta", "int64"), ("trades", "int64"), ("ticks", "int64"),
                ("vwap", "float64"), ("quote_n", "int64"), ("mid_n", "int64"), ("tick_n", "int64"),
                ("zero_tick_n", "int64"), ("pending_n", "int64"), ("conflict_n", "int64"), ("unclassified", "int64"),
                ("avg_spread", "float64"), ("ofi", "float64"), ("ofi_events", "int64"), ("avg_confidence", "float64"),
                ("oi", "int64")],
    "ladders": [("symbol", "string"), ("day", "string"), ("price", "float64"), ("buy_volume", "int64"),
                ("sell_volume", "int64")],
}
TIME_COLUMN = {"ticks": "ts_ms", "bars_1m": "timestamp", "chains": "ts", "oi_minutes": "ts", "flow_1m": "minute_ts",
               "ladders": None}
SYMBOL_COLUMN = {"ticks": "symbol", "bars_1m": "symbol", "chains": "underlying", "oi_minutes": "symbol",
                 "flow_1m": "symbol", "ladders": "symbol"}
# datasets whose per-symbol totals are kept in <dataset>/_symbols.json for the hot store's per-symbol pruning
SYMBOL_TOTALS = ("bars_1m",)


def day_bounds(day: str) -> tuple[int, int]:
    """[start, end) of an IST calendar day in epoch seconds."""
    start = int(datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=IST).timestamp())
    return start, start + 86_400


def ist_day(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, IST).date().isoformat()


class ArchiveError(RuntimeError):
    pass


class Archive:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    # -------------------------------------------------------------------------------------------- reading the index

    def available(self) -> bool:
        return self.root.is_dir()

    def day_dir(self, dataset: str, day: str) -> Path:
        return self.root / dataset / f"day={day}"

    def sidecars(self, dataset: str, day: str) -> list[dict]:
        folder = self.day_dir(dataset, day)
        out = []
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                out.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        return out

    def rows(self, dataset: str, day: str, part: str | None = None) -> int | None:
        """Rows sealed for that day (all parts, or one); None when nothing is sealed."""
        found = [s for s in self.sidecars(dataset, day) if part is None or s.get("part") == part]
        return sum(int(s.get("rows") or 0) for s in found) if found else None

    def covers(self, dataset: str, day: str, hot_rows: int) -> bool:
        """True when the archive holds at least ``hot_rows`` rows for the day: the hot copy may then go."""
        if hot_rows <= 0:
            return True
        sealed = self.rows(dataset, day)
        return sealed is not None and sealed >= hot_rows

    def symbol_rows(self, dataset: str, day: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        for sidecar in self.sidecars(dataset, day):
            for symbol, rows in (sidecar.get("symbol_rows") or {}).items():
                totals[symbol] = totals.get(symbol, 0) + int(rows)
        return totals

    def symbol_totals(self, dataset: str) -> dict[str, int]:
        path = self.root / dataset / "_symbols.json"
        try:
            return {k: int(v) for k, v in json.loads(path.read_text()).items()}
        except (OSError, ValueError):
            return {}

    def days(self, dataset: str) -> list[str]:
        base = self.root / dataset
        if not base.is_dir():
            return []
        return sorted(p.name[4:] for p in base.iterdir() if p.is_dir() and p.name.startswith("day=")
                      and any(p.glob("*.json")))

    # -------------------------------------------------------------------------------------------------- writing

    def write(self, dataset: str, day: str, part: str, batches, *, source: str) -> dict:
        """Stream ``batches`` (iterables of row tuples in SCHEMAS order) into <dataset>/day=<day>/<part>.parquet.

        Written to a temporary name, read back to verify the row count, hashed, then moved into place with its
        sidecar written last: a sidecar only ever describes a complete, verified file. An empty day writes a
        sidecar with rows 0 and no file, so it is not retried every night.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        columns = SCHEMAS[dataset]
        schema = pa.schema([(name, getattr(pa, kind)()) for name, kind in columns])
        folder = self.day_dir(dataset, day)
        folder.mkdir(parents=True, exist_ok=True)
        final, temporary = folder / f"{part}.parquet", folder / f".{part}.parquet.tmp"
        time_col, symbol_col = TIME_COLUMN[dataset], SYMBOL_COLUMN[dataset]
        t_index = [name for name, _ in columns].index(time_col) if time_col else None
        s_index = [name for name, _ in columns].index(symbol_col)
        rows, low, high, per_symbol = 0, None, None, {}
        writer = None
        try:
            for batch in batches:
                if not batch:
                    continue
                arrays = [pa.array([row[i] for row in batch], type=schema.field(i).type) for i in range(len(columns))]
                if writer is None:
                    writer = pq.ParquetWriter(temporary, schema, compression="zstd", compression_level=ZSTD_LEVEL)
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema), row_group_size=BATCH_ROWS)
                rows += len(batch)
                for row in batch:
                    key = row[s_index]
                    per_symbol[key] = per_symbol.get(key, 0) + 1
                if t_index is not None:
                    stamps = [row[t_index] for row in batch if row[t_index] is not None]
                    if stamps:
                        low = min(stamps) if low is None else min(low, min(stamps))
                        high = max(stamps) if high is None else max(high, max(stamps))
        finally:
            if writer is not None:
                writer.close()
        sidecar = {"dataset": dataset, "day": day, "part": part, "rows": rows, "source": source,
                   "version": VERSION, "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
                   "min_time": low, "max_time": high,
                   "symbol_rows": per_symbol if dataset in SYMBOL_TOTALS or dataset == "ticks" else None,
                   "symbols": len(per_symbol)}
        if rows:
            check = pq.ParquetFile(temporary).metadata.num_rows
            if check != rows:
                temporary.unlink(missing_ok=True)
                raise ArchiveError(f"{dataset} {day} {part}: wrote {check} of {rows} rows")
            digest = hashlib.sha256()
            with open(temporary, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            sidecar.update(sha256=digest.hexdigest(), bytes=temporary.stat().st_size, file=final.name)
            os.replace(temporary, final)
        else:
            final.unlink(missing_ok=True)
        side_tmp = folder / f".{part}.json.tmp"
        side_tmp.write_text(json.dumps(sidecar, separators=(",", ":")))
        os.replace(side_tmp, folder / f"{part}.json")
        if dataset in SYMBOL_TOTALS:
            self.rebuild_symbol_totals(dataset)
        return {k: v for k, v in sidecar.items() if k != "symbol_rows"}

    def rebuild_symbol_totals(self, dataset: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        for day in self.days(dataset):
            for symbol, rows in self.symbol_rows(dataset, day).items():
                totals[symbol] = totals.get(symbol, 0) + rows
        path = self.root / dataset / "_symbols.json"
        temporary = path.with_name("._symbols.json.tmp")
        temporary.write_text(json.dumps(totals, separators=(",", ":"), sort_keys=True))
        os.replace(temporary, path)
        return totals

    def drop_days_before(self, dataset: str, cutoff: str) -> list[str]:
        """Retention for a dataset kept a limited time (raw ticks): remove sealed days before ``cutoff``."""
        gone = []
        for day in self.days(dataset):
            if day < cutoff:
                shutil.rmtree(self.day_dir(dataset, day))
                gone.append(day)
        return gone


# --------------------------------------------------------------------------------------------------- the sources

def _chunks(cursor, size: int = BATCH_ROWS):
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield rows


def tick_batches(ticks: sqlite3.Connection, day: str):
    start, end = day_bounds(day)
    cursor = ticks.execute(
        """SELECT s.symbol, t.ts_ms, t.ltp, t.cum_volume, t.last_qty, t.bid, t.ask, t.bid_qty, t.ask_qty, t.oi,
                  t.tbq, t.tsq
           FROM ticks t JOIN tick_symbols s ON s.id = t.symbol_id
           WHERE t.ts_ms >= ? AND t.ts_ms < ? ORDER BY t.symbol_id, t.ts_ms""", (start * 1000, end * 1000))
    yield from _chunks(cursor)


def tick_count(ticks: sqlite3.Connection, day: str) -> int:
    start, end = day_bounds(day)
    return ticks.execute("SELECT COUNT(*) FROM ticks WHERE ts_ms >= ? AND ts_ms < ?",
                         (start * 1000, end * 1000)).fetchone()[0]


def bar_symbols(history: sqlite3.Connection) -> list[str]:
    """Every symbol with minute bars (a PK prefix scan: ~40 s on 55M rows; once per pass)."""
    return [row[0] for row in history.execute("SELECT DISTINCT symbol FROM historical_candles")]


def bar_batches(history: sqlite3.Connection, day: str, symbols: list[str], counts: dict | None = None):
    """One day's 1-minute bars, symbol by symbol on the primary key (the table has no time index)."""
    start, end = day_bounds(day)
    buffer: list[tuple] = []
    for symbol in symbols:
        rows = history.execute(
            """SELECT symbol, timestamp, open, high, low, close, volume, asset_type, expiry FROM historical_candles
               WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? AND timestamp < ?
               ORDER BY timestamp""", (symbol, start, end)).fetchall()
        if counts is not None and rows:
            counts[symbol] = len(rows)
        buffer.extend(rows)
        if len(buffer) >= BATCH_ROWS:
            yield buffer
            buffer = []
    if buffer:
        yield buffer


def bar_counts(history: sqlite3.Connection, day: str, symbols: list[str]) -> dict[str, int]:
    start, end = day_bounds(day)
    out = {}
    for symbol in symbols:
        n = history.execute("""SELECT COUNT(*) FROM historical_candles WHERE symbol = ? AND timeframe_seconds = 60
                               AND timestamp >= ? AND timestamp < ?""", (symbol, start, end)).fetchone()[0]
        if n:
            out[symbol] = n
    return out


def chain_batches(history: sqlite3.Connection, day: str):
    start, end = day_bounds(day)
    cursor = history.execute(
        """SELECT ts, underlying, expiry, strike, option_type, symbol, ltp, volume, oi, spot, bid, ask, prev_oi, fp, vix
           FROM chain_snapshots WHERE ts >= ? AND ts < ? ORDER BY underlying, symbol, ts""", (start, end))
    yield from _chunks(cursor)


def chain_count(history: sqlite3.Connection, day: str) -> int:
    start, end = day_bounds(day)
    return history.execute("SELECT COUNT(*) FROM chain_snapshots WHERE ts >= ? AND ts < ?", (start, end)).fetchone()[0]


def oi_batches(history: sqlite3.Connection, day: str):
    start, end = day_bounds(day)
    cursor = history.execute("""SELECT ts, symbol, oi, prev_oi, ltp, volume FROM oi_minutes WHERE ts >= ? AND ts < ?
                                ORDER BY symbol, ts""", (start, end))
    yield from _chunks(cursor)


def flow_batches(ticks: sqlite3.Connection, day: str):
    start, end = day_bounds(day)
    cursor = ticks.execute(
        """SELECT s.symbol, f.minute_ts, f.open, f.high, f.low, f.close, f.volume, f.buy_volume, f.sell_volume, f.delta,
                  f.trades, f.ticks, f.vwap, f.quote_n, f.mid_n, f.tick_n, f.zero_tick_n, f.pending_n, f.conflict_n,
                  f.unclassified, f.avg_spread, f.ofi, f.ofi_events, f.avg_confidence, f.oi
           FROM tick_minute_flow f JOIN tick_symbols s ON s.id = f.symbol_id
           WHERE f.minute_ts >= ? AND f.minute_ts < ? ORDER BY f.symbol_id, f.minute_ts""", (start, end))
    yield from _chunks(cursor)


def flow_count(ticks: sqlite3.Connection, day: str) -> int:
    start, end = day_bounds(day)
    return ticks.execute("SELECT COUNT(*) FROM tick_minute_flow WHERE minute_ts >= ? AND minute_ts < ?",
                         (start, end)).fetchone()[0]


def ladder_batches(ticks: sqlite3.Connection, day: str):
    cursor = ticks.execute(
        """SELECT s.symbol, l.day, l.price, l.buy_volume, l.sell_volume FROM tick_session_ladder l
           JOIN tick_symbols s ON s.id = l.symbol_id WHERE l.day = ? ORDER BY l.symbol_id, l.price""", (day,))
    yield from _chunks(cursor)


def ladder_count(ticks: sqlite3.Connection, day: str) -> int:
    return ticks.execute("SELECT COUNT(*) FROM tick_session_ladder WHERE day = ?", (day,)).fetchone()[0]


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# ---------------------------------------------------------------------------------------------- the nightly pass

def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)


def stale_bar_days(archive: Archive, history: sqlite3.Connection, before: str) -> dict[str, list[str]]:
    """Days whose sealed bars miss rows the hot store now has, by symbol.

    Contract selection back-fills up to 90 days of minutes for a newly picked contract, so rows land in days that
    were sealed long ago. One grouped pass over the table finds the symbols whose hot rows (before ``before``)
    outnumber the archive's; their per-day counts then name the days to re-seal.
    """
    start, _ = day_bounds(before)
    totals = archive.symbol_totals("bars_1m")
    grown = [symbol for symbol, hot in history.execute(
        """SELECT symbol, COUNT(*) FROM historical_candles WHERE timeframe_seconds = 60 AND timestamp < ?
           GROUP BY symbol""", (start,)) if hot > totals.get(symbol, 0)]
    out: dict[str, list[str]] = {}
    sealed_cache: dict[str, dict[str, int]] = {}
    for symbol in grown:
        for day, hot in history.execute(
                """SELECT date(timestamp,'unixepoch','+5 hours','+30 minutes') d, COUNT(*) FROM historical_candles
                   WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp < ? GROUP BY d""", (symbol, start)):
            if day not in sealed_cache:
                sealed_cache[day] = archive.symbol_rows("bars_1m", day)
            # an unsealed day (one off the trading calendar that still has bars, e.g. stray Saturday prints)
            # is sealed whole here, so every hot bar reaches the archive
            if hot > sealed_cache[day].get(symbol, 0):
                out.setdefault(day, []).append(symbol)
    return out


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)


def seal_pending(archive: Archive, *, history_path: str, ticks_path: str, today: date,
                 bars_lookback_days: int = 400, tick_retention_days: int = 120, log=None) -> dict:
    """Seal every finished day not yet in the archive, re-seal days whose hot copy has since grown, and apply the
    raw-tick retention. Read-only on the SQLite stores; idempotent, so a crash mid-pass just repeats it."""
    from . import market_calendar

    report: dict = {"sealed": [], "resealed": [], "errors": [], "ticks_dropped": []}
    say = log or (lambda *_: None)
    before = today.isoformat()

    def seal(dataset, day, batches, source, hot_rows=None, again=False):
        try:
            info = archive.write(dataset, day, "data", batches, source=source)
            if hot_rows is not None and info["rows"] < hot_rows:
                raise ArchiveError(f"{dataset} {day}: sealed {info['rows']} of {hot_rows} rows")
            report["resealed" if again else "sealed"].append(f"{dataset}:{day}:{info['rows']}")
            say(f"{'re-sealed' if again else 'sealed'} {dataset} {day}: {info['rows']:,} rows")
        except Exception as exc:  # noqa: BLE001 — one day must not stop the pass
            report["errors"].append(f"{dataset} {day}: {exc}"[:300])

    def due(dataset, day, hot):
        """(seal it?, is it a re-seal?) for a day whose hot store holds ``hot`` rows."""
        sealed = archive.rows(dataset, day, "data")
        if sealed is None:
            return True, False
        return hot > sealed, True

    if ticks_path and Path(ticks_path).exists():
        ticks = _ro(ticks_path)
        try:
            raw_days = [d for (d,) in ticks.execute(
                "SELECT DISTINCT date(ts_ms/1000,'unixepoch','+5 hours','+30 minutes') FROM ticks")
                if d and "2000-01-01" <= d < before]
            for day in sorted(raw_days):
                hot = tick_count(ticks, day)
                go, again = due("ticks", day, hot)
                if go:
                    seal("ticks", day, tick_batches(ticks, day), "ticks.sqlite3", hot, again)
            if _table_exists(ticks, "tick_condensed_days"):
                days = [d for (d,) in ticks.execute("SELECT day FROM tick_condensed_days WHERE day >= '2000-01-01' "
                                                    "AND day < ? ORDER BY day", (before,))]
                for day in days:
                    for dataset, batches, count in (("flow_1m", flow_batches, flow_count),
                                                    ("ladders", ladder_batches, ladder_count)):
                        hot = count(ticks, day)
                        go, again = due(dataset, day, hot)
                        if hot and go:
                            seal(dataset, day, batches(ticks, day), "ticks.sqlite3", hot, again)
        finally:
            ticks.close()

    if history_path and Path(history_path).exists():
        history = _ro(history_path)
        try:
            if _table_exists(history, "chain_days"):
                for (day,) in history.execute("SELECT DISTINCT day FROM chain_days WHERE day < ? ORDER BY day",
                                              (before,)):
                    hot = chain_count(history, day)
                    go, again = due("chains", day, hot)
                    if hot and go:
                        seal("chains", day, chain_batches(history, day), "historical.sqlite3", hot, again)
            if _table_exists(history, "oi_minutes"):
                for (day,) in history.execute("""SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes')
                                                 FROM oi_minutes ORDER BY 1"""):
                    if day and day < before:
                        start, end = day_bounds(day)
                        hot = history.execute("SELECT COUNT(*) FROM oi_minutes WHERE ts >= ? AND ts < ?",
                                              (start, end)).fetchone()[0]
                        go, again = due("oi_minutes", day, hot)
                        if hot and go:
                            seal("oi_minutes", day, oi_batches(history, day), "historical.sqlite3", hot, again)
            if _table_exists(history, "historical_candles"):
                todo, cursor = [], today - timedelta(days=bars_lookback_days)
                while cursor < today:
                    if market_calendar.is_trading_day(cursor) and archive.rows("bars_1m", cursor.isoformat()) is None:
                        todo.append(cursor.isoformat())
                    cursor += timedelta(days=1)
                symbols = bar_symbols(history) if todo else []
                for day in todo:
                    seal("bars_1m", day, bar_batches(history, day, symbols), "historical.sqlite3")
                stale = stale_bar_days(archive, history, before)
                if stale and not symbols:
                    symbols = bar_symbols(history)
                for day in sorted(stale):
                    seal("bars_1m", day, bar_batches(history, day, symbols), "historical.sqlite3",
                         again=archive.rows("bars_1m", day, "data") is not None)
        finally:
            history.close()

    if tick_retention_days > 0:
        cutoff = (today - timedelta(days=tick_retention_days)).isoformat()
        report["ticks_dropped"] = archive.drop_days_before("ticks", cutoff)
    return report
