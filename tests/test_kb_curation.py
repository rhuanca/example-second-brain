import unittest

from second_brain.kb.curation import diff, needs_refresh, stable_order
from second_brain.kb.notes import Card
from second_brain.kb.topics import Taxonomy, Topic
from second_brain.kb.visuals import topic_slots


def card(note_id):
    return Card(note_id=note_id, title=note_id)


def taxonomy(*topics):
    return Taxonomy(topics=[Topic(i, name, "", list(notes)) for i, name, notes in topics])


class NeedsRefreshTest(unittest.TestCase):
    def test_a_vault_with_no_taxonomy_always_needs_one(self):
        should, why = needs_refresh([card("a")], None)
        self.assertTrue(should)
        self.assertIn("no taxonomy", why)

    def test_enough_unfiled_notes(self):
        tax = taxonomy(("t1", "Agents", ["a"]))
        cards = [card("a")] + [card(f"new{i}") for i in range(5)]
        should, why = needs_refresh(cards, tax)
        self.assertTrue(should)
        self.assertIn("5 notes unfiled", why)

    def test_too_few_to_be_worth_a_call(self):
        tax = taxonomy(("t1", "Agents", ["a"]))
        should, why = needs_refresh([card("a"), card("new")], tax)
        self.assertFalse(should)
        self.assertIn("under the threshold", why)

    def test_the_threshold_moves(self):
        tax = taxonomy(("t1", "Agents", ["a"]))
        cards = [card("a"), card("b"), card("c")]
        self.assertTrue(needs_refresh(cards, tax, minimum=2)[0])
        self.assertFalse(needs_refresh(cards, tax, minimum=3)[0])

    def test_an_empty_vault_is_never_worth_a_call(self):
        self.assertFalse(needs_refresh([], None)[0])


class StableOrderTest(unittest.TestCase):
    """The guarantee: a refresh may change the taxonomy, not the map's colours."""

    def test_surviving_topics_keep_their_colours(self):
        before = taxonomy(
            ("agents", "Agents", ["a"]), ("rag", "RAG", ["b"]), ("evals", "Evals", ["c"])
        )
        # Discovery returns them in a different order, renames one, adds another.
        fresh = taxonomy(
            ("evals", "Agent Evals & Verification", ["c"]),
            ("new", "Voice Agents", ["d"]),
            ("agents", "Agents", ["a"]),
            ("rag", "RAG & Retrieval", ["b"]),
        )

        ordered = stable_order(before, fresh)

        self.assertEqual([t.id for t in ordered.topics], ["agents", "rag", "evals", "new"])
        was, now = topic_slots(before.topics), topic_slots(ordered.topics)
        for topic_id in ["agents", "rag", "evals"]:
            self.assertEqual(was[topic_id], now[topic_id], topic_id)
        self.assertEqual(now["new"], 4)  # the next free colour

    def test_without_it_the_colours_would_move(self):
        before = taxonomy(("agents", "Agents", ["a"]), ("rag", "RAG", ["b"]))
        fresh = taxonomy(("rag", "RAG", ["b"]), ("agents", "Agents", ["a"]))
        self.assertNotEqual(topic_slots(before.topics), topic_slots(fresh.topics))
        self.assertEqual(topic_slots(before.topics), topic_slots(stable_order(before, fresh).topics))

    def test_renames_and_retirements_still_happen(self):
        before = taxonomy(("agents", "Agents", ["a"]), ("gone", "Retired", ["x"]))
        fresh = taxonomy(("agents", "Agentic Patterns", ["a", "b"]))

        ordered = stable_order(before, fresh)

        self.assertEqual([t.name for t in ordered.topics], ["Agentic Patterns"])
        self.assertEqual(ordered.topics[0].note_ids, ["a", "b"])

    def test_the_first_taxonomy_is_taken_as_it_comes(self):
        fresh = taxonomy(("b", "B", ["1"]), ("a", "A", ["2"]))
        self.assertEqual([t.id for t in stable_order(None, fresh).topics], ["b", "a"])
        self.assertEqual([t.id for t in stable_order(Taxonomy(), fresh).topics], ["b", "a"])

    def test_unassigned_notes_come_along(self):
        fresh = Taxonomy(topics=[Topic("a", "A", "", ["1"])], unassigned=["orphan"])
        self.assertEqual(stable_order(taxonomy(("a", "A", ["1"])), fresh).unassigned, ["orphan"])


class DiffTest(unittest.TestCase):
    def test_an_unchanged_taxonomy_reports_nothing(self):
        tax = taxonomy(("agents", "Agents", ["a", "b"]))
        changes = diff(tax, tax)
        self.assertFalse(changes)
        self.assertEqual(changes.summary(), "nothing changed")

    def test_what_a_real_refresh_looks_like(self):
        before = taxonomy(("agents", "Agents", ["a"]), ("old", "Old Topic", ["b"]))
        fresh = taxonomy(
            ("agents", "Agentic Patterns", ["a", "new1", "new2"]),
            ("voice", "Voice Agents", ["b"]),
        )

        changes = diff(before, fresh)

        self.assertEqual(changes.added, ["Voice Agents"])
        self.assertEqual(changes.retired, ["Old Topic"])
        self.assertEqual(changes.renamed, [("Agents", "Agentic Patterns")])
        self.assertEqual(changes.filed, 2)   # new1, new2
        self.assertEqual(changes.moved, 1)   # b: old -> voice
        self.assertTrue(changes)
        summary = changes.summary()
        self.assertIn("Voice Agents", summary)
        self.assertIn("2 notes filed", summary)

    def test_the_first_run_files_everything(self):
        changes = diff(None, taxonomy(("a", "A", ["1", "2", "3"])))
        self.assertEqual(changes.filed, 3)
        self.assertEqual(changes.moved, 0)
        self.assertEqual(changes.added, ["A"])


if __name__ == "__main__":
    unittest.main()
