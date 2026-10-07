#!/usr/bin/env python3
"""Take a consistent copy of the knowledge base's SQLite database.

`cp state.db backup/` while the service is running can capture a torn file: the
database is in WAL mode, so the newest writes live in a sidecar that a plain copy
may miss or catch half-written. SQLite's own backup API reads a consistent view
instead, which is the whole reason this is a script and not a `cp` in bash.

    uv run python scripts/snapshot_state_db.py <source.db> <destination.db>

The destination is replaced each run; `backup.sh` points it at the staging
directory it then hands to restic.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def snapshot(src: Path, dst: Path) -> Path:
    """Copy a live SQLite database to `dst`, consistently. Returns `dst`."""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise FileNotFoundError(f"no database at {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # A partial copy from an interrupted run must never be mistaken for a good
    # one, so the backup lands beside the target and is moved into place.
    staged = dst.with_name(dst.name + ".partial")
    staged.unlink(missing_ok=True)
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source, sqlite3.connect(
        staged
    ) as target:
        source.backup(target)
    staged.replace(dst)
    return dst


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="the live database")
    parser.add_argument("destination", type=Path, help="where to write the copy")
    args = parser.parse_args()

    try:
        written = snapshot(args.source, args.destination)
    except (FileNotFoundError, sqlite3.Error) as exc:
        raise SystemExit(f"snapshot failed: {exc}") from None
    print(f"{written} ({written.stat().st_size:,} bytes)", file=sys.stdout)


if __name__ == "__main__":
    main()
