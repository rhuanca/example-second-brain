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
from dataclasses import dataclass
from pathlib import Path

from second_brain.ask import ask as default_ask
from second_brain.config import Settings
from second_brain.fetcher import FetchError
from second_brain.sources import fetch as default_fetch
from second_brain.summarizer import SummarizerError
from second_brain.summarizer import summarize as default_summarize
from second_brain.urls import extract_url
from second_brain.vault import DuplicateNoteError, Vault

NO_URL_MESSAGE = (
    "Send me a link (http/https) and I'll summarize it and file it in your "
    "second brain."
)

# Sources whose raw text is costly/unreliable to re-fetch — keep the transcript.
_STORE_RAW_SOURCES = {"youtube", "medium"}


@dataclass
class PipelineResult:
    reply: str
    note_path: Path | None = None
    ok: bool = False


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
        return PipelineResult(NO_URL_MESSAGE)

    existing = vault.find_by_url(url)
    if existing is not None:
        return PipelineResult(f"📌 Already in your second brain: {existing.name}")

    try:
        article = fetch(url)
    except FetchError as exc:
        return PipelineResult(f"⚠️ Couldn't read that article: {exc}")

    try:
        summary = summarize(
            article.title,
            article.text,
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
        )
    except SummarizerError as exc:
        return PipelineResult(f"⚠️ Couldn't summarize that article: {exc}")

    summary.tags = _with_source_tag(summary.tags, article.source)

    # Preserve the raw text for sources that are costly/unreliable to re-fetch.
    store_raw = article.source in _STORE_RAW_SOURCES
    try:
        path = vault.write_note(
            summary,
            url,
            today(),
            transcript=article.text if store_raw else None,
            source_type=article.source,
        )
    except DuplicateNoteError as exc:
        return PipelineResult(
            f"📌 Already in your second brain: {exc.existing.name}"
        )
    except OSError as exc:
        return PipelineResult(f"⚠️ Couldn't save the note: {exc}")

    return PipelineResult(_render_reply(summary, path), note_path=path, ok=True)


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


async def _send_typing(message) -> None:
    """Show the 'typing…' chat action. Best-effort — never fails the request."""
    chat = getattr(message, "chat", None)
    send = getattr(chat, "send_action", None)
    if send is None:
        return
    try:
        await send("typing")
    except Exception:  # noqa: BLE001 — feedback is cosmetic
        pass


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

    # Bind per-source credentials so the pipeline keeps its fetch(url) shape.
    fetch = functools.partial(
        default_fetch,
        medium_cookie=settings.medium_cookie,
        supadata_api_key=settings.supadata_api_key,
    )

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("ask", make_ask_handler(settings, vault)))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            make_handler(settings, vault, fetch=fetch),
        )
    )
    return app


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
        reply = await _run_with_typing(
            message,
            asyncio.to_thread(run_ask, question, vault=vault, settings=settings),
        )
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
