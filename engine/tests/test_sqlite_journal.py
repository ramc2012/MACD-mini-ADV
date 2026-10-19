"""WAL on a local filesystem, the rollback journal on a bind mount, and the override."""
from __future__ import annotations

import sqlite3

import pytest

from macd_trader import sqlite_journal as sj

MOUNTS = """overlay / overlay rw 0 0
/dev/vda1 /app/data ext4 rw,relatime 0 0
/run/host_mark/Users /app/runtime fakeowner rw,nosuid 0 0
"""


def test_the_filesystem_is_the_longest_matching_mount(tmp_path):
    mounts = tmp_path / "mounts"
    mounts.write_text(MOUNTS)
    assert sj.filesystem_of("/app/data/historical.sqlite3", str(mounts)) == "ext4"
    assert sj.filesystem_of("/app/runtime/settings.json", str(mounts)) == "fakeowner"
    assert sj.filesystem_of("/tmp/x.sqlite3", str(mounts)) == "overlay"
    assert sj.filesystem_of("/app/datastore/x", str(mounts)) == "overlay"      # a prefix is not a parent


def test_wal_only_where_the_filesystem_is_local(monkeypatch):
    monkeypatch.delenv("MACD_SQLITE_JOURNAL", raising=False)
    sj.journal_mode.cache_clear()
    monkeypatch.setattr(sj, "filesystem_of", lambda path: {"/a/local.db": "ext4"}.get(path, "fakeowner"))
    assert sj.journal_mode("/a/local.db") == "wal"
    assert sj.journal_mode("/b/bind.db") == "delete"
    sj.journal_mode.cache_clear()
    monkeypatch.setenv("MACD_SQLITE_JOURNAL", "delete")
    assert sj.journal_mode("/a/local.db") == "delete"
    sj.journal_mode.cache_clear()


@pytest.mark.parametrize("override, mode, sync", [("wal", "wal", 1), ("delete", "delete", 2)])
def test_configure_sets_journal_and_sync(tmp_path, monkeypatch, override, mode, sync):
    sj.journal_mode.cache_clear()
    monkeypatch.setenv("MACD_SQLITE_JOURNAL", override)
    path = tmp_path / "x.sqlite3"
    connection = sqlite3.connect(path)
    assert sj.configure(connection, str(path)) == mode
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == sync     # NORMAL=1, FULL=2
    connection.close()
    sj.journal_mode.cache_clear()
