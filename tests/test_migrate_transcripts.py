import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import frontmatter

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "migrate_transcripts", _ROOT / "scripts" / "migrate_transcripts_to_sources.py"
)
migrate_transcripts = importlib.util.module_from_spec(_spec)
sys.modules["migrate_transcripts"] = migrate_transcripts
_spec.loader.exec_module(migrate_transcripts)

STEM = "2026-07-03-agent-memory"

OLD_TRANSCRIPT = """\
---
date: '2026-07-03'
note: '[[2026-07-03-agent-memory]]'
source: https://youtu.be/abc
tags:
- transcript
- youtube
title: 'Agent Memory — transcript'
---

full transcript body
"""

OLD_NOTE = """\
---
date: '2026-07-03'
source: https://youtu.be/abc
tags:
- agent-memory
- youtube
title: Agent Memory
transcript: '[[transcripts/2026-07-03-agent-memory.transcript]]'
---

## TL;DR

A summary.

## Transcript

[[transcripts/2026-07-03-agent-memory.transcript|Full transcript]]
"""


class MigrateTranscriptsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        (self.vault / "transcripts").mkdir()
        (self.vault / "transcripts" / f"{STEM}.transcript.md").write_text(OLD_TRANSCRIPT)
        (self.vault / f"{STEM}.md").write_text(OLD_NOTE)

    def tearDown(self):
        self._tmp.cleanup()

    def test_dry_run_changes_nothing(self):
        report = migrate_transcripts.migrate(self.vault, apply=False)
        self.assertEqual(len(report.items), 1)
        self.assertTrue((self.vault / "transcripts" / f"{STEM}.transcript.md").exists())
        self.assertFalse((self.vault / "sources").exists())

    def test_apply_migrates_archive_and_note(self):
        migrate_transcripts.migrate(self.vault, apply=True)

        # archive moved + reshaped
        apath = self.vault / "sources" / f"{STEM}.source.md"
        self.assertTrue(apath.exists())
        apost = frontmatter.load(str(apath))
        self.assertEqual(apost["title"], "Agent Memory — source")
        self.assertEqual(apost["kind"], "transcript")
        self.assertEqual(apost["tags"], ["source", "youtube"])
        self.assertEqual(apost.content, "full transcript body")

        # old transcript + folder gone
        self.assertFalse((self.vault / "transcripts").exists())

        # note relinked
        npost = frontmatter.load(str(self.vault / f"{STEM}.md"))
        self.assertNotIn("transcript", npost.metadata)
        self.assertEqual(npost["archive"], f"[[sources/{STEM}.source]]")
        self.assertIn("## Source", npost.content)
        self.assertIn(f"[[sources/{STEM}.source|Full source]]", npost.content)

    def test_idempotent(self):
        migrate_transcripts.migrate(self.vault, apply=True)
        report = migrate_transcripts.migrate(self.vault, apply=True)
        self.assertEqual(report.items, [])


if __name__ == "__main__":
    unittest.main()
