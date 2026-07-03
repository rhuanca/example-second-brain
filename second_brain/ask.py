"""Ask-your-second-brain: retrieve relevant notes and answer with Claude.

First cut uses lexical retrieval (keyword overlap over title, tags, and body) —
no embeddings or graph. The retriever is swappable for semantic/agentic search
later behind the same `ask()` entry point.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from second_brain.config import Settings
from second_brain.vault import Vault

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "of", "to", "in", "on",
    "for", "and", "or", "with", "about", "what", "how", "why", "when", "which",
    "who", "have", "has", "had", "i", "my", "me", "do", "did", "does", "any",
    "some", "that", "this", "it", "as", "at", "by", "from",
}
_BODY_CAP = 2000  # chars of body per note fed to the model

_SYSTEM = (
    "You answer the user's questions using only their own saved notes (summaries of "
    "articles and videos they captured). Be concise and technical. Cite the note "
    "titles you drew from. If the notes don't cover the question, say so plainly "
    "rather than guessing."
)


class AskError(Exception):
    """Raised when an answer can't be produced (e.g. missing API key)."""


@dataclass
class Note:
    path: Path
    title: str
    source: str
    tags: list[str] = field(default_factory=list)
    body: str = ""


def ask(
    question: str,
    *,
    vault: Vault,
    settings: Settings,
    client=None,
    searcher=None,
    answerer=None,
) -> str:
    """Answer `question` from the vault. Returns a reply string, always safe to send."""
    searcher = searcher or search
    answerer = answerer or answer

    notes = load_notes(vault)
    hits = searcher(notes, question)
    if not hits:
        return "🔍 I couldn't find anything about that in your notes yet."

    return answerer(
        question,
        hits,
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        client=client,
    )


def load_notes(vault: Vault) -> list[Note]:
    """Read every note in the vault into a Note (title, source, tags, body)."""
    notes = []
    for path in vault.iter_notes():
        try:
            post = frontmatter.load(str(path))
        except Exception:  # noqa: BLE001 — skip unreadable files
            continue
        meta = post.metadata
        tags = meta.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        notes.append(
            Note(
                path=path,
                title=str(meta.get("title") or path.stem),
                source=str(meta.get("source") or ""),
                tags=[str(t) for t in tags],
                body=post.content or "",
            )
        )
    return notes


def search(notes: list[Note], question: str, *, limit: int = 5) -> list[Note]:
    """Return up to `limit` notes ranked by keyword overlap with the question."""
    q_tokens = _tokenize(question)
    if not q_tokens:
        return []
    scored = []
    for note in notes:
        s = _score(note, q_tokens)
        if s > 0:
            scored.append((s, note))
    scored.sort(key=lambda pair: (-pair[0], pair[1].title))
    return [note for _, note in scored[:limit]]


def answer(question: str, notes: list[Note], *, model: str, api_key=None, client=None) -> str:
    """Ask Claude to answer from the retrieved notes, citing note titles."""
    client = client or _build_client(api_key)

    blocks = []
    for note in notes:
        tags = ", ".join(note.tags)
        blocks.append(
            f"### {note.title}\nSource: {note.source}\nTags: {tags}\n"
            f"{note.body[:_BODY_CAP]}"
        )
    context = "\n\n".join(blocks)

    prompt = (
        f"Question: {question}\n\n"
        f"Answer using only these saved notes, and cite the note titles you use:\n\n"
        f"{context}"
    )
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(response) or "(no answer produced)"


def _tokenize(text: str) -> list[str]:
    return [
        tok
        for tok in _TOKEN_RE.findall((text or "").lower())
        if len(tok) > 1 and tok not in _STOPWORDS
    ]


def _score(note: Note, q_tokens: list[str]) -> int:
    weighted: Counter[str] = Counter()
    for tok in _tokenize(note.title):
        weighted[tok] += 3
    for tag in note.tags:
        for tok in _tokenize(tag):
            weighted[tok] += 3
    for tok in _tokenize(note.body):
        weighted[tok] += 1
    return sum(weighted[tok] for tok in q_tokens)


def _build_client(api_key):
    if not api_key:
        raise AskError(
            "ANTHROPIC_API_KEY is not set; cannot answer. Provide the key in the "
            "environment or inject a client."
        )
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def _extract_text(response) -> str:
    parts = [
        getattr(block, "text", "")
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(parts).strip()
