# Spec: Second Brain Telegram Bot

## Problem and goal
I regularly find good links about agentic development, software engineering, and
related topics. Today I forward them to WhatsApp, where they pile up unread and
forgotten. I want a personal assistant that, when I send it a link, fetches the
article, produces a **technical summary**, sends it back to me so I actually
absorb it, and files a note into my **Obsidian** vault (organized PARA-style) so
I can review it later and reuse articles as inspiration for prototypes.

The interface is a **Telegram bot** (replacing the WhatsApp habit). It runs
**locally first**, but is built config-driven so it can move to a cloud server
later with minimal change.

## Success criteria
Testable, observable outcomes (EARS-style where it fits):
- [ ] When I send a message containing a URL to the bot, the system shall fetch
      the article's main content.
- [ ] When the article content is fetched, the system shall generate a technical
      summary (TL;DR, key technical points, and a "prototype ideas" hint) using
      the Claude API.
- [ ] When a summary is generated, the system shall reply to me in Telegram with
      that summary.
- [ ] When a summary is generated, the system shall write a Markdown note into
      the configured Obsidian vault under the correct PARA folder, with YAML
      frontmatter (title, source URL, date, tags, para category).
- [ ] When a message contains no URL, the system shall reply with a short usage
      hint and shall not create a note.
- [ ] When fetching or summarizing fails, the system shall reply with a clear
      error message and shall not write a partial/corrupt note.
- [ ] When a duplicate URL is sent (already in the vault), the system shall tell
      me it already exists rather than creating a second note.
- [ ] The bot shall only respond to my own Telegram user/chat id (single-user
      allow-list).
- [ ] All paths, tokens, and the vault location shall come from environment
      variables / config — no hardcoded secrets or paths.

## In scope
- A long-running Telegram bot (single user: me).
- URL extraction from incoming messages.
- Article fetching + main-content extraction (readability-style).
- Technical summarization via the Claude API (latest Opus/Sonnet model).
- Telegram reply with the summary.
- Writing a PARA-organized Markdown note with frontmatter into an Obsidian vault.
- Duplicate detection by URL.
- Config via environment variables (tokens, vault path, model, allowed user id).
- Tests (stdlib `unittest`) for URL parsing, note rendering, PARA routing,
  filename/dedup logic, with the network/LLM calls mocked.

## Out of scope (explicit)
- "Ask your second brain" Q&A / semantic search over notes — **planned Phase 2**,
  delivered through the same Telegram bot (search/RAG over the vault). Designed
  toward, not built now.
- Any web interface or dashboard — Obsidian is the review/visualize tool.
- Cloud deployment / hosting automation — **planned later**; code is kept
  cloud-ready (env-driven, docker-aware) but we run locally for the MVP.
- Multi-user support, accounts, or sharing.
- Automatic prototype/code generation from articles (we only capture a
  "prototype ideas" hint in the note).
- Non-URL content (PDFs, images, raw text capture).

## Decisions (resolved)
- **Dedicated vault:** the bot owns an entire Obsidian vault used solely for the
  second brain (no shared/existing vault to coexist with). PARA folders are
  created inside it.
- **Default PARA category:** **Resources** for reference articles; Claude may
  suggest a category from the summary, falling back to Resources.
- **Tags:** Claude auto-suggests 2-5 tags, normalized to lowercase-kebab.
- **Claude API key:** an `ANTHROPIC_API_KEY` will be provided later; until then,
  the LLM call is mocked in tests and the bot reads the key from env at runtime.

## Open questions
- Preferred article-extraction library (e.g. `trafilatura` vs `readability-lxml`)
  — to be decided in the Plan.
