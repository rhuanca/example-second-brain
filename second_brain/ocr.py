"""Read a scanned PDF by showing its pages to Claude.

`pypdf` can only find text that is already there. A document captured as pictures
— a screenshot export, a photographed page — has none, and used to be refused.
This reads it instead.

One request per page, deliberately: a long document cannot then be cut off
mid-transcript, a page that fails costs only that page, and the context stays
small enough that the model transcribes rather than summarises.
"""

from __future__ import annotations

import base64
import logging
import os

from second_brain.fetcher import FetchError

logger = logging.getLogger(__name__)

# Transcription is reading, not reasoning. The cheap model is the right one, and
# the price difference is the whole cost story for this feature.
DEFAULT_OCR_MODEL = "claude-haiku-4-5-20251001"

# A ceiling so that sending a 400-page scan can never quietly run up a bill.
MAX_OCR_PAGES = 20
MAX_TOKENS_PER_PAGE = 4000

PROMPT = (
    "Transcribe this page as Markdown: its text, headings, lists and tables, in "
    "reading order. Describe a figure or chart in one short line in square "
    "brackets, e.g. [Figure: bar chart of monthly revenue]. Output only the "
    "page's content — no preamble, no commentary, no notes about image quality. "
    "If the page is blank, output nothing."
)


def ocr_model() -> str:
    return (os.environ.get("OCR_MODEL") or "").strip() or DEFAULT_OCR_MODEL


def transcribe(images: list[bytes], *, model: str | None = None, api_key=None, client=None) -> str:
    """The text of each page image, joined with page rules.

    A page that fails is marked rather than dropped: a transcript with a hole in
    it that says so is honest, and one that silently skips a page is not.
    """
    if not images:
        raise FetchError("There are no page images to read.")
    if len(images) > MAX_OCR_PAGES:
        raise FetchError(
            f"That PDF is {len(images)} pages. I read up to {MAX_OCR_PAGES} "
            "automatically — anything longer needs to be split first."
        )

    client = client or _build_client(api_key)
    model = model or ocr_model()
    logger.info("ocr: reading %d page(s) with %s", len(images), model)

    pages = []
    for number, image in enumerate(images, start=1):
        pages.append(f"--- page {number} ---")
        try:
            pages.append(_read_page(client, model, image))
        except Exception as exc:  # noqa: BLE001 -- one bad page must not lose the rest
            logger.warning("ocr: page %d failed: %s: %s", number, type(exc).__name__, exc)
            pages[-1] = f"--- page {number} (unreadable) ---"
    return "\n\n".join(part for part in pages if part).strip()


def _read_page(client, model: str, image: bytes) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS_PER_PAGE,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(image).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    return "\n".join(
        getattr(block, "text", "")
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text"
    ).strip()


def _build_client(api_key):
    if not api_key:
        raise FetchError("Reading a scanned PDF needs ANTHROPIC_API_KEY.")
    import anthropic

    return anthropic.Anthropic(api_key=api_key)
