"""Dispatch a URL to the right adapter: YouTube, Medium (cookie), or web article.

Web articles go through Jina Reader (Markdown + image captions) by default, with
trafilatura as the fallback. YouTube and Medium(+cookie) have dedicated paths.
"""

from __future__ import annotations

from second_brain.fetcher import Article, FetchError
from second_brain.fetcher import fetch as _article_fetch
from second_brain.jina import fetch_jina as _jina_fetch
from second_brain.medium import fetch_medium as _medium_fetch
from second_brain.medium import is_medium_url
from second_brain.youtube import fetch_transcript as _youtube_fetch
from second_brain.youtube import is_youtube_url


def fetch(
    url: str,
    *,
    medium_cookie: str | None = None,
    supadata_api_key: str | None = None,
    jina_enabled: bool = True,
    jina_api_key: str | None = None,
    article_fetch=_article_fetch,
    youtube_fetch=_youtube_fetch,
    medium_fetch=_medium_fetch,
    jina_fetch=_jina_fetch,
) -> Article:
    """Return an Article (canonical Markdown) for `url`, routing by source.

    YouTube → transcript. Medium(+cookie) → cookie fetch. Everything else → Jina
    Reader (Markdown + image captions) when enabled AND a JINA_API_KEY is set —
    captions require a key — falling back to trafilatura otherwise or on failure.
    """
    if is_youtube_url(url):
        return youtube_fetch(url, api_key=supadata_api_key)
    if medium_cookie and is_medium_url(url):
        return medium_fetch(url, medium_cookie)

    if jina_enabled and jina_api_key:
        try:
            return jina_fetch(url, api_key=jina_api_key)
        except FetchError:
            pass  # fall back to trafilatura
    return article_fetch(url)
