"""A local embedding index over note cards.

Each card (title + TL;DR + key points) becomes one normalised vector, computed on
this machine by fastembed (ONNX, no torch) so note text never leaves the box.

The index is a numpy matrix plus a JSON manifest, stored outside the vault. It is
incremental by content hash: a daily capture embeds one note, not the corpus.
At 1,300 notes the matrix is ~2 MB -- no vector database is warranted.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from second_brain.kb.notes import Card

MANIFEST = "manifest.json"
VECTORS = "vectors.npy"
MODELS_DIR = "models"


class Embedder(Protocol):
    """Turns text into L2-normalised float32 vectors."""

    model_name: str

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedder:
    """fastembed-backed embedder. The model loads on first use, not on import."""

    def __init__(self, model_name: str, cache_dir: Path):
        self.model_name = model_name
        self._cache_dir = Path(cache_dir)
        self._model = None

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return normalize(np.stack(list(self._load().passage_embed(texts))))

    def embed_query(self, text: str) -> np.ndarray:
        return normalize(np.stack(list(self._load().query_embed(text))))[0]

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(
                model_name=self.model_name, cache_dir=str(self._cache_dir)
            )
        return self._model


def normalize(matrix: np.ndarray) -> np.ndarray:
    """Scale rows to unit length so a dot product is a cosine similarity."""
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        norm = np.linalg.norm(matrix)
        return matrix / norm if norm else matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def card_text(card: Card) -> str:
    """What gets embedded for a note: the card, never the full archive."""
    parts = [card.title, card.tldr, *card.key_points]
    return "\n".join(p for p in parts if p)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class RefreshStats:
    added: int = 0
    changed: int = 0
    removed: int = 0
    unchanged: int = 0

    @property
    def dirty(self) -> bool:
        return bool(self.added or self.changed or self.removed)


@dataclass
class EmbeddingIndex:
    model: str
    note_ids: list[str] = field(default_factory=list)
    hashes: list[str] = field(default_factory=list)
    vectors: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), np.float32))

    def row(self, note_id: str) -> int | None:
        try:
            return self.note_ids.index(note_id)
        except ValueError:
            return None

    @classmethod
    def load(cls, index_dir: Path) -> "EmbeddingIndex | None":
        """The stored index, or None if missing or unreadable (it is rebuildable)."""
        index_dir = Path(index_dir)
        try:
            manifest = json.loads((index_dir / MANIFEST).read_text(encoding="utf-8"))
            vectors = np.load(index_dir / VECTORS, allow_pickle=False)
        except (OSError, ValueError):
            return None
        entries = manifest.get("entries") or []
        if len(entries) != len(vectors):
            return None
        return cls(
            model=str(manifest.get("model") or ""),
            note_ids=[str(e["note_id"]) for e in entries],
            hashes=[str(e["hash"]) for e in entries],
            vectors=vectors.astype(np.float32, copy=False),
        )

    def save(self, index_dir: Path) -> None:
        """Write atomically: a crash mid-save leaves the previous index intact."""
        index_dir = Path(index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "model": self.model,
            "dim": int(self.vectors.shape[1]) if self.vectors.ndim == 2 else 0,
            "entries": [
                {"note_id": n, "hash": h} for n, h in zip(self.note_ids, self.hashes)
            ],
        }
        _atomic_write(index_dir / VECTORS, lambda f: np.save(f, self.vectors))
        _atomic_write(
            index_dir / MANIFEST,
            lambda f: f.write(json.dumps(manifest, indent=1).encode("utf-8")),
        )


def refresh(
    index: EmbeddingIndex | None, cards: list[Card], embedder: Embedder
) -> tuple[EmbeddingIndex, RefreshStats]:
    """Bring `index` in line with `cards`, embedding only what changed.

    A different embedding model makes every stored vector incomparable, so that
    case starts over.
    """
    if index is None or index.model != embedder.model_name:
        index = EmbeddingIndex(model=embedder.model_name)

    old = {n: (h, i) for i, (n, h) in enumerate(zip(index.note_ids, index.hashes))}
    stats = RefreshStats()
    note_ids, hashes, rows, to_embed = [], [], [], []

    for card in cards:
        text = card_text(card)
        digest = content_hash(text)
        previous = old.get(card.note_id)
        note_ids.append(card.note_id)
        hashes.append(digest)
        if previous and previous[0] == digest:
            stats.unchanged += 1
            rows.append(index.vectors[previous[1]])
        else:
            if previous:
                stats.changed += 1
            else:
                stats.added += 1
            rows.append(None)
            to_embed.append((len(rows) - 1, text))

    stats.removed = len(set(old) - set(note_ids))

    if to_embed:
        fresh = embedder.embed_passages([text for _, text in to_embed])
        for (position, _), vector in zip(to_embed, fresh):
            rows[position] = vector

    vectors = (
        normalize(np.stack(rows)) if rows else np.zeros((0, 0), dtype=np.float32)
    )
    return (
        EmbeddingIndex(
            model=embedder.model_name, note_ids=note_ids, hashes=hashes, vectors=vectors
        ),
        stats,
    )


def _atomic_write(path: Path, write) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            write(handle)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
