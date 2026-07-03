import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from second_brain.bot import (
    ASK_USAGE,
    NO_URL_MESSAGE,
    is_allowed,
    make_ask_handler,
    make_handler,
)
from second_brain.config import Settings
from second_brain.vault import Vault


def _settings(vault_path):
    return Settings.from_env(
        {
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_ALLOWED_USER_ID": "42",
            "VAULT_PATH": str(vault_path),
            "ANTHROPIC_API_KEY": "sk-test",
        }
    )


class FakeChat:
    def __init__(self):
        self.actions = []

    async def send_action(self, action):
        self.actions.append(action)


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []
        self.chat = FakeChat()

    async def reply_text(self, text):
        self.replies.append(text)


def _update(user_id, text):
    message = FakeMessage(text)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if user_id is not None else None,
        effective_message=message,
    ), message


class AllowListTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = _settings(self._tmp.name)
        self.vault = Vault(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_is_allowed_predicate(self):
        self.assertTrue(is_allowed(42, self.settings))
        self.assertFalse(is_allowed(99, self.settings))
        self.assertFalse(is_allowed(None, self.settings))

    def test_handler_replies_to_owner(self):
        handler = make_handler(self.settings, self.vault)
        update, message = _update(42, "no link here")
        asyncio.run(handler(update, None))
        self.assertEqual(message.replies, [NO_URL_MESSAGE])

    def test_handler_shows_typing_for_owner(self):
        handler = make_handler(self.settings, self.vault)
        update, message = _update(42, "no link here")
        asyncio.run(handler(update, None))
        self.assertIn("typing", message.chat.actions)

    def test_handler_ignores_other_users(self):
        handler = make_handler(self.settings, self.vault)
        update, message = _update(99, "no link here")
        asyncio.run(handler(update, None))
        self.assertEqual(message.replies, [])
        self.assertEqual(message.chat.actions, [])  # no typing for strangers

    def test_handler_happy_path_replies_with_summary(self):
        from second_brain.fetcher import Article
        from second_brain.models import Summary

        summary = Summary(
            title="Agentic Patterns",
            tldr="How to build agent loops.",
            tags=["agentic-dev"],
        )
        handler = make_handler(
            self.settings,
            self.vault,
            fetch=lambda url: Article("Agentic Patterns", "body"),
            summarize=lambda *a, **k: summary,
            today=lambda: __import__("datetime").date(2026, 6, 30),
        )
        update, message = _update(42, "https://example.com/post")
        asyncio.run(handler(update, None))
        self.assertEqual(len(message.replies), 1)
        self.assertIn("Agentic Patterns", message.replies[0])
        self.assertEqual(len(list(self.vault.iter_notes())), 1)


class AskHandlerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = _settings(self._tmp.name)
        self.vault = Vault(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_answers_owner_question(self):
        seen = {}

        def fake_ask(question, *, vault, settings):
            seen["q"] = question
            return "here is your answer"

        handler = make_ask_handler(self.settings, self.vault, run_ask=fake_ask)
        update, message = _update(42, "/ask what about agent memory?")
        asyncio.run(handler(update, None))
        self.assertEqual(message.replies, ["here is your answer"])
        self.assertEqual(seen["q"], "what about agent memory?")
        self.assertIn("typing", message.chat.actions)

    def test_empty_question_shows_usage(self):
        handler = make_ask_handler(
            self.settings, self.vault, run_ask=lambda *a, **k: "should not run"
        )
        update, message = _update(42, "/ask")
        asyncio.run(handler(update, None))
        self.assertEqual(message.replies, [ASK_USAGE])

    def test_ignores_other_users(self):
        handler = make_ask_handler(
            self.settings, self.vault, run_ask=lambda *a, **k: "nope"
        )
        update, message = _update(99, "/ask anything")
        asyncio.run(handler(update, None))
        self.assertEqual(message.replies, [])


if __name__ == "__main__":
    unittest.main()
