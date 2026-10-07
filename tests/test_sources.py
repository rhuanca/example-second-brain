import unittest

from second_brain.fetcher import Article, FetchError
from second_brain.sources import fetch

# Routing tests care which route answered, not how much it said -- but a route
# that answers with almost nothing is now demoted, so the bodies have to be the
# length of a real article. The marker says which route produced it.
def body(marker: str) -> str:
    return f"{marker}: " + "enough words here to count as an article. " * 12



class DispatchTest(unittest.TestCase):
    def test_youtube_url_routes_to_youtube_fetch(self):
        calls = {}
        article = fetch(
            "https://youtu.be/dQw4w9WgXcQ",
            article_fetch=lambda url: calls.setdefault("article", url),
            youtube_fetch=lambda url, api_key=None: Article("video", "transcript"),
        )
        self.assertEqual(article.text, "transcript")
        self.assertNotIn("article", calls)  # article path not used

    def test_youtube_receives_supadata_api_key(self):
        seen = {}
        fetch(
            "https://youtu.be/dQw4w9WgXcQ",
            supadata_api_key="KEY",
            youtube_fetch=lambda url, api_key=None: (
                seen.update(api_key=api_key),
                Article("v", "t"),
            )[1],
        )
        self.assertEqual(seen["api_key"], "KEY")

    def test_web_url_uses_jina_when_key_set(self):
        calls = {}
        article = fetch(
            "https://example.com/post",
            jina_api_key="K",
            jina_fetch=lambda url, api_key=None: Article("post", body("jina md")),
            article_fetch=lambda url: calls.setdefault("trafilatura", url),
        )
        self.assertIn("jina md", article.text)
        self.assertNotIn("trafilatura", calls)  # trafilatura not used when Jina works

    def test_jina_skipped_without_key(self):
        calls = {}
        article = fetch(
            "https://example.com/post",  # jina_api_key unset
            jina_fetch=lambda url, api_key=None: calls.setdefault("jina", url),
            article_fetch=lambda url: Article("post", body("trafilatura body")),
        )
        self.assertIn("trafilatura body", article.text)
        self.assertNotIn("jina", calls)  # no key → captions unavailable → trafilatura

    def test_jina_failure_falls_back_to_trafilatura(self):
        def boom(url, api_key=None):
            raise FetchError("jina down")

        article = fetch(
            "https://example.com/post",
            jina_api_key="K",
            jina_fetch=boom,
            article_fetch=lambda url: Article("post", body("trafilatura body")),
        )
        self.assertIn("trafilatura body", article.text)

    def test_jina_disabled_uses_trafilatura_directly(self):
        calls = {}
        article = fetch(
            "https://example.com/post",
            jina_enabled=False,
            jina_api_key="K",
            jina_fetch=lambda url, api_key=None: calls.setdefault("jina", url),
            article_fetch=lambda url: Article("post", body("trafilatura body")),
        )
        self.assertIn("trafilatura body", article.text)
        self.assertNotIn("jina", calls)  # jina skipped when disabled

    def test_jina_receives_api_key(self):
        seen = {}
        fetch(
            "https://example.com/post",
            jina_api_key="JK",
            jina_fetch=lambda url, api_key=None: (
                seen.update(api_key=api_key),
                Article("p", body("jina")),
            )[1],
        )
        self.assertEqual(seen["api_key"], "JK")

    def test_medium_with_cookie_routes_to_medium_fetch(self):
        seen = {}
        article = fetch(
            "https://medium.com/@u/slug-123",
            medium_cookie="SID",
            article_fetch=lambda url: Article("teaser", body("teaser body")),
            medium_fetch=lambda url, cookie: (
                seen.update(call=(url, cookie)),
                Article("full", body("full body")),
            )[1],
        )
        self.assertIn("full body", article.text)
        self.assertEqual(seen["call"], ("https://medium.com/@u/slug-123", "SID"))

    def test_medium_without_cookie_falls_back_to_article_fetch(self):
        calls = {}
        article = fetch(
            "https://medium.com/@u/slug-123",
            medium_cookie=None,
            article_fetch=lambda url: Article("teaser", body("teaser body")),
            medium_fetch=lambda url, cookie: calls.setdefault("medium", url),
        )
        self.assertIn("teaser body", article.text)
        self.assertNotIn("medium", calls)  # cookie missing → no cookie fetch

    def test_medium_failure_falls_through_to_jina(self):
        """The regression this chain exists for: Medium began answering plain HTTP
        clients with a bot challenge, and a set cookie used to end the attempt there."""
        def blocked(url, cookie):
            raise FetchError(f"Could not download the page at {url}")

        article = fetch(
            "https://medium.com/@u/slug-123",
            medium_cookie="SID",
            jina_api_key="K",
            medium_fetch=blocked,
            jina_fetch=lambda url, api_key=None: Article("full", body("jina body")),
            article_fetch=lambda url: Article("teaser", body("teaser body")),
        )
        self.assertIn("jina body", article.text)

    def test_every_route_failing_raises_the_last_error(self):
        def blocked(url, cookie):
            raise FetchError("medium blocked")

        def jina_down(url, api_key=None, cookie=None):
            raise FetchError("jina down")

        def trafilatura_down(url):
            raise FetchError("trafilatura down")

        with self.assertRaises(FetchError) as caught:
            fetch(
                "https://medium.com/@u/slug-123",
                medium_cookie="SID",
                jina_api_key="K",
                medium_fetch=blocked,
                jina_fetch=jina_down,
                article_fetch=trafilatura_down,
            )
        self.assertIn("trafilatura down", str(caught.exception))

    def test_the_session_cookie_never_reaches_the_reader_service(self):
        """The cookie is a full Medium login: only the local route may use it."""
        seen = {}

        def blocked(url, cookie):
            raise FetchError("blocked")

        def jina(url, **kwargs):
            seen.update(kwargs)
            return Article("p", body("jina"))

        fetch(
            "https://medium.com/@u/slug-123",
            medium_cookie="SID",
            jina_api_key="K",
            medium_fetch=blocked,
            jina_fetch=jina,
        )
        self.assertEqual(set(seen), {"api_key"})
        self.assertNotIn("SID", str(seen))


class ThinResultTest(unittest.TestCase):
    """A route that answers with almost nothing has not read the page.

    The case this comes from: Jina served a cached 37-character caption of a 1x1
    ad pixel for a Databricks post, the chain stopped there, and the note was
    filed from that. trafilatura reads the same page fine.
    """

    ARTICLE = "A real article, with enough words in it to be one. " * 20

    def test_a_thin_answer_falls_through_to_the_next_route(self):
        tried = []
        article = fetch(
            "https://example.com/post",
            jina_api_key="K",
            jina_fetch=lambda url, api_key=None: (
                tried.append("jina"),
                Article("Ad pixel", "A 1x1 image, likely be a tacker probe"),
            )[1],
            article_fetch=lambda url: (
                tried.append("trafilatura"),
                Article("The real post", self.ARTICLE),
            )[1],
        )
        self.assertEqual(article.title, "The real post")
        self.assertEqual(tried, ["jina", "trafilatura"])

    def test_the_last_route_is_taken_however_short_it_is(self):
        """Thinness only decides which route to prefer -- a short post still saves."""
        article = fetch(
            "https://example.com/post",
            jina_api_key="K",
            jina_fetch=lambda url, api_key=None: Article("thin", "barely anything"),
            article_fetch=lambda url: Article("short post", "also short"),
        )
        self.assertEqual(article.text, "also short")

    def test_a_full_answer_stops_the_chain(self):
        tried = []
        fetch(
            "https://example.com/post",
            jina_api_key="K",
            jina_fetch=lambda url, api_key=None: Article("good", self.ARTICLE),
            article_fetch=lambda url: tried.append("trafilatura"),
        )
        self.assertEqual(tried, [])


class LinkedPdfTest(unittest.TestCase):
    """A reader converts a linked PDF fine, but called it an article named
    `1706.03762v7.pdf`, so the PDF icon in the browse UI never showed."""

    PAPER = "# Attention Is All You Need\n\n" + "The dominant sequence models are. " * 20

    def fetch_pdf(self, title, text=None, url="https://arxiv.org/pdf/1706.03762.pdf",
                  pdf_download=None):
        self.downloaded = []

        def download(u):
            self.downloaded.append(u)
            return b"%PDF-1.4 bytes" if pdf_download is None else pdf_download(u)

        return fetch(
            url,
            jina_api_key="K",
            jina_fetch=lambda u, api_key=None: Article(title, text or self.PAPER),
            pdf_download=download,
        )

    def test_a_pdf_link_is_tagged_as_a_pdf(self):
        article = self.fetch_pdf("1706.03762v7.pdf")
        self.assertEqual((article.source, article.kind), ("pdf", "pdf"))

    def test_a_filename_title_is_replaced_by_the_papers_own(self):
        self.assertEqual(self.fetch_pdf("1706.03762v7.pdf").title, "Attention Is All You Need")

    def test_a_real_title_from_the_reader_is_kept(self):
        self.assertEqual(self.fetch_pdf("Attention Is All You Need").title,
                         "Attention Is All You Need")

    def test_an_extensionless_pdf_url_is_caught_by_its_title(self):
        """arxiv.org/pdf/1706.03762 is a PDF with no extension in the path."""
        article = self.fetch_pdf("1706.03762v7.pdf", url="https://arxiv.org/pdf/1706.03762")
        self.assertEqual((article.source, article.kind), ("pdf", "pdf"))
        self.assertEqual(article.title, "Attention Is All You Need")

    def test_a_query_string_does_not_hide_the_extension(self):
        article = self.fetch_pdf("x.pdf", url="https://example.com/paper.pdf?download=1")
        self.assertEqual(article.source, "pdf")

    def test_an_ordinary_page_is_untouched_and_never_downloaded(self):
        article = self.fetch_pdf("A Blog Post", url="https://example.com/post")
        self.assertEqual((article.source, article.kind, article.title),
                         ("article", "article", "A Blog Post"))
        self.assertIsNone(article.original)
        self.assertEqual(self.downloaded, [])  # no second request for a web page

    def test_the_file_itself_is_kept_for_its_diagrams(self):
        article = self.fetch_pdf("1706.03762v7.pdf")
        self.assertEqual(article.original, b"%PDF-1.4 bytes")
        self.assertEqual(self.downloaded, ["https://arxiv.org/pdf/1706.03762.pdf"])

    def test_a_failed_download_costs_the_file_not_the_capture(self):
        article = self.fetch_pdf("1706.03762v7.pdf", pdf_download=lambda url: None)
        self.assertIsNone(article.original)
        self.assertEqual(article.title, "Attention Is All You Need")
        self.assertIn("dominant sequence models", article.text)


if __name__ == "__main__":
    unittest.main()
