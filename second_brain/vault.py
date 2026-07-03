"""Obsidian vault: note filenames, Markdown rendering, and flat write + dedup.

Notes live flat at the vault root, organized by tags (topic tags from the
summary plus a source tag). This module is the interface to the vault on disk.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Iterator
from pathlib import Path

import frontmatter

from second_brain.models import Summary
from second_brain.urls import normalize_url

_SLUG_MAX_LEN = 60
_slug_strip_re = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Turn a title into a filesystem-safe, lowercase-kebab slug."""
    slug = _slug_strip_re.sub("-", title.lower()).strip("-")
    if len(slug) > _SLUG_MAX_LEN:
        slug = slug[:_SLUG_MAX_LEN].rstrip("-")
    return slug or "untitled"


def note_filename(title: str, date: _dt.date) -> str:
    """Build a date-prefixed Markdown filename, e.g. 2026-06-30-my-article.md."""
    return f"{date.isoformat()}-{slugify(title)}.md"


def render_note(summary: Summary, source_url: str, date: _dt.date) -> str:
    """Render a Summary into Markdown with YAML frontmatter."""
    body_sections = [f"## TL;DR\n\n{summary.tldr.strip()}"]

    if summary.key_points:
        points = "\n".join(f"- {p}" for p in summary.key_points)
        body_sections.append(f"## Key technical points\n\n{points}")

    if summary.prototype_ideas:
        ideas = "\n".join(f"- {i}" for i in summary.prototype_ideas)
        body_sections.append(f"## Prototype ideas\n\n{ideas}")

    post = frontmatter.Post(
        "\n\n".join(body_sections),
        title=summary.title,
        source=source_url,
        date=date.isoformat(),
        tags=list(summary.tags),
    )
    return frontmatter.dumps(post)


class DuplicateNoteError(Exception):
    """Raised when a note for the same source URL already exists in the vault."""

    def __init__(self, url: str, existing: Path):
        super().__init__(f"URL already saved: {url} -> {existing}")
        self.url = url
        self.existing = existing


class Vault:
    """A dedicated Obsidian vault rooted at a directory on disk (flat layout)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def ensure_folders(self) -> None:
        """Create the vault root directory if it doesn't exist."""
        self.root.mkdir(parents=True, exist_ok=True)

    def iter_notes(self) -> Iterator[Path]:
        """Yield every Markdown note at the vault root."""
        yield from self.root.glob("*.md")

    def find_by_url(self, url: str) -> Path | None:
        """Return the note whose `source` matches `url` (normalized), else None."""
        target = normalize_url(url)
        for note in self.iter_notes():
            source = _read_source(note)
            if source and normalize_url(source) == target:
                return note
        return None

    def is_duplicate(self, url: str) -> bool:
        return self.find_by_url(url) is not None

    def write_note(
        self, summary: Summary, source_url: str, date: _dt.date
    ) -> Path:
        """Render and write a note flat at the vault root; refuse duplicates.

        Returns the path of the written note. Raises DuplicateNoteError if a note
        for the same source URL already exists.
        """
        existing = self.find_by_url(source_url)
        if existing is not None:
            raise DuplicateNoteError(source_url, existing)

        self.root.mkdir(parents=True, exist_ok=True)
        path = _unique_path(self.root, note_filename(summary.title, date))
        path.write_text(render_note(summary, source_url, date), encoding="utf-8")
        return path


def _unique_path(folder: Path, filename: str) -> Path:
    """Return a non-colliding path in `folder`, suffixing -2, -3, ... if needed.

    Two different articles can share a title+date and thus a slug; this keeps both
    notes instead of one silently overwriting the other.
    """
    path = folder / filename
    if not path.exists():
        return path
    stem = path.stem
    for n in range(2, 1000):
        candidate = folder / f"{stem}-{n}.md"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Too many filename collisions for {filename}")


def _read_source(note: Path) -> str | None:
    try:
        post = frontmatter.load(str(note))
    except Exception:
        return None
    source = post.get("source")
    return source if isinstance(source, str) else None
