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
from second_brain.kb.chats import ChatStore
from second_brain.kb.state import NoteState
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
        self.now = dt.datetime(2026, 9, 21, 12, 0).timestamp()
        self.state = NoteState(base / "state.db", clock=lambda: self.now)
        self.chats = ChatStore(base / "state.db", clock=lambda: self.now)

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
            Vault(self.root),
            self.index,
            FakeEmbedder(),
            taxonomy_loader=lambda: self.taxonomy,
            state=self.state,
        )
        app = create_app(settings, library=library, access_verifier=verifier, chats=self.chats)
        return TestClient(app)


SAME_SITE = {"Sec-Fetch-Site": "same-origin"}


class _Client(_Vault):
    """An open (dev-mode) client, with helpers; no tests of its own."""

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

    def post(self, path, data, headers=SAME_SITE):
        return self._client.post(path, data=data, headers=headers, follow_redirects=False)


class PagesTest(_Client):
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

    def test_map_links_every_note_and_nothing_else(self):
        html = self.get("/map")
        for note_id in ["memory", "rag", "xss"]:
            self.assertIn(f'href="/notes/{note_id}"', html)
        self.assertEqual(html.count('class="map-hit"'), 3)
        # Titles in tooltips are escaped like everywhere else.
        self.assertIn("<title>&lt;script&gt;alert(1)&lt;/script&gt; tricky title</title>", html)
        self.assertNotIn("<script>", html)

    def test_map_highlights_one_topic(self):
        html = self.get("/map?topic=agents")
        self.assertIn("map-dot fill-s1", html)
        self.assertEqual(html.count("map-dot dim"), 2)
        self.assertIn("1 of 3 notes", html)
        self.get("/map?topic=nope", status=404)

    def test_map_stays_coloured_past_the_palette(self):
        """A real taxonomy runs to a dozen-plus topics. The map used to give up and
        draw every dot grey; now the palette's eight are coloured and the rest
        share the neutral slot, named as such in the legend."""
        self.taxonomy = Taxonomy(
            topics=[Topic(f"t{i}", f"Topic {i}", "", ["memory" if i == 0 else "rag"])
                    for i in range(12)]
        )
        self._client.app.state.library.refresh_if_stale(force=True)
        html = self.get("/map")

        self.assertIn("map-dot fill-s1", html)  # coloured, not neutral
        self.assertIn('<ul class="legend">', html)
        self.assertIn("smaller topics, or none", html)
        self.assertNotIn("Topic 11", html.split('<ul class="legend">')[1].split("</ul>")[0])

    def test_map_colours_every_topic_when_there_are_few(self):
        self.taxonomy = Taxonomy(
            topics=[
                Topic("agents", "Agents", "", ["memory"]),
                Topic("evals", "Evals", "", ["rag"]),
            ]
        )
        self._client.app.state.library.refresh_if_stale(force=True)
        html = self.get("/map")
        self.assertIn("map-dot fill-s1", html)
        self.assertIn("map-dot fill-s2", html)
        self.assertIn("map-dot fill-s0", html)  # the untopiced note
        self.assertIn('<ul class="legend">', html)

    def test_timeline_by_topic_and_by_source(self):
        by_topic = self.get("/timeline")
        self.assertIn('aria-label="Notes saved per month, stacked by topic"', by_topic)
        self.assertIn('href="/notes?topic=agents&amp;month=2026-08"', by_topic)
        self.assertIn("Show as a table", by_topic)

        by_source = self.get("/timeline?by=source")
        self.assertIn('href="/notes?source=youtube&amp;month=2026-08"', by_source)
        self.assertIn("<th>Youtube</th>", by_source)
        # Anything unexpected falls back to topics rather than erroring.
        self.assertIn("stacked by topic", self.get("/timeline?by=%3Cscript%3E"))

    def test_nav_marks_the_current_page(self):
        self.assertIn('<a href="/map" aria-current="page">Map</a>', self.get("/map"))

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


class StarAndArchiveTest(_Client):
    def test_star_and_unstar(self):
        response = self.post("/notes/rag/star", {"on": "1"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/notes/rag")
        self.assertEqual(self.state.starred(), {"rag"})

        self.assertIn('aria-pressed="true"', self.get("/notes/rag"))
        self.assertIn("★ Starred", self.get("/"))
        starred = self.get("/notes?starred=1")
        self.assertIn("RAG evals", starred)
        self.assertNotIn("Agent memory", starred)

        self.post("/notes/rag/star", {"on": "0"})
        self.assertEqual(self.state.starred(), set())
        self.assertNotIn("★ Starred", self.get("/"))

    def test_archived_notes_leave_the_pages_but_keep_their_url(self):
        self.assertEqual(self.post("/notes/memory/archive", {"on": "1"}).status_code, 303)

        for path in ["/", "/notes", "/notes?topic=agents", "/map", "/search?q=vector+memory"]:
            with self.subTest(path=path):
                self.assertNotIn("/notes/memory", self.get(path))
        page = self.get("/notes/memory")
        self.assertIn("Archived — hidden", page)
        self.assertIn("Unarchive", page)

        self.post("/notes/memory/archive", {"on": "0"})
        self.assertIn("/notes/memory", self.get("/notes"))

    def test_writes_from_other_sites_are_refused(self):
        for headers in [
            {"Sec-Fetch-Site": "cross-site"},
            {"Origin": "https://evil.example"},
            {"Origin": "null"},
            {},
        ]:
            with self.subTest(headers=headers):
                response = self.post("/notes/rag/star", {"on": "1"}, headers=headers)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.state.starred(), set())
        # A browser without Sec-Fetch-Site still gets through with a matching Origin.
        ok = self.post("/notes/rag/star", {"on": "1"}, headers={"Origin": "http://testserver"})
        self.assertEqual(ok.status_code, 303)

    def test_unknown_and_crafted_ids_are_404(self):
        for note_id in ["nope", "..%2Fsecret"]:
            with self.subTest(note_id=note_id):
                self.assertEqual(self.post(f"/notes/{note_id}/star", {"on": "1"}).status_code, 404)
        self.assertEqual(self.state.starred(), set())

    def test_oversized_form_is_refused(self):
        response = self.post("/notes/rag/star", {"on": "1", "pad": "x" * 2000})
        self.assertEqual(response.status_code, 413)

    def test_reading_a_note_is_counted(self):
        self.assertIn("First time reading this", self.get("/notes/memory"))
        self.get("/notes/memory/source")
        self.state.record_read("memory", "mcp")
        # The page shows the reads before this visit.
        page = self.get("/notes/memory")
        self.assertIn("Read 2× on the web and 1× by agents · last 21 Sep 2026", page)
        self.assertEqual(self.state.read_stats()["memory"].web, 3)


class ArchivePageTest(_Client):
    def test_lists_archived_notes_and_unarchives_back_to_the_page(self):
        self.post("/notes/rag/archive", {"on": "1"})
        page = self.get("/archive")
        self.assertIn("Archived · 1", page)
        self.assertIn('href="/notes/rag"', page)

        response = self.post("/notes/rag/archive", {"on": "0", "back": "archive"})
        self.assertEqual(response.headers["location"], "/archive")
        self.assertIn("No archived notes.", self.get("/archive"))

    def test_candidates_are_old_unread_and_unstarred(self):
        self.assertIn("Nothing looks stale.", self.get("/archive"))

        # Four months on: everything is older than 90 days.
        self.now = dt.datetime(2027, 1, 15).timestamp()
        self.post("/notes/xss/star", {"on": "1"})
        self._client.get("/notes/memory")  # read just now: not stale
        page = self.get("/archive")
        self.assertIn("Candidates · 1", page)
        self.assertIn('href="/notes/rag"', page)


class ArchiveCandidatesTest(unittest.TestCase):
    def test_order_and_rules(self):
        from second_brain.kb.notes import Card
        from second_brain.kb.state import ReadStats
        from second_brain.kb.web import archive_candidates

        now = dt.datetime(2026, 12, 1).timestamp()
        day = 86_400
        cards = [
            Card("fresh", "Fresh", date="2026-11-20"),
            Card("undated", "Undated", date=""),
            Card("starred", "Starred", date="2026-01-01"),
            Card("read-lately", "Read lately", date="2026-01-01"),
            Card("read-long-ago", "Read long ago", date="2026-01-01"),
            Card("never-newer", "Never read, newer", date="2026-05-01"),
            Card("never-older", "Never read, older", date="2026-02-01"),
        ]
        stats = {
            "read-lately": ReadStats(web=1, last_at=now - 10 * day),
            "read-long-ago": ReadStats(mcp=2, last_at=now - 200 * day),
        }
        picked = archive_candidates(cards, stats, starred=lambda i: i == "starred", now=now)
        self.assertEqual(
            [c.note_id for c in picked], ["never-older", "never-newer", "read-long-ago"]
        )


class SavedChatsTest(_Client):
    def setUp(self):
        super().setUp()
        # Chat needs a key to be on; saved chats are read through its page.
        self._client.__exit__(None, None, None)
        self._client = self.client(
            KB_WEB_ALLOW_UNAUTHENTICATED="true", ANTHROPIC_API_KEY="sk-test"
        ).__enter__()

    def chat_with(self, title="What is a semantic layer?", answer="It is [[rag]]."):
        chat = self.chats.create(title)
        self.chats.append(chat, "user", title)
        self.chats.append(chat, "assistant", answer, source_ids=["rag"], cited=["rag"])
        return chat

    def test_the_sidebar_lists_chats_newest_first(self):
        first = self.chat_with("Older question")
        self.now += 60
        second = self.chat_with("Newer question")

        page = self.get("/chat")
        self.assertIn("Older question", page)
        self.assertIn("Newer question", page)
        self.assertLess(page.index(f"/chat/{second}"), page.index(f"/chat/{first}"))

    def test_reopening_a_chat_marks_it_and_serves_its_messages(self):
        chat = self.chat_with()

        page = self.get(f"/chat/{chat}")
        self.assertIn(f'data-conversation="{chat}"', page)
        self.assertIn('aria-current="page"', page)

        saved = self._client.get(f"/api/chats/{chat}").json()
        self.assertEqual(saved["title"], "What is a semantic layer?")
        self.assertEqual([m["role"] for m in saved["messages"]], ["user", "assistant"])
        answer = saved["messages"][1]
        self.assertEqual(answer["cited"], ["rag"])
        self.assertEqual(answer["notes"][0]["title"], "RAG evals")  # resolved through the library

    def test_unknown_or_deleted_chats_are_404(self):
        for path in ["/chat/nope", "/chat/" + "0" * 32, "/api/chats/" + "0" * 32]:
            with self.subTest(path=path):
                self.assertEqual(self._client.get(path).status_code, 404)

    def test_notes_that_no_longer_exist_are_left_out(self):
        chat = self.chats.create("t")
        self.chats.append(self.chats.recent()[0].id, "assistant", "a", source_ids=["gone", "rag"])
        saved = self._client.get(f"/api/chats/{chat}").json()
        self.assertEqual([n["id"] for n in saved["messages"][0]["notes"]], ["rag"])

    def test_rename(self):
        chat = self.chat_with()
        response = self.post(f"/chat/{chat}/rename", {"title": "  Semantic layers  "})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"/chat/{chat}")
        self.assertEqual(self.chats.get(chat).title, "Semantic layers")

    def test_delete_returns_to_a_fresh_chat(self):
        chat = self.chat_with()
        response = self.post(f"/chat/{chat}/delete", {})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/chat")
        self.assertIsNone(self.chats.get(chat))
        self.assertNotIn("What is a semantic layer?", self.get("/chat"))

    def test_rename_and_delete_are_refused_from_other_sites(self):
        chat = self.chat_with()
        for path, data in [(f"/chat/{chat}/rename", {"title": "x"}), (f"/chat/{chat}/delete", {})]:
            for headers in [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}, {}]:
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.post(path, data, headers=headers).status_code, 403)
        self.assertIsNotNone(self.chats.get(chat))

    def test_an_unknown_chat_cannot_be_renamed_or_deleted(self):
        for path in ["/chat/nope/rename", "/chat/" + "0" * 32 + "/delete"]:
            with self.subTest(path=path):
                self.assertEqual(self.post(path, {"title": "x"}).status_code, 404)


class OriginalFileTest(_Client):
    """A paper's diagrams live in the file, so the note offers it for download."""

    def store_pdf(self, note_id="memory", data=b"%PDF-1.4 with a diagram"):
        folder = self.root / "sources"
        folder.mkdir(exist_ok=True)
        (folder / f"{note_id}.pdf").write_bytes(data)
        self._client.app.state.library.refresh_if_stale(force=True)

    def test_the_file_is_served_as_a_download(self):
        self.store_pdf()
        response = self._client.get("/notes/memory/file")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.content, b"%PDF-1.4 with a diagram")

    def test_the_note_links_it_with_its_size(self):
        self.store_pdf(data=b"%PDF" + b"x" * (2 * 1024 * 1024))
        page = self.get("/notes/memory")
        self.assertIn('href="/notes/memory/file"', page)
        self.assertIn("Original PDF (2.0 MB)", page)

    def test_a_note_without_one_offers_nothing(self):
        page = self.get("/notes/memory")
        self.assertNotIn("/notes/memory/file", page)
        self.assertEqual(self._client.get("/notes/memory/file").status_code, 404)

    def test_unknown_and_crafted_ids_are_404(self):
        self.store_pdf()
        for note_id in ["nope", "..%2Fsecret", "memory.md"]:
            with self.subTest(note_id=note_id):
                self.assertEqual(self._client.get(f"/notes/{note_id}/file").status_code, 404)


class DeleteTest(_Client):
    def test_a_get_never_deletes_it_only_asks(self):
        page = self.get("/notes/memory/delete")
        self.assertIn("Delete this note?", page)
        self.assertIn("Agent memory", page)
        self.assertIn('action="/notes/memory/delete"', page)
        self.assertIn('href="/notes/memory"', page)  # cancel
        self.assertIn("/notes/memory", self.get("/notes"))  # still there

    def test_deleting_moves_it_to_the_trash_and_out_of_the_library(self):
        response = self.post("/notes/memory/delete", {})

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/trash")
        for path in ["/", "/notes", "/map", "/search?q=vector+memory"]:
            with self.subTest(path=path):
                self.assertNotIn("/notes/memory", self.get(path))
        self.assertEqual(self._client.get("/notes/memory").status_code, 404)
        self.assertIn("Agent memory", self.get("/trash"))

    def test_restoring_puts_it_back(self):
        self.post("/notes/memory/delete", {})
        response = self.post("/trash/restore", {"name": "memory.md"})

        self.assertEqual(response.status_code, 303)
        self.assertIn("/notes/memory", self.get("/notes"))
        self.assertIn("The trash is empty", self.get("/trash"))

    def test_emptying_asks_first(self):
        self.post("/notes/memory/delete", {})

        self.assertIn("Empty trash…", self.get("/trash"))
        self.assertIn("Delete 1 note for good", self.get("/trash?confirm=empty"))

        self.assertEqual(self.post("/trash/empty", {}).status_code, 303)
        self.assertIn("The trash is empty", self.get("/trash"))
        self.assertEqual(self.post("/trash/restore", {"name": "memory.md"}).status_code, 404)

    def test_every_write_is_refused_from_another_site(self):
        for path, data in [
            ("/notes/memory/delete", {}),
            ("/trash/restore", {"name": "memory.md"}),
            ("/trash/empty", {}),
        ]:
            for headers in [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}, {}]:
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.post(path, data, headers=headers).status_code, 403)
        self.assertIn("/notes/memory", self.get("/notes"))

    def test_unknown_and_crafted_ids_are_404(self):
        for note_id in ["nope", "..%2Fsecret"]:
            with self.subTest(note_id=note_id):
                self.assertEqual(self._client.get(f"/notes/{note_id}/delete").status_code, 404)
                self.assertEqual(self.post(f"/notes/{note_id}/delete", {}).status_code, 404)
        self.assertEqual(self.post("/trash/restore", {"name": "../secret.md"}).status_code, 404)
        self.assertTrue((self.root.parent / "secret.md").is_file())  # never touched

    def test_an_archived_note_can_still_be_deleted(self):
        self.post("/notes/memory/archive", {"on": "1"})
        self.assertEqual(self.post("/notes/memory/delete", {}).status_code, 303)
        self.assertIn("Agent memory", self.get("/trash"))


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
