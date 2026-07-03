import datetime
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from second_brain.ask import AskError, Note, answer, ask, load_notes, search
from second_brain.config import Settings
from second_brain.models import Summary
from second_brain.vault import Vault

DATE = datetime.date(2026, 7, 3)


def _settings(vault_path):
    return Settings.from_env(
        {
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_ALLOWED_USER_ID": "42",
            "VAULT_PATH": str(vault_path),
            "ANTHROPIC_API_KEY": "sk-test",
        }
    )


class FakeMessages:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._text)]
        )


class FakeClient:
    def __init__(self, text):
        self.messages = FakeMessages(text)


def _note(title, tags, body, source="https://ex.com/x"):
    return Note(path=Path(f"/{title}.md"), title=title, source=source, tags=tags, body=body)


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.notes = [
            _note("Agent memory patterns", ["agentic-dev", "memory"], "How agents store state across turns."),
            _note("CSS grid guide", ["frontend", "css"], "Laying out pages with grid."),
            _note("Context editing", ["agentic-dev"], "Clearing stale tool results from context."),
        ]

    def test_matches_by_title_and_tags(self):
        hits = search(self.notes, "what about agent memory?")
        self.assertEqual(hits[0].title, "Agent memory patterns")

    def test_unrelated_query_returns_nothing(self):
        self.assertEqual(search(self.notes, "quantum gardening recipes"), [])

    def test_respects_limit(self):
        hits = search(self.notes, "agentic dev context memory", limit=2)
        self.assertLessEqual(len(hits), 2)

    def test_empty_question_returns_nothing(self):
        self.assertEqual(search(self.notes, "the a of to"), [])  # all stopwords


class LoadNotesTest(unittest.TestCase):
    def test_reads_written_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Vault(Path(tmp))
            vault.write_note(
                Summary(title="Agent memory", tldr="state across turns", tags=["memory", "article"]),
                "https://ex.com/mem",
                DATE,
            )
            notes = load_notes(vault)
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].title, "Agent memory")
            self.assertEqual(notes[0].source, "https://ex.com/mem")
            self.assertIn("memory", notes[0].tags)
            self.assertIn("state across turns", notes[0].body)


class AnswerTest(unittest.TestCase):
    def test_answer_uses_client_and_includes_note_titles(self):
        client = FakeClient("Based on 'Agent memory patterns', agents persist state.")
        notes = [_note("Agent memory patterns", ["memory"], "persist state")]
        out = answer("how do agents remember?", notes, model="m", client=client)
        self.assertIn("Agent memory patterns", out)
        prompt = client.messages.calls[0]["messages"][0]["content"]
        self.assertIn("Agent memory patterns", prompt)
        self.assertIn("how do agents remember?", prompt)

    def test_missing_key_without_client_raises(self):
        with self.assertRaises(AskError):
            answer("q", [_note("t", [], "b")], model="m", api_key=None)


class AskOrchestratorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name))
        self.settings = _settings(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_matches_returns_friendly_message(self):
        self.vault.write_note(
            Summary(title="CSS grid", tldr="layout", tags=["css"]),
            "https://ex.com/css",
            DATE,
        )
        reply = ask("what about agent memory?", vault=self.vault, settings=self.settings)
        self.assertIn("couldn't find", reply.lower())

    def test_match_calls_answerer(self):
        self.vault.write_note(
            Summary(title="Agent memory", tldr="state", tags=["memory"]),
            "https://ex.com/mem",
            DATE,
        )
        captured = {}

        def fake_answerer(question, notes, **kw):
            captured["question"] = question
            captured["titles"] = [n.title for n in notes]
            return "answer text"

        reply = ask(
            "agent memory?",
            vault=self.vault,
            settings=self.settings,
            answerer=fake_answerer,
        )
        self.assertEqual(reply, "answer text")
        self.assertIn("Agent memory", captured["titles"])


if __name__ == "__main__":
    unittest.main()
