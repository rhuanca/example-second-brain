"""YouTube support: detect video URLs and fetch their transcript as an Article.

We summarize the *transcript*, not the web page — a YouTube watch page has no
readable article text. Reuses `fetcher.Article` / `fetcher.FetchError` so the rest
of the pipeline treats a video exactly like an article.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from second_brain.fetcher import Article, FetchError

_ID_RE = re.compile(r"^[\w-]{11}$")
_YT_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com"}
_PATH_PREFIXES = ("/shorts/", "/embed/", "/v/", "/live/")


def video_id(url: str) -> str | None:
    """Extract the 11-char video id from a YouTube URL, or None if it isn't one."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")

    candidate = None
    if host == "youtu.be":
        candidate = parts.path.lstrip("/").split("/", 1)[0]
    elif host in _YT_HOSTS:
        if parts.path == "/watch":
            candidate = parse_qs(parts.query).get("v", [None])[0]
        else:
            for prefix in _PATH_PREFIXES:
                if parts.path.startswith(prefix):
                    candidate = parts.path[len(prefix):].split("/", 1)[0]
                    break

    return candidate if candidate and _ID_RE.match(candidate) else None


def is_youtube_url(url: str) -> bool:
    return video_id(url) is not None


def fetch_transcript(url: str, *, get_transcript=None, get_title=None) -> Article:
    """Return an Article whose text is the video transcript.

    `get_transcript`/`get_title` are injectable for testing. Raises FetchError if
    the URL isn't a video or has no usable transcript (captions disabled, none
    available, region/age blocked, etc.).
    """
    vid = video_id(url)
    if vid is None:
        raise FetchError(f"Not a recognizable YouTube video URL: {url}")

    get_transcript = get_transcript or _default_transcript
    get_title = get_title or _default_title

    try:
        text = get_transcript(vid)
    except FetchError:
        raise
    except Exception as exc:  # noqa: BLE001 — normalize any library error
        raise FetchError(_transcript_error_message(exc)) from exc

    text = (text or "").strip()
    if not text:
        raise FetchError("This video has no usable transcript.")

    try:
        title = get_title(vid, url)
    except Exception:  # noqa: BLE001 — title is best-effort
        title = None

    return Article(title=title or f"YouTube video {vid}", text=text, source="youtube")


def _transcript_error_message(exc: Exception) -> str:
    """Turn a youtube-transcript-api error into a message that aids diagnosis.

    Classify by the exception's class name so we don't have to import the library
    here (it's only imported inside `_default_transcript`).
    """
    name = type(exc).__name__
    if name in {"IpBlocked", "RequestBlocked"}:
        return (
            "YouTube is blocking transcript requests from this host's IP (common on "
            "cloud/VPS hosts). Run the bot from a residential IP or use a proxy."
        )
    if name in {"AgeRestricted", "VideoUnavailable", "InvalidVideoId", "PoTokenRequired"}:
        return "This video is unavailable or restricted, so its transcript can't be fetched."
    return "No transcript available for this video (captions may be disabled)."


def _default_transcript(video_id: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi

    fetched = YouTubeTranscriptApi().fetch(video_id)
    return " ".join(snippet.text for snippet in fetched)


def _default_title(video_id: str, url: str) -> str | None:
    import requests

    resp = requests.get(
        "https://www.youtube.com/oembed",
        params={"url": url, "format": "json"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("title")
