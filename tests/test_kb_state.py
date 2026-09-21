import tempfile
import unittest
from pathlib import Path

from second_brain.kb.state import NoteState, ReadStats


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class NoteStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "nested" / "state.db"
        self.clock = Clock()
        self.state = NoteState(self.path, clock=self.clock)

    def test_creates_its_parent_directory(self):
        self.assertTrue(self.path.is_file())

    def test_flags_round_trip_independently(self):
        self.state.set_starred("a", True)
        self.state.set_archived("b", True)
        self.state.set_starred("b", True)
        self.state.set_starred("b", False)

        self.assertEqual(self.state.starred(), {"a"})
        self.assertEqual(self.state.archived(), {"b"})

    def test_flags_persist_across_instances(self):
        self.state.set_starred("a", True)
        self.state.set_archived("a", True)
        reopened = NoteState(self.path)
        self.assertEqual(reopened.starred(), {"a"})
        self.assertEqual(reopened.archived(), {"a"})

    def test_reads_are_counted_per_channel(self):
        self.state.record_read("a", "web")
        self.clock.now = 2000.0
        self.state.record_read("a", "mcp")
        self.state.record_read("a", "mcp")
        self.clock.now = 1500.0
        self.state.record_read("b", "web")

        stats = self.state.read_stats()
        self.assertEqual(stats["a"], ReadStats(web=1, mcp=2, last_at=2000.0))
        self.assertEqual(stats["a"].total, 3)
        self.assertEqual(stats["b"], ReadStats(web=1, mcp=0, last_at=1500.0))
        self.assertNotIn("c", stats)

    def test_unknown_channel_is_refused(self):
        with self.assertRaises(ValueError):
            self.state.record_read("a", "telegram")


if __name__ == "__main__":
    unittest.main()
