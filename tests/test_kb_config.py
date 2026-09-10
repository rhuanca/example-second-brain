import unittest
from pathlib import Path

from second_brain.config import ConfigError
from second_brain.kb.config import DEFAULT_MODEL, KbSettings


class KbSettingsTest(unittest.TestCase):
    def test_loads_without_any_telegram_or_slack_vars(self):
        """The whole point of this loader: a read-only tool must not need the
        collector's credentials."""
        settings = KbSettings.from_env({"VAULT_PATH": "/tmp/vault"})

        self.assertEqual(settings.vault_path, Path("/tmp/vault").resolve())
        self.assertEqual(settings.anthropic_model, DEFAULT_MODEL)
        self.assertIsNone(settings.anthropic_api_key)

    def test_vault_path_is_required(self):
        with self.assertRaises(ConfigError):
            KbSettings.from_env({})

    def test_blank_vault_path_is_rejected(self):
        with self.assertRaises(ConfigError):
            KbSettings.from_env({"VAULT_PATH": "   "})

    def test_reads_optional_values(self):
        settings = KbSettings.from_env(
            {
                "VAULT_PATH": "/tmp/vault",
                "ANTHROPIC_API_KEY": "sk-test",
                "ANTHROPIC_MODEL": "claude-sonnet-5",
            }
        )
        self.assertEqual(settings.anthropic_api_key, "sk-test")
        self.assertEqual(settings.anthropic_model, "claude-sonnet-5")

    def test_blank_optional_values_fall_back(self):
        settings = KbSettings.from_env(
            {"VAULT_PATH": "/tmp/vault", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": " "}
        )
        self.assertIsNone(settings.anthropic_api_key)
        self.assertEqual(settings.anthropic_model, DEFAULT_MODEL)

    def test_expands_user_and_resolves(self):
        settings = KbSettings.from_env({"VAULT_PATH": "~/vault"})
        self.assertTrue(settings.vault_path.is_absolute())
        self.assertNotIn("~", str(settings.vault_path))


if __name__ == "__main__":
    unittest.main()
