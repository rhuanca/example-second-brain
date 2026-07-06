import unittest

from second_brain.fetcher import Article, FetchError
from second_brain.sources import fetch


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
            jina_fetch=lambda url, api_key=None: Article("post", "jina md"),
            article_fetch=lambda url: calls.setdefault("trafilatura", url),
        )
        self.assertEqual(article.text, "jina md")
        self.assertNotIn("trafilatura", calls)  # trafilatura not used when Jina works

    def test_jina_skipped_without_key(self):
        calls = {}
        article = fetch(
            "https://example.com/post",  # jina_api_key unset
            jina_fetch=lambda url, api_key=None: calls.setdefault("jina", url),
            article_fetch=lambda url: Article("post", "trafilatura body"),
        )
        self.assertEqual(article.text, "trafilatura body")
        self.assertNotIn("jina", calls)  # no key → captions unavailable → trafilatura

    def test_jina_failure_falls_back_to_trafilatura(self):
        def boom(url, api_key=None):
            raise FetchError("jina down")

        article = fetch(
            "https://example.com/post",
            jina_api_key="K",
            jina_fetch=boom,
            article_fetch=lambda url: Article("post", "trafilatura body"),
        )
        self.assertEqual(article.text, "trafilatura body")

    def test_jina_disabled_uses_trafilatura_directly(self):
        calls = {}
        article = fetch(
            "https://example.com/post",
            jina_enabled=False,
            jina_api_key="K",
            jina_fetch=lambda url, api_key=None: calls.setdefault("jina", url),
            article_fetch=lambda url: Article("post", "trafilatura body"),
        )
        self.assertEqual(article.text, "trafilatura body")
        self.assertNotIn("jina", calls)  # jina skipped when disabled

    def test_jina_receives_api_key(self):
        seen = {}
        fetch(
            "https://example.com/post",
            jina_api_key="JK",
            jina_fetch=lambda url, api_key=None: (
                seen.update(api_key=api_key),
                Article("p", "b"),
            )[1],
        )
        self.assertEqual(seen["api_key"], "JK")

    def test_medium_with_cookie_routes_to_medium_fetch(self):
        seen = {}
        article = fetch(
            "https://medium.com/@u/slug-123",
            medium_cookie="SID",
            article_fetch=lambda url: Article("teaser", "teaser body"),
            medium_fetch=lambda url, cookie: (
                seen.update(call=(url, cookie)),
                Article("full", "full body"),
            )[1],
        )
        self.assertEqual(article.text, "full body")
        self.assertEqual(seen["call"], ("https://medium.com/@u/slug-123", "SID"))

    def test_medium_without_cookie_falls_back_to_article_fetch(self):
        calls = {}
        article = fetch(
            "https://medium.com/@u/slug-123",
            medium_cookie=None,
            article_fetch=lambda url: Article("teaser", "teaser body"),
            medium_fetch=lambda url, cookie: calls.setdefault("medium", url),
        )
        self.assertEqual(article.text, "teaser body")
        self.assertNotIn("medium", calls)  # cookie missing → no cookie fetch


if __name__ == "__main__":
    unittest.main()
