import json
import tempfile
import unittest
from pathlib import Path

import anthropic
import httpx2 as httpx
from fastapi.testclient import TestClient

from second_brain.kb.app import create_app
from second_brain.kb.chat import (
    FALLBACK_BETA,
    MAX_CHARS,
    SYSTEM,
    ChatError,
    ChatTurn,
    build_messages,
    gather_notes,
    parse_ask,
    turns_for,
    stream_answer,
)
from second_brain.kb.chats import STOPPED, ChatStore
from second_brain.kb.config import KbSettings
from second_brain.kb.retrieval import Library
from second_brain.kb.tools import UNTRUSTED_NOTICE
from second_brain.vault import Vault
from tests.kb_fixtures import FakeEmbedder, write_note

SAME_SITE = {"Sec-Fetch-Site": "same-origin"}


class _Final:
    def __init__(self, stop_reason="end_turn"):
        self.stop_reason = stop_reason


class _Stream:
    def __init__(self, chunks, stop_reason):
        self.text_stream = iter(chunks)
        self._final = _Final(stop_reason)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._final


class FakeClient:
    """Stands in for anthropic.Anthropic: records requests, replays chunks."""

    def __init__(self, chunks=("Hello",), stop_reason="end_turn", raises=None):
        self.chunks, self.stop_reason, self.raises = chunks, stop_reason, raises
        self.requests = []
        self.beta = self
        self.messages = self

    def stream(self, **request):
        self.requests.append(request)
        if self.raises:
            raise self.raises
        return _Stream(self.chunks, self.stop_reason)


def _status_error(cls, status):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return cls("boom", response=response, body=None)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "vault"
        self.root.mkdir()
        write_note(self.root, "memory", "Agent memory", "vector memory for llm agents",
                   key_points=("episodic memory",))
        write_note(self.root, "rag", "RAG evals", "measuring retrieval quality with code examples")
        write_note(self.root, "k8s", "Kubernetes operators", "controllers and reconcile loops")
        self.index = base / "index"
        self.library = Library(Vault(self.root), self.index, FakeEmbedder(), taxonomy_loader=lambda: None)
        self.library.refresh_if_stale()
        self.settings = KbSettings.from_env(
            {"VAULT_PATH": str(self.root), "ANTHROPIC_API_KEY": "sk-test", "KB_INDEX_DIR": str(self.index)}
        )

    def tearDown(self):
        self._tmp.cleanup()

    def run_chat(self, turns, client, settings=None):
        cards = gather_notes(self.library, turns)
        return list(
            stream_answer(
                turns, cards, library=self.library, settings=settings or self.settings,
                describe=lambda c: {"id": c.note_id, "title": c.title}, client=client,
            )
        )


class ParseAskTest(unittest.TestCase):
    def test_a_question_with_and_without_a_chat(self):
        self.assertEqual(parse_ask({"message": "  hello  "}), (None, "hello"))
        self.assertEqual(parse_ask({"message": "hi", "conversation_id": "a" * 32}), ("a" * 32, "hi"))

    def test_rejects_bad_shapes(self):
        for payload in [
            None,
            [],
            {},
            {"message": ""},
            {"message": "   "},
            {"message": 5},
            {"message": "x" * (MAX_CHARS + 1)},
            {"message": "q", "conversation_id": 7},
        ]:
            with self.subTest(payload=str(payload)[:60]), self.assertRaises(ChatError):
                parse_ask(payload)


class TurnsForTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ChatStore(Path(self._tmp.name) / "state.db")

    def test_a_new_chat_is_just_the_question(self):
        turns = turns_for(self.store, None, "first question")
        self.assertEqual([(t.role, t.content) for t in turns], [("user", "first question")])

    def test_stored_turns_come_back_with_the_notes_they_used(self):
        chat = self.store.create("t")
        self.store.append(chat, "user", "kubernetes controllers")
        self.store.append(chat, "assistant", "They reconcile.", source_ids=["k8s", "rag"], cited=["k8s"])

        turns = turns_for(self.store, chat, "which one had code examples?")

        self.assertEqual([t.role for t in turns], ["user", "assistant", "user"])
        self.assertEqual(turns[1].source_ids, ["k8s"])  # cited wins over consulted
        self.assertEqual(turns[-1].content, "which one had code examples?")

    def test_failed_answers_are_never_sent_back_to_the_model(self):
        chat = self.store.create("t")
        self.store.append(chat, "user", "q")
        self.store.append(chat, "assistant", "", status="error")
        self.store.append(chat, "assistant", "half an answer", status=STOPPED)

        turns = turns_for(self.store, chat, "next")

        self.assertEqual([t.content for t in turns], ["q", "half an answer", "next"])

    def test_only_the_last_turns_are_carried(self):
        chat = self.store.create("t")
        for i in range(41):
            self.store.append(chat, "user" if i % 2 == 0 else "assistant", f"m{i}")
        turns = turns_for(self.store, chat, "now")
        self.assertEqual(len(turns), 20)
        self.assertEqual(turns[-1].content, "now")

    def test_an_unknown_chat_starts_a_fresh_conversation(self):
        turns = turns_for(self.store, "0" * 32, "q")
        self.assertEqual([t.content for t in turns], ["q"])


class GatherNotesTest(_Base):
    def test_latest_question_first_then_follow_up_context_then_prior_sources(self):
        turns = [
            ChatTurn("user", "kubernetes controllers"),
            ChatTurn("assistant", "They reconcile.", ["k8s", "../../etc/passwd", "nope"]),
            ChatTurn("user", "which one had code examples?"),
        ]
        cards = gather_notes(self.library, turns)
        ids = [c.note_id for c in cards]
        self.assertEqual(ids[0], "rag")  # "code examples" matches the RAG note
        self.assertIn("k8s", ids)  # carried over from the earlier answer
        self.assertNotIn("../../etc/passwd", ids)
        self.assertEqual(len(ids), len(set(ids)))


class BuildMessagesTest(_Base):
    def test_notes_fenced_and_only_on_the_latest_question(self):
        turns = [
            ChatTurn("assistant", "stray greeting"),  # can't open a conversation
            ChatTurn("user", "q1"),
            ChatTurn("assistant", "a1"),
            ChatTurn("user", "agent memory?"),
        ]
        cards = [self.library.card("memory")]
        messages = build_messages(turns, cards, self.library)

        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        self.assertEqual(messages[0]["content"], "q1")
        latest = messages[-1]["content"]
        self.assertIn(UNTRUSTED_NOTICE, latest)
        self.assertIn('<untrusted_source note_id="memory" kind="note">', latest)
        self.assertTrue(latest.endswith("Question: agent memory?"))

    def test_no_matching_notes_is_said_explicitly(self):
        messages = build_messages([ChatTurn("user", "q")], [], self.library)
        self.assertIn("No notes matched", messages[-1]["content"])


class StreamAnswerTest(_Base):
    def test_event_sequence_and_request(self):
        client = FakeClient(chunks=["Agents use ", "[[memory]] and ", "[[invented-note]]."])
        events = self.run_chat([ChatTurn("user", "agent memory")], client)

        self.assertEqual(events[0]["type"], "sources")
        self.assertEqual(events[0]["notes"][0]["id"], "memory")
        self.assertEqual([e["text"] for e in events if e["type"] == "delta"],
                         ["Agents use ", "[[memory]] and ", "[[invented-note]]."])
        self.assertEqual(events[-1], {"type": "done", "cited": ["memory"], "truncated": False})

        (request,) = client.requests
        self.assertEqual(request["model"], "claude-opus-5")
        self.assertEqual(request["system"], SYSTEM)
        self.assertEqual(request["output_config"], {"effort": "medium"})
        self.assertEqual(request["cache_control"], {"type": "ephemeral"})
        self.assertEqual(request["betas"], [FALLBACK_BETA])
        self.assertEqual(request["extra_body"], {"fallbacks": "default"})

    def test_models_without_fallback_support_do_not_get_it(self):
        settings = KbSettings.from_env(
            {"VAULT_PATH": str(self.root), "ANTHROPIC_API_KEY": "k", "KB_CHAT_MODEL": "claude-sonnet-5"}
        )
        client = FakeClient()
        self.run_chat([ChatTurn("user", "q")], client, settings)
        self.assertNotIn("extra_body", client.requests[0])
        self.assertNotIn("betas", client.requests[0])

    def test_refusal_becomes_an_error(self):
        events = self.run_chat([ChatTurn("user", "q")], FakeClient(chunks=[], stop_reason="refusal"))
        self.assertEqual(events[-1]["type"], "error")

    def test_truncation_is_flagged(self):
        events = self.run_chat([ChatTurn("user", "q")], FakeClient(stop_reason="max_tokens"))
        self.assertTrue(events[-1]["truncated"])

    def test_api_failures_become_friendly_errors(self):
        for exc, expected in [
            (_status_error(anthropic.AuthenticationError, 401), "key was rejected"),
            (_status_error(anthropic.RateLimitError, 429), "Rate limited"),
            (_status_error(anthropic.InternalServerError, 500), "(500)"),
        ]:
            with self.subTest(expected=expected):
                events = self.run_chat([ChatTurn("user", "q")], FakeClient(raises=exc))
                self.assertEqual(events[-1]["type"], "error")
                self.assertIn(expected, events[-1]["message"])

    def test_missing_key(self):
        settings = KbSettings.from_env({"VAULT_PATH": str(self.root)})
        events = self.run_chat([ChatTurn("user", "q")], None, settings)
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("ANTHROPIC_API_KEY", events[-1]["message"])


class ChatApiTest(_Base):
    def app(self, client, **env):
        settings = KbSettings.from_env(
            {
                "VAULT_PATH": str(self.root),
                "KB_INDEX_DIR": str(self.index),
                "KB_AUTH_TOKENS": "t",
                "KB_WEB_ALLOW_UNAUTHENTICATED": "true",
                "ANTHROPIC_API_KEY": "sk-test",
                **env,
            }
        )
        self.chats = ChatStore(Path(self._tmp.name) / "state.db")
        return TestClient(
            create_app(settings, library=self.library, chat_client=client, chats=self.chats)
        )

    def post(self, http, body, **headers):
        return http.post(
            "/api/chat",
            content=json.dumps(body),
            # What a browser sends from our own page, unless a test overrides it.
            headers={"Content-Type": "application/json",
                     **({} if "Origin" in headers else SAME_SITE), **headers},
        )

    def test_streams_ndjson_events(self):
        client = FakeClient(chunks=["See [[memory]]."])
        with self.app(client) as http:
            response = self.post(http, {"message": "agent memory"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/x-ndjson"))
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual([e["type"] for e in events], ["conversation", "sources", "delta", "done"])
        source = events[1]["notes"][0]
        self.assertEqual(set(source), {"id", "title", "date", "thumbnail", "slot", "source_type"})
        self.assertEqual(events[-1]["cited"], ["memory"])

    def test_rejects_non_json_foreign_origin_bad_body_and_oversize(self):
        with self.app(FakeClient()) as http:
            self.assertEqual(
                http.post("/api/chat", content="messages=hi",
                          headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code,
                415,
            )
            self.assertEqual(
                self.post(http, {"message": "q"},
                          Origin="https://evil.example").status_code,
                403,
            )
            for foreign in [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}, {}]:
                response = http.post("/api/chat", content="{}",
                                     headers={"Content-Type": "application/json", **foreign})
                self.assertEqual(response.status_code, 403, foreign)
            self.assertEqual(
                self.post(http, {"message": "q"},
                          Origin="http://testserver").status_code,
                200,
            )
            self.assertEqual(self.post(http, {"message": "  "}).status_code, 400)
            bad = http.post("/api/chat", content="{not json",
                            headers={"Content-Type": "application/json", **SAME_SITE})
            self.assertEqual(bad.status_code, 400)
            big = http.post("/api/chat", content="x" * 200_001,
                            headers={"Content-Type": "application/json", **SAME_SITE})
            self.assertEqual(big.status_code, 413)

    def test_the_chat_is_saved_and_can_be_continued(self):
        client = FakeClient(chunks=["See [[memory]]."])
        with self.app(client) as http:
            events = [json.loads(l) for l in self.post(http, {"message": "agent memory"}).text.splitlines() if l]
            opening = events[0]
            self.assertEqual(opening["type"], "conversation")  # first, so the page can keep the id
            self.assertEqual(opening["title"], "agent memory")

            saved = self.chats.get(opening["id"])
            self.assertEqual([(m.role, m.content) for m in saved.messages],
                             [("user", "agent memory"), ("assistant", "See [[memory]].")])
            self.assertEqual(saved.messages[1].cited, ["memory"])
            self.assertIn("memory", saved.messages[1].source_ids)

            self.post(http, {"message": "and the second?", "conversation_id": opening["id"]})

        self.assertEqual(len(self.chats.get(opening["id"]).messages), 4)
        # The follow-up carried the first exchange to the model.
        sent = client.requests[-1]["messages"]
        self.assertEqual(sent[0]["content"], "agent memory")
        self.assertEqual(len(self.chats.recent()), 1)

    def test_a_failed_answer_is_stored_as_failed(self):
        client = FakeClient(chunks=[], stop_reason="refusal")
        with self.app(client) as http:
            events = [json.loads(l) for l in self.post(http, {"message": "q"}).text.splitlines() if l]
        chat = self.chats.get(events[0]["id"])
        self.assertEqual(chat.messages[1].status, "error")

    def test_without_a_key_the_page_explains_and_the_api_refuses(self):
        with self.app(FakeClient(), ANTHROPIC_API_KEY="") as http:
            page = http.get("/chat")
            self.assertIn("Chat is off", page.text)
            self.assertNotIn("/static/chat.js", page.text)
            self.assertEqual(self.post(http, {"message": "q"}).status_code, 503)

    def test_page_loads_its_script_and_csp_allows_only_our_own(self):
        with self.app(FakeClient()) as http:
            page = http.get("/chat")
            script = http.get("/static/chat.js")
        self.assertIn('<script src="/static/chat.js" defer></script>', page.text)
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        self.assertIn("connect-src 'self'", page.headers["content-security-policy"])
        self.assertEqual(script.status_code, 200)
        # Model output is untrusted: nothing may be parsed as HTML.
        self.assertNotRegex(script.text, r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write")

    def test_requires_access_when_configured(self):
        from second_brain.kb.auth import AccessVerifier

        settings = KbSettings.from_env(
            {
                "VAULT_PATH": str(self.root),
                "KB_INDEX_DIR": str(self.index),
                "KB_AUTH_TOKENS": "t",
                "ANTHROPIC_API_KEY": "sk-test",
            }
        )
        verifier = AccessVerifier("team.cloudflareaccess.com", "aud", key_resolver=lambda t: None)
        client = FakeClient()
        app = create_app(
            settings,
            library=self.library,
            access_verifier=verifier,
            chat_client=client,
            chats=ChatStore(Path(self._tmp.name) / "state.db"),
        )
        with TestClient(app) as http:
            response = self.post(http, {"message": "q"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
