# Tasks: Second Brain Telegram Bot

Ordered, atomic, independently verifiable. Implement top to bottom; do not start
a task until the one above is green and committed.

- [x] 1. **Project scaffold** — done. See Done log.

- [x] 2. **config.py** — done. See Done log.

- [x] 3. **urls.py** — done. See Done log.

- [x] 4. **vault.py: routing, slug, render** — done. See Done log.

- [x] 5. **vault.py: write + dedup** — done. See Done log.

- [x] 6. **summarizer.py** — done. See Done log.

- [x] 7. **fetcher.py** — done. See Done log.

- [x] 8. **bot.py orchestration (pure pipeline fn)** — done. See Done log.

- [x] 9. **bot.py Telegram wiring + main** — done. See Done log.

- [x] 10. **README + .env.example polish** — setup, run, and "get a Telegram bot
      token / set VAULT_PATH" instructions; note Phase 2 (ask-your-second-brain)
      as future. — verify: README lists every env var in config.py; `uv run`
      command documented.

## Done log
- 1. Project scaffold — uv project, deps, `second_brain/` + `tests/`, `.gitignore`,
     `env.example` (named without leading dot — `.env*` is hook-blocked), smoke
     test green. Commit `c948d54`.
- 2. config.py — frozen `Settings` dataclass, `from_env()` with validation;
     vault path resolved to absolute, positive user-id guard, api key optional.
     12 tests green. Review: no blockers; addressed relative-path resolve.
     Commit `7f74908`.
- 3. urls.py — `extract_url` (first http(s) URL, trailing-punct trim) +
     `normalize_url` (lowercase scheme/host, drop fragment + tracking params,
     strip trailing slash). 24 tests green. Commit `b0eba89`.
- 4. models.py (`Para` enum + `Summary` dataclass, shared) and vault.py pure
     pieces — `folder_for`, `slugify`, `note_filename`, `render_note` (frontmatter
     + TL;DR/points/prototype sections). 34 tests green. Commit `113b132`.
- 5. vault.py write+dedup — `Vault` class: `ensure_folders`, `find_by_url`/
     `is_duplicate` (normalized source scan), `write_note` (refuses dup URLs,
     collision-safe `-N` suffix keeps both notes). Review caught silent-overwrite
     bug; fixed + tested. 42 tests green. Commit `ea1d6f3`.
- 6. summarizer.py — `summarize()` builds a strict-JSON prompt, calls Claude
     (`client.messages.create`), parses into `Summary`; tolerant fallback (code-fence
     strip, raw-text on parse failure), tags kebab-normalized. Injectable client for
     tests. Review: no blockers; applied empty-tldr guard + coverage. Commit `2f61852`.
- 7. fetcher.py — `fetch(url)` → `Article(title, text)` via trafilatura
     (injectable downloader/extractor); `FetchError` on blocked/empty. Commit `473681e`.
- 8. bot.py `handle_url` — extract→dedup→fetch→summarize→write pipeline; every
     failure returns a clear reply, write is last (no partial notes). Review caught
     unhandled write `OSError`; fixed + tested. Commit `2fa6715`.
- 9. bot.py Telegram wiring + main.py — `is_allowed` single-user allow-list, async
     `make_handler` offloading blocking I/O via `asyncio.to_thread`, `build_application`,
     long-polling entry. Review: thread injectables through handler for a happy-path
     wiring test. Commit `6de6bf9`.
- 10. README — setup (uv sync, .env, BotFather/@userinfobot, VAULT_PATH), run
     command, PARA note format, Phase 2 roadmap. env template covers all config vars.
     Commit `979b5f4`. **All 70 tests green; project complete.**
