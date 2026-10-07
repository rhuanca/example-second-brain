"""The capture pipeline and (task 9) the Telegram wiring.

`handle_url` is the Telegram-independent core: text in, a reply (and maybe a
saved note) out. It never writes a partial note — the vault write is the last
step, after fetch and summarize both succeed.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import functools
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from second_brain.ask import ask as default_ask
from second_brain.config import Settings
from second_brain.fetcher import FetchError, is_thin
from second_brain.pdf import extract_pdf as default_extract
from second_brain.sources import fetch as default_fetch
from second_brain.summarizer import SummarizerError
from second_brain.summarizer import summarize as default_summarize
from second_brain.urls import extract_url
from second_brain.vault import DuplicateNoteError, Vault

logger = logging.getLogger(__name__)

# The Bot API will not hand a bot a file larger than this, so say so rather than
# failing somewhere inside the download.
MAX_PDF_BYTES = 20 * 1024 * 1024
PDF_MIME = "application/pdf"
NOT_A_PDF_MESSAGE = "📄 I can only read PDFs for now — send me a PDF or a link."
TOO_BIG_MESSAGE = (
    f"📄 That file is over {MAX_PDF_BYTES // (1024 * 1024)}MB, which is more than "
    "Telegram will hand me. Send a link to it instead."
)

TOO_THIN_MESSAGE = (
    "⚠️ That page gave back almost no text, so there's nothing worth "
    "summarizing — it may be JavaScript-only, paywalled, or a redirect. "
    "Nothing saved."
)

NO_URL_MESSAGE = (
    "Send me a link (http/https) and I'll summarize it and file it in your "
    "second brain."
)


# What a capture ended as. `ok` says whether a note was written; this says why
# not, which is what the bot's reaction to your message reports.
SAVED, DUPLICATE, FAILED, NOTHING = "saved", "duplicate", "failed", "nothing"


@dataclass
class PipelineResult:
    reply: str
    note_path: Path | None = None
    ok: bool = False
    outcome: str = FAILED


def handle_url(
    text: str | None,
    *,
    vault: Vault,
    settings: Settings,
    fetch=default_fetch,
    summarize=default_summarize,
    today=None,
) -> PipelineResult:
    """Run capture → summarize → save for the URL in `text`.

    `fetch`, `summarize`, and `today` are injectable for testing. Returns a
    PipelineResult whose `reply` is safe to send back to the user in every case.
    """
    today = today or _dt.date.today

    url = extract_url(text)
    if not url:
        return PipelineResult(NO_URL_MESSAGE, outcome=NOTHING)

    existing = vault.find_by_url(url)
    if existing is not None:
        return PipelineResult(
            f"📌 Already in your second brain: {existing.name}", outcome=DUPLICATE
        )

    try:
        article = fetch(url)
    except FetchError as exc:
        return PipelineResult(f"⚠️ Couldn't read that article: {exc}", outcome=FAILED)

    return _capture(
        article, url, vault=vault, settings=settings, summarize=summarize, today=today
    )


def handle_document(
    data: bytes,
    filename: str,
    *,
    vault: Vault,
    settings: Settings,
    extract=default_extract,
    summarize=default_summarize,
    today=None,
) -> PipelineResult:
    """Run capture → summarize → save for an uploaded file.

    The file's identity is the hash of its bytes, so the same PDF sent twice is a
    duplicate however it was named -- the counterpart of a URL for a link.
    """
    today = today or _dt.date.today
    source = f"file:sha256-{hashlib.sha256(data).hexdigest()}"

    existing = vault.find_by_url(source)
    if existing is not None:
        return PipelineResult(
            f"📌 Already in your second brain: {existing.name}", outcome=DUPLICATE
        )

    try:
        article = extract(data, filename)
    except FetchError as exc:
        return PipelineResult(f"⚠️ Couldn't read that file: {exc}", outcome=FAILED)
    article.original = data  # keep the file itself: the text has no diagrams

    return _capture(
        article, source, vault=vault, settings=settings, summarize=summarize, today=today
    )


def _capture(
    article,
    source: str,
    *,
    vault: Vault,
    settings: Settings,
    summarize,
    today,
) -> PipelineResult:
    """Summarize an article and file it. Everything after "we have the text".

    Shared by the two ways text arrives -- a link and an uploaded file -- which
    differ only in how they got it and what identifies the source.
    """
    # The source came back with scraps. Summarizing those produces a confident
    # note about a consent wall or a tracking pixel, which is worse than nothing:
    # it looks like a real note in the vault forever.
    if is_thin(article.text):
        return PipelineResult(TOO_THIN_MESSAGE, outcome=FAILED)

    try:
        summary = summarize(
            article.title,
            article.text,
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
        )
    except SummarizerError as exc:
        return PipelineResult(f"⚠️ Couldn't summarize that article: {exc}", outcome=FAILED)

    summary.tags = _with_source_tag(summary.tags, article.source)

    # Always archive the full canonical source alongside the summary.
    try:
        path = vault.write_note(
            summary,
            source,
            today(),
            archive=article.text,
            kind=article.kind,
            source_type=article.source,
            original=article.original,
        )
    except DuplicateNoteError as exc:
        return PipelineResult(
            f"📌 Already in your second brain: {exc.existing.name}", outcome=DUPLICATE
        )
    except OSError as exc:
        return PipelineResult(f"⚠️ Couldn't save the note: {exc}", outcome=FAILED)

    return PipelineResult(
        _render_reply(summary, path), note_path=path, ok=True, outcome=SAVED
    )


def _render_reply(summary, path: Path) -> str:
    lines = [f"📝 {summary.title}", "", summary.tldr]

    if summary.key_points:
        lines += ["", "Key points:"]
        lines += [f"• {p}" for p in summary.key_points]

    if summary.prototype_ideas:
        lines += ["", "Prototype ideas:"]
        lines += [f"• {i}" for i in summary.prototype_ideas]

    if summary.tags:
        lines += ["", "Tags: " + " ".join(f"#{t}" for t in summary.tags)]

    lines += ["", f"Saved: {path.name}"]
    return "\n".join(lines)


def _with_source_tag(tags: list[str], source: str) -> list[str]:
    """Append the source (article/youtube/medium) as a tag, without duplicating."""
    source = source.strip().lower()
    return tags if source in tags else [*tags, source]


# --- Telegram wiring -------------------------------------------------------

_TYPING_REFRESH_SECONDS = 4  # Telegram's typing indicator lasts ~5s; refresh it.

# A reaction on your own message, as feedback that outlives the typing dots: it
# survives in the chat history, so every link shows what became of it. Telegram
# accepts only the 73 emoji in telegram.constants.ReactionEmoji -- ✅, ❌ and ⚠️
# are not among them, which is why "done" is 💯 rather than a tick.
WORKING = "👀"
REACTIONS = {SAVED: "💯", DUPLICATE: "🤔", FAILED: "😢", NOTHING: None}


async def _react(message, emoji: str | None) -> None:
    """Set (or clear, with None) the reaction on a message. Best-effort.

    Cosmetic like the typing indicator, and swallowed the same way -- but logged,
    because a reaction that silently never arrives is indistinguishable from one
    that was never attempted.
    """
    react = getattr(message, "set_reaction", None)
    if react is None:
        logger.warning("no reaction: %s has no set_reaction", type(message).__name__)
        return
    try:
        await react(emoji)
    except Exception as exc:  # noqa: BLE001 — feedback is cosmetic
        logger.warning("reaction %r failed: %s: %s", emoji, type(exc).__name__, exc)


async def _send_typing(message) -> None:
    """Show the 'typing…' chat action. Best-effort — never fails the request.

    Cosmetic, so a failure is swallowed -- but it is logged, because a silently
    missing indicator is indistinguishable from one that was never sent.
    """
    chat = getattr(message, "chat", None)
    send = getattr(chat, "send_action", None)
    if send is None:
        logger.warning("no typing indicator: %s has no send_action", type(chat).__name__)
        return
    try:
        await send("typing")
    except Exception as exc:  # noqa: BLE001 — feedback is cosmetic
        logger.warning("typing indicator failed: %s: %s", type(exc).__name__, exc)


async def _run_with_typing(message, work):
    """Await `work` while keeping the typing indicator alive; return its result."""
    await _send_typing(message)
    ticker = asyncio.create_task(_typing_loop(message))
    try:
        return await work
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker


async def _typing_loop(message) -> None:
    while True:
        await asyncio.sleep(_TYPING_REFRESH_SECONDS)
        await _send_typing(message)


def is_allowed(user_id: int | None, settings: Settings) -> bool:
    """True only for the single configured user id (the allow-list)."""
    return user_id == settings.telegram_allowed_user_id


def build_application(settings: Settings, vault: Vault):
    """Build a python-telegram-bot Application wired to the capture pipeline."""
    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    # Bind per-source credentials/toggles so the pipeline keeps its fetch(url) shape.
    fetch = functools.partial(
        default_fetch,
        medium_cookie=settings.medium_cookie,
        supadata_api_key=settings.supadata_api_key,
        jina_enabled=settings.jina_enabled,
        jina_api_key=settings.jina_api_key,
    )

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("ask", make_ask_handler(settings, vault)))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            make_handler(settings, vault, fetch=fetch),
        )
    )
    # Every document, not just PDFs: an unanswered upload looks like a dead bot.
    app.add_handler(MessageHandler(filters.Document.ALL, make_document_handler(settings, vault)))
    return app


def make_document_handler(
    settings: Settings,
    vault: Vault,
    *,
    extract=default_extract,
    summarize=default_summarize,
    today=None,
):
    """Create the async handler for uploaded files (PDFs today)."""

    async def handle(update, context):
        user = update.effective_user
        if user is None or not is_allowed(user.id, settings):
            return  # silently ignore anyone who isn't the owner
        message = update.effective_message
        document = getattr(message, "document", None) if message else None
        if document is None:
            return
        if (getattr(document, "mime_type", "") or "") != PDF_MIME:
            await message.reply_text(NOT_A_PDF_MESSAGE)
            return
        if (getattr(document, "file_size", 0) or 0) > MAX_PDF_BYTES:
            await message.reply_text(TOO_BIG_MESSAGE)
            return
        await _react(message, WORKING)

        async def work():
            handle = await document.get_file()
            data = bytes(await handle.download_as_bytearray())
            return await asyncio.to_thread(
                handle_document,
                data,
                getattr(document, "file_name", "") or "",
                vault=vault,
                settings=settings,
                extract=extract,
                summarize=summarize,
                today=today,
            )

        result = await _run_with_typing(message, work())
        await _react(message, REACTIONS[result.outcome])
        await message.reply_text(result.reply)

    return handle


def make_handler(
    settings: Settings,
    vault: Vault,
    *,
    fetch=default_fetch,
    summarize=default_summarize,
    today=None,
):
    """Create the async message handler that enforces the allow-list.

    The capture pipeline does synchronous network + HTTP I/O, so it runs in a
    worker thread to avoid stalling the bot's asyncio event loop.
    """

    async def handle(update, context):
        user = update.effective_user
        if user is None or not is_allowed(user.id, settings):
            return  # silently ignore anyone who isn't the owner
        message = update.effective_message
        if message is None:
            return
        await _react(message, WORKING)
        result = await _run_with_typing(
            message,
            asyncio.to_thread(
                handle_url,
                message.text,
                vault=vault,
                settings=settings,
                fetch=fetch,
                summarize=summarize,
                today=today,
            ),
        )
        await _react(message, REACTIONS[result.outcome])
        await message.reply_text(result.reply)

    return handle


ASK_USAGE = (
    "Ask about your saved notes, e.g. /ask what have I saved about agent memory?"
)


def make_ask_handler(settings: Settings, vault: Vault, *, run_ask=default_ask):
    """Create the async /ask command handler (allow-list enforced)."""

    async def handle(update, context):
        user = update.effective_user
        if user is None or not is_allowed(user.id, settings):
            return
        message = update.effective_message
        if message is None:
            return
        question = _strip_command(message.text)
        if not question:
            await message.reply_text(ASK_USAGE)
            return
        await _react(message, WORKING)
        reply = await _run_with_typing(
            message,
            asyncio.to_thread(run_ask, question, vault=vault, settings=settings),
        )
        # The answer is its own feedback, so the mark comes off rather than
        # claiming an outcome /ask does not have.
        await _react(message, None)
        await message.reply_text(reply)

    return handle


def _strip_command(text: str | None) -> str:
    """Return the argument text after a leading /command, else the text itself."""
    if not text:
        return ""
    stripped = text.strip()
    if stripped.startswith("/"):
        parts = stripped.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""
    return stripped
