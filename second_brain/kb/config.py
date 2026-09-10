"""Settings for the knowledge-base tools.

Deliberately narrower than `second_brain.config.Settings`, which requires the
collector's Telegram credentials. A read-only knowledge tool has no business
loading those, so this reads only what it actually needs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from second_brain.config import ConfigError

# Topic discovery reasons over the whole corpus and runs rarely, so it is worth
# a strong model; at ~7k input tokens the cost is cents per year.
DEFAULT_MODEL = "claude-opus-5"


@dataclass(frozen=True)
class KbSettings:
    """Validated configuration for the knowledge-base tools."""

    vault_path: Path
    anthropic_api_key: str | None = None
    anthropic_model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "KbSettings":
        """Build settings from a mapping (defaults to the process environment)."""
        if env is None:
            load_dotenv()
            env = os.environ

        raw_vault = _clean(env.get("VAULT_PATH"))
        if not raw_vault:
            raise ConfigError("VAULT_PATH is required")

        return cls(
            vault_path=Path(raw_vault).expanduser().resolve(),
            anthropic_api_key=_clean(env.get("ANTHROPIC_API_KEY")) or None,
            anthropic_model=_clean(env.get("ANTHROPIC_MODEL")) or DEFAULT_MODEL,
        )


def _clean(value: str | None) -> str:
    return (value or "").strip()
