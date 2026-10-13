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

# Big enough for a long paper with figures, small enough that one bad link cannot
# drop a few hundred MB into a vault that syncs.
MAX_FILE_BYTES = 25 * 1024 * 1024
PDF_MAGIC = b"%PDF"

MISSING_DEPENDENCY = (
    "Reading PDFs needs the pdf extra. Install it on the server with: "
    "uv sync --extra pdf"
)

NO_TEXT_LAYER = (
    "That PDF has no text layer — it looks like a scan, so there is nothing to read."
)
NO_PAGE_IMAGES = "That PDF has neither text nor page images I can read."

# What Claude's vision is charged on: a longer edge than this costs more tokens
# for no gain, because the API resizes to roughly this anyway.
MAX_EDGE = 1568
JPEG_QUALITY = 80


def extract_pdf(data: bytes, filename: str = "", *, reader=None, ocr=None) -> Article:
    """The text of a PDF, page by page. Raises FetchError if there is none.

    A scan has no text to find. When `ocr` is supplied it is asked to read the
    page images instead — the decision to spend money on that belongs to the
    capture pipeline, not to a parser, so this only calls what it was handed.
    """
    pages, meta_title = (reader or _default_reader)(data)
    text = "\n\n".join(page.strip() for page in pages if page and page.strip()).strip()
    if not text or is_thin(text):
        if ocr is None:
            raise FetchError(NO_TEXT_LAYER)
        text = ocr(data).strip()
        if not text or is_thin(text):
            raise FetchError(NO_TEXT_LAYER)
    return Article(
        title=_title(meta_title, text, filename),
        text=text,
        source="pdf",
        kind="pdf",
    )


# Longer than this and the line is a sentence, not a title. Papers open with
# licence boilerplate often enough -- the arXiv transformer paper leads with
# Google's reproduction notice -- that the first line cannot simply be trusted.
TITLE_MAX = 110
TITLE_SEARCH_LINES = 30


def title_from(text: str, fallback: str = "") -> str:
    """A title from the content: the first line short enough to be one."""
    for line in (text or "").splitlines()[:TITLE_SEARCH_LINES]:
        candidate = line.strip().lstrip("#").strip()
        if candidate and len(candidate) <= TITLE_MAX:
            return candidate
    return fallback


def looks_like_a_filename(title: str) -> bool:
    """True for titles that are really file names, e.g. `1706.03762v7.pdf`.

    Also the most reliable sign that a link *was* a PDF: arXiv and many hosts
    serve one from an extensionless URL, and the reader then titles it with the
    file it actually downloaded.
    """
    return bool(title) and title.strip().lower().endswith(".pdf")


def _title(meta_title: str | None, text: str, filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    meta = (meta_title or "").strip()
    if meta and not looks_like_a_filename(meta):
        return meta
    return title_from(text, stem or filename or "Untitled PDF")


def page_images(data: bytes, *, max_pages: int, reader=None) -> list[bytes]:
    """One JPEG per page, for a PDF whose pages are pictures.

    A scan is one image per page; when a page holds several, the largest is the
    page and the rest are decoration, so that is what goes to the model. Pages
    are downscaled before they are sent: the bill is per pixel, and 4K costs more
    than it is worth.
    """
    images = (reader or _default_page_images)(data, max_pages=max_pages, max_edge=MAX_EDGE)
    if not images:
        raise FetchError(NO_PAGE_IMAGES)
    return images


def _default_page_images(data: bytes, *, max_pages: int, max_edge: int) -> list[bytes]:
    try:
        from PIL import Image
        from pypdf import PdfReader
    except ImportError as exc:
        raise FetchError(MISSING_DEPENDENCY) from exc

    import io

    out: list[bytes] = []
    document = PdfReader(io.BytesIO(data))
    for page in document.pages[:max_pages]:
        biggest = None
        for embedded in page.images:
            try:
                image = Image.open(io.BytesIO(embedded.data))
            except Exception:  # noqa: BLE001 -- an image we cannot open is not the page
                continue
            if biggest is None or image.width * image.height > biggest.width * biggest.height:
                biggest = image
        if biggest is None:
            continue
        out.append(_as_jpeg(biggest, max_edge))
    return out


def _as_jpeg(image, max_edge: int) -> bytes:
    import io

    if max(image.size) > max_edge:
        scale = max_edge / max(image.size)
        image = image.resize((max(int(image.width * scale), 1), max(int(image.height * scale), 1)))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def download(url: str, *, get=None) -> bytes | None:
    """The PDF behind a link, or None -- never an exception.

    The reader hands back Markdown, not the file, so keeping the original needs
    this one extra fetch. Losing it costs the diagrams, not the capture, so every
    failure here is answered with None and the note is saved from the text as
    usual. A response is only accepted when it really starts with %PDF: an error
    page served with the wrong content type is the common case.
    """
    try:
        resp = (get or _default_get)(url, timeout=60, stream=False)
        status = getattr(resp, "status_code", 200)
        data = bytes(getattr(resp, "content", b"") or b"")
    except Exception:  # noqa: BLE001 -- the capture does not depend on this
        return None
    if status >= 400 or not data.startswith(PDF_MAGIC) or len(data) > MAX_FILE_BYTES:
        return None
    return data


def _default_get(url: str, **kwargs):
    import requests

    return requests.get(url, **kwargs)


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
