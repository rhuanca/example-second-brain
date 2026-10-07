import asyncio
import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

FILLER = " " + "enough words here to count as an article. " * 12


def _summary():
    from second_brain.models import Summary

    return Summary(title="Agentic Patterns", tldr="How to build agent loops.", tags=["agentic-dev"])

from second_brain.bot import (
    ASK_USAGE,
    DUPLICATE,
    FAILED,
    MAX_PDF_BYTES,
    NOT_A_PDF_MESSAGE,
    NO_URL_MESSAGE,
    TOO_BIG_MESSAGE,
    REACTIONS,
    SAVED,
    WORKING,
    is_allowed,
    make_ask_handler,
    make_document_handler,
    make_handler,
)
from second_brain.fetcher import Article
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


class FakeFile:
    def __init__(self, data):
        self.data = data

    async def download_as_bytearray(self):
        return bytearray(self.data)


class FakeDocument:
    def __init__(self, data=b"%PDF-1.4", name="paper.pdf", mime="application/pdf", size=None):
        self.data = data
        self.file_name = name
        self.mime_type = mime
        self.file_size = len(data) if size is None else size
        self.downloads = 0

    async def get_file(self):
        self.downloads += 1
        return FakeFile(self.data)


class FakeMessage:
    def __init__(self, text, document=None):
        self.text = text
        self.document = document
        self.replies = []
        self.reactions = []  # every set_reaction call, in order (None = cleared)
        self.chat = FakeChat()

    async def set_reaction(self, emoji):
        self.reactions.append(emoji)

    async def reply_text(self, text):
        self.replies.append(text)


def _update(user_id, text, document=None):
    message = FakeMessage(text, document=document)
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

    def test_a_failing_indicator_is_logged_but_never_breaks_the_reply(self):
        """Cosmetic, so it is swallowed -- but silence would make a missing
        indicator impossible to tell from one that was never attempted."""
        handler = make_handler(self.settings, self.vault)
        update, message = _update(42, "no link here")

        async def refuses(action):
            raise RuntimeError("Bad Gateway")

        message.chat.send_action = refuses
        with self.assertLogs("second_brain.bot", level="WARNING") as logged:
            asyncio.run(handler(update, None))

        self.assertEqual(message.replies, [NO_URL_MESSAGE])  # the reply still lands
        self.assertIn("RuntimeError", logged.output[0])
        self.assertIn("Bad Gateway", logged.output[0])

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
            fetch=lambda url: Article("Agentic Patterns", "body" + FILLER),
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


class DocumentHandlerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = _settings(self._tmp.name)
        self.vault = Vault(Path(self._tmp.name))

    def handler(self, **kwargs):
        kwargs.setdefault(
            "extract", lambda data, name: Article("A Paper", "the paper text" + FILLER, source="pdf", kind="pdf")
        )
        kwargs.setdefault("summarize", lambda *a, **k: _summary())
        kwargs.setdefault("today", lambda: datetime.date(2026, 6, 30))
        return make_document_handler(self.settings, self.vault, **kwargs)

    def run_handler(self, document, user_id=42, **kwargs):
        update, message = _update(user_id, None, document=document)
        asyncio.run(self.handler(**kwargs)(update, None))
        return message

    def test_a_pdf_is_captured_with_typing_shown(self):
        document = FakeDocument()
        message = self.run_handler(document)

        self.assertEqual(document.downloads, 1)
        self.assertIn("typing", message.chat.actions)
        self.assertIn("Agentic Patterns", message.replies[0])
        self.assertEqual(len(list(self.vault.iter_notes())), 1)

    def test_other_users_are_ignored_entirely(self):
        document = FakeDocument()
        message = self.run_handler(document, user_id=99)

        self.assertEqual(message.replies, [])
        self.assertEqual(document.downloads, 0)  # never even downloaded
        self.assertEqual(list(self.vault.iter_notes()), [])

    def test_anything_that_is_not_a_pdf_says_so_without_downloading(self):
        document = FakeDocument(name="notes.docx", mime="application/vnd.openxmlformats")
        message = self.run_handler(document)

        self.assertEqual(message.replies, [NOT_A_PDF_MESSAGE])
        self.assertEqual(document.downloads, 0)

    def test_a_file_bigger_than_telegram_will_send_is_refused_early(self):
        document = FakeDocument(size=MAX_PDF_BYTES + 1)
        message = self.run_handler(document)

        self.assertEqual(message.replies, [TOO_BIG_MESSAGE])
        self.assertEqual(document.downloads, 0)

    def test_a_scan_is_reported_and_nothing_is_saved(self):
        from second_brain.fetcher import FetchError

        def no_text(data, name):
            raise FetchError("That PDF has no text layer — it looks like a scan.")

        message = self.run_handler(FakeDocument(), extract=no_text)

        self.assertIn("no text layer", message.replies[0])
        self.assertEqual(list(self.vault.iter_notes()), [])


class ReactionTest(unittest.TestCase):
    """The dots are ephemeral and have been missed; a reaction stays on the
    message, so the chat history says what became of every link."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = _settings(self._tmp.name)
        self.vault = Vault(Path(self._tmp.name))

    def capture(self, text="https://example.com/post", user_id=42, **kw):
        from second_brain.fetcher import Article

        kw.setdefault("fetch", lambda url: Article("A Post", "body" + FILLER))
        kw.setdefault("summarize", lambda *a, **k: _summary())
        kw.setdefault("today", lambda: datetime.date(2026, 6, 30))
        handler = make_handler(self.settings, self.vault, **kw)
        update, message = _update(user_id, text)
        asyncio.run(handler(update, None))
        return message

    def test_working_then_the_outcome(self):
        message = self.capture()
        self.assertEqual(message.reactions, [WORKING, REACTIONS[SAVED]])

    def test_a_duplicate_ends_differently_from_a_failure(self):
        self.capture()
        self.assertEqual(self.capture().reactions[-1], REACTIONS[DUPLICATE])

        from second_brain.fetcher import FetchError

        def dead(url):
            raise FetchError("dead link")

        failed = self.capture("https://example.com/other", fetch=dead)
        self.assertEqual(failed.reactions[-1], REACTIONS[FAILED])

    def test_a_message_with_no_link_leaves_no_mark(self):
        message = self.capture("hello there")
        self.assertEqual(message.reactions, [WORKING, None])

    def test_strangers_get_nothing(self):
        message = self.capture(user_id=99)
        self.assertEqual(message.reactions, [])

    def test_a_refused_reaction_never_breaks_the_reply(self):
        message_box = {}

        async def refuses(emoji):
            message_box.setdefault("tried", []).append(emoji)
            raise RuntimeError("REACTION_INVALID")

        from second_brain.fetcher import Article

        handler = make_handler(
            self.settings,
            self.vault,
            fetch=lambda url: Article("A Post", "body" + FILLER),
            summarize=lambda *a, **k: _summary(),
            today=lambda: datetime.date(2026, 6, 30),
        )
        update, message = _update(42, "https://example.com/post")
        message.set_reaction = refuses

        with self.assertLogs("second_brain.bot", level="WARNING") as logged:
            asyncio.run(handler(update, None))

        self.assertEqual(len(message.replies), 1)  # the note still lands
        self.assertTrue(any("REACTION_INVALID" in line for line in logged.output))

    def test_ask_marks_and_then_clears(self):
        handler = make_ask_handler(self.settings, self.vault, run_ask=lambda *a, **k: "an answer")
        update, message = _update(42, "/ask what did I save?")
        asyncio.run(handler(update, None))

        self.assertEqual(message.reactions, [WORKING, None])
        self.assertEqual(message.replies, ["an answer"])

    def test_an_uploaded_pdf_is_marked_too(self):
        from second_brain.fetcher import Article

        handler = make_document_handler(
            self.settings,
            self.vault,
            extract=lambda data, name: Article("A Paper", "text" + FILLER, source="pdf"),
            summarize=lambda *a, **k: _summary(),
            today=lambda: datetime.date(2026, 6, 30),
        )
        update, message = _update(42, None, document=FakeDocument())
        asyncio.run(handler(update, None))

        self.assertEqual(message.reactions, [WORKING, REACTIONS[SAVED]])

    def test_a_file_refused_before_the_download_is_not_marked_as_working(self):
        handler = make_document_handler(self.settings, self.vault)
        update, message = _update(42, None, document=FakeDocument(name="x.docx", mime="text/plain"))
        asyncio.run(handler(update, None))

        self.assertEqual(message.reactions, [])  # nothing started, nothing to mark
