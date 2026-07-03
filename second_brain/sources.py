"""Dispatch a URL to the right fetcher: YouTube transcript, Medium (cookie), or web article."""

from __future__ import annotations

from second_brain.fetcher import Article
from second_brain.fetcher import fetch as _article_fetch
from second_brain.medium import fetch_medium as _medium_fetch
from second_brain.medium import is_medium_url
from second_brain.youtube import fetch_transcript as _youtube_fetch
from second_brain.youtube import is_youtube_url


def fetch(
    url: str,
    *,
    medium_cookie: str | None = None,
    supadata_api_key: str | None = None,
    article_fetch=_article_fetch,
    youtube_fetch=_youtube_fetch,
    medium_fetch=_medium_fetch,
) -> Article:
    """Return an Article for `url`, routing by source.

    YouTube → transcript (via Supadata when `supadata_api_key` is set, else the
    local library). Medium → cookie fetch when `medium_cookie` is set (otherwise a
    Medium link just uses the normal article fetch → teaser).
    """
    if is_youtube_url(url):
        return youtube_fetch(url, api_key=supadata_api_key)
    if medium_cookie and is_medium_url(url):
        return medium_fetch(url, medium_cookie)
    return article_fetch(url)
