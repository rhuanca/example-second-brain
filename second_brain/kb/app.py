"""The knowledge-base service: one ASGI app, one port, one systemd unit.

`/mcp` is the MCP endpoint for agents; everything else is the browse UI. Both
read the same `Library`. The MCP app is mounted last, at the root, so it answers
exactly `/mcp` (no trailing-slash redirect) while the web routes match first.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable

import anyio
from fastapi import FastAPI

from second_brain.kb.auth import McpAuthMiddleware
from second_brain.kb.config import KbSettings
from second_brain.kb.embeddings import MODELS_DIR, Embedder, FastEmbedder
from second_brain.kb.mcp_server import build_mcp, http_app
from second_brain.kb.retrieval import Library
from second_brain.kb.tools import KbTools
from second_brain.vault import Vault


def create_app(
    settings: KbSettings,
    *,
    library: Library | None = None,
    embedder: Embedder | None = None,
    answer: Callable[..., str] | None = None,
) -> FastAPI:
    """Build the service. `library`, `embedder` and `answer` are injectable for tests."""
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

    app.mount("/", mcp_app)
    app.add_middleware(McpAuthMiddleware, tokens=settings.auth_tokens)
    return app
