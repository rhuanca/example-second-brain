# Second Brain Telegram Bot

Send a link to your Telegram bot → it fetches the article (or a **YouTube
video's transcript**), writes a **technical summary** with Claude, replies to you
in the chat, and files a Markdown note into a dedicated **Obsidian** vault
(organized PARA-style). Review and remix your reading later in Obsidian.

```
Telegram message → extract URL → fetch article → summarize (Claude)
                → save PARA note → reply with the summary
```

Single-user by design (only your Telegram id is served). Runs locally; built
config-driven so moving it to a server later is mechanical.

## Requirements

- Python 3.13 + [uv](https://docs.astral.sh/uv/)
- A Telegram bot token
- An Anthropic API key (Claude)

## Setup

```bash
uv sync                 # install dependencies
cp env.example .env     # then edit .env (see below)
```

Fill in `.env`:

| Variable | How to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Message [@BotFather](https://t.me/BotFather), `/newbot`, copy the token |
| `TELEGRAM_ALLOWED_USER_ID` | Message [@userinfobot](https://t.me/userinfobot); it replies with your numeric id. Only this user is served. |
| `ANTHROPIC_API_KEY` | From the [Anthropic Console](https://console.anthropic.com/) |
| `ANTHROPIC_MODEL` | Claude model for summaries. Defaults to `claude-sonnet-4-6`. |
| `VAULT_PATH` | Path to the Obsidian vault the bot owns (e.g. `./vault`). PARA folders are created here on startup. |

## Run

```bash
uv run python -m second_brain.main
```

Then, from the Telegram account whose id you configured, send the bot a link.
You'll get a summary reply, and a note will appear under
`<VAULT_PATH>/Resources/` (open the vault in Obsidian to browse).

- Re-sending the same link → "already in your second brain", no duplicate note.
- Sending a non-link message → a short usage hint, no note.
- **YouTube links** are summarized from the video's transcript. If a video has no
  usable transcript (captions disabled/unavailable), the bot says so and saves
  nothing.

## Notes & PARA

Notes are Markdown with YAML frontmatter and land in a PARA folder
(`Projects/`, `Areas/`, `Resources/`, `Archives/`) — reference reading defaults
to **Resources**. Example:

```markdown
---
title: "Building Agentic Systems"
source: "https://example.com/post"
date: 2026-06-30
para: resources
tags: [agentic-dev, llm]
---
## TL;DR
...
## Key technical points
- ...
## Prototype ideas
- ...
```

## Tests

```bash
uv run python -m unittest discover -s tests
```

The full suite runs without a Telegram token or API key — the network, the LLM,
and Telegram are mocked. Only the live run above needs real credentials.

## Roadmap

- **Phase 2 — Ask your second brain:** query your saved notes from the same
  Telegram bot ("what have I saved about agent memory?") via search/RAG over the
  vault. Designed toward, not built yet.
- **Cloud:** the bot is env-driven and docker-aware, so hosting it on a server
  (always-on) is a later, mechanical step.

## Project layout

- `second_brain/config.py` — settings from environment
- `second_brain/urls.py` — extract + normalize URLs (dedup key)
- `second_brain/fetcher.py` — article extraction (trafilatura)
- `second_brain/youtube.py` — YouTube detection + transcript fetch
- `second_brain/sources.py` — routes a URL to the article or YouTube fetcher
- `second_brain/summarizer.py` — Claude summary → structured `Summary`
- `second_brain/vault.py` — PARA routing, note rendering, write + dedup
- `second_brain/bot.py` — capture pipeline + Telegram wiring
- `second_brain/main.py` — entry point (long-polling)
- `specs/` — the spec, plan, and task breakdown (spec-driven development)
