import unittest

from second_brain.fetcher import Article, FetchError
from second_brain.youtube import fetch_transcript, is_youtube_url, video_id

VID = "dQw4w9WgXcQ"  # 11 chars


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

    def test_empty_transcript_raises(self):
        with self.assertRaises(FetchError):
            fetch_transcript(
                f"https://youtu.be/{VID}", get_transcript=lambda vid: "   "
            )

    def test_not_a_video_url_raises(self):
        with self.assertRaises(FetchError):
            fetch_transcript("https://example.com/post")


if __name__ == "__main__":
    unittest.main()
