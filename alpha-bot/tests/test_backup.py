import sqlite3

import pytest

from scripts.backup import backup


def test_online_backup_includes_wal_and_never_overwrites(tmp_path):
    source = tmp_path / "live.sqlite3"
    destination = tmp_path / "backups" / "copy.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE example (value TEXT)")
        db.execute("INSERT INTO example VALUES ('committed data')")
        db.commit()
        backup(source, destination)
        with sqlite3.connect(destination) as restored:
            assert restored.execute("SELECT value FROM example").fetchone()[0] == "committed data"
        with pytest.raises(ValueError, match="already exists"):
            backup(source, destination)


def test_backup_of_missing_source_does_not_create_an_empty_database(tmp_path):
    source, destination = tmp_path / "missing.sqlite3", tmp_path / "backup.sqlite3"
    with pytest.raises(ValueError):
        backup(source, destination)
    assert not source.exists() and not destination.exists()
