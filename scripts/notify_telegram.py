#!/usr/bin/env python3
"""Send one message to the owner's Telegram chat, through the capture bot.

Used by the backup unit's `OnFailure=`: a backup that quietly stopped months ago
is the usual way backups fail, and the journal is only read by someone who
already suspects something.

    uv run python scripts/notify_telegram.py "backup failed on renan-nuc"

Best-effort by design — it never exits non-zero, because a notifier that fails
the thing it was reporting on makes the report harder to find, not easier.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from second_brain.config import ConfigError, Settings  # noqa: E402


async def send(settings: Settings, text: str) -> None:
    import telegram

    bot = telegram.Bot(settings.telegram_bot_token)
    async with bot:
        await bot.send_message(chat_id=settings.telegram_allowed_user_id, text=text)


def main() -> None:
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        print("nothing to send", file=sys.stderr)
        return
    try:
        asyncio.run(send(Settings.from_env(), text))
        print("sent")
    except ConfigError as exc:
        print(f"not sent (config): {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — the caller is already failing
        print(f"not sent ({type(exc).__name__}): {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
