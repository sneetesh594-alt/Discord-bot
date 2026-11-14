"""Consistent online SQLite backup, including committed WAL data.

Usage: python scripts/backup.py --database data/aegis.sqlite3 --output backups/aegis.sqlite3
"""

import argparse
import sqlite3
from pathlib import Path


def backup(database: Path, output: Path) -> None:
    if not database.is_file():
        raise ValueError("Source database does not exist")
    if output.exists():
        raise ValueError("Output already exists; choose a new backup filename")
    if database.resolve() == output.resolve():
        raise ValueError("Backup must be a different file")
    output.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(output)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError("Backup integrity check failed")
    finally:
        destination.close()
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        backup(args.database, args.output)
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as error:
        parser.exit(2, f"Backup failed: {error}\n")
    print(f"Verified database backup written to {args.output}")


if __name__ == "__main__":
    main()
