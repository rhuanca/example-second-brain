#!/usr/bin/env python
"""Discover the vault's topics and write them into each note's frontmatter.

Sends every note's card (title + TL;DR) to Claude in one call and asks how the
library actually clusters, then records the result two ways:

  - `second_brain/kb/topics.json` -- the taxonomy itself, versioned in git so the
    way your interests shift is visible in the history.
  - a `topics:` list in each note's frontmatter, so Obsidian can search and group
    by it on mobile. Free-form `tags:` are left untouched.

Re-running refines the existing taxonomy rather than replacing it: topics may be
merged, split, added or retired, but their ids stay put so links do not rot.

DRY RUN BY DEFAULT -- nothing changes until you pass --apply.

Usage (from the project root, so config is picked up):
    uv run python scripts/discover_topics.py                    # preview
    uv run python scripts/discover_topics.py /path/to/vault     # preview a vault
    uv run python scripts/discover_topics.py --apply            # write it

Run this on the machine the vault lives on. Writing topics into a synced copy
while the collector adds notes on the server invites a conflict.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `second_brain` importable when this file is run as a script (Python puts
# the script's own dir on sys.path, not the project root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import frontmatter

from second_brain.config import ConfigError
from second_brain.kb.config import DEFAULT_MODEL, KbSettings
from second_brain.kb.notes import load_cards
from second_brain.kb.topics import (
    Taxonomy,
    load_taxonomy as load_taxonomy_from,
    discover,
    health,
    load_taxonomy,
    save_taxonomy,
    suggest_target,
)
from second_brain.vault import Vault


def write_topics(vault_root: Path, assignments: dict[str, list[str]]) -> list[Path]:
    """Set `topics:` on each assigned note. Returns the paths actually changed."""
    changed = []
    for note_id, topic_ids in sorted(assignments.items()):
        path = vault_root / f"{note_id}.md"
        if not path.exists():
            continue
        post = frontmatter.load(str(path))
        if list(post.get("topics") or []) == topic_ids:
            continue  # already correct; leave the file alone
        post["topics"] = topic_ids
        path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
        changed.append(path)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover vault topics.")
    parser.add_argument(
        "vault", nargs="?", help="vault path (default: VAULT_PATH from the environment)"
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the topics (default: dry run)"
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="ignore the stored taxonomy and start over (ids will change)",
    )
    parser.add_argument("--target", type=int, help="how many topics to aim for")
    parser.add_argument(
        "--from-stored",
        action="store_true",
        help="use the committed topics.json instead of calling Claude (no API key "
        "needed) -- how you apply a reviewed taxonomy on the vault server",
    )
    parser.add_argument(
        "--no-notes",
        action="store_true",
        help="write only topics.json, leaving note frontmatter alone",
    )
    parser.add_argument(
        "--model",
        help="override ANTHROPIC_MODEL (discovery is rare and reasoning-heavy, so a "
        "stronger model than the bot's default is usually worth it)",
    )
    args = parser.parse_args()

    try:
        settings = KbSettings.from_env()
    except ConfigError as exc:
        if not args.vault:
            raise SystemExit(f"Config error (is your config present?): {exc}")
        settings = None

    vault_path = (
        Path(args.vault).expanduser().resolve() if args.vault else settings.vault_path
    )
    if not vault_path.is_dir():
        raise SystemExit(f"Not a directory: {vault_path}")

    cards = load_cards(Vault(vault_path))
    if not cards:
        raise SystemExit(f"No notes with a source: found in {vault_path}")

    if args.from_stored:
        taxonomy = load_taxonomy()
        if taxonomy is None or not taxonomy.topics:
            raise SystemExit(
                f"No stored taxonomy in {TOPICS_LABEL}. Run discovery first "
                "(without --from-stored) and commit the result."
            )
        print(
            f"{len(cards)} notes · applying {len(taxonomy.topics)} stored topic(s) "
            "· no API call"
        )
    else:
        existing = None if args.fresh else load_taxonomy()
        target = args.target or suggest_target(len(cards))
        print(
            f"{len(cards)} notes · aiming for {target} topics · "
            f"{'refining ' + str(len(existing.topics)) + ' existing' if existing else 'starting fresh'}"
        )

        model = args.model or (settings.anthropic_model if settings else DEFAULT_MODEL)
        print(f"model: {model}")

        taxonomy = discover(
            cards,
            model=model,
            api_key=settings.anthropic_api_key if settings else None,
            existing=existing,
            target=target,
        )

    _report(taxonomy, len(cards))

    if not args.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to save.")
        return

    wrote = []
    if not args.from_stored:
        save_taxonomy(taxonomy)
        wrote.append(TOPICS_LABEL)
    if args.no_notes:
        print(f"\nWrote {TOPICS_LABEL}. Notes untouched (--no-notes).")
        return

    changed = write_topics(vault_path, taxonomy.assignments())
    prefix = f"Wrote {' and '.join(wrote)} · " if wrote else ""
    print(f"\n{prefix}updated {len(changed)} note(s).")

    stale = set(taxonomy.assignments()) - {c.note_id for c in cards}
    missing = {c.note_id for c in cards} - set(taxonomy.assignments())
    if missing:
        print(
            f"{len(missing)} note(s) are newer than the taxonomy and got no topics; "
            "re-run discovery to fold them in."
        )
    if stale:
        print(f"{len(stale)} note(s) in the taxonomy are not in this vault.")


TOPICS_LABEL = "second_brain/kb/topics.json"


def _report(taxonomy: Taxonomy, card_count: int) -> None:
    for topic in taxonomy.topics:
        print(f"\n  {topic.name}  ({len(topic.note_ids)})  [{topic.id}]")
        if topic.description:
            print(f"    {topic.description}")

    assigned = len(taxonomy.assignments())
    print(
        f"\n{len(taxonomy.topics)} topic(s) · {assigned}/{card_count} notes assigned "
        f"· {len(taxonomy.unassigned)} unassigned"
    )
    for problem in health(taxonomy, card_count):
        print(f"  ! {problem}")


if __name__ == "__main__":
    main()
