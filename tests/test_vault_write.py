import datetime
import tempfile
import unittest
from pathlib import Path

from second_brain.models import Para, Summary
from second_brain.vault import (
    PARA_FOLDERS,
    DuplicateNoteError,
    Vault,
)

DATE = datetime.date(2026, 6, 30)


def _summary(**overrides):
    data = dict(
        title="Building Agentic Systems",
        tldr="A guide.",
        key_points=["a"],
        tags=["agentic-dev"],
        prototype_ideas=["idea"],
        para=Para.RESOURCES,
    )
    data.update(overrides)
    return Summary(**data)


class VaultWriteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_ensure_folders_creates_para_dirs(self):
        self.vault.ensure_folders()
        for folder in PARA_FOLDERS.values():
            self.assertTrue((self.vault.root / folder).is_dir())

    def test_write_note_lands_in_correct_para_folder(self):
        path = self.vault.write_note(
            _summary(para=Para.RESOURCES), "https://example.com/post", DATE
        )
        self.assertTrue(path.exists())
        self.assertEqual(path.parent.name, "Resources")
        self.assertEqual(path.name, "2026-06-30-building-agentic-systems.md")

    def test_write_creates_missing_folder(self):
        # No ensure_folders() call first: write_note must create it.
        path = self.vault.write_note(
            _summary(para=Para.PROJECTS), "https://example.com/x", DATE
        )
        self.assertEqual(path.parent.name, "Projects")

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
