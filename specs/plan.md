# Plan: Second Brain Telegram Bot

Derived from spec.md. The simplest design that satisfies the spec.

## Architecture
A single long-running Python process. The Telegram bot receives a message,
extracts a URL, and runs a linear pipeline:

```
Telegram message
   -> urls.extract()        (find + normalize the URL; ignore if none)
   -> vault.is_duplicate()  (already captured? -> reply "already saved")
   -> fetcher.fetch()       (download + extract main article text)
   -> summarizer.summarize()(Claude API -> structured summary object)
   -> vault.write_note()    (route to PARA folder, render markdown, write file)
   -> reply to Telegram with the summary
```

Each step is a plain function in its own module; `bot.py` wires them together
and handles errors by replying with a clear message (and never writing a partial
note). All steps except `bot.py` are pure/IO-isolated enough to unit-test with
the network and LLM mocked.

## Stack
- Language / runtime: **Python + uv**.
- Key libraries:
  - `python-telegram-bot` — Telegram bot framework (async, well maintained).
  - `trafilatura` — robust main-content article extraction from a URL/HTML
    (single dependency, beats hand-rolled readability). Resolves the open
    question in the spec.
  - `anthropic` — Claude API client for summarization.
  - `python-frontmatter` — read/write YAML frontmatter in notes (used for both
    rendering notes and duplicate detection).
  - `python-dotenv` — load a local `.env` for convenience (env still wins).
- Testing: stdlib **unittest**; network/LLM mocked.

## Layers
Only what the spec needs:
- `config.py` — load + validate settings from environment (bot token, allowed
  user id, vault path, model name, API key). One typed Settings object.
- `urls.py` — extract the first URL from text; normalize it (strip tracking
  params, trailing slash) for stable dedup keys.
- `fetcher.py` — given a URL, return extracted article text + title (trafilatura).
- `summarizer.py` — given title + text, call Claude and return a `Summary`
  dataclass (title, tldr, key_points, tags, para_category, prototype_ideas).
  Prompt asks for strict JSON; we parse it.
- `vault.py` — PARA routing (default Resources), slug/filename, dedup scan over
  existing notes' `source` frontmatter, render the markdown note, write it.
- `bot.py` — Telegram handlers, single-user allow-list, orchestrates the pipeline,
  error replies.
- `main.py` (or `__main__`) — build Settings, start the bot.

No repository/DB/service-abstraction layers — not earned. Dedup is a simple scan
of the vault folder (small, single user). If it ever gets slow we add an index
then, not now.

## Data and config
Environment variables (with a local `.env` for dev):
- `TELEGRAM_BOT_TOKEN` — bot token.
- `TELEGRAM_ALLOWED_USER_ID` — only this Telegram user is served.
- `ANTHROPIC_API_KEY` — Claude key (provided later; mocked in tests).
- `ANTHROPIC_MODEL` — default to a current model (e.g. `claude-sonnet-4-6`).
- `VAULT_PATH` — absolute path to the dedicated Obsidian vault.
PARA folders (`Projects/`, `Areas/`, `Resources/`, `Archives/`) are created
under `VAULT_PATH` on startup if missing. No secrets committed; `.env` is
gitignored. No hardcoded paths/ports → cloud move is mechanical later.

Note format (Markdown + YAML frontmatter):
```
---
title: "..."
source: "https://..."
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

## Risks / unknowns
- **LLM JSON reliability:** Claude must return parseable structured output. Mitigate
  with a strict prompt + a tolerant parser, and a fallback that stores the raw
  summary text if JSON parsing fails (still a valid note).
- **Article extraction failures:** some sites block bots / are JS-only. Mitigate
  by replying with a clear error and not writing a note; capture the URL+title
  only is a possible future fallback (out of scope now).
- **No API key yet:** all tests mock the Claude call; the bot can't truly run
  end-to-end until the key is provided — acceptable for building + unit testing.
- **Telegram long-polling vs webhook:** use long-polling for local MVP (no public
  URL needed); webhook is a later cloud concern.
