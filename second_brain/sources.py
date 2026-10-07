"""Dispatch a URL to the right adapter: YouTube, Medium (cookie), or web article.

YouTube has its own path (a transcript, not a page). Everything else is tried in
order of how much it can recover, falling through on failure:

  1. Medium + cookie   member-only HTML, rendered locally with a browser TLS
                       fingerprint (see `medium.py`)
  2. Jina Reader       Markdown + image captions, when a key is set
  3. trafilatura       no key, no service; the floor

A link that points at a PDF is still fetched this way -- the reader converts it
to Markdown -- but it is labelled `pdf`, carries the same tag and icon as a PDF
sent to the bot, and the file itself is downloaded alongside so its diagrams are
kept too.

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

from urllib.parse import urlsplit

from second_brain.fetcher import Article, FetchError, is_thin
from second_brain.pdf import download as _pdf_download
from second_brain.pdf import looks_like_a_filename, title_from
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
    pdf_download=_pdf_download,
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
            return _label(article, url, pdf_download)
    return _label(routes[-1](), url, pdf_download)


def _label(article: Article, url: str, pdf_download) -> Article:
    """Mark a linked PDF as one, and name it after its contents.

    A reader hands back `1706.03762v7.pdf` as the title of a paper called
    "Attention Is All You Need", and tags it as an article, so the PDF icon in
    the browse UI never appeared for anything.
    """
    named_like_a_file = looks_like_a_filename(article.title)
    # arxiv.org/pdf/1706.03762 has no extension at all, so the title the reader
    # gives back -- the file it downloaded -- is the more reliable signal.
    if not named_like_a_file and not urlsplit(url).path.lower().endswith(".pdf"):
        return article
    article.source = "pdf"
    article.kind = "pdf"
    if not article.title or named_like_a_file or article.title == url:
        article.title = title_from(article.text, article.title)
    # The file itself, for the diagrams the text layer leaves behind. Optional by
    # design: None here means the note is saved without it.
    article.original = pdf_download(url)
    return article
