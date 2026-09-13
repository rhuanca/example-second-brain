import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from second_brain.kb.app import create_app
from second_brain.kb.config import KbSettings
from tests.kb_fixtures import FakeEmbedder, write_archive, write_note

TOKEN = "test-token-123"
MCP_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def rpc(method, params=None, id=1):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


class McpAppTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "vault"
        self.root.mkdir()
        write_note(self.root, "memory", "Agent memory", "vector memory for llm agents")
        write_archive(self.root, "memory", "the full captured text")
        (base / "outside.md").write_text("SECRET", encoding="utf-8")

        self.settings = KbSettings.from_env(
            {
                "VAULT_PATH": str(self.root),
                "KB_INDEX_DIR": str(base / "index"),
                "KB_AUTH_TOKENS": TOKEN,
                "KB_ALLOWED_HOSTS": "testserver",
            }
        )
        self.app = create_app(self.settings, embedder=FakeEmbedder())

    def tearDown(self):
        self._tmp.cleanup()

    def call(self, client, name, arguments):
        response = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json=rpc("tools/call", {"name": name, "arguments": arguments}),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]

    def test_requires_a_token(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.post("/mcp", json=rpc("tools/list")).status_code, 401)
            wrong = dict(MCP_HEADERS, Authorization="Bearer nope")
            self.assertEqual(
                client.post("/mcp", headers=wrong, json=rpc("tools/list")).status_code, 401
            )
            self.assertEqual(
                client.post(
                    f"/mcp?token={TOKEN}", headers=MCP_HEADERS, json=rpc("tools/list")
                ).status_code,
                400,
            )

    def test_initialize_and_list_tools(self):
        with TestClient(self.app) as client:
            init = client.post(
                "/mcp",
                headers=MCP_HEADERS,
                json=rpc(
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                ),
            )
            self.assertEqual(init.status_code, 200, init.text)
            self.assertIn("reference data", init.json()["result"]["instructions"])

            tools = client.post("/mcp", headers=MCP_HEADERS, json=rpc("tools/list"))
            listed = tools.json()["result"]["tools"]

        self.assertEqual(
            sorted(t["name"] for t in listed),
            ["ask", "get_note", "get_source", "list_topics", "search_notes"],
        )
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in listed))

    def test_tool_calls_over_http(self):
        with TestClient(self.app) as client:
            search = self.call(client, "search_notes", {"query": "agent memory"})
            source = self.call(client, "get_source", {"id": "memory"})
            traversal = self.call(client, "get_note", {"id": "../outside"})

        cards = search["structuredContent"]["result"]
        self.assertEqual(cards[0]["id"], "memory")
        self.assertIn("the full captured text", source["content"][0]["text"])
        # Text tools are sent once, not duplicated as structured content.
        self.assertNotIn("structuredContent", source)
        self.assertNotIn("SECRET", json.dumps(traversal))
        self.assertIn("No note with that id", traversal["content"][0]["text"])

    def test_unknown_host_is_refused(self):
        with TestClient(self.app, base_url="http://evil.example") as client:
            response = client.post("/mcp", headers=MCP_HEADERS, json=rpc("tools/list"))
        self.assertEqual(response.status_code, 421)

    def test_no_api_docs_are_published(self):
        with TestClient(self.app) as client:
            for path in ["/docs", "/redoc", "/openapi.json"]:
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
