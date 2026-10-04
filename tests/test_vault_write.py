import datetime
import tempfile
import unittest
from pathlib import Path

from second_brain.models import Summary
from second_brain.vault import SOURCES_DIR, DuplicateNoteError, Vault

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


class SourceArchiveWriteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_archive_companion_written_and_linked(self):
        import frontmatter

        note_path = self.vault.write_note(
            _summary(title="Agent Memory"),
            "https://youtu.be/abc",
            DATE,
            archive="the full source body",
            kind="transcript",
            source_type="youtube",
        )
        stem = note_path.stem
        apath = self.vault.root / "sources" / f"{stem}.source.md"
        self.assertTrue(apath.exists())

        # archive file holds the raw markdown and links back to the note
        apost = frontmatter.load(str(apath))
        self.assertEqual(apost.content, "the full source body")
        self.assertEqual(apost["kind"], "transcript")
        self.assertEqual(apost["note"], f"[[{stem}]]")

        # note links down to the archive
        npost = frontmatter.load(str(note_path))
        self.assertEqual(npost["archive"], f"[[sources/{stem}.source]]")

    def test_no_archive_means_no_sources_folder(self):
        self.vault.write_note(_summary(), "https://example.com/post", DATE)
        self.assertFalse((self.vault.root / "sources").exists())

    def test_blank_archive_is_skipped(self):
        self.vault.write_note(
            _summary(), "https://example.com/post", DATE, archive="   "
        )
        self.assertFalse((self.vault.root / "sources").exists())


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

    def test_duplicate_detected_across_youtube_share_tokens(self):
        self.vault.write_note(
            _summary(), "https://youtu.be/ve7AA01vplE?si=-2YhO5vh9RcEiSzu", DATE
        )
        self.assertTrue(
            self.vault.is_duplicate("https://youtu.be/ve7AA01vplE?si=GxFa4HqDaut8XPNy")
        )

    def test_duplicate_detected_across_youtube_url_shapes(self):
        self.vault.write_note(_summary(), "https://youtu.be/ve7AA01vplE", DATE)
        self.assertTrue(
            self.vault.is_duplicate("https://www.youtube.com/watch?v=ve7AA01vplE&t=90s")
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


class TrashTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = Vault(self.root)

    def write(self, title="Building Agentic Systems", archive="the captured text"):
        return self.vault.write_note(
            _summary(title=title), f"https://example.com/{title}", DATE, archive=archive
        )

    def test_trashing_takes_the_archive_with_it_and_hides_both(self):
        note = self.write()
        archive = self.root / SOURCES_DIR / f"{note.stem}.source.md"
        self.assertTrue(archive.is_file())

        name = self.vault.trash(note)

        self.assertEqual(name, note.name)
        self.assertFalse(note.exists())
        self.assertFalse(archive.exists())
        self.assertTrue((self.vault.trash_dir / name).is_file())
        self.assertTrue((self.vault.trash_dir / f"{note.stem}.source.md").is_file())
        # Invisible to everything that reads the vault (and to Obsidian).
        self.assertEqual(list(self.vault.iter_notes()), [])

    def test_a_note_without_an_archive_still_trashes(self):
        note = self.write(archive=None)
        self.assertEqual(self.vault.trash(note), note.name)
        self.assertEqual([n.has_archive for n in self.vault.trashed()], [False])

    def test_trashed_lists_what_is_there(self):
        note = self.write()
        self.vault.trash(note)

        (trashed,) = self.vault.trashed()

        self.assertEqual(trashed.name, note.name)
        self.assertEqual(trashed.title, "Building Agentic Systems")
        self.assertEqual(trashed.source, "https://example.com/Building Agentic Systems")
        self.assertTrue(trashed.has_archive)

    def test_restore_puts_the_pair_back(self):
        note = self.write()
        self.vault.trash(note)

        restored = self.vault.restore(note.name)

        self.assertEqual(restored, note)
        self.assertTrue(restored.is_file())
        self.assertTrue((self.root / SOURCES_DIR / f"{note.stem}.source.md").is_file())
        self.assertEqual(self.vault.trashed(), [])

    def test_restore_never_overwrites_a_replacement(self):
        """Re-capturing the same article after deleting it must not be clobbered."""
        note = self.write()
        self.vault.trash(note)
        again = self.write()  # same title+date, so the same filename
        self.assertEqual(again, note)

        restored = self.vault.restore(note.name)

        self.assertNotEqual(restored, again)
        self.assertTrue(again.is_file())
        self.assertIn("the captured text", (self.root / SOURCES_DIR / f"{restored.stem}.source.md").read_text())

    def test_restore_refuses_anything_not_in_the_listing(self):
        self.write()
        for name in ["nope.md", "../secret.md", None, 5, ""]:
            with self.subTest(name=name):
                self.assertIsNone(self.vault.restore(name))

    def test_empty_trash_counts_the_notes_it_removed(self):
        for title in ["One", "Two"]:
            self.vault.trash(self.write(title=title))

        self.assertEqual(self.vault.empty_trash(), 2)
        self.assertEqual(self.vault.trashed(), [])
        self.assertEqual(list(self.vault.trash_dir.glob("*")), [])
        self.assertEqual(self.vault.empty_trash(), 0)  # idempotent

    def test_trash_refuses_a_path_from_outside_the_vault(self):
        outside = Path(self._tmp.name).parent / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        self.addCleanup(outside.unlink)
        self.assertIsNone(self.vault.trash(outside))
        self.assertTrue(outside.is_file())

    def test_an_empty_vault_has_an_empty_trash(self):
        self.assertEqual(self.vault.trashed(), [])
        self.assertEqual(self.vault.empty_trash(), 0)
