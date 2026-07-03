import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import frontmatter

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "flatten_vault", _ROOT / "scripts" / "flatten_vault.py"
)
flatten_vault = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass + `from __future__ import annotations` needs
# the module in sys.modules to resolve annotations.
sys.modules["flatten_vault"] = flatten_vault
_spec.loader.exec_module(flatten_vault)


def _write(path: Path, title: str, *, para=None, tags=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(
        "## TL;DR\n\nbody",
        title=title,
        source=f"https://ex.com/{title}",
        tags=tags or [],
    )
    if para:
        post["para"] = para
    path.write_text(frontmatter.dumps(post), encoding="utf-8")


class FlattenVaultTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_dry_run_reports_but_moves_nothing(self):
        _write(self.vault / "Resources" / "a.md", "a")
        report = flatten_vault.flatten(self.vault, apply=False)
        self.assertEqual(len(report.moves), 1)
        self.assertTrue((self.vault / "Resources" / "a.md").exists())
        self.assertFalse((self.vault / "a.md").exists())

    def test_apply_moves_to_root_and_removes_folders(self):
        _write(self.vault / "Resources" / "a.md", "a")
        _write(self.vault / "Projects" / "b.md", "b")
        flatten_vault.flatten(self.vault, apply=True)
        self.assertTrue((self.vault / "a.md").exists())
        self.assertTrue((self.vault / "b.md").exists())
        self.assertFalse((self.vault / "Resources").exists())
        self.assertFalse((self.vault / "Projects").exists())

    def test_name_collision_gets_suffix(self):
        _write(self.vault / "Resources" / "dup.md", "one")
        _write(self.vault / "Archives" / "dup.md", "two")
        flatten_vault.flatten(self.vault, apply=True)
        roots = sorted(p.name for p in self.vault.glob("*.md"))
        self.assertEqual(roots, ["dup-2.md", "dup.md"])

    def test_strip_para_removes_field_keeps_content(self):
        _write(self.vault / "Resources" / "c.md", "c", para="resources", tags=["x"])
        flatten_vault.flatten(self.vault, apply=True, strip_para=True)
        post = frontmatter.load(str(self.vault / "c.md"))
        self.assertNotIn("para", post.metadata)
        self.assertEqual(post["title"], "c")
        self.assertEqual(post["tags"], ["x"])
        self.assertIn("body", post.content)

    def test_idempotent_second_run_is_noop(self):
        _write(self.vault / "Resources" / "a.md", "a")
        flatten_vault.flatten(self.vault, apply=True)
        report = flatten_vault.flatten(self.vault, apply=True)
        self.assertEqual(report.moves, [])


if __name__ == "__main__":
    unittest.main()
