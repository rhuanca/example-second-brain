import unittest

from second_brain.fetcher import Article
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

    def test_web_url_routes_to_article_fetch(self):
        calls = {}
        article = fetch(
            "https://example.com/post",
            article_fetch=lambda url: Article("post", "body"),
            youtube_fetch=lambda url, api_key=None: calls.setdefault("youtube", url),
        )
        self.assertEqual(article.text, "body")
        self.assertNotIn("youtube", calls)  # youtube path not used

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
