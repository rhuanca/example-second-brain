"""Medium support: fetch member-only articles using the user's session cookie.

The paywall only gates the *download* — once we retrieve the full HTML (by sending
the paying member's `sid` cookie), the normal trafilatura extraction in
`fetcher.fetch` handles the rest. So this module just supplies a cookie-aware
downloader and delegates.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from second_brain.fetcher import Article
from second_brain.fetcher import fetch as _article_fetch

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def is_medium_url(url: str) -> bool:
    """True for medium.com and its *.medium.com subdomains.

    Medium publications on custom domains are not detected here (can't be told
    from the URL alone) — those fall back to the normal article fetch.
    """
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return host == "medium.com" or host.endswith(".medium.com")


def fetch_medium(url: str, cookie: str, *, article_fetch=_article_fetch, get=None) -> Article:
    """Fetch a Medium article with the session cookie, then extract as usual.

    A bad/expired cookie simply yields the public teaser HTML (Medium returns it
    for logged-out requests), so the note degrades gracefully rather than erroring.
    """
    article = article_fetch(url, downloader=_cookie_downloader(cookie, get=get))
    article.source = "medium"
    return article


def _cookie_downloader(cookie: str, get=None):
    def download(url: str):
        getter = get or _default_get
        try:
            resp = getter(
                url,
                cookies={"sid": cookie},
                headers={"User-Agent": _UA},
                timeout=20,
            )
            resp.raise_for_status()
            return resp.text
        except Exception:
            # Downstream fetcher.fetch turns a falsy result into a clear FetchError.
            return None

    return download


def _default_get(url: str, **kwargs):
    import requests

    return requests.get(url, **kwargs)
