import tempfile
import unittest
from pathlib import Path

from second_brain import topics_admin
from second_brain.kb.topics import Taxonomy, Topic, load_taxonomy, save_taxonomy


def taxonomy(*topics):
    return Taxonomy(topics=[Topic(i, n, "", list(ids)) for i, n, ids in topics])


class TopicsAdminTest(unittest.TestCase):
    """The refresh applies without asking, so this is the control it offers."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.history = base / "history"
        self.history.mkdir()
        self.topics_file = base / "topics.json"
        self.written = []

    def store(self, tax, name="20261001T120000"):
        save_taxonomy(tax, self.history / f"{name}.json")

    def test_describing_the_current_taxonomy(self):
        text = topics_admin.describe(
            taxonomy(("a", "Agents", ["n1", "n2"]), ("b", "RAG", ["n3"])),
            history=self.history,
        )
        self.assertIn("2 topics", text)
        self.assertIn("Agents — 2", text)
        self.assertIn("No earlier version stored", text)

    def test_the_ninth_topic_is_marked_differently(self):
        """The first eight carry a colour on the map; the rest share grey."""
        big = taxonomy(*[(f"t{i}", f"Topic {i}", ["n"]) for i in range(10)])
        lines = topics_admin.describe(big, history=self.history).splitlines()
        self.assertTrue(lines[1].startswith("•"))   # first
        self.assertTrue(lines[8].startswith("•"))   # eighth
        self.assertTrue(lines[9].startswith("·"))   # ninth

    def test_an_empty_taxonomy_says_how_to_make_one(self):
        self.assertEqual(topics_admin.describe(None, history=self.history), topics_admin.NO_TAXONOMY)
        self.assertEqual(topics_admin.describe(Taxonomy(), history=self.history), topics_admin.NO_TAXONOMY)

    def test_describe_points_at_the_snapshot_it_would_restore(self):
        self.store(taxonomy(("a", "Agents", ["n1"])))
        text = topics_admin.describe(taxonomy(("a", "Agents", ["n1", "n2"])), history=self.history)
        self.assertIn("20261001T120000", text)
        self.assertIn("/topics undo", text)

    def test_undo_restores_the_previous_taxonomy_and_rewrites_notes(self):
        before = taxonomy(("a", "Agents", ["n1"]), ("b", "RAG", ["n2"]))
        self.store(before)
        save_taxonomy(taxonomy(("a", "Agents Renamed", ["n1", "n2"])), self.topics_file)

        message = topics_admin.undo(
            topics_path=self.topics_file,
            history=self.history,
            write_notes=lambda assignments: self.written.append(assignments) or ["n1.md", "n2.md"],
        )

        restored = load_taxonomy(self.topics_file)
        self.assertEqual([t.name for t in restored.topics], ["Agents", "RAG"])
        self.assertEqual(self.written, [{"n1": ["a"], "n2": ["b"]}])
        self.assertIn("Restored 2 topics", message)
        self.assertIn("2 notes rewritten", message)

    def test_a_snapshot_is_consumed_so_a_second_undo_goes_further_back(self):
        self.store(taxonomy(("a", "Oldest", ["n1"])), name="20260101T000000")
        self.store(taxonomy(("a", "Middle", ["n1"])), name="20260202T000000")

        topics_admin.undo(topics_path=self.topics_file, history=self.history)
        self.assertEqual(load_taxonomy(self.topics_file).topics[0].name, "Middle")

        topics_admin.undo(topics_path=self.topics_file, history=self.history)
        self.assertEqual(load_taxonomy(self.topics_file).topics[0].name, "Oldest")

        self.assertEqual(
            topics_admin.undo(topics_path=self.topics_file, history=self.history),
            topics_admin.NOTHING_TO_UNDO,
        )

    def test_nothing_to_undo(self):
        self.assertEqual(
            topics_admin.undo(topics_path=self.topics_file, history=self.history),
            topics_admin.NOTHING_TO_UNDO,
        )

    def test_an_unreadable_snapshot_is_discarded_not_applied(self):
        (self.history / "20260101T000000.json").write_text("{not json", encoding="utf-8")

        message = topics_admin.undo(topics_path=self.topics_file, history=self.history)

        self.assertIn("could not be read", message)
        self.assertEqual(topics_admin.snapshots(self.history), [])
        self.assertFalse(self.topics_file.exists())


if __name__ == "__main__":
    unittest.main()
