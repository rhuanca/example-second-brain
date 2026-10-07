"""PDF support: the text layer of a PDF, as an `Article` like any other source.

A PDF is just another thing to throw at the bot (`specs/design-canonical-archive.md`),
so this returns the same `Article` the web and YouTube adapters do and the rest of
the pipeline -- summarize, archive, file -- does not care where the text came from.

Text layer only. A scanned PDF is a stack of images and gives nothing back; that
says so plainly rather than filing a note about an empty page. OCR is out of scope.
"""

from __future__ import annotations

from pathlib import Path

from second_brain.fetcher import Article, FetchError, is_thin

MISSING_DEPENDENCY = (
    "Reading PDFs needs the pdf extra. Install it on the server with: "
    "uv sync --extra pdf"
)

NO_TEXT_LAYER = (
    "That PDF has no text layer — it looks like a scan, so there is nothing to read."
)


def extract_pdf(data: bytes, filename: str = "", *, reader=None) -> Article:
    """The text of a PDF, page by page. Raises FetchError if there is none."""
    pages, meta_title = (reader or _default_reader)(data)
    text = "\n\n".join(page.strip() for page in pages if page and page.strip()).strip()
    if not text or is_thin(text):
        raise FetchError(NO_TEXT_LAYER)
    return Article(
        title=_title(meta_title, text, filename),
        text=text,
        source="pdf",
        kind="pdf",
    )


def title_from(text: str, fallback: str = "") -> str:
    """A title from the content: its first heading, else its first real line."""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            return line.lstrip("#").strip() or fallback
        # A first line that is already a sentence makes a poor title.
        return line if len(line) <= 120 else fallback
    return fallback


def looks_like_a_filename(title: str) -> bool:
    """True for titles that are really file names, e.g. `1706.03762v7.pdf`."""
    return bool(title) and title.strip().lower().endswith(".pdf")


def _title(meta_title: str | None, text: str, filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    meta = (meta_title or "").strip()
    if meta and not looks_like_a_filename(meta):
        return meta
    return title_from(text, stem or filename or "Untitled PDF")


def _default_reader(data: bytes) -> tuple[list[str], str | None]:
    """Page texts and the document title, via pypdf (an optional extra)."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise FetchError(MISSING_DEPENDENCY) from exc

    import io

    try:
        document = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in document.pages]
    except Exception as exc:  # noqa: BLE001 -- a corrupt file is a user error, not a crash
        raise FetchError(f"That PDF could not be read: {exc}") from exc
    title = (document.metadata or {}).get("/Title") if document.metadata else None
    return pages, str(title) if title else None
