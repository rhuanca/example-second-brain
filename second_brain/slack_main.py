"""Entry point for the Slack query bot: validate Slack config, start Socket Mode."""

from __future__ import annotations

from second_brain import slack_bot
from second_brain.config import ConfigError, Settings
from second_brain.vault import Vault


def main() -> None:
    settings = Settings.from_env()

    missing = [
        name
        for name, value in (
            ("SLACK_BOT_TOKEN", settings.slack_bot_token),
            ("SLACK_APP_TOKEN", settings.slack_app_token),
            ("SLACK_ALLOWED_USER_ID", settings.slack_allowed_user_id),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"Slack bot needs: {', '.join(missing)} (set them in .env)")

    vault = Vault(settings.vault_path)
    vault.ensure_folders()

    print(f"Second Brain Slack bot running. Vault: {settings.vault_path}")
    slack_bot.run(settings, vault)


if __name__ == "__main__":
    main()
