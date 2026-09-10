"""Read vault notes as structured cards.

A *card* is the cheap unit -- title plus TL;DR, roughly 100 tokens -- and it is
what gets sent to the model and returned from search. Full notes and archives are
fetched only once a card proves relevant, which is what keeps token cost flat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from second_brain.ask import Note, load_notes
from second_brain.vault import SOURCES_DIR, Vault

TLDR = "TL;DR"
KEY_POINTS = "Key technical points"
PROTOTYPE_IDEAS = "Prototype ideas"


@dataclass
class Card:
    """One note, parsed into the fields a browser or an agent actually wants."""

    note_id: str
    title: str
    tldr: str = ""
    source: str = ""
    date: str = ""
    tags: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    key_points: list[str] = field(default_factory=list)
    prototype_ideas: list[str] = field(default_factory=list)
    path: Path | None = None

    def summary_line(self) -> str:
        """The compact form sent to the model during topic discovery."""
        return f"[{self.note_id}] {self.title} — {self.tldr}".strip()


def load_cards(vault: Vault) -> list[Card]:
    """Every captured note in the vault, parsed into a Card, ordered by note id.

    Notes without a `source:` are skipped -- they are hand-written pages like the
    `Home.md` dashboard, not captured resources, and they would otherwise show up
    as junk topics. Same rule `scripts/dedupe_vault.py` uses.
    """
    cards = [_to_card(n) for n in load_notes(vault) if n.source.strip()]
    cards.sort(key=lambda c: c.note_id)
    return cards


def archive_path(vault: Vault, note_id: str) -> Path:
    """Where the full-text archive for `note_id` lives (may not exist)."""
    return vault.root / SOURCES_DIR / f"{note_id}.source.md"


def _to_card(note: Note) -> Card:
    sections = _sections(note.body)
    return Card(
        note_id=note.path.stem,
        title=note.title,
        tldr=" ".join(sections.get(TLDR, "").split()),
        source=note.source,
        date=note.date,
        tags=list(note.tags),
        topics=list(note.topics),
        key_points=_bullets(sections.get(KEY_POINTS, "")),
        prototype_ideas=_bullets(sections.get(PROTOTYPE_IDEAS, "")),
        path=note.path,
    )


def _sections(body: str) -> dict[str, str]:
    """Split a note body on its `## ` headings into {heading: text}."""
    sections: dict[str, str] = {}
    heading: str | None = None
    lines: list[str] = []
    for line in (body or "").splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections[heading] = "\n".join(lines).strip()
            heading = line[3:].strip()
            lines = []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        sections[heading] = "\n".join(lines).strip()
    return sections


def _bullets(text: str) -> list[str]:
    """The `- ` list items in a section, in order."""
    return [
        stripped[2:].strip()
        for line in text.splitlines()
        if (stripped := line.strip()).startswith("- ") and stripped[2:].strip()
    ]
