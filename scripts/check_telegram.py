#!/usr/bin/env python3
"""Check the bot's two feedback channels against Telegram, from this machine.

The typing indicator and the message reaction are both cosmetic, so the bot
swallows their failures -- which makes "I never saw the dots" impossible to
diagnose from the outside. This sends each one to the owner's chat and reports
exactly what the API said.

    uv run python scripts/check_telegram.py            # show what it would do
    uv run python scripts/check_telegram.py --send     # actually send

Run it with the chat open. A typing action shows for ~5 seconds in the chat
header; the reaction lands on the bot's own probe message and is then cleared.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from second_brain.bot import WORKING  # noqa: E402
from second_brain.config import ConfigError, Settings  # noqa: E402


async def probe(settings: Settings) -> int:
    import telegram

    bot = telegram.Bot(settings.telegram_bot_token)
    chat_id = settings.telegram_allowed_user_id
    failures = 0

    async with bot:
        me = await bot.get_me()
        print(f"bot           : @{me.username} (id {me.id})")

        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            print("chat action   : sent — watch the chat header for ~5 seconds")
        except Exception as exc:  # noqa: BLE001 — the report is the point
            failures += 1
            print(f"chat action   : REFUSED — {type(exc).__name__}: {exc}")

        try:
            message = await bot.send_message(
                chat_id=chat_id, text="Feedback check — this message can be deleted."
            )
        except Exception as exc:  # noqa: BLE001
            print(f"probe message : REFUSED — {type(exc).__name__}: {exc}")
            return failures + 1

        for label, emoji in [(f"reaction {WORKING}", WORKING), ("reaction clear", None)]:
            try:
                await message.set_reaction(emoji)
                print(f"{label:14}: sent")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"{label:14}: REFUSED — {type(exc).__name__}: {exc}")

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--send", action="store_true", help="actually send (default: dry run)"
    )
    args = parser.parse_args()

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        raise SystemExit(f"config: {exc}") from None

    if not args.send:
        print(
            "Dry run. This would send a typing action and a reaction to chat "
            f"{settings.telegram_allowed_user_id}.\nRe-run with --send."
        )
        return

    failures = asyncio.run(probe(settings))
    if failures:
        raise SystemExit(
            f"\n{failures} of the bot's feedback channels were refused — that is "
            "why you don't see them. The error above says which."
        )
    print("\nBoth feedback channels work. If you still see neither, it is the client.")


if __name__ == "__main__":
    main()
