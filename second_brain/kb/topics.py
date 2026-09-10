"""Discover the vault's topics and assign notes to them.

The tags Claude writes per note are free-form, so they fragment badly (224
distinct tags over 73 notes, 167 of them used once). This derives a small,
stable taxonomy instead, by showing the model every note's card at once -- the
whole corpus is only ~12k tokens, which is why no embeddings or clustering
library are needed here.

Re-running is a *refinement*: the current taxonomy is passed back in and the
model may merge, split, add or retire topics, but ids never change. Stability
matters more than perfection -- churning ids silently rots links and MOCs.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from second_brain.kb.notes import Card

TOPICS_FILE = Path(__file__).with_name("topics.json")

_slug_strip = re.compile(r"[^a-z0-9]+")

# Below this, "one topic holds too much of the library" is not a real signal.
_MIN_FOR_SHARE_CHECK = 10

_SYSTEM = (
    "You organize an engineer's personal reading library. You group saved notes "
    "into a small number of coherent topics that reflect how the material actually "
    "clusters -- not a generic taxonomy of the field. Return only JSON."
)

_PROMPT = """\
Below is every note in the library, one per line, as `[id] Title — summary`.

Group them into {target} topics that describe how this collection actually
clusters. A note may belong to up to 3 topics. Put a note in `unassigned` rather
than forcing it into a topic it does not belong to.

Return ONLY a JSON object (no markdown fences, no prose):
- "topics": array of objects with
    - "id": lowercase-kebab slug, stable and descriptive (string)
    - "name": short display name (string)
    - "description": one line saying what belongs here (string)
    - "note_ids": the ids from the list below that belong to this topic (array)
- "unassigned": ids that fit no topic (array of strings)

Rules:
- Use every id exactly as written; never invent or abbreviate one.
- Avoid a catch-all topic. If a group would be vague, leave its notes unassigned.
- Prefer topics of comparable size; a topic holding almost everything is useless.
{refinement}
Notes:
{menu}
"""

_REFINEMENT = """\
These topics already exist. Keep their `id` values EXACTLY as given for any topic
you keep, even if you change its name or description. You may merge, split, add
or retire topics as the collection has shifted, but do not renumber or rename ids.

{existing}
"""


# Structured output: the API guarantees a response matching this shape, which
# removes the "model wrapped it in prose" failure mode entirely.
_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "note_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "name", "description", "note_ids"],
                "additionalProperties": False,
            },
        },
        "unassigned": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["topics", "unassigned"],
    "additionalProperties": False,
}


class TopicError(Exception):
    """Raised when a taxonomy cannot be produced."""


@dataclass
class Topic:
    id: str
    name: str
    description: str = ""
    note_ids: list[str] = field(default_factory=list)


@dataclass
class Taxonomy:
    topics: list[Topic] = field(default_factory=list)
    unassigned: list[str] = field(default_factory=list)

    def topics_for(self, note_id: str) -> list[str]:
        """The topic ids a note belongs to, in taxonomy order."""
        return [t.id for t in self.topics if note_id in t.note_ids]

    def assignments(self) -> dict[str, list[str]]:
        """{note_id: [topic_id, ...]} for every note the taxonomy mentions."""
        out: dict[str, list[str]] = {}
        for topic in self.topics:
            for note_id in topic.note_ids:
                out.setdefault(note_id, []).append(topic.id)
        return out


def _budget(card_count: int) -> int:
    """Output budget for a taxonomy over `card_count` notes.

    Every note id has to be echoed back, sometimes under more than one topic, and
    on models with adaptive thinking the reasoning shares this budget -- so it has
    to grow with the library rather than sit at a constant.
    """
    return max(16000, 200 * card_count)


def suggest_target(card_count: int) -> int:
    """A sensible number of topics for a library of this size.

    Roughly one topic per five notes, clamped -- few enough to scan, many enough
    that no topic becomes a junk drawer. Grows with the vault.
    """
    return max(6, min(24, round(card_count / 5)))


def discover(
    cards: list[Card],
    *,
    model: str,
    api_key: str | None = None,
    client=None,
    existing: Taxonomy | None = None,
    target: int | None = None,
    max_tokens: int | None = None,
) -> Taxonomy:
    """Derive a taxonomy from every card at once.

    `client` may be injected (tests); otherwise one is built from `api_key`.
    Passing `existing` turns this into a refinement that preserves topic ids.
    """
    if not cards:
        return Taxonomy()

    client = client or _build_client(api_key)
    prompt = _PROMPT.format(
        target=target or suggest_target(len(cards)),
        menu="\n".join(card.summary_line() for card in cards),
        refinement=_REFINEMENT.format(existing=_render_existing(existing)) if existing else "",
    )
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens or _budget(len(cards)),
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
    )

    stop = getattr(response, "stop_reason", None)
    raw = _extract_text(response)
    if stop == "max_tokens":
        raise TopicError(
            "the reply was cut off by max_tokens before the JSON was complete. "
            "Every note id has to fit in the response, so raise max_tokens "
            f"(was {max_tokens or _budget(len(cards))}) or lower --target."
        )
    return _parse(raw, known_ids={c.note_id for c in cards}, stop_reason=stop)


def load_taxonomy(path: Path = TOPICS_FILE) -> Taxonomy | None:
    """Read a stored taxonomy, or None if there isn't one yet."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _from_dict(data)


def save_taxonomy(taxonomy: Taxonomy, path: Path = TOPICS_FILE) -> Path:
    """Write the taxonomy as readable, diff-friendly JSON."""
    path.write_text(
        json.dumps(asdict(taxonomy), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def health(taxonomy: Taxonomy, card_count: int) -> list[str]:
    """Complaints about a taxonomy: empty means it looks usable.

    An unsupervised taxonomy has no ground truth, so these are the cheap
    structural checks worth making before anything is built on top of it.
    """
    problems = []
    if not taxonomy.topics:
        problems.append("no topics were produced")
    # A share check only means something once there are enough notes to spread
    # around: two topics over four notes is 50% each and perfectly healthy.
    check_share = card_count >= _MIN_FOR_SHARE_CHECK
    for topic in taxonomy.topics:
        if len(topic.note_ids) < 2:
            problems.append(f"{topic.id!r} holds {len(topic.note_ids)} note(s)")
        elif check_share and len(topic.note_ids) > 0.4 * card_count:
            share = len(topic.note_ids) / card_count
            problems.append(f"{topic.id!r} holds {share:.0%} of the library")
    if card_count and len(taxonomy.unassigned) > 0.2 * card_count:
        share = len(taxonomy.unassigned) / card_count
        problems.append(f"{share:.0%} of notes are unassigned")
    return problems


def _render_existing(taxonomy: Taxonomy | None) -> str:
    if not taxonomy:
        return ""
    return "\n".join(
        f"- {t.id}: {t.name} — {t.description}".rstrip(" —") for t in taxonomy.topics
    )


def _parse(raw: str, *, known_ids: set[str], stop_reason=None) -> Taxonomy:
    payload = _load_json(raw)
    if payload is None:
        # Never swallow the reply -- without it this failure is undiagnosable.
        snippet = (raw or "").strip()[:400] or "(the reply had no text at all)"
        raise TopicError(
            f"the model did not return usable JSON (stop_reason={stop_reason!r}). "
            f"Reply began: {snippet}"
        )

    topics = []
    seen_slugs: set[str] = set()
    for item in payload.get("topics") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        slug = _slug(str(item.get("id") or "") or name)
        if not slug or slug in seen_slugs:
            continue
        # Drop ids the model invented; only real notes may be assigned.
        note_ids = [n for n in _str_list(item.get("note_ids")) if n in known_ids]
        if not note_ids:
            continue
        seen_slugs.add(slug)
        topics.append(
            Topic(
                id=slug,
                name=name or slug,
                description=str(item.get("description") or "").strip(),
                note_ids=note_ids,
            )
        )

    taxonomy = Taxonomy(topics=topics)
    assigned = set(taxonomy.assignments())
    # Trust our own arithmetic over the model's: anything unmentioned is unassigned.
    taxonomy.unassigned = sorted(known_ids - assigned)
    return taxonomy


def _from_dict(data) -> Taxonomy | None:
    if not isinstance(data, dict):
        return None
    topics = [
        Topic(
            id=str(t.get("id") or ""),
            name=str(t.get("name") or ""),
            description=str(t.get("description") or ""),
            note_ids=_str_list(t.get("note_ids")),
        )
        for t in data.get("topics") or []
        if isinstance(t, dict) and t.get("id")
    ]
    return Taxonomy(topics=topics, unassigned=_str_list(data.get("unassigned")))


def _build_client(api_key: str | None):
    if not api_key:
        raise TopicError(
            "ANTHROPIC_API_KEY is not set; cannot discover topics. Provide the key "
            "in the environment or inject a client."
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


def _load_json(raw: str) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s for v in value if (s := str(v).strip())]


def _slug(value: str) -> str:
    return _slug_strip.sub("-", value.lower()).strip("-")
