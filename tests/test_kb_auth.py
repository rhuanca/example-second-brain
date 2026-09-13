import unittest

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from second_brain.kb.auth import McpAuthMiddleware, bearer_ok, is_mcp_path

TOKENS = ("laptop-token-abc", "cloud-token-xyz")


class BearerOkTest(unittest.TestCase):
    def test_accepts_any_configured_token(self):
        self.assertTrue(bearer_ok("Bearer laptop-token-abc", TOKENS))
        self.assertTrue(bearer_ok("Bearer cloud-token-xyz", TOKENS))
        self.assertTrue(bearer_ok("bearer cloud-token-xyz", TOKENS))

    def test_rejects_everything_else(self):
        for header in [
            None,
            "",
            "Bearer",
            "Bearer ",
            "Bearer wrong",
            "Bearer laptop-token-ab",  # one character short
            "Bearer laptop-token-abcd",  # one character long
            "Bearer laptop-token-abC",  # one character off
            "Basic laptop-token-abc",  # right token, wrong scheme
            "laptop-token-abc",  # no scheme
            "Bearer laptop-token-abc,cloud-token-xyz",
        ]:
            with self.subTest(header=header):
                self.assertFalse(bearer_ok(header, TOKENS))

    def test_no_configured_tokens_rejects_all(self):
        self.assertFalse(bearer_ok("Bearer anything", ()))
        self.assertFalse(bearer_ok("Bearer ", ("",)))

    def test_mcp_path(self):
        self.assertTrue(is_mcp_path("/mcp"))
        self.assertTrue(is_mcp_path("/mcp/"))
        self.assertFalse(is_mcp_path("/mcpx"))
        self.assertFalse(is_mcp_path("/"))


class MiddlewareTest(unittest.TestCase):
    def setUp(self):
        self.reached = []

        async def downstream(request):
            # Stands in for the MCP app: anything here would read the vault.
            self.reached.append(request.url.path)
            return PlainTextResponse("ok")

        app = Starlette(
            routes=[
                Route("/mcp", downstream, methods=["GET", "POST"]),
                Route("/", downstream),
            ]
        )
        app.add_middleware(McpAuthMiddleware, tokens=TOKENS)
        self.client = TestClient(app)

    def test_rejected_requests_never_reach_the_app(self):
        for headers in [
            {},
            {"Authorization": ""},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer wrong"},
            {"Authorization": "Bearer laptop-token-ab"},
            {"Authorization": "Basic laptop-token-abc"},
        ]:
            with self.subTest(headers=headers):
                response = self.client.post("/mcp", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertEqual(self.reached, [])

    def test_token_in_query_string_is_refused_even_with_a_valid_header(self):
        for query in ["token=laptop-token-abc", "access_token=x", "API_KEY=x"]:
            with self.subTest(query=query):
                response = self.client.post(
                    f"/mcp?{query}", headers={"Authorization": "Bearer laptop-token-abc"}
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.reached, [])

    def test_valid_token_passes(self):
        response = self.client.post(
            "/mcp", headers={"Authorization": "Bearer cloud-token-xyz"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.reached, ["/mcp"])

    def test_other_paths_are_not_its_concern(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_rejection_is_logged_without_the_token(self):
        with self.assertLogs("second_brain.kb.auth", level="WARNING") as logs:
            self.client.post("/mcp", headers={"Authorization": "Bearer sneaky-guess"})
        self.assertNotIn("sneaky-guess", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
