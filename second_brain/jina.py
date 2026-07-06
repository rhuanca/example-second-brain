"""Jina Reader adapter — a web link → canonical Markdown, with image captions.

`r.jina.ai` fetches and cleans a page into Markdown; with the
`x-with-generated-alt` header it runs a vision model over images and inserts
captions inline (`![Image N: <caption>](url)`), so diagrams/screenshots become
text our (text-only) summarizer and `/ask` can use. Free tier works without a key;
`JINA_API_KEY` raises the rate limit.
"""

from __future__ import annotations

from second_brain.fetcher import Article, FetchError

_ENDPOINT = "https://r.jina.ai/"


def fetch_jina(url: str, *, api_key=None, get=None) -> Article:
    """Fetch `url` as Markdown (with image captions) via Jina Reader.

    Raises FetchError on any failure so the caller can fall back to trafilatura.
    """
    get = get or _default_get
    headers = {"Accept": "application/json", "X-With-Generated-Alt": "true"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

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
