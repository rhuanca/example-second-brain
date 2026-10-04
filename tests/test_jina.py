import unittest
from types import SimpleNamespace

from second_brain.fetcher import Article, FetchError
from second_brain.jina import fetch_jina


ARTICLE = "A real article, with enough words in it to be one. " * 20
THIN = "A 1x1 image, likely be a tacker probe"  # what a poisoned cache served


def _resp(status=200, data=None):
    return SimpleNamespace(status_code=status, json=lambda: {"data": data or {}})


class FetchJinaTest(unittest.TestCase):
    def test_returns_article_with_content_and_title(self):
        seen = {}

        def fake_get(url, **kwargs):
            seen.update(url=url, **kwargs)
            return _resp(data={"title": "My Post", "content": f"# md\n![Image 1: a chart](x)\n{ARTICLE}"})

        art = fetch_jina("https://example.com/post", api_key="KEY", get=fake_get)
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
            get=lambda url, **k: seen.update(k) or _resp(data={"content": ARTICLE}),
        )
        self.assertEqual(seen["headers"]["Authorization"], "Bearer KEY")

    def test_no_auth_header_without_key(self):
        seen = {}
        fetch_jina(
            "https://example.com/post",
            get=lambda url, **k: seen.update(k) or _resp(data={"content": ARTICLE}),
        )
        self.assertNotIn("Authorization", seen["headers"])

    def test_captions_are_only_requested_with_a_key(self):
        # Asking for generated alt text without a key fails the whole request (401),
        # so a keyless read must not ask for it.
        seen = {}
        fetch_jina(
            "https://example.com/post",
            get=lambda url, **k: seen.update(k) or _resp(data={"content": ARTICLE}),
        )
        self.assertNotIn("X-With-Generated-Alt", seen["headers"])

    def test_http_error_raises(self):
        with self.assertRaises(FetchError):
            fetch_jina("https://example.com/post", get=lambda url, **k: _resp(status=451))

    def test_a_cached_snapshot_of_junk_is_retried_without_the_cache(self):
        """The failure this guards: Jina served a cached 37-character caption of a
        1x1 ad pixel for a real article, and we filed it as a note."""
        calls = []

        def fake_get(url, **kwargs):
            calls.append(kwargs["headers"])
            thin = len(calls) == 1
            return _resp(data={"title": "x", "content": THIN if thin else ARTICLE})

        art = fetch_jina("https://example.com/post", get=fake_get)

        self.assertIn(ARTICLE.strip()[:20], art.text)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("X-No-Cache", calls[0])
        self.assertEqual(calls[1]["X-No-Cache"], "true")

    def test_thin_after_the_retry_raises_so_the_caller_falls_back(self):
        calls = []

        def fake_get(url, **kwargs):
            calls.append(kwargs["headers"])
            return _resp(data={"title": "x", "content": THIN})

        with self.assertRaises(FetchError) as caught:
            fetch_jina("https://example.com/post", get=fake_get)

        self.assertEqual(len(calls), 2)  # tried, retried, gave up
        self.assertIn("not the article", str(caught.exception))

    def test_a_good_answer_is_not_retried(self):
        calls = []
        fetch_jina(
            "https://example.com/post",
            get=lambda url, **k: calls.append(k) or _resp(data={"content": ARTICLE}),
        )
        self.assertEqual(len(calls), 1)

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
