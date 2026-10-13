import unittest
from types import SimpleNamespace

from second_brain.fetcher import FetchError
from second_brain.ocr import DEFAULT_OCR_MODEL, MAX_OCR_PAGES, transcribe


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Client:
    """Records each call and replays canned page text."""

    def __init__(self, texts=None, fails_on=()):
        self.texts = texts or []
        self.fails_on = set(fails_on)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) in self.fails_on:
            raise RuntimeError("overloaded")
        index = len(self.calls) - 1
        text = self.texts[index] if index < len(self.texts) else f"page {index + 1} text"
        return SimpleNamespace(content=[_Block(text)])


class TranscribeTest(unittest.TestCase):
    def test_one_request_per_page_joined_in_order(self):
        client = _Client(["# Title\nfirst", "second page"])

        text = transcribe([b"a", b"b"], client=client)

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(
            text,
            "--- page 1 ---\n\n# Title\nfirst\n\n--- page 2 ---\n\nsecond page",
        )

    def test_the_page_image_is_sent_as_an_image_block(self):
        client = _Client(["text"])
        transcribe([b"\xff\xd8pretend-jpeg"], client=client)

        content = client.calls[0]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["media_type"], "image/jpeg")
        self.assertIn("Transcribe this page", content[1]["text"])

    def test_the_cheap_model_is_the_default(self):
        client = _Client(["x"])
        transcribe([b"a"], client=client)
        self.assertEqual(client.calls[0]["model"], DEFAULT_OCR_MODEL)

    def test_an_explicit_model_wins(self):
        client = _Client(["x"])
        transcribe([b"a"], model="claude-sonnet-5", client=client)
        self.assertEqual(client.calls[0]["model"], "claude-sonnet-5")

    def test_a_failed_page_is_marked_and_the_rest_survive(self):
        """A transcript with a hole that says so is honest; one that silently
        skips a page is not."""
        client = _Client(["first", "unused", "third"], fails_on={2})

        text = transcribe([b"a", b"b", b"c"], client=client)

        self.assertIn("--- page 2 (unreadable) ---", text)
        self.assertIn("first", text)
        self.assertIn("third", text)
        self.assertEqual(len(client.calls), 3)  # it kept going

    def test_a_long_document_is_refused_before_it_costs_anything(self):
        client = _Client()
        with self.assertRaises(FetchError) as caught:
            transcribe([b"page"] * (MAX_OCR_PAGES + 1), client=client)

        self.assertIn(str(MAX_OCR_PAGES + 1), str(caught.exception))
        self.assertIn(str(MAX_OCR_PAGES), str(caught.exception))
        self.assertEqual(client.calls, [])  # nothing was sent

    def test_the_cap_itself_is_allowed(self):
        client = _Client()
        transcribe([b"page"] * MAX_OCR_PAGES, client=client)
        self.assertEqual(len(client.calls), MAX_OCR_PAGES)

    def test_no_pages_at_all(self):
        with self.assertRaises(FetchError):
            transcribe([], client=_Client())

    def test_a_missing_key_is_reported_not_crashed(self):
        with self.assertRaises(FetchError) as caught:
            transcribe([b"a"], api_key=None)
        self.assertIn("ANTHROPIC_API_KEY", str(caught.exception))

    def test_blank_pages_leave_only_their_rule(self):
        text = transcribe([b"a", b"b"], client=_Client(["", "content"]))
        self.assertIn("--- page 1 ---", text)
        self.assertIn("content", text)


if __name__ == "__main__":
    unittest.main()
