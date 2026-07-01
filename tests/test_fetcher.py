import unittest

from second_brain.fetcher import Article, FetchError, fetch


class FetchTest(unittest.TestCase):
    def test_good_page_returns_title_and_text(self):
        article = fetch(
            "https://example.com/post",
            downloader=lambda url: "<html>ok</html>",
            extractor=lambda html: {"title": "My Post", "text": "  Body text.  "},
        )
        self.assertIsInstance(article, Article)
        self.assertEqual(article.title, "My Post")
        self.assertEqual(article.text, "Body text.")

    def test_missing_title_falls_back_to_url(self):
        article = fetch(
            "https://example.com/post",
            downloader=lambda url: "<html>ok</html>",
            extractor=lambda html: {"title": None, "text": "Body."},
        )
        self.assertEqual(article.title, "https://example.com/post")

    def test_download_returns_nothing_raises(self):
        with self.assertRaises(FetchError):
            fetch("https://blocked.example", downloader=lambda url: None)

    def test_empty_extraction_raises(self):
        with self.assertRaises(FetchError):
            fetch(
                "https://example.com/js-only",
                downloader=lambda url: "<html>spa</html>",
                extractor=lambda html: {"title": "T", "text": ""},
            )

    def test_none_extraction_raises(self):
        with self.assertRaises(FetchError):
            fetch(
                "https://example.com/weird",
                downloader=lambda url: "<html></html>",
                extractor=lambda html: None,
            )

    def test_downloader_receives_url(self):
        seen = {}

        def dl(url):
            seen["url"] = url
            return "<html>ok</html>"

        fetch(
            "https://example.com/x",
            downloader=dl,
            extractor=lambda html: {"title": "t", "text": "body"},
        )
        self.assertEqual(seen["url"], "https://example.com/x")


if __name__ == "__main__":
    unittest.main()
