import unittest
from types import SimpleNamespace

from second_brain.fetcher import Article, FetchError
from second_brain.jina import fetch_jina


def _resp(status=200, data=None):
    return SimpleNamespace(status_code=status, json=lambda: {"data": data or {}})


class FetchJinaTest(unittest.TestCase):
    def test_returns_article_with_content_and_title(self):
        seen = {}

        def fake_get(url, **kwargs):
            seen.update(url=url, **kwargs)
            return _resp(data={"title": "My Post", "content": "# md\n![Image 1: a chart](x)"})

        art = fetch_jina("https://example.com/post", get=fake_get)
        self.assertIsInstance(art, Article)
        self.assertEqual(art.title, "My Post")
        self.assertIn("Image 1: a chart", art.text)
        self.assertEqual(art.kind, "article")
        self.assertTrue(seen["url"].endswith("https://example.com/post"))
        self.assertEqual(seen["headers"]["X-With-Generated-Alt"], "true")
        self.assertEqual(seen["headers"]["Accept"], "application/json")

    def test_api_key_sets_authorization(self):
        seen = {}
        fetch_jina(
            "https://example.com/post",
            api_key="KEY",
            get=lambda url, **k: seen.update(k) or _resp(data={"content": "x"}),
        )
        self.assertEqual(seen["headers"]["Authorization"], "Bearer KEY")

    def test_no_auth_header_without_key(self):
        seen = {}
        fetch_jina(
            "https://example.com/post",
            get=lambda url, **k: seen.update(k) or _resp(data={"content": "x"}),
        )
        self.assertNotIn("Authorization", seen["headers"])

    def test_http_error_raises(self):
        with self.assertRaises(FetchError):
            fetch_jina("https://example.com/post", get=lambda url, **k: _resp(status=451))

    def test_empty_content_raises(self):
        with self.assertRaises(FetchError):
            fetch_jina(
                "https://example.com/post", get=lambda url, **k: _resp(data={"content": " "})
            )

    def test_network_error_raises(self):
        def boom(url, **k):
            raise ConnectionError("down")

        with self.assertRaises(FetchError):
            fetch_jina("https://example.com/post", get=boom)


if __name__ == "__main__":
    unittest.main()
