"""Tell the owner something happened, through the capture bot.

The jobs that run on a timer have no one watching them: a backup that failed and
a topic refresh that rewrote half the vault both deserve a message rather than a
line in a journal nobody reads.

Best-effort by design. A notifier that raises makes the thing it was reporting on
harder to find, so every failure here is swallowed and reported as False.
"""

from __future__ import annotations

import asyncio
import logging

from second_brain.config import ConfigError, Settings

logger = logging.getLogger(__name__)


def notify(text: str, *, settings: Settings | None = None) -> bool:
    """Send one Telegram message to the owner. True if it went."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        settings = settings or Settings.from_env()
    except ConfigError as exc:
        logger.warning("not notified (config): %s", exc)
        return False

    try:
        asyncio.run(_send(settings, text))
        return True
    except Exception as exc:  # noqa: BLE001 — the caller may already be failing
        logger.warning("not notified (%s): %s", type(exc).__name__, exc)
        return False


async def _send(settings: Settings, text: str) -> None:
    import telegram

    bot = telegram.Bot(settings.telegram_bot_token)
    async with bot:
        await bot.send_message(chat_id=settings.telegram_allowed_user_id, text=text)
