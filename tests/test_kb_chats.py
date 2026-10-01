import tempfile
import unittest
from pathlib import Path

from second_brain.kb.chats import (
    DONE,
    STOPPED,
    ChatStore,
    title_from,
)


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class TitleTest(unittest.TestCase):
    def test_short_questions_are_the_title(self):
        self.assertEqual(title_from("What is a semantic layer?"), "What is a semantic layer?")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(title_from("  two\n\nlines  "), "two lines")

    def test_long_questions_are_cut_on_a_word_boundary(self):
        question = (
            "What have I saved about building agent memory systems with vector "
            "databases and retrieval pipelines?"
        )
        title = title_from(question)
        self.assertTrue(title.endswith("…"))
        self.assertLessEqual(len(title), 62)
        self.assertTrue(question.startswith(title[:-1]))  # no mid-word cut

    def test_empty_question(self):
        self.assertEqual(title_from("   "), "New chat")


class ChatStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.path = Path(self.tmp.name) / "nested" / "state.db"
        self.store = ChatStore(self.path, clock=self.clock)

    def test_messages_round_trip_in_order(self):
        chat = self.store.create("First question")
        self.store.append(chat, "user", "first question")
        self.store.append(
            chat, "assistant", "an answer", source_ids=["a", "b"], cited=["a"]
        )

        saved = self.store.get(chat)
        self.assertEqual(saved.title, "First question")
        self.assertEqual([m.role for m in saved.messages], ["user", "assistant"])
        self.assertEqual(saved.messages[1].source_ids, ["a", "b"])
        self.assertEqual(saved.messages[1].cited, ["a"])
        self.assertEqual(saved.messages[1].status, DONE)

    def test_a_stopped_answer_is_kept(self):
        chat = self.store.create("t")
        self.store.append(chat, "assistant", "half an ans", status=STOPPED)
        self.assertEqual(self.store.get(chat).messages[0].status, STOPPED)

    def test_recent_is_ordered_by_last_activity(self):
        first = self.store.create("first")
        self.clock.now += 10
        second = self.store.create("second")
        self.assertEqual([c.id for c in self.store.recent()], [second, first])

        self.clock.now += 10
        self.store.append(first, "user", "back to the first chat")
        self.assertEqual([c.id for c in self.store.recent()], [first, second])
        self.assertEqual(self.store.get(first).updated_at, self.clock.now)

    def test_recent_respects_its_limit(self):
        for i in range(5):
            self.clock.now += 1
            self.store.create(f"chat {i}")
        self.assertEqual(len(self.store.recent(limit=3)), 3)

    def test_rename_and_delete(self):
        chat = self.store.create("First question")
        self.store.append(chat, "user", "q")

        self.assertTrue(self.store.rename(chat, "  Semantic layers  "))
        self.assertEqual(self.store.get(chat).title, "Semantic layers")
        self.assertFalse(self.store.rename("0" * 32, "nope"))

        self.assertTrue(self.store.delete(chat))
        self.assertIsNone(self.store.get(chat))
        self.assertFalse(self.store.delete(chat))

    def test_deleting_takes_the_messages_with_it(self):
        chat = self.store.create("t")
        self.store.append(chat, "user", "q")
        self.store.delete(chat)
        # A new conversation must not inherit the old one's messages.
        again = self.store.create("t")
        self.assertEqual(self.store.get(again).messages, [])

    def test_unknown_and_malformed_ids_are_refused(self):
        for bad in ["", "nope", "../etc/passwd", "0" * 31, None, 7, "A" * 32]:
            with self.subTest(bad=bad):
                self.assertIsNone(self.store.get(bad))
                self.assertFalse(self.store.append(bad, "user", "q"))
                self.assertFalse(self.store.rename(bad, "x"))
                self.assertFalse(self.store.delete(bad))

    def test_appending_to_an_unknown_conversation_does_nothing(self):
        self.assertFalse(self.store.append("0" * 32, "user", "q"))

    def test_chats_persist_across_instances(self):
        chat = self.store.create("First question")
        self.store.append(chat, "user", "q")
        reopened = ChatStore(self.path)
        self.assertEqual(reopened.get(chat).messages[0].content, "q")

    def test_creates_its_parent_directory(self):
        self.assertTrue(self.path.is_file())


if __name__ == "__main__":
    unittest.main()
