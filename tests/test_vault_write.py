import datetime
import tempfile
import unittest
from pathlib import Path

from second_brain.models import Summary
from second_brain.vault import DuplicateNoteError, Vault

DATE = datetime.date(2026, 6, 30)


def _summary(**overrides):
    data = dict(
        title="Building Agentic Systems",
        tldr="A guide.",
        key_points=["a"],
        tags=["agentic-dev"],
        prototype_ideas=["idea"],
    )
    data.update(overrides)
    return Summary(**data)


class TranscriptWriteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_transcript_companion_written_and_linked(self):
        import frontmatter

        note_path = self.vault.write_note(
            _summary(title="Agent Memory"),
            "https://youtu.be/abc",
            DATE,
            transcript="the full transcript body",
            source_type="youtube",
        )
        stem = note_path.stem
        tpath = self.vault.root / "transcripts" / f"{stem}.transcript.md"
        self.assertTrue(tpath.exists())

        # transcript file holds the raw text and links back to the note
        tpost = frontmatter.load(str(tpath))
        self.assertEqual(tpost.content, "the full transcript body")
        self.assertEqual(tpost["note"], f"[[{stem}]]")

        # note links down to the transcript
        npost = frontmatter.load(str(note_path))
        self.assertEqual(
            npost["transcript"], f"[[transcripts/{stem}.transcript]]"
        )

    def test_no_transcript_means_no_transcripts_folder(self):
        self.vault.write_note(_summary(), "https://example.com/post", DATE)
        self.assertFalse((self.vault.root / "transcripts").exists())

    def test_blank_transcript_is_skipped(self):
        self.vault.write_note(
            _summary(), "https://example.com/post", DATE, transcript="   "
        )
        self.assertFalse((self.vault.root / "transcripts").exists())


class VaultWriteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_ensure_folders_creates_vault_root(self):
        self.vault.ensure_folders()
        self.assertTrue(self.vault.root.is_dir())

    def test_write_note_lands_flat_at_vault_root(self):
        path = self.vault.write_note(
            _summary(), "https://example.com/post", DATE
        )
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self.vault.root)
        self.assertEqual(path.name, "2026-06-30-building-agentic-systems.md")

    def test_write_creates_missing_vault_root(self):
        # Point at a not-yet-created dir: write_note must create it.
        missing = Vault(Path(self._tmp.name) / "sub")
        path = missing.write_note(_summary(), "https://example.com/x", DATE)
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, missing.root)

    def test_is_duplicate_detects_written_note(self):
        url = "https://example.com/post"
        self.assertFalse(self.vault.is_duplicate(url))
        self.vault.write_note(_summary(), url, DATE)
        self.assertTrue(self.vault.is_duplicate(url))

    def test_duplicate_detected_across_tracking_variants(self):
        self.vault.write_note(_summary(), "https://example.com/post", DATE)
        self.assertTrue(
            self.vault.is_duplicate("https://example.com/post/?utm_source=nl")
        )

    def test_second_write_raises_duplicate(self):
        url = "https://example.com/post"
        first = self.vault.write_note(_summary(), url, DATE)
        with self.assertRaises(DuplicateNoteError) as ctx:
            self.vault.write_note(_summary(title="Different Title"), url, DATE)
        self.assertEqual(ctx.exception.existing, first)
        self.assertEqual(ctx.exception.url, url)

    def test_filename_collision_keeps_both_notes(self):
        # Same title+date but different URLs -> both notes must survive.
        first = self.vault.write_note(
            _summary(title="Same"), "https://example.com/a", DATE
        )
        second = self.vault.write_note(
            _summary(title="Same"), "https://example.com/b", DATE
        )
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue(second.name.endswith("-2.md"))

    def test_different_urls_not_duplicate(self):
        self.vault.write_note(_summary(), "https://example.com/a", DATE)
        self.assertFalse(self.vault.is_duplicate("https://example.com/b"))


if __name__ == "__main__":
    unittest.main()
