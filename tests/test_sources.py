import unittest

from second_brain.fetcher import Article
from second_brain.sources import fetch


class DispatchTest(unittest.TestCase):
    def test_youtube_url_routes_to_youtube_fetch(self):
        calls = {}
        article = fetch(
            "https://youtu.be/dQw4w9WgXcQ",
            article_fetch=lambda url: calls.setdefault("article", url),
            youtube_fetch=lambda url: Article("video", "transcript"),
        )
        self.assertEqual(article.text, "transcript")
        self.assertNotIn("article", calls)  # article path not used

    def test_web_url_routes_to_article_fetch(self):
        calls = {}
        article = fetch(
            "https://example.com/post",
            article_fetch=lambda url: Article("post", "body"),
            youtube_fetch=lambda url: calls.setdefault("youtube", url),
        )
        self.assertEqual(article.text, "body")
        self.assertNotIn("youtube", calls)  # youtube path not used


if __name__ == "__main__":
    unittest.main()
