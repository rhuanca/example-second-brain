import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from second_brain.kb.config import KbSettings
from second_brain.kb.topics import Taxonomy, Topic
from tests.kb_fixtures import write_note

_ROOT = Path(__file__).resolve().parent.parent
for name in ["discover_topics", "refresh_topics"]:
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
refresh_topics = sys.modules["refresh_topics"]


def taxonomy(*topics):
    return Taxonomy(topics=[Topic(i, n, "", list(ids)) for i, n, ids in topics])


class RefreshTest(unittest.TestCase):
    """The job applies without asking, so what it refuses to do is the point."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "vault"
        self.root.mkdir()
        for i in range(8):
            write_note(self.root, f"note{i}", f"Note {i}", f"body about subject {i}")
        self.topics_file = base / "topics.json"
        self.history = base / "history"
        self.settings = KbSettings.from_env(
            {"VAULT_PATH": str(self.root), "ANTHROPIC_API_KEY": "sk-test"}
        )
        self.notified = []

        patch = mock.patch(
            "refresh_topics.notify", lambda text: self.notified.append(text) or True
        )
        patch.start()
        self.addCleanup(patch.stop)

    def stored(self):
        if not self.topics_file.exists():
            return None
        data = json.loads(self.topics_file.read_text())
        return taxonomy(*[(t["id"], t["name"], t["note_ids"]) for t in data["topics"]])

    def write_stored(self, tax):
        from second_brain.kb.topics import save_taxonomy

        save_taxonomy(tax, self.topics_file)

    def run_refresh(self, fresh, **kwargs):
        calls = []

        def fake_discover(cards, **kw):
            calls.append(kw)
            return fresh

        code = refresh_topics.refresh(
            self.settings,
            discover_fn=fake_discover,
            topics_path=self.topics_file,
            history=self.history,
            **kwargs,
        )
        return code, calls

    def test_a_first_run_files_every_note(self):
        fresh = taxonomy(("a", "Alpha", [f"note{i}" for i in range(4)]),
                         ("b", "Beta", [f"note{i}" for i in range(4, 8)]))

        code, calls = self.run_refresh(fresh)

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual([t.id for t in self.stored().topics], ["a", "b"])
        note = (self.root / "note0.md").read_text()
        self.assertIn("topics:", note)
        self.assertIn("- a", note)
        self.assertIn("Topics refreshed", self.notified[0])

    def test_a_quiet_week_costs_nothing(self):
        self.write_stored(taxonomy(("a", "Alpha", [f"note{i}" for i in range(8)])))

        code, calls = self.run_refresh(taxonomy(("a", "Alpha", ["note0"])))

        self.assertEqual(code, 0)
        self.assertEqual(calls, [])  # discovery was never called
        self.assertEqual(self.notified, [])

    def test_a_bad_taxonomy_is_refused_and_nothing_is_written(self):
        """health() complains about one-note topics; auto-apply is exactly where
        that must not land silently."""
        before = taxonomy(("a", "Alpha", [f"note{i}" for i in range(8)]))
        self.write_stored(before)
        for i in range(8, 16):  # enough new notes to trigger a run
            write_note(self.root, f"note{i}", f"Note {i}", "another subject")
        splintered = taxonomy(*[(f"t{i}", f"Topic {i}", [f"note{i}"]) for i in range(16)])

        code, calls = self.run_refresh(splintered)

        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual([t.id for t in self.stored().topics], ["a"])  # untouched
        self.assertIn("refused", self.notified[0])

    def test_the_previous_taxonomy_is_snapshotted_before_being_replaced(self):
        before = taxonomy(("a", "Alpha", ["note0"]))
        self.write_stored(before)
        fresh = taxonomy(("a", "Alpha", [f"note{i}" for i in range(6)]),
                         ("b", "Beta", ["note6", "note7"]))

        self.run_refresh(fresh)

        snapshots = sorted(self.history.glob("*.json"))
        self.assertEqual(len(snapshots), 1)
        kept = json.loads(snapshots[0].read_text())
        self.assertEqual(kept["topics"][0]["note_ids"], ["note0"])  # the old assignment

    def test_history_keeps_only_the_last_few(self):
        for i in range(13):
            refresh_topics.snapshot(
                taxonomy(("a", f"Alpha {i}", ["note0"])), self.history, keep=10
            )
        kept = sorted(self.history.glob("*.json"))
        self.assertLessEqual(len(kept), 10)

    def test_dry_run_changes_nothing(self):
        fresh = taxonomy(("a", "Alpha", [f"note{i}" for i in range(8)]))

        code, calls = self.run_refresh(fresh, dry_run=True)

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)  # it still asks, to show you the diff
        self.assertIsNone(self.stored())
        self.assertNotIn("topics:", (self.root / "note0.md").read_text())
        self.assertEqual(self.notified, [])

    def test_colours_survive_a_reordering_discovery(self):
        from second_brain.kb.visuals import topic_slots

        before = taxonomy(("a", "Alpha", ["note0"]), ("b", "Beta", ["note1"]))
        self.write_stored(before)
        reordered = taxonomy(("b", "Beta", ["note1", "note2", "note3"]),
                             ("a", "Alpha", ["note0", "note4", "note5"]),
                             ("c", "Gamma", ["note6", "note7"]))

        self.run_refresh(reordered)

        after = topic_slots(self.stored().topics)
        self.assertEqual(after["a"], topic_slots(before.topics)["a"])
        self.assertEqual(after["b"], topic_slots(before.topics)["b"])


if __name__ == "__main__":
    unittest.main()
