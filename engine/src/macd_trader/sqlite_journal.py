"""Journal mode for the large research databases, chosen by where the file lives.

The rollback journal (DELETE) was adopted because these files once lived on a
Docker Desktop bind mount, whose virtualised filesystem does not reliably
provide the shared memory and byte-range locks WAL needs -- WAL corrupted a
trade book there. But with DELETE every write locks the whole file: readers
wait for writers, and the nightly job, a Fyers backfill and the desk's reads
queue behind one another (a backfill fell from ~85 to ~22 contracts a minute
while the nightly ran).

On a local Linux filesystem -- the Docker named volume is ext4 inside the VM,
shared by the engine and desk containers through one kernel -- WAL is the
normal, crash-safe choice: readers never block the writer or each other. So:
WAL where the filesystem is local, the rollback journal anywhere else (a bind
mount, a network share, anything unrecognised). ``MACD_SQLITE_JOURNAL`` =
``wal`` / ``delete`` overrides; ``auto`` (the default) decides by filesystem.

Only the research stores use this (historical.sqlite3, ticks.sqlite3). The
trade books stay on DELETE + synchronous=FULL: they are small, rarely
contended, and durability of every fill matters more than throughput.
"""
from __future__ import annotations

import os
import sqlite3
from functools import lru_cache
from pathlib import Path

LOCAL_FILESYSTEMS = frozenset({"ext4", "ext3", "xfs", "btrfs", "zfs", "f2fs", "apfs"})


def filesystem_of(path: str, mounts: str = "/proc/mounts") -> str | None:
    """The filesystem type of the mount holding ``path`` (longest mount-point prefix), or None."""
    target = str(Path(path).resolve().parent if not Path(path).is_dir() else Path(path).resolve())
    best, kind = "", None
    try:
        with open(mounts, encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 3:
                    continue
                point = parts[1].replace("\\040", " ")
                if (target == point or target.startswith(point.rstrip("/") + "/") or point == "/") and len(point) >= len(best):
                    best, kind = point, parts[2]
    except OSError:
        return None
    return kind


@lru_cache(maxsize=32)
def journal_mode(path: str) -> str:
    choice = (os.environ.get("MACD_SQLITE_JOURNAL") or "auto").strip().lower()
    if choice in ("wal", "delete"):
        return choice
    return "wal" if filesystem_of(path) in LOCAL_FILESYSTEMS else "delete"


def configure(connection: sqlite3.Connection, path: str, *, rollback_sync: str = "FULL") -> str:
    """Set the journal and sync level for ``path``; returns the mode in effect.

    Switching to WAL needs a moment with no other connection mid-transaction;
    if another holds the file, this connection carries on in the current mode
    and a later one converts it (the mode is stored in the file).
    """
    wanted = journal_mode(str(path))
    try:
        mode = connection.execute(f"PRAGMA journal_mode={'WAL' if wanted == 'wal' else 'DELETE'}").fetchone()[0]
    except sqlite3.OperationalError:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    connection.execute(f"PRAGMA synchronous={'NORMAL' if str(mode).lower() == 'wal' else rollback_sync}")
    return str(mode).lower()
