import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "snapshot_state_db", _ROOT / "scripts" / "snapshot_state_db.py"
)
snapshot_state_db = importlib.util.module_from_spec(_spec)
sys.modules["snapshot_state_db"] = snapshot_state_db
_spec.loader.exec_module(snapshot_state_db)

snapshot = snapshot_state_db.snapshot


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.src = self.dir / "state.db"
        self.db = sqlite3.connect(self.src)
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE notes (id TEXT, starred INT)")
        self.db.executemany("INSERT INTO notes VALUES (?, ?)", [(f"n{i}", 1) for i in range(50)])
        self.db.commit()

    def rows(self, path):
        with sqlite3.connect(path) as db:
            return db.execute("SELECT count(*) FROM notes").fetchone()[0]

    def test_the_copy_holds_every_row(self):
        written = snapshot(self.src, self.dir / "backup" / "state.db")

        self.assertTrue(written.is_file())
        self.assertEqual(self.rows(written), 50)

    def test_writes_still_in_the_wal_are_included(self):
        """The reason this isn't `cp`: in WAL mode the newest rows are in a
        sidecar file, and a plain copy of state.db alone would miss them."""
        self.db.executemany("INSERT INTO notes VALUES (?, ?)", [(f"w{i}", 0) for i in range(10)])
        self.db.commit()
        self.assertTrue((self.dir / "state.db-wal").exists())  # not yet checkpointed

        written = snapshot(self.src, self.dir / "state-copy.db")

        self.assertEqual(self.rows(written), 60)

    def test_an_open_writer_does_not_stop_it(self):
        other = sqlite3.connect(self.src)
        self.addCleanup(other.close)
        other.execute("INSERT INTO notes VALUES ('uncommitted', 1)")  # held open

        written = snapshot(self.src, self.dir / "state-copy.db")

        self.assertEqual(self.rows(written), 50)  # the open transaction is not in it

    def test_the_source_is_left_alone(self):
        before = self.src.read_bytes()
        snapshot(self.src, self.dir / "state-copy.db")
        self.assertEqual(self.src.read_bytes(), before)

    def test_replacing_an_older_copy(self):
        target = self.dir / "state-copy.db"
        target.write_bytes(b"stale junk")

        snapshot(self.src, target)

        self.assertEqual(self.rows(target), 50)
        self.assertFalse(target.with_name(target.name + ".partial").exists())

    def test_a_missing_database_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            snapshot(self.dir / "nope.db", self.dir / "out.db")
        self.assertFalse((self.dir / "out.db").exists())  # no empty file left behind


if __name__ == "__main__":
    unittest.main()
