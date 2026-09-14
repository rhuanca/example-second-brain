"""Shared test doubles for the knowledge-base tests (not a test module itself)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from second_brain.kb.embeddings import normalize

_TOKEN = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words score as similar.

    Stands in for fastembed so no test ever downloads a model, and records every
    call so tests can assert what was (and was not) re-embedded.
    """

    def __init__(self, model_name: str = "fake-model", dim: int = 64):
        self.model_name = model_name
        self.dim = dim
        self.passage_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        self.passage_calls.append(list(texts))
        return normalize(np.stack([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        self.query_calls.append(text)
        return normalize(self._vector(text))

    @property
    def embedded_texts(self) -> list[str]:
        return [t for call in self.passage_calls for t in call]

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        for token in _TOKEN.findall(text.lower()):
            bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim
            vector[bucket] += 1.0
        if not vector.any():
            vector[0] = 1.0
        return vector


def note_text(
    title: str,
    tldr: str,
    *,
    source: str = "https://example.com/post",
    date: str = "2026-08-01",
    tags: tuple[str, ...] = ("article",),
    topics: tuple[str, ...] = (),
    key_points: tuple[str, ...] = (),
    ideas: tuple[str, ...] = (),
) -> str:
    lines = ["---", f"title: {title!r}", f"source: {source}", f"date: '{date}'"]
    lines += ["tags:", *[f"- {t}" for t in tags]]
    if topics:
        lines += ["topics:", *[f"- {t}" for t in topics]]
    lines += ["---", "", "## TL;DR", "", tldr, "", "## Key technical points", ""]
    lines += [f"- {p}" for p in key_points]
    lines += ["", "## Prototype ideas", ""]
    lines += [f"- {i}" for i in ideas]
    return "\n".join(lines) + "\n"


def write_note(root: Path, note_id: str, title: str, tldr: str, **kwargs) -> Path:
    path = Path(root) / f"{note_id}.md"
    path.write_text(note_text(title, tldr, **kwargs), encoding="utf-8")
    return path


def write_archive(root: Path, note_id: str, text: str) -> Path:
    folder = Path(root) / "sources"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{note_id}.source.md"
    path.write_text(text, encoding="utf-8")
    return path
