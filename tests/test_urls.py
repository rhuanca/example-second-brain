import unittest

from second_brain.urls import extract_url, normalize_url


class ExtractUrlTest(unittest.TestCase):
    def test_no_text_returns_none(self):
        self.assertIsNone(extract_url(None))
        self.assertIsNone(extract_url(""))

    def test_text_without_url_returns_none(self):
        self.assertIsNone(extract_url("just a plain note, no links here"))

    def test_extracts_url_from_surrounding_text(self):
        text = "check this out https://example.com/article it's great"
        self.assertEqual(extract_url(text), "https://example.com/article")

    def test_extracts_first_of_multiple(self):
        text = "https://a.com/one and https://b.com/two"
        self.assertEqual(extract_url(text), "https://a.com/one")

    def test_trailing_punctuation_trimmed(self):
        self.assertEqual(
            extract_url("see https://example.com/post."),
            "https://example.com/post",
        )


class NormalizeUrlTest(unittest.TestCase):
    def test_strips_utm_and_tracking_params(self):
        url = "https://example.com/post?utm_source=nl&utm_medium=email&id=5&fbclid=xyz"
        self.assertEqual(normalize_url(url), "https://example.com/post?id=5")

    def test_removes_all_params_when_all_tracking(self):
        url = "https://example.com/post?utm_source=x&gclid=y"
        self.assertEqual(normalize_url(url), "https://example.com/post")

    def test_strips_trailing_slash(self):
        self.assertEqual(
            normalize_url("https://example.com/post/"),
            "https://example.com/post",
        )

    def test_keeps_root_slash(self):
        self.assertEqual(normalize_url("https://example.com/"), "https://example.com")

    def test_lowercases_scheme_and_host(self):
        self.assertEqual(
            normalize_url("HTTPS://Example.COM/Path"),
            "https://example.com/Path",
        )

    def test_drops_fragment(self):
        self.assertEqual(
            normalize_url("https://example.com/post#section-2"),
            "https://example.com/post",
        )

    def test_two_variants_of_same_article_dedup_equal(self):
        a = normalize_url("https://example.com/post/?utm_source=twitter")
        b = normalize_url("https://example.com/post#top")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
