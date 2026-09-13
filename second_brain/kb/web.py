"""The browse UI: topics -> note cards -> note -> full source.

Server-rendered HTML, no JavaScript. Every note id in a URL is resolved through
`Library.card()`, so an unknown or crafted id is a 404 and never a file read.
Captured content is always escaped (Jinja autoescape) and archives are shown as
plain text, never rendered as Markdown/HTML, because that text came from
arbitrary web pages.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

import frontmatter
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.types import ASGIApp, Receive, Scope, Send

from second_brain.kb.auth import is_mcp_path
from second_brain.kb.notes import Card
from second_brain.kb.retrieval import Library

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))

SOURCE_TYPES = ("youtube", "medium", "pdf", "article")
UNTOPICED = "none"
SEARCH_LIMIT = 25
RECENT = 8
_MONTH = re.compile(r"^\d{4}-\d{2}")

SECURITY_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
        b"form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-robots-tag", b"noindex, nofollow"),
]


def build_router(library: Library) -> APIRouter:
    router = APIRouter()

    def render(request: Request, name: str, status_code: int = 200, **context):
        return TEMPLATES.TemplateResponse(
            request, name, {"q": "", **context}, status_code=status_code
        )

    def not_found(request: Request):
        return render(request, "not_found.html", status_code=404)

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request):
        library.refresh_if_stale()
        cards = library.cards()
        topics = [
            {"topic": topic, "count": len(library.notes_in_topic(topic.id))}
            for topic in library.topics()
        ]
        return render(
            request,
            "index.html",
            topics=topics,
            untopiced=sum(1 for c in cards if not library.topics_for(c)),
            total=len(cards),
            recent=[_view(c, library) for c in sorted(cards, key=_newest_first)[:RECENT]],
        )

    @router.get("/notes", response_class=HTMLResponse)
    def notes(
        request: Request,
        topic: str | None = None,
        source: str | None = None,
        month: str | None = None,
    ):
        library.refresh_if_stale()
        cards = library.cards()
        heading, description = "All notes", ""

        if topic == UNTOPICED:
            cards = [c for c in cards if not library.topics_for(c)]
            heading = "Not in any topic"
        elif topic:
            match = next((t for t in library.topics() if t.id == topic), None)
            cards = library.notes_in_topic(topic)
            if match is None and not cards:
                return not_found(request)
            heading = match.name if match else topic
            description = match.description if match else ""

        filters = {"topic": topic, "source": source, "month": month}
        facets = {
            "source": _facet(cards, _source_type, "source", filters),
            "month": _facet(cards, _month, "month", filters, newest_first=True),
        }
        if source:
            cards = [c for c in cards if _source_type(c) == source]
        if month:
            cards = [c for c in cards if _month(c) == month]

        return render(
            request,
            "cards.html",
            heading=heading,
            description=description,
            facets=facets,
            cards=[_view(c, library) for c in sorted(cards, key=_newest_first)],
        )

    @router.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = "", topic: str | None = None):
        library.refresh_if_stale()
        hits = library.search(q, topic=topic or None, limit=SEARCH_LIMIT) if q.strip() else []
        return render(
            request,
            "cards.html",
            q=q,
            heading=f"Results for “{q}”" if q.strip() else "Search",
            description="" if q.strip() else "Type a question or a few words above.",
            facets={},
            cards=[_view(hit.card, library) for hit in hits],
        )

    @router.get("/notes/{note_id}", response_class=HTMLResponse)
    def note(request: Request, note_id: str):
        library.refresh_if_stale()
        card = library.card(note_id)
        if card is None:
            return not_found(request)
        return render(
            request,
            "note.html",
            note=_view(card, library),
            has_source=library.archive_text(card.note_id) is not None,
        )

    @router.get("/notes/{note_id}/source", response_class=HTMLResponse)
    def source(request: Request, note_id: str):
        library.refresh_if_stale()
        card = library.card(note_id)
        text = library.archive_text(note_id) if card else None
        if card is None or text is None:
            return not_found(request)
        return render(request, "source.html", note=_view(card, library), text=_body(text))

    return router


class SecurityHeadersMiddleware:
    """Locks the browse pages down: no scripts, no framing, no referrer leaks."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_mcp_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + SECURITY_HEADERS
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _view(card: Card, library: Library) -> dict:
    names = {t.id: t.name for t in library.topics()}
    return {
        "id": card.note_id,
        "title": card.title,
        "tldr": card.tldr,
        "date": card.date,
        "source_url": card.source if _is_web_url(card.source) else "",
        "source_label": card.source,
        "source_type": _source_type(card),
        "topics": [{"id": t, "name": names.get(t, t)} for t in library.topics_for(card)],
        "key_points": card.key_points,
        "prototype_ideas": card.prototype_ideas,
    }


def _facet(cards, key, param, filters, *, newest_first=False) -> list[dict]:
    counts = Counter(value for c in cards if (value := key(c)))
    values = sorted(counts, reverse=newest_first)
    options = [
        {
            "label": "All",
            "count": len(cards),
            "url": _url({**filters, param: None}),
            "active": not filters.get(param),
        }
    ]
    options += [
        {
            "label": value,
            "count": counts[value],
            "url": _url({**filters, param: value}),
            "active": filters.get(param) == value,
        }
        for value in values
    ]
    return options if len(values) > 1 or filters.get(param) else []


def _url(params: dict) -> str:
    query = urlencode({k: v for k, v in params.items() if v})
    return f"/notes?{query}" if query else "/notes"


def _source_type(card: Card) -> str:
    return next((t for t in SOURCE_TYPES if t in card.tags), "article")


def _month(card: Card) -> str:
    match = _MONTH.match(card.date or "")
    return match.group(0) if match else ""


def _newest_first(card: Card) -> tuple:
    # Undated notes sink to the bottom; ties break alphabetically.
    return (-_date_key(card.date), card.title.lower())


def _date_key(date: str) -> int:
    digits = re.sub(r"\D", "", date or "")[:8]
    return int(digits) if len(digits) == 8 else 0


def _is_web_url(value: str) -> bool:
    return value.startswith(("https://", "http://"))


def _body(archive: str) -> str:
    """The archive without its YAML frontmatter, which is bookkeeping, not content."""
    try:
        return frontmatter.loads(archive).content
    except Exception:  # noqa: BLE001 -- show the raw text rather than nothing
        return archive
