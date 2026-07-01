"""Entry point: build Settings, ensure the vault, and start long-polling."""

from __future__ import annotations

from second_brain.bot import build_application
from second_brain.config import Settings
from second_brain.vault import Vault


def main() -> None:
    settings = Settings.from_env()
    vault = Vault(settings.vault_path)
    vault.ensure_folders()

    app = build_application(settings, vault)
    print(f"Second Brain bot running. Vault: {settings.vault_path}")
    app.run_polling()


if __name__ == "__main__":
    main()
