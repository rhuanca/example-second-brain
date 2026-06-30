import unittest
from pathlib import Path

from second_brain.config import DEFAULT_MODEL, ConfigError, Settings


def _valid_env(**overrides):
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "TELEGRAM_ALLOWED_USER_ID": "42",
        "VAULT_PATH": "/tmp/vault",
        "ANTHROPIC_MODEL": "claude-opus-4-8",
        "ANTHROPIC_API_KEY": "sk-test",
    }
    env.update(overrides)
    return env


class SettingsTest(unittest.TestCase):
    def test_valid_env_populates_settings(self):
        s = Settings.from_env(_valid_env())
        self.assertEqual(s.telegram_bot_token, "123:abc")
        self.assertEqual(s.telegram_allowed_user_id, 42)
        self.assertEqual(s.vault_path, Path("/tmp/vault"))
        self.assertEqual(s.anthropic_model, "claude-opus-4-8")
        self.assertEqual(s.anthropic_api_key, "sk-test")

    def test_relative_vault_path_resolved_to_absolute(self):
        s = Settings.from_env(_valid_env(VAULT_PATH="relative/vault"))
        self.assertTrue(s.vault_path.is_absolute())
        self.assertTrue(str(s.vault_path).endswith("relative/vault"))

    def test_model_defaults_when_absent(self):
        env = _valid_env()
        del env["ANTHROPIC_MODEL"]
        s = Settings.from_env(env)
        self.assertEqual(s.anthropic_model, DEFAULT_MODEL)

    def test_api_key_optional_becomes_none(self):
        env = _valid_env()
        del env["ANTHROPIC_API_KEY"]
        s = Settings.from_env(env)
        self.assertIsNone(s.anthropic_api_key)

    def test_missing_required_var_raises(self):
        env = _valid_env()
        del env["TELEGRAM_BOT_TOKEN"]
        with self.assertRaises(ConfigError) as ctx:
            Settings.from_env(env)
        self.assertIn("TELEGRAM_BOT_TOKEN", str(ctx.exception))

    def test_empty_required_var_raises(self):
        with self.assertRaises(ConfigError):
            Settings.from_env(_valid_env(VAULT_PATH="   "))

    def test_non_integer_user_id_raises(self):
        with self.assertRaises(ConfigError) as ctx:
            Settings.from_env(_valid_env(TELEGRAM_ALLOWED_USER_ID="notanint"))
        self.assertIn("TELEGRAM_ALLOWED_USER_ID", str(ctx.exception))

    def test_non_positive_user_id_raises(self):
        with self.assertRaises(ConfigError):
            Settings.from_env(_valid_env(TELEGRAM_ALLOWED_USER_ID="0"))

    def test_whitespace_api_key_becomes_none(self):
        s = Settings.from_env(_valid_env(ANTHROPIC_API_KEY="   "))
        self.assertIsNone(s.anthropic_api_key)

    def test_whitespace_model_uses_default(self):
        s = Settings.from_env(_valid_env(ANTHROPIC_MODEL="   "))
        self.assertEqual(s.anthropic_model, DEFAULT_MODEL)

    def test_vault_path_expanduser(self):
        s = Settings.from_env(_valid_env(VAULT_PATH="~/myvault"))
        self.assertTrue(s.vault_path.is_absolute())
        self.assertTrue(str(s.vault_path).endswith("myvault"))


if __name__ == "__main__":
    unittest.main()
