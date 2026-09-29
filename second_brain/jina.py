"""Jina Reader adapter — a web link → canonical Markdown, with image captions.

`r.jina.ai` fetches and cleans a page into Markdown; with the
`x-with-generated-alt` header it runs a vision model over images and inserts
captions inline (`![Image N: <caption>](url)`), so diagrams/screenshots become
text our (text-only) summarizer and `/ask` can use. Captions need a key --
without one the service answers 401 -- so they are requested only when there is
one, and a keyless read still returns the article.

Every URL sent here is visible to the service, so nothing that identifies the
reader goes with it — a session cookie belongs to a local fetch (see `medium.py`),
never to a third party.
"""

from __future__ import annotations

from second_brain.fetcher import Article, FetchError

_ENDPOINT = "https://r.jina.ai/"


def fetch_jina(url: str, *, api_key=None, get=None) -> Article:
    """Fetch `url` as Markdown (with image captions when a key is set) via Jina Reader.

    Raises FetchError on any failure so the caller can fall back to trafilatura.
    """
    get = get or _default_get
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        # Vision captions are a paid feature: asking without a key fails the
        # whole request, so only ask when we can pay for it.
        headers["X-With-Generated-Alt"] = "true"

    try:
        resp = get(_ENDPOINT + url, headers=headers, timeout=60)
    except Exception as exc:  # noqa: BLE001 — network/transport
        raise FetchError(f"Couldn't reach the reader service: {exc}") from exc

    status = getattr(resp, "status_code", 200)
    if status >= 400:
        raise FetchError(f"Reader service error (HTTP {status}).")

    data = (resp.json() or {}).get("data") or {}
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise FetchError("The reader service returned no readable content.")

    title = (data.get("title") or "").strip() or url
    return Article(title=title, text=content, source="article", kind="article")


def _default_get(url: str, **kwargs):
    import requests

    return requests.get(url, **kwargs)
