import os
import tempfile
import unittest
from pathlib import Path

from second_brain.kb.retrieval import Library
from second_brain.kb.topics import Taxonomy, Topic
from second_brain.vault import Vault
from tests.kb_fixtures import FakeEmbedder, write_archive, write_note


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "vault"
        self.root.mkdir()
        self.index_dir = base / "index"
        self.outside = base / "outside"
        self.outside.mkdir()
        self.embedder = FakeEmbedder()
        self.clock = _Clock()
        self.taxonomy = None

        write_note(self.root, "memory", "Agent memory", "vector memory for llm agents")
        write_note(self.root, "rag", "Retrieval augmented generation", "rag retrieval pipelines")
        write_note(self.root, "k8s", "Kubernetes operators", "kubernetes controllers crds")
        # A hand-written page with no source: never a card.
        (self.root / "Home.md").write_text("# Home\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def library(self, **kwargs):
        lib = Library(
            Vault(self.root),
            self.index_dir,
            self.embedder,
            taxonomy_loader=lambda: self.taxonomy,
            clock=self.clock,
            **kwargs,
        )
        lib.refresh_if_stale()
        return lib


class RefreshTest(_Base):
    def test_loads_cards_and_writes_the_index(self):
        lib = self.library()

        self.assertEqual(sorted(c.note_id for c in lib.cards()), ["k8s", "memory", "rag"])
        self.assertTrue((self.index_dir / "manifest.json").exists())

    def test_nothing_happens_within_the_ttl(self):
        lib = self.library()
        write_note(self.root, "new", "New note", "fresh")

        self.assertIsNone(lib.refresh_if_stale())
        self.assertIsNone(lib.card("new"))

    def test_a_new_note_is_picked_up_after_the_ttl(self):
        lib = self.library()
        self.embedder.passage_calls.clear()
        write_note(self.root, "new", "New note", "fresh")
        self.clock.now += 60

        stats = lib.refresh_if_stale()

        self.assertEqual(stats.added, 1)
        self.assertIsNotNone(lib.card("new"))
        self.assertEqual(len(self.embedder.embedded_texts), 1)

    def test_an_unchanged_listing_skips_parsing_and_embedding(self):
        lib = self.library()
        self.embedder.passage_calls.clear()
        self.clock.now += 60

        self.assertIsNone(lib.refresh_if_stale())
        self.assertEqual(self.embedder.passage_calls, [])

    def test_a_restart_reuses_the_saved_index(self):
        self.library()
        fresh_embedder = FakeEmbedder()
        self.embedder = fresh_embedder

        self.library()

        self.assertEqual(fresh_embedder.passage_calls, [])

    def test_deleted_note_disappears(self):
        lib = self.library()
        (self.root / "k8s.md").unlink()
        self.clock.now += 60

        stats = lib.refresh_if_stale()

        self.assertEqual(stats.removed, 1)
        self.assertIsNone(lib.card("k8s"))


class LookupTest(_Base):
    def test_known_id(self):
        lib = self.library()
        self.assertEqual(lib.card("memory").title, "Agent memory")

    def test_traversal_and_unknown_ids_are_rejected(self):
        (self.outside / "secret.md").write_text("secret", encoding="utf-8")
        lib = self.library()
        for bad in [
            "../outside/secret",
            "../../etc/passwd",
            "/etc/passwd",
            "%2e%2e%2fsecret",
            "..%2F..%2Fetc%2Fpasswd",
            "memory.md",
            "sources/memory.source",
            "",
            "nope",
            None,
            123,
        ]:
            with self.subTest(bad=bad):
                self.assertIsNone(lib.card(bad))
                self.assertIsNone(lib.archive_text(bad))

    def test_symlinked_note_pointing_outside_the_vault_is_rejected(self):
        target = self.outside / "leak.md"
        write_note(self.outside, "leak", "Leaked", "outside the vault")
        os.symlink(target, self.root / "leak.md")
        lib = self.library()

        self.assertIsNone(lib.card("leak"))

    def test_archive_text(self):
        write_archive(self.root, "memory", "full archive text")
        lib = self.library()

        self.assertEqual(lib.archive_text("memory"), "full archive text")
        self.assertIsNone(lib.archive_text("rag"))  # no archive on disk

    def test_symlinked_archive_outside_the_vault_is_rejected(self):
        (self.root / "sources").mkdir()
        (self.outside / "env").write_text("SECRET=1", encoding="utf-8")
        os.symlink(self.outside / "env", self.root / "sources" / "memory.source.md")
        lib = self.library()

        self.assertIsNone(lib.archive_text("memory"))


class SearchTest(_Base):
    def test_ranks_by_similarity_without_topics(self):
        lib = self.library()

        hits = lib.search("kubernetes controllers")

        self.assertEqual(hits[0].card.note_id, "k8s")
        self.assertEqual(len(hits), 3)

    def test_limit_and_blank_query(self):
        lib = self.library()
        self.assertEqual(len(lib.search("agents", limit=1)), 1)
        self.assertEqual(lib.search("   "), [])
        self.assertEqual(lib.search("agents", limit=0), [])

    def test_explicit_topic_only_returns_that_topic(self):
        self.taxonomy = Taxonomy(
            topics=[
                Topic("ai", "AI", "llm agents and retrieval", ["memory", "rag"]),
                Topic("infra", "Infra", "kubernetes", ["k8s"]),
            ]
        )
        lib = self.library()

        hits = lib.search("kubernetes controllers", topic="ai")

        self.assertEqual({h.card.note_id for h in hits}, {"memory", "rag"})

    def test_unknown_topic_returns_nothing(self):
        lib = self.library()
        self.assertEqual(lib.search("agents", topic="no-such-topic"), [])

    def test_matching_topic_ranks_first_then_fills_from_the_rest(self):
        self.taxonomy = Taxonomy(
            topics=[Topic("infra", "Kubernetes", "kubernetes controllers crds", ["k8s"])]
        )
        lib = self.library(topic_threshold=0.5, fallback_threshold=0.1)

        hits = lib.search("kubernetes controllers crds", limit=3)

        self.assertEqual(hits[0].card.note_id, "k8s")
        self.assertEqual(len(hits), 3)

    def test_frontmatter_topics_are_used_without_a_taxonomy(self):
        write_note(
            self.root, "tagged", "Tagged", "something", topics=("infra",)
        )
        lib = self.library()

        self.assertEqual([h.card.note_id for h in lib.search("x", topic="infra")], ["tagged"])

    def test_empty_vault(self):
        for path in self.root.glob("*.md"):
            path.unlink()
        lib = self.library()
        self.assertEqual(lib.search("anything"), [])

    def test_topic_vectors_are_memoised(self):
        self.taxonomy = Taxonomy(topics=[Topic("infra", "Infra", "kubernetes", ["k8s"])])
        lib = self.library()
        self.embedder.passage_calls.clear()

        lib.search("one")
        lib.search("two")

        self.assertEqual(len(self.embedder.passage_calls), 1)


if __name__ == "__main__":
    unittest.main()
