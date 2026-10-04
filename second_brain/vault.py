"""Obsidian vault: note filenames, Markdown rendering, and flat write + dedup.

Notes live flat at the vault root, organized by tags (topic tags from the
summary plus a source tag). This module is the interface to the vault on disk.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from second_brain.models import Summary
from second_brain.urls import dedup_key

_SLUG_MAX_LEN = 60
_slug_strip_re = re.compile(r"[^a-z0-9]+")

# The canonical Markdown archive of each source is stored here as
# `<note-stem>.source.md`. The `.source.md` marker makes archives findable by
# extension regardless of folder, so a future reorg is a bulk glob.
SOURCES_DIR = "sources"

# Deleted notes move here rather than being unlinked, so a misclick in the web UI
# costs nothing. A dot-folder is invisible to `iter_notes` (which globs the root)
# and to Obsidian, so a trashed note leaves the library in every sense that
# matters while the file is still there.
TRASH_DIR = ".trash"


def slugify(title: str) -> str:
    """Turn a title into a filesystem-safe, lowercase-kebab slug."""
    slug = _slug_strip_re.sub("-", title.lower()).strip("-")
    if len(slug) > _SLUG_MAX_LEN:
        slug = slug[:_SLUG_MAX_LEN].rstrip("-")
    return slug or "untitled"


def note_filename(title: str, date: _dt.date) -> str:
    """Build a date-prefixed Markdown filename, e.g. 2026-06-30-my-article.md."""
    return f"{date.isoformat()}-{slugify(title)}.md"


def render_note(
    summary: Summary, source_url: str, date: _dt.date, *, archive_link: str | None = None
) -> str:
    """Render a Summary into Markdown with YAML frontmatter.

    When `archive_link` (a vault-relative target like
    `sources/2026-07-03-slug.source`) is given, the note links down to the stored
    canonical archive via an `archive:` frontmatter field and a `## Source` section.
    """
    body_sections = [f"## TL;DR\n\n{summary.tldr.strip()}"]

    if summary.key_points:
        points = "\n".join(f"- {p}" for p in summary.key_points)
        body_sections.append(f"## Key technical points\n\n{points}")

    if summary.prototype_ideas:
        ideas = "\n".join(f"- {i}" for i in summary.prototype_ideas)
        body_sections.append(f"## Prototype ideas\n\n{ideas}")

    meta = dict(
        title=summary.title,
        source=source_url,
        date=date.isoformat(),
        tags=list(summary.tags),
    )
    if archive_link:
        meta["archive"] = f"[[{archive_link}]]"
        body_sections.append(f"## Source\n\n[[{archive_link}|Full source]]")

    return frontmatter.dumps(frontmatter.Post("\n\n".join(body_sections), **meta))


def render_archive(
    title: str,
    source_url: str,
    date: _dt.date,
    markdown: str,
    *,
    note_stem: str,
    kind: str = "article",
    source_type: str | None = None,
) -> str:
    """Render the canonical Markdown archive of a source, linked back to its note."""
    tags = ["source"]
    if source_type:
        tags.append(source_type)
    post = frontmatter.Post(
        markdown.strip(),
        title=f"{title} — source",
        source=source_url,
        date=date.isoformat(),
        kind=kind,
        tags=tags,
        note=f"[[{note_stem}]]",
    )
    return frontmatter.dumps(post)


class DuplicateNoteError(Exception):
    """Raised when a note for the same source URL already exists in the vault."""

    def __init__(self, url: str, existing: Path):
        super().__init__(f"URL already saved: {url} -> {existing}")
        self.url = url
        self.existing = existing


@dataclass(frozen=True)
class TrashedNote:
    """One note in the trash: enough to recognise it and put it back."""

    name: str  # the file name inside .trash, which is also the restore handle
    title: str
    source: str = ""
    date: str = ""
    has_archive: bool = False


class Vault:
    """A dedicated Obsidian vault rooted at a directory on disk (flat layout)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def trash_dir(self) -> Path:
        return self.root / TRASH_DIR

    def ensure_folders(self) -> None:
        """Create the vault root directory if it doesn't exist."""
        self.root.mkdir(parents=True, exist_ok=True)

    def iter_notes(self) -> Iterator[Path]:
        """Yield every Markdown note at the vault root."""
        yield from self.root.glob("*.md")

    def find_by_url(self, url: str) -> Path | None:
        """Return the note whose `source` is the same source as `url`, else None.

        Compares by `dedup_key`, not by the stored URL, so a link re-shared with a
        fresh tracking token or in another YouTube URL shape still matches.
        """
        target = dedup_key(url)
        for note in self.iter_notes():
            source = _read_source(note)
            if source and dedup_key(source) == target:
                return note
        return None

    def is_duplicate(self, url: str) -> bool:
        return self.find_by_url(url) is not None

    def write_note(
        self,
        summary: Summary,
        source_url: str,
        date: _dt.date,
        *,
        archive: str | None = None,
        kind: str = "article",
        source_type: str | None = None,
    ) -> Path:
        """Render and write a note flat at the vault root; refuse duplicates.

        When `archive` (the canonical Markdown of the source) is given, it's stored
        as a companion `sources/<note-stem>.source.md` and linked from the note.
        Returns the path of the written note. Raises DuplicateNoteError if a note
        for the same source URL already exists.
        """
        existing = self.find_by_url(source_url)
        if existing is not None:
            raise DuplicateNoteError(source_url, existing)

        self.root.mkdir(parents=True, exist_ok=True)
        path = _unique_path(self.root, note_filename(summary.title, date))
        stem = path.stem

        archive_link = None
        if archive and archive.strip():
            folder = self.root / SOURCES_DIR
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{stem}.source.md").write_text(
                render_archive(
                    summary.title, source_url, date, archive,
                    note_stem=stem, kind=kind, source_type=source_type,
                ),
                encoding="utf-8",
            )
            archive_link = f"{SOURCES_DIR}/{stem}.source"

        path.write_text(
            render_note(summary, source_url, date, archive_link=archive_link),
            encoding="utf-8",
        )
        return path

    # --- trash ------------------------------------------------------------------

    def trash(self, note: Path) -> str | None:
        """Move a note and its archive into the trash. Returns the trashed name.

        None when the note isn't in this vault -- callers pass paths they got from
        the vault itself, and this is the backstop if one ever doesn't.
        """
        note = Path(note)
        if not note.is_file() or note.parent.resolve() != self.root.resolve():
            return None
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(self.trash_dir, note.name)
        archive = self.root / SOURCES_DIR / f"{note.stem}.source.md"
        if archive.is_file():
            archive.rename(self.trash_dir / f"{target.stem}.source.md")
        note.rename(target)
        return target.name

    def trashed(self) -> list[TrashedNote]:
        """What is in the trash, newest first."""
        if not self.trash_dir.is_dir():
            return []
        notes = []
        for path in self.trash_dir.glob("*.md"):
            if path.name.endswith(".source.md"):
                continue  # the companion archive, listed with its note
            notes.append(
                TrashedNote(
                    name=path.name,
                    title=_read_field(path, "title") or path.stem,
                    source=_read_field(path, "source") or "",
                    date=_read_field(path, "date") or "",
                    has_archive=(self.trash_dir / f"{path.stem}.source.md").is_file(),
                )
            )
        notes.sort(key=lambda n: (n.date, n.name), reverse=True)
        return notes

    def restore(self, name: object) -> Path | None:
        """Put a trashed note (and its archive) back. None if there is no such note.

        `name` comes from a web form, so it is matched against the listing rather
        than joined into a path.
        """
        if not isinstance(name, str) or name not in {n.name for n in self.trashed()}:
            return None
        source = self.trash_dir / name
        # A replacement may have been captured since; never overwrite it.
        target = self._free_name(name)
        archive = self.trash_dir / f"{source.stem}.source.md"
        if archive.is_file():
            folder = self.root / SOURCES_DIR
            folder.mkdir(parents=True, exist_ok=True)
            archive.rename(folder / f"{target.stem}.source.md")
        source.rename(target)
        return target

    def _free_name(self, filename: str) -> Path:
        """A root path whose note *and* archive names are both free.

        An archive is found at `sources/<note stem>.source.md`, so the pair has to
        move together: a free note name beside an occupied archive name would
        restore a note pointing at someone else's text.
        """
        stem = Path(filename).stem
        folder = self.root / SOURCES_DIR
        for suffix in ["", *[f"-{n}" for n in range(2, 1000)]]:
            note = self.root / f"{stem}{suffix}.md"
            if not note.exists() and not (folder / f"{stem}{suffix}.source.md").exists():
                return note
        raise FileExistsError(f"Too many filename collisions for {filename}")

    def empty_trash(self) -> int:
        """Delete everything in the trash for good. Returns how many notes went."""
        if not self.trash_dir.is_dir():
            return 0
        count = len(self.trashed())
        for path in self.trash_dir.glob("*"):
            if path.is_file():
                path.unlink()
        return count


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
    return _read_field(note, "source")


def _read_field(note: Path, field: str) -> str | None:
    try:
        post = frontmatter.load(str(note))
    except Exception:
        return None
    value = post.get(field) if field != "title" else (post.get("title") or _heading(post.content))
    return str(value) if value is not None and not isinstance(value, (list, dict)) else None


def _heading(body: str) -> str | None:
    """The note's `# ` heading, which is where the title lives in our notes."""
    for line in (body or "").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None
