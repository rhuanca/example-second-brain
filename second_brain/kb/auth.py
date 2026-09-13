"""Application-level authentication for the knowledge-base service.

Cloudflare Access is the front door, but this layer assumes the edge has been
bypassed: every `/mcp` request must carry a known bearer token, checked in
constant time, and it is rejected here -- before the MCP app runs and so before
anything on disk is read.

Tokens are accepted from the `Authorization` header only. A token in the query
string is refused outright rather than ignored, because URLs end up in logs,
shell history and referrers; failing loudly stops that habit forming.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Sequence
from urllib.parse import parse_qs

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

MCP_PREFIX = "/mcp"
_QUERY_TOKEN_NAMES = {"token", "access_token", "auth", "key", "api_key", "bearer"}


def bearer_ok(header: str | None, tokens: Sequence[str]) -> bool:
    """True if `header` is `Bearer <t>` for one of `tokens`.

    Every configured token is compared, even after a match, so response timing
    says nothing about which token -- or how much of one -- was right.
    """
    if not header or not tokens:
        return False
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    presented = presented.strip()
    if not presented:
        return False
    candidate = presented.encode("utf-8")
    matched = False
    for token in tokens:
        if token and hmac.compare_digest(candidate, token.encode("utf-8")):
            matched = True
    return matched


def is_mcp_path(path: str) -> bool:
    return path == MCP_PREFIX or path.startswith(MCP_PREFIX + "/")


class McpAuthMiddleware:
    """Guards `/mcp` with a bearer token; other paths pass straight through."""

    def __init__(self, app: ASGIApp, tokens: Sequence[str]):
        self.app = app
        self.tokens = tuple(tokens)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_mcp_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if _QUERY_TOKEN_NAMES & {name.lower() for name in query}:
            _log_rejection(scope, "token in query string")
            response = PlainTextResponse(
                "Send the token in the Authorization header, never in the URL.",
                status_code=400,
            )
            await response(scope, receive, send)
            return

        if not bearer_ok(_header(scope, b"authorization"), self.tokens):
            _log_rejection(scope, "missing or invalid bearer token")
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _log_rejection(scope: Scope, reason: str) -> None:
    client = scope.get("client") or ("?", 0)
    # Never log the presented credential, only that one was refused.
    logger.warning(
        "auth rejected %s %s from %s: %s",
        scope.get("method"),
        scope.get("path"),
        client[0],
        reason,
    )
