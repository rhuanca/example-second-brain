"""Saved chats: the conversations themselves, so one can be reopened later.

The browser used to hold the conversation (`sessionStorage`) and send it back
each turn, so a chat died with the tab. It lives here instead -- in the same
SQLite file as `state.py`, because both hold the reader's own data, which is why
`DEPLOY.md` says to back that file up.

Ids are generated here, never taken from a caller, and checked before any lookup:
the same rule `Library.card()` follows for note ids.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# How an assistant message ended. A stopped or failed answer is kept: a long
# answer interrupted near the end is still worth reading.
DONE, STOPPED, ERROR = "done", "stopped", "error"

TITLE_CHARS = 60
RECENT_LIMIT = 30

_ID = re.compile(r"^[0-9a-f]{32}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    conversation_id TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    source_ids      TEXT NOT NULL DEFAULT '[]',
    cited           TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'done',
    at              REAL NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);
"""


@dataclass
class Message:
    role: str
    content: str
    source_ids: list[str] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
    status: str = DONE
    at: float = 0.0


@dataclass
class Conversation:
    id: str
    title: str
    created_at: float
    updated_at: float
    messages: list[Message] = field(default_factory=list)


def title_from(question: str) -> str:
    """A chat's title: the first question, shortened on a word boundary."""
    text = " ".join((question or "").split())
    if not text:
        return "New chat"
    if len(text) <= TITLE_CHARS:
        return text
    cut = text[: TITLE_CHARS + 1]
    head, _, tail = cut.rpartition(" ")
    return (head or cut[:TITLE_CHARS]).rstrip(" ,.;:") + "…"


class ChatStore:
    """Conversations and their messages, persisted in one SQLite file."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    def _connect(self) -> closing[sqlite3.Connection]:
        return closing(sqlite3.connect(self.path, timeout=5))

    # --- conversations -----------------------------------------------------------

    def create(self, title: str) -> str:
        """Start a conversation; returns its id."""
        conversation_id = uuid.uuid4().hex
        now = self._clock()
        with self._connect() as db, db:
            db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, title or "New chat", now, now),
            )
        return conversation_id

    def get(self, conversation_id: object) -> Conversation | None:
        """One conversation with its messages in order, or None."""
        if not _known_shape(conversation_id):
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            messages = db.execute(
                "SELECT role, content, source_ids, cited, status, at FROM messages "
                "WHERE conversation_id = ? ORDER BY seq",
                (conversation_id,),
            ).fetchall()
        return Conversation(
            *row,
            messages=[
                Message(role, content, json.loads(sources), json.loads(cited), status, at)
                for role, content, sources, cited, status, at in messages
            ],
        )

    def recent(self, limit: int = RECENT_LIMIT) -> list[Conversation]:
        """Conversations, most recently active first (no messages loaded)."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "ORDER BY updated_at DESC, id LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [Conversation(*row) for row in rows]

    def rename(self, conversation_id: object, title: str) -> bool:
        title = " ".join((title or "").split())[:TITLE_CHARS] or "New chat"
        if not _known_shape(conversation_id):
            return False
        with self._connect() as db, db:
            changed = db.execute(
                "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
            ).rowcount
        return bool(changed)

    def delete(self, conversation_id: object) -> bool:
        if not _known_shape(conversation_id):
            return False
        with self._connect() as db, db:
            changed = db.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            ).rowcount
            db.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        return bool(changed)

    # --- messages ----------------------------------------------------------------

    def append(
        self,
        conversation_id: object,
        role: str,
        content: str,
        *,
        source_ids: list[str] | None = None,
        cited: list[str] | None = None,
        status: str = DONE,
    ) -> bool:
        """Add one message; False if the conversation is unknown.

        Also bumps `updated_at`, which is the order chats are listed in.
        """
        if not _known_shape(conversation_id):
            return False
        now = self._clock()
        with self._connect() as db, db:
            if db.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone() is None:
                return False
            seq = db.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO messages "
                "(conversation_id, seq, role, content, source_ids, cited, status, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    seq,
                    role,
                    content,
                    json.dumps(list(source_ids or [])),
                    json.dumps(list(cited or [])),
                    status,
                    now,
                ),
            )
            db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
            )
        return True


def _known_shape(conversation_id: object) -> bool:
    """Ids are ours (uuid4 hex), so anything else is never looked up."""
    return isinstance(conversation_id, str) and bool(_ID.match(conversation_id))
