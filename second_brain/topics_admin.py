"""Reading and undoing what the scheduled topic refresh did.

The refresh applies without asking, so the control it offers is afterwards: see
what the taxonomy looks like now, and put the previous one back. Kept out of
`bot.py` because none of it is about Telegram -- the bot is one caller, a script
could be another, and this way it is testable without an update object.
"""

from __future__ import annotations

import os
from pathlib import Path

from second_brain.kb.topics import Taxonomy, load_taxonomy, save_taxonomy

HISTORY_DIR = Path(
    os.environ.get("KB_TOPICS_HISTORY", "~/.local/share/second-brain/topics-history")
).expanduser()

NO_TAXONOMY = (
    "No topics yet. They appear after the first refresh — "
    "scripts/refresh_topics.py, or the timer it installs."
)
NOTHING_TO_UNDO = "Nothing to undo: no earlier taxonomy has been stored."


def snapshots(history: Path | None = None) -> list[Path]:
    """Stored taxonomies, oldest first. The newest is what undo restores."""
    history = Path(history or HISTORY_DIR)
    return sorted(history.glob("*.json")) if history.is_dir() else []


def describe(taxonomy: Taxonomy | None, *, history: Path | None = None) -> str:
    """The current taxonomy as a Telegram message."""
    if taxonomy is None or not taxonomy.topics:
        return NO_TAXONOMY
    lines = [f"🗂 {len(taxonomy.topics)} topics"]
    for i, topic in enumerate(taxonomy.topics, start=1):
        # The first eight are the ones that carry a colour on the map.
        mark = "•" if i <= 8 else "·"
        lines.append(f"{mark} {topic.name} — {len(topic.note_ids)}")
    if taxonomy.unassigned:
        lines.append(f"· not in any topic — {len(taxonomy.unassigned)}")
    stored = snapshots(history)
    lines.append("")
    lines.append(
        f"Last change kept as {stored[-1].stem} — send /topics undo to restore it."
        if stored
        else "No earlier version stored yet."
    )
    return "\n".join(lines)


def undo(
    *, topics_path: Path | None = None, history: Path | None = None, write_notes=None
) -> str:
    """Restore the newest snapshot, rewriting note frontmatter. Returns a message.

    The snapshot is consumed, so a second undo reaches the one before it rather
    than restoring the same taxonomy forever.
    """
    stored = snapshots(history)
    if not stored:
        return NOTHING_TO_UNDO

    newest = stored[-1]
    previous = load_taxonomy(newest)
    if previous is None or not previous.topics:
        newest.unlink(missing_ok=True)
        return "That stored taxonomy could not be read; it has been discarded."

    from second_brain.kb import topics as topics_module

    path = Path(topics_path or topics_module.TOPICS_FILE)
    restored = previous.assignments()
    # Notes filed under a topic that is being rolled back are not in the restored
    # taxonomy at all, so they would keep a `topics:` entry for a topic that no
    # longer exists. They are explicitly cleared.
    current = load_taxonomy(path)
    stale = {
        note_id: [] for note_id in (current.assignments() if current else {}) if note_id not in restored
    }

    save_taxonomy(previous, path)
    changed = write_notes({**restored, **stale}) if write_notes else []
    newest.unlink(missing_ok=True)
    return (
        f"↩️ Restored {len(previous.topics)} topics from {newest.stem} "
        f"({len(changed)} notes rewritten)."
    )
