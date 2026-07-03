import unittest
from types import SimpleNamespace

from second_brain.fetcher import Article, FetchError
from second_brain.medium import fetch_medium, is_medium_url

FULL_HTML = (
    "<html><head><title>Deep Dive</title></head><body><article>"
    "<h1>Deep Dive</h1><p>This is the full member-only article body with plenty "
    "of substantive text so the extractor keeps it as the main content.</p>"
    "</article></body></html>"
)


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class IsMediumUrlTest(unittest.TestCase):
    def test_medium_com_and_subdomains(self):
        self.assertTrue(is_medium_url("https://medium.com/@user/some-slug-123"))
        self.assertTrue(is_medium_url("https://towardsdatascience.medium.com/x"))
        self.assertTrue(is_medium_url("https://www.medium.com/p/abc"))

    def test_non_medium(self):
        self.assertFalse(is_medium_url("https://example.com/post"))
        self.assertFalse(is_medium_url("https://notmedium.com/post"))


class FetchMediumTest(unittest.TestCase):
    def test_sends_sid_cookie_and_extracts_full_text(self):
        seen = {}

        def fake_get(url, **kwargs):
            seen.update(kwargs)
            seen["url"] = url
            return FakeResponse(FULL_HTML)

        article = fetch_medium("https://medium.com/@u/deep-dive", "SID123", get=fake_get)
        self.assertIsInstance(article, Article)
        self.assertIn("member-only article body", article.text)
        self.assertEqual(seen["cookies"], {"sid": "SID123"})
        self.assertEqual(seen["url"], "https://medium.com/@u/deep-dive")

    def test_download_failure_becomes_fetcherror(self):
        def boom_get(url, **kwargs):
            raise ConnectionError("network down")

        with self.assertRaises(FetchError):
            fetch_medium("https://medium.com/@u/x", "SID", get=boom_get)

    def test_http_error_status_becomes_fetcherror(self):
        class Err(FakeResponse):
            def raise_for_status(self):
                raise RuntimeError("403")

        with self.assertRaises(FetchError):
            fetch_medium(
                "https://medium.com/@u/x", "SID", get=lambda url, **k: Err("nope")
            )


if __name__ == "__main__":
    unittest.main()
