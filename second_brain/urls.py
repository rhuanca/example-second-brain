"""Extract and normalize URLs from incoming message text.

`extract_url` finds the first http(s) URL in a message. `normalize_url` produces
a canonical form used as the dedup key, so the same article shared with different
tracking parameters or a trailing slash maps to one note.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Matches http/https URLs; stops at whitespace and common trailing punctuation.
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# Query parameters that are tracking noise, dropped during normalization.
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
        "spm",
        "s",
    }
)


def extract_url(text: str | None) -> str | None:
    """Return the normalized first URL in `text`, or None if there is none."""
    if not text:
        return None
    match = _URL_RE.search(text)
    if not match:
        return None
    return normalize_url(match.group(0))


def normalize_url(url: str) -> str:
    """Canonicalize a URL for stable dedup.

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


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    if lowered in _TRACKING_PARAMS:
        return True
    return any(lowered.startswith(prefix) for prefix in _TRACKING_PREFIXES)
