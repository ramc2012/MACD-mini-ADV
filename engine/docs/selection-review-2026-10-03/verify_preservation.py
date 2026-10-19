"""Run inside the strategy container to record private, read-only audit hashes.

An optional --backup-dir makes consistent SQLite backups of the five paper
books. No trading, learning, settings change or shared-database write is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import urllib.request


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def api(path, base="http://127.0.0.1:8100"):
    request = urllib.request.Request(base + path, headers={"x-macd-token": os.getenv("MACD_API_TOKEN", "")})
    return json.load(urllib.request.urlopen(request, timeout=20))


def portfolio(book):
    keys = ("symbol", "quantity", "average_price", "opened_at", "entry_fees", "position_id")
    positions = [{key: row.get(key) for key in keys} for row in book["positions"]]
    return {"positions": len(positions), "book_hash": digest(sorted(positions, key=lambda row: row["symbol"])),
            "cash": book["cash"], "realized_pnl": book["realized_pnl"]}


def tables(connection, names):
    result = {}
    for name in names:
        rows = connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall()
        result[name] = {"rows": len(rows), "sha256": digest(rows)}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--backup-dir")
    args = parser.parse_args()
    result = {"books": {}}
    paths = {"macd": os.environ["MACD_DATABASE_PATH"], "blast": os.environ["MACD_BLAST_DATABASE_PATH"],
             "mp": os.environ["MACD_MP_DATABASE_PATH"]}
    paths.update({name: str(Path(paths["macd"]).parent / (name + ".sqlite3")) for name in ("smart_1l", "smart_50l")})
    for book, path in paths.items():
        source = sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=30)
        try:
            result["books"][book] = {"quick_check": source.execute("PRAGMA quick_check(1)").fetchone()[0],
                                     "tables": tables(source, ("orders", "trades", "signals", "closed_positions"))}
            if args.backup_dir:
                folder = Path(args.backup_dir)
                folder.mkdir(parents=True, exist_ok=True)
                target_path = folder / (book + ".sqlite3")
                if target_path.exists():
                    raise FileExistsError(target_path)
                target = sqlite3.connect(target_path)
                try:
                    source.backup(target)
                    assert target.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok"
                finally:
                    target.close()
        finally:
            source.close()
    history = sqlite3.connect("file:" + os.environ["MACD_RESEARCH_DATABASE_PATH"] + "?mode=ro", uri=True, timeout=30)
    try:
        names = [row[0] for row in history.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'agent_%' ORDER BY name")]
        result["agent_archive"] = tables(history, names)
    finally:
        history.close()
    result["portfolios"] = {"macd": portfolio(api("/api/portfolio")),
                             "blast": portfolio(api("/api/blast/snapshot")["portfolio"]),
                             "mp": portfolio(api("/api/mp/snapshot", "http://desk:8100")["portfolio"])}
    result["portfolios"].update({name: portfolio(api("/api/lanes/" + name + "/snapshot")["portfolio"])
                                 for name in ("smart_1l", "smart_50l")})
    snapshot = api("/api/snapshot")
    health = api("/api/system/health")
    result["execution"] = {key: snapshot["execution"].get(key) for key in ("mode", "live_orders_unlocked", "risk")}
    result["health"] = {key: health.get(key) for key in ("observed_at", "status", "feed_alive", "token_expired", "ticks_total", "session_open", "warmup", "market_calendar")}
    result["settings_sha256"] = hashlib.sha256(Path("/app/runtime/settings.json").read_bytes()).hexdigest()
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": args.output, "portfolios": result["portfolios"],
                      "integrity": {key: value["quick_check"] for key, value in result["books"].items()},
                      "agent_rows": {key: value["rows"] for key, value in result["agent_archive"].items()},
                      "execution": result["execution"], "health": result["health"]}))


if __name__ == "__main__":
    main()
