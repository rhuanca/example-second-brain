"""Dispatch a URL to the right adapter: YouTube, Medium (cookie), or web article.

YouTube has its own path (a transcript, not a page). Everything else is tried in
order of how much it can recover, falling through on failure:

  1. Medium + cookie   member-only HTML, rendered locally with a browser TLS
                       fingerprint (see `medium.py`)
  2. Jina Reader       Markdown + image captions, when a key is set
  3. trafilatura       no key, no service; the floor

The session cookie is used only by route 1, which runs on this machine. It is a
full Medium login, so it is never forwarded to Jina or any other service; what
route 2 recovers for a member-only article is the public teaser.

Falling through matters: Medium began serving a Cloudflare challenge to plain
HTTP clients, and when the cookie path raised straight to the caller a set
`MEDIUM_COOKIE` turned every Medium link into "could not download" -- the one
route that still worked was never tried.

A route that answers with almost no text has not succeeded either -- a consent
wall, a redirect stub, or (once) a cached ad pixel standing in for a Databricks
post, which was then summarised and filed as a note. Too little text demotes a
route to the next one; it never ends the capture, and whatever the last route
returns is accepted, so a genuinely short post still gets through.
"""

from __future__ import annotations

from second_brain.fetcher import Article, FetchError, is_thin
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

    Raises the last route's FetchError if every route fails.
    """
    if is_youtube_url(url):
        return youtube_fetch(url, api_key=supadata_api_key)

    cookie = medium_cookie if medium_cookie and is_medium_url(url) else None
    routes = []
    if cookie:
        routes.append(lambda: medium_fetch(url, cookie))
    if jina_enabled and jina_api_key:
        routes.append(lambda: jina_fetch(url, api_key=jina_api_key))
    routes.append(lambda: article_fetch(url))

    for route in routes[:-1]:
        try:
            article = route()
        except FetchError:
            continue  # the next route may still reach it
        if not is_thin(article.text):
            return article
    return routes[-1]()
