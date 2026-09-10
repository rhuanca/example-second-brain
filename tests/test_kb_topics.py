import json
import tempfile
import unittest
from pathlib import Path

from second_brain.kb.notes import Card
from second_brain.kb.topics import (
    Taxonomy,
    Topic,
    TopicError,
    discover,
    health,
    load_taxonomy,
    save_taxonomy,
    suggest_target,
)


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _Client:
    """Records the call it was given and replays a canned reply."""

    def __init__(self, payload, stop_reason="end_turn"):
        self.payload = payload
        self.stop_reason = stop_reason
        self.calls = []
        self.messages = self

    @property
    def prompts(self):
        return [c["messages"][0]["content"] for c in self.calls]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return _Response(text, self.stop_reason)


def _cards(n=4):
    return [Card(note_id=f"n{i}", title=f"Title {i}", tldr=f"Summary {i}.") for i in range(n)]


def _payload(topics, unassigned=None):
    return {"topics": topics, "unassigned": unassigned or []}


class DiscoverTest(unittest.TestCase):
    def test_parses_topics_and_assignments(self):
        client = _Client(
            _payload([
                {"id": "agents", "name": "Agents", "description": "d", "note_ids": ["n0", "n1"]},
                {"id": "rag", "name": "RAG", "description": "d", "note_ids": ["n2"]},
            ])
        )

        tax = discover(_cards(), model="m", client=client)

        self.assertEqual([t.id for t in tax.topics], ["agents", "rag"])
        self.assertEqual(tax.topics_for("n1"), ["agents"])
        self.assertEqual(tax.unassigned, ["n3"])

    def test_every_note_is_assigned_or_explicitly_unassigned(self):
        client = _Client(_payload([{"id": "a", "name": "A", "note_ids": ["n0"]}]))

        tax = discover(_cards(4), model="m", client=client)

        accounted = set(tax.assignments()) | set(tax.unassigned)
        self.assertEqual(accounted, {"n0", "n1", "n2", "n3"})

    def test_invented_note_ids_are_dropped(self):
        client = _Client(
            _payload([{"id": "a", "name": "A", "note_ids": ["n0", "does-not-exist"]}])
        )

        tax = discover(_cards(2), model="m", client=client)

        self.assertEqual(tax.topics[0].note_ids, ["n0"])

    def test_a_note_may_belong_to_several_topics(self):
        client = _Client(
            _payload([
                {"id": "a", "name": "A", "note_ids": ["n0"]},
                {"id": "b", "name": "B", "note_ids": ["n0"]},
            ])
        )

        tax = discover(_cards(1), model="m", client=client)

        self.assertEqual(tax.topics_for("n0"), ["a", "b"])

    def test_empty_and_duplicate_topics_are_dropped(self):
        client = _Client(
            _payload([
                {"id": "a", "name": "A", "note_ids": ["n0"]},
                {"id": "a", "name": "Dup", "note_ids": ["n1"]},
                {"id": "empty", "name": "Empty", "note_ids": []},
            ])
        )

        tax = discover(_cards(2), model="m", client=client)

        self.assertEqual([t.id for t in tax.topics], ["a"])

    def test_ids_are_slugified(self):
        client = _Client(_payload([{"id": "Agentic AI!", "name": "X", "note_ids": ["n0"]}]))
        tax = discover(_cards(1), model="m", client=client)
        self.assertEqual(tax.topics[0].id, "agentic-ai")

    def test_menu_contains_every_card(self):
        client = _Client(_payload([{"id": "a", "name": "A", "note_ids": ["n0"]}]))
        discover(_cards(3), model="m", client=client)
        prompt = client.prompts[0]
        for note_id in ("n0", "n1", "n2"):
            self.assertIn(f"[{note_id}]", prompt)

    def test_refinement_passes_existing_ids_and_demands_stability(self):
        existing = Taxonomy(topics=[Topic(id="agents", name="Agents", description="d")])
        client = _Client(_payload([{"id": "agents", "name": "Renamed", "note_ids": ["n0"]}]))

        tax = discover(_cards(1), model="m", client=client, existing=existing)

        self.assertIn("agents", client.prompts[0])
        self.assertIn("EXACTLY", client.prompts[0])
        # Display name may change; the id must not.
        self.assertEqual(tax.topics[0].id, "agents")
        self.assertEqual(tax.topics[0].name, "Renamed")

    def test_stable_across_identical_reruns(self):
        payload = _payload([{"id": "a", "name": "A", "note_ids": ["n0", "n1"]}])
        first = discover(_cards(2), model="m", client=_Client(payload))
        second = discover(_cards(2), model="m", client=_Client(payload))
        self.assertEqual(first.assignments(), second.assignments())
        self.assertEqual(first.unassigned, second.unassigned)

    def test_json_fence_is_tolerated(self):
        raw = '```json\n{"topics": [{"id": "a", "name": "A", "note_ids": ["n0"]}]}\n```'
        tax = discover(_cards(1), model="m", client=_Client(raw))
        self.assertEqual(tax.topics[0].id, "a")

    def test_unparseable_reply_raises_with_the_evidence(self):
        """A bare 'did not return usable JSON' is undiagnosable -- keep the reply."""
        client = _Client("Sure! Here are your topics:")

        with self.assertRaises(TopicError) as ctx:
            discover(_cards(1), model="m", client=client)

        self.assertIn("Sure! Here are your topics:", str(ctx.exception))
        self.assertIn("stop_reason", str(ctx.exception))

    def test_truncated_reply_says_so_plainly(self):
        client = _Client('{"topics": [{"id": "a"', stop_reason="max_tokens")

        with self.assertRaises(TopicError) as ctx:
            discover(_cards(1), model="m", client=client)

        self.assertIn("max_tokens", str(ctx.exception))

    def test_requests_json_schema_so_prose_cannot_come_back(self):
        client = _Client(_payload([{"id": "a", "name": "A", "note_ids": ["n0"]}]))

        discover(_cards(1), model="m", client=client)

        fmt = client.calls[0]["output_config"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertEqual(
            sorted(fmt["schema"]["properties"]), ["topics", "unassigned"]
        )

    def test_output_budget_grows_with_the_library(self):
        """Every note id is echoed back, so a constant budget truncates."""
        small = _Client(_payload([{"id": "a", "name": "A", "note_ids": ["n0"]}]))
        discover(_cards(2), model="m", client=small)

        big = _Client(_payload([{"id": "a", "name": "A", "note_ids": ["n0"]}]))
        discover(_cards(400), model="m", client=big)

        self.assertGreater(big.calls[0]["max_tokens"], small.calls[0]["max_tokens"])

    def test_explicit_max_tokens_is_respected(self):
        client = _Client(_payload([{"id": "a", "name": "A", "note_ids": ["n0"]}]))
        discover(_cards(1), model="m", client=client, max_tokens=1234)
        self.assertEqual(client.calls[0]["max_tokens"], 1234)

    def test_no_cards_needs_no_client(self):
        self.assertEqual(discover([], model="m").topics, [])

    def test_missing_api_key_raises_rather_than_calling_out(self):
        with self.assertRaises(TopicError):
            discover(_cards(1), model="m", api_key=None)


class TargetTest(unittest.TestCase):
    def test_grows_with_the_library_but_stays_scannable(self):
        self.assertEqual(suggest_target(75), 15)
        self.assertGreaterEqual(suggest_target(5), 6)
        self.assertLessEqual(suggest_target(5000), 24)


class HealthTest(unittest.TestCase):
    def test_clean_taxonomy_has_no_complaints(self):
        """A balanced taxonomy, on a library big enough for every check to run."""
        tax = Taxonomy(
            topics=[
                Topic(id="a", name="A", note_ids=[f"n{i}" for i in range(4)]),
                Topic(id="b", name="B", note_ids=[f"n{i}" for i in range(4, 8)]),
                Topic(id="c", name="C", note_ids=[f"n{i}" for i in range(8, 12)]),
            ]
        )
        self.assertEqual(health(tax, 12), [])

    def test_flags_a_singleton_topic(self):
        tax = Taxonomy(topics=[Topic(id="a", name="A", note_ids=["n0"])])
        self.assertTrue(any("1 note" in p for p in health(tax, 10)))

    def test_share_check_is_skipped_for_a_tiny_library(self):
        """Two topics over four notes is 50% each and entirely healthy."""
        tax = Taxonomy(
            topics=[
                Topic(id="a", name="A", note_ids=["n0", "n1"]),
                Topic(id="b", name="B", note_ids=["n2", "n3"]),
            ]
        )
        self.assertEqual(health(tax, 4), [])

    def test_flags_a_junk_drawer(self):
        tax = Taxonomy(topics=[Topic(id="a", name="A", note_ids=[f"n{i}" for i in range(9)])])
        self.assertTrue(any("%" in p for p in health(tax, 10)))

    def test_flags_too_many_unassigned(self):
        tax = Taxonomy(
            topics=[Topic(id="a", name="A", note_ids=["n0", "n1"])],
            unassigned=[f"u{i}" for i in range(5)],
        )
        self.assertTrue(any("unassigned" in p for p in health(tax, 10)))


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "topics.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trips(self):
        tax = Taxonomy(
            topics=[Topic(id="a", name="A", description="d", note_ids=["n0"])],
            unassigned=["n1"],
        )
        save_taxonomy(tax, self.path)
        loaded = load_taxonomy(self.path)
        self.assertEqual(loaded.topics[0].id, "a")
        self.assertEqual(loaded.topics[0].note_ids, ["n0"])
        self.assertEqual(loaded.unassigned, ["n1"])

    def test_missing_file_returns_none(self):
        self.assertIsNone(load_taxonomy(self.path))

    def test_corrupt_file_returns_none_rather_than_raising(self):
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(load_taxonomy(self.path))


if __name__ == "__main__":
    unittest.main()
