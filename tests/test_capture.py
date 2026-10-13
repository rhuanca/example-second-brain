import unittest

from second_brain.capture import (
    SLIDES_HEADING,
    TRANSCRIPT_HEADING,
    Upload,
    classify,
    merge,
    read_transcript,
)


def pdf(name="deck.pdf", mime="application/pdf"):
    return Upload(name, mime, b"%PDF-1.4 slides")


def text(name="talk.txt", mime="text/plain"):
    return Upload(name, mime, b"we begin with the problem")


class ClassifyTest(unittest.TestCase):
    """Two files are a talk only in one shape; everything else keeps today's
    behaviour, which is a note per file."""

    def test_slides_and_a_transcript_in_either_order(self):
        for files in ([pdf(), text()], [text(), pdf()]):
            with self.subTest(order=[f.filename for f in files]):
                talk = classify(files)
                self.assertIsNotNone(talk)
                self.assertEqual(talk.slides.filename, "deck.pdf")
                self.assertEqual(talk.transcript.filename, "talk.txt")

    def test_markdown_counts_as_a_transcript(self):
        self.assertIsNotNone(classify([pdf(), text("notes.md", "text/markdown")]))

    def test_a_name_settles_it_when_telegram_sends_octet_stream(self):
        """Telegram often types a .md upload as application/octet-stream."""
        self.assertIsNotNone(classify([pdf(), text("notes.md", "application/octet-stream")]))
        self.assertIsNotNone(classify([pdf("deck.pdf", "application/octet-stream"), text()]))

    def test_what_is_not_a_talk(self):
        cases = {
            "two decks": [pdf("a.pdf"), pdf("b.pdf")],
            "two transcripts": [text("a.txt"), text("b.txt")],
            "one file": [pdf()],
            "three files": [pdf(), text(), text("extra.txt")],
            "nothing": [],
            "a deck and an image": [pdf(), Upload("photo.png", "image/png", b"\x89PNG")],
        }
        for label, files in cases.items():
            with self.subTest(case=label):
                self.assertIsNone(classify(files))


class ReadTranscriptTest(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(read_transcript(b"hello there\n"), "hello there")

    def test_a_bom_and_windows_line_endings(self):
        text = "﻿line one\r\nline two\r\n".encode("utf-8")
        self.assertEqual(read_transcript(text), "line one\nline two")

    def test_trailing_space_on_every_line_goes(self):
        self.assertEqual(read_transcript(b"one   \ntwo \n"), "one\ntwo")

    def test_undecodable_bytes_do_not_raise(self):
        out = read_transcript(b"good \xff\xfe bytes")
        self.assertIn("good", out)
        self.assertIn("bytes", out)


class MergeTest(unittest.TestCase):
    def test_both_sections_in_order_and_labelled(self):
        archive = merge(
            "Slide one\nSlide two",
            "So the first thing to say is…",
            slides_name="deck.pdf",
            transcript_name="talk.txt",
        )

        self.assertLess(archive.index(SLIDES_HEADING), archive.index(TRANSCRIPT_HEADING))
        self.assertIn("From deck.pdf", archive)
        self.assertIn("From talk.txt", archive)
        self.assertIn("Slide two", archive)
        self.assertIn("first thing to say", archive)

    def test_an_empty_transcript_leaves_only_the_slides(self):
        archive = merge("Slide one", "   ")
        self.assertIn(SLIDES_HEADING, archive)
        self.assertNotIn(TRANSCRIPT_HEADING, archive)

    def test_an_empty_deck_leaves_only_the_transcript(self):
        archive = merge("", "what was said")
        self.assertNotIn(SLIDES_HEADING, archive)
        self.assertIn("what was said", archive)

    def test_nothing_at_all(self):
        self.assertEqual(merge("", ""), "")


if __name__ == "__main__":
    unittest.main()
