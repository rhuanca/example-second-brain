#!/usr/bin/env python
"""Migrate legacy `transcripts/*.transcript.md` to `sources/*.source.md`.

The canonical-archive change (specs/design-canonical-archive.md) renamed the
transcript companion to a generic source archive. This migrates vaults captured
before that:
  - moves transcripts/<stem>.transcript.md -> sources/<stem>.source.md
  - rewrites its frontmatter (title "— transcript" -> "— source", tag
    transcript -> source, adds `kind`)
  - fixes the summary note's link (transcript: -> archive:, ## Transcript ->
    ## Source)

Reads VAULT_PATH from the same .env the bot uses. DRY RUN BY DEFAULT.

Usage (from the project root):
    uv run python scripts/migrate_transcripts_to_sources.py            # preview
    uv run python scripts/migrate_transcripts_to_sources.py --apply    # migrate

Tip: back up or commit the vault before --apply.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import frontmatter

_OLD_SUFFIX = ".transcript.md"


@dataclass
class Item:
    transcript: Path
    archive: Path
    note: Path | None


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def plan(vault: Path) -> list[Item]:
    items: list[Item] = []
    folder = vault / "transcripts"
    if not folder.is_dir():
        return items
    for tpath in sorted(folder.glob(f"*{_OLD_SUFFIX}")):
        stem = tpath.name[: -len(_OLD_SUFFIX)]
        note = vault / f"{stem}.md"
        items.append(
            Item(
                transcript=tpath,
                archive=vault / "sources" / f"{stem}.source.md",
                note=note if note.exists() else None,
            )
        )
    return items


def migrate(vault: Path, *, apply: bool = False) -> Report:
    report = Report()
    for item in plan(vault):
        stem = item.transcript.name[: -len(_OLD_SUFFIX)]
        if item.archive.exists():
            report.skipped.append(f"{item.archive.name} already exists — skipped")
            continue
        report.items.append(item)
        if not apply:
            continue
        item.archive.parent.mkdir(parents=True, exist_ok=True)
        item.archive.write_text(
            _migrate_archive(item.transcript.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
        item.transcript.unlink()
        if item.note is not None:
            item.note.write_text(
                _migrate_note(item.note.read_text(encoding="utf-8"), stem),
                encoding="utf-8",
            )

    if apply:
        folder = vault / "transcripts"
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    return report


def _migrate_archive(text: str) -> str:
    post = frontmatter.loads(text)
    tags = [str(t) for t in (post.get("tags") or [])]
    source_type = next((t for t in tags if t != "transcript"), None)
    post["title"] = str(post.get("title", "")).replace(" — transcript", " — source")
    post["tags"] = ["source" if t == "transcript" else t for t in tags]
    post["kind"] = "transcript" if source_type == "youtube" else "article"
    return frontmatter.dumps(post)


def _migrate_note(text: str, stem: str) -> str:
    post = frontmatter.loads(text)
    if "transcript" in post.metadata:
        post.metadata.pop("transcript")
        post["archive"] = f"[[sources/{stem}.source]]"
    body = post.content
    body = body.replace(
        f"[[transcripts/{stem}.transcript|Full transcript]]",
        f"[[sources/{stem}.source|Full source]]",
    ).replace("## Transcript", "## Source")
    post.content = body
    return frontmatter.dumps(post)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate transcripts/ to sources/.")
    parser.add_argument("--apply", action="store_true", help="perform it (default: dry run)")
    args = parser.parse_args()

    from second_brain.config import ConfigError, Settings

    try:
        vault = Settings.from_env().vault_path
    except ConfigError as exc:
        raise SystemExit(f"Config error (is your .env present?): {exc}")

    report = migrate(vault, apply=args.apply)
    verb = "migrated" if args.apply else "would migrate"
    for item in report.items:
        note = f"  (+ note {item.note.name})" if item.note else ""
        print(f"{verb}: {item.transcript.name} -> sources/{item.archive.name}{note}")
    for msg in report.skipped:
        print(f"skipped: {msg}")

    print(f"\nVault: {vault}")
    print(f"{len(report.items)} archive(s) {verb}.")
    if not args.apply:
        if report.items:
            print("\nDRY RUN — nothing changed. Back up the vault, then re-run with --apply.")
        else:
            print("\nNothing to migrate — no transcripts/*.transcript.md found.")


if __name__ == "__main__":
    main()
