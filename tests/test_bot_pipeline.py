import datetime
import tempfile
import unittest
from pathlib import Path

from second_brain.config import Settings
from second_brain.fetcher import Article, FetchError
from second_brain.models import Summary
from second_brain.summarizer import SummarizerError
from second_brain.bot import NO_URL_MESSAGE, handle_url
from second_brain.vault import Vault

DATE = datetime.date(2026, 6, 30)


def _settings(vault_path):
    return Settings.from_env(
        {
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_ALLOWED_USER_ID": "42",
            "VAULT_PATH": str(vault_path),
            "ANTHROPIC_API_KEY": "sk-test",
        }
    )


def _summary():
    return Summary(
        title="Agentic Patterns",
        tldr="How to build agent loops.",
        key_points=["Use tools"],
        tags=["agentic-dev"],
        prototype_ideas=["A planner loop"],
    )


class HandleUrlTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name))
        self.settings = _settings(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, text, **kw):
        kw.setdefault("today", lambda: DATE)
        return handle_url(text, vault=self.vault, settings=self.settings, **kw)

    def test_happy_path_writes_note_and_replies(self):
        result = self._run(
            "check https://example.com/post",
            fetch=lambda url: Article("Agentic Patterns", "body"),
            summarize=lambda *a, **k: _summary(),
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.note_path)
        self.assertTrue(result.note_path.exists())
        self.assertEqual(result.note_path.parent, self.vault.root)
        self.assertIn("Agentic Patterns", result.reply)
        self.assertIn("#agentic-dev", result.reply)
        self.assertIn("#article", result.reply)  # source tag auto-added

    def test_source_tag_reflects_the_fetcher(self):
        result = self._run(
            "https://youtu.be/dQw4w9WgXcQ",
            fetch=lambda url: Article("A Talk", "transcript", source="youtube"),
            summarize=lambda *a, **k: _summary(),
        )
        self.assertIn("#youtube", result.reply)
        import frontmatter

        post = frontmatter.load(str(result.note_path))
        self.assertIn("youtube", post["tags"])
        self.assertIn("agentic-dev", post["tags"])

    def test_no_url_returns_hint_and_writes_nothing(self):
        result = self._run("just a note, no link")
        self.assertFalse(result.ok)
        self.assertEqual(result.reply, NO_URL_MESSAGE)
        self.assertEqual(list(self.vault.iter_notes()), [])

    def test_duplicate_url_is_reported_without_second_note(self):
        args = dict(
            fetch=lambda url: Article("Agentic Patterns", "body"),
            summarize=lambda *a, **k: _summary(),
        )
        first = self._run("https://example.com/post", **args)
        self.assertTrue(first.ok)

        second = self._run("https://example.com/post/?utm_source=nl", **args)
        self.assertFalse(second.ok)
        self.assertIn("Already in your second brain", second.reply)
        self.assertEqual(len(list(self.vault.iter_notes())), 1)

    def test_fetch_failure_returns_message_and_writes_nothing(self):
        def boom(url):
            raise FetchError("blocked")

        result = self._run("https://example.com/post", fetch=boom)
        self.assertFalse(result.ok)
        self.assertIn("Couldn't read", result.reply)
        self.assertEqual(list(self.vault.iter_notes()), [])

    def test_write_duplicate_race_is_reported(self):
        from second_brain.vault import DuplicateNoteError

        # find_by_url misses, but write_note loses a race and refuses.
        self.vault.find_by_url = lambda url: None
        self.vault.write_note = lambda *a, **k: (_ for _ in ()).throw(
            DuplicateNoteError("https://example.com/post", Path("Resources/x.md"))
        )
        result = self._run(
            "https://example.com/post",
            fetch=lambda url: Article("t", "body"),
            summarize=lambda *a, **k: _summary(),
        )
        self.assertFalse(result.ok)
        self.assertIn("Already in your second brain", result.reply)

    def test_write_os_error_returns_message(self):
        self.vault.find_by_url = lambda url: None
        self.vault.write_note = lambda *a, **k: (_ for _ in ()).throw(
            PermissionError("disk full")
        )
        result = self._run(
            "https://example.com/post",
            fetch=lambda url: Article("t", "body"),
            summarize=lambda *a, **k: _summary(),
        )
        self.assertFalse(result.ok)
        self.assertIn("Couldn't save", result.reply)

    def test_summarize_failure_returns_message_and_writes_nothing(self):
        def boom(*a, **k):
            raise SummarizerError("no key")

        result = self._run(
            "https://example.com/post",
            fetch=lambda url: Article("t", "body"),
            summarize=boom,
        )
        self.assertFalse(result.ok)
        self.assertIn("Couldn't summarize", result.reply)
        self.assertEqual(list(self.vault.iter_notes()), [])


if __name__ == "__main__":
    unittest.main()
