import sys
import unittest
from types import SimpleNamespace

from second_brain.fetcher import Article, FetchError
from second_brain.medium import (
    IMPERSONATE,
    MISSING_DEPENDENCY,
    _default_get,
    fetch_medium,
    is_medium_url,
)

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


class DefaultDownloaderTest(unittest.TestCase):
    """The real downloader, with a stand-in for curl_cffi in sys.modules.

    Nothing here touches the network; what is asserted is that the request is made
    the way the bot challenge requires, and locally.
    """

    def _install(self, module):
        for name in ["curl_cffi", "curl_cffi.requests"]:
            self.addCleanup(sys.modules.pop, name, None)
        original = {name: sys.modules.get(name) for name in ["curl_cffi"]}
        self.addCleanup(lambda: sys.modules.update({k: v for k, v in original.items() if v}))
        sys.modules["curl_cffi"] = module

    def test_impersonates_a_browser_and_passes_the_cookie(self):
        seen = {}
        requests = SimpleNamespace(
            get=lambda url, **kwargs: (seen.update(url=url, **kwargs), "resp")[1]
        )
        self._install(SimpleNamespace(requests=requests))

        self.assertEqual(_default_get("https://medium.com/@u/x", cookies={"sid": "S"}), "resp")
        self.assertEqual(seen["impersonate"], IMPERSONATE)
        self.assertEqual(seen["cookies"], {"sid": "S"})
        self.assertEqual(seen["url"], "https://medium.com/@u/x")

    def test_missing_extra_says_how_to_install_it(self):
        self._install(None)  # importing it raises ImportError
        with self.assertRaises(FetchError) as caught:
            _default_get("https://medium.com/@u/x")
        self.assertIn("uv sync --extra browser", str(caught.exception))

    def test_the_missing_extra_error_survives_the_downloader(self):
        """`fetch_medium` swallows network errors so the next route can try, but a
        missing dependency is a setup problem and must not be silent."""
        def missing(url, **kwargs):
            raise FetchError(MISSING_DEPENDENCY)

        with self.assertRaises(FetchError) as caught:
            fetch_medium("https://medium.com/@u/x", "SID", get=missing)
        self.assertIn("uv sync --extra browser", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
