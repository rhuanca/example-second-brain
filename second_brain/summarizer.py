"""Summarize an article into a structured `Summary` via the Claude API.

We ask Claude for strict JSON and parse it into a `Summary`. If the model returns
something we can't parse as JSON, we fall back to storing the raw text as the
TL;DR so a valid (if less structured) note is still produced.
"""

from __future__ import annotations

import json
import re

from second_brain.models import Para, Summary

_SYSTEM = (
    "You are a technical analyst who writes concise, information-dense summaries "
    "of articles about software engineering, agentic development, and related "
    "topics for an engineer's personal knowledge base."
)

# The model is asked to return exactly this JSON shape.
_PROMPT_TEMPLATE = """\
Summarize the following article for a technical second brain.

Return ONLY a JSON object (no markdown fences, no prose) with these keys:
- "title": a clean title for the article (string)
- "tldr": a 2-4 sentence technical summary (string)
- "key_points": 3-6 concrete technical takeaways (array of strings)
- "tags": 2-5 topic tags, lowercase, single words or short phrases (array of strings)
- "para_category": one of "projects", "areas", "resources", "archives" \
(default "resources" for reference reading)
- "prototype_ideas": 1-3 ideas for prototypes this article could inspire \
(array of strings; empty array if none)

Article title: {title}

Article content:
{content}
"""

# Keep the article text sent to the model bounded (rough char budget).
_MAX_CONTENT_CHARS = 24000

_kebab_strip = re.compile(r"[^a-z0-9]+")


class SummarizerError(Exception):
    """Raised when a summary cannot be produced (e.g. missing API key)."""


def summarize(
    title: str,
    article_text: str,
    *,
    model: str,
    api_key: str | None = None,
    client=None,
    max_tokens: int = 2000,
) -> Summary:
    """Produce a `Summary` for an article.

    `client` may be injected (used by tests); otherwise an Anthropic client is
    built from `api_key`, which must be present.
    """
    client = client or _build_client(api_key)

    prompt = _PROMPT_TEMPLATE.format(
        title=title or "(untitled)",
        content=article_text[:_MAX_CONTENT_CHARS],
    )
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = _extract_text(response)
    return _parse_summary(raw, fallback_title=title)


def _build_client(api_key: str | None):
    if not api_key:
        raise SummarizerError(
            "ANTHROPIC_API_KEY is not set; cannot summarize. Provide the key in the "
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


def _parse_summary(raw: str, fallback_title: str) -> Summary:
    payload = _load_json(raw)
    if payload is None:
        # Tolerant fallback: keep the raw text as a usable note.
        return Summary(
            title=fallback_title or "Untitled",
            tldr=raw or "(no summary produced)",
            para=Para.RESOURCES,
        )

    return Summary(
        title=str(payload.get("title") or fallback_title or "Untitled"),
        tldr=str(payload.get("tldr") or "(summary unavailable)").strip(),
        key_points=_str_list(payload.get("key_points")),
        tags=[_kebab(t) for t in _str_list(payload.get("tags"))],
        prototype_ideas=_str_list(payload.get("prototype_ideas")),
        para=Para.from_str(payload.get("para_category")),
    )


def _load_json(raw: str) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    # Strip a ```json ... ``` (or bare ```) fence if the model added one.
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


def _kebab(tag: str) -> str:
    return _kebab_strip.sub("-", tag.lower()).strip("-")
