#!/usr/bin/env python
"""Flatten legacy PARA folders into the flat vault layout.

Moves every Markdown note out of Projects/Areas/Resources/Archives up to the
vault root (the new flat layout), then removes the now-empty folders. Reads
VAULT_PATH from the same .env the bot uses.

DRY RUN BY DEFAULT — nothing changes until you pass --apply.

Usage (from the project root, so .env is picked up):
    uv run python scripts/flatten_vault.py                 # preview
    uv run python scripts/flatten_vault.py --apply         # move the files
    uv run python scripts/flatten_vault.py --apply --strip-para   # also drop `para:` frontmatter

Tip: back up or commit the vault before --apply (moves touch your real notes).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

# The four legacy PARA folder names the bot used to write into.
PARA_FOLDERS = ("Projects", "Areas", "Resources", "Archives")


@dataclass
class Move:
    src: Path
    dst: Path


@dataclass
class Report:
    moves: list[Move] = field(default_factory=list)
    removed_dirs: list[Path] = field(default_factory=list)


def plan_moves(vault_path: Path) -> list[Move]:
    """Compute collision-safe destinations for every note under the PARA folders."""
    moves: list[Move] = []
    taken: set[Path] = set()
    for name in PARA_FOLDERS:
        folder = vault_path / name
        if not folder.is_dir():
            continue
        for note in sorted(folder.rglob("*.md")):
            dst = _unique_dest(vault_path, note.name, taken)
            taken.add(dst)
            moves.append(Move(note, dst))
    return moves


def flatten(vault_path: Path, *, apply: bool = False, strip_para: bool = False) -> Report:
    """Plan (and optionally perform) the flatten. Safe to re-run (idempotent)."""
    report = Report(moves=plan_moves(vault_path))
    if not apply:
        return report

    for mv in report.moves:
        if strip_para:
            _move_and_clean(mv.src, mv.dst)
        else:
            mv.src.rename(mv.dst)

    for name in PARA_FOLDERS:
        folder = vault_path / name
        if folder.is_dir():
            _remove_empty_dirs(folder)
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
                report.removed_dirs.append(folder)
    return report


def _unique_dest(root: Path, filename: str, taken: set[Path]) -> Path:
    """A destination in `root` that collides with neither disk nor a planned move."""
    candidate = root / filename
    if not candidate.exists() and candidate not in taken:
        return candidate
    stem = candidate.stem
    n = 2
    while True:
        candidate = root / f"{stem}-{n}.md"
        if not candidate.exists() and candidate not in taken:
            return candidate
        n += 1


def _move_and_clean(src: Path, dst: Path) -> None:
    post = frontmatter.load(str(src))
    post.metadata.pop("para", None)
    dst.write_text(frontmatter.dumps(post), encoding="utf-8")
    src.unlink()


def _remove_empty_dirs(folder: Path) -> None:
    """Remove empty subdirectories bottom-up (leaves the top folder to caller)."""
    for child in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description="Flatten legacy PARA folders in the vault.")
    parser.add_argument("--apply", action="store_true", help="perform the moves (default: dry run)")
    parser.add_argument("--strip-para", action="store_true", help="also remove the unused `para:` frontmatter")
    args = parser.parse_args()

    from second_brain.config import ConfigError, Settings

    try:
        vault_path = Settings.from_env().vault_path
    except ConfigError as exc:
        raise SystemExit(f"Config error (is your .env present?): {exc}")

    report = flatten(vault_path, apply=args.apply, strip_para=args.strip_para)

    verb = "moved" if args.apply else "would move"
    for mv in report.moves:
        print(f"{verb}: {mv.src.relative_to(vault_path)}  ->  {mv.dst.name}")

    print(f"\nVault: {vault_path}")
    print(f"{len(report.moves)} note(s) {verb}.")
    if report.removed_dirs:
        print(f"Removed {len(report.removed_dirs)} empty PARA folder(s).")

    if not args.apply:
        if report.moves:
            print("\nDRY RUN — nothing changed. Back up or commit the vault, then re-run with --apply.")
        else:
            print("\nNothing to flatten — no notes found in PARA folders.")


if __name__ == "__main__":
    main()
