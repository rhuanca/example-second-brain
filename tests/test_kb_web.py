import datetime as dt
import tempfile
import unittest
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from second_brain.kb.app import create_app
from second_brain.kb.auth import AccessVerifier
from second_brain.kb.config import KbSettings
from second_brain.kb.retrieval import Library
from second_brain.kb.topics import Taxonomy, Topic
from second_brain.vault import Vault
from tests.kb_fixtures import FakeEmbedder, write_archive, write_note

TEAM = "team.cloudflareaccess.com"
AUD = "aud-tag-123"


def _settings(root: Path, index: Path, **extra) -> KbSettings:
    return KbSettings.from_env(
        {
            "VAULT_PATH": str(root),
            "KB_INDEX_DIR": str(index),
            "KB_AUTH_TOKENS": "mcp-token",
            "KB_ALLOWED_HOSTS": "testserver",
            **extra,
        }
    )


class _Vault(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "vault"
        self.root.mkdir()
        self.index = base / "index"
        (base / "secret.md").write_text("SECRET", encoding="utf-8")

        write_note(
            self.root,
            "memory",
            "Agent memory",
            "vector memory for llm agents",
            date="2026-08-20",
            tags=("agents", "youtube"),
            key_points=("episodic memory",),
            ideas=("build a memory store",),
            source="https://youtu.be/abc",
        )
        write_note(
            self.root,
            "rag",
            "RAG evals",
            "measuring retrieval quality",
            date="2026-07-02",
            tags=("rag", "article"),
        )
        write_note(
            self.root,
            "xss",
            "<script>alert(1)</script> tricky title",
            "tldr with <img src=x onerror=alert(1)>",
            date="2026-08-01",
            source="javascript:alert(1)",
        )
        write_archive(self.root, "memory", "---\ntitle: x\n---\narchive <b>body</b> text")

        self.taxonomy = Taxonomy(
            topics=[Topic("agents", "Agents", "llm agents & memory", ["memory"])]
        )

    def tearDown(self):
        self._tmp.cleanup()

    def client(self, verifier=None, **env):
        settings = _settings(self.root, self.index, **env)
        library = Library(
            Vault(self.root), self.index, FakeEmbedder(), taxonomy_loader=lambda: self.taxonomy
        )
        app = create_app(settings, library=library, access_verifier=verifier)
        return TestClient(app)


class PagesTest(_Vault):
    def setUp(self):
        super().setUp()
        self._client = self.client(KB_WEB_ALLOW_UNAUTHENTICATED="true").__enter__()

    def tearDown(self):
        self._client.__exit__(None, None, None)
        super().tearDown()

    def get(self, path, status=200):
        response = self._client.get(path)
        self.assertEqual(response.status_code, status, path)
        return response.text

    def test_index_shows_topics_untopiced_and_recent(self):
        html = self.get("/")
        self.assertIn("Agents", html)
        self.assertIn("llm agents &amp; memory", html)
        self.assertIn('href="/notes?topic=none"', html)
        # Newest first.
        self.assertLess(html.index("Agent memory"), html.index("RAG evals"))

    def test_home_draws_the_topic_treemap_and_monthly_chart(self):
        html = self.get("/")
        self.assertIn('aria-label="Topics sized by number of notes"', html)
        self.assertIn('class="seg fill-s1"', html)  # the Agents tile, slot 1
        self.assertIn('aria-label="Notes saved per month"', html)
        self.assertIn('href="/notes?month=2026-08"', html)

    def test_cards_show_youtube_thumbnails_and_topic_colours(self):
        html = self.get("/notes")
        # The fixture's youtu.be/abc is not a valid video id, so no thumbnail;
        # it falls back to a band in its topic colour.
        self.assertNotIn("i.ytimg.com", html)
        self.assertIn('class="band tint-s1"', html)
        self.assertIn('<span class="swatch bg-s1"></span>Agents', html)

    def test_topic_listing(self):
        html = self.get("/notes?topic=agents")
        self.assertIn("Agent memory", html)
        self.assertNotIn("RAG evals", html)

    def test_untopiced_listing(self):
        html = self.get("/notes?topic=none")
        self.assertIn("RAG evals", html)
        self.assertNotIn("Agent memory", html)

    def test_unknown_topic_is_404(self):
        self.get("/notes?topic=nope", status=404)

    def test_source_and_month_facets(self):
        html = self.get("/notes")
        self.assertIn('href="/notes?source=youtube"', html)
        self.assertIn('href="/notes?month=2026-08"', html)

        youtube = self.get("/notes?source=youtube")
        self.assertIn("Agent memory", youtube)
        self.assertNotIn("RAG evals", youtube)

        july = self.get("/notes?month=2026-07")
        self.assertIn("RAG evals", july)
        self.assertNotIn("Agent memory", july)

    def test_search(self):
        html = self.get("/search?q=retrieval+quality")
        self.assertIn("RAG evals", html)
        self.assertIn("Search", self.get("/search"))

    def test_note_detail(self):
        html = self.get("/notes/memory")
        for expected in ["Agent memory", "episodic memory", "build a memory store"]:
            self.assertIn(expected, html)
        self.assertIn('href="https://youtu.be/abc" rel="noopener noreferrer"', html)
        self.assertIn('href="/notes/memory/source"', html)

    def test_source_page_is_escaped_plain_text_without_frontmatter(self):
        html = self.get("/notes/memory/source")
        self.assertIn("archive &lt;b&gt;body&lt;/b&gt; text", html)
        self.assertNotIn("title: x", html)

    def test_note_without_archive_has_no_source_page(self):
        self.assertNotIn("/source", self.get("/notes/rag"))
        self.get("/notes/rag/source", status=404)

    def test_captured_html_is_escaped_and_bad_links_are_not_linked(self):
        html = self.get("/notes/xss")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn('href="javascript:', html)

    def test_traversal_ids_are_404(self):
        for path in [
            "/notes/..%2Fsecret",
            "/notes/%2e%2e%2fsecret",
            "/notes/secret",
            "/notes/..%2F..%2Fetc%2Fpasswd/source",
            "/notes/nope/source",
        ]:
            with self.subTest(path=path):
                response = self._client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("SECRET", response.text)

    def test_real_youtube_notes_get_a_thumbnail(self):
        write_note(
            self.root, "video", "A video", "about things",
            source="https://www.youtube.com/watch?v=dQw4w9WgXcQ", tags=("youtube",),
        )
        self._client.app.state.library.refresh_if_stale(force=True)
        html = self.get("/notes/video")
        self.assertIn('src="https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"', html)

    def test_security_headers(self):
        response = self._client.get("/")
        self.assertIn("default-src 'none'", response.headers["content-security-policy"])
        self.assertIn("img-src 'self' https://i.ytimg.com", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")


class WebAuthTest(_Vault):
    def setUp(self):
        super().setUp()
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.verifier = AccessVerifier(
            TEAM, AUD, key_resolver=lambda token: self.key.public_key()
        )

    def token(self, **overrides):
        now = dt.datetime.now(dt.timezone.utc)
        claims = {
            "aud": [AUD],
            "iss": f"https://{TEAM}",
            "iat": now,
            "exp": now + dt.timedelta(minutes=5),
            "email": "me@example.com",
            **overrides,
        }
        return jwt.encode(claims, self.key, algorithm="RS256")

    def test_closed_by_default(self):
        with self.client() as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 403)
        self.assertIn("KB_CF_ACCESS_TEAM_DOMAIN", response.text)

    def test_dev_opt_out_opens_it(self):
        with self.client(KB_WEB_ALLOW_UNAUTHENTICATED="true") as client:
            self.assertEqual(client.get("/").status_code, 200)

    def test_valid_access_token_is_accepted(self):
        with self.client(self.verifier) as client:
            response = client.get("/", headers={"Cf-Access-Jwt-Assertion": self.token()})
        self.assertEqual(response.status_code, 200)

    def test_bad_access_tokens_are_refused(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = dt.datetime.now(dt.timezone.utc)
        cases = {
            "missing": None,
            "garbage": "not-a-jwt",
            "wrong audience": self.token(aud=["someone-else"]),
            "wrong issuer": self.token(iss="https://evil.cloudflareaccess.com"),
            "expired": self.token(
                iat=now - dt.timedelta(hours=2), exp=now - dt.timedelta(hours=1)
            ),
            "wrong key": jwt.encode(
                {"aud": [AUD], "iss": f"https://{TEAM}", "iat": now,
                 "exp": now + dt.timedelta(minutes=5)},
                other_key,
                algorithm="RS256",
            ),
            "alg none": jwt.encode(
                {"aud": [AUD], "iss": f"https://{TEAM}", "iat": now,
                 "exp": now + dt.timedelta(minutes=5)},
                None,
                algorithm="none",
            ),
            "HS256": jwt.encode(
                {"aud": [AUD], "iss": f"https://{TEAM}", "iat": now,
                 "exp": now + dt.timedelta(minutes=5)},
                "shared-secret-of-at-least-thirty-two-bytes!!",
                algorithm="HS256",
            ),
        }
        with self.client(self.verifier) as client:
            for name, token in cases.items():
                with self.subTest(name):
                    headers = {"Cf-Access-Jwt-Assertion": token} if token else {}
                    self.assertEqual(client.get("/", headers=headers).status_code, 403)

    def test_dev_flag_does_not_bypass_a_configured_access_check(self):
        with self.client(self.verifier, KB_WEB_ALLOW_UNAUTHENTICATED="true") as client:
            self.assertEqual(client.get("/").status_code, 403)

    def test_mcp_still_needs_its_bearer_token(self):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Cf-Access-Jwt-Assertion": self.token(),
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        with self.client(self.verifier) as client:
            self.assertEqual(client.post("/mcp", headers=headers, json=body).status_code, 401)
            headers["Authorization"] = "Bearer mcp-token"
            self.assertEqual(client.post("/mcp", headers=headers, json=body).status_code, 200)

    def test_team_domain_is_normalised(self):
        verifier = AccessVerifier(
            f"https://{TEAM}/", AUD, key_resolver=lambda token: self.key.public_key()
        )
        self.assertTrue(verifier.verify(self.token()))


if __name__ == "__main__":
    unittest.main()
