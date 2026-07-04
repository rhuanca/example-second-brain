import tempfile
import unittest
from pathlib import Path

from second_brain.config import Settings
from second_brain.slack_bot import CAPTURE_HINT, is_allowed, process_message
from second_brain.vault import Vault


def _settings(vault_path):
    return Settings.from_env(
        {
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_ALLOWED_USER_ID": "42",
            "VAULT_PATH": str(vault_path),
            "ANTHROPIC_API_KEY": "sk-test",
            "SLACK_BOT_TOKEN": "xoxb",
            "SLACK_APP_TOKEN": "xapp",
            "SLACK_ALLOWED_USER_ID": "U_OWNER",
        }
    )


def _dm(text, user="U_OWNER"):
    return {"type": "message", "channel_type": "im", "user": user, "text": text,
            "channel": "D1", "ts": "1.0"}


class SlackProcessTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = _settings(self._tmp.name)
        self.vault = Vault(Path(self._tmp.name))
        self.said = []
        self.flags = []

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, event, **kw):
        kw.setdefault("run_ask", lambda q, **k: f"answer to: {q}")
        process_message(
            event,
            settings=self.settings,
            vault=self.vault,
            say=self.said.append,
            react=lambda: self.flags.append("react"),
            unreact=lambda: self.flags.append("unreact"),
            **kw,
        )

    def test_allow_list_predicate(self):
        self.assertTrue(is_allowed("U_OWNER", self.settings))
        self.assertFalse(is_allowed("U_OTHER", self.settings))
        self.assertFalse(is_allowed(None, self.settings))

    def test_owner_question_is_answered_with_feedback(self):
        self._run(_dm("what about agent memory?"))
        self.assertEqual(self.said, ["answer to: what about agent memory?"])
        self.assertEqual(self.flags, ["react", "unreact"])

    def test_url_gets_capture_hint_not_ask(self):
        called = []
        self._run(_dm("https://example.com/post"), run_ask=lambda q, **k: called.append(q))
        self.assertEqual(self.said, [CAPTURE_HINT])
        self.assertEqual(called, [])
        self.assertEqual(self.flags, [])  # no working indicator for the hint

    def test_other_user_ignored(self):
        self._run(_dm("hello", user="U_OTHER"))
        self.assertEqual(self.said, [])

    def test_non_dm_ignored(self):
        event = _dm("hi")
        event["channel_type"] = "channel"
        self._run(event)
        self.assertEqual(self.said, [])

    def test_bot_and_edited_messages_ignored(self):
        self._run({**_dm("x"), "bot_id": "B1"})
        self._run({**_dm("x"), "subtype": "message_changed"})
        self.assertEqual(self.said, [])

    def test_empty_text_ignored(self):
        self._run(_dm("   "))
        self.assertEqual(self.said, [])


if __name__ == "__main__":
    unittest.main()
