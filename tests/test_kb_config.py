import os
import unittest
from pathlib import Path

from second_brain.config import ConfigError
from second_brain.kb.config import DEFAULT_EMBED_MODEL, DEFAULT_MODEL, KbSettings


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

    def test_service_defaults(self):
        settings = KbSettings.from_env({"VAULT_PATH": "/tmp/vault"})

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8765)
        self.assertEqual(settings.embed_model, DEFAULT_EMBED_MODEL)
        self.assertEqual(settings.auth_tokens, ())
        self.assertEqual(settings.allowed_hosts, ())
        self.assertFalse(settings.web_allow_unauthenticated)
        self.assertTrue(settings.index_dir.is_absolute())
        self.assertNotIn(str(settings.vault_path), str(settings.index_dir))

    def test_reads_service_values(self):
        settings = KbSettings.from_env(
            {
                "VAULT_PATH": "/tmp/vault",
                "KB_INDEX_DIR": "/tmp/kb-index",
                "KB_EMBED_MODEL": "some/model",
                "KB_HOST": "localhost",
                "KB_PORT": "9000",
                "KB_AUTH_TOKENS": " laptop-token , cloud-token,, ",
                "KB_ALLOWED_HOSTS": "kb.example.com",
                "KB_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
                "KB_CF_ACCESS_AUD": "aud123",
                "KB_WEB_ALLOW_UNAUTHENTICATED": "true",
            }
        )
        self.assertEqual(settings.index_dir, Path("/tmp/kb-index").resolve())
        self.assertEqual(settings.embed_model, "some/model")
        self.assertEqual(settings.host, "localhost")
        self.assertEqual(settings.port, 9000)
        self.assertEqual(settings.auth_tokens, ("laptop-token", "cloud-token"))
        self.assertEqual(settings.allowed_hosts, ("kb.example.com",))
        self.assertEqual(settings.cf_access_team_domain, "team.cloudflareaccess.com")
        self.assertEqual(settings.cf_access_aud, "aud123")
        self.assertTrue(settings.web_allow_unauthenticated)

    def test_non_loopback_host_is_rejected(self):
        for host in ["0.0.0.0", "192.168.1.10", "example.com", "::"]:
            with self.subTest(host=host), self.assertRaises(ConfigError):
                KbSettings.from_env({"VAULT_PATH": "/tmp/vault", "KB_HOST": host})

    def test_ipv6_loopback_is_allowed(self):
        settings = KbSettings.from_env({"VAULT_PATH": "/tmp/vault", "KB_HOST": "::1"})
        self.assertEqual(settings.host, "::1")

    def test_chat_settings(self):
        defaults = KbSettings.from_env({"VAULT_PATH": "/tmp/vault"})
        self.assertEqual((defaults.chat_model, defaults.chat_effort), (DEFAULT_MODEL, "medium"))

        follows = KbSettings.from_env(
            {"VAULT_PATH": "/tmp/vault", "ANTHROPIC_MODEL": "claude-sonnet-5"}
        )
        self.assertEqual(follows.chat_model, "claude-sonnet-5")

        override = KbSettings.from_env(
            {
                "VAULT_PATH": "/tmp/vault",
                "ANTHROPIC_MODEL": "claude-sonnet-5",
                "KB_CHAT_MODEL": "claude-opus-5",
                "KB_CHAT_EFFORT": "HIGH",
            }
        )
        self.assertEqual((override.chat_model, override.chat_effort), ("claude-opus-5", "high"))

        with self.assertRaises(ConfigError):
            KbSettings.from_env({"VAULT_PATH": "/tmp/vault", "KB_CHAT_EFFORT": "turbo"})

    def test_access_settings_must_come_as_a_pair(self):
        for env in [
            {"KB_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com"},
            {"KB_CF_ACCESS_AUD": "aud123"},
        ]:
            with self.subTest(env=env), self.assertRaises(ConfigError):
                KbSettings.from_env({"VAULT_PATH": "/tmp/vault", **env})

    def test_bad_port_is_rejected(self):
        for port in ["abc", "0", "70000"]:
            with self.subTest(port=port), self.assertRaises(ConfigError):
                KbSettings.from_env({"VAULT_PATH": "/tmp/vault", "KB_PORT": port})

    def test_expands_environment_variables_in_paths(self):
        os.environ["KB_TEST_BASE"] = "/tmp/kb-base"
        self.addCleanup(os.environ.pop, "KB_TEST_BASE")
        settings = KbSettings.from_env(
            {
                "VAULT_PATH": "$KB_TEST_BASE/vault",
                "KB_INDEX_DIR": "${KB_TEST_BASE}/.kb-index",
            }
        )
        self.assertEqual(settings.vault_path, Path("/tmp/kb-base/vault").resolve())
        self.assertEqual(settings.index_dir, Path("/tmp/kb-base/.kb-index").resolve())
        self.assertNotIn("$", str(settings.index_dir))

    def test_expands_user_and_resolves(self):
        settings = KbSettings.from_env({"VAULT_PATH": "~/vault"})
        self.assertTrue(settings.vault_path.is_absolute())
        self.assertNotIn("~", str(settings.vault_path))


if __name__ == "__main__":
    unittest.main()
