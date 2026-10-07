import importlib.util
import unittest
from types import SimpleNamespace

from second_brain.fetcher import FetchError
from second_brain.pdf import (
    MAX_FILE_BYTES,
    MISSING_DEPENDENCY,
    NO_TEXT_LAYER,
    download,
    extract_pdf,
    looks_like_a_filename,
    title_from,
)

BODY = "A real paper, with enough words in it to count as one. " * 12


def reader(pages, title=None):
    return lambda data: (pages, title)


class TitleTest(unittest.TestCase):
    def test_a_heading_wins(self):
        self.assertEqual(title_from("# Attention Is All You Need\n\nbody"), "Attention Is All You Need")

    def test_otherwise_the_first_short_line(self):
        self.assertEqual(title_from("Attention Is All You Need\n\nbody"), "Attention Is All You Need")

    def test_a_long_opening_line_is_skipped_for_the_next_one(self):
        """Papers open with licence boilerplate: the arXiv transformer paper leads
        with Google's reproduction notice, then the actual title."""
        text = "## " + "Provided proper attribution is provided, " * 5 + "\nAttention Is All You Need"
        self.assertEqual(title_from(text, "fallback"), "Attention Is All You Need")

    def test_nothing_short_enough_falls_back(self):
        self.assertEqual(title_from("\n".join(["x" * 200] * 40), "fallback"), "fallback")

    def test_empty_text_falls_back(self):
        self.assertEqual(title_from("   \n\n", "fallback"), "fallback")

    def test_filenames_are_recognised(self):
        self.assertTrue(looks_like_a_filename("1706.03762v7.pdf"))
        self.assertFalse(looks_like_a_filename("Attention Is All You Need"))


class ExtractTest(unittest.TestCase):
    def test_pages_are_joined_and_tagged_as_a_pdf(self):
        article = extract_pdf(b"%PDF", "paper.pdf", reader=reader([BODY, "page two"]))

        self.assertEqual((article.source, article.kind), ("pdf", "pdf"))
        self.assertIn("page two", article.text)
        self.assertTrue(article.text.startswith("A real paper"))

    def test_the_documents_own_title_is_preferred(self):
        article = extract_pdf(b"%PDF", "1706.03762v7.pdf",
                              reader=reader([BODY], "Attention Is All You Need"))
        self.assertEqual(article.title, "Attention Is All You Need")

    def test_a_filename_title_in_the_metadata_is_ignored(self):
        article = extract_pdf(b"%PDF", "1706.03762v7.pdf",
                              reader=reader(["# The Real Title\n" + BODY], "1706.03762v7.pdf"))
        self.assertEqual(article.title, "The Real Title")

    def test_without_metadata_the_content_names_it(self):
        article = extract_pdf(b"%PDF", "notes.pdf", reader=reader(["# From The Page\n" + BODY]))
        self.assertEqual(article.title, "From The Page")

    def test_a_nameless_pdf_falls_back_to_its_filename(self):
        article = extract_pdf(b"%PDF", "my_quarterly_report.pdf", reader=reader(["x" * 200 + BODY]))
        self.assertEqual(article.title, "my quarterly report")

    def test_a_scan_says_so_instead_of_filing_an_empty_note(self):
        for pages in [[], [""], ["   \n  "], ["a few words"]]:
            with self.subTest(pages=pages), self.assertRaises(FetchError) as caught:
                extract_pdf(b"%PDF", "scan.pdf", reader=reader(pages))
            self.assertEqual(str(caught.exception), NO_TEXT_LAYER)


class RealLibraryTest(unittest.TestCase):
    """One pass over the actual library, when the optional extra is installed."""

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "pypdf not installed")
    def test_reads_a_real_pdf(self):
        from pypdf import PdfWriter

        import io

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)

        # A blank page has no text layer, which is exactly the scan case.
        with self.assertRaises(FetchError) as caught:
            extract_pdf(buffer.getvalue(), "blank.pdf")
        self.assertEqual(str(caught.exception), NO_TEXT_LAYER)

    @unittest.skipIf(importlib.util.find_spec("pypdf"), "pypdf is installed")
    def test_without_the_extra_it_says_how_to_install_it(self):
        with self.assertRaises(FetchError) as caught:
            extract_pdf(b"%PDF-1.4", "x.pdf")
        self.assertIn("uv sync --extra pdf", str(caught.exception))
        self.assertEqual(str(caught.exception), MISSING_DEPENDENCY)


if __name__ == "__main__":
    unittest.main()


class DownloadTest(unittest.TestCase):
    """Keeping the original is best-effort: it must never cost the capture."""

    def get(self, content, status=200):
        return lambda url, **kwargs: SimpleNamespace(status_code=status, content=content)

    def test_a_real_pdf_comes_back(self):
        self.assertEqual(download("https://x/p.pdf", get=self.get(b"%PDF-1.4 body")), b"%PDF-1.4 body")

    def test_an_error_page_served_as_a_pdf_is_refused(self):
        self.assertIsNone(download("https://x/p.pdf", get=self.get(b"<!DOCTYPE html><html>")))

    def test_an_http_error_is_refused(self):
        self.assertIsNone(download("https://x/p.pdf", get=self.get(b"%PDF-1.4", status=404)))

    def test_oversize_is_refused(self):
        big = b"%PDF-1.4" + b"x" * MAX_FILE_BYTES
        self.assertIsNone(download("https://x/p.pdf", get=self.get(big)))

    def test_a_network_error_returns_none_rather_than_raising(self):
        def boom(url, **kwargs):
            raise ConnectionError("down")

        self.assertIsNone(download("https://x/p.pdf", get=boom))
