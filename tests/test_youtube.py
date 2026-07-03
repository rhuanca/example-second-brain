import unittest

from types import SimpleNamespace

from second_brain.fetcher import Article, FetchError
from second_brain.youtube import (
    fetch_transcript,
    is_youtube_url,
    supadata_transcript,
    video_id,
)

VID = "dQw4w9WgXcQ"  # 11 chars


def _resp(status=200, json_body=None):
    return SimpleNamespace(status_code=status, json=lambda: (json_body or {}))


class VideoIdTest(unittest.TestCase):
    def test_watch_url(self):
        self.assertEqual(video_id(f"https://www.youtube.com/watch?v={VID}"), VID)

    def test_watch_url_extra_params(self):
        self.assertEqual(
            video_id(f"https://youtube.com/watch?v={VID}&t=42s&list=xyz"), VID
        )

    def test_short_youtu_be(self):
        self.assertEqual(video_id(f"https://youtu.be/{VID}"), VID)

    def test_shorts_and_embed(self):
        self.assertEqual(video_id(f"https://www.youtube.com/shorts/{VID}"), VID)
        self.assertEqual(video_id(f"https://www.youtube.com/embed/{VID}"), VID)

    def test_non_youtube_is_none(self):
        self.assertIsNone(video_id("https://example.com/watch?v=abc"))

    def test_bad_id_length_is_none(self):
        self.assertIsNone(video_id("https://youtu.be/tooShort"))

    def test_is_youtube_url(self):
        self.assertTrue(is_youtube_url(f"https://youtu.be/{VID}"))
        self.assertFalse(is_youtube_url("https://example.com/post"))


class FetchTranscriptTest(unittest.TestCase):
    def test_happy_path_builds_article(self):
        article = fetch_transcript(
            f"https://youtu.be/{VID}",
            get_transcript=lambda vid: "hello world transcript",
            get_title=lambda vid, url: "Rick Astley - Never Gonna Give You Up",
        )
        self.assertIsInstance(article, Article)
        self.assertEqual(article.text, "hello world transcript")
        self.assertIn("Never Gonna", article.title)

    def test_missing_title_falls_back_to_video_id(self):
        article = fetch_transcript(
            f"https://youtu.be/{VID}",
            get_transcript=lambda vid: "text",
            get_title=lambda vid, url: None,
        )
        self.assertIn(VID, article.title)

    def test_title_error_is_non_fatal(self):
        def boom_title(vid, url):
            raise RuntimeError("oembed down")

        article = fetch_transcript(
            f"https://youtu.be/{VID}",
            get_transcript=lambda vid: "text",
            get_title=boom_title,
        )
        self.assertIn(VID, article.title)

    def test_no_transcript_raises_fetcherror(self):
        def boom(vid):
            raise RuntimeError("TranscriptsDisabled")

        with self.assertRaises(FetchError):
            fetch_transcript(f"https://youtu.be/{VID}", get_transcript=boom)

    def test_ip_block_gets_distinct_message(self):
        class IpBlocked(Exception):  # mirrors youtube_transcript_api's class name
            pass

        def boom(vid):
            raise IpBlocked()

        with self.assertRaises(FetchError) as ctx:
            fetch_transcript(f"https://youtu.be/{VID}", get_transcript=boom)
        self.assertIn("IP", str(ctx.exception))

    def test_empty_transcript_raises(self):
        with self.assertRaises(FetchError):
            fetch_transcript(
                f"https://youtu.be/{VID}", get_transcript=lambda vid: "   "
            )

    def test_not_a_video_url_raises(self):
        with self.assertRaises(FetchError):
            fetch_transcript("https://example.com/post")

    def test_uses_supadata_when_api_key_set(self):
        seen = {}

        def fake_get(url, **kwargs):
            seen.update(url=url, **kwargs)
            return _resp(json_body={"content": "transcript via supadata"})

        article = fetch_transcript(
            f"https://youtu.be/{VID}",
            api_key="KEY",
            get=fake_get,
            get_title=lambda vid, url: "Some Video",
        )
        self.assertEqual(article.text, "transcript via supadata")
        self.assertEqual(seen["headers"], {"x-api-key": "KEY"})
        self.assertIn(VID, seen["params"]["url"])


class SupadataTranscriptTest(unittest.TestCase):
    def test_returns_content(self):
        text = supadata_transcript(
            VID, "KEY", get=lambda url, **k: _resp(json_body={"content": "hello"})
        )
        self.assertEqual(text, "hello")

    def test_202_is_a_clear_error(self):
        with self.assertRaises(FetchError) as ctx:
            supadata_transcript(VID, "KEY", get=lambda url, **k: _resp(status=202))
        self.assertIn("generated", str(ctx.exception).lower())

    def test_404_and_403_map_to_fetcherror(self):
        for status in (404, 403, 500):
            with self.assertRaises(FetchError):
                supadata_transcript(VID, "KEY", get=lambda url, **k: _resp(status=status))

    def test_empty_content_raises(self):
        with self.assertRaises(FetchError):
            supadata_transcript(
                VID, "KEY", get=lambda url, **k: _resp(json_body={"content": "  "})
            )

    def test_network_error_becomes_fetcherror(self):
        def boom(url, **k):
            raise ConnectionError("down")

        with self.assertRaises(FetchError):
            supadata_transcript(VID, "KEY", get=boom)


if __name__ == "__main__":
    unittest.main()
