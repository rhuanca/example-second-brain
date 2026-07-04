"""Slack adapter — the desk-side query surface for the second brain.

Capture stays on Telegram; Slack is for asking questions of what you've saved.
DM the bot a question and it runs the same `ask()` brain and replies. A link in a
Slack DM gets a gentle nudge toward Telegram (capture's home).

The Slack SDK is imported lazily so the pure `process_message` core (and its
tests) don't need slack_bolt installed.
"""

from __future__ import annotations

from second_brain.ask import ask as default_ask
from second_brain.config import Settings
from second_brain.urls import extract_url
from second_brain.vault import Vault

CAPTURE_HINT = (
    "I'm your desk assistant — send links to the Telegram bot to capture them. "
    "Here, ask me about what you've saved, e.g. "
    "\"what have I saved about agent memory?\""
)
_WORKING_EMOJI = "hourglass_flowing_sand"


def is_allowed(user_id, settings: Settings) -> bool:
    """True only for the single configured Slack user id."""
    return user_id is not None and user_id == settings.slack_allowed_user_id


def process_message(
    event: dict,
    *,
    settings: Settings,
    vault: Vault,
    say,
    run_ask=default_ask,
    react=None,
    unreact=None,
) -> None:
    """Handle one Slack message event (pure of the Slack SDK, for testability).

    `say(text)` posts a reply; `react()`/`unreact()` toggle the working indicator.
    """
    if not _is_owner_dm(event, settings):
        return
    text = (event.get("text") or "").strip()
    if not text:
        return
    if extract_url(text):
        say(CAPTURE_HINT)
        return

    if react:
        react()
    try:
        reply = run_ask(text, vault=vault, settings=settings)
    finally:
        if unreact:
            unreact()
    say(reply)


def _is_owner_dm(event: dict, settings: Settings) -> bool:
    if event.get("bot_id") or event.get("subtype"):
        return False  # ignore bot messages, edits, joins, etc.
    if event.get("channel_type") != "im":
        return False  # DMs only
    return is_allowed(event.get("user"), settings)


def build_app(settings: Settings, vault: Vault, *, run_ask=default_ask):
    """Build a slack_bolt App wired to the query pipeline."""
    from slack_bolt import App

    app = App(token=settings.slack_bot_token)

    @app.event("message")
    def _on_message(event, say, client):  # pragma: no cover — thin Slack wiring
        channel, ts = event.get("channel"), event.get("ts")
        process_message(
            event,
            settings=settings,
            vault=vault,
            say=say,
            run_ask=run_ask,
            react=lambda: _safe_reaction(client.reactions_add, channel, ts),
            unreact=lambda: _safe_reaction(client.reactions_remove, channel, ts),
        )

    return app


def _safe_reaction(fn, channel, ts) -> None:  # pragma: no cover — best-effort I/O
    try:
        fn(channel=channel, timestamp=ts, name=_WORKING_EMOJI)
    except Exception:  # noqa: BLE001 — feedback is cosmetic, never fail the reply
        pass


def run(settings: Settings, vault: Vault) -> None:  # pragma: no cover — process entry
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = build_app(settings, vault)
    SocketModeHandler(app, settings.slack_app_token).start()
