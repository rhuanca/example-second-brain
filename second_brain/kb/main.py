"""Entry point for the knowledge-base service: validate config, serve on loopback."""

from __future__ import annotations

import logging

import uvicorn

from second_brain.config import ConfigError
from second_brain.kb.app import create_app
from second_brain.kb.config import KbSettings


def main() -> None:
    settings = KbSettings.from_env()
    if not settings.auth_tokens:
        # Without a token /mcp would refuse everything; say so up front instead.
        raise ConfigError("KB_AUTH_TOKENS is required (comma-separated, one per client)")
    if not settings.vault_path.is_dir():
        raise ConfigError(f"VAULT_PATH is not a directory: {settings.vault_path}")

    logging.basicConfig(level=logging.INFO)
    print(
        f"Second Brain knowledge base on http://{settings.host}:{settings.port} "
        f"(vault: {settings.vault_path}, index: {settings.index_dir})"
    )
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
