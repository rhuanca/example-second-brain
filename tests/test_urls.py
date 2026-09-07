import unittest

from second_brain.urls import dedup_key, extract_url, normalize_url


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


class DedupKeyTest(unittest.TestCase):
    # The real pair that got saved twice: one video, two share tokens.
    SHARED_A = "https://youtu.be/ve7AA01vplE?si=-2YhO5vh9RcEiSzu"
    SHARED_B = "https://youtu.be/ve7AA01vplE?si=GxFa4HqDaut8XPNy"

    def test_youtube_share_tokens_collapse(self):
        self.assertEqual(dedup_key(self.SHARED_A), dedup_key(self.SHARED_B))

    def test_normalize_url_strips_si(self):
        self.assertEqual(normalize_url(self.SHARED_A), "https://youtu.be/ve7AA01vplE")

    def test_every_youtube_shape_shares_one_key(self):
        shapes = [
            "https://youtu.be/ve7AA01vplE",
            "https://www.youtube.com/watch?v=ve7AA01vplE",
            "https://www.youtube.com/watch?v=ve7AA01vplE&t=573s",
            "https://m.youtube.com/watch?v=ve7AA01vplE",
            "https://www.youtube.com/live/ve7AA01vplE?si=abc",
            "https://www.youtube.com/shorts/ve7AA01vplE",
        ]
        keys = {dedup_key(u) for u in shapes}
        self.assertEqual(keys, {"youtube:ve7AA01vplE"})

    def test_different_videos_keep_different_keys(self):
        self.assertNotEqual(
            dedup_key("https://youtu.be/ve7AA01vplE"),
            dedup_key("https://youtu.be/ihmashJt3I4"),
        )

    def test_non_youtube_url_drops_www_and_tracking(self):
        self.assertEqual(
            dedup_key("https://www.trydeepteam.com/docs/getting-started/?utm_source=x"),
            "https://trydeepteam.com/docs/getting-started",
        )

    def test_non_youtube_distinct_paths_stay_distinct(self):
        self.assertNotEqual(
            dedup_key("https://example.com/a"), dedup_key("https://example.com/b")
        )
