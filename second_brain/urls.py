"""Extract and normalize URLs from incoming message text.

`extract_url` finds the first http(s) URL in a message and returns it in a clean,
still-clickable form -- this is what gets stored as a note's `source:`.
`dedup_key` is the separate, comparison-only identity used to decide whether a
link is already in the vault; it may be opaque (e.g. `youtube:<id>`), so the same
video shared as youtu.be, /watch?v=, /live/ or with a timestamp maps to one note.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from second_brain.youtube import video_id

# Matches http/https URLs; stops at whitespace and common trailing punctuation.
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# Query parameters that are tracking noise, dropped during normalization.
# `si` is YouTube's share token: the share sheet mints a fresh one every time, so
# leaving it in made the same video look like two different links.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref",
        "ref_src",
        "si",
        "spm",
        "s",
    }
)

# Params that change where playback starts or how you arrived, not which page it
# is. Dropped from the dedup key only, so the stored URL keeps the timestamp.
_POSITION_PARAMS = frozenset({"t", "start", "feature", "pp", "ab_channel"})


def extract_url(text: str | None) -> str | None:
    """Return the normalized first URL in `text`, or None if there is none."""
    if not text:
        return None
    match = _URL_RE.search(text)
    if not match:
        return None
    return normalize_url(match.group(0))


def normalize_url(url: str) -> str:
    """Canonicalize a URL for storage: still a real, clickable link.

    Lowercases scheme and host, drops the fragment, removes tracking query
    params, and strips a trailing slash from the path.
    """
    # Trim trailing punctuation that often clings to a pasted URL.
    url = url.rstrip(".,;!?")

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(k)
    ]
    query = urlencode(kept)

    path = parts.path.rstrip("/")

    # Fragment intentionally dropped.
    return urlunsplit((scheme, netloc, path, query, ""))


def dedup_key(url: str) -> str:
    """Return the identity used to tell whether two links are the same source.

    Unlike `normalize_url` this need not stay a usable URL, so it can fold every
    YouTube URL shape onto the video id. Everything else is the normalized URL
    without `www.` or playback-position params.
    """
    vid = video_id(url)
    if vid:
        return f"youtube:{vid}"

    parts = urlsplit(normalize_url(url))
    netloc = parts.netloc.removeprefix("www.")
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _POSITION_PARAMS
    ]
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(kept), ""))


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    if lowered in _TRACKING_PARAMS:
        return True
    return any(lowered.startswith(prefix) for prefix in _TRACKING_PREFIXES)
