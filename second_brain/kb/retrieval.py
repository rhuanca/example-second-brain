"""Hybrid retrieval over the vault: narrow by topic, then rank by meaning.

`Library` is the one object the service holds. It reads the vault off disk,
keeps the embedding index in step with it, and answers searches. It is also the
only place a caller-supplied note id is turned into a file, and it does that by
looking the id up in the notes it enumerated -- never by joining the id into a
path. Everything internet-facing goes through `card()` and `archive_text()`.

The collector keeps writing notes while this runs, so staleness is checked
cheaply (file names and mtimes) at most once per `ttl`, and the expensive work --
parsing and embedding -- only happens when that listing actually changed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from second_brain.kb.embeddings import Embedder, EmbeddingIndex, RefreshStats, refresh
from second_brain.kb.notes import Card, archive_path, load_cards
from second_brain.kb.topics import Taxonomy, Topic, load_taxonomy
from second_brain.kb.visuals import Point, map_layout
from second_brain.vault import Vault

# bge-small cosines sit in a narrow, high band: on a sample vault unrelated notes
# scored 0.53-0.61 and the right note 0.73-0.84. Recalibrate on the real vault;
# both are overridable.
TOPIC_THRESHOLD = 0.7
FALLBACK_THRESHOLD = 0.65
TOPICS_PER_QUERY = 2
DEFAULT_TTL = 30.0


@dataclass
class Hit:
    card: Card
    score: float


class Library:
    """The vault as the knowledge-base service sees it: cards, topics, an index."""

    def __init__(
        self,
        vault: Vault,
        index_dir: Path,
        embedder: Embedder,
        *,
        taxonomy_loader: Callable[[], Taxonomy | None] = load_taxonomy,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = DEFAULT_TTL,
        topic_threshold: float = TOPIC_THRESHOLD,
        fallback_threshold: float = FALLBACK_THRESHOLD,
    ):
        self.vault = vault
        self.index_dir = Path(index_dir)
        self.embedder = embedder
        self._taxonomy_loader = taxonomy_loader
        self._clock = clock
        self._ttl = ttl
        self._topic_threshold = topic_threshold
        self._fallback_threshold = fallback_threshold

        self._lock = threading.RLock()
        self._root = Path(vault.root).resolve()
        self._cards: dict[str, Card] = {}
        self._index: EmbeddingIndex | None = None
        self._signature: tuple | None = None
        self._checked_at: float | None = None
        self._taxonomy = Taxonomy()
        self._topic_key: tuple | None = None
        self._topic_vectors = np.zeros((0, 0), dtype=np.float32)
        self._version = 0  # bumps whenever the index is replaced
        self._map_cache: tuple[tuple, list] | None = None

    # --- keeping up with the vault -------------------------------------------

    def refresh_if_stale(self, *, force: bool = False) -> RefreshStats | None:
        """Re-read the vault if its listing changed. Returns stats when it did."""
        with self._lock:
            now = self._clock()
            if (
                not force
                and self._checked_at is not None
                and now - self._checked_at < self._ttl
            ):
                return None
            self._checked_at = now
            self._taxonomy = self._taxonomy_loader() or Taxonomy()

            signature = self._listing_signature()
            if not force and signature == self._signature:
                return None

            cards = load_cards(self.vault)
            if self._index is None:
                self._index = EmbeddingIndex.load(self.index_dir)
            index, stats = refresh(self._index, cards, self.embedder)
            if stats.dirty or not (self.index_dir / "manifest.json").exists():
                index.save(self.index_dir)

            self._index = index
            self._cards = {card.note_id: card for card in cards}
            self._signature = signature
            self._version += 1
            return stats

    def _listing_signature(self) -> tuple:
        entries = []
        for path in self.vault.iter_notes():
            try:
                entries.append((path.name, path.stat().st_mtime_ns))
            except OSError:
                continue
        return tuple(sorted(entries))

    # --- lookups (the only way ids become files) ------------------------------

    def cards(self) -> list[Card]:
        with self._lock:
            return list(self._cards.values())

    def card(self, note_id: object) -> Card | None:
        """The card for a known note id, or None. Never builds a path from input."""
        if not isinstance(note_id, str):
            return None
        with self._lock:
            card = self._cards.get(note_id)
        if card is None or card.path is None or not self._contained(card.path):
            return None
        return card

    def archive_text(self, note_id: object) -> str | None:
        """The full-text archive for a known note, or None."""
        card = self.card(note_id)
        if card is None:
            return None
        # `card.note_id` came from the enumerated listing, not from the caller.
        path = archive_path(self.vault, card.note_id)
        if not path.is_file() or not self._contained(path):
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _contained(self, path: Path) -> bool:
        """False for anything that resolves outside the vault (e.g. a symlink)."""
        try:
            return Path(path).resolve().is_relative_to(self._root)
        except OSError:
            return False

    # --- topics ----------------------------------------------------------------

    def topics(self) -> list[Topic]:
        with self._lock:
            return list(self._taxonomy.topics)

    def topics_for(self, card: Card) -> list[str]:
        """A note's topics: the stored taxonomy first, its own frontmatter second."""
        with self._lock:
            assigned = self._taxonomy.topics_for(card.note_id)
        return assigned or list(card.topics)

    def notes_in_topic(self, topic_id: str) -> list[Card]:
        return [c for c in self.cards() if topic_id in self.topics_for(c)]

    # --- the map ---------------------------------------------------------------

    def map_points(self, width: float, height: float) -> list[Point]:
        """Every note placed on a 2D map by similarity. Recomputed only when the
        index changes, not per request."""
        with self._lock:
            key = (self._version, width, height)
            if self._map_cache is None or self._map_cache[0] != key:
                index = self._index
                if index is None or not index.note_ids:
                    points = []
                else:
                    points = map_layout(index.note_ids, index.vectors, width, height)
                points = [p for p in points if p.note_id in self._cards]
                self._map_cache = (key, points)
            return list(self._map_cache[1])

    # --- search -----------------------------------------------------------------

    def search(self, query: str, *, topic: str | None = None, limit: int = 10) -> list[Hit]:
        """Rank notes against `query`.

        With an explicit `topic`, only that topic's notes are ranked. Otherwise the
        query picks up to two topics it resembles; their notes rank first, and the
        rest of the vault fills the remaining slots. When no topic resembles the
        query well enough, it is a plain ranking over everything.
        """
        query = (query or "").strip()
        if not query or limit <= 0:
            return []

        with self._lock:
            index = self._index
            cards = dict(self._cards)
        if index is None or not index.note_ids or index.vectors.size == 0:
            return []

        q = self.embedder.embed_query(query)
        scores = index.vectors @ q
        ranked = [
            Hit(cards[note_id], float(score))
            for note_id, score in zip(index.note_ids, scores)
            if note_id in cards
        ]
        ranked.sort(key=lambda hit: (-hit.score, hit.card.note_id))

        if topic is not None:
            return [h for h in ranked if topic in self.topics_for(h.card)][:limit]

        preferred = self._matching_topics(q)
        if not preferred:
            return ranked[:limit]

        in_topic = [h for h in ranked if preferred & set(self.topics_for(h.card))]
        if not in_topic or in_topic[0].score < self._fallback_threshold:
            return ranked[:limit]

        chosen = in_topic[:limit]
        seen = {h.card.note_id for h in chosen}
        chosen += [h for h in ranked if h.card.note_id not in seen][: limit - len(chosen)]
        return chosen

    def _matching_topics(self, q: np.ndarray) -> set[str]:
        topics = self.topics()
        if not topics:
            return set()
        vectors = self._vectors_for(topics)
        sims = vectors @ q
        order = np.argsort(-sims)[:TOPICS_PER_QUERY]
        return {topics[i].id for i in order if sims[i] >= self._topic_threshold}

    def _vectors_for(self, topics: list[Topic]) -> np.ndarray:
        key = tuple((t.id, t.name, t.description) for t in topics)
        with self._lock:
            if key != self._topic_key:
                self._topic_vectors = self.embedder.embed_passages(
                    [f"{t.name}: {t.description}".strip(": ") for t in topics]
                )
                self._topic_key = key
            return self._topic_vectors
