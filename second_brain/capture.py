"""What a set of uploaded files means together.

A talk arrives as two files. Separately they make two poor notes: a deck of
headings with no argument, and a wall of speech with no structure. Together they
are the talk — the transcript says what was argued, the slides say how it was
organised — so they become one note.

Kept out of `bot.py` because none of it is about Telegram: the rules are the
interesting part, and they are worth testing without an update object.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PDF_MIME = "application/pdf"
TEXT_MIMES = {"text/plain", "text/markdown", "text/x-markdown"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}

SLIDES_HEADING = "## Slides"
TRANSCRIPT_HEADING = "## Transcript"


@dataclass(frozen=True)
class Upload:
    """One uploaded file, as the bot hands it over."""

    filename: str
    mime: str
    data: bytes

    def is_pdf(self) -> bool:
        return self.mime == PDF_MIME or Path(self.filename).suffix.lower() == ".pdf"

    def is_text(self) -> bool:
        # Telegram sends a .md file as application/octet-stream often enough that
        # the name has to be allowed to settle it.
        return (
            self.mime in TEXT_MIMES
            or Path(self.filename).suffix.lower() in TEXT_SUFFIXES
        )


@dataclass(frozen=True)
class Talk:
    slides: Upload
    transcript: Upload


def classify(files: list[Upload]) -> Talk | None:
    """A talk, or None when these files are not one.

    Exactly one PDF and exactly one text file. Two PDFs are two documents, three
    files are an album of something else, and a lone PDF is the path that already
    works — in every one of those cases the caller does what it did before.
    """
    if len(files) != 2:
        return None
    slides = [f for f in files if f.is_pdf()]
    transcripts = [f for f in files if f.is_text() and not f.is_pdf()]
    if len(slides) != 1 or len(transcripts) != 1:
        return None
    return Talk(slides=slides[0], transcript=transcripts[0])


def read_transcript(data: bytes, filename: str = "") -> str:
    """A transcript file as text.

    Deliberately dull: these are text files, and pretending otherwise invites a
    parser nobody needs. Undecodable bytes are replaced rather than raising — a
    transcript with a mangled character is still a transcript.
    """
    text = data.decode("utf-8", "replace").lstrip("﻿")
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()


def merge(slides_text: str, transcript_text: str, *, slides_name: str = "", transcript_name: str = "") -> str:
    """The archive for a talk: what was shown, then what was said.

    Each section says which file it came from, so a reader -- and the summarizer
    reading it next -- can tell a slide heading from a sentence someone spoke.
    """
    parts = []
    if slides_text.strip():
        parts.append(
            f"{SLIDES_HEADING}\n\n"
            + (f"*From {slides_name}.*\n\n" if slides_name else "")
            + slides_text.strip()
        )
    if transcript_text.strip():
        parts.append(
            f"{TRANSCRIPT_HEADING}\n\n"
            + (f"*From {transcript_name}.*\n\n" if transcript_name else "")
            + transcript_text.strip()
        )
    return "\n\n".join(parts).strip()
