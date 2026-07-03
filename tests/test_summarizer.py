import json
import unittest
from types import SimpleNamespace

from second_brain.summarizer import SummarizerError, summarize


class FakeBlock(SimpleNamespace):
    pass


class FakeMessages:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[FakeBlock(type="text", text=self._text)])


class FakeClient:
    def __init__(self, text):
        self.messages = FakeMessages(text)


def _valid_json(**overrides):
    data = {
        "title": "Agentic Patterns",
        "tldr": "How to build agent loops.",
        "key_points": ["Use tools", "Keep context tight"],
        "tags": ["Agentic Dev", "LLM Agents"],
        "prototype_ideas": ["A planner loop"],
    }
    data.update(overrides)
    return json.dumps(data)


class SummarizeTest(unittest.TestCase):
    def test_valid_json_populates_summary(self):
        client = FakeClient(_valid_json())
        s = summarize("Original Title", "body text", model="m", client=client)
        self.assertEqual(s.title, "Agentic Patterns")
        self.assertEqual(s.tldr, "How to build agent loops.")
        self.assertEqual(s.key_points, ["Use tools", "Keep context tight"])
        self.assertEqual(s.prototype_ideas, ["A planner loop"])

    def test_tags_normalized_to_lowercase_kebab(self):
        client = FakeClient(_valid_json(tags=["Agentic Dev", "LLM  Agents", "RAG"]))
        s = summarize("t", "body", model="m", client=client)
        self.assertEqual(s.tags, ["agentic-dev", "llm-agents", "rag"])

    def test_json_wrapped_in_code_fence_is_parsed(self):
        fenced = "```json\n" + _valid_json() + "\n```"
        client = FakeClient(fenced)
        s = summarize("t", "body", model="m", client=client)
        self.assertEqual(s.title, "Agentic Patterns")

    def test_non_list_fields_default_to_empty(self):
        client = FakeClient(_valid_json(key_points="Use tools", tags="llm"))
        s = summarize("t", "body", model="m", client=client)
        self.assertEqual(s.key_points, [])
        self.assertEqual(s.tags, [])

    def test_missing_tldr_gets_placeholder(self):
        client = FakeClient(_valid_json(tldr=""))
        s = summarize("t", "body", model="m", client=client)
        self.assertTrue(s.tldr)

    def test_bare_code_fence_is_parsed(self):
        client = FakeClient("```\n" + _valid_json() + "\n```")
        s = summarize("t", "body", model="m", client=client)
        self.assertEqual(s.title, "Agentic Patterns")

    def test_extract_text_skips_non_text_blocks(self):
        blocks = [
            FakeBlock(type="tool_use"),  # no .text — must be ignored
            FakeBlock(type="text", text=_valid_json()),
        ]
        client = FakeClient("")
        client.messages.create = lambda **kw: SimpleNamespace(content=blocks)
        s = summarize("t", "body", model="m", client=client)
        self.assertEqual(s.title, "Agentic Patterns")

    def test_malformed_json_falls_back_to_raw_text(self):
        client = FakeClient("Sorry, here is a plain text summary of the article.")
        s = summarize("My Title", "body", model="m", client=client)
        self.assertEqual(s.title, "My Title")
        self.assertIn("plain text summary", s.tldr)
        self.assertEqual(s.key_points, [])

    def test_missing_api_key_without_client_raises(self):
        with self.assertRaises(SummarizerError):
            summarize("t", "body", model="m", api_key=None)

    def test_model_and_prompt_passed_to_client(self):
        client = FakeClient(_valid_json())
        summarize("t", "the article body", model="claude-x", client=client)
        call = client.messages.calls[0]
        self.assertEqual(call["model"], "claude-x")
        self.assertIn("the article body", call["messages"][0]["content"])
        self.assertIn("technical analyst", call["system"])


if __name__ == "__main__":
    unittest.main()
