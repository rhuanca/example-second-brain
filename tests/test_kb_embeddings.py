import tempfile
import unittest
from pathlib import Path

import numpy as np

from second_brain.kb.embeddings import (
    EmbeddingIndex,
    card_text,
    content_hash,
    normalize,
    refresh,
)
from second_brain.kb.notes import Card
from tests.kb_fixtures import FakeEmbedder


def _card(note_id, title, tldr="", key_points=()):
    return Card(note_id=note_id, title=title, tldr=tldr, key_points=list(key_points))


class CardTextTest(unittest.TestCase):
    def test_embeds_title_tldr_and_key_points_only(self):
        card = Card(
            note_id="n",
            title="Agent memory",
            tldr="Short summary",
            key_points=["one", "two"],
            prototype_ideas=["not embedded"],
        )
        self.assertEqual(card_text(card), "Agent memory\nShort summary\none\ntwo")


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.embedder = FakeEmbedder()
        self.cards = [
            _card("a", "Agent memory", "vector stores for agents"),
            _card("b", "Kubernetes operators", "controllers and CRDs"),
        ]

    def test_first_build_embeds_everything(self):
        index, stats = refresh(None, self.cards, self.embedder)

        self.assertEqual((stats.added, stats.changed, stats.removed), (2, 0, 0))
        self.assertEqual(index.note_ids, ["a", "b"])
        self.assertEqual(index.vectors.shape, (2, self.embedder.dim))
        np.testing.assert_allclose(np.linalg.norm(index.vectors, axis=1), 1.0, rtol=1e-5)

    def test_unchanged_notes_are_not_re_embedded(self):
        index, _ = refresh(None, self.cards, self.embedder)
        self.embedder.passage_calls.clear()

        again, stats = refresh(index, self.cards, self.embedder)

        self.assertEqual(self.embedder.passage_calls, [])
        self.assertEqual(stats.unchanged, 2)
        self.assertFalse(stats.dirty)
        np.testing.assert_array_equal(again.vectors, index.vectors)

    def test_only_new_and_changed_notes_are_embedded(self):
        index, _ = refresh(None, self.cards, self.embedder)
        self.embedder.passage_calls.clear()
        cards = [
            _card("a", "Agent memory", "now about episodic memory"),
            self.cards[1],
            _card("c", "Rust async", "tokio runtime"),
        ]

        updated, stats = refresh(index, cards, self.embedder)

        self.assertEqual((stats.added, stats.changed, stats.unchanged), (1, 1, 1))
        self.assertEqual(
            self.embedder.embedded_texts,
            [card_text(cards[0]), card_text(cards[2])],
        )
        self.assertEqual(updated.note_ids, ["a", "b", "c"])
        np.testing.assert_array_equal(updated.vectors[1], index.vectors[1])

    def test_removed_notes_are_dropped(self):
        index, _ = refresh(None, self.cards, self.embedder)

        updated, stats = refresh(index, self.cards[:1], self.embedder)

        self.assertEqual(stats.removed, 1)
        self.assertEqual(updated.note_ids, ["a"])
        self.assertEqual(updated.vectors.shape[0], 1)

    def test_a_different_model_rebuilds_from_scratch(self):
        index, _ = refresh(None, self.cards, self.embedder)
        other = FakeEmbedder(model_name="other-model")

        updated, stats = refresh(index, self.cards, other)

        self.assertEqual(stats.added, 2)
        self.assertEqual(len(other.embedded_texts), 2)
        self.assertEqual(updated.model, "other-model")

    def test_empty_vault(self):
        index, stats = refresh(None, [], self.embedder)
        self.assertEqual(index.note_ids, [])
        self.assertFalse(stats.dirty)


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "index"

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip(self):
        cards = [_card("a", "one"), _card("b", "two")]
        index, _ = refresh(None, cards, FakeEmbedder())

        index.save(self.dir)
        loaded = EmbeddingIndex.load(self.dir)

        self.assertEqual(loaded.model, index.model)
        self.assertEqual(loaded.note_ids, index.note_ids)
        self.assertEqual(loaded.hashes, [content_hash(card_text(c)) for c in cards])
        np.testing.assert_array_equal(loaded.vectors, index.vectors)
        # No temp files left behind by the atomic write.
        self.assertEqual(
            sorted(p.name for p in self.dir.iterdir()), ["manifest.json", "vectors.npy"]
        )

    def test_missing_index_loads_as_none(self):
        self.assertIsNone(EmbeddingIndex.load(self.dir))

    def test_corrupt_manifest_loads_as_none(self):
        index, _ = refresh(None, [_card("a", "one")], FakeEmbedder())
        index.save(self.dir)
        (self.dir / "manifest.json").write_text("{not json", encoding="utf-8")

        self.assertIsNone(EmbeddingIndex.load(self.dir))


class NormalizeTest(unittest.TestCase):
    def test_zero_rows_stay_zero(self):
        out = normalize(np.array([[0.0, 0.0], [3.0, 4.0]]))
        np.testing.assert_allclose(out, [[0.0, 0.0], [0.6, 0.8]])


if __name__ == "__main__":
    unittest.main()
