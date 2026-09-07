#!/usr/bin/env python
"""Find (and optionally drop) notes that capture the same source twice.

Groups every note by `urls.dedup_key(source)` -- the same identity the bot now
uses to refuse duplicates -- so a video re-shared with a fresh `?si=` token, or
saved once as youtu.be and once as youtube.com/watch, lands in one group.

In each group the OLDEST note is kept (by `date:` frontmatter, filename as
tiebreak) and the newer ones are dropped along with their companion
`sources/<stem>.source.md` archive.

DRY RUN BY DEFAULT -- nothing changes until you pass --apply.

Usage (from the project root, so config is picked up):
    uv run python scripts/dedupe_vault.py                      # preview
    uv run python scripts/dedupe_vault.py /path/to/vault       # preview a specific vault
    uv run python scripts/dedupe_vault.py --apply              # delete the duplicates

Tip: back up or commit the vault before --apply (it deletes your real notes).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make `second_brain` importable when this file is run as a script (Python puts
# the script's own dir on sys.path, not the project root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import frontmatter

from second_brain.urls import dedup_key
from second_brain.vault import SOURCES_DIR


@dataclass
class Note:
    path: Path
    source: str
    date: str

    @property
    def archive(self) -> Path:
        return self.path.parent / SOURCES_DIR / f"{self.path.stem}.source.md"

    def sort_key(self) -> tuple[str, str]:
        # Undated notes sort last, so a dated note is preferred as the keeper.
        return (self.date or "9999-99-99", self.path.name)


@dataclass
class Group:
    key: str
    keep: Note
    drop: list[Note] = field(default_factory=list)


def read_notes(vault_path: Path) -> list[Note]:
    """Every root-level note that records a `source:` URL."""
    notes: list[Note] = []
    for path in sorted(vault_path.glob("*.md")):
        try:
            post = frontmatter.load(str(path))
        except Exception:
            continue
        source = post.get("source")
        if not isinstance(source, str) or not source.strip():
            continue  # e.g. Home.md, hand-written notes
        date = post.get("date")
        notes.append(Note(path, source.strip(), str(date) if date else ""))
    return notes


def plan_dedupe(vault_path: Path) -> list[Group]:
    """Group notes by source identity; return only the groups with duplicates."""
    by_key: dict[str, list[Note]] = {}
    for note in read_notes(vault_path):
        by_key.setdefault(dedup_key(note.source), []).append(note)

    groups: list[Group] = []
    for key, notes in sorted(by_key.items()):
        if len(notes) < 2:
            continue
        oldest, *rest = sorted(notes, key=Note.sort_key)
        groups.append(Group(key, oldest, rest))
    return groups


def dedupe(vault_path: Path, *, apply: bool = False) -> list[Group]:
    """Plan (and optionally perform) the removal. Safe to re-run (idempotent)."""
    groups = plan_dedupe(vault_path)
    if not apply:
        return groups

    for group in groups:
        for note in group.drop:
            note.path.unlink(missing_ok=True)
            note.archive.unlink(missing_ok=True)
    return groups


def main() -> None:
    parser = argparse.ArgumentParser(description="Drop duplicate notes from the vault.")
    parser.add_argument(
        "vault", nargs="?", help="vault path (default: VAULT_PATH from the environment)"
    )
    parser.add_argument(
        "--apply", action="store_true", help="delete the duplicates (default: dry run)"
    )
    args = parser.parse_args()

    if args.vault:
        vault_path = Path(args.vault).expanduser().resolve()
    else:
        from second_brain.config import ConfigError, Settings

        try:
            vault_path = Settings.from_env().vault_path
        except ConfigError as exc:
            raise SystemExit(f"Config error (is your config present?): {exc}")

    if not vault_path.is_dir():
        raise SystemExit(f"Not a directory: {vault_path}")

    groups = dedupe(vault_path, apply=args.apply)

    verb = "deleted" if args.apply else "would delete"
    for group in groups:
        print(f"\n{group.key}")
        print(f"  keep         {group.keep.date}  {group.keep.path.name}")
        print(f"               {group.keep.source}")
        for note in group.drop:
            print(f"  {verb:<12} {note.date}  {note.path.name}")
            print(f"               {note.source}")

    total = sum(len(g.drop) for g in groups)
    print(f"\nVault: {vault_path}")
    print(f"{len(groups)} duplicate group(s), {total} note(s) {verb}.")

    if not args.apply and total:
        print("\nDRY RUN - nothing changed. Back up the vault, then re-run with --apply.")
    elif not total:
        print("\nNo duplicates found.")


if __name__ == "__main__":
    main()
