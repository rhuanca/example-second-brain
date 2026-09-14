"""The knowledge-base service: one ASGI app, one port, one systemd unit.

`/mcp` is the MCP endpoint for agents; everything else is the browse UI. Both
read the same `Library`. The MCP app is mounted last, at the root, so it answers
exactly `/mcp` (no trailing-slash redirect) while the web routes match first.

`/mcp` is guarded by a bearer token, every other path by Cloudflare Access (or
the explicit local-dev opt-out); see `auth.py`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable

from pathlib import Path

import anyio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from second_brain.kb.auth import AccessVerifier, McpAuthMiddleware, WebAuthMiddleware
from second_brain.kb.config import KbSettings
from second_brain.kb.embeddings import MODELS_DIR, Embedder, FastEmbedder
from second_brain.kb.mcp_server import build_mcp, http_app
from second_brain.kb.retrieval import Library
from second_brain.kb.tools import KbTools
from second_brain.kb.web import SecurityHeadersMiddleware, build_router
from second_brain.vault import Vault

STATIC_DIR = Path(__file__).with_name("static")


def create_app(
    settings: KbSettings,
    *,
    library: Library | None = None,
    embedder: Embedder | None = None,
    answer: Callable[..., str] | None = None,
    access_verifier: AccessVerifier | None = None,
    chat_client=None,
) -> FastAPI:
    """Build the service. Collaborators are injectable for tests."""
    if library is None:
        embedder = embedder or FastEmbedder(
            settings.embed_model, settings.index_dir / MODELS_DIR
        )
        library = Library(Vault(settings.vault_path), settings.index_dir, embedder)

    tools = KbTools(library, settings=settings, **({"answer": answer} if answer else {}))
    mcp = build_mcp(tools)
    mcp_app = http_app(mcp, settings)  # must exist before session_manager is used

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A mounted sub-app's lifespan never runs, so the host has to start the
        # MCP session manager itself.
        await anyio.to_thread.run_sync(lambda: library.refresh_if_stale(force=True))
        async with mcp.session_manager.run():
            yield

    # No /docs or /openapi.json: they would be unauthenticated pages describing
    # the service to anyone who reaches it.
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.library = library
    app.state.settings = settings

    if access_verifier is None and settings.cf_access_team_domain and settings.cf_access_aud:
        access_verifier = AccessVerifier(
            settings.cf_access_team_domain, settings.cf_access_aud
        )

    app.include_router(build_router(library, settings, chat_client=chat_client))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/", mcp_app)
    # Each middleware guards its own paths: bearer token for /mcp, Access for the rest.
    app.add_middleware(
        WebAuthMiddleware,
        verifier=access_verifier,
        allow_unauthenticated=settings.web_allow_unauthenticated,
    )
    app.add_middleware(McpAuthMiddleware, tokens=settings.auth_tokens)
    app.add_middleware(SecurityHeadersMiddleware)
    return app
