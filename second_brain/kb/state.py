"""What the reader did with a note: stars, archive flags, and reads.

This is the user's own data, not derived from the vault, so it lives in a small
SQLite file of its own -- outside the vault (which the service may only read)
and outside the index directory (which is safe to delete and rebuild).

State is keyed by note id (the file stem). The collector never renames notes,
so ids are stable; rows for notes that no longer exist are simply never asked for.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CHANNELS = ("web", "mcp")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    note_id    TEXT PRIMARY KEY,
    starred    INTEGER NOT NULL DEFAULT 0,
    archived   INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reads (
    note_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS reads_note ON reads (note_id);
"""


@dataclass(frozen=True)
class ReadStats:
    web: int = 0
    mcp: int = 0
    last_at: float | None = None  # unix seconds

    @property
    def total(self) -> int:
        return self.web + self.mcp


class NoteState:
    """Stars, archive flags and read counts, persisted in one SQLite file."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            # WAL lets a page view record a read while another request reads.
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    def _connect(self) -> closing[sqlite3.Connection]:
        # One short-lived connection per call: requests arrive on many threads.
        return closing(sqlite3.connect(self.path, timeout=5))

    def now(self) -> float:
        """The store's clock, so "how long ago" agrees with the recorded times."""
        return self._clock()

    # --- flags -------------------------------------------------------------------

    def set_starred(self, note_id: str, on: bool) -> None:
        self._set_flag(note_id, "starred", on)

    def set_archived(self, note_id: str, on: bool) -> None:
        self._set_flag(note_id, "archived", on)

    def starred(self) -> set[str]:
        return self._flagged("starred")

    def archived(self) -> set[str]:
        return self._flagged("archived")

    def _set_flag(self, note_id: str, column: str, on: bool) -> None:
        # `column` is one of two literals above, never caller input.
        with self._connect() as db, db:
            db.execute(
                f"INSERT INTO flags (note_id, {column}, updated_at) VALUES (?, ?, ?) "
                f"ON CONFLICT (note_id) DO UPDATE SET {column} = excluded.{column}, "
                "updated_at = excluded.updated_at",
                (note_id, int(bool(on)), self._clock()),
            )

    def _flagged(self, column: str) -> set[str]:
        with self._connect() as db:
            rows = db.execute(f"SELECT note_id FROM flags WHERE {column} = 1").fetchall()
        return {row[0] for row in rows}

    # --- reads -------------------------------------------------------------------

    def record_read(self, note_id: str, channel: str) -> None:
        if channel not in CHANNELS:
            raise ValueError(f"unknown channel {channel!r}")
        with self._connect() as db, db:
            db.execute(
                "INSERT INTO reads (note_id, channel, at) VALUES (?, ?, ?)",
                (note_id, channel, self._clock()),
            )

    def read_stats(self) -> dict[str, ReadStats]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT note_id, "
                "SUM(channel = 'web'), SUM(channel = 'mcp'), MAX(at) "
                "FROM reads GROUP BY note_id"
            ).fetchall()
        return {note_id: ReadStats(web, mcp, last) for note_id, web, mcp, last in rows}
