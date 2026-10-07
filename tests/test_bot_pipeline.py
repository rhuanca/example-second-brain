import datetime
import tempfile
import unittest
from pathlib import Path

from second_brain.config import Settings
from second_brain.fetcher import Article, FetchError
from second_brain.models import Summary
from second_brain.summarizer import SummarizerError
from second_brain.bot import NO_URL_MESSAGE, handle_document, handle_url
from second_brain.vault import Vault

DATE = datetime.date(2026, 6, 30)

# Captures below the article-length bar are refused now, so test bodies have to
# look like articles. The marker stays at the front for the assertions.
FILLER = " " + "enough words here to count as an article. " * 12


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
            fetch=lambda url: Article("Agentic Patterns", "body" + FILLER),
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
            fetch=lambda url: Article("A Talk", "transcript" + FILLER, source="youtube"),
            summarize=lambda *a, **k: _summary(),
        )
        self.assertIn("#youtube", result.reply)
        import frontmatter

        post = frontmatter.load(str(result.note_path))
        self.assertIn("youtube", post["tags"])
        self.assertIn("agentic-dev", post["tags"])

    def test_every_capture_archives_the_source(self):
        # YouTube (transcript) …
        r1 = self._run(
            "https://youtu.be/dQw4w9WgXcQ",
            fetch=lambda url: Article(
                "A Talk", "the raw transcript" + FILLER, source="youtube", kind="transcript"
            ),
            summarize=lambda *a, **k: _summary(),
        )
        a1 = self.vault.root / "sources" / f"{r1.note_path.stem}.source.md"
        self.assertTrue(a1.exists())
        self.assertIn("the raw transcript", a1.read_text())

        # … and a plain article is archived too (the shift: always archive).
        r2 = self._run(
            "https://example.com/post",
            fetch=lambda url: Article("Post", "the article body" + FILLER, source="article"),
            summarize=lambda *a, **k: _summary(),
        )
        a2 = self.vault.root / "sources" / f"{r2.note_path.stem}.source.md"
        self.assertTrue(a2.exists())
        self.assertIn("the article body", a2.read_text())

    def test_no_url_returns_hint_and_writes_nothing(self):
        result = self._run("just a note, no link")
        self.assertFalse(result.ok)
        self.assertEqual(result.reply, NO_URL_MESSAGE)
        self.assertEqual(list(self.vault.iter_notes()), [])

    def test_a_page_that_gives_back_scraps_is_not_filed(self):
        """A Databricks post once arrived as a 37-character caption of an ad pixel
        (a poisoned reader cache) and was summarised and saved as a real note."""
        summarized = []
        result = self._run(
            "https://example.com/post",
            fetch=lambda url: Article("Ad pixel", "A 1x1 image, likely be a tacker probe"),
            summarize=lambda *a, **k: summarized.append(a) or _summary(),
        )

        self.assertIn("almost no text", result.reply)
        self.assertIsNone(result.note_path)
        self.assertFalse(result.ok)
        self.assertEqual(summarized, [])  # never sent to the model
        self.assertEqual(list(self.vault.iter_notes()), [])

    def test_duplicate_url_is_reported_without_second_note(self):
        args = dict(
            fetch=lambda url: Article("Agentic Patterns", "body" + FILLER),
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

    def test_reshared_youtube_link_is_reported_without_fetching(self):
        """A re-share mints a fresh ?si= token; it must still be caught, and
        caught before the expensive fetch/summarize."""
        self.vault.write_note(
            _summary(), "https://youtu.be/ve7AA01vplE?si=-2YhO5vh9RcEiSzu", DATE
        )

        def _boom(*a, **k):
            raise AssertionError("must not fetch/summarize a known link")

        result = self._run(
            "https://youtu.be/ve7AA01vplE?si=GxFa4HqDaut8XPNy",
            fetch=_boom,
            summarize=_boom,
        )
        self.assertFalse(result.ok)
        self.assertIn("Already in your second brain", result.reply)

    def test_write_duplicate_race_is_reported(self):
        from second_brain.vault import DuplicateNoteError

        # find_by_url misses, but write_note loses a race and refuses.
        self.vault.find_by_url = lambda url: None
        self.vault.write_note = lambda *a, **k: (_ for _ in ()).throw(
            DuplicateNoteError("https://example.com/post", Path("Resources/x.md"))
        )
        result = self._run(
            "https://example.com/post",
            fetch=lambda url: Article("t", "body" + FILLER),
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
            fetch=lambda url: Article("t", "body" + FILLER),
            summarize=lambda *a, **k: _summary(),
        )
        self.assertFalse(result.ok)
        self.assertIn("Couldn't save", result.reply)

    def test_summarize_failure_returns_message_and_writes_nothing(self):
        def boom(*a, **k):
            raise SummarizerError("no key")

        result = self._run(
            "https://example.com/post",
            fetch=lambda url: Article("t", "body" + FILLER),
            summarize=boom,
        )
        self.assertFalse(result.ok)
        self.assertIn("Couldn't summarize", result.reply)
        self.assertEqual(list(self.vault.iter_notes()), [])


if __name__ == "__main__":
    unittest.main()


class HandleDocumentTest(unittest.TestCase):
    """Uploads take the same path as links from "we have the text" onward."""

    PDF = b"%PDF-1.4 pretend bytes"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Vault(Path(self._tmp.name))
        self.settings = _settings(self._tmp.name)

    def _run(self, data=None, filename="paper.pdf", **kw):
        kw.setdefault(
            "extract",
            lambda data, name: Article("A Paper", "the paper text" + FILLER, source="pdf", kind="pdf"),
        )
        kw.setdefault("summarize", lambda *a, **k: _summary())
        kw.setdefault("today", lambda: DATE)
        return handle_document(
            self.PDF if data is None else data,
            filename,
            vault=self.vault,
            settings=self.settings,
            **kw,
        )

    def test_a_pdf_is_filed_with_its_hash_as_the_source(self):
        import hashlib

        result = self._run()

        self.assertTrue(result.ok)
        note = result.note_path.read_text()
        self.assertIn(f"source: file:sha256-{hashlib.sha256(self.PDF).hexdigest()}", note)
        self.assertIn("#pdf", result.reply)

    def test_the_text_is_archived_beside_the_note(self):
        result = self._run()
        archive = self.vault.root / "sources" / f"{result.note_path.stem}.source.md"
        self.assertIn("the paper text", archive.read_text())
        self.assertIn("kind: pdf", archive.read_text())

    def test_the_same_file_twice_is_a_duplicate_whatever_it_is_called(self):
        self._run(filename="paper.pdf")
        again = self._run(filename="renamed-copy.pdf")

        self.assertFalse(again.ok)
        self.assertIn("Already in your second brain", again.reply)
        self.assertEqual(len(list(self.vault.iter_notes())), 1)

    def test_a_different_file_is_not_a_duplicate(self):
        self._run()
        other = self._run(data=b"%PDF-1.4 different bytes")
        self.assertTrue(other.ok)
        self.assertEqual(len(list(self.vault.iter_notes())), 2)

    def test_a_pdf_that_cannot_be_read_saves_nothing(self):
        def no_text(data, name):
            raise FetchError("That PDF has no text layer — it looks like a scan.")

        result = self._run(extract=no_text)

        self.assertIn("no text layer", result.reply)
        self.assertIsNone(result.note_path)
        self.assertEqual(list(self.vault.iter_notes()), [])

    def test_scraps_are_refused_as_they_are_for_links(self):
        result = self._run(extract=lambda data, name: Article("x", "two words", source="pdf"))
        self.assertIn("almost no text", result.reply)
        self.assertEqual(list(self.vault.iter_notes()), [])
