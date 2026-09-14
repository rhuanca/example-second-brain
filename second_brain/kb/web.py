"""The browse UI: topic overview -> note cards -> note -> full source.

Server-rendered HTML and SVG; the only script is the chat's own `static/chat.js`
(no inline scripts, no third-party code). Every note id in a URL is resolved through
`Library.card()`, so an unknown or crafted id is a 404 and never a file read.
Captured content is always escaped (Jinja autoescape) and archives are shown as
plain text, never rendered as Markdown/HTML, because that text came from
arbitrary web pages. Chart geometry lives in `visuals.py`; templates only draw.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

import frontmatter
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.types import ASGIApp, Receive, Scope, Send

from second_brain.kb.auth import is_mcp_path
from second_brain.kb.chat import ChatError, gather_notes, parse_turns, stream_answer
from second_brain.kb.config import KbSettings
from second_brain.kb.notes import Card
from second_brain.kb.retrieval import Library
from second_brain.kb.visuals import (
    OTHER,
    SOURCE_ORDER,
    Series,
    label_positions,
    month_label,
    month_of,
    source_slots,
    squarified_treemap,
    timeline_chart,
    topic_overlaps,
    topic_slots,
    youtube_thumbnail,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))

UNTOPICED = "none"
SEARCH_LIMIT = 24
RECENT = 8
NAV = [
    ("/", "Library"),
    ("/notes", "Notes"),
    ("/map", "Map"),
    ("/timeline", "Timeline"),
    ("/chat", "Chat"),
]
MAX_CHAT_BODY = 200_000  # bytes; 20 turns × 4,000 chars fits with room to spare

# Close to the panel's real width on a desktop, so 13px labels render near 13px.
TREEMAP_W, TREEMAP_H = 1050, 300
MAP_W, MAP_H = 1050, 620
TIMELINE_W, TIMELINE_H = 1050, 320
OTHER_KEY = "__other"
# A scatter only keeps colours distinguishable for a few series at once, so the
# map colours every topic only when there are this many or fewer; otherwise it
# highlights one topic at a time.
MAP_COLOUR_ALL_MAX = 3
# Rough width of a 13px label character, to decide whether a name fits its tile.
_CHAR_W = 7.2

SECURITY_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'none'; style-src 'unsafe-inline'; "
        b"script-src 'self'; connect-src 'self'; "
        b"img-src 'self' https://i.ytimg.com; "
        b"form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-robots-tag", b"noindex, nofollow"),
]


def build_router(
    library: Library, settings: KbSettings | None = None, *, chat_client=None
) -> APIRouter:
    router = APIRouter()

    def render(request: Request, name: str, status_code: int = 200, **context):
        return TEMPLATES.TemplateResponse(
            request,
            name,
            {"q": "", "nav_items": NAV, "active": "", **context},
            status_code=status_code,
        )

    def not_found(request: Request):
        return render(request, "not_found.html", status_code=404)

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request):
        library.refresh_if_stale()
        cards = library.cards()
        topics = library.topics()
        slots = topic_slots(topics)
        counts = {t.id: len(library.notes_in_topic(t.id)) for t in topics}
        biggest = max(counts.values(), default=0)
        by_id = {t.id: _topic_view(t.id, library, slots) for t in topics}

        topic_rows = [
            {
                **by_id[t.id],
                "description": t.description,
                "count": counts[t.id],
                "share": round(100 * counts[t.id] / biggest) if biggest else 0,
            }
            for t in topics
        ]
        return render(
            request,
            "index.html",
            active="/",
            total=len(cards),
            topics=topic_rows,
            treemap=_treemap(topic_rows),
            together=[
                (by_id[a], by_id[b], n)
                for a, b, n in topic_overlaps(cards, library.topics_for)
                if a in by_id and b in by_id
            ],
            untopiced=sum(1 for c in cards if not library.topics_for(c)),
            activity=timeline_chart(
                cards,
                [Series("all", "Notes saved", 1)],
                lambda card: "all",
                width=520,
                height=200,
            ),
            recent=[_view(c, library, slots) for c in sorted(cards, key=_newest_first)[:RECENT]],
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
        slots = topic_slots(library.topics())
        heading, description, topic_slot = "All notes", "", None

        if topic == UNTOPICED:
            cards = [c for c in cards if not library.topics_for(c)]
            heading = "Not in any topic"
            topic_slot = OTHER
        elif topic:
            match = next((t for t in library.topics() if t.id == topic), None)
            cards = library.notes_in_topic(topic)
            if match is None and not cards:
                return not_found(request)
            heading = match.name if match else topic
            description = match.description if match else ""
            topic_slot = slots.get(topic, OTHER)

        filters = {"topic": topic, "source": source, "month": month}
        facets = {
            "source": _facet(cards, _source_type, "source", filters),
            "month": _facet(
                cards, lambda c: month_of(c) or "", "month", filters,
                newest_first=True, label=lambda m: month_label(m, with_year=True),
            ),
        }
        if source:
            cards = [c for c in cards if _source_type(c) == source]
        if month:
            cards = [c for c in cards if month_of(c) == month]

        return render(
            request,
            "cards.html",
            active="/notes",
            heading=heading,
            description=description,
            topic_slot=topic_slot,
            facets=facets,
            empty="No notes match these filters.",
            cards=[_view(c, library, slots) for c in sorted(cards, key=_newest_first)],
        )

    @router.get("/map", response_class=HTMLResponse)
    def map_page(request: Request, topic: str | None = None):
        library.refresh_if_stale()
        topics = library.topics()
        slots = topic_slots(topics)
        cards = {c.note_id: c for c in library.cards()}
        known = {t.id for t in topics}
        if topic and topic not in known:
            return not_found(request)
        colour_all = not topic and 2 <= len(topics) <= MAP_COLOUR_ALL_MAX

        dots, groups = [], {t.id: [] for t in topics}
        for point in library.map_points(MAP_W, MAP_H):
            card = cards.get(point.note_id)
            if card is None:
                continue
            ids = library.topics_for(card)
            for tid in ids:
                if tid in groups:
                    groups[tid].append(point)
            highlighted = bool(topic) and topic in ids
            if highlighted:
                slot = slots[topic]
            elif colour_all:
                slot = slots.get(ids[0], OTHER) if ids else OTHER
            else:
                slot = None
            dots.append(
                {
                    "id": card.note_id,
                    "title": card.title,
                    "x": point.x,
                    "y": point.y,
                    "slot": slot,
                    "dim": bool(topic) and not highlighted,
                }
            )
        # Highlighted dots are drawn last so they sit on top.
        dots.sort(key=lambda d: (d["slot"] is not None, not d["dim"]))

        wanted = {topic: groups[topic]} if topic else groups
        labels = [
            {"name": next(t.name for t in topics if t.id == tid), "x": x, "y": y}
            for tid, (x, y) in label_positions(wanted).items()
        ]
        return render(
            request,
            "map.html",
            active="/map",
            width=MAP_W,
            height=MAP_H,
            dots=dots,
            labels=labels,
            note_count=len(cards),
            topic=next((_topic_view(t.id, library, slots) | {"count": len(groups[t.id])}
                        for t in topics if t.id == topic), None),
            legend=[_topic_view(t.id, library, slots) for t in topics] if colour_all else [],
            pickers=[
                {**_topic_view(t.id, library, slots), "active": t.id == topic,
                 "url": f"/map?{urlencode({'topic': t.id})}"}
                for t in topics
            ],
        )

    @router.get("/timeline", response_class=HTMLResponse)
    def timeline(request: Request, by: str = "topic"):
        library.refresh_if_stale()
        by = "source" if by == "source" else "topic"
        cards = library.cards()
        topics = library.topics()
        slots = topic_slots(topics)

        if by == "source":
            series = [
                Series(source, source.capitalize(), slot, {"source": source})
                for source, slot in source_slots().items()
            ]
            series_of = _source_type
        else:
            series = [
                Series(t.id, t.name, slots[t.id], {"topic": t.id})
                for t in topics
                if slots[t.id] != OTHER
            ]
            series.append(Series(OTHER_KEY, "Other topics / none" if topics else "No topic", OTHER))

            def series_of(card: Card) -> str:
                ids = library.topics_for(card)
                return ids[0] if ids and slots.get(ids[0], OTHER) != OTHER else OTHER_KEY

        chart = timeline_chart(cards, series, series_of, width=TIMELINE_W, height=TIMELINE_H)
        return render(
            request,
            "timeline.html",
            active="/timeline",
            by=by,
            chart=chart,
            total=len(cards),
            undated=sum(1 for c in cards if not month_of(c)),
        )

    @router.get("/chat", response_class=HTMLResponse)
    def chat_page(request: Request):
        library.refresh_if_stale()
        topics = library.topics()
        suggestions = ["What have I saved recently that's worth revisiting?"]
        if topics:
            suggestions.append(f"Summarise what my notes say about {topics[0].name.lower()}.")
        suggestions.append("Which prototype ideas come up in more than one note?")
        return render(
            request,
            "chat.html",
            active="/chat",
            enabled=bool(settings and settings.anthropic_api_key),
            suggestions=suggestions,
        )

    @router.post("/api/chat")
    async def chat_api(request: Request):
        if not (request.headers.get("content-type") or "").startswith("application/json"):
            return JSONResponse({"error": "Send JSON."}, status_code=415)
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        if origin and origin not in {f"https://{host}", f"http://{host}"}:
            # The browser attaches the Access cookie to cross-site requests too;
            # only our own pages may start a chat.
            return JSONResponse({"error": "Cross-origin requests are not allowed."}, status_code=403)
        length = request.headers.get("content-length") or "0"
        if not length.isdigit() or int(length) > MAX_CHAT_BODY:
            return JSONResponse({"error": "That conversation is too long."}, status_code=413)
        if not (settings and settings.anthropic_api_key):
            return JSONResponse(
                {"error": "Chat needs ANTHROPIC_API_KEY to be set for the knowledge base."},
                status_code=503,
            )
        try:
            turns = parse_turns(await request.json())
        except (ChatError, ValueError) as exc:
            message = str(exc) if isinstance(exc, ChatError) else "Send valid JSON."
            return JSONResponse({"error": message}, status_code=400)

        def events():
            library.refresh_if_stale()
            slots = topic_slots(library.topics())
            cards = gather_notes(library, turns)
            for event in stream_answer(
                turns,
                cards,
                library=library,
                settings=settings,
                describe=lambda card: _chat_card(card, library, slots),
                client=chat_client,
            ):
                yield json.dumps(event) + "\n"

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @router.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = "", topic: str | None = None):
        library.refresh_if_stale()
        slots = topic_slots(library.topics())
        hits = library.search(q, topic=topic or None, limit=SEARCH_LIMIT) if q.strip() else []
        return render(
            request,
            "cards.html",
            q=q,
            heading=f"Results for “{q}”" if q.strip() else "Search",
            description="" if q.strip() else "Type a question or a few words above.",
            topic_slot=None,
            facets={},
            empty="Nothing matched." if q.strip() else "Results appear here.",
            cards=[_view(hit.card, library, slots) for hit in hits],
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
            note=_view(card, library, topic_slots(library.topics())),
            has_source=library.archive_text(card.note_id) is not None,
        )

    @router.get("/notes/{note_id}/source", response_class=HTMLResponse)
    def source(request: Request, note_id: str):
        library.refresh_if_stale()
        card = library.card(note_id)
        text = library.archive_text(note_id) if card else None
        if card is None or text is None:
            return not_found(request)
        return render(
            request,
            "source.html",
            note=_view(card, library, topic_slots(library.topics())),
            text=_body(text),
        )

    return router


class SecurityHeadersMiddleware:
    """Locks the browse pages down: no framing, no referrer leaks, only our own assets."""

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


def _topic_view(topic_id: str, library: Library, slots: dict[str, int]) -> dict:
    names = {t.id: t.name for t in library.topics()}
    return {"id": topic_id, "name": names.get(topic_id, topic_id), "slot": slots.get(topic_id, OTHER)}


def _view(card: Card, library: Library, slots: dict[str, int]) -> dict:
    topics = [_topic_view(t, library, slots) for t in library.topics_for(card)]
    return {
        "id": card.note_id,
        "title": card.title,
        "tldr": card.tldr,
        "date": card.date,
        "source_url": card.source if _is_web_url(card.source) else "",
        "source_label": card.source,
        "source_type": _source_type(card),
        "thumbnail": youtube_thumbnail(card.source),
        "slot": topics[0]["slot"] if topics else OTHER,
        "topics": topics,
        "key_points": card.key_points,
        "prototype_ideas": card.prototype_ideas,
    }


def _chat_card(card: Card, library: Library, slots: dict[str, int]) -> dict:
    """The small card shown under a chat answer (no body text)."""
    view = _view(card, library, slots)
    return {key: view[key] for key in ("id", "title", "date", "thumbnail", "slot", "source_type")}


def _treemap(topic_rows: list[dict]) -> dict:
    by_id = {row["id"]: row for row in topic_rows}
    tiles = []
    for rect in squarified_treemap(
        [(row["id"], row["count"]) for row in topic_rows], TREEMAP_W, TREEMAP_H
    ):
        row = by_id[rect.key]
        # 2px surface gap between neighbouring tiles.
        x, y, w, h = rect.x + 1, rect.y + 1, max(rect.w - 2, 0), max(rect.h - 2, 0)
        fits = w >= 56 and h >= 28
        label = _fit(row["name"], w - 20) if fits else ""
        tiles.append(
            {
                **row,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "label": label,
                "show_count": bool(label) and h >= 48,
            }
        )
    return {"width": TREEMAP_W, "height": TREEMAP_H, "tiles": tiles}


def _fit(text: str, width: float) -> str:
    """Shorten a label to fit `width` (never clip mid-glyph); the full name is in the tooltip."""
    room = int(width // _CHAR_W)
    if room < 4:
        return ""
    return text if len(text) <= room else text[: room - 1].rstrip() + "…"


def _facet(cards, key, param, filters, *, newest_first=False, label=str) -> list[dict]:
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
            "label": label(value),
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
    return next((t for t in SOURCE_ORDER if t in card.tags), "article")


def _newest_first(card: Card) -> tuple:
    # Undated notes sink to the bottom; ties break alphabetically.
    return (-_date_key(card.date), card.title.lower())


def _date_key(date: str) -> int:
    digits = "".join(ch for ch in (date or "") if ch.isdigit())[:8]
    return int(digits) if len(digits) == 8 else 0


def _is_web_url(value: str) -> bool:
    return value.startswith(("https://", "http://"))


def _body(archive: str) -> str:
    """The archive without its YAML frontmatter, which is bookkeeping, not content."""
    try:
        return frontmatter.loads(archive).content
    except Exception:  # noqa: BLE001 -- show the raw text rather than nothing
        return archive
