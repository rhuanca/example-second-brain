import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import frontmatter

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "discover_topics", _ROOT / "scripts" / "discover_topics.py"
)
discover_topics = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass + `from __future__ import annotations` needs
# the module in sys.modules to resolve annotations.
sys.modules["discover_topics"] = discover_topics
_spec.loader.exec_module(discover_topics)

NOTE = """\
---
title: A Note
source: https://youtu.be/abc
date: '2026-08-22'
tags:
- ontology
- rag
---

## TL;DR

Something.
"""


class WriteTopicsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "n0.md"
        self.path.write_text(NOTE, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_adds_topics_to_frontmatter(self):
        changed = discover_topics.write_topics(self.root, {"n0": ["agents", "rag"]})

        post = frontmatter.load(str(self.path))
        self.assertEqual(changed, [self.path])
        self.assertEqual(list(post["topics"]), ["agents", "rag"])

    def test_leaves_free_form_tags_untouched(self):
        discover_topics.write_topics(self.root, {"n0": ["agents"]})

        post = frontmatter.load(str(self.path))
        self.assertEqual(list(post["tags"]), ["ontology", "rag"])

    def test_preserves_body_and_other_frontmatter(self):
        discover_topics.write_topics(self.root, {"n0": ["agents"]})

        post = frontmatter.load(str(self.path))
        self.assertEqual(post["title"], "A Note")
        self.assertEqual(post["source"], "https://youtu.be/abc")
        self.assertIn("Something.", post.content)

    def test_rewriting_identical_topics_is_a_no_op(self):
        discover_topics.write_topics(self.root, {"n0": ["agents"]})
        before = self.path.read_text(encoding="utf-8")

        changed = discover_topics.write_topics(self.root, {"n0": ["agents"]})

        self.assertEqual(changed, [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_replaces_stale_topics_on_reassignment(self):
        discover_topics.write_topics(self.root, {"n0": ["old"]})
        discover_topics.write_topics(self.root, {"n0": ["new"]})

        post = frontmatter.load(str(self.path))
        self.assertEqual(list(post["topics"]), ["new"])

    def test_unknown_note_id_is_skipped_not_created(self):
        changed = discover_topics.write_topics(self.root, {"does-not-exist": ["a"]})

        self.assertEqual(changed, [])
        self.assertFalse((self.root / "does-not-exist.md").exists())


if __name__ == "__main__":
    unittest.main()
