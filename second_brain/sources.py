"""Dispatch a URL to the right fetcher: YouTube transcript vs. web article."""

from __future__ import annotations

from second_brain.fetcher import Article
from second_brain.fetcher import fetch as _article_fetch
from second_brain.youtube import fetch_transcript as _youtube_fetch
from second_brain.youtube import is_youtube_url


def fetch(url: str, *, article_fetch=_article_fetch, youtube_fetch=_youtube_fetch) -> Article:
    """Return an Article for `url`, routing YouTube links to their transcript."""
    if is_youtube_url(url):
        return youtube_fetch(url)
    return article_fetch(url)
