"""The capture pipeline and (task 9) the Telegram wiring.

`handle_url` is the Telegram-independent core: text in, a reply (and maybe a
saved note) out. It never writes a partial note — the vault write is the last
step, after fetch and summarize both succeed.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

from second_brain.config import Settings
from second_brain.fetcher import FetchError
from second_brain.fetcher import fetch as default_fetch
from second_brain.summarizer import SummarizerError
from second_brain.summarizer import summarize as default_summarize
from second_brain.urls import extract_url
from second_brain.vault import DuplicateNoteError, Vault, folder_for

NO_URL_MESSAGE = (
    "Send me a link (http/https) and I'll summarize it and file it in your "
    "second brain."
)


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

    try:
        path = vault.write_note(summary, url, today())
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

    lines += ["", f"Saved to {folder_for(summary.para)}/{path.name}"]
    return "\n".join(lines)


# --- Telegram wiring -------------------------------------------------------


def is_allowed(user_id: int | None, settings: Settings) -> bool:
    """True only for the single configured user id (the allow-list)."""
    return user_id == settings.telegram_allowed_user_id


def build_application(settings: Settings, vault: Vault):
    """Build a python-telegram-bot Application wired to the capture pipeline."""
    from telegram.ext import Application, MessageHandler, filters

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, make_handler(settings, vault))
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
        result = await asyncio.to_thread(
            handle_url,
            message.text,
            vault=vault,
            settings=settings,
            fetch=fetch,
            summarize=summarize,
            today=today,
        )
        await message.reply_text(result.reply)

    return handle
