#!/usr/bin/env python
"""Build or update the knowledge base's embedding index.

Embeds every note's card (title + TL;DR + key points) with a local fastembed
model and stores the vectors outside the vault, in KB_INDEX_DIR. Only notes that
are new or changed since the last run are embedded; deleted notes are dropped.

The service also keeps the index current on its own while it runs. This script
exists so the first build -- and the one-time model download -- happens at deploy
time rather than on the first search.

DRY RUN BY DEFAULT -- reports what would change without downloading the model or
writing anything. Pass --apply to embed and save.

Usage (from the project root, so config is picked up):
    uv run python scripts/build_index.py                    # preview
    uv run python scripts/build_index.py /path/to/vault     # preview a vault
    uv run python scripts/build_index.py --apply            # build/update
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `second_brain` importable when this file is run as a script (Python puts
# the script's own dir on sys.path, not the project root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from second_brain.config import ConfigError
from second_brain.kb.config import DEFAULT_EMBED_MODEL, DEFAULT_INDEX_DIR, KbSettings
from second_brain.kb.embeddings import (
    MODELS_DIR,
    EmbeddingIndex,
    FastEmbedder,
    RefreshStats,
    card_text,
    content_hash,
    refresh,
)
from second_brain.kb.notes import Card, load_cards
from second_brain.vault import Vault


def plan(index: EmbeddingIndex | None, cards: list[Card], model: str) -> RefreshStats:
    """What `refresh` would do, computed without an embedder."""
    stored = {}
    if index is not None and index.model == model:
        stored = dict(zip(index.note_ids, index.hashes))
    stats = RefreshStats()
    for card in cards:
        previous = stored.get(card.note_id)
        if previous is None:
            stats.added += 1
        elif previous == content_hash(card_text(card)):
            stats.unchanged += 1
        else:
            stats.changed += 1
    stats.removed = len(set(stored) - {c.note_id for c in cards})
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the embedding index.")
    parser.add_argument(
        "vault", nargs="?", help="vault path (default: VAULT_PATH from the environment)"
    )
    parser.add_argument(
        "--apply", action="store_true", help="embed and save (default: dry run)"
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

    index_dir = settings.index_dir if settings else Path(DEFAULT_INDEX_DIR).expanduser()
    model = settings.embed_model if settings else DEFAULT_EMBED_MODEL

    cards = load_cards(Vault(vault_path))
    index = EmbeddingIndex.load(index_dir)
    print(f"vault: {vault_path}\nindex: {index_dir}\nmodel: {model}")
    if index is not None and index.model != model:
        print(f"stored index used {index.model!r}; everything will be re-embedded")

    if not args.apply:
        _report(plan(index, cards, model), len(cards))
        print("\nDRY RUN - nothing written. Re-run with --apply to embed and save.")
        return

    embedder = FastEmbedder(model, index_dir / MODELS_DIR)
    existed = index is not None
    index, stats = refresh(index, cards, embedder)
    _report(stats, len(cards))
    if stats.dirty or not existed:
        index.save(index_dir)
        print(f"\nSaved {len(index.note_ids)} vector(s) to {index_dir}.")
    else:
        print("\nIndex already up to date.")


def _report(stats: RefreshStats, card_count: int) -> None:
    print(
        f"\n{card_count} note(s) · {stats.added} new · {stats.changed} changed · "
        f"{stats.removed} removed · {stats.unchanged} unchanged"
    )


if __name__ == "__main__":
    main()
