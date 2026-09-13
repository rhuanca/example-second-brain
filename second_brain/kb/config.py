"""Settings for the knowledge-base tools.

Deliberately narrower than `second_brain.config.Settings`, which requires the
collector's Telegram credentials. A read-only knowledge tool has no business
loading those, so this reads only what it actually needs.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from second_brain.config import ConfigError

# Topic discovery reasons over the whole corpus and runs rarely, so it is worth
# a strong model; at ~7k input tokens the cost is cents per year.
DEFAULT_MODEL = "claude-opus-5"

# Small, English, 384 dimensions, runs on CPU through ONNX -- the fastembed default.
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"

# Derived data lives outside the vault so it never pollutes Obsidian or sync.
DEFAULT_INDEX_DIR = "~/.local/share/second-brain-kb"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@dataclass(frozen=True)
class KbSettings:
    """Validated configuration for the knowledge-base tools."""

    vault_path: Path
    anthropic_api_key: str | None = None
    anthropic_model: str = DEFAULT_MODEL
    index_dir: Path = field(
        default_factory=lambda: Path(DEFAULT_INDEX_DIR).expanduser().resolve()
    )
    embed_model: str = DEFAULT_EMBED_MODEL
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # One token per client, so a laptop can be revoked without breaking the rest.
    auth_tokens: tuple[str, ...] = ()
    # Public hostnames the service answers to behind the tunnel (e.g. kb.example.com).
    allowed_hosts: tuple[str, ...] = ()
    cf_access_team_domain: str | None = None
    cf_access_aud: str | None = None
    web_allow_unauthenticated: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "KbSettings":
        """Build settings from a mapping (defaults to the process environment)."""
        if env is None:
            load_dotenv()
            env = os.environ

        raw_vault = _clean(env.get("VAULT_PATH"))
        if not raw_vault:
            raise ConfigError("VAULT_PATH is required")

        host = _clean(env.get("KB_HOST")) or DEFAULT_HOST
        if not _is_loopback(host):
            # The tunnel connects locally; binding wider would expose the vault
            # to the LAN and bypass the edge authentication entirely.
            raise ConfigError(f"KB_HOST must be a loopback address, got {host!r}")

        team_domain = _clean(env.get("KB_CF_ACCESS_TEAM_DOMAIN")) or None
        audience = _clean(env.get("KB_CF_ACCESS_AUD")) or None
        if bool(team_domain) != bool(audience):
            # Half a configuration would silently leave the browse UI closed.
            raise ConfigError(
                "KB_CF_ACCESS_TEAM_DOMAIN and KB_CF_ACCESS_AUD must be set together"
            )

        return cls(
            vault_path=_path(raw_vault),
            anthropic_api_key=_clean(env.get("ANTHROPIC_API_KEY")) or None,
            anthropic_model=_clean(env.get("ANTHROPIC_MODEL")) or DEFAULT_MODEL,
            index_dir=_path(_clean(env.get("KB_INDEX_DIR")) or DEFAULT_INDEX_DIR),
            embed_model=_clean(env.get("KB_EMBED_MODEL")) or DEFAULT_EMBED_MODEL,
            host=host,
            port=_port(env.get("KB_PORT")),
            auth_tokens=_csv(env.get("KB_AUTH_TOKENS")),
            allowed_hosts=_csv(env.get("KB_ALLOWED_HOSTS")),
            cf_access_team_domain=team_domain,
            cf_access_aud=audience,
            web_allow_unauthenticated=_bool(env.get("KB_WEB_ALLOW_UNAUTHENTICATED")),
        )


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _path(value: str) -> Path:
    # .env files don't expand variables, so `$PWD/.kb-index` would otherwise
    # become a directory literally named "$PWD".
    return Path(os.path.expandvars(value)).expanduser().resolve()


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(part for part in (p.strip() for p in _clean(value).split(",")) if part)


def _bool(value: str | None) -> bool:
    return _clean(value).lower() in {"1", "true", "yes", "on"}


def _port(value: str | None) -> int:
    raw = _clean(value)
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        raise ConfigError(f"KB_PORT must be an integer, got {raw!r}") from None
    if not 0 < port < 65536:
        raise ConfigError(f"KB_PORT out of range: {port}")
    return port


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
