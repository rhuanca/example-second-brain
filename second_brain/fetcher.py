"""Fetch a URL and extract the article's title and main text via trafilatura."""

from __future__ import annotations

from dataclasses import dataclass


# Below this many characters the "article" is not one: a consent wall, a
# redirect stub, a tracking pixel. ~70 words -- shorter than any real post, and
# the bar only decides whether to *try the next route*, never whether to give up.
MIN_ARTICLE_CHARS = 400


class FetchError(Exception):
    """Raised when a URL can't be downloaded or has no extractable article."""


def is_thin(text: str) -> bool:
    """True when there is too little text for this to be the article we asked for."""
    return len(" ".join((text or "").split())) < MIN_ARTICLE_CHARS


@dataclass
class Article:
    title: str
    text: str  # the canonical Markdown archive body
    source: str = "article"  # source_type tag: article / youtube / medium / pdf
    kind: str = "article"  # what the archive is: article / transcript / pdf
    # The file this text was read from, when there was one (a PDF). Kept so the
    # diagrams survive: extraction gives back words, not figures.
    original: bytes | None = None


def fetch(url: str, *, downloader=None, extractor=None) -> Article:
    """Download `url` and return its `Article` (title + main text).

    `downloader` and `extractor` are injectable for testing; by default they use
    trafilatura. Raises FetchError if the page can't be downloaded or yields no
    article text (e.g. bot-blocked or JS-only pages).
    """
    downloader = downloader or _default_download
    extractor = extractor or _default_extract

    html = downloader(url)
    if not html:
        raise FetchError(f"Could not download the page at {url}")

    data = extractor(html)
    text = (data.get("text") if data else None) or ""
    text = text.strip()
    if not text:
        raise FetchError(f"No readable article content found at {url}")

    title = ((data.get("title") if data else None) or "").strip() or url
    return Article(title=title, text=text)


def _default_download(url: str):
    import trafilatura

    return trafilatura.fetch_url(url)


def _default_extract(html) -> dict:
    import trafilatura

    doc = trafilatura.bare_extraction(html)
    if doc is None:
        return {}
    # trafilatura 2.x returns a Document object; older versions return a dict.
    return doc.as_dict() if hasattr(doc, "as_dict") else doc
