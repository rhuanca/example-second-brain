"""Application settings, loaded and validated from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-sonnet-4-6"


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Validated runtime configuration.

    `anthropic_api_key` is optional at load time: the key is provided later and
    tests mock the LLM, so the bot can be built and unit-tested without it. It is
    required only when a summary is actually requested (checked by the summarizer).
    """

    telegram_bot_token: str
    telegram_allowed_user_id: int
    vault_path: Path
    anthropic_model: str = DEFAULT_MODEL
    anthropic_api_key: str | None = None
    medium_cookie: str | None = None
    supadata_api_key: str | None = None
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    slack_allowed_user_id: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build Settings from a mapping (defaults to the process environment).

        When `env` is None the process environment is used and a local `.env`
        file is loaded first (real env vars still take precedence).
        """
        if env is None:
            load_dotenv()
            env = os.environ

        token = _require(env, "TELEGRAM_BOT_TOKEN")

        raw_user_id = _require(env, "TELEGRAM_ALLOWED_USER_ID")
        try:
            user_id = int(raw_user_id)
        except ValueError as exc:
            raise ConfigError(
                f"TELEGRAM_ALLOWED_USER_ID must be an integer, got {raw_user_id!r}"
            ) from exc
        if user_id <= 0:
            raise ConfigError(
                f"TELEGRAM_ALLOWED_USER_ID must be a positive id, got {user_id}"
            )

        # Resolve to an absolute path now (cwd is the project root at startup) so
        # the vault location never depends on the process's cwd later. Relative
        # values like "./vault" are intentionally supported for local dev.
        vault_path = Path(_require(env, "VAULT_PATH")).expanduser().resolve()

        model = env.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
        api_key = env.get("ANTHROPIC_API_KEY", "").strip() or None
        medium_cookie = env.get("MEDIUM_COOKIE", "").strip() or None
        supadata_api_key = env.get("SUPADATA_API_KEY", "").strip() or None

        return cls(
            telegram_bot_token=token,
            telegram_allowed_user_id=user_id,
            vault_path=vault_path,
            anthropic_model=model,
            anthropic_api_key=api_key,
            medium_cookie=medium_cookie,
            supadata_api_key=supadata_api_key,
            slack_bot_token=env.get("SLACK_BOT_TOKEN", "").strip() or None,
            slack_app_token=env.get("SLACK_APP_TOKEN", "").strip() or None,
            slack_allowed_user_id=env.get("SLACK_ALLOWED_USER_ID", "").strip() or None,
        )


def _require(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigError(f"Required environment variable {key} is missing or empty")
    return value
