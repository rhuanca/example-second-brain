import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "dedupe_vault", _ROOT / "scripts" / "dedupe_vault.py"
)
dedupe_vault = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass + `from __future__ import annotations` needs
# the module in sys.modules to resolve annotations.
sys.modules["dedupe_vault"] = dedupe_vault
_spec.loader.exec_module(dedupe_vault)


def _note(vault: Path, name: str, source: str, date: str) -> Path:
    path = vault / f"{name}.md"
    path.write_text(
        f"---\ntitle: {name}\nsource: {source}\ndate: '{date}'\n---\n\nbody\n",
        encoding="utf-8",
    )
    return path


def _archive(vault: Path, stem: str) -> Path:
    folder = vault / "sources"
    folder.mkdir(exist_ok=True)
    path = folder / f"{stem}.source.md"
    path.write_text("---\ntitle: x\n---\n\ntranscript\n", encoding="utf-8")
    return path


class DedupeVaultTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_groups_youtube_share_token_variants(self):
        _note(self.vault, "a", "https://youtu.be/ve7AA01vplE?si=AAA", "2026-08-22")
        _note(self.vault, "b", "https://youtu.be/ve7AA01vplE?si=BBB", "2026-08-24")

        groups = dedupe_vault.plan_dedupe(self.vault)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].key, "youtube:ve7AA01vplE")
        self.assertEqual(groups[0].keep.path.name, "a.md")
        self.assertEqual([n.path.name for n in groups[0].drop], ["b.md"])

    def test_keeps_the_oldest_note(self):
        _note(self.vault, "newer", "https://youtu.be/ve7AA01vplE?si=A", "2026-08-24")
        _note(self.vault, "older", "https://youtu.be/ve7AA01vplE?si=B", "2026-08-22")

        groups = dedupe_vault.plan_dedupe(self.vault)

        self.assertEqual(groups[0].keep.path.name, "older.md")

    def test_distinct_sources_are_not_grouped(self):
        _note(self.vault, "a", "https://youtu.be/ve7AA01vplE", "2026-08-22")
        _note(self.vault, "b", "https://youtu.be/ihmashJt3I4", "2026-08-24")
        _note(self.vault, "c", "https://example.com/post", "2026-08-25")

        self.assertEqual(dedupe_vault.plan_dedupe(self.vault), [])

    def test_notes_without_source_are_ignored(self):
        (self.vault / "Home.md").write_text("---\ntitle: Home\n---\n\nindex\n")

        self.assertEqual(dedupe_vault.read_notes(self.vault), [])

    def test_dry_run_deletes_nothing(self):
        a = _note(self.vault, "a", "https://youtu.be/ve7AA01vplE?si=A", "2026-08-22")
        b = _note(self.vault, "b", "https://youtu.be/ve7AA01vplE?si=B", "2026-08-24")

        dedupe_vault.dedupe(self.vault)

        self.assertTrue(a.exists())
        self.assertTrue(b.exists())

    def test_apply_removes_newer_note_and_its_archive(self):
        a = _note(self.vault, "a", "https://youtu.be/ve7AA01vplE?si=A", "2026-08-22")
        b = _note(self.vault, "b", "https://youtu.be/ve7AA01vplE?si=B", "2026-08-24")
        a_archive = _archive(self.vault, "a")
        b_archive = _archive(self.vault, "b")

        dedupe_vault.dedupe(self.vault, apply=True)

        self.assertTrue(a.exists())
        self.assertTrue(a_archive.exists())
        self.assertFalse(b.exists())
        self.assertFalse(b_archive.exists())

    def test_apply_is_idempotent(self):
        _note(self.vault, "a", "https://youtu.be/ve7AA01vplE?si=A", "2026-08-22")
        _note(self.vault, "b", "https://youtu.be/ve7AA01vplE?si=B", "2026-08-24")

        dedupe_vault.dedupe(self.vault, apply=True)

        self.assertEqual(dedupe_vault.dedupe(self.vault, apply=True), [])

    def test_apply_survives_a_missing_archive(self):
        _note(self.vault, "a", "https://youtu.be/ve7AA01vplE?si=A", "2026-08-22")
        b = _note(self.vault, "b", "https://youtu.be/ve7AA01vplE?si=B", "2026-08-24")

        dedupe_vault.dedupe(self.vault, apply=True)  # b has no .source.md companion

        self.assertFalse(b.exists())


if __name__ == "__main__":
    unittest.main()
