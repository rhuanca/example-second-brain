"""Medium support: fetch member-only articles using the user's session cookie.

The paywall only gates the *download* — once we retrieve the full HTML (by sending
the paying member's `sid` cookie), the normal trafilatura extraction in
`fetcher.fetch` handles the rest. So this module just supplies a cookie-aware
downloader and delegates.

Medium now sits behind a bot challenge that answers plain HTTP clients with a 403
*before* it looks at any cookie, so `requests` cannot get in whatever headers it
sends: the check reads the TLS handshake, not the User-Agent. `curl_cffi` replays
a real Chrome fingerprint, which gets through.

The rendering happens here, on this machine, precisely so the session cookie stays
here. It is a full Medium login, not a scoped token, so it is never handed to a
third-party reader service.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from second_brain.fetcher import Article, FetchError
from second_brain.fetcher import fetch as _article_fetch

# A browser profile curl_cffi knows how to impersonate, TLS fingerprint included.
IMPERSONATE = "chrome"

MISSING_DEPENDENCY = (
    "Medium capture needs the browser-impersonation extra. "
    "Install it on the server with: uv sync --extra browser"
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
            resp = getter(url, cookies={"sid": cookie}, timeout=30)
            resp.raise_for_status()
            return resp.text
        except FetchError:
            raise  # a missing dependency is worth saying out loud
        except Exception:
            # Downstream fetcher.fetch turns a falsy result into a clear FetchError,
            # and sources.fetch then tries the next route.
            return None

    return download


def _default_get(url: str, **kwargs):
    """Download as Chrome would — same TLS handshake, not just the same header.

    Headers come from the impersonated profile, so none are set here: a
    hand-written User-Agent that disagrees with the handshake is itself a tell.
    """
    try:
        from curl_cffi import requests as browser
    except ImportError as exc:
        raise FetchError(MISSING_DEPENDENCY) from exc

    return browser.get(url, impersonate=IMPERSONATE, **kwargs)
