"""Application-level authentication for the knowledge-base service.

Cloudflare Access is the front door, but this layer assumes the edge has been
bypassed. Two consumers, two checks:

- Agents (`/mcp`): a known bearer token, checked in constant time, rejected here
  -- before the MCP app runs and so before anything on disk is read. Tokens are
  accepted from the `Authorization` header only; a token in the query string is
  refused outright, because URLs end up in logs, shell history and referrers.
- Browsers (everything else): the signed `Cf-Access-Jwt-Assertion` that Access
  attaches after its own sign-in. A request that did not come through Access has
  no valid assertion and is refused. With Access not configured, the browse UI
  is closed unless `KB_WEB_ALLOW_UNAUTHENTICATED` is set for local use.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable, Sequence
from urllib.parse import parse_qs

import anyio
import jwt
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

ACCESS_JWT_HEADER = b"cf-access-jwt-assertion"

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


class AccessVerifier:
    """Validates Cloudflare Access application tokens (RS256, audience, issuer, expiry).

    `key_resolver` maps a token to its public key; by default it fetches the
    team's published certs and caches them, and tests inject a local key.
    """

    def __init__(
        self,
        team_domain: str,
        audience: str,
        *,
        key_resolver: Callable[[str], object] | None = None,
    ):
        domain = team_domain.strip().removeprefix("https://").rstrip("/")
        self.issuer = f"https://{domain}"
        self.audience = audience
        if key_resolver is None:
            client = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs")
            key_resolver = lambda token: client.get_signing_key_from_jwt(token).key
        self._key_resolver = key_resolver

    def verify(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            key = self._key_resolver(token)
            jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except (jwt.PyJWTError, ValueError) as exc:
            logger.warning("access token rejected: %s", type(exc).__name__)
            return False
        return True


class WebAuthMiddleware:
    """Guards every non-`/mcp` path: Access JWT, explicit dev opt-out, or 403."""

    def __init__(
        self,
        app: ASGIApp,
        verifier: AccessVerifier | None,
        allow_unauthenticated: bool = False,
    ):
        self.app = app
        self.verifier = verifier
        self.allow_unauthenticated = allow_unauthenticated

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_mcp_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        if self.verifier is not None:
            token = _header(scope, ACCESS_JWT_HEADER)
            # Fetching the certs is blocking network I/O; keep it off the loop.
            if await anyio.to_thread.run_sync(self.verifier.verify, token):
                await self.app(scope, receive, send)
                return
            _log_rejection(scope, "missing or invalid Access token")
            message = "Forbidden: sign in through Cloudflare Access."
        elif self.allow_unauthenticated:
            await self.app(scope, receive, send)
            return
        else:
            message = (
                "The browse UI is closed: configure KB_CF_ACCESS_TEAM_DOMAIN and "
                "KB_CF_ACCESS_AUD, or set KB_WEB_ALLOW_UNAUTHENTICATED=true for local use."
            )
        response = PlainTextResponse(message, status_code=403)
        await response(scope, receive, send)


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
