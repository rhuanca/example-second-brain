"""Fetch a URL and extract the article's title and main text via trafilatura."""

from __future__ import annotations

from dataclasses import dataclass


class FetchError(Exception):
    """Raised when a URL can't be downloaded or has no extractable article."""


@dataclass
class Article:
    title: str
    text: str


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
